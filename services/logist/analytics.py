"""Durable independent projection, explicit coverage, real timers and alert history."""

from contextlib import asynccontextmanager
from decimal import Decimal
import asyncio
import json
import logging
import os
import threading
import time
import uuid
import httpx
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from kafka import KafkaConsumer
from kafka.consumer.subscription_state import ConsumerRebalanceListener
from psycopg.types.json import Jsonb
from prometheus_client import Gauge, Counter
from .domain import (
    FACTS_TOPIC,
    calculate,
    alert_states,
    epoch,
    utc,
    validate_event,
    coverage_digest,
    decode_message,
)
from .storage import connect, retry_bootstrap
from .metrics import instrument

log = logging.getLogger(__name__)
TICK = float(os.getenv("ANALYTICS_TICK_SECONDS", ".2"))
SOURCE = os.getenv("FULFILLMENT_URL", "http://fulfillment-api:8000")
stop = threading.Event()
state = {
    "ready": False,
    "lastPoll": 0.0,
    "sourceCheckedAt": 0.0,
    "sourcePosition": 0,
    "lag": 0,
    "sourcePending": 0,
    "aggregateCount": 0,
    "versionDigest": None,
}
DUPLICATES = Counter(
    "logistpulse_analytics_duplicates_total", "Ignored duplicate event identities"
)
ERRORS = Counter("logistpulse_analytics_errors_total", "Invalid events quarantined")
GAUGES = {
    name: Gauge("logistpulse_" + name, help)
    for name, help in {
        "orders_due_window": "Eligible orders in 15 minute cohort",
        "orders_breached_window": "Orders missing promise in cohort",
        "overdue_order_value": "Overdue backlog value in demo units",
        "preparation_debt_seconds": "Overdue backlog order seconds",
        "analytics_lag": "Kafka records behind end offset",
        "analytics_gaps": "Unresolved aggregate revision gaps",
        "analytics_snapshot_timestamp_seconds": "Last committed snapshot time",
        "analytics_timer_lag_seconds": "Timer scheduling delay",
        "analytics_coverage_complete": "1 only when coverage is complete and current",
    }.items()
}


def dbconn():
    return connect("ANALYTICS_DB_URL")


def metadata(db, key, default=None):
    row = db.execute("SELECT value FROM analytics_meta WHERE key=%s", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(db, key, value):
    db.execute(
        "INSERT INTO analytics_meta VALUES (%s,%s) ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value",
        (key, Jsonb(value)),
    )


def migrate():
    def run():
        with dbconn() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS projection(order_id text PRIMARY KEY, version integer NOT NULL,
                data jsonb NOT NULL, deadline_at numeric NOT NULL)"""
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS processed_events(event_id text PRIMARY KEY, aggregate_id text,
                aggregate_version integer, envelope jsonb, received_at numeric)"""
            )
            db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS unique_aggregate_version ON processed_events(aggregate_id,aggregate_version)"
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS consumer_offsets(topic text, partition integer,
                next_offset bigint, PRIMARY KEY(topic,partition))"""
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS revision_gaps(order_id text, version integer, PRIMARY KEY(order_id,version))"
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS quarantine(topic text, partition integer, record_offset bigint,
                reason text, envelope jsonb, PRIMARY KEY(topic,partition,record_offset))"""
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS analytics_meta(key text PRIMARY KEY,value jsonb)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS snapshots(id integer PRIMARY KEY CHECK(id=1), revision bigint, snapshot jsonb)"
            )
            db.execute(
                "INSERT INTO snapshots VALUES (1,0,%s) ON CONFLICT DO NOTHING",
                (Jsonb({}),),
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS alerts(kpi text PRIMARY KEY, active boolean NOT NULL,
                activated_at numeric, resolved_at numeric)"""
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS alert_history(id bigserial PRIMARY KEY, kpi text,active boolean,
                computed_at numeric,snapshot_revision bigint)"""
            )

    retry_bootstrap(run)


