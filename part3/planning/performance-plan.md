# DuckLake Assessment Part 3 — S3 Performance Plan

**Owner:** the researcher  
**Plan date:** 24 August 2026  
**Status:** Approved design; Stage 0 passed, Stage 1 remote preflight pending  
**Decision:** Determine whether DuckLake improves DuckDB query-in-place performance on the target environment
staging S3, then measure bounded concurrent-read scaling at 1, 2 and 4 clients.

## Outcome and evidence boundary

Part 3 will compare a direct `read_parquet(...)` relation with a DuckLake relation that registers
the exact same S3 Parquet objects. It will test approximately 1 GB and 10 GB synthetic datasets in
one-file and eight-file layouts. The 1 GB result is a directional comparison with Part 2; the
10 GB result controls the Part 3 S3 conclusion.

Part 3 does not assess DuckLake writing, compaction, concurrent writes, a shared PostgreSQL
catalogue, production data, notebook rendering or a production migration. A valid null or slower
result satisfies the method; DuckLake is not required to win.

## Runtime policy

As verified on 24 August 2026, DuckDB 1.5.5 is the latest stable DuckDB release and DuckLake 1.0 is
the latest stable DuckLake specification/extension line. DuckDB 1.5.6 and 2.0 remain scheduled,
not released.

Immediately before building the remote transfer bundle:

1. Resolve the latest stable DuckDB release from the official release calendar.
2. Resolve the matching latest stable DuckLake extension/specification from the official
   compatibility matrix.
3. Pin the exact compatible versions in `pyproject.toml` and `uv.lock`.
4. Run the complete unit and local smoke suite.
5. Stop if signed compatible stable extensions are unavailable. Do not fall back silently and do
   not use `core_nightly`, preview or release-candidate builds.

Measured runs install signed stable `aws`, `httpfs` and `ducklake` extensions once into a
task-specific extension directory. Their versions, source repository and SHA-256 hashes are
frozen before timing; auto-install and updates are disabled during the campaign.

## Part 2 reuse

Preserve Part 2 unchanged. The Part 3 harness may snapshot these generic behaviours and their
tests, recording original paths and SHA-256 hashes:

- deterministic synthetic fixture and layout generation;
- Q0–Q6 definitions and equal primary-query weights;
- exact and fingerprint-based correctness gates;
- paired block ordering, bootstrap intervals and A/A noise rules;
- compact evidence records and hash-chain validation; and
- runtime/provenance identity helpers that are not GCP-specific.

Do not carry forward GCS OAuth, GCP guardrails, GCE/GCS identities, GCS stage identifiers, hardcoded
Part 2 runtime attestations, cloud archives, historical results or Part 2 claim text.

## Local and remote structure

The local source-of-truth is `workstreams/ducklake/part3/`. The SageMaker working root is supplied
at runtime and must contain only the uploaded harness, local catalogues, temporary fixtures, logs
and status files. It must not use an unresolved home-directory target for cleanup.

The approved S3 prefix uses this logical shape:

```text
s3://<approved-bucket>/<approved-prefix>/ducklake-p3/<campaign-id>/
├── scale-1g/
│   ├── one-file/lane-a/
│   ├── one-file/lane-b/
│   ├── eight-file/lane-a/
│   └── eight-file/lane-b/
└── scale-10g/
    ├── one-file/lane-a/
    ├── one-file/lane-b/
    ├── eight-file/lane-a/
    └── eight-file/lane-b/
```

Every object is create-only, checksummed and immutable for the campaign. Direct and DuckLake
candidate relations both use lane A. Lane B exists only for byte-identical A/A noise controls.

## Stage 0 — local preparation

- Create the Part 3 directory structure and governing records.
- Implement a fail-closed preflight config and a redacted environment report.
- Test allowlisted S3 URI validation, version compatibility, redaction and create-only output.
- Package the code-only harness with a provenance manifest and SHA-256 checksums.
- Do not include Parquet data, credentials, internal identifiers or Part 2 generated evidence.

## Stage 1 — SageMaker/S3 preflight

Run before generating a performance fixture:

1. Record the SageMaker runtime, instance/host resources, Python, `uv`, DuckDB and extension state.
2. Confirm the compute Region equals the S3 bucket Region; abort on cross-Region placement.
3. Create a temporary, prefix-scoped DuckDB S3 secret using `PROVIDER credential_chain` and the
   execution role. No static key is accepted by the harness.
4. Prove list, read, create-only write and cleanup on one tiny disposable object within the exact
   approved prefix. Reject access outside that prefix.
5. Detect bucket versioning. Enable `enable_http_metadata_cache` and `s3_version_id_pinning`; record
   `VersionId` where available. Otherwise enforce unique create-only keys plus before/after `HEAD`
   identity checks.
6. Record whether prefix-scoped CloudWatch request metrics already exist and are readable. Do not
   alter bucket monitoring configuration.

Any permission, version, Region, credential-persistence or unexpected-write failure stops the
campaign.

## Stage 2 — deterministic fixtures and correctness

- Reuse the Part 2 logical schema, generator seed, scenarios and query suite.
- Generate and upload one layout at a time to control local disk use.
- Target approximately 1 GB and 10 GB physical Parquet, recording exact rows and bytes rather than
  treating the labels as exact sizes.
