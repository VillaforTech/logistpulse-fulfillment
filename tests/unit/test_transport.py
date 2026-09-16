from copy import deepcopy
import time
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from logist import analytics, live
from logist.domain import calculate, utc, coverage_digest


def sample():
    return dict(
        computedAt=utc(1000),
        revision=1,
        quality={"status": "FRESH", "reason": ""},
        coverage={"complete": True},
        kpis=calculate([], 1000),
    )


def test_timer_freeze_invalidates_stored_snapshot_without_mutation():
    value = sample()
    aged = analytics.age_checked(value, 1004)
    assert aged["quality"]["status"] == "STALE" and not aged["coverage"]["complete"]
    assert all(k["value"] is None for k in aged["kpis"].values())
    assert value["quality"]["status"] == "FRESH"
    assert analytics.age_checked(value, 1003) is value


def test_live_line_has_explicit_no_sample_and_invalid_sentinels():
    value = sample()
    assert "lk1=-2" in live.line_protocol(value)
    assert "lk2=0.00" in live.line_protocol(value)
    value["quality"]["status"] = "INCOMPLETE"
    assert all(f"lk{i}=-1" in live.line_protocol(value) for i in range(1, 4))


def test_checkpoint_restores_every_assignment(monkeypatch):
    db = Mock()
    db.execute.return_value.fetchone.return_value = {"next_offset": 42}
    cm = Mock()
    cm.__enter__ = Mock(return_value=db)
    cm.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(analytics, "dbconn", lambda: cm)
    consumer = Mock()
    listener = analytics.CheckpointListener(consumer)
    partition = SimpleNamespace(topic="facts", partition=0)
    listener.on_partitions_assigned([partition])
    listener.on_partitions_revoked([partition])
    assert analytics.state["ready"] is False
    listener.on_partitions_assigned([partition])
    assert consumer.seek.call_count == 2
    consumer.seek.assert_called_with(partition, 42)
