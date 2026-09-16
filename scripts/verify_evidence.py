#!/usr/bin/env python3
"""Success evidence is mandatory; diagnostic capture remains useful in a red run."""
from pathlib import Path
import json
import math
import xml.etree.ElementTree as ET

root = Path("artifacts")


def read(path):
    value = json.loads((root / path).read_text())
    assert isinstance(value, dict), path
    return value


business = read("business/evidence.json")
stream = read("streaming/measurements.json")
resilience = read("resilience/evidence.json")
assert business["passed"] and resilience["passed"] and stream["passed"]
assert (
    stream["requested"] == stream["observed"] == len(stream["samples"]) == 100
    and stream["lost"] == 0
)
assert (
    stream["p95Ms"] < 1000
    and stream["transport"]["frames"] > 0
    and stream["transport"]["channels"]
)
assert all(
    channel == {"organizationId": 1, "wireChannel": "1/stream/logistpulse/business", "logicalChannel": "stream/logistpulse/business"}
    for channel in stream["transport"]["channels"]
)
assert len({s["correlation"] for s in stream["samples"]}) == 100
latencies = sorted(float(s["latencyMs"]) for s in stream["samples"])
assert all(math.isfinite(value) and value >= 0 for value in latencies)
assert abs(latencies[94] - stream["p95Ms"]) < 0.001
for sample in stream["samples"]:
    assert sample["rendered"] and len(sample["cards"]) == 3
    assert {c["logistpulsePanel"] for c in sample["cards"]} == {"lk1", "lk2", "lk3"}
    assert len({c["revision"] for c in sample["cards"]}) == 1
    assert len({c["eventId"] for c in sample["cards"]}) == 1
    assert all(
        c["quality"] == "FRESH"
        and c["aggregateId"] == sample["orderId"]
        and c["correlationId"] == sample["correlation"]
        and c["eventId"]
        and int(c["revision"]) > 0
        and math.isfinite(float(c["value"]))
        and math.isfinite(float(sample["expected"][c["logistpulsePanel"]]))
        and abs(float(c["value"]) - sample["expected"][c["logistpulsePanel"]]) <= 0.01
        for c in sample["cards"]
    )
for key in ("deadline", "browser", "adapter", "grafana"):
    assert stream["visualChecks"][key]["passed"]
deadline = stream["visualChecks"]["deadline"]
assert deadline["thresholdMs"] == 1000
assert 0 <= deadline["snapshotDelayMs"] <= 1000
assert 0 <= deadline["renderDelayMs"] <= 1000
for key in ("browser", "adapter", "grafana"):
    test = stream["visualChecks"][key]
    assert test["reload"] is False and all(
        c["quality"] == "STALE" for c in test["stale"]
    )
    assert all(
        c["quality"] == "FRESH"
        and int(c["revision"]) > int(test["before"][0]["revision"])
        for c in test["recovered"]
    )
xml = ET.parse(root / "database-tests.xml").getroot()
suites = list(xml.iter("testsuite"))
assert sum(int(s.attrib["tests"]) for s in suites) > 0
assert all(
    int(s.attrib.get(k, 0)) == 0
    for s in suites
    for k in ("failures", "errors", "skipped")
)
print(
    "Success evidence verified: SQL, exact business contract, 100 coherent renders, real deadline and recovery."
)