def bootstrap_projection():
    with dbconn() as db:
        if metadata(db, "bootstrapped", False):
            return
    response = httpx.get(SOURCE + "/api/fulfillment/snapshot", timeout=5)
    response.raise_for_status()
    source = response.json()
    with dbconn() as db:
        for order in source["orders"]:
            db.execute(
                """INSERT INTO projection VALUES (%s,%s,%s,%s)
                ON CONFLICT(order_id) DO UPDATE SET version=EXCLUDED.version,data=EXCLUDED.data,deadline_at=EXCLUDED.deadline_at
                WHERE projection.version < EXCLUDED.version""",
                (
                    order["orderId"],
                    order["version"],
                    Jsonb(order),
                    epoch(order["createdAt"]) + 15,
                ),
            )
        set_meta(db, "baselinePosition", source["sourcePosition"])
        set_meta(db, "coverage", source["coverage"])
        set_meta(db, "bootstrapped", True)


def persist_event(event, topic, partition, offset):
    try:
        validate_event(event)
        reason = None
    except (KeyError, TypeError, ValueError, OverflowError, AttributeError) as exc:
        reason = f"{type(exc).__name__}: {exc}"
    with dbconn() as db:
        if reason:
            db.execute(
                "INSERT INTO quarantine VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (topic, partition, offset, reason, Jsonb(event)),
            )
            ERRORS.inc()
        else:
            inserted = db.execute(
                """INSERT INTO processed_events VALUES (%s,%s,%s,%s,%s)
                ON CONFLICT DO NOTHING RETURNING event_id""",
                (
                    event["eventId"],
                    event["aggregateId"],
                    event["aggregateVersion"],
                    Jsonb(event),
                    time.time(),
                ),
            ).fetchone()
            if inserted:
                oid, version = event["aggregateId"], event["aggregateVersion"]
                current = db.execute(
                    "SELECT version FROM projection WHERE order_id=%s FOR UPDATE",
                    (oid,),
                ).fetchone()
                previous = current["version"] if current else 0
                if version > previous + 1000:
                    db.execute(
                        "INSERT INTO quarantine VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                        (
                            topic,
                            partition,
                            offset,
                            "Unbounded version gap",
                            Jsonb(event),
                        ),
                    )
                else:
                    for missing in range(previous + 1, version):
                        db.execute(
                            "INSERT INTO revision_gaps VALUES (%s,%s) ON CONFLICT DO NOTHING",
                            (oid, missing),
                        )
                    db.execute(
                        "DELETE FROM revision_gaps WHERE order_id=%s AND version=%s",
                        (oid, version),
                    )
                    if version > previous:
                        data = event["data"]
                        db.execute(
                            """INSERT INTO projection VALUES (%s,%s,%s,%s)
                            ON CONFLICT(order_id) DO UPDATE SET version=EXCLUDED.version,data=EXCLUDED.data,deadline_at=EXCLUDED.deadline_at""",
                            (oid, version, Jsonb(data), epoch(data["createdAt"]) + 15),
                        )
                        set_meta(
                            db,
                            "sourceEvent",
                            {
                                key: event.get(key)
                                for key in (
                                    "eventId",
                                    "aggregateId",
                                    "aggregateVersion",
                                    "correlationId",
                                    "occurredAt",
                                    "sourcePosition",
                                )
                            },
                        )
                    set_meta(
                        db,
                        "lastPosition",
                        max(
                            metadata(db, "lastPosition", 0),
                            event.get("sourcePosition", 0),
                        ),
                    )
            else:
                previous = db.execute(
                    "SELECT envelope FROM processed_events WHERE event_id=%s OR (aggregate_id=%s AND aggregate_version=%s)",
                    (event["eventId"], event["aggregateId"], event["aggregateVersion"]),
                ).fetchall()
                if len(previous) != 1 or previous[0]["envelope"] != event:
                    db.execute(
                        "INSERT INTO quarantine VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                        (
                            topic,
                            partition,
                            offset,
                            "Conflicting event identity or aggregate version",
                            Jsonb(event),
                        ),
                    )
                    ERRORS.inc()
                else:
                    DUPLICATES.inc()
        db.execute(
            """INSERT INTO consumer_offsets VALUES (%s,%s,%s) ON CONFLICT(topic,partition)
            DO UPDATE SET next_offset=GREATEST(consumer_offsets.next_offset,EXCLUDED.next_offset)""",
            (topic, partition, offset + 1),
        )
    # Caller acknowledges Kafka only after exiting this transaction successfully.


