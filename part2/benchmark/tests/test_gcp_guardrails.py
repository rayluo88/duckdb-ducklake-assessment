import json
from datetime import datetime, timedelta, timezone

import pytest

from ducklake_benchmark.gcp_guardrails import (
    GuardrailConfig,
    build_guardrail_bundle,
    verify_guardrail_bundle,
    write_guardrail_bundle,
)


NOW = datetime(2026, 8, 9, 4, 0, tzinfo=timezone.utc)


def test_guardrail_config_is_strictly_scoped_to_the_assessment_project() -> None:
    config = GuardrailConfig()

    assert config.project_id == "ducklake-example-project"
    assert config.project_number == "000000000000"
    assert config.region == "asia-southeast1"
    assert config.zone == "asia-southeast1-b"
    assert config.vm_name == "ducklake-p2-bench-01"
    assert config.bucket_name == "ducklake-example-project-data"
    assert config.network_name == "ducklake-p2-net"
    assert config.subnet_name == "ducklake-p2-subnet"
    assert config.subnet_cidr == "10.20.0.0/24"
    assert config.firewall_name == "ducklake-p2-allow-iap-ssh"
    assert config.network_tag == "ducklake-p2-iap"
    assert config.iap_source_range == "35.235.240.0/20"
    assert config.service_account_id == "ducklake-p2-reader"
    assert config.service_account_email == (
        "ducklake-p2-reader@ducklake-example-project.iam.gserviceaccount.com"
    )
    assert config.vm_runtime == timedelta(hours=6)
    assert config.bundle_validity == timedelta(minutes=15)
    assert config.object_ttl_days == 1


@pytest.mark.parametrize(
    "overrides",
    [
        {"project_id": "some-other-project"},
        {"project_number": "123"},
        {"region": "us-central1"},
        {"zone": "asia-southeast2-a"},
        {"vm_name": "unapproved-vm"},
        {"bucket_name": "unapproved-bucket"},
        {"network_name": "default"},
        {"subnet_name": "unapproved-subnet"},
        {"subnet_cidr": "10.0.0.0/8"},
        {"firewall_name": "allow-all"},
        {"network_tag": "ssh"},
        {"iap_source_range": "0.0.0.0/0"},
        {"service_account_id": "default"},
        {"vm_runtime": timedelta(hours=7)},
        {"vm_runtime": timedelta(seconds=29)},
        {"bundle_validity": timedelta(minutes=16)},
        {"object_ttl_days": 2},
    ],
)
def test_guardrail_config_rejects_scope_or_safety_relaxation(overrides) -> None:
    with pytest.raises(ValueError):
        GuardrailConfig(**overrides)


def test_bundle_renders_cloud_enforced_compute_and_storage_controls() -> None:
    bundle = build_guardrail_bundle(GuardrailConfig(), generated_at=NOW)

    assert bundle["scope"] == "gcp_guardrail_plan_only"
    assert bundle["resources_created"] is False
    assert bundle["valid_until"] == "2026-08-09T04:15:00Z"
    assert bundle["compute"]["termination_time"] == "2026-08-09T10:00:00Z"
    assert bundle["compute"]["termination_action"] == "DELETE"
    assert bundle["compute"]["restart_on_failure"] is False
    assert bundle["compute"]["boot_disk_auto_delete_required"] is True
    assert bundle["storage"]["soft_delete_duration"] == "0d"
    assert bundle["storage"]["object_lifecycle_age_days"] == 1
    assert bundle["storage"]["uniform_bucket_level_access"] is True
    assert bundle["storage"]["public_access_prevention"] is True
    assert bundle["network"]["external_ip"] is False
    assert bundle["network"]["private_google_access"] is True
    assert bundle["network"]["ssh_source_range"] == "35.235.240.0/20"
    assert bundle["identity"]["bucket_role"] == "roles/storage.objectViewer"

    vm_command = bundle["commands"]["create_vm"]
    assert "--project=ducklake-example-project" in vm_command
    assert "--termination-time=2026-08-09T10:00:00Z" in vm_command
    assert "--instance-termination-action=DELETE" in vm_command
    assert "--no-restart-on-failure" in vm_command
    assert "--no-deletion-protection" in vm_command
    assert "--no-address" in vm_command
    assert "--subnet=ducklake-p2-subnet" in vm_command
    assert "--tags=ducklake-p2-iap" in vm_command
    assert (
        "--service-account=ducklake-p2-reader@ducklake-example-project.iam.gserviceaccount.com"
        in vm_command
    )
    assert "--scopes=storage-ro" in vm_command
    assert not any(part.startswith("--max-run-duration") for part in vm_command)

    bucket_command = bundle["commands"]["create_bucket"]
    assert "--soft-delete-duration=0d" in bucket_command
    assert "--uniform-bucket-level-access" in bucket_command
    assert "--public-access-prevention" in bucket_command
    assert "--default-storage-class=STANDARD" in bucket_command
    assert bundle["lifecycle"] == {
        "rule": [{"action": {"type": "Delete"}, "condition": {"age": 1}}]
    }
    assert "create_service_account" in bundle["commands"]
    assert "create_network" in bundle["commands"]
    assert "create_subnet" in bundle["commands"]
    assert "create_firewall" in bundle["commands"]
    assert "grant_bucket_reader" in bundle["commands"]


