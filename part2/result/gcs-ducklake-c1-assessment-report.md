# C1 DuckLake Catalogue-Over-Identical-Parquet Assessment

**Date:** 9 August 2026  
**Stage:** `P2-GCE-GCS-DUCKLAKE-C1-1GB-20260809-001`  
**Decision:** `eligible` for the declared synthetic, single-client, warm/default-cache GCE/GCS
comparison

## Answer

For this controlled C1 workload, DuckLake reduced aggregate query elapsed time by **43.4%** versus
a direct `read_parquet` scan of the **same immutable GCS Parquet object**. The paired aggregate log
gain was 0.5688 (95% CI 0.5567–0.5816), equivalent to an estimated elapsed-time reduction of
42.7%–44.1%. The nonaligned Q3 guard also improved by 18.3% (95% CI 17.1%–19.9%), so it did not
hide a regression.

This is credible evidence that a read-only DuckLake catalogue can improve this particular
query-in-place path even when Parquet bytes, object generation, logical results and observed row
order are held fixed. It is not evidence about production data, the target environment, S3, concurrent clients, a
DuckLake writer, compaction, or a production catalogue.

## Controlled comparison

| Item | Direct baseline | Candidate |
|---|---|---|
| Data object | `gcs-ducklake-c1-20260809/c1/data.parquet`, generation `1786277926382433` | The same registered object |
| Parquet SHA-256 / bytes | `d27011…7d65f1` / 1,307,149,012 | The same |
| Read path | Generation-pinned HTTPS `read_parquet` | Local read-only DuckLake catalogue, `c1.main.b1` |
| Catalogue metadata | Not used | Local DuckDB metadata catalogue; SHA-256 `407bfd…b5dc14` |
| Client/storage | One GCE VM, one same-region GCS bucket, warm/default cache | The same |

The C1 object is a create-only byte-identical clone of the eligible B1 object. The original B1
object was never registered in DuckLake. C1 used `ducklake_add_data_files`, not a DuckLake writer,
and `data_inlining_row_limit = 0`; the 1.3 GB data file was not inlined into the 4.2 MB local
catalogue.

## Gates passed

- Untimed direct-clone versus DuckLake validation returned 65,000,000 rows on both sides, exact
  multiset equivalence, matching Q0–Q6 results, zero floating difference and zero
  `filter_key,row_id` observed-order failures.
- A fresh 19-block direct-clone/direct-clone control was stable. Its aggregate noise bound was
  0.0777 log units and its Q3 noise bound was 0.2022 log units.
- The 19-block causal screen was stable and passed its aggregate effect, aggregate precision, Q3
  precision and Q3-veto gates.

## Query-level context

| Query | Direct median | DuckLake median | Median elapsed reduction |
|---|---:|---:|---:|
| Q1 narrow full scan | 183.47 ms | 144.97 ms | 20.9% |
| Q2a aligned 0.1% filter | 51.45 ms | 14.18 ms | 71.9% |
| Q2b aligned 1% filter | 53.83 ms | 15.46 ms | 71.3% |
| Q2c aligned 10% filter | 67.32 ms | 29.21 ms | 57.1% |
| Q3 nonaligned guard | 202.74 ms | 165.69 ms | 18.3% |
| Q4 multi-column filter | 100.65 ms | 64.96 ms | 36.2% |
| Q5 group by | 252.39 ms | 211.56 ms | 15.9% |
| Q6 mapping join | 683.93 ms | 648.14 ms | 5.3% |

The strongest gains are in the aligned selective queries, which makes catalogue metadata and
planning a plausible explanation. The untimed `EXPLAIN` diagnostic shows the same projections and
filters on both paths, differing only in the scan node (`READ_PARQUET` versus `DUCKLAKE_SCAN`). It
does not report remote bytes, GCS requests or per-row-group decisions. Therefore this assessment
does **not** claim that a particular pruning or caching mechanism caused the improvement.

## Evidence and integrity

The screen is entry 10 (`P2-GCE-GCS-DUCKLAKE-C1-DIRECT-1GB-20260809-001`) in the verified
[append-only evidence register](../benchmark/evidence/run-register.jsonl). Its checksum-linked compact
evidence is retained locally; no Parquet payload or DuckLake metadata database was downloaded.

- [Causal screen result](../benchmark/generated/gcs-ducklake-c1-stage-20260809/evidence/direct-clone-20260809/ducklake-screen-result.json)
  — SHA-256 `3fd43a47cdd30c8bc3eaae712d5ea7c3c924100dd16ae5525b7ab8160c91c967`
- [Noise control](../benchmark/generated/gcs-ducklake-c1-stage-20260809/evidence/direct-clone-20260809/ducklake-noise-result.json)
  — SHA-256 `0b1eecd5cbf759d37ae5738c0625589bf99a449e15660f5a2031342f4c831665`
- [Correctness result](../benchmark/generated/gcs-ducklake-c1-stage-20260809/evidence/direct-clone-20260809/ducklake-validation-result.json)
  — SHA-256 `b683fad9e247973199213be4f205c163210882e7e0efa627034e03b89282eb36`
- [Untimed plan diagnostic](../benchmark/generated/gcs-ducklake-c1-stage-20260809/evidence/direct-clone-20260809/ducklake-c1-explain-diagnostic.json)
  — SHA-256 `8cc87246ddf9ac48f2e756a5036c876892089d4aa9ad7912a9f39c3ca33ef302`
- [C1 stage manifest](../benchmark/generated/gcs-ducklake-c1-stage-20260809/evidence/c1-stage-manifest.json)
  — SHA-256 `ef0754ce2d90ce5e9cfe51567401e9757dac32ec2c22057747319e912b622b1b`

The governing C1 design and its limits are in [gcs-ducklake-c1-stage.md](../stages/gcs-ducklake-c1-stage.md).

## Recommended next decision

Do not yet infer a general DuckLake recommendation. The most informative next approved test would
be the separate **Cm many-file sensitivity**: direct Parquet versus DuckLake over the exact same
generation-pinned multi-file layout, with GCS request/byte instrumentation if observable. That
would test whether C1's result survives the file-discovery condition closer to the project lead's intended
S3-streaming use case. A DuckLake-writer/compaction experiment and multi-client/concurrency test
remain distinct, later questions.
