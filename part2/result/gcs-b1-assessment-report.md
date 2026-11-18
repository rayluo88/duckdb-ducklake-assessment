# DuckLake Part 2 — Same-Region GCS B1 Assessment

**Completed:** 9 August 2026  
**Workload:** assumed synthetic 65,000,000-row shuffled dataset; GCE `n2-standard-8`; DuckDB 1.5.5; same-region Standard GCS; warm/default cache  
**Scope:** plain Parquet only. DuckLake, S3, production data, concurrency and the project lead's local DuckDB remain outside this result.

## Decision

**B1 is the balanced remote plain-Parquet candidate for this synthetic workload.** It is eligible: the aggregate result is clearly faster and the non-aligned Q3 safety guard is stable and passes.

| Measure | Result |
|---|---:|
| Aggregate log gain, 95% CI | 0.6687 (0.6561, 0.6886) |
| Aggregate elapsed-time effect | 48.8% lower (48.1%–49.8%) |
| Q3 log gain, 95% CI | 0.0088 (-0.0144, 0.0143) |
| Q3 elapsed-time effect | 0.9% lower (about 1.4% higher to 1.4% lower) |
| Aggregate effect gate | pass |
| Q3 guard | pass |
| Final status | `eligible` |

## What B1 isolates

B1 is one 1,307,149,012-byte Snappy Parquet object sorted by `(filter_key, row_id)`. It retains the same sort mechanism and virtually the same compressed size as the 11-object B* representation, but removes B*'s object-count/file-boundary condition. Two fresh, byte-identical 986,193,351-byte A0 objects provided a new A0/A0 control for this stage.

The control's aggregate noise was 0.02079 log units and Q3 noise was 0.08422. It completed 19 shared blocks with all 152 primary result comparisons matching. Its B1 screen also completed 19 blocks with stable aggregate and Q3 precision.

The selective, sort-aligned queries improved markedly: median paired elapsed time was 67.8%–78.6% lower for Q2a–Q2c and Q4. Q1 and Q3 were effectively unchanged; Q5 and Q6 were only about 1%–2% higher. This is the balanced pattern that B* did not achieve.

## Interpretation

Compared with B*, B1 changes the remote result materially: B*'s aggregate gain was 14.3% but Q3 was 40.8% higher, whereas B1's aggregate gain is 48.8% and Q3 is indistinguishable from A0 within the predeclared bound. This is **consistent with** B*'s 11-object layout creating an adverse remote effect for scans that cannot benefit from its sort order. It does not prove file count caused the difference: no DuckDB profile, object-request trace or remote-byte trace was collected in these narrow screens.

B1 now supplies the required balanced plain-Parquet baseline for a later DuckLake increment test. That later test must use a separately approved DuckLake-managed, logically and physically controlled B1 comparison; this result alone is not an incremental DuckLake claim.

## Evidence and operational record

The compact evidence is in [`benchmark/generated/gcs-b1-metrics-20260809/`](../benchmark/generated/gcs-b1-metrics-20260809/), with the immutable source/object contract and code-only deployment archive in [`benchmark/generated/gcs-b1-stage-20260809/`](../benchmark/generated/gcs-b1-stage-20260809/). No Parquet payload or extension binary was transferred to this workspace. The run is entry 9 in the [append-only register](../benchmark/evidence/run-register.jsonl) and appears in the verified [compiled table](../benchmark/evidence/compiled/2026-08-09-after-gcs-b1/campaign-runs.md).

The deployment archive binds the 404f5788… pre-result digest of the B1 stage design. The live
[stage page](../stages/gcs-b1-stage.md) subsequently records the completed result, so its current text is an
assessment record rather than the execution-time design snapshot.

Two failed runtime-attestation invocations were preserved as pre-timing operational anomalies: the first pre-created a harness-owned output directory, and the second supplied a harness manifest instead of the calibration manifest that binds the CPython archive. Neither wrote a result or queried a Parquet object. The successful fresh retry attested the same runtime hash as the B* stage.

This finding applies only to the declared synthetic data shape, SQL, VM, DuckDB/httpfs build, same-region GCS and warm/default client-cache condition. It is not an S3, production data, production or DuckLake finding.