def snapshot_data(db, now, fixture=None):
    checkpoint = dict(state)
    rows = db.execute("SELECT data FROM projection").fetchall()
    all_orders = [row["data"] for row in rows]
    orders = [
        o for o in all_orders if fixture is None or o.get("fixtureRunId") == fixture
    ]
    gaps = db.execute("SELECT count(*) AS n FROM revision_gaps").fetchone()["n"]
    errors = db.execute("SELECT count(*) AS n FROM quarantine").fetchone()["n"]
    events = db.execute("SELECT count(*) AS n FROM processed_events").fetchone()["n"]
    offsets = db.execute(
        "SELECT topic,partition,next_offset FROM consumer_offsets ORDER BY topic,partition"
    ).fetchall()
    coverage = metadata(
        db, "coverage", {"complete": False, "reason": "Bootstrap pending"}
    )
    position = max(metadata(db, "baselinePosition", 0), metadata(db, "lastPosition", 0))
    quality, reason = "FRESH", ""
    if not coverage["complete"] or gaps or errors:
        quality, reason = (
            "INCOMPLETE",
            coverage.get("reason")
            or f"{gaps} revision gaps; {errors} quarantined records",
        )
    elif (
        not checkpoint["ready"]
        or now - checkpoint["lastPoll"] > 3
        or now - checkpoint["sourceCheckedAt"] > 3
    ):
        quality, reason = "STALE", "Consumer or source watermark heartbeat unavailable"
    elif (
        checkpoint["lag"] > 0
        or checkpoint["sourcePosition"] > position
        or checkpoint["sourcePending"] > 0
        or checkpoint["aggregateCount"] != len(all_orders)
        or checkpoint["versionDigest"] != coverage_digest(all_orders)
    ):
        quality, reason = "INCOMPLETE", "Committed source changes not yet projected"
    kpis = calculate(orders, now)
    if quality != "FRESH":
        for kpi in kpis.values():
            kpi["observedValue"] = kpi["value"]
            kpi["value"] = None
            kpi["status"] = quality
    source = metadata(db, "sourceEvent", {})
    return {
        "schemaVersion": 1,
        "computedAt": utc(now),
        "generatedAt": utc(now),
        "sourceEventId": source.get("eventId"),
        "sourceAggregateId": source.get("aggregateId"),
        "sourceAggregateVersion": source.get("aggregateVersion"),
        "correlationId": source.get("correlationId"),
        "watermark": {
            "occurredAt": source.get("occurredAt"),
            "partitionOffsets": offsets,
            "sourcePosition": position,
            "expectedSourcePosition": checkpoint["sourcePosition"],
            "sourcePending": checkpoint["sourcePending"],
            "versionDigest": coverage_digest(all_orders),
        },
        "quality": {"status": quality, "reason": reason},
        "coverage": {
            "complete": quality == "FRESH",
            "aggregateCount": len(orders),
            "eventCount": events,
        },
        "fixtureRunId": fixture,
        "kpis": kpis,
        "lag": checkpoint["lag"],
        "gaps": gaps,
        "errors": errors,
        "deadlineSeconds": 15,
        "windowSeconds": 900,
        "heartbeatAt": utc(checkpoint["lastPoll"]),
    }


