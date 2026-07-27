"""Process-level guardian for an idempotent OSIFOG campaign."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


TERMINAL_STATES = {"goal_reached", "budget_exhausted", "blocked"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError):
        return {}


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        process = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not process:
            return False
        exit_code = ctypes.c_ulong()
        try:
            return bool(
                ctypes.windll.kernel32.GetExitCodeProcess(
                    process, ctypes.byref(exit_code)
                )
                and exit_code.value == 259
            )
        finally:
            ctypes.windll.kernel32.CloseHandle(process)
    try:
        os.kill(pid, 0)
    except (OSError, SystemError):
        return False
    return True


def _acquire_watchdog_lease(root: Path) -> tuple[Path, str]:
    path = root / "watchdog.lease.json"
    token = f"{socket.gethostname()}:{os.getpid()}:{time.time_ns()}"
    record = {"pid": os.getpid(), "token": token, "started_at": _now()}
    while True:
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(record, stream, indent=2, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            return path, token
        except FileExistsError:
            incumbent = _read_json(path)
            if _pid_alive(int(incumbent.get("pid", -1))):
                raise RuntimeError(
                    f"watchdog already running as PID {incumbent['pid']}"
                )
            try:
                os.replace(path, root / f"watchdog.lease.stale-{time.time_ns()}.json")
            except FileNotFoundError:
                pass


def _campaign_pid(root: Path) -> int:
    return int(_read_json(root / "campaign.lease.json").get("pid", -1))


def _campaign_status(root: Path) -> str:
    return str(_read_json(root / "campaign-state.json").get("status", "starting"))


def _spawn(root: Path, command: list[str]) -> int:
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    stdout = (root / "campaign.stdout.log").open("a", encoding="utf-8")
    stderr = (root / "campaign.stderr.log").open("a", encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, *command],
        cwd=Path(__file__).resolve().parent,
        stdout=stdout,
        stderr=stderr,
        creationflags=creationflags,
    )
    stdout.close()
    stderr.close()
    return process.pid


def watch(root: Path, command: list[str], interval: float, max_restarts: int) -> int:
    root.mkdir(parents=True, exist_ok=True)
    lease_path, token = _acquire_watchdog_lease(root)
    restarts = 0
    missing_checks = 0
    try:
        while True:
            status = _campaign_status(root)
            campaign_pid = _campaign_pid(root)
            alive = _pid_alive(campaign_pid)
            _atomic_json(root / "watchdog.json", {
                "status": "watching" if alive else "recovering",
                "updated_at": _now(),
                "watchdog_pid": os.getpid(),
                "campaign_pid": campaign_pid if campaign_pid > 0 else None,
                "campaign_status": status,
                "restart_count": restarts,
            })
            if status in TERMINAL_STATES:
                _atomic_json(root / "watchdog-alert.json", {
                    "status": "clear", "updated_at": _now(),
                    "message": f"campaign ended normally: {status}",
                })
                return 0
            if alive:
                missing_checks = 0
                time.sleep(interval)
                continue
            missing_checks += 1
            if missing_checks < 2:
                time.sleep(min(interval, 5.0))
                continue
            if restarts >= max_restarts:
                _atomic_json(root / "watchdog-alert.json", {
                    "status": "operator_attention_required",
                    "updated_at": _now(),
                    "restart_count": restarts,
                    "message": "campaign process exceeded automatic restart budget",
                })
                return 2
            restarts += 1
            new_pid = _spawn(root, command)
            _atomic_json(root / "watchdog-alert.json", {
                "status": "auto_recovered", "updated_at": _now(),
                "restart_count": restarts, "new_pid": new_pid,
                "message": "campaign process was absent and has been restarted idempotently",
            })
            missing_checks = 0
            time.sleep(interval)
    finally:
        current = _read_json(lease_path)
        if current.get("token") == token:
            try:
                lease_path.unlink()
            except FileNotFoundError:
                pass


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--interval", type=float, default=15.0)
    parser.add_argument("--max-restarts", type=int, default=8)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("campaign command is required after --")
    return watch(args.root, command, args.interval, args.max_restarts)


if __name__ == "__main__":
    raise SystemExit(main())
