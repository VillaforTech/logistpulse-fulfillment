from contextlib import asynccontextmanager
from decimal import Decimal
import time
import uuid
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from .domain import COMMANDS_TOPIC, FACTS_TOPIC, money, coverage_digest
from .storage import (
    COLUMNS,
    api_view,
    connect,
    enqueue,
    migrate,
    record_transition,
    snapshot,
)


@asynccontextmanager
async def lifespan(app):
    migrate()
    yield


app = FastAPI(title="LOGISTPULSE Fulfillment API", version="2.0.0", lifespan=lifespan)
from .metrics import instrument

instrument(app, "fulfillment-api")


class NewOrder(BaseModel):
    storeId: str = Field(default="STORE-042", min_length=1, max_length=80)
    channel: str = Field(default="MOBILE", min_length=1, max_length=40)
    total: Decimal = Field(
        default=Decimal("18.50"), gt=0, max_digits=12, decimal_places=2
    )
    fixtureRunId: str | None = Field(default=None, max_length=100)

    @field_validator("total")
    @classmethod
    def valid_total(cls, value):
        return Decimal(money(value))


@app.get("/health")
def health():
    try:
        with connect() as db:
            db.execute("SELECT order_id FROM orders LIMIT 1")
            db.execute("SELECT event_id FROM outbox LIMIT 1")
        return {"status": "UP", "service": "fulfillment-api", "database": "READY"}
    except Exception as exc:
        raise HTTPException(503, "Fulfillment database not ready") from exc


@app.get("/api/fulfillment/orders")
def orders():
    with connect() as db:
        rows = db.execute(
            "SELECT " + COLUMNS + " FROM orders ORDER BY created_at DESC LIMIT 20"
        ).fetchall()
    return [api_view(row) for row in rows]


@app.get("/api/fulfillment/orders/{order_id}")
def order(order_id: str):
    with connect() as db:
        row = db.execute(
            "SELECT " + COLUMNS + " FROM orders WHERE order_id=%s", (order_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(404, "Order not found")
    return api_view(row)


@app.post("/api/fulfillment/orders", status_code=201)
def create(body: NewOrder, request: Request):
    oid = "ORD-" + uuid.uuid4().hex.upper()
    now = Decimal(str(time.time())).quantize(Decimal(".000001"))
    correlation = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))[:100]
    with connect() as db:
        row = db.execute(
            """INSERT INTO orders(order_id,store_id,channel,total,status,created_at,
            updated_at,version,fixture_run_id,correlation_id) VALUES (%s,%s,%s,%s,'WAITING',%s,%s,1,%s,%s)
            RETURNING """
            + COLUMNS,
            (
                oid,
                body.storeId,
                body.channel,
                body.total,
                now,
                now,
                body.fixtureRunId,
                correlation,
            ),
        ).fetchone()
        fact = record_transition(db, snapshot(row), "ORDER_CREATED", now)
        enqueue(
            db,
            {
                "schemaVersion": 1,
                "commandId": str(uuid.uuid4()),
                "commandType": "PREPARE_ORDER",
                "event": "ORDER_CREATED",
                "orderId": oid,
                "correlationId": correlation,
                "fixtureRunId": body.fixtureRunId,
            },
            COMMANDS_TOPIC,
            now,
        )
    return dict(
        api_view(row), eventId=fact["eventId"], sourcePosition=fact["sourcePosition"]
    )


@app.get("/api/fulfillment/snapshot")
def source_snapshot():
    with connect() as db:
        db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        rows = db.execute(
            "SELECT " + COLUMNS + " FROM orders ORDER BY order_id"
        ).fetchall()
        position = db.execute(
            "SELECT COALESCE(max(sequence),0) AS n FROM outbox WHERE topic=%s",
            (FACTS_TOPIC,),
        ).fetchone()["n"]
    complete = all(
        row["status"] != "READY" or row["ready_at"] is not None for row in rows
    )
    return {
        "schemaVersion": 1,
        "orders": [snapshot(row) for row in rows],
        "sourcePosition": position,
        "coverage": {
            "complete": complete,
            "reason": "" if complete else "Legacy READY without observed readyAt",
        },
        "capturedAt": time.time(),
    }


@app.get("/api/fulfillment/watermark")
def watermark():
    with connect() as db:
        db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        rows = db.execute(
            'SELECT order_id AS "orderId",version FROM orders ORDER BY order_id'
        ).fetchall()
        row = db.execute(
            """SELECT COALESCE(max(sequence),0) AS position,
            count(*) FILTER (WHERE published_at IS NULL) AS pending FROM outbox WHERE topic=%s""",
            (FACTS_TOPIC,),
        ).fetchone()
    return {
        "sourcePosition": row["position"],
        "pending": row["pending"],
        "checkedAt": time.time(),
        "aggregateCount": len(rows),
        "versionDigest": coverage_digest(rows),
    }
