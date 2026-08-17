"""Shared, topology-agnostic primitives for long-running L2 campaigns:
atomic JSON writes, crash-recoverable exclusive leases, PID liveness checks,
and append-only event logs.

Extracted/consolidated from the two independent implementations that had
grown in `osifog_engine_search.py` and `osifog_campaign_watchdog.py` (same
patterns, slightly different code) so new campaign runners (this repo's
`organic_campaign.py`) share one tested implementation instead of a third
copy. `osifog_campaign_watchdog.py` is reused as-is as the external process
watchdog -- it only needs `campaign.lease.json`'s `pid` and
`campaign-state.json`'s `status`, both of which this module produces.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical_digest(value: object) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def atomic_json(path: Path, payload: dict) -> None:
    """Write JSON so a concurrent reader (a watchdog, a dashboard) never
    observes a partially-written file: write to a PID+timestamp-unique temp
    file in the same directory, fsync, then atomically rename over the
    target. Retries a few times to ride out a transient Windows sharing
    violation from an antivirus/indexer holding a brief read lock."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, indent=2, sort_keys=True)
    last_error = None
    for attempt in range(5):
        temporary = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.{attempt}.tmp")
        try:
            with temporary.open("w", encoding="utf-8") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            return
        except OSError as exc:
            last_error = exc
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            time.sleep(0.1 * (attempt + 1))
    raise last_error


def read_json(path: Path) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError):
        return {}


def write_health(output_dir: Path, status: str, phase: str, **details) -> None:
    atomic_json(
        Path(output_dir) / "health.json",
        {"status": status, "phase": phase, "updated_at": now_iso(), "pid": os.getpid(), **details},
    )


def append_event(root: Path, event: str, **details) -> None:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    record = {"at": now_iso(), "event": event, **details}
    with (root / "events.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(process_query_limited_information, False, int(pid))
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return int(exit_code.value) == still_active
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


@contextmanager
def campaign_lease(root: Path):
    """Exclusive, crash-recoverable lease. A second instance pointed at the
    same `root` while a live process holds the lease raises immediately
    (fail fast, do not silently corrupt shared state); a lease left behind
    by a process that has since died is detected via `pid_is_alive` and
    reclaimed automatically -- a killed/crashed campaign is always
    resumable, never permanently locked out."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / "campaign.lease.json"
    token = canonical_digest({"pid": os.getpid(), "host": socket.gethostname(), "time_ns": time.time_ns()})
    lease = {"token": token, "pid": os.getpid(), "host": socket.gethostname(), "started_at": now_iso()}
    while True:
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(lease, stream, indent=2, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            break
        except FileExistsError:
            incumbent = read_json(path)
            if pid_is_alive(int(incumbent.get("pid", -1))):
                raise RuntimeError(f"campaign already owned by live PID {incumbent['pid']}: {path}")
            stale = root / f"campaign.lease.stale-{time.time_ns()}.json"
            try:
                os.replace(path, stale)
            except FileNotFoundError:
                pass
    try:
        yield lease
    finally:
        current = read_json(path)
        if current.get("token") == token:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
