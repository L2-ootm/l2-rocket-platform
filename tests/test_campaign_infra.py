import json
import os

import pytest

from campaign_infra import atomic_json, campaign_lease, pid_is_alive, read_json


def test_atomic_json_write_and_read_roundtrip(tmp_path):
    path = tmp_path / "sub" / "value.json"
    atomic_json(path, {"a": 1, "b": [1, 2, 3]})
    assert read_json(path) == {"a": 1, "b": [1, 2, 3]}
    # no leftover temp files
    leftovers = [p for p in path.parent.iterdir() if p.name != "value.json"]
    assert leftovers == []


def test_atomic_json_overwrite_never_leaves_partial_file(tmp_path):
    path = tmp_path / "value.json"
    atomic_json(path, {"v": 1})
    atomic_json(path, {"v": 2, "extra": "x" * 10_000})
    assert read_json(path)["v"] == 2
    assert len(list(tmp_path.iterdir())) == 1


def test_read_json_missing_file_returns_empty_dict(tmp_path):
    assert read_json(tmp_path / "nope.json") == {}


def test_campaign_lease_blocks_concurrent_live_owner(tmp_path):
    with campaign_lease(tmp_path):
        with pytest.raises(RuntimeError, match="already owned by live PID"):
            with campaign_lease(tmp_path):
                pass
    # released after the context exits -- must be re-acquirable
    with campaign_lease(tmp_path):
        pass


def test_campaign_lease_reclaims_stale_dead_pid(tmp_path):
    lease_path = tmp_path / "campaign.lease.json"
    tmp_path.mkdir(parents=True, exist_ok=True)
    lease_path.write_text(
        json.dumps({"token": "stale", "pid": 999_999_999, "host": "nowhere", "started_at": "x"}),
        encoding="utf-8",
    )
    assert not pid_is_alive(999_999_999)
    with campaign_lease(tmp_path) as lease:
        assert lease["pid"] == os.getpid()
    assert not lease_path.exists()


def test_pid_is_alive_true_for_self_false_for_bogus():
    assert pid_is_alive(os.getpid()) is True
    assert pid_is_alive(999_999_999) is False
    assert pid_is_alive(-1) is False
