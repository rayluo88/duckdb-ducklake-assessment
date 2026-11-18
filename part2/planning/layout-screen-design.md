# DuckLake Part 2 — Plain-Parquet Layout Screen Design

**Date:** 9 August 2026  
**Status:** Approved experimental design; implementation not started  
**Scope:** Focused GCE-local plain-Parquet B1–B3 generation, correctness and timing only  
**Out of scope:** GCS candidate timing, DuckLake, S3, production data data and the project lead's local `*.db`

## Decision being tested

For this single assumed shuffled dataset and locked query mix, screen which independently controlled
Parquet layout properties reduce elapsed query-in-place time for Q1–Q6 relative to A0, without a
material regression in the Q3 non-aligned-filter guard. Q0 remains a metadata/request diagnostic
and does not select a winner. This screen cannot establish a transferable layout ranking.

All variants use the same 65,000,000-row shuffled synthetic dataset, seed `20260809`, schema and
logical values as the corrected GCE-local A0. Every design parameter remains `assumed`; no result
is production data-representative.

## Fixed baseline

A0 is the DuckDB 1.5.5 default-write Parquet already measured on the GCE VM:

- one file, 986,193,351 physical bytes;
- 65,000,000 rows and 529 row groups;
- approximately 122,880 rows per row group;
- Snappy compression;
- source order by `row_id`;
- unpartitioned; and
- locked Q0–Q6 SQL and corrected GCE-local calibration procedure.

Every B manifest must bind to both immutable source objects:

- `benchmark.duckdb`: 903,622,656 bytes, SHA-256
  `2fb0e47994a5c6253d71d2b0402634c40710297a1d0c4c01194721a7b8c1d544`;
- A0 Parquet: 986,193,351 bytes, SHA-256
  `28869339241259e9162e5fb1cb8660acfa1e9c791b6182384a78f1794830a7f0`.

Generation is not timed as query performance. DuckDB is the controlled Parquet writer and reader;
DuckLake is neither installed nor loaded in this stage.

## Frozen one-factor matrix

Each B variant changes exactly one physical factor from A0. DuckDB writer threads are fixed at one
for deterministic file creation; measured reader threads remain fixed at the campaign baseline of
eight. Actual file count, file sizes, row groups, codec and Parquet metadata are recorded rather
than inferred from requested targets.

| ID | Only changed factor | Frozen DuckDB write control | Mechanism under test |
|---|---|---|---|
| B1 | Sort order | `ORDER BY filter_key, row_id` before default Parquet write | Tighter row-group min/max ranges and fewer bytes read for Q2a–c and Q4 |
| B2 | Smaller target files | `FILE_SIZE_BYTES '128MB'`, `PER_THREAD_OUTPUT true`, `FILENAME_PATTERN 'part_{i}'`, one writer thread, Snappy and default row groups | More object/file concurrency versus more opens, metadata and later cloud requests |
| B3 | Compression codec | `COMPRESSION ZSTD, COMPRESSION_LEVEL 3` | Fewer stored/transferred bytes versus additional decode CPU |

`FILE_SIZE_BYTES` is a best-effort boundary at row-group boundaries. Here, MiB means 1,048,576
bytes. B2 uses a fresh directory output with the exact mechanics above and no overwrite option.
Before any timing is inspected, sort its files by writer index and treat the last as the permitted
remainder. B2 is accepted only when:

- total file count is 6–10, consistent with splitting approximately 940.5 MiB around 128 MiB;
- every non-final file is 96–160 MiB and their median is 112–144 MiB; and
- the final remainder is greater than zero and no larger than 160 MiB.

These tolerances permit DuckDB's row-group-boundary rollover while still identifying a genuine
approximately-128-MiB condition. Failure is recorded as unsupported before timing; limits are not
changed after observing the files. Requested and observed sizes are both retained.

Larger row groups, Hive partitioning and a 512 MB midpoint are deferred. They may be reconsidered
only after the focused assessment is analysed; they are not silently introduced during this run.

