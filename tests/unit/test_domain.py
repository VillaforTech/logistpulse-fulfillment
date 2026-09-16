from copy import deepcopy
from decimal import Decimal
import json
import pytest
from logist.domain import (
    money,
    utc,
    epoch,
    event_for,
    validate_event,
    transition,
    calculate,
    coverage_digest,
    decode_message,
    alert_states,
)
from logist.worker import finish_preparation


def order(created=1000, status="WAITING", ready=None, total="25.50", version=1):
    return {
        "orderId": "ORD-1",
        "storeId": "STORE-042",
        "channel": "TEST",
        "total": total,
        "unit": "DEMO",
        "status": status,
        "createdAt": utc(created),
        "updatedAt": utc(ready or created),
        "readyAt": utc(ready) if ready is not None else None,
        "version": version,
        "correlationId": "run-1",
        "fixtureRunId": "fixture-1",
    }


@pytest.mark.parametrize(
    "value", ["NaN", "Infinity", "-1", "0", "1.234", "999999999999.99"]
)
def test_money_rejects_invalid(value):
    with pytest.raises(ValueError):
        money(value)


def test_money_exact():
    assert money(Decimal("25.5")) == "25.50"


def test_lifecycle_and_repeated_ready():
    initial = order()
    preparing, _ = transition(initial, "PREPARING", 1001)
    ready, changed = finish_preparation(preparing, 1005)
    assert changed and ready["status"] == "READY" and ready["version"] == 3
    repeated, changed = transition(ready, "PREPARING", 1010)
    assert not changed and repeated == ready and epoch(ready["readyAt"]) == 1005
    with pytest.raises(ValueError):
        transition(initial, "READY", 1001)


def test_no_sample_is_not_zero_percent():
    assert calculate([order()], 1014)["L-K1"]["value"] is None
    assert calculate([], 1100)["L-K1"]["status"] == "NO_SAMPLE"


def test_due_without_new_event_and_no_double_debt():
    state = [order()]
    result = calculate(state, 1020)
    assert result["L-K1"]["value"] == 100
    assert result["L-K2"]["value"] == "25.50"
    assert result["L-K3"]["value"] == 5
    assert calculate(state, 1020) == result
    assert calculate(state, 1021)["L-K3"]["value"] == 6
    assert all(alert_states(result).values())


@pytest.mark.parametrize("ready,expected", [(1015, 0), (1015.000001, 100), (1018, 100)])
def test_deadline_boundary_and_late_history(ready, expected):
    result = calculate([order(status="READY", ready=ready, version=3)], 1020)
    assert result["L-K1"]["value"] == expected
    assert result["L-K2"]["value"] == "0.00" and result["L-K3"]["value"] == 0


def test_window_expires_but_old_backlog_remains():
    result = calculate([order()], 1900)
    assert result["L-K1"]["status"] == "NO_SAMPLE"
    assert result["L-K2"]["value"] == "25.50" and result["L-K3"]["value"] == 885


def test_late_delivery_of_on_time_ready_corrects_provisional_breach():
    assert calculate([order(status="PREPARING")], 1020)["L-K1"]["value"] == 100
    assert (
        calculate([order(status="READY", ready=1014, version=3)], 1021)["L-K1"]["value"]
        == 0
    )


def test_full_population_over_twenty():
    orders = [dict(order(), orderId=f"ORD-{i}") for i in range(30)]
    result = calculate(orders, 1020)
    assert result["L-K1"]["sample"] == 30 and result["L-K2"]["value"] == "765.00"


def test_event_contract_does_not_hide_real_breach():
    event = event_for(order(status="PREPARING", version=2), "ORDER_PREPARING", 1001)
    assert validate_event(event) == event
    assert calculate([event["data"]], 1020)["L-K1"]["value"] == 100
    for key, value in [
        ("schemaVersion", 2),
        ("aggregateVersion", 0),
        ("aggregateId", "other"),
    ]:
        broken = deepcopy(event)
        broken[key] = value
        with pytest.raises(ValueError):
            validate_event(broken)


def test_ready_event_requires_observed_timestamp():
    with pytest.raises(ValueError):
        event_for(order(status="READY"), "ORDER_READY", 1002)


def test_coverage_detects_a_whole_missing_aggregate():
    left = [order(), dict(order(), orderId="ORD-2")]
    assert coverage_digest(left) != coverage_digest(left[1:])
    assert coverage_digest(left) == coverage_digest(list(reversed(left)))
    assert coverage_digest(left) != coverage_digest([left[0], dict(left[1], version=2)])


@pytest.mark.parametrize("raw", [None, b"not json", b"[]", b"null", b"\xff"])
def test_malformed_messages_are_quarantinable(raw):
    value = decode_message(raw)
    assert value["_malformed"]
    with pytest.raises(ValueError):
        validate_event(value)


@pytest.mark.parametrize("position", ["1", True, 0, -1])
def test_bad_source_position_is_rejected_before_offset_work(position):
    event = event_for(order(), "ORDER_CREATED", 1001)
    event["sourcePosition"] = position
    with pytest.raises(ValueError):
        validate_event(event)
