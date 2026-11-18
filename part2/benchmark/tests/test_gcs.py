import json

import pytest

from ducklake_benchmark.gcs import (
    GcsCalibrationManifest,
    configure_duckdb_gcs_oauth,
    fetch_gce_access_token,
    gcs_media_url,
    gcs_media_url_for_generation,
    read_gcs_manifest,
    read_gcs_screen_manifest,
)


def test_gcs_media_url_percent_encodes_object_name() -> None:
    assert gcs_media_url("example-bucket", "calibration/lane 1/data.parquet") == (
        "https://storage.googleapis.com/storage/v1/b/example-bucket/o/"
        "calibration%2Flane%201%2Fdata.parquet?alt=media"
    )


def test_generation_pinned_media_url_selects_the_recorded_object_revision() -> None:
    assert gcs_media_url_for_generation(
        "example-bucket", "stage/a0 lane/data.parquet", "102"
    ) == (
        "https://storage.googleapis.com/storage/v1/b/example-bucket/o/"
        "stage%2Fa0%20lane%2Fdata.parquet?alt=media&generation=102"
    )


def test_gcs_manifest_requires_two_matching_immutable_objects(tmp_path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps(
            {
                "bucket": "ducklake-example-project-data",
                "bucket_region": "asia-southeast1",
                "lane_1": {
                    "object": "calibration/a0_lane_1/data.parquet",
                    "generation": "101",
                    "sha256": "a" * 64,
                    "physical_bytes": 986_193_351,
                },
                "lane_2": {
                    "object": "calibration/a0_lane_2/data.parquet",
                    "generation": "102",
                    "sha256": "a" * 64,
                    "physical_bytes": 986_193_351,
                },
            }
        )
    )

    manifest = read_gcs_manifest(path)

    assert isinstance(manifest, GcsCalibrationManifest)
    assert manifest.lane_1.generation == "101"
    assert manifest.physical_bytes_per_lane == 986_193_351


def test_gcs_manifest_rejects_different_hashes(tmp_path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps(
            {
                "bucket": "ducklake-example-project-data",
                "bucket_region": "asia-southeast1",
                "lane_1": {"object": "one", "generation": "1", "sha256": "a" * 64, "physical_bytes": 10},
                "lane_2": {"object": "two", "generation": "2", "sha256": "b" * 64, "physical_bytes": 10},
            }
        )
    )

    with pytest.raises(ValueError, match="byte-identical"):
        read_gcs_manifest(path)


def test_gcs_screen_manifest_requires_distinct_a0_lanes_and_generation_pinned_bstar_files(tmp_path) -> None:
    path = tmp_path / "stage.json"
    path.write_text(
        json.dumps(
            {
                "stage_id": "P2-GCE-GCS-BSTAR-1GB-20260809-001",
                "bucket": "ducklake-example-project-data",
                "bucket_region": "asia-southeast1",
                "object_prefix": "gcs-bstar-20260809/",
                "a0_lane_1": {
                    "object": "gcs-bstar-20260809/a0-lane-1/data.parquet",
                    "generation": "101",
                    "sha256": "a" * 64,
                    "physical_bytes": 100,
                    "crc32c": "lane1",
                },
                "a0_lane_2": {
                    "object": "gcs-bstar-20260809/a0-lane-2/data.parquet",
                    "generation": "102",
                    "sha256": "a" * 64,
                    "physical_bytes": 100,
                    "crc32c": "lane2",
                },
                "candidate": {
                    "layout_id": "Bstar",
                    "objects": [
                        {
                            "object": "gcs-bstar-20260809/bstar/part_0.parquet",
                            "generation": "103",
                            "sha256": "b" * 64,
                            "physical_bytes": 60,
                            "crc32c": "candidate0",
                        },
                        {
                            "object": "gcs-bstar-20260809/bstar/part_1.parquet",
                            "generation": "104",
                            "sha256": "c" * 64,
                            "physical_bytes": 40,
                            "crc32c": "candidate1",
                        },
                    ],
                },
            }
        )
    )

    manifest = read_gcs_screen_manifest(path)

    assert manifest.candidate.layout_id == "Bstar"
    assert [item.generation for item in manifest.candidate.objects] == ["103", "104"]


