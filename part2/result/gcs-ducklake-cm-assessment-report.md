# DuckLake Cm multi-file sensitivity — assessment result

**Stage:** `P2-GCE-GCS-DUCKLAKE-CM-MULTIFILE-20260809-001`  
**Completed:** 9 August 2026, 23:28 SGT  
**Status:** `eligible`

## Answer

DuckLake was faster than a direct Parquet scan over the **same eight immutable,
generation-pinned B2 Parquet objects**: **28.4% lower aggregate elapsed time**
(95% bootstrap interval: **27.8% to 29.4%**). Q3, the conservative safeguard
query, was **33.0% lower** (95% interval: 32.2% to 33.6%) and passed its veto.

This is a bounded multi-file sensitivity result. It supports the C1 finding that
DuckLake can improve the tested cloud read/query workload, now for an eight-file
layout rather than C1's single-file layout.

## Controlled comparison

| Property | Direct baseline | DuckLake candidate |
|---|---|---|
| Parquet bytes | Eight B2 objects, explicit generation-pinned URLs | The exact same eight object generations registered as DuckLake data files |
| Object sizes | Seven 128.6 MB files and one 85.7 MB remainder | Same |
| Read relation | `read_parquet([eight pinned URLs])` | `cm.main.b2` |
| Catalogue | None | Local, single-client, read-only DuckLake metadata catalogue |
| Engine/storage | DuckDB 1.5.5; same-region GCE and GCS | Same |
| Timing | Fixed 19 block-major paired blocks; two warmups per representation/query | Same |

Untimed validation passed exact data equivalence and Q0–Q6 query-result equality
before timing. The A0/A0-style direct-versus-direct control used the same runtime
setup; aggregate noise was 0.0723 log units and Q3 noise was 0.0832 log units.

## Results

| Measure | Estimate |
|---|---:|
| Aggregate elapsed-time reduction | 28.4% |
| Aggregate 95% interval | 27.8%–29.4% |
| Q3 elapsed-time reduction | 33.0% |
| Q3 95% interval | 32.2%–33.6% |
| Primary paired observations | 152 (19 blocks × 8 cells) |
| Q0 diagnostic observations | 19 |

## Evidence and limits

Only compact JSON evidence was copied back. The eight Parquet files and the
local `metadata.ducklake` catalogue remain on GCP/VM.

- [Cm stage manifest](../benchmark/generated/gcs-ducklake-cm-20260809/cm-stage-manifest.json) — SHA-256 `e0fc92fa995167a9f65007705b7cb2f075255e5cb95f3fc0136df21f9d249890`
- [Cm screen result](../benchmark/generated/gcs-ducklake-cm-20260809/cm-screen-result.json) — SHA-256 `caf5834fe6a009274d5d5e4686379959422512f65ad0968e4c4cd020c31b9f9f`

The result is synthetic, same-region, single-client GCE/GCS evidence with eight
files. It is **not** evidence about production data, the target environment, S3, production behaviour,
concurrent access, DuckLake writer/compaction, or all multi-file layouts.
