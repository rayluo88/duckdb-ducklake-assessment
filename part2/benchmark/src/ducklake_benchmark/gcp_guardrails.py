import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path


APPROVED_PROJECT_ID = "ducklake-example-project"
APPROVED_PROJECT_NUMBER = "000000000000"
APPROVED_REGION = "asia-southeast1"
APPROVED_ZONE = "asia-southeast1-b"
APPROVED_VM_NAME = "ducklake-p2-bench-01"
APPROVED_BUCKET_NAME = "ducklake-example-project-data"
APPROVED_NETWORK_NAME = "ducklake-p2-net"
APPROVED_SUBNET_NAME = "ducklake-p2-subnet"
APPROVED_SUBNET_CIDR = "10.20.0.0/24"
APPROVED_FIREWALL_NAME = "ducklake-p2-allow-iap-ssh"
APPROVED_NETWORK_TAG = "ducklake-p2-iap"
APPROVED_IAP_SOURCE_RANGE = "35.235.240.0/20"
APPROVED_SERVICE_ACCOUNT_ID = "ducklake-p2-reader"


@dataclass(frozen=True)
class GuardrailConfig:
    project_id: str = APPROVED_PROJECT_ID
    project_number: str = APPROVED_PROJECT_NUMBER
    region: str = APPROVED_REGION
    zone: str = APPROVED_ZONE
    vm_name: str = APPROVED_VM_NAME
    bucket_name: str = APPROVED_BUCKET_NAME
    network_name: str = APPROVED_NETWORK_NAME
    subnet_name: str = APPROVED_SUBNET_NAME
    subnet_cidr: str = APPROVED_SUBNET_CIDR
    firewall_name: str = APPROVED_FIREWALL_NAME
    network_tag: str = APPROVED_NETWORK_TAG
    iap_source_range: str = APPROVED_IAP_SOURCE_RANGE
    service_account_id: str = APPROVED_SERVICE_ACCOUNT_ID
    vm_runtime: timedelta = timedelta(hours=6)
    bundle_validity: timedelta = timedelta(minutes=15)
    object_ttl_days: int = 1
    machine_type: str = "n2-standard-8"
    boot_disk_size_gb: int = 200

    def __post_init__(self) -> None:
        fixed_values = {
            "project_id": (self.project_id, APPROVED_PROJECT_ID),
            "project_number": (self.project_number, APPROVED_PROJECT_NUMBER),
            "region": (self.region, APPROVED_REGION),
            "zone": (self.zone, APPROVED_ZONE),
            "vm_name": (self.vm_name, APPROVED_VM_NAME),
            "bucket_name": (self.bucket_name, APPROVED_BUCKET_NAME),
            "network_name": (self.network_name, APPROVED_NETWORK_NAME),
            "subnet_name": (self.subnet_name, APPROVED_SUBNET_NAME),
            "subnet_cidr": (self.subnet_cidr, APPROVED_SUBNET_CIDR),
            "firewall_name": (self.firewall_name, APPROVED_FIREWALL_NAME),
            "network_tag": (self.network_tag, APPROVED_NETWORK_TAG),
            "iap_source_range": (self.iap_source_range, APPROVED_IAP_SOURCE_RANGE),
            "service_account_id": (
                self.service_account_id,
                APPROVED_SERVICE_ACCOUNT_ID,
            ),
        }
        for name, (actual, expected) in fixed_values.items():
            if actual != expected:
                raise ValueError(f"{name} must remain fixed at {expected}")
        if not timedelta(seconds=30) <= self.vm_runtime <= timedelta(hours=6):
            raise ValueError("vm_runtime must be between 30 seconds and 6 hours")
        if not timedelta(seconds=30) <= self.bundle_validity <= timedelta(minutes=15):
            raise ValueError("bundle_validity must be between 30 seconds and 15 minutes")
        if self.object_ttl_days != 1:
            raise ValueError("object_ttl_days must remain fixed at 1")
        if self.machine_type != "n2-standard-8":
            raise ValueError("machine_type must remain fixed at n2-standard-8")
        if self.boot_disk_size_gb != 200:
            raise ValueError("boot_disk_size_gb must remain fixed at 200")

    @property
    def service_account_email(self) -> str:
        return f"{self.service_account_id}@{self.project_id}.iam.gserviceaccount.com"


