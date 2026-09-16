#!/usr/bin/env python3
"""Independent API oracle. Health is recorded separately from the READY promise."""
import argparse, json, os, sys, time, uuid
from pathlib import Path
import httpx

p = argparse.ArgumentParser()
p.add_argument("--base", default=os.getenv("BASE_URL", "http://localhost:28080"))
p.add_argument("--output", default="artifacts/business")
a = p.parse_args()
out = Path(a.output)
out.mkdir(parents=True, exist_ok=True)
fixture = "business-" + str(uuid.uuid4())
evidence = {
    "fixtureRunId": fixture,
    "startedAt": time.time(),
    "observations": [],
    "technical": {},
    "expected": {
        "status": "READY",
        "deadlineSeconds": 15,
        "total": "25.50",
        "unit": "DEMO",
    },
}
client = httpx.Client(base_url=a.base, timeout=5)


def save():
    (out / "evidence.json").write_text(json.dumps(evidence, indent=2))


def get(path):
    r = client.get(path)
    r.raise_for_status()
    return r.json()


try:
    for service in (
        "inventory",
        "logistics",
        "operations",
        "fulfillment",
        "worker",
        "relay",
        "analytics",
        "live",
    ):
        evidence["technical"][service] = get("/health/" + service)
        assert evidence["technical"][service]["status"] == "UP"
    response = client.post(
        "/api/fulfillment/orders",
        json={
            "storeId": "STORE-042",
            "channel": "BUSINESS-CI",
            "total": "25.50",
            "fixtureRunId": fixture,
        },
        headers={"X-Correlation-ID": fixture},
    )
    response.raise_for_status()
    order = response.json()
    evidence["created"] = order
    save()
    deadline = time.monotonic() + 22
    while time.monotonic() < deadline:
        observed = get("/api/fulfillment/orders/" + order["orderId"])
        snap = get("/api/business/snapshot?fixtureRunId=" + fixture)
        evidence["observations"].append(
            {"at": time.time(), "order": observed, "snapshot": snap}
        )
        age = time.time() - order["createdAt"]
        if (
            age >= 19
            and snap["quality"]["status"] == "FRESH"
            and snap["kpis"]["L-K1"]["sample"] == 1
        ):
            break
        time.sleep(0.2)
    evidence["final"] = observed
    evidence["snapshot"] = snap
    persisted = get("/api/business/snapshot")
    evidence["persistedTimerSnapshot"] = persisted
    for service in tuple(evidence["technical"]):
        evidence["technical"][service] = get("/health/" + service)
    # A real no-new-event deadline sample is saved even when a regression blocks READY.
    save()
    assert observed["orderId"] == order["orderId"] and observed["totalExact"] == "25.50"
    assert (
        observed["status"] == "READY"
    ), f"Business breach: {observed['status']} after {time.time()-order['createdAt']:.2f}s; health does not prove fulfillment"
    assert observed["readyAt"] is not None
    assert (
        0 <= observed["readyAt"] - observed["createdAt"] <= 15
    ), "READY missed 15-second promise"
    assert snap["quality"]["status"] == "FRESH" and snap["coverage"]["complete"]
    from datetime import datetime

    assert (
        time.time()
        - datetime.fromisoformat(snap["computedAt"].replace("Z", "+00:00")).timestamp()
        < 3
    )
    assert persisted["quality"]["status"] == "FRESH"
    assert (
        time.time()
        - datetime.fromisoformat(
            persisted["computedAt"].replace("Z", "+00:00")
        ).timestamp()
        < 3
    )
    assert (
        datetime.fromisoformat(
            persisted["computedAt"].replace("Z", "+00:00")
        ).timestamp()
        >= order["createdAt"] + 15
    ), "Persisted timer never crossed the deadline"
    assert snap["kpis"]["L-K1"]["sample"] == 1 and snap["kpis"]["L-K1"]["value"] == 0
    assert (
        snap["kpis"]["L-K2"]["value"] == "0.00" and snap["kpis"]["L-K3"]["value"] == 0
    )
    evidence.update(passed=True, completedAt=time.time())
    save()
    print(
        "Business contract PASS: exact order READY on time; full independent projection agrees."
    )
except Exception as exc:
    evidence.update(passed=False, error=str(exc), completedAt=time.time())
    save()
    print(f"Business contract BLOCK: {exc}", file=sys.stderr)
    sys.exit(1)
