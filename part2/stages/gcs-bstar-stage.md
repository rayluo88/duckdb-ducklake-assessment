# DuckLake Part 2 — Same-Region GCS B* Confirmation

**Date:** 9 August 2026  
**Status:** Completed — B* is valid but not remotely eligible  
**Stage ID:** `P2-GCE-GCS-BSTAR-1GB-20260809-001`

## Decision and scope

This stage asks one narrow question: on the existing `n2-standard-8` VM and same-region GCS,
does the already-approved B* Parquet representation outperform A0 for the locked synthetic
Q1–Q6 mix, while retaining the Q3 non-aligned-filter guard?

It tests plain Parquet through DuckDB 1.5.5's pre-staged, hashed `httpfs` extension. It is not a
DuckLake test and does not make a production data, S3, production-layout, concurrency or remote-cold
claim. The relevant local result is [the first layout assessment](../result/first-assessment-report.md).

## Progress and abnormality record

- [x] **Inspect the earlier GCS A0/A0 evidence.** Its two distinct A0 objects were each
  986,193,351 bytes with the correct SHA-256, and Q1–Q6 reached the predeclared precision target.
  Q0 was correctly retained as unstable at 19 pairs.
- [x] **Inspect the live bucket.** At 2026-08-09T10:17Z, the bucket contained no live Parquet
  objects. It has a one-day delete lifecycle and a zero-day soft-delete retention period.
- [x] **Investigate rather than infer a cause.** No matching Cloud Audit Log object
  create/delete events are retained, and the VM command history contains no matching storage
  command. A one-day `age` lifecycle alone should not delete an object written only hours before;
  the cause is therefore unknown rather than attributed to lifecycle cleanup. The absence of soft
  delete means the old objects cannot be recovered.
- [x] **Decision.** Preserve the old A0/A0 JSON as historical method/noise evidence only. Do not
  reuse it as the noise reference for this comparison. Use fresh, immutable GCS objects and run a
  fresh A0/A0 shared-block control before B* timing.
- [x] **Implement generation-bound remote screening.** Add a small, separately scoped GCS runner
  with test-first coverage; do not widen the completed local-layout runner's claim boundary.
- [ ] **Stage and validate fresh objects directly cloud-to-cloud.** Grant the VM service account
  temporary bucket-level object-creation permission, copy only synthetic A0/B* objects from the
  VM to a fresh prefix, record object generations and hashes, then remove that writer grant.
- [x] **Stage and validate fresh objects directly cloud-to-cloud.** The new prefix contains exactly
  two A0 objects and eleven B* objects (3,279,526,612 bytes total). Their GCS names, generations,
  byte counts and CRC32C values were checked before timing. The VM's temporary `objectCreator` and
  bucket-metadata-reader roles were removed; its OAuth scope was restored to `storage-ro`.
- [x] **Run fresh GCS A0/A0 noise and A0/B* confirmation.** The fresh A0/A0 control and A0/B*
  screen each completed 19 blocks. Retain raw JSON on the VM and transfer
  only checksummed result, manifest and summary files to this workspace.
- [x] **Analyse the result.** B* passed its aggregate remote effect gate but failed the stable Q3
  guard, so it is `valid_not_eligible`. See [gcs-bstar-assessment-report.md](../result/gcs-bstar-assessment-report.md).
- [x] **Decide the next stage.** the researcher approved the separately frozen
  [B1-only remote isolation test](../result/gcs-b1-assessment-report.md) on 9 August 2026. It completed as
  eligible, so a separately approved DuckLake increment design is now permitted.

