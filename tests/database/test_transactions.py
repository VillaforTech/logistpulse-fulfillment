"""Real PostgreSQL transactions in disposable schemas; production tables stay untouched."""

from copy import deepcopy
import os
import time
import uuid
from unittest.mock import Mock
import pytest
import psycopg
from psycopg.rows import dict_row
from psycopg import sql
from fastapi.testclient import TestClient
from logist import analytics, storage, fulfillment, worker, relay
from logist.domain import event_for, transition, utc, coverage_digest, decode_message

pytestmark = pytest.mark.skipif(
    not os.getenv("RUN_DATABASE_TESTS"),
    reason="Explicit PostgreSQL integration runner required",
)


def connection_factory(url, schema):
    return lambda: psycopg.connect(
        url, row_factory=dict_row, options=f"-c search_path={schema}"
    )


@pytest.fixture
def domain_db(monkeypatch):
    schema = "test_" + uuid.uuid4().hex
    url = os.environ["FULFILLMENT_DB_URL"]
    with psycopg.connect(url) as db:
        db.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    factory = connection_factory(url, schema)
    for module in (storage, fulfillment, worker, relay):
        monkeypatch.setattr(module, "connect", factory)
    storage.migrate()
    worker.stop.clear()
    monkeypatch.setattr(worker, "PREPARATION_SECONDS", 0)
    yield factory
    with psycopg.connect(url) as db:
        db.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