def test_teardown_is_exactly_allowlisted_and_dry_run_by_default() -> None:
    bundle = build_guardrail_bundle(GuardrailConfig(), generated_at=NOW)

    teardown = bundle["teardown"]
    assert teardown["default_mode"] == "dry_run"
    assert teardown["execute_requires_project_confirmation"] == "ducklake-example-project"
    assert teardown["resource_allowlist"] == {
        "vm": "zones/asia-southeast1-b/instances/ducklake-p2-bench-01",
        "bucket": "gs://ducklake-example-project-data",
        "firewall": "global/firewalls/ducklake-p2-allow-iap-ssh",
        "subnet": "regions/asia-southeast1/subnetworks/ducklake-p2-subnet",
        "network": "global/networks/ducklake-p2-net",
        "service_account": (
            "ducklake-p2-reader@ducklake-example-project.iam.gserviceaccount.com"
        ),
    }
    assert all(
        "ducklake-example-project" in " ".join(command)
        or "gs://ducklake-example-project-data" in " ".join(command)
        for command in teardown["commands"]
    )


def test_write_and_verify_bundle_detects_expiry_and_file_changes(tmp_path) -> None:
    artifacts = write_guardrail_bundle(tmp_path / "guardrails", GuardrailConfig(), generated_at=NOW)

    verified = verify_guardrail_bundle(artifacts.manifest_path, now=NOW + timedelta(minutes=10))
    assert verified["valid"] is True
    assert verified["resources_created"] is False
    assert json.loads(artifacts.lifecycle_path.read_text())["rule"][0]["condition"]["age"] == 1

    with pytest.raises(ValueError, match="expired"):
        verify_guardrail_bundle(artifacts.manifest_path, now=NOW + timedelta(minutes=16))

    lifecycle = json.loads(artifacts.lifecycle_path.read_text())
    lifecycle["rule"][0]["condition"]["age"] = 30
    artifacts.lifecycle_path.write_text(json.dumps(lifecycle))
    with pytest.raises(ValueError, match="checksum"):
        verify_guardrail_bundle(artifacts.manifest_path, now=NOW + timedelta(minutes=10))

    with pytest.raises(FileExistsError):
        write_guardrail_bundle(tmp_path / "guardrails", GuardrailConfig(), generated_at=NOW)


def test_expired_bundle_can_still_be_integrity_checked_for_teardown(tmp_path) -> None:
    artifacts = write_guardrail_bundle(tmp_path / "guardrails", GuardrailConfig(), generated_at=NOW)

    verified = verify_guardrail_bundle(
        artifacts.manifest_path,
        now=NOW + timedelta(days=1),
        permit_expired=True,
    )

    assert verified["integrity_valid"] is True
    assert verified["valid"] is False
