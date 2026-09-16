"""At-least-once relay. Broker acknowledgement precedes the outbox checkpoint."""

from contextlib import asynccontextmanager
import json
import logging
import os
import threading
import time
from fastapi import FastAPI, HTTPException
from kafka import KafkaProducer
from kafka.admin import KafkaAdminClient, NewTopic
from kafka.errors import TopicAlreadyExistsError
from .domain import COMMANDS_TOPIC, FACTS_TOPIC
from .storage import connect, migrate
from .metrics import instrument

log = logging.getLogger(__name__)
state = {"ready": False, "lastPoll": 0, "published": 0}
stop = threading.Event()


def setup_topics():
    admin = KafkaAdminClient(
        bootstrap_servers=os.environ["KAFKA_BOOTSTRAP"], request_timeout_ms=5000
    )
    try:
        for name in (COMMANDS_TOPIC, FACTS_TOPIC):
            try:
                admin.create_topics(
                    [NewTopic(name, 1, 1, topic_configs={"retention.ms": "604800000"})]
                )
            except TopicAlreadyExistsError:
                pass
    finally:
        admin.close()


def relay_once(producer):
    with connect() as db:
        rows = db.execute(
            """SELECT sequence,topic,aggregate_id,envelope FROM outbox
            WHERE published_at IS NULL ORDER BY sequence LIMIT 30 FOR UPDATE SKIP LOCKED"""
        ).fetchall()
        for row in rows:
            producer.send(
                row["topic"], key=row["aggregate_id"].encode(), value=row["envelope"]
            ).get(timeout=10)
            db.execute(
                "UPDATE outbox SET published_at=%s,attempts=attempts+1 WHERE sequence=%s",
                (time.time(), row["sequence"]),
            )
    return len(rows)


def run():
    producer = None
    while not stop.is_set():
        try:
            if producer is None:
                setup_topics()
                producer = KafkaProducer(
                    bootstrap_servers=os.environ["KAFKA_BOOTSTRAP"],
                    acks="all",
                    retries=3,
                    max_in_flight_requests_per_connection=1,
                    request_timeout_ms=5000,
                    max_block_ms=5000,
                    value_serializer=lambda v: json.dumps(v).encode(),
                )
            state["published"] += relay_once(producer)
            state.update(ready=True, lastPoll=time.time())
            stop.wait(0.1)
        except Exception as exc:
            state["ready"] = False
            log.warning("Outbox will retry after %s", type(exc).__name__)
            if producer is not None:
                producer.close(timeout=1)
                producer = None
            stop.wait(0.5)
    if producer is not None:
        producer.close(timeout=1)


@asynccontextmanager
async def lifespan(app):
    migrate()
    stop.clear()
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    yield
    stop.set()
    thread.join(timeout=12)


app = FastAPI(title="Fulfillment Outbox Relay", lifespan=lifespan)
instrument(app, "outbox-relay")


@app.get("/health")
def health():
    if not state["ready"] or time.time() - state["lastPoll"] > 15:
        raise HTTPException(503, "Outbox relay not ready")
    return {"status": "UP", **state}