@dataclass(frozen=True)
class GuardrailArtifacts:
    manifest_path: Path
    lifecycle_path: Path


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("timestamps must include a timezone")
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _canonical_sha256(value: object) -> str:
    return sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _file_sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def build_guardrail_bundle(
    config: GuardrailConfig, *, generated_at: datetime
) -> dict[str, object]:
    generated_at_text = _utc_iso(generated_at)
    valid_until = _utc_iso(generated_at + config.bundle_validity)
    termination_time = _utc_iso(generated_at + config.vm_runtime)
    expiry_label = termination_time.lower().replace(":", "-")
    labels = f"assessment=ducklake-p2,managed-by=guardrail-bundle,expires={expiry_label}"
    lifecycle = {
        "rule": [{"action": {"type": "Delete"}, "condition": {"age": config.object_ttl_days}}]
    }
    create_vm = [
        "gcloud",
        "compute",
        "instances",
        "create",
        config.vm_name,
        f"--project={config.project_id}",
        f"--zone={config.zone}",
        f"--machine-type={config.machine_type}",
        "--image-family=debian-12",
        "--image-project=debian-cloud",
        f"--boot-disk-size={config.boot_disk_size_gb}GB",
        "--boot-disk-type=pd-balanced",
        f"--subnet={config.subnet_name}",
        "--no-address",
        f"--tags={config.network_tag}",
        f"--service-account={config.service_account_email}",
        "--scopes=storage-ro",
        f"--termination-time={termination_time}",
        "--instance-termination-action=DELETE",
        "--no-restart-on-failure",
        "--no-deletion-protection",
        f"--labels={labels}",
    ]
    create_bucket = [
        "gcloud",
        "storage",
        "buckets",
        "create",
        f"gs://{config.bucket_name}",
        f"--project={config.project_id}",
        f"--location={config.region}",
        "--default-storage-class=STANDARD",
        "--uniform-bucket-level-access",
        "--public-access-prevention",
        "--soft-delete-duration=0d",
        "--lifecycle-file=gcs-lifecycle.json",
    ]
    create_service_account = [
        "gcloud",
        "iam",
        "service-accounts",
        "create",
        config.service_account_id,
        f"--project={config.project_id}",
        "--display-name=DuckLake Part 2 read-only benchmark VM",
    ]
    create_network = [
        "gcloud",
        "compute",
        "networks",
        "create",
        config.network_name,
        f"--project={config.project_id}",
        "--subnet-mode=custom",
    ]
    create_subnet = [
        "gcloud",
        "compute",
        "networks",
        "subnets",
        "create",
        config.subnet_name,
        f"--project={config.project_id}",
        f"--region={config.region}",
        f"--network={config.network_name}",
        f"--range={config.subnet_cidr}",
        "--enable-private-ip-google-access",
    ]
    create_firewall = [
        "gcloud",
        "compute",
        "firewall-rules",
        "create",
        config.firewall_name,
        f"--project={config.project_id}",
        f"--network={config.network_name}",
        "--direction=INGRESS",
        "--action=ALLOW",
        "--rules=tcp:22",
        f"--source-ranges={config.iap_source_range}",
        f"--target-tags={config.network_tag}",
    ]
    grant_bucket_reader = [
        "gcloud",
        "storage",
        "buckets",
        "add-iam-policy-binding",
        f"gs://{config.bucket_name}",
        f"--project={config.project_id}",
        f"--member=serviceAccount:{config.service_account_email}",
        "--role=roles/storage.objectViewer",
    ]
    teardown_commands = [
        [
            "gcloud",
            "compute",
            "instances",
            "delete",
            config.vm_name,
            f"--project={config.project_id}",
            f"--zone={config.zone}",
            "--quiet",
        ],
        [
            "gcloud",
            "storage",
            "rm",
            "--recursive",
            f"gs://{config.bucket_name}/**",
            f"--project={config.project_id}",
        ],
        [
            "gcloud",
            "storage",
            "buckets",
            "delete",
            f"gs://{config.bucket_name}",
            f"--project={config.project_id}",
            "--quiet",
        ],
        [
            "gcloud",
            "compute",
            "firewall-rules",
            "delete",
            config.firewall_name,
            f"--project={config.project_id}",
            "--quiet",
        ],
        [
            "gcloud",
            "compute",
            "networks",
            "subnets",
            "delete",
            config.subnet_name,
            f"--project={config.project_id}",
            f"--region={config.region}",
            "--quiet",
        ],
        [
            "gcloud",
            "compute",
            "networks",
            "delete",
            config.network_name,
            f"--project={config.project_id}",
            "--quiet",
        ],
        [
            "gcloud",
            "iam",
            "service-accounts",
            "delete",
            config.service_account_email,
            f"--project={config.project_id}",
            "--quiet",
        ],
    ]
    return {
        "result_version": 1,
        "scope": "gcp_guardrail_plan_only",
        "resources_created": False,
        "generated_at": generated_at_text,
        "valid_until": valid_until,
        "project": {"id": config.project_id, "number": config.project_number},
        "compute": {
            "region": config.region,
            "zone": config.zone,
            "vm_name": config.vm_name,
            "machine_type": config.machine_type,
            "termination_time": termination_time,
            "termination_action": "DELETE",
            "restart_on_failure": False,
            "deletion_protection": False,
            "boot_disk_auto_delete_required": True,
            "post_create_verification_required": True,
        },
        "network": {
            "network_name": config.network_name,
            "subnet_name": config.subnet_name,
            "subnet_cidr": config.subnet_cidr,
            "external_ip": False,
            "private_google_access": True,
            "ssh_ingress": "tcp:22",
            "ssh_source_range": config.iap_source_range,
            "target_tag": config.network_tag,
            "post_create_verification_required": True,
        },
        "identity": {
            "service_account_id": config.service_account_id,
            "service_account_email": config.service_account_email,
            "vm_oauth_scopes": ["storage-ro"],
            "bucket_role": "roles/storage.objectViewer",
            "post_create_verification_required": True,
        },
        "storage": {
            "bucket_name": config.bucket_name,
            "location": config.region,
            "storage_class": "STANDARD",
            "soft_delete_duration": "0d",
            "object_lifecycle_age_days": config.object_ttl_days,
            "uniform_bucket_level_access": True,
            "public_access_prevention": True,
            "post_create_verification_required": True,
        },
        "lifecycle": lifecycle,
        "commands": {
            "create_service_account": create_service_account,
            "create_network": create_network,
            "create_subnet": create_subnet,
            "create_firewall": create_firewall,
            "create_bucket": create_bucket,
            "grant_bucket_reader": grant_bucket_reader,
            "create_vm": create_vm,
        },
        "teardown": {
            "default_mode": "dry_run",
            "execute_requires_project_confirmation": config.project_id,
            "resource_allowlist": {
                "vm": f"zones/{config.zone}/instances/{config.vm_name}",
                "bucket": f"gs://{config.bucket_name}",
                "firewall": f"global/firewalls/{config.firewall_name}",
                "subnet": f"regions/{config.region}/subnetworks/{config.subnet_name}",
                "network": f"global/networks/{config.network_name}",
                "service_account": config.service_account_email,
            },
            "commands": teardown_commands,
        },
        "claim_boundary": (
            "Safety plan only; no GCE VM, GCS bucket, benchmark, DuckDB, DuckLake, production data, "
            "S3, or performance result was created."
        ),
        "environment": {},
    }