def commit_snapshot():
    now = time.time()
    with dbconn() as db:
        db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        data = snapshot_data(db, now)
        current = db.execute(
            "SELECT revision FROM snapshots WHERE id=1 FOR UPDATE"
        ).fetchone()
        revision = current["revision"] + 1
        data.update(snapshotId=str(uuid.uuid4()), revision=revision)
        if data["quality"]["status"] == "FRESH":
            for key, active in alert_states(data["kpis"]).items():
                old = db.execute(
                    "SELECT active FROM alerts WHERE kpi=%s", (key,)
                ).fetchone()
                if old is None or old["active"] != active:
                    db.execute(
                        """INSERT INTO alerts VALUES (%s,%s,%s,%s) ON CONFLICT(kpi) DO UPDATE
                        SET active=EXCLUDED.active,activated_at=CASE WHEN EXCLUDED.active THEN EXCLUDED.activated_at ELSE alerts.activated_at END,
                        resolved_at=CASE WHEN EXCLUDED.active THEN NULL ELSE EXCLUDED.resolved_at END""",
                        (key, active, now if active else None, None if active else now),
                    )
                    db.execute(
                        "INSERT INTO alert_history(kpi,active,computed_at,snapshot_revision) VALUES (%s,%s,%s,%s)",
                        (key, active, now, revision),
                    )
        data["alerts"] = db.execute(
            "SELECT kpi,active,activated_at,resolved_at FROM alerts ORDER BY kpi"
        ).fetchall()
        # Decimal timestamps from SQL must be JSON-safe.
        for alert in data["alerts"]:
            for key in ("activated_at", "resolved_at"):
                if alert[key] is not None:
                    alert[key] = float(alert[key])
        db.execute(
            "UPDATE snapshots SET revision=%s,snapshot=%s WHERE id=1",
            (revision, Jsonb(data)),
        )
    GAUGES["analytics_snapshot_timestamp_seconds"].set(now)
    GAUGES["analytics_coverage_complete"].set(data["coverage"]["complete"])
    GAUGES["analytics_lag"].set(data["lag"])
    GAUGES["analytics_gaps"].set(data["gaps"])
    valid = data["quality"]["status"] == "FRESH"
    GAUGES["orders_due_window"].set(
        data["kpis"]["L-K1"]["sample"] if valid else float("nan")
    )
    GAUGES["orders_breached_window"].set(
        data["kpis"]["L-K1"]["breached"] if valid else float("nan")
    )
    GAUGES["overdue_order_value"].set(
        float(data["kpis"]["L-K2"]["value"]) if valid else float("nan")
    )
    GAUGES["preparation_debt_seconds"].set(
        data["kpis"]["L-K3"]["value"] if valid else float("nan")
    )


class CheckpointListener(ConsumerRebalanceListener):
    def __init__(self, consumer):
        self.consumer = consumer

    def on_partitions_revoked(self, partitions):
        state["ready"] = False

    def on_partitions_assigned(self, partitions):
        with dbconn() as db:
            for tp in partitions:
                row = db.execute(
                    "SELECT next_offset FROM consumer_offsets WHERE topic=%s AND partition=%s",
                    (tp.topic, tp.partition),
                ).fetchone()
                if row:
                    self.consumer.seek(tp, row["next_offset"])


def consume():
    while not stop.is_set():
        consumer = None
        source_client = None
        try:
            bootstrap_projection()
            source_client = httpx.Client(timeout=2)
            consumer = KafkaConsumer(
                bootstrap_servers=os.environ["KAFKA_BOOTSTRAP"],
                group_id="logistpulse-business-analytics-v1",
                enable_auto_commit=False,
                auto_offset_reset="earliest",
                max_poll_records=1,
                fetch_max_wait_ms=50,
            )
            consumer.subscribe([FACTS_TOPIC], listener=CheckpointListener(consumer))
            while not stop.is_set():
                batches = consumer.poll(timeout_ms=50, max_records=1)
                for messages in batches.values():
                    for message in messages:
                        persist_event(
                            decode_message(message.value),
                            message.topic,
                            message.partition,
                            message.offset,
                        )
                        consumer.commit()
                end = (
                    consumer.end_offsets(consumer.assignment())
                    if consumer.assignment()
                    else {}
                )
                state.update(
                    ready=bool(consumer.assignment()),
                    lastPoll=time.time(),
                    lag=sum(
                        max(0, offset - consumer.position(tp))
                        for tp, offset in end.items()
                    ),
                )
                # A reused connection and a short confirmation interval keep the
                # coverage check off the half-second critical path to the panel.
                if time.time() - state["sourceCheckedAt"] >= 0.1:
                    response = source_client.get(SOURCE + "/api/fulfillment/watermark")
                    response.raise_for_status()
                    mark = response.json()
                    state.update(
                        sourcePosition=mark["sourcePosition"],
                        sourcePending=mark["pending"],
                        aggregateCount=mark["aggregateCount"],
                        versionDigest=mark["versionDigest"],
                        sourceCheckedAt=time.time(),
                    )
        except Exception as exc:
            state["ready"] = False
            log.warning("Analytics will reconnect after %s", type(exc).__name__)
            stop.wait(0.5)
        finally:
            if source_client:
                source_client.close()
            if consumer:
                consumer.close(autocommit=False)


