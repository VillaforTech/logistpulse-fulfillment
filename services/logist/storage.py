"""Transactions belong to Fulfillment; analytics uses a different restricted database."""

import logging
import os
import time
from decimal import Decimal
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from .domain import COMMANDS_TOPIC, FACTS_TOPIC, epoch, event_for, utc

log = logging.getLogger(__name__)
COLUMNS = "order_id,store_id,channel,total,status,created_at,updated_at,ready_at,version,fixture_run_id,correlation_id,preparing_at"


def connect(variable="FULFILLMENT_DB_URL"):
    return psycopg.connect(
        os.environ[variable], row_factory=dict_row, connect_timeout=3
    )


def retry_bootstrap(action, attempts=40, pause=1):
    for attempt in range(attempts):
        try:
            return action()
        except psycopg.OperationalError:
            log.warning("Database not ready (%s/%s)", attempt + 1, attempts)
            if attempt + 1 == attempts:
                raise
            time.sleep(pause)


def migrate():
    def run():
        with connect() as db:
            db.execute("SELECT pg_advisory_xact_lock(4008002)")
            db.execute(
                """CREATE TABLE IF NOT EXISTS orders (
                order_id text PRIMARY KEY, store_id text, channel text, total numeric,
                status text, created_at numeric, updated_at numeric)"""
            )
            for declaration in (
                "ready_at numeric",
                "version integer NOT NULL DEFAULT 1",
                "fixture_run_id text",
                "correlation_id text",
                "preparing_at numeric",
            ):
                db.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS " + declaration)
            db.execute(
                """CREATE TABLE IF NOT EXISTS outbox (
                sequence bigserial PRIMARY KEY, event_id text UNIQUE NOT NULL, topic text NOT NULL,
                aggregate_id text NOT NULL, envelope jsonb NOT NULL, created_at numeric NOT NULL,
                published_at numeric, attempts integer NOT NULL DEFAULT 0)"""
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS outbox_pending ON outbox(sequence) WHERE published_at IS NULL"
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS kitchen_inbox (
                command_id text PRIMARY KEY, order_id text NOT NULL, completed_at numeric NOT NULL)"""
            )

    retry_bootstrap(run)


def snapshot(row):
    return {
        "orderId": row["order_id"],
        "storeId": row["store_id"],
        "channel": row["channel"],
        "total": format(row["total"], ".2f"),
        "unit": "DEMO",
        "status": row["status"],
        "createdAt": utc(row["created_at"]),
        "updatedAt": utc(row["updated_at"]),
        "readyAt": utc(row["ready_at"]) if row["ready_at"] is not None else None,
        "version": row["version"],
        "fixtureRunId": row["fixture_run_id"],
        "correlationId": row["correlation_id"] or row["order_id"],
    }


def api_view(row):
    result = snapshot(row)
    result["totalExact"] = result["total"]
    result["total"] = float(
        row["total"]
    )  # Existing UI compatibility; events retain decimal strings.
    for key, column in [
        ("createdAt", "created_at"),
        ("updatedAt", "updated_at"),
        ("readyAt", "ready_at"),
    ]:
        result[key + "Utc"] = result[key]
        result[key] = float(row[column]) if row[column] is not None else None
    result["aggregateVersion"] = result["version"]
    return result


def enqueue(db, envelope, topic, now):
    event_id = envelope.get("eventId") or envelope["commandId"]
    row = db.execute(
        """INSERT INTO outbox(event_id,topic,aggregate_id,envelope,created_at)
        VALUES (%s,%s,%s,%s,%s) RETURNING sequence""",
        (
            event_id,
            topic,
            envelope.get("aggregateId") or envelope["orderId"],
            Jsonb(envelope),
            Decimal(str(now)),
        ),
    ).fetchone()
    envelope = dict(envelope, sourcePosition=row["sequence"])
    db.execute(
        "UPDATE outbox SET envelope=%s WHERE sequence=%s",
        (Jsonb(envelope), row["sequence"]),
    )
    return envelope


def record_transition(db, order, event_type, now):
    return enqueue(db, event_for(order, event_type, now), FACTS_TOPIC, now)


def update_order(db, order):
    db.execute(
        """UPDATE orders SET status=%s,updated_at=%s,ready_at=%s,version=%s,
        preparing_at=CASE WHEN %s='PREPARING' THEN COALESCE(preparing_at,%s) ELSE preparing_at END
        WHERE order_id=%s""",
        (
            order["status"],
            Decimal(str(epoch(order["updatedAt"]))),
            Decimal(str(epoch(order["readyAt"]))) if order.get("readyAt") else None,
            order["version"],
            order["status"],
            Decimal(str(epoch(order["updatedAt"]))),
            order["orderId"],
        ),
    )
