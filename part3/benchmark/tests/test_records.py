from __future__ import annotations

import json

import pytest

from ducklake_s3_benchmark.records import write_create_only_json


def test_json_record_is_private_canonical_and_create_only(tmp_path) -> None:
    path = tmp_path / "nested" / "status.json"

    write_create_only_json(path, {"z": 1, "a": "value"})

    assert path.read_text(encoding="utf-8") == '{"a":"value","z":1}\n'
    assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        write_create_only_json(path, {"replacement": True})


def test_non_finite_json_is_rejected(tmp_path) -> None:
    with pytest.raises(ValueError):
        write_create_only_json(tmp_path / "bad.json", {"latency": float("nan")})