def timers():
    target = time.monotonic()
    while not stop.is_set():
        try:
            GAUGES["analytics_timer_lag_seconds"].set(max(0, time.monotonic() - target))
            commit_snapshot()
        except Exception as exc:
            log.warning("Snapshot transaction failed: %s", type(exc).__name__)
        target += TICK
        if target < time.monotonic() - TICK:
            target = time.monotonic()
        stop.wait(max(0, target - time.monotonic()))


@asynccontextmanager
async def lifespan(app):
    migrate()
    stop.clear()
    threads = [threading.Thread(target=f, daemon=True) for f in (consume, timers)]
    for thread in threads:
        thread.start()
    yield
    stop.set()
    for thread in threads:
        thread.join(timeout=5)


app = FastAPI(title="Independent Business Analytics", lifespan=lifespan)
instrument(app, "business-analytics")


@app.get("/health")
def health():
    with dbconn() as db:
        db.execute("SELECT 1 FROM snapshots")
    if not state["ready"] or time.time() - state["lastPoll"] > 3:
        raise HTTPException(503, "Analytics consumer unavailable")
    return {"status": "UP", **state}


def age_checked(snapshot, now):
    if now - epoch(snapshot["computedAt"]) <= 3:
        return snapshot
    snapshot = json.loads(json.dumps(snapshot))
    snapshot["quality"] = {
        "status": "STALE",
        "reason": "Snapshot timer heartbeat expired",
    }
    snapshot["coverage"]["complete"] = False
    for kpi in snapshot["kpis"].values():
        kpi["observedValue"], kpi["value"], kpi["status"] = kpi["value"], None, "STALE"
    return snapshot


@app.get("/api/business/snapshot")
def current(fixtureRunId: str | None = None):
    with dbconn() as db:
        db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        if fixtureRunId is not None:
            data = snapshot_data(db, time.time(), fixtureRunId)
            data.update(
                snapshotId=str(uuid.uuid4()),
                revision=db.execute(
                    "SELECT revision FROM snapshots WHERE id=1"
                ).fetchone()["revision"],
            )
            return data
        row = db.execute("SELECT snapshot FROM snapshots WHERE id=1").fetchone()
    if not row or not row["snapshot"]:
        raise HTTPException(503, "Initial snapshot pending")
    return age_checked(row["snapshot"], time.time())


@app.get("/api/business/orders/{order_id}")
def projected(order_id: str):
    with dbconn() as db:
        row = db.execute(
            "SELECT data FROM projection WHERE order_id=%s", (order_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(404, "Order not projected")
    return row["data"]


@app.get("/api/business/alerts")
def alerts():
    with dbconn() as db:
        return db.execute(
            "SELECT * FROM alert_history ORDER BY id DESC LIMIT 100"
        ).fetchall()


@app.websocket("/api/business/ws")
async def stream(ws: WebSocket):
    await ws.accept()
    revision = -1
    try:
        while True:
            value = await asyncio.to_thread(current)
            if value["revision"] > revision:
                await ws.send_json(value)
                revision = value["revision"]
            await asyncio.sleep(0.05)
    except (WebSocketDisconnect, RuntimeError):
        pass