### Causal-versus-consequential metadata contract

Frozen inputs for every B variant are source hashes, schema and value multiset, DuckDB/runtime,
writer thread count, and every writer option other than the declared factor. Set and record all
relevant output controls explicitly: Snappy compression (except B3), compression level where
applicable, row-group size 122,880, Parquet version, dictionary-page limit,
`preserve_insertion_order`, partition-column writing, per-thread output, filename pattern, append,
overwrite and writer thread count. Also preserve the full resolved `duckdb_settings()` output.
Consequential changes are allowed only as follows:

| Variant | Allowed downstream differences from A0 |
|---|---|
| B1 | Physical row order, row-group statistics and resulting bytes/compression ratio |
| B2 | File count/boundaries/names, row-group allocation by file and resulting bytes |
| B3 | Codec, compressed bytes and codec-dependent page/chunk sizes |

Any change outside the relevant allowlist aborts the comparison. “One factor” therefore describes
the controlled writer input, not the physical consequences caused by that input.

No full factorial is run. A many-small-files Bm condition is deferred until after B* and remains a
separate sensitivity test.

## Data flow and artifacts

1. Open the existing immutable GCE-local `benchmark.duckdb` read-only as the logical source.
2. Write each variant once into a fresh, non-overwritable directory.
3. Produce a representation manifest containing the source hashes, exact SQL, settings, provenance
   labels, Python/package/OS/CPU/disk identity, complete DuckDB settings and extension state, file
   inventory, per-file SHA-256 and bytes, row-group inventory, compression metadata and generation
   duration.
4. Run untimed correctness gates before exposing the representation to the timer.
5. Time A0 versus one B variant in seeded paired blocks using the frozen GCE-local procedure.
6. Write raw observations and statistical summaries to a fresh result file, then append only
   validated runs to the checksum-chained evidence register.

A variant with a correctness mismatch is marked invalid and is never timed. Existing A0 evidence
and generated B evidence are immutable.

## Correctness gates

All representations use a sorted explicit list of resolved Parquet files with
`read_parquet(file_list, hive_partitioning=false)`, including the single-file representations.

For A0 and every B variant, DuckDB must confirm:

- identical schema, row count, null counts and exact integer/`DECIMAL` aggregates;
- exact Q0–Q6 equality for canonical non-floating outputs and the already predeclared absolute and
  relative tolerances for floating outputs;
- full 65-million-row `EXCEPT ALL` equivalence in both directions against the native source table;
- consistent compact fingerprints at 1, 4 and 8 reader threads.

Fingerprints support corruption detection but are not proof. If exact full-scale multiset checking
is operationally infeasible, timing stops until an explicit design amendment is approved; it is
not silently replaced with fingerprints. Metadata must prove the frozen-input and allowed-
consequence contract above.

## Timing and run order

- Environment: the existing `n2-standard-8` GCE VM and local balanced persistent disk.
- Engine: pinned DuckDB 1.5.5 and the existing Python runtime.
- Reader threads: eight; memory and cache settings fixed and recorded.
- Process/cache condition: warm/default cache only. Create one fresh connection for each primary
  query, create its A0/B views, and perform two warmups per representation outside measured blocks.
  Keep those eight connections open through the run. Do not clear OS page cache and do not call the
  condition cold.
- Pairing and schedule: execute block-major. For block `b`, visit all eight primary queries in a
  frozen query order seeded from `20260809 + b`; within each query independently seed whether A0 or
  B runs first from the campaign seed, variant ID, block and query ID. Complete the eight query
  pairs for block `b` before starting block `b+1`. This makes a whole block a defensible shared
  environmental bootstrap unit.
- Repetitions: fixed at 19 pairs for every primary cell; no candidate-dependent stopping.
- Precision: bootstrap 95% interval half-width must not exceed `log(1.05)`.
- Bootstrap: 2,000 seeded resamples of whole shared block identifiers; never resample individual
  query observations independently.
