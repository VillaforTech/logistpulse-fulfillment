"""Pure domain/event/KPI functions. No network connections or background work at import."""

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import math
import hashlib
import json
import uuid

FACTS_TOPIC = "logistpulse.fulfillment.events.v1"
COMMANDS_TOPIC = "logistpulse.orders"
DEADLINE_SECONDS = 15
WINDOW_SECONDS = 900
EVENT_STATES = {
    "ORDER_CREATED": "WAITING",
    "ORDER_PREPARING": "PREPARING",
    "ORDER_READY": "READY",
}


def money(value):
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("total must be decimal") from exc
    if not result.is_finite() or result <= 0 or result > Decimal("9999999999.99"):
        raise ValueError("total must be finite and positive")
    if result != result.quantize(Decimal(".01")):
        raise ValueError("total supports two decimal places")
    return format(result, ".2f")


def utc(epoch):
    return (
        datetime.fromtimestamp(float(epoch), timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def epoch(value):
    if isinstance(value, (float, int)):
        if not math.isfinite(value):
            raise ValueError("invalid timestamp")
        return float(value)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp requires timezone")
    return parsed.timestamp()


def event_for(order, event_type, now):
    event = {
        "schemaVersion": 1,
        "eventId": str(uuid.uuid4()),
        "eventType": event_type,
        "aggregateType": "Order",
        "aggregateId": order["orderId"],
        "aggregateVersion": order["version"],
        "occurredAt": utc(now),
        "correlationId": order["correlationId"],
        "fixtureRunId": order.get("fixtureRunId"),
        "data": dict(order),
    }
    validate_event(event)
    return event


def validate_event(event):
    if type(event.get("schemaVersion")) is not int or event["schemaVersion"] != 1:
        raise ValueError("unsupported schemaVersion")
    uuid.UUID(event["eventId"])
    position = event.get("sourcePosition")
    if position is not None and (type(position) is not int or position < 1):
        raise ValueError("invalid sourcePosition")
    if (
        event.get("aggregateType") != "Order"
        or event.get("eventType") not in EVENT_STATES
    ):
        raise ValueError("unsupported aggregate/event type")
    version = event.get("aggregateVersion")
    if type(version) is not int or version < 1:
        raise ValueError("invalid aggregateVersion")
    data = event["data"]
    if (
        not event.get("aggregateId")
        or data["orderId"] != event["aggregateId"]
        or data["version"] != version
    ):
        raise ValueError("aggregate identity/version mismatch")
    if (
        not isinstance(event.get("correlationId"), str)
        or not event["correlationId"]
        or data.get("correlationId") != event["correlationId"]
        or data.get("unit") != "DEMO"
        or data.get("fixtureRunId") != event.get("fixtureRunId")
    ):
        raise ValueError("correlation and explicit DEMO unit required")
    if data.get("status") != EVENT_STATES[event["eventType"]]:
        raise ValueError("event does not describe persisted state")
    if not isinstance(data["total"], str) or money(data["total"]) != data["total"]:
        raise ValueError("total must be a two-place decimal string")
    created = epoch(data["createdAt"])
    if epoch(data["updatedAt"]) < created or epoch(event["occurredAt"]) < created:
        raise ValueError("timestamps precede creation")
    ready = data.get("readyAt")
    if (data["status"] == "READY") != (ready is not None):
        raise ValueError(
            "READY requires an observed readyAt; other states must not have it"
        )
    if ready is not None and (
        epoch(ready) < created
        or epoch(ready) > epoch(data["updatedAt"])
        or epoch(ready) > epoch(event["occurredAt"])
    ):
        raise ValueError("readyAt precedes creation")
    return event


def transition(order, target, now):
    """Duplicates preserve READY and its original timestamp/version."""
    if order["status"] == target or order["status"] == "READY":
        return order, False
    if (order["status"], target) not in {
        ("WAITING", "PREPARING"),
        ("PREPARING", "READY"),
    }:
        raise ValueError("invalid order transition")
    result = dict(
        order, status=target, version=order["version"] + 1, updatedAt=utc(now)
    )
    if target == "READY":
        result["readyAt"] = utc(now)
    return result, True


def calculate(orders, now, *, window=WINDOW_SECONDS, deadline=DEADLINE_SECONDS):
    """Recompute from full current order snapshots; never accumulate timer deltas."""
    cohort = [
        o for o in orders if now - window < epoch(o["createdAt"]) <= now - deadline
    ]
    late = [
        o
        for o in cohort
        if o.get("readyAt") is None
        or epoch(o["readyAt"]) > epoch(o["createdAt"]) + deadline
    ]
    overdue = [
        o
        for o in orders
        if o["status"] in ("WAITING", "PREPARING")
        and epoch(o["createdAt"]) + deadline <= now
    ]
    value = sum((Decimal(o["total"]) for o in overdue), Decimal("0"))
    debt = sum(max(0, now - epoch(o["createdAt"]) - deadline) for o in overdue)
    return {
        "L-K1": {
            "value": 100 * len(late) / len(cohort) if cohort else None,
            "unit": "percent",
            "status": "OK" if cohort else "NO_SAMPLE",
            "sample": len(cohort),
            "breached": len(late),
        },
        "L-K2": {
            "value": format(value, ".2f"),
            "unit": "DEMO",
            "status": "OK",
            "sample": len(overdue),
        },
        "L-K3": {
            "value": round(debt, 6),
            "unit": "order_seconds",
            "status": "OK",
            "sample": len(overdue),
        },
    }


def alert_states(kpis):
    return {
        key: value["value"] is not None and Decimal(str(value["value"])) > 0
        for key, value in kpis.items()
    }


def coverage_digest(orders):
    pairs = sorted((order["orderId"], order["version"]) for order in orders)
    return hashlib.sha256(json.dumps(pairs, separators=(",", ":")).encode()).hexdigest()


def decode_message(raw):
    if raw is None:
        return {"_malformed": True, "reason": "Kafka tombstone is not a domain fact"}
    try:
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("event must be an object")
        return value
    except (ValueError, UnicodeError, AttributeError):
        return {
            "_malformed": True,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "preview": raw[:1000].decode("utf-8", errors="replace"),
        }
