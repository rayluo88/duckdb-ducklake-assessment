# DuckLake Part 2 — Same-Region GCS B1 Isolation

**Date:** 9 August 2026  
**Status:** Completed — B1 eligible  
**Stage ID:** `P2-GCE-GCS-B1-1GB-20260809-001`

## Question

Does the already validated **B1** layout — Parquet sorted by `(filter_key, row_id)`, Snappy,
one file — pass the same remote aggregate and Q3 criteria against A0? This follows the completed
[B* GCS assessment](../result/gcs-bstar-assessment-report.md), whose 11-file representation improved the
aggregate but regressed Q3 materially.

B1 preserves the sort order and approximately 1.307 GB compressed physical size of B*, but reduces
the candidate to one object. It is therefore an isolation test of the likely remote file-count
mechanism, not a search for another layout and not a DuckLake, S3, production data, production or
concurrency test.

## Frozen design

| Role | Required object form |
|---|---|
| A0 lane 1 | Fresh, immutable copy of corrected one-file A0 |
| A0 lane 2 | Second fresh immutable copy; byte-identical to lane 1 but a distinct object/generation |
| B1 candidate | Exactly one fresh immutable object copied from the validated local B1 file |

The source B1 file is the validated local `layout-B1-20260809/data.parquet` output. Its layout
manifest, validation result, SHA-256 and byte count are recorded in the stage manifest before
timing. The stage must reject a multi-file B1 candidate, mutable read path, unpinned generation,
unexpected hash/bytes, missing object or non-matching A0 lanes.

All three objects use a new `gcs-b1-20260809/` prefix. This intentionally reruns A0/A0 noise
against those fresh object identities: B*'s noise control stays historical evidence for B*, not a
transferable bound for a later stage.

## Procedure and decision rule

1. Deploy and verify the freshly hashed harness archive, runtime identity and pinned `httpfs`
   extension. No install, update, credentials file or external IP is allowed.
2. Stage only the three synthetic Parquet objects directly from the VM to GCS under no-overwrite
   generation preconditions. Record object generation, CRC32C, byte count and source SHA-256;
   restore read-only VM scope and object-viewer-only IAM before timing.
3. Run 19 fixed block-major A0-lane-1 versus A0-lane-2 pairs. It uses the same locked Q1–Q6
   schedule, two warm-ups per representation/query, 2,000 whole-block bootstrap resamples and Q0
   diagnostics as the B* stage. Freeze the resulting aggregate and Q3 noise bounds before B1.
4. Run 19 fixed A0-lane-1 versus B1 pairs. Every result must match before timing evidence can be
   eligible. `gcs-screen` records the source manifest hash, generation-pinned read relations,
   extension hash and raw monotonic elapsed observations.
5. B1 is `eligible` only if correctness is valid, aggregate and Q3 precision are stable, aggregate
   lower 95% log-gain CI exceeds the fresh aggregate noise bound, and Q3 lower 95% log-gain CI is
   at least the negative fresh Q3 noise bound. Q0 is recorded but never selects B1.

`valid_not_eligible` is a complete, useful result: its representation and query outputs are valid,
but it fails an effect or Q3 safety gate. An object/runtime/source failure is instead
`aborted_environment` and must not be converted into a candidate result.

## Evidence and claim boundary

The VM retains Parquet. Only the stage manifest, runtime attestation, timing JSON, command records,
checksums and an analysis summary return to this workspace. Results apply only to the assumed
synthetic shuffled dataset, locked SQL, one `n2-standard-8` VM, DuckDB 1.5.5/pinned `httpfs`,
same-region Standard GCS and warm/default cache condition. They do not establish a general cause
for B*'s Q3 regression; B1 can strengthen or weaken that file-count mechanism hypothesis.

## Result

The fresh A0/A0 control was stable at 19 blocks, with aggregate noise 0.02079 log units and Q3 noise 0.08422. The B1 screen was `eligible`: aggregate log gain 0.6687 (95% CI 0.6561–0.6886), or 48.8% lower elapsed time; Q3 log gain 0.0088 (95% CI -0.0144–0.0143), safely above the negative Q3-noise bound. All primary result comparisons matched. See the detailed [B1 assessment](../result/gcs-b1-assessment-report.md).

The B1 result supports, but does not prove, the inference that B*'s 11 remote objects contributed to its Q3 regression. B1 is now the balanced remote plain-Parquet baseline; a DuckLake increment test remains a separately gated next stage.
