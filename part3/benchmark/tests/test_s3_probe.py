from __future__ import annotations

import io

import pytest

from ducklake_s3_benchmark.config import ConfigError, PreflightConfig
from ducklake_s3_benchmark.s3_probe import build_campaign_key, run_s3_probe

from test_config import valid_config


class MissingObject(Exception):
    def __init__(self) -> None:
        self.response = {"Error": {"Code": "404"}}


class MemoryS3:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.calls: list[tuple[str, str]] = []

    def get_bucket_location(self, *, Bucket):
        self.calls.append(("get_bucket_location", Bucket))
        return {"LocationConstraint": "ap-southeast-1"}

    def list_objects_v2(self, *, Bucket, Prefix, MaxKeys):
        self.calls.append(("list_objects_v2", Prefix))
        return {"KeyCount": 0}

    def head_object(self, *, Bucket, Key):
        self.calls.append(("head_object", Key))
        try:
            body = self.objects[(Bucket, Key)]
        except KeyError as error:
            raise MissingObject() from error
        return {
            "ContentLength": len(body),
            "ETag": '"probe-etag"',
            "VersionId": "probe-version",
        }

    def put_object(self, *, Bucket, Key, Body, IfNoneMatch, Metadata):
        self.calls.append(("put_object", Key))
        assert IfNoneMatch == "*"
        assert (Bucket, Key) not in self.objects
        self.objects[(Bucket, Key)] = Body
        return {"ETag": '"probe-etag"', "VersionId": "probe-version"}

    def get_object(self, *, Bucket, Key):
        self.calls.append(("get_object", Key))
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}

    def delete_object(self, *, Bucket, Key, VersionId=None):
        self.calls.append(("delete_object", Key))
        self.objects.pop((Bucket, Key))
        return {}


def enabled_config() -> PreflightConfig:
    payload = valid_config()
    payload["allow_remote_probe"] = True
    return PreflightConfig.from_mapping(payload)


def test_key_builder_cannot_escape_campaign_prefix() -> None:
    config = enabled_config()

    assert build_campaign_key(config, "preflight/probe.txt").startswith(config.campaign_prefix)
    for unsafe in ("../escape", "/absolute", "preflight/../../escape", ""):
        with pytest.raises(ConfigError):
            build_campaign_key(config, unsafe)


def test_remote_probe_is_doubly_gated() -> None:
    config = PreflightConfig.from_mapping(valid_config())

    with pytest.raises(ConfigError, match="not enabled"):
        run_s3_probe(config, MemoryS3(), confirmation=False)
    with pytest.raises(ConfigError, match="confirmation"):
        run_s3_probe(enabled_config(), MemoryS3(), confirmation=False)


def test_remote_probe_checks_region_and_round_trip_without_leaking_names() -> None:
    config = enabled_config()
    client = MemoryS3()

    report = run_s3_probe(config, client, confirmation=True, probe_id="fixed-probe")

    assert report["state"] == "passed"
    assert report["region_match"] is True
    assert report["round_trip_match"] is True
    assert report["cleanup_confirmed"] is True
    assert client.objects == {}
    assert config.s3_bucket not in str(report)
    assert config.approved_prefix not in str(report)
    assert all(
        value.startswith(config.campaign_prefix)
        for operation, value in client.calls
        if operation != "get_bucket_location"
    )


def test_region_mismatch_stops_before_any_write() -> None:
    config = enabled_config()
    client = MemoryS3()
    client.get_bucket_location = lambda **_: {"LocationConstraint": "us-east-1"}

    report = run_s3_probe(config, client, confirmation=True, probe_id="fixed-probe")

    assert report["state"] == "stopped"
    assert report["region_match"] is False
    assert not any(operation == "put_object" for operation, _ in client.calls)
