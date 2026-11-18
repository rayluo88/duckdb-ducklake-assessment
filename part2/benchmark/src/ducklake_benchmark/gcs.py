import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

import duckdb


@dataclass(frozen=True)
class GcsObject:
    object: str
    generation: str
    sha256: str
    physical_bytes: int
    crc32c: str | None = None


@dataclass(frozen=True)
class GcsCalibrationManifest:
    bucket: str
    bucket_region: str
    lane_1: GcsObject
    lane_2: GcsObject

    @property
    def physical_bytes_per_lane(self) -> int:
        return self.lane_1.physical_bytes


@dataclass(frozen=True)
class GcsScreenCandidate:
    layout_id: str
    objects: tuple[GcsObject, ...]


@dataclass(frozen=True)
class GcsScreenManifest:
    stage_id: str
    bucket: str
    bucket_region: str
    object_prefix: str
    a0_lane_1: GcsObject
    a0_lane_2: GcsObject
    candidate: GcsScreenCandidate


def gcs_media_url(bucket: str, object_name: str) -> str:
    return (
        f"https://storage.googleapis.com/storage/v1/b/{quote(bucket, safe='')}/o/"
        f"{quote(object_name, safe='')}?alt=media"
    )


def gcs_media_url_for_generation(bucket: str, object_name: str, generation: str) -> str:
    """Return a JSON-API media URL pinned to one immutable GCS object generation."""

    return f"{gcs_media_url(bucket, object_name)}&generation={quote(generation, safe='')}"


def render_gcs_read_parquet(bucket: str, objects: tuple[GcsObject, ...]) -> str:
    """Render an explicit, generation-pinned DuckDB Parquet file list."""

    if not objects:
        raise ValueError("GCS Parquet relation requires at least one object")
    urls = [gcs_media_url_for_generation(bucket, item.object, item.generation) for item in objects]
    quoted_urls = ", ".join("'" + url.replace("'", "''") + "'" for url in urls)
    return f"read_parquet([{quoted_urls}])"


def fetch_gce_access_token(*, opener=urlopen) -> str:
    request = Request(
        "http://metadata.google.internal/computeMetadata/v1/instance/"
        "service-accounts/default/token",
        headers={"Metadata-Flavor": "Google"},
    )
    with opener(request, timeout=5) as response:
        payload = json.loads(response.read())
    token = payload.get("access_token")
    if not isinstance(token, str) or not token:
        raise ValueError("GCE metadata response did not contain an access token")
    return token


def configure_duckdb_gcs_oauth(
    connection: duckdb.DuckDBPyConnection,
    *,
    extension_path: Path,
    bucket: str,
) -> None:
    escaped_extension = str(extension_path.resolve()).replace("'", "''")
    token = fetch_gce_access_token().replace("'", "''")
    escaped_scope = (
        "https://storage.googleapis.com/storage/v1/b/"
        f"{quote(bucket, safe='')}/o/"
    ).replace("'", "''")
    try:
        connection.execute("SET autoinstall_known_extensions = false")
        connection.execute(f"LOAD '{escaped_extension}'")
        connection.execute("SET autoload_known_extensions = false")
        connection.execute(
            "CREATE SECRET gcs_http_oauth (TYPE http, "
            f"BEARER_TOKEN '{token}', SCOPE '{escaped_scope}')"
        )
    except Exception:
        raise RuntimeError("DuckDB GCS OAuth setup failed; credential redacted") from None


def read_gcs_manifest(path: Path) -> GcsCalibrationManifest:
    raw = json.loads(path.read_text())
    lane_1 = GcsObject(**raw["lane_1"])
    lane_2 = GcsObject(**raw["lane_2"])
    if (
        lane_1.sha256 != lane_2.sha256
        or lane_1.physical_bytes != lane_2.physical_bytes
        or lane_1.physical_bytes <= 0
    ):
        raise ValueError("GCS calibration objects must be byte-identical")
    if lane_1.object == lane_2.object or lane_1.generation == lane_2.generation:
        raise ValueError("GCS calibration requires two distinct immutable objects")
    return GcsCalibrationManifest(
        bucket=raw["bucket"],
        bucket_region=raw["bucket_region"],
        lane_1=lane_1,
        lane_2=lane_2,
    )


def _validate_screen_object(value: object, *, label: str, prefix: str) -> GcsObject:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    try:
        item = GcsObject(**value)
    except TypeError as error:
        raise ValueError(f"{label} is missing an object identity field") from error
    if (
        not item.object.startswith(prefix)
        or not item.generation
        or len(item.sha256) != 64
        or item.physical_bytes <= 0
        or not item.crc32c
    ):
        raise ValueError(f"{label} has an invalid GCS object identity")
    return item


def read_gcs_screen_manifest(path: Path) -> GcsScreenManifest:
    """Read an approved immutable A0-versus-layout GCS stage manifest."""

    raw = json.loads(path.read_text())
    if not isinstance(raw, dict):
        raise ValueError("GCS screen manifest must be an object")
    stage_id = raw.get("stage_id")
    bucket = raw.get("bucket")
    bucket_region = raw.get("bucket_region")
    prefix = raw.get("object_prefix")
    if (
        not isinstance(stage_id, str)
        or not stage_id
        or not isinstance(bucket, str)
        or not bucket
        or not isinstance(bucket_region, str)
        or not bucket_region
        or not isinstance(prefix, str)
        or not prefix.endswith("/")
    ):
        raise ValueError("GCS screen manifest has an invalid stage location")
    lane_1 = _validate_screen_object(raw.get("a0_lane_1"), label="a0_lane_1", prefix=prefix)
    lane_2 = _validate_screen_object(raw.get("a0_lane_2"), label="a0_lane_2", prefix=prefix)
    if (
        lane_1.object == lane_2.object
        or lane_1.generation == lane_2.generation
        or lane_1.sha256 != lane_2.sha256
        or lane_1.physical_bytes != lane_2.physical_bytes
    ):
        raise ValueError("GCS screen A0 lanes must be distinct byte-identical objects")
    candidate_raw = raw.get("candidate")
    if not isinstance(candidate_raw, dict):
        raise ValueError("GCS screen manifest candidate must be an object")
    layout_id = candidate_raw.get("layout_id")
    if layout_id not in {"B1", "Bstar"}:
        raise ValueError("GCS screen manifest has an unapproved candidate layout")
    raw_objects = candidate_raw.get("objects")
    if not isinstance(raw_objects, list) or not raw_objects:
        raise ValueError("GCS screen candidate must contain a non-empty object list")
    candidate_objects = tuple(
        _validate_screen_object(item, label=f"candidate.objects[{index}]", prefix=prefix)
        for index, item in enumerate(raw_objects)
    )
    identity_pairs = {(item.object, item.generation) for item in candidate_objects}
    if len(identity_pairs) != len(candidate_objects):
        raise ValueError("GCS screen candidate objects must have distinct object generations")
    if any(item.object in {lane_1.object, lane_2.object} for item in candidate_objects):
        raise ValueError("GCS screen candidate cannot reuse an A0 object name")
    if layout_id == "B1" and len(candidate_objects) != 1:
        raise ValueError("GCS B1 isolation candidate must contain exactly one object")
    return GcsScreenManifest(
        stage_id=stage_id,
        bucket=bucket,
        bucket_region=bucket_region,
        object_prefix=prefix,
        a0_lane_1=lane_1,
        a0_lane_2=lane_2,
        candidate=GcsScreenCandidate(layout_id=layout_id, objects=candidate_objects),
    )
