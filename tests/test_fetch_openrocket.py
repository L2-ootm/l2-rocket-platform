from pathlib import Path
import hashlib
import pytest
from scripts import fetch_openrocket


def test_compute_sha256_matches_known_content(tmp_path):
    test_file = tmp_path / "sample.bin"
    test_content = b"OpenRocket 24.12 test binary payload"
    test_file.write_bytes(test_content)

    expected = hashlib.sha256(test_content).hexdigest()
    assert fetch_openrocket.compute_sha256(test_file) == expected


def test_verify_jar_returns_false_for_missing_file(tmp_path):
    missing_jar = tmp_path / "nonexistent.jar"
    assert not fetch_openrocket.verify_jar(missing_jar)


def test_verify_jar_returns_false_for_corrupt_file(tmp_path):
    corrupt_jar = tmp_path / "corrupt.jar"
    corrupt_jar.write_bytes(b"invalid data")
    assert not fetch_openrocket.verify_jar(corrupt_jar)


def test_verify_jar_returns_true_for_valid_mock_hash(tmp_path, monkeypatch):
    mock_jar = tmp_path / "valid.jar"
    mock_jar.write_bytes(b"mock valid jar")
    mock_hash = hashlib.sha256(b"mock valid jar").hexdigest()

    monkeypatch.setattr(fetch_openrocket, "EXPECTED_SHA256", mock_hash)
    assert fetch_openrocket.verify_jar(mock_jar)