- Produce one-file and eight-file layouts, then upload each byte sequence to lane A and lane B.
- Record row count, schema, nulls, compression, row groups, file sizes, SHA-256, S3 checksum, ETag,
  VersionId when present, and provenance.
- Register lane A's existing objects in a local DuckLake catalogue. Do not use the DuckLake writer,
  rewrite files or compact them.

Before timing each relation, validate schema, row count, null counts, exact integer/decimal
aggregates, probabilistic fingerprints, row multiset and all Q0–Q6 results. A mismatch invalidates
that condition and stops its timed stage.

## Stage 3 — single-client timing

Q0 is a separate metadata diagnostic. The eight equally weighted primary analytical cells are Q1,
Q2a, Q2b, Q2c, Q3, Q4, Q5 and Q6. Q3 remains the non-sort/non-partition safeguard.

For every scale/layout condition:

1. Run two untimed warmups.
2. Run lane-A-versus-lane-B direct reads beginning with seven paired blocks.
3. Add four blocks when the bootstrap confidence half-width exceeds `log(1.05)`, capped at 19.
4. Freeze the paired count and define aggregate and Q3 noise bounds as the 95th percentile of
   absolute A/A paired log gains.
5. Run direct lane A versus DuckLake-over-lane-A with the same paired count and seeded order.

Define gain as `log(direct_time / ducklake_time)`. A DuckLake speed claim requires the aggregate
bootstrap 95% lower bound to exceed the aggregate noise bound. The candidate also requires the Q3
bootstrap 95% lower bound to be at least negative Q3 noise. Report raw observations, medians, IQRs,
confidence intervals and noise bounds even when the result is not eligible.

Run five fresh-process observations per condition for startup, extension load, credential setup,
catalogue attach and first-query latency. Keep them separate from warm/default-cache query time.

## Stage 4 — mechanism diagnostics

Run diagnostics only after primary timing so instrumentation cannot change the decision evidence.
Use one matched fresh and one matched warm pass with DuckDB HTTP, FileSystem and detailed profiling.

Aggregate before persistence, then discard raw HTTP headers and URLs. Retained metrics include:

| Group | Metrics |
|---|---|
| Primary | paired latency, median, IQR, bootstrap interval, noise bound |
| DuckDB | CPU time, operator time, rows scanned, result size, total bytes read, peak memory, spill |
| S3 | `HEAD`/`GET`/`LIST`, Range requests, response bytes, duration, status and retry/error indicators |
| Host | process CPU/RSS, host CPU/memory, disk and network bytes/rate |
| Integrity | runtime and extension hashes, object identity, layout, seed, SQL hash and run order |

Existing prefix-scoped CloudWatch request metrics may be captured as a best-effort cross-check. Do
not create a metrics configuration or treat CloudWatch as complete request accounting.

## Stage 5 — adaptive concurrent reads

Use the valid 10 GB eight-file condition. Do not silently substitute another condition if it is
invalid or unstable.

- Give every process an identical read-only local catalogue copy.
- Test both direct and DuckLake representations at 1, 2 and 4 independent clients.
- For each cell, run three trials with a 60-second warmup and five-minute measured interval.
- Synchronise starts and use seeded query order.
- Report per-client p50/p95 latency, aggregate queries per second, scaling efficiency, fairness,
  errors/retries, CPU, memory, spill and network saturation.

The default campaign stops at four clients. An eight-client stage requires a separate confirmation
that eight simultaneous users are realistic, a positive lower 95% bound for four-versus-two-client
throughput, no errors or spill, and no sustained CPU, memory or network use above 90%.

## Computer Use handoff

the researcher starts Amazon WorkSpaces and waits for the SageMaker app to show `Running`. Computer Use
then navigates to JupyterLab, attempts the normal file upload, opens a terminal and runs checksum
verification plus preflight.

If WorkSpaces does not expose an approved local-file transfer path, stop for one manual harness
upload. Do not recreate the archive through clipboard/base64 text.

After preflight passes, launch the gated campaign as a detached command. The harness writes terse
`status.json`, `heartbeat.json` and `summary.json` files and detailed logs to disk. Inspect status at
stage boundaries rather than repeatedly reading screenshots or notebook output.

## Completion and cleanup

Part 3 succeeds when its valid comparisons pass correctness, every performance claim passes the
frozen noise rule, evidence is checksum-linked and redacted, and the report keeps staging results
separate from production conclusions.

At the end, package only compact evidence and verify its redaction manifest before download.
Immediately ask the researcher whether to retain or delete the synthetic S3 prefix and whether to stop the
SageMaker app and any other active billable resources. Do not delete or terminate them silently.

## Fixed assumptions

- The S3 scope is a dedicated read/write/delete test prefix containing synthetic data only.
- Query-in-place elapsed time is primary; notebook rendering is out of scope.
- Query weights are equal and explicitly assumed until an authoritative workload mix exists.
- Concurrent reads are in scope; concurrent writes and PostgreSQL/RDS provisioning are not.
- The default maximum concurrency is four clients.
- No S3 bucket policy, versioning, encryption, lifecycle, monitoring or access configuration is
  changed without separate approval.