The lifecycle remains in place because the data is synthetic and disposable. Changing it cannot
recover the missing objects, and lifecycle-configuration changes can take up to 24 hours to take
effect. Every new object is therefore checked immediately before timing; a missing generation
causes an aborted-environment result rather than a partial rerun. See Google's documentation on
[object lifecycle management](https://cloud.google.com/storage/docs/lifecycle).

### Staging anomaly and recovery

The original read-only VM scope correctly prevented uploads even after a temporary IAM
`objectCreator` grant. The legacy `storage-rw` scope also failed for the `gcloud` upload API, and
the client initially attempted unsupported parallel-composite uploads. No objects were left behind.
The controlled recovery was to disable composite upload, use a fresh short-lived metadata token
for the staging client, and temporarily use `storage-full` while the service account itself remained
limited to this one bucket. Direct uploads then succeeded. The VM was stopped, returned to
`storage-ro`, restarted, and both temporary IAM roles were removed before the remote preflight and
any timed command. This is staging-process evidence, not performance evidence.

## Frozen inputs and object contract

The local source files stay on the VM. No Parquet payload is copied to the researcher's Mac.

| Representation | VM source | Required remote representation |
|---|---|---|
| A0 lane 1 | corrected A0 Parquet | one immutable object in the fresh stage prefix |
| A0 lane 2 | the same corrected A0 Parquet | a second object with a distinct name and generation |
| B* | approved sorted `(filter_key, row_id)` + approximately-128 MiB Snappy layout | the explicit, numeric writer-order list of all eleven objects |

Every remote object manifest records its logical role, object name, GCS generation, byte count,
CRC32C and SHA-256. The A0 lanes must be byte-identical, but their object names and generations
must differ. B* must match the existing approved B* layout manifest's per-file SHA-256 and total
bytes. Copy requests use fresh destination names and a no-overwrite generation precondition.

DuckDB reads generation-pinned JSON-API media URLs, never a mutable bare object name. The Cloud
Storage API's `generation` parameter selects an exact object revision; see
[Objects: get](https://cloud.google.com/storage/docs/json_api/v1/objects/get).

## Method

1. Re-attest the VM runtime and pinned `httpfs` extension before the command runs. No extension
   installation, update or credential file is permitted.
2. Build the fresh object manifest from Cloud Storage metadata and the existing local source and
   B* manifests. Reject unexpected hashes, bytes, generation reuse, a missing object, a non-GCS
   location or any object outside the dedicated prefix.
3. Run a fixed 19-block A0-lane-1 versus A0-lane-2 control. It uses the same block-major query
   schedule, two warm-ups per representation/query, 2,000 whole-block bootstrap resamples and
   Q0 diagnostic policy as the completed local assessment. Its aggregate and Q3 noise bounds are
   frozen before inspecting B* timing.
4. Run a fixed fresh 19-block A0-lane-1 versus B* screen. Query order and A0/B* order are seeded;
   all eight primary query pairs complete within each block before the Q0 diagnostic pair.
5. Declare the GCS B* result `eligible` only when all source/remote object checks and result
   equality checks pass, aggregate and Q3 precision are stable, aggregate lower 95% CI exceeds the
   new remote aggregate-noise bound, and Q3 lower 95% CI is at least negative remote-Q3 noise.
   Q0 is retained but never selects the candidate.

This measures warm/default client-cache behaviour. It deliberately does not label either run
"GCS cold". Each remote-screen connection acquires a short-lived VM-metadata OAuth token during
setup; that setup is outside the timed query pair and token values are neither persisted nor
recorded.

## Implementation contract

The new `gcs-screen` command is allowed to import `gcs.py`; the completed local layout commands
remain prohibited from importing it. It accepts one validated remote-stage manifest, one hashed
`httpfs` binary and, for the B* run, the freshly completed remote-noise result. It writes:

- `gcs-stage-manifest.json` — input links, source/object identities, runtime and extension hashes;
- `gcs-noise-result.json` or `gcs-screen-result.json` — raw paired observations, statistics and
  claim boundary; and
- `command.json` — the complete non-secret invocation.

Output directories are fresh-only. Expected `unsupported`, `invalid_correctness`, `unstable` and
`valid_not_eligible` outcomes are complete, zero-exit evidence; source, runtime, object or
environment drift produces a complete `aborted_environment` record and no candidate timing.

Only `gcs-stage-manifest.json`, timing JSON, command records, a concise decision summary and
SHA-256 checksums are copied locally. Raw Parquet and extension binaries remain in the disposable
cloud environment.

## Claim boundary

An eligible result would show a B* effect for this one synthetic data shape, query mix, VM,
DuckDB/httpfs build, same-region GCS bucket and warm/default-cache condition. It cannot establish
an S3 bandwidth limit, a production data result, a multi-reader result or an incremental DuckLake benefit.