@pytest.fixture
def analytics_db(monkeypatch):
    schema = "test_" + uuid.uuid4().hex
    url = os.environ["ANALYTICS_DB_URL"]
    with psycopg.connect(url) as db:
        db.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    factory = connection_factory(url, schema)
    monkeypatch.setattr(analytics, "dbconn", factory)
    analytics.migrate()
    yield factory
    with psycopg.connect(url) as db:
        db.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def create():
    response = TestClient(fulfillment.app).post(
        "/api/fulfillment/orders",
        json={"total": "25.50", "fixtureRunId": "database-test"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def query(factory, query, args=()):
    with factory() as db:
        return db.execute(query, args or None).fetchall()


def test_api_and_two_outbox_records_commit_together(domain_db):
    order = create()
    assert len(query(domain_db, "SELECT * FROM orders")) == 1
    records = query(domain_db, "SELECT * FROM outbox ORDER BY sequence")
    assert len(records) == 2 and records[0]["envelope"]["data"]["total"] == "25.50"
    assert records[0]["envelope"]["eventId"] == order["eventId"]
    assert all(r["published_at"] is None for r in records)


def test_outbox_error_rolls_back_order(domain_db, monkeypatch):
    def fail(*args):
        raise RuntimeError("controlled outbox error")

    monkeypatch.setattr(fulfillment, "enqueue", fail)
    with pytest.raises(RuntimeError):
        create()
    assert query(domain_db, "SELECT * FROM orders") == []
    assert query(domain_db, "SELECT * FROM outbox") == []


def test_broker_failure_rolls_back_publish_marks_and_retries_same_ids(domain_db):
    create()
    producer = Mock()
    sent = []

    def send(topic, **kw):
        sent.append(
            kw["value"]["eventId"]
            if "eventId" in kw["value"]
            else kw["value"]["commandId"]
        )
        future = Mock()
        future.get.side_effect = TimeoutError("broker down") if len(sent) == 2 else None
        return future

    producer.send.side_effect = send
    with pytest.raises(TimeoutError):
        relay.relay_once(producer)
    assert all(
        r["published_at"] is None for r in query(domain_db, "SELECT * FROM outbox")
    )
    producer.send.side_effect = lambda topic, **kw: Mock()
    assert relay.relay_once(producer) == 2
    assert (
        query(domain_db, "SELECT event_id FROM outbox ORDER BY sequence")[0]["event_id"]
        == sent[0]
    )


def test_duplicate_commands_do_not_reopen_or_retime_ready(domain_db):
    order = create()
    cmd = {"orderId": order["orderId"]}
    worker.process(cmd, "command-1")
    before = fulfillment.order(order["orderId"])
    worker.process(cmd, "command-1")
    worker.process(cmd, "command-2")
    after = fulfillment.order(order["orderId"])
    assert (
        before == after
        and after["status"] == "READY"
        and after["readyAt"] - after["createdAt"] <= 15
    )
    assert [
        r["envelope"]["eventType"]
        for r in query(
            domain_db,
            "SELECT envelope FROM outbox WHERE topic LIKE '%events%' ORDER BY sequence",
        )
    ] == ["ORDER_CREATED", "ORDER_PREPARING", "ORDER_READY"]


def test_interrupted_worker_resumes_preparing(domain_db, monkeypatch):
    order = create()
    cmd = {"orderId": order["orderId"]}
    worker.stop.set()
    with pytest.raises(RuntimeError):
        worker.process(cmd, "command-1")
    assert fulfillment.order(order["orderId"])["status"] == "PREPARING"
    assert query(domain_db, "SELECT * FROM kitchen_inbox") == []
    worker.stop.clear()
    worker.process(cmd, "command-1")
    assert fulfillment.order(order["orderId"])["status"] == "READY"


def events():
    now = time.time() - 20
    order = {
        "orderId": "ORD-A",
        "storeId": "STORE-042",
        "channel": "TEST",
        "total": "25.50",
        "unit": "DEMO",
        "status": "WAITING",
        "createdAt": utc(now),
        "updatedAt": utc(now),
        "readyAt": None,
        "version": 1,
        "fixtureRunId": "db",
        "correlationId": "db",
    }
    preparing, _ = transition(order, "PREPARING", now + 1)
    ready, _ = transition(preparing, "READY", now + 4)
    return [
        dict(event_for(o, t, time.time()), sourcePosition=i + 1)
        for i, (o, t) in enumerate(
            zip(
                [order, preparing, ready],
                ["ORDER_CREATED", "ORDER_PREPARING", "ORDER_READY"],
            )
        )
    ]


def ingest(event, offset):
    analytics.persist_event(event, "facts", 0, offset)


def test_reorder_gap_replay_and_duplicate_are_durable(analytics_db):
    facts = events()
    ingest(facts[2], 0)
    assert len(query(analytics_db, "SELECT * FROM revision_gaps")) == 2
    ingest(facts[0], 1)
    ingest(facts[1], 2)
    ingest(facts[1], 3)
    assert query(analytics_db, "SELECT * FROM revision_gaps") == []
    assert len(query(analytics_db, "SELECT * FROM processed_events")) == 3
    assert (
        query(analytics_db, "SELECT data FROM projection")[0]["data"]["status"]
        == "READY"
    )
    assert (
        query(analytics_db, "SELECT next_offset FROM consumer_offsets")[0][
            "next_offset"
        ]
        == 4
    )


@pytest.mark.parametrize("conflict", ["event_id", "aggregate_version", "malformed"])
def test_conflict_or_poison_is_quarantined_with_checkpoint(analytics_db, conflict):
    facts = events()
    ingest(facts[0], 0)
    bad = deepcopy(facts[0])
    if conflict == "event_id":
        bad["data"]["total"] = "99.99"
    elif conflict == "aggregate_version":
        bad["eventId"] = str(uuid.uuid4())
    else:
        bad = decode_message(b"not valid json")
    ingest(bad, 1)
    assert len(query(analytics_db, "SELECT * FROM quarantine")) == 1
    assert (
        query(analytics_db, "SELECT next_offset FROM consumer_offsets")[0][
            "next_offset"
        ]
        == 2
    )
    assert (
        query(analytics_db, "SELECT data FROM projection")[0]["data"]["total"]
        == "25.50"
    )


def test_failed_offset_transaction_does_not_advance_projection(
    analytics_db, monkeypatch
):
    original = analytics.set_meta

    def fail(*args):
        raise RuntimeError("before checkpoint")

    monkeypatch.setattr(analytics, "set_meta", fail)
    with pytest.raises(RuntimeError):
        ingest(events()[0], 0)
    assert query(analytics_db, "SELECT * FROM processed_events") == []
    assert query(analytics_db, "SELECT * FROM projection") == []
    assert query(analytics_db, "SELECT * FROM consumer_offsets") == []


@pytest.mark.parametrize(
    "problem", ["missing_aggregate", "pending", "digest", "lag", "stale"]
)
def test_incomplete_source_cannot_appear_healthy(analytics_db, monkeypatch, problem):
    fact = events()[0]
    ingest(fact, 0)
    now = time.time()
    with analytics_db() as db:
        analytics.set_meta(db, "coverage", {"complete": True})
    state = dict(
        ready=True,
        lastPoll=now,
        sourceCheckedAt=now,
        sourcePosition=1,
        lag=0,
        sourcePending=0,
        aggregateCount=1,
        versionDigest=coverage_digest([fact["data"]]),
    )
    if problem == "missing_aggregate":
        state["aggregateCount"] = 2
    if problem == "pending":
        state["sourcePending"] = 1
    if problem == "digest":
        state["versionDigest"] = "wrong"
    if problem == "lag":
        state["lag"] = 1
    if problem == "stale":
        state["sourceCheckedAt"] = now - 4
    monkeypatch.setattr(analytics, "state", state)
    with analytics_db() as db:
        value = analytics.snapshot_data(db, now)
    assert value["quality"]["status"] != "FRESH"
    assert not value["coverage"]["complete"] and value["kpis"]["L-K1"]["value"] is None


def test_timer_persists_activation_and_late_resolution(analytics_db, monkeypatch):
    fact = events()[0]
    ingest(fact, 0)
    now = time.time()
    with analytics_db() as db:
        analytics.set_meta(db, "coverage", {"complete": True})
    monkeypatch.setattr(
        analytics,
        "state",
        dict(
            ready=True,
            lastPoll=now,
            sourceCheckedAt=now,
            sourcePosition=1,
            lag=0,
            sourcePending=0,
            aggregateCount=1,
            versionDigest=coverage_digest([fact["data"]]),
        ),
    )
    analytics.commit_snapshot()
    assert len(query(analytics_db, "SELECT * FROM alerts WHERE active")) == 3
    first = analytics.current()
    assert first["kpis"]["L-K3"]["value"] > 0
    # Timer work is repeatable and does not accumulate debt a second time.
    analytics.commit_snapshot()
    assert len(query(analytics_db, "SELECT * FROM alert_history")) == 3
    facts = events()
    facts[0] = fact
    preparing, _ = transition(fact["data"], "PREPARING", now - 18)
    ready, _ = transition(preparing, "READY", now - 16)
    ingest(dict(event_for(preparing, "ORDER_PREPARING", now), sourcePosition=2), 1)
    ingest(dict(event_for(ready, "ORDER_READY", now), sourcePosition=3), 2)
    analytics.state.update(sourcePosition=3, versionDigest=coverage_digest([ready]))
    analytics.commit_snapshot()
    assert query(analytics_db, "SELECT * FROM alerts WHERE active") == []
    assert len(query(analytics_db, "SELECT * FROM alert_history")) == 6


def test_analytics_role_cannot_connect_to_fulfillment():
    with pytest.raises(psycopg.OperationalError):
        psycopg.connect(
            os.environ["ANALYTICS_DB_URL"], dbname="fulfillment_db", connect_timeout=3
        )


def test_projection_keeps_more_than_twenty_orders(analytics_db, monkeypatch):
    facts = []
    for i in range(30):
        fact = events()[0]
        fact["aggregateId"] = fact["data"]["orderId"] = f"ORD-{i}"
        facts.append(fact)
        ingest(fact, i)
    now = time.time()
    with analytics_db() as db:
        analytics.set_meta(db, "coverage", {"complete": True})
    monkeypatch.setattr(
        analytics,
        "state",
        dict(
            ready=True,
            lastPoll=now,
            sourceCheckedAt=now,
            sourcePosition=1,
            lag=0,
            sourcePending=0,
            aggregateCount=30,
            versionDigest=coverage_digest([e["data"] for e in facts]),
        ),
    )
    with analytics_db() as db:
        value = analytics.snapshot_data(db, now)
    assert (
        value["quality"]["status"] == "FRESH"
        and value["coverage"]["aggregateCount"] == 30
    )
    assert (
        value["kpis"]["L-K1"]["sample"] == 30
        and value["kpis"]["L-K2"]["value"] == "765.00"
    )


def test_microsecond_creation_survives_postgres_numeric_adapter(domain_db, monkeypatch):
    monkeypatch.setattr(fulfillment.time, "time", lambda: 1789400000.123456)
    created = create()
    assert created["createdAtUtc"] == "2026-09-14T15:33:20.123456Z"
    fact = query(domain_db, "SELECT envelope FROM outbox WHERE topic LIKE '%events%'")[
        0
    ]["envelope"]
    assert fact["data"]["createdAt"] == fact["occurredAt"] == created["createdAtUtc"]
