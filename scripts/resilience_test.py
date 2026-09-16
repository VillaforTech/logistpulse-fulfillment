#!/usr/bin/env python3
"""Bounded chaos against this twin only; finally restores every stopped service."""
import json, subprocess, time, uuid
from pathlib import Path
import httpx

BASE = "http://localhost:28080"
client = httpx.Client(base_url=BASE, timeout=5)
out = Path("artifacts/resilience")
out.mkdir(parents=True, exist_ok=True)
result = {}


def compose(*args):
    subprocess.run(
        ["bash", "scripts/compose.sh", *args], check=True, stdout=subprocess.DEVNULL
    )


def get(path):
    r = client.get(path)
    r.raise_for_status()
    return r.json()


def wait(predicate, timeout=40):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            last = predicate()
            if last:
                return last
        except (httpx.HTTPError, KeyError):
            pass
        time.sleep(0.2)
    raise AssertionError(f"Recovery timed out, last={last}")


def fresh():
    s = get("/api/business/snapshot")
    return s if s["quality"]["status"] == "FRESH" else None


try:
    before = wait(fresh)
    result["before"] = before
    compose("stop", "redpanda")
    fixture = "broker-outage-" + str(uuid.uuid4())
    r = client.post(
        "/api/fulfillment/orders",
        json={"total": "25.50", "fixtureRunId": fixture},
        headers={"X-Correlation-ID": fixture},
    )
    r.raise_for_status()
    created = r.json()
    result["committedWhileBrokerDown"] = created
    stale = wait(
        lambda: (
            s
            if (s := get("/api/business/snapshot"))["quality"]["status"] != "FRESH"
            else None
        ),
        10,
    )
    assert all(v["value"] is None for v in stale["kpis"].values())
    result["invalidDuringOutage"] = stale
    compose("start", "redpanda")
    restored = wait(
        lambda: (
            o
            if (o := get("/api/fulfillment/orders/" + created["orderId"]))["status"]
            == "READY"
            else None
        ),
        60,
    )
    result["recoveredOrder"] = restored
    wait(fresh, 60)
    # Stopping analytics leaves its durable history; adapter must explicitly publish stale.
    cut = get("/api/fulfillment/watermark")
    result["confirmedSourceCut"] = cut
    old = wait(
        lambda: (
            s
            if (s := fresh())
            and s["watermark"]["versionDigest"] == cut["versionDigest"]
            and s["watermark"]["sourcePosition"] >= cut["sourcePosition"]
            else None
        ),
        30,
    )
    result["beforeAnalyticsRestart"] = old
    compose("stop", "business-analytics")
    time.sleep(3.5)
    result["adapterDuringAnalyticsOutage"] = get("/health/live")
    assert result["adapterDuringAnalyticsOutage"]["ready"] is False
    compose("start", "business-analytics")
    new = wait(fresh, 60)
    assert new["revision"] > old["revision"]
    assert new["coverage"]["aggregateCount"] == old["coverage"]["aggregateCount"]
    assert new["watermark"]["versionDigest"] == old["watermark"]["versionDigest"]
    assert new["watermark"]["sourcePosition"] == old["watermark"]["sourcePosition"]
    assert new["coverage"]["eventCount"] == old["coverage"]["eventCount"]
    assert (
        get("/api/fulfillment/orders/" + created["orderId"])["readyAt"]
        == restored["readyAt"]
    )
    result["afterAnalyticsRestart"] = new
    source = get("/api/fulfillment/snapshot")["orders"]
    from datetime import datetime
    from decimal import Decimal

    def epoch(value):
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()

    now = epoch(new["computedAt"])
    cohort = [o for o in source if now - 900 < epoch(o["createdAt"]) <= now - 15]
    breached = [
        o
        for o in cohort
        if o["readyAt"] is None or epoch(o["readyAt"]) > epoch(o["createdAt"]) + 15
    ]
    open_due = [
        o
        for o in source
        if o["status"] != "READY" and epoch(o["createdAt"]) + 15 <= now
    ]
    assert new["kpis"]["L-K1"]["sample"] == len(cohort)
    assert new["kpis"]["L-K1"]["value"] == (
        100 * len(breached) / len(cohort) if cohort else None
    )
    assert Decimal(new["kpis"]["L-K2"]["value"]) == sum(
        (Decimal(o["total"]) for o in open_due), Decimal(0)
    )
    assert (
        abs(
            new["kpis"]["L-K3"]["value"]
            - sum(max(0, now - epoch(o["createdAt"]) - 15) for o in open_due)
        )
        < 0.001
    )
    result["independentOracle"] = {
        "cohort": len(cohort),
        "breached": len(breached),
        "openOverdue": len(open_due),
    }
    compose("restart", "grafana-live-adapter")
    wait(lambda: get("/health/live")["ready"], 40)
    compose("restart", "grafana")
    wait(lambda: get("/health/live")["ready"], 60)
    result["afterGrafanaRestart"] = get("/health/live")
    result["passed"] = True
except Exception as exc:
    result.update(passed=False, error=str(exc))
    raise
finally:
    compose(
        "start", "redpanda", "business-analytics", "grafana", "grafana-live-adapter"
    )
    (out / "evidence.json").write_text(json.dumps(result, indent=2))
