# DuckLake Part 2 — Same-Region GCS B* Assessment

**Completed:** 9 August 2026  
**Workload:** assumed synthetic 65,000,000-row shuffled dataset; GCE `n2-standard-8`; DuckDB
1.5.5; same-region Standard GCS; warm/default cache  
**Scope:** plain Parquet only. DuckLake, S3, production data, concurrency and the project lead's local DuckDB remain
outside this result.

## Decision

Do not promote the local B* recommendation as the default remote-object layout. In this controlled
GCS test it delivered a statistically clear aggregate gain but failed the predeclared Q3
non-aligned-filter guard by a large, stable margin. The correct result is therefore
`valid_not_eligible`, not a remotely eligible layout.

## Method and integrity

- Two fresh, byte-identical A0 objects and the eleven-file B* representation were uploaded from
  the VM directly into a fresh GCS prefix. All 13 names, object generations, byte counts and
  CRC32C values were checked before timing.
- DuckDB read explicit generation-pinned JSON-API media URLs through the pinned `httpfs` binary.
  The VM had returned to read-only OAuth scope and object-viewer-only IAM before any timed query.
- A fresh remote A0/A0 control ran 19 shared blocks (152 primary pairs and 19 Q0 diagnostics).
  It froze aggregate noise at `0.01604` log units and Q3 noise at `0.09406`.
- The A0/B* screen used the same 19-block procedure. All result comparisons matched; aggregate and
  Q3 precision were stable. Q0 was retained as a diagnostic and did not select the candidate.

## Result

| Measure | Result |
|---|---:|
| Aggregate log gain, 95% CI | 0.1539 (0.1340, 0.1615) |
| Aggregate elapsed-time effect | 14.3% lower (12.5%–14.9%) |
| Q3 log gain, 95% CI | -0.3419 (-0.3610, -0.3215) |
| Q3 elapsed-time effect | 40.8% higher (37.9%–43.5%) |
| Aggregate effect gate | pass |
| Q3 guard | fail |
| Final status | `valid_not_eligible` |

The per-query pattern explains the decision. The sorted B* objects strongly improve the aligned
selective scans Q2a–Q2c and Q4, but are slower for the full scan Q1, non-aligned Q3, group-by Q5
and join Q6. B* has eleven remote objects and a 1.307 GB physical representation, versus A0's
single 986 MB object. The result is therefore consistent with those additional remote-object and
full-scan-byte costs outweighing sort/pruning benefits when a query cannot exploit the sort order.
It is an inference from the pattern, not an attribution proof: no DuckDB profile or GCS
request-count trace was collected in this narrow stage.

## Next decision

The approved follow-up is the **[B1-only GCS test](../stages/gcs-b1-stage.md)**: sorted Snappy in one file
versus the one-file A0 baseline. It holds the sorted physical bytes essentially constant relative to
B* while removing B*'s eleven-file remote effect, and tests whether Q3 can remain within the same
guard. Do not move on to a DuckLake comparison until a balanced plain-Parquet remote representation
is established.

## Evidence and limits

The compact evidence set is in
[`benchmark/generated/gcs-bstar-metrics-20260809/`](../benchmark/generated/gcs-bstar-metrics-20260809/):
the remote object manifest, runtime attestation, raw timing JSON, distinct command records and the
VM-origin checksum file. The run is entry 8 in the append-only evidence register and was included
in the verified compiled table at
[`benchmark/evidence/compiled/2026-08-09-after-gcs-bstar/`](../benchmark/evidence/compiled/2026-08-09-after-gcs-bstar/).
No Parquet payload was copied to this workspace. The separately named
`gcs-bstar-metrics-flat-transfer-incomplete-20260809/` directory is a quarantined, unused first
transfer that lacked one distinct command record; it is not part of this assessment's evidence
chain.

This finding applies only to the declared synthetic data shape, SQL, VM, DuckDB/httpfs build,
same-region GCS and warm/default client-cache condition. It is not an S3, production data, production or
DuckLake finding.