def write_guardrail_bundle(
    output_dir: Path, config: GuardrailConfig, *, generated_at: datetime
) -> GuardrailArtifacts:
    if output_dir.exists():
        raise FileExistsError(f"guardrail output already exists: {output_dir}")
    output_dir.mkdir(parents=True)
    lifecycle_path = output_dir / "gcs-lifecycle.json"
    bundle = build_guardrail_bundle(config, generated_at=generated_at)
    lifecycle_path.write_text(json.dumps(bundle["lifecycle"], indent=2, sort_keys=True) + "\n")
    bundle["lifecycle_file"] = {
        "path": lifecycle_path.name,
        "sha256": _file_sha256(lifecycle_path),
    }
    bundle["bundle_sha256"] = _canonical_sha256(bundle)
    manifest_path = output_dir / "guardrail-manifest.json"
    manifest_path.write_text(json.dumps(bundle, indent=2, sort_keys=True) + "\n")
    return GuardrailArtifacts(manifest_path=manifest_path, lifecycle_path=lifecycle_path)


def verify_guardrail_bundle(
    manifest_path: Path, *, now: datetime, permit_expired: bool = False
) -> dict[str, object]:
    if now.tzinfo is None:
        raise ValueError("now must include a timezone")
    bundle = json.loads(manifest_path.read_text())
    recorded_bundle_hash = bundle.pop("bundle_sha256", None)
    if recorded_bundle_hash != _canonical_sha256(bundle):
        raise ValueError("guardrail bundle checksum mismatch")
    bundle["bundle_sha256"] = recorded_bundle_hash
    lifecycle_file = bundle["lifecycle_file"]
    lifecycle_path = manifest_path.parent / lifecycle_file["path"]
    if _file_sha256(lifecycle_path) != lifecycle_file["sha256"]:
        raise ValueError("lifecycle checksum mismatch")
    if json.loads(lifecycle_path.read_text()) != bundle["lifecycle"]:
        raise ValueError("lifecycle content mismatch")
    valid_until = datetime.fromisoformat(bundle["valid_until"].replace("Z", "+00:00"))
    is_current = now.astimezone(timezone.utc) <= valid_until
    if not is_current and not permit_expired:
        raise ValueError("guardrail bundle expired")
    if bundle["project"] != {"id": APPROVED_PROJECT_ID, "number": APPROVED_PROJECT_NUMBER}:
        raise ValueError("guardrail project scope mismatch")
    if bundle["resources_created"] is not False:
        raise ValueError("guardrail plan must not claim resources were created")
    return {**bundle, "integrity_valid": True, "valid": is_current}
