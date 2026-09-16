"""Recoverable analytics snapshot to official Grafana Live line-protocol adapter."""

from contextlib import asynccontextmanager
import json
import logging
import os
import threading
import time
import httpx
import websocket
from fastapi import FastAPI, HTTPException
from prometheus_client import Gauge
from .domain import epoch
from .metrics import instrument

log = logging.getLogger(__name__)
ANALYTICS = os.getenv("ANALYTICS_URL", "http://business-analytics:8000")
GRAFANA = os.getenv("GRAFANA_URL", "http://grafana:3000")
STREAM = "logistpulse"
MEASUREMENT = "business"
state = {
    "ready": False,
    "lastPush": 0.0,
    "lastSnapshot": 0.0,
    "revision": -1,
    "errors": 0,
}
stop = threading.Event()
LAST_PUSH = Gauge(
    "logistpulse_live_last_success_timestamp_seconds",
    "Last successful Grafana Live push timestamp",
)


def line_protocol(snapshot):
    quality = snapshot["quality"]["status"]

    def value(key):
        item = snapshot["kpis"][key]
        if quality != "FRESH" or item["status"] in ("STALE", "INCOMPLETE"):
            return "-1"
        if item["status"] == "NO_SAMPLE":
            return "-2"
        return str(item["value"])

    fields = {f"lk{i}": value(f"L-K{i}") for i in (1, 2, 3)}
    fields.update(
        revision=str(snapshot["revision"]) + "i",
        sample=str(snapshot["kpis"]["L-K1"]["sample"]) + "i",
        quality=str({"FRESH": 0, "STALE": 1, "INCOMPLETE": 2}[quality]) + "i",
        aggregate_version=str(snapshot.get("sourceAggregateVersion") or 0) + "i",
        snapshot_epoch=str(epoch(snapshot["computedAt"])),
    )
    for key, val in [
        ("correlation_id", snapshot.get("correlationId") or ""),
        ("aggregate_id", snapshot.get("sourceAggregateId") or ""),
        ("source_event_id", snapshot.get("sourceEventId") or ""),
        ("quality_reason", snapshot["quality"].get("reason") or ""),
        ("computed_at", snapshot["computedAt"]),
    ]:
        fields[key] = json.dumps(val, ensure_ascii=False)
    return (
        MEASUREMENT
        + " "
        + ",".join(k + "=" + v for k, v in fields.items())
        + " "
        + str(time.time_ns())
    )


def publish(client, snapshot):
    response = client.post(
        GRAFANA + "/api/live/push/" + STREAM,
        content=line_protocol(snapshot),
        headers={"Content-Type": "text/plain"},
    )
    response.raise_for_status()
    state.update(lastPush=time.time(), ready=snapshot["quality"]["status"] == "FRESH")
    LAST_PUSH.set(state["lastPush"])


def run():
    last = None
    auth = (os.environ["GRAFANA_USER"], os.environ["GRAFANA_PASSWORD"])
    with httpx.Client(auth=auth, timeout=2) as client:
        while not stop.is_set():
            ws = None
            try:
                response = httpx.get(ANALYTICS + "/api/business/snapshot", timeout=2)
                response.raise_for_status()
                candidate = response.json()
                if candidate["revision"] >= state["revision"]:
                    last = candidate
                    state.update(revision=last["revision"], lastSnapshot=time.time())
                    publish(client, last)
                ws = websocket.create_connection(
                    ANALYTICS.replace("http", "ws", 1) + "/api/business/ws", timeout=2
                )
                while not stop.is_set():
                    candidate = json.loads(ws.recv())
                    if candidate["revision"] <= state["revision"]:
                        continue
                    last = candidate
                    state.update(revision=last["revision"], lastSnapshot=time.time())
                    publish(client, last)
            except Exception as exc:
                state.update(ready=False, errors=state["errors"] + 1)
                log.warning("Live adapter reconnecting after %s", type(exc).__name__)
                if last:
                    stale = dict(
                        last,
                        quality={
                            "status": "STALE",
                            "reason": "Analytics stream disconnected",
                        },
                    )
                    try:
                        publish(client, stale)
                    except Exception:
                        pass
                stop.wait(0.2)
            finally:
                if ws is not None:
                    ws.close()


@asynccontextmanager
async def lifespan(app):
    stop.clear()
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    yield
    stop.set()
    thread.join(timeout=4)


app = FastAPI(title="Grafana Live Adapter", lifespan=lifespan)
instrument(app, "live-adapter")


@app.get("/health")
def health():
    if time.time() - state["lastPush"] > 3:
        raise HTTPException(503, "Grafana Live transport unavailable")
    return {"status": "UP", "stream": f"stream/{STREAM}/{MEASUREMENT}", **state}
