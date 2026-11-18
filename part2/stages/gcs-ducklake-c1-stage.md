# DuckLake C1 — Controlled Catalogue-Over-Identical-Parquet Stage

**Status:** Complete — `eligible` causal direct-clone screen; see
[assessment report](../result/gcs-ducklake-c1-assessment-report.md)  
**Stage ID:** `P2-GCE-GCS-DUCKLAKE-C1-1GB-20260809-001`  
**Approved by:** the researcher, 9 August 2026  
**Purpose:** Measure the incremental read/query effect of DuckLake catalogue management over the
eligible remote one-file B1 baseline without changing the Parquet bytes.

## First-principles question

The eligible B1 result already isolates a one-file, sorted, Snappy Parquet layout on same-region
GCS. A DuckLake table normally writes its own files, which would change byte layout, file metadata
or both. That would confound a claimed DuckLake effect with a Parquet-layout effect.

C1 instead uses DuckLake's documented `ducklake_add_data_files` facility to register a fresh,
byte-identical **clone** of the B1 object. DuckLake documentation says that registration transfers
file ownership to DuckLake, so the original B1 object must never be registered or exposed to
DuckLake maintenance. [DuckLake adding files](https://ducklake.select/docs/stable/duckdb/metadata/adding_files)

The test asks a narrow, useful question: when the same logical table and equivalent object bytes are
read through a DuckLake catalogue instead of a direct `read_parquet` reference, is there a measurable
incremental gain, neutral effect or overhead for this query mix?

It does **not** test DuckLake's Parquet writer, compaction, snapshot maintenance, multi-client
catalogue choice, S3, or production data data.

## Frozen C1 contract

| Component | C1 rule |
|---|---|
| Physical origin | Eligible B1 object from `P2-GCE-GCS-B1-1GB-20260809-001`; never registered into DuckLake |
| Timed plain baseline | A direct `read_parquet` scan of C1's own generation-pinned cloned object — the exact object registered in `c1.main.b1` |
| Candidate object | Fresh object under `gcs-ducklake-c1-20260809/`, copied from B1 with a create-only precondition |
| Physical gate | Candidate SHA-256, byte count and CRC32C must exactly match B1; otherwise C1 is `unsupported`, not timed |
| Catalogue | One local DuckDB-backed DuckLake metadata file on the single GCE VM; its SHA-256 is recorded and rechecked before every screen |
| Required catalogue option | `data_inlining_row_limit = 0`, frozen before table creation because the fixed source schema legitimately contains `row_id`, a name DuckLake otherwise reserves for inlined rows |
| Managed relation | Frozen to `c1.main.b1`, attached `READ_ONLY` in every timing connection |
| Data access | Absolute, generation-pinned HTTPS object URL with the VM's ephemeral OAuth bearer setup; no HMAC key, persistent secret or remote catalogue |
| Extensions | DuckDB 1.5.5 matching `httpfs` and `ducklake` binaries loaded by explicit local path; hashes and resolved extension metadata recorded |
| Timed work | Only Q0–Q6 SQL execution. Token creation, extension loading, catalogue attach and temporary-view creation happen before warm-ups and are recorded separately |
| Cache condition | Same warm/default GCS condition as B1; no remote-cold claim |

DuckLake can work with any DuckDB filesystem backend and records absolute or data-path-relative file
locations. The absolute HTTPS arrangement is deliberate: the VM has short-lived OAuth bearer access
to Google Cloud Storage, whereas DuckDB's `gs://` secret path expects GCS HMAC credentials. It makes
the C1 result a **catalogue-over-managed-file** result, not a test of DuckLake writing directly to
GCS. [DuckLake storage](https://ducklake.select/docs/stable/duckdb/usage/choosing_storage),
[DuckLake paths](https://ducklake.select/docs/stable/duckdb/usage/paths),
[DuckDB GCS secrets](https://duckdb.org/docs/current/core_extensions/httpfs/s3api)

## Gates and evidence

1. Acquire the matching stable `ducklake` extension for DuckDB 1.5.5 and record source URL,
   compressed/uncompressed SHA-256, byte counts, `duckdb_extensions()` output and an offline explicit
   `LOAD` proof. Do not use `core_nightly` or automatic `INSTALL`.
2. Make the isolated C1 clone under the fresh prefix while the VM has temporary write access. Verify
   immutable generation, SHA-256, CRC32C and byte equality with B1; then restore the service account
   to bucket read-only before catalogue registration or timing.
3. Create a fresh local C1 metadata catalogue, register only the cloned object, then capture its
   checksum, `ducklake_settings`, catalogue option snapshot and DuckLake file-list metadata. Do not
   run compaction, expiry, cleanup, rewrites or any maintenance command.
4. Run untimed Q0–Q6 equivalence and an observed physical-order check between a direct read of the
   C1 clone and `c1.main.b1`. A mismatch makes C1 `invalid` and prevents timing.
5. Run a fresh 19-block direct-clone/direct-clone control with the same extensions and
   per-connection read-only catalogue attachment. The aggregate and Q3 precision requirements remain
   the existing 5% rule.
6. If stable, screen direct C1-clone `read_parquet` versus `c1.main.b1` using the fixed block-major
   schedule, aggregate lower-CI gate and Q3 lower-CI guard. Record `eligible`, `valid_not_eligible`,
   `unstable`, `invalid` or `unsupported` without treating any terminal state as a general DuckLake
   conclusion.
7. Analyse JSON evidence on the VM and transfer only its compact result, manifest, checksums and
   derived summary to the workspace. Do not copy Parquet or the metadata database to the Mac.

If C1 passes, a later, separately approved experiment may assess DuckLake's own writer and a
many-file sensitivity layout. That is a different causal question and must not be inferred from C1.

## Interpretation safeguard

An earlier A0-versus-C1 representation screen is retained as intermediate evidence only: it combines
the move from A0's physical representation to the cloned B1 layout with the catalogue path. It must
not be reported as an incremental DuckLake result or used to select a DuckLake configuration. Only
the direct-clone comparison above answers C1's management-layer question.

## Outcome — 9 August 2026

The direct-clone correctness gate, 19-block noise control and 19-block causal screen all completed.
The screen is `eligible`: DuckLake reduced aggregate elapsed time by 43.4% (95% CI 42.7%–44.1%)
and improved the Q3 guard by 18.3% (95% CI 17.1%–19.9%). See the
[assessment report](../result/gcs-ducklake-c1-assessment-report.md) for the evidence chain and interpretation
limits.
