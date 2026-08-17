"""Fetch and verify the official OpenRocket 24.12 JAR asset.

Downloads OpenRocket-24.12.jar directly from official GitHub releases
and verifies its SHA-256 checksum against known release artifacts.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LIB_DIR = REPO_ROOT / "lib"
DEFAULT_JAR_PATH = LIB_DIR / "OpenRocket-24.12.jar"

OPENROCKET_VERSION = "24.12"
OPENROCKET_RELEASE_TAG = "release-24.12"
OPENROCKET_JAR_NAME = "OpenRocket-24.12.jar"
OPENROCKET_URL = (
    f"https://github.com/openrocket/openrocket/releases/download/"
    f"{OPENROCKET_RELEASE_TAG}/{OPENROCKET_JAR_NAME}"
)

# Known release SHA-256 hashes (release-24.12)
EXPECTED_SHA256 = "4959b72f52f5f607941e9722abbb7b7f0c4a38ebbbf84204a329db9f31c4f897"


def compute_sha256(path: Path) -> str:
    """Compute SHA-256 hex digest of a file in streaming chunks."""
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            hasher.update(chunk)
    return hasher.hexdigest()


def verify_jar(jar_path: Path = DEFAULT_JAR_PATH) -> bool:
    """Verify that the JAR file exists and matches the expected SHA-256 hash."""
    if not jar_path.is_file():
        return False
    actual_hash = compute_sha256(jar_path)
    return actual_hash.lower() == EXPECTED_SHA256.lower()


def fetch_openrocket_jar(
    target_path: Path = DEFAULT_JAR_PATH,
    force: bool = False,
    quiet: bool = False,
) -> Path:
    """Download OpenRocket JAR from GitHub release if missing or checksum mismatch."""
    target_path = target_path.resolve()
    target_path.parent.mkdir(parents=True, exist_ok=True)

    if not force and target_path.is_file():
        if verify_jar(target_path):
            if not quiet:
                print(f"[OK] OpenRocket JAR already present and verified: {target_path}")
            return target_path
        if not quiet:
            print(f"[!] Checksum mismatch on existing JAR. Re-downloading...")

    tmp_path = target_path.with_suffix(".tmp")
    if not quiet:
        print(f"[*] Downloading OpenRocket {OPENROCKET_VERSION} from:\n    {OPENROCKET_URL}")
        print(f"[*] Destination: {target_path}")

    req = urllib.request.Request(
        OPENROCKET_URL,
        headers={"User-Agent": "L2-Rocket-Platform-Setup/1.0"},
    )

    try:
        with urllib.request.urlopen(req) as resp, tmp_path.open("wb") as out_file:
            total_size = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            block_size = 1024 * 1024  # 1 MB

            while True:
                buffer = resp.read(block_size)
                if not buffer:
                    break
                downloaded += len(buffer)
                out_file.write(buffer)
                if not quiet and total_size > 0:
                    pct = downloaded / total_size * 100
                    mb_cur = downloaded / (1024 * 1024)
                    mb_tot = total_size / (1024 * 1024)
                    sys.stdout.write(f"\r    Downloading: {mb_cur:.1f} / {mb_tot:.1f} MB ({pct:.1f}%)")
                    sys.stdout.flush()

        if not quiet and total_size > 0:
            sys.stdout.write("\n")

        # Verify downloaded checksum
        actual_hash = compute_sha256(tmp_path)
        if actual_hash.lower() != EXPECTED_SHA256.lower():
            tmp_path.unlink(missing_ok=True)
            raise ValueError(
                f"SHA-256 verification failed for downloaded JAR!\n"
                f"  Expected: {EXPECTED_SHA256}\n"
                f"  Actual:   {actual_hash}"
            )

        # Atomic replacement
        if target_path.exists():
            target_path.unlink()
        tmp_path.rename(target_path)

        if not quiet:
            print(f"[OK] Successfully verified and installed OpenRocket JAR:\n     {target_path}")
            print(f"     SHA-256: {actual_hash}")

        return target_path

    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Download and verify official OpenRocket 24.12 JAR."
    )
    parser.add_argument(
        "--dest",
        type=Path,
        default=DEFAULT_JAR_PATH,
        help=f"Target path for JAR (default: {DEFAULT_JAR_PATH})",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Only verify presence and checksum without downloading",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-download even if already present and verified",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress download progress output",
    )

    args = parser.parse_args()

    if args.check:
        if verify_jar(args.dest):
            print(f"[OK] OpenRocket JAR verified: {args.dest}")
            return 0
        else:
            print(f"[FAIL] OpenRocket JAR missing or invalid: {args.dest}")
            return 1

    try:
        fetch_openrocket_jar(target_path=args.dest, force=args.force, quiet=args.quiet)
        return 0
    except Exception as exc:
        print(f"[ERROR] Failed to fetch OpenRocket JAR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
