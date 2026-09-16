"""Kitchen command consumer. State and emitted facts commit before Kafka offset."""

from contextlib import asynccontextmanager
import json
import logging
import os
import threading
import time
from fastapi import FastAPI, HTTPException
from kafka import KafkaConsumer
from .domain import COMMANDS_TOPIC, transition
from .storage import (
    COLUMNS,
    connect,
    migrate,
    record_transition,
    snapshot,
    update_order,
)
from .metrics import instrument

log = logging.getLogger(__name__)
state = {"ready": False, "lastPoll": 0}
stop = threading.Event()
PREPARATION_SECONDS = 4.0


def finish_preparation(order, now):
    # Controlled regression target: omitting this transition keeps technology UP.
    return transition(order, "READY", now)


def process(command, command_id):
    oid = command["orderId"]
    with connect() as db:
        if db.execute(
            "SELECT 1 FROM kitchen_inbox WHERE command_id=%s", (command_id,)
        ).fetchone():
            return
        row = db.execute(
            "SELECT " + COLUMNS + " FROM orders WHERE order_id=%s FOR UPDATE", (oid,)
        ).fetchone()
        if row is None:
            raise ValueError("Command references a missing order")
        if row["status"] == "READY":
            db.execute(
                "INSERT INTO kitchen_inbox VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                (command_id, oid, time.time()),
            )
            return
        preparing, changed = transition(snapshot(row), "PREPARING", time.time())
        if changed:
            update_order(db, preparing)
            record_transition(db, preparing, "ORDER_PREPARING", time.time())
            started = time.time()
        else:
            started = float(row["preparing_at"] or row["updated_at"])
    if stop.wait(max(0, PREPARATION_SECONDS - (time.time() - started))):
        raise RuntimeError(
            "Worker stopping before completion; command remains uncommitted"
        )
    with connect() as db:
        row = db.execute(
            "SELECT " + COLUMNS + " FROM orders WHERE order_id=%s FOR UPDATE", (oid,)
        ).fetchone()
        ready, changed = finish_preparation(snapshot(row), time.time())
        if changed:
            update_order(db, ready)
            record_transition(db, ready, "ORDER_READY", time.time())
        db.execute(
            "INSERT INTO kitchen_inbox VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
            (command_id, oid, time.time()),
        )


def run():
    while not stop.is_set():
        consumer = None
        try:
            consumer = KafkaConsumer(
                COMMANDS_TOPIC,
                bootstrap_servers=os.environ["KAFKA_BOOTSTRAP"],
                group_id="kitchen-worker",
                enable_auto_commit=False,
                auto_offset_reset="earliest",
                value_deserializer=lambda b: json.loads(b.decode()),
                max_poll_records=1,
            )
            while not stop.is_set():
                batches = consumer.poll(timeout_ms=200, max_records=1)
                state.update(ready=bool(consumer.assignment()), lastPoll=time.time())
                for messages in batches.values():
                    for message in messages:
                        command = message.value
                        # Old ORDER_CREATED commands remain consumable; their partition/offset is stable.
                        command_id = (
                            command.get("commandId")
                            or f"legacy:{message.topic}:{message.partition}:{message.offset}"
                        )
                        process(command, command_id)
                        consumer.commit()
                        state["lastPoll"] = time.time()
        except Exception as exc:
            state["ready"] = False
            log.warning("Kitchen command will retry after %s", type(exc).__name__)
            stop.wait(0.5)
        finally:
            if consumer is not None:
                consumer.close(autocommit=False)


@asynccontextmanager
async def lifespan(app):
    migrate()
    stop.clear()
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    yield
    stop.set()
    thread.join(timeout=6)


app = FastAPI(title="Fulfillment Kitchen Worker", lifespan=lifespan)
instrument(app, "fulfillment-worker")


@app.get("/health")
def health():
    if not state["ready"] or time.time() - state["lastPoll"] > 15:
        raise HTTPException(503, "Kitchen consumer not ready")
    with connect() as db:
        db.execute("SELECT command_id FROM kitchen_inbox LIMIT 1")
    return {"status": "UP", **state}
