# DuckLake Part 2 — First Parquet Layout Assessment

**Completed:** 9 August 2026  
**Workload:** synthetic 65,000,000-row shuffled dataset; GCE `n2-standard-8`; DuckDB 1.5.5; local balanced persistent disk  
**Scope:** Plain-Parquet read/query layout only. DuckLake, GCS, S3, production data data and the project lead's local database remain outside this result.

## Decision

Use the B* layout as the next-stage candidate: sort the data by `(filter_key, row_id)` and write
Snappy Parquet in approximately 128 MiB files. It passed a fresh 19-block confirmation against A0
and the Q3 non-aligned-filter guard.

## Method

- A0 was a 986,193,351-byte single Snappy Parquet file with 529 row groups.
- A fresh 19-block A0/A0 control established aggregate noise `0.00735` and Q3 noise `0.02266` log units.
- Every candidate passed full untimed source/value/schema/query/order validation before timing.
- Each screen used 19 block-major paired A0/candidate comparisons: 152 primary pairs plus 19 Q0 diagnostic pairs.
- The aggregate is the median equal-weight mean log gain across the eight primary queries. Positive means lower elapsed time.

The original writer invocation was unavailable. the researcher approved the frozen synthetic-A0 control
values as a documented amendment before timing; this is a provenance limitation, not evidence about
production data production files.

## Results

| Layout | Aggregate log gain (95% CI) | Approx. elapsed-time effect | Q3 result | Status |
|---|---:|---:|---:|---|
| B1 — sorted Snappy, one file | 1.086 (1.077, 1.092) | 66.2% lower | 0.001 (-0.008, 0.012) | eligible |
| B2 — unsorted Snappy, eight ~128 MiB files | 0.036 (0.034, 0.041) | 3.6% lower | 0.042 (0.032, 0.054) | eligible |
| B3 — single-file ZSTD | -0.197 (-0.199, -0.194) | 21.7% higher | -0.199 (-0.206, -0.197) | valid_not_eligible |
| B* — sorted Snappy, eleven ~128 MiB files | 1.324 (1.311, 1.326) | 73.4% lower | 0.043 (0.035, 0.053) | eligible |

Sorting is the dominant local-read factor for this query mix. Splitting unsorted files is a small
positive effect. ZSTD substantially reduced physical bytes but lost on elapsed time, consistent
with decode cost dominating this local workload. Combining sorting with target-sized files improved
on B1 in the fresh B* confirmation.

## Physical layouts

- B1: 1.307 GB, one Snappy file, 529 row groups.
- B2: 986 MB, eight Snappy files: seven ~128.6 MB files and an 85.7 MB remainder.
- B3: 381 MB, one ZSTD file, 529 row groups.
- B*: 1.307 GB, eleven Snappy files: ten ~128.5 MB target files and a 22.2 MB remainder.

The B* file count exceeds B2's 6–10 count because sort order raised the compressed representation
size. Its target-file envelope, median target size and remainder bounds passed before B* timing.

## Evidence and limits

Compact JSON manifests, raw timing observations, validation results and command records are in
[`benchmark/generated/layout-campaign-metrics-20260809/`](../benchmark/generated/layout-campaign-metrics-20260809/),
with SHA-256 values in `sha256sums.txt`. Parquet payloads were intentionally not downloaded from the
VM; therefore the results remain reproducible from the retained manifests and raw timings, but a
future byte-level Parquet audit would require rerunning the controlled generation.

Do not generalise this local layout ranking to production, GCS/S3 streaming, concurrent readers or
DuckLake. The separately controlled [same-region GCS A0-versus-B* confirmation](gcs-bstar-assessment-report.md)
has since completed: its aggregate improved, but its Q3 guard failed, so B* is not a balanced
remote-object candidate. The completed [one-file B1 isolation test](gcs-b1-assessment-report.md)
is eligible: its aggregate improved by 48.8% while Q3 remained within the guard. B1 is therefore
the remote plain-Parquet baseline for a separately gated DuckLake increment comparison.