def test_gcs_screen_manifest_accepts_the_approved_one_file_b1_isolation_candidate(tmp_path) -> None:
    path = tmp_path / "stage.json"
    path.write_text(
        json.dumps(
            {
                "stage_id": "P2-GCE-GCS-B1-1GB-20260809-001",
                "bucket": "ducklake-example-project-data",
                "bucket_region": "asia-southeast1",
                "object_prefix": "gcs-b1-20260809/",
                "a0_lane_1": {
                    "object": "gcs-b1-20260809/a0-lane-1/data.parquet",
                    "generation": "101",
                    "sha256": "a" * 64,
                    "physical_bytes": 100,
                    "crc32c": "lane1",
                },
                "a0_lane_2": {
                    "object": "gcs-b1-20260809/a0-lane-2/data.parquet",
                    "generation": "102",
                    "sha256": "a" * 64,
                    "physical_bytes": 100,
                    "crc32c": "lane2",
                },
                "candidate": {
                    "layout_id": "B1",
                    "objects": [
                        {
                            "object": "gcs-b1-20260809/b1/data.parquet",
                            "generation": "103",
                            "sha256": "b" * 64,
                            "physical_bytes": 131,
                            "crc32c": "candidate",
                        }
                    ],
                },
            }
        )
    )

    manifest = read_gcs_screen_manifest(path)

    assert manifest.candidate.layout_id == "B1"
    assert len(manifest.candidate.objects) == 1


def test_gcs_screen_manifest_rejects_a_multi_file_b1_candidate(tmp_path) -> None:
    path = tmp_path / "stage.json"
    path.write_text(
        json.dumps(
            {
                "stage_id": "P2-GCE-GCS-B1-1GB-20260809-001",
                "bucket": "ducklake-example-project-data",
                "bucket_region": "asia-southeast1",
                "object_prefix": "gcs-b1-20260809/",
                "a0_lane_1": {"object": "gcs-b1-20260809/a0-lane-1/data.parquet", "generation": "101", "sha256": "a" * 64, "physical_bytes": 100, "crc32c": "lane1"},
                "a0_lane_2": {"object": "gcs-b1-20260809/a0-lane-2/data.parquet", "generation": "102", "sha256": "a" * 64, "physical_bytes": 100, "crc32c": "lane2"},
                "candidate": {"layout_id": "B1", "objects": [
                    {"object": "gcs-b1-20260809/b1/part_0.parquet", "generation": "103", "sha256": "b" * 64, "physical_bytes": 70, "crc32c": "candidate0"},
                    {"object": "gcs-b1-20260809/b1/part_1.parquet", "generation": "104", "sha256": "c" * 64, "physical_bytes": 61, "crc32c": "candidate1"},
                ]},
            }
        )
    )

    with pytest.raises(ValueError, match="exactly one object"):
        read_gcs_screen_manifest(path)


def test_metadata_token_is_read_from_gce_identity_endpoint() -> None:
    requests = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return b'{"access_token":"short-lived-token","expires_in":3599,"token_type":"Bearer"}'

    def opener(request, timeout):
        requests.append((request, timeout))
        return Response()

    assert fetch_gce_access_token(opener=opener) == "short-lived-token"
    request, timeout = requests[0]
    assert request.get_header("Metadata-flavor") == "Google"
    assert "instance/service-accounts/default/token" in request.full_url
    assert timeout == 5


def test_duckdb_oauth_setup_uses_default_temporary_secret_and_redacts_errors(
    tmp_path, monkeypatch
) -> None:
    statements = []

    class Connection:
        def execute(self, sql):
            statements.append(sql)
            if sql.startswith("CREATE SECRET"):
                raise ValueError(sql)

    monkeypatch.setattr("ducklake_benchmark.gcs.fetch_gce_access_token", lambda: "secret-token")
    extension = tmp_path / "httpfs.duckdb_extension"
    extension.write_bytes(b"fixture")

    with pytest.raises(RuntimeError, match="credential redacted") as error:
        configure_duckdb_gcs_oauth(
            Connection(), extension_path=extension, bucket="assessment-bucket"
        )

    assert statements[-1].startswith("CREATE SECRET gcs_http_oauth")
    assert "CREATE TEMP SECRET" not in statements[-1]
    assert "secret-token" not in str(error.value)