- Q0: recorded separately and never included in layout selection.
- Interleave an A0 control with every B variant; do not compare B timings only with an old A0 run.
- Record raw `time.perf_counter_ns` observations, order, monotonic start/end timestamps, process ID,
  load average, free memory and competing benchmark processes. Abort on VM identity/runtime drift
  or another active benchmark harness.

Before B generation, run a new 19-block A0-versus-A0 control under this exact shared-block runner.
Its result and manifest hashes become the immutable layout-screen noise reference; old seven-pair
calibration evidence remains valid but is not repurposed for the aggregate selection statistic.

## Winner rule

The eight primary cells—Q1, Q2a, Q2b, Q2c, Q3, Q4, Q5 and Q6—receive equal weight. For block `b`
and query `q`, define signed log speedup `g[b,q] = log(t_A0[b,q] / t_B[b,q])`. Define block score
`s[b]` as the arithmetic mean of the eight `g[b,q]` values. The aggregate point estimate is the
median of the 19 `s[b]` values; its 95% interval comes from the 2,000 whole-block bootstrap above,
with seed `20260809 + sum(ord(character) for character in variant_id)`.

Apply the identical statistic to the new A0-versus-A0 control and define aggregate noise `N_agg`
as the 95th percentile of `abs(s_noise[b])`. Define Q3 noise `N_Q3` as the 95th percentile of the
absolute Q3 A0-versus-A0 log gains. For candidate Q3, compute its median log speedup and whole-block
bootstrap interval separately. A factor is eligible for B* only when:

1. correctness and one-factor metadata gates pass;
2. the aggregate precision status is stable at 19 pairs;
3. the aggregate lower 95% confidence bound is strictly greater than `N_agg`; and
4. Q3 is precision-stable and its lower 95% confidence bound is greater than or equal to `-N_Q3`.

Equality fails rule 3 and passes rule 4. An unstable aggregate or Q3 cell is automatically
ineligible. Physical-size reduction is reported but cannot select a winner by itself. The three
individual screens are exploratory engineering evidence, not three family-wise inferential claims.
A bounded null finding is permitted only when all three planned screens are valid, supported,
precision-stable and complete, but none passes the effect gates. Unsupported, correctness-invalid,
environmentally aborted or unstable factors remain individually inconclusive and prohibit a
general null finding; they do not prevent other fully eligible factors from proceeding to B*.

Before generating B*, freeze a separate combined-factor manifest specification containing the
selected controls, exact combined SQL/settings and the union of their allowed consequential
differences. When sorting is selected, retain deterministic `ORDER BY filter_key, row_id` while
applying any selected file-size and compression controls. B* rule 1 becomes correctness plus
compliance with this predeclared combined-factor contract; rules 2–4 remain
unchanged. If selected controls conflict, the combined layout cannot meet its acceptance criteria,
or an unlisted physical difference occurs, B* is inconclusive and timing stops for an approved
design amendment—no factor is silently removed after results are known. Confirm a valid B* against
a fresh interleaved A0. Only that fresh B* confirmation may support the selected layout
improvement; never add individual speedups.

## Failure handling and claim boundary

- Refuse existing output paths and partial manifests.
- Preserve failed/unstable results with their original status; do not selectively rerun cells.
- Abort on data mismatch, undeclared physical changes, missing provenance, VM drift or a changed
  DuckDB/runtime hash.
- If a requested file-size layout cannot be produced reproducibly, record it as unsupported and do
  not substitute another layout after inspecting timing.
- Report this stage only as synthetic GCE-local Parquet-layout evidence. GCS, DuckLake, S3 and
  production data conclusions require their later gated stages.
- Defer moderately correlated and strongly clustered sensitivity scenarios until after the
  shuffled screen. Without those reruns, prohibit a general or transferable layout ranking.

## Verification before implementation handoff

Implementation must add tests for manifest validation, one-factor isolation, deterministic writer
SQL, correctness failure, non-overwrite behaviour, paired scoring and the Q3 veto. A small local
fixture must pass all paths before any 65-million-row variant is generated on GCE.
