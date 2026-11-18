# DuckLake-Managed Parquet Performance Assessment

**Prepared by:** the researcher  
**Report date:** 16 August 2026  

## Executive summary

- **DuckLake materially reduced query elapsed time in the tested Parquet access path.** Against
  direct reads of the exact same immutable cloud objects, combined elapsed time was 43.4% lower for
  one file and 28.4% lower for eight files.
- **DuckLake passed the declared controls in both the one-file and eight-file comparisons.** Each final benchmark completed 19 paired
  repetitions over eight analytical query types after correctness validation and a
  direct-versus-direct noise control. The safeguard query (designed to detect a slowdown outside
  the file's sort order) also improved in both conditions.
- **The evidence supports a targeted Amazon Simple Storage Service (S3) validation, not immediate
  production adoption.** The tests used synthetic data, one client and same-region Google Compute
  Engine and Google Cloud Storage. S3, production data, concurrent access and the reason for the
  measured gain remain untested.

## Problem and intended outcome

**Decision addressed:** Does DuckLake management improve read/query efficiency over direct access
to the same Parquet bytes enough to justify controlled S3 validation?

The current design sends notebook Structured Query Language (SQL) through JuPySQL, the notebook
interface, to an in-process DuckDB engine. DuckDB then uses its HTTP/S3 connector (`httpfs`) to
query Parquet objects directly from S3. DuckLake is not currently part of that read path. This
assessment therefore isolates one decision: whether adding a DuckLake catalogue over unchanged
Parquet objects can reduce query elapsed time before S3, production-data and concurrent-user
validation are available.

DuckLake adds database-style table and catalogue metadata over Parquet files; the underlying data
remains in Parquet. In this report, **direct access** means DuckDB reads the files through
`read_parquet`, while **DuckLake-managed access** means DuckDB reaches the same files through the
DuckLake catalogue.

The intended outcome is a decision on whether to carry a narrow direct-Parquet-versus-DuckLake
comparison into the approved S3 environment. It is not a general DuckLake adoption or data-migration
recommendation.

## What was measured

Elapsed-time reduction compares a DuckLake catalogue scan with a direct `read_parquet` scan of the
**same generation-pinned Parquet object or objects**. The combined result gives equal weight to
eight analytical query types. Each condition used 19 paired repetitions: every repetition ran both
access methods, with their order varied to reduce timing bias. Each query also received two warmup
runs, and a direct-versus-direct run measured normal timing noise.

One of the eight queries was **Query 3 (Q3), the non-aligned safeguard query**. It filters on a
field deliberately unrelated to the file's sort order. Its purpose is to catch an optimization
that improves the combined result while materially slowing a query that cannot benefit from that
ordering. A candidate could not pass if this safeguard query slowed beyond the pre-measured noise
allowance. A separate metadata-only diagnostic was recorded but did not affect the decision.

## Test environment

| Layer | Recorded configuration |
|---|---|
| Compute | Google Compute Engine `n2-standard-8`: 8 virtual CPUs, 32 GB memory, Linux x86-64 |
| Storage | Google Cloud Storage Standard in `asia-southeast1`, with compute in `asia-southeast1-b` |
| Runtime | DuckDB 1.5.5; matching DuckLake and `httpfs` extensions loaded from pinned local binaries with extension auto-install disabled |
| Data | Synthetic 65-million-row dataset; one-file 1.31 GB and eight-file 0.99 GB conditions |
| Physical control | Direct and DuckLake reads used identical immutable Parquet bytes within each final comparison |
| Catalogue | Local, single-client DuckDB metadata catalogue, attached read-only during timing |
| Cache and repetitions | Warm/default cache; 19 paired repetitions; two warmups per access method and query |

## Experiment sequence

DuckLake was tested only after a balanced remote Parquet baseline had been established. Local
screening favoured sorted, target-sized files, but the eleven-file representation failed the remote
non-aligned safeguard query because that query became materially slower. A sorted one-file
representation then passed as the remote baseline. DuckLake was subsequently tested over the
identical one-file object and an identical eight-file sensitivity layout.

The effects below are relative to each stage's own baseline and must not be added across stages.
Positive values mean lower elapsed time; a negative value means the candidate was slower.

| Step | Stage | Candidate | Combined effect | Safeguard-query effect | Decision |
|---:|---|---|---:|---:|---|
| 1 | Local layout benchmark | Sorted, approximately 128 MB files | 73.4% lower | 4.2% lower | Selected locally |
| 2 | Google Cloud Storage layout validation | Sorted eleven-file layout | 14.3% lower | 40.8% higher | Rejected: safeguard query was slower |
| 3 | Google Cloud Storage baseline isolation | Sorted one-file layout | 48.8% lower | 0.9% lower | Accepted remote baseline |
| 4 | DuckLake one-file increment | DuckLake over the identical one-file object | 43.4% lower | 18.3% lower | Passed all gates |
| 5 | DuckLake eight-file sensitivity | DuckLake over the identical eight files | 28.4% lower | 33.0% lower | Passed all gates |

## DuckLake remained faster with one and eight files

| Condition | Combined reduction | 95% confidence interval | Safeguard-query reduction | Safeguard-query 95% confidence interval | Decision result |
|---|---:|---:|---:|---:|---|
| One file | **43.4%** | 42.7%–44.1% | 18.3% | 17.1%–19.9% | Passed all gates |
| Eight files | **28.4%** | 27.8%–29.4% | 33.0% | 32.2%–33.6% | Passed all gates |

The performance gain was large in both controlled conditions. The smaller aggregate gain with eight
files should not be treated as a scaling curve: two layouts are insufficient to infer how the
benefit changes with file count.

## Relevance to the current access pattern

The current setup uses JuPySQL as the notebook interface to an in-process DuckDB engine that reads
Parquet directly from S3 through its HTTP/S3 connector. The assessment reproduced the same logical
comparison on Google Cloud Storage: direct object-store Parquet versus DuckLake-managed access to
identical bytes. JuPySQL is not a separate execution engine, so the DuckDB timing remains relevant.
Notebook rendering and end-to-end interactive latency were not measured.

## Announced asynchronous I/O is not yet in the stable runtime

DuckDB announced asynchronous reads for Parquet and CSV on 31 July 2026. The feature is available
only in DuckDB 2.0 development preview builds and is planned to be enabled by default in stable
DuckDB 2.0, tentatively scheduled for fall 2026. It is therefore **not available in the stable
DuckDB 1.5.5 runtime used by this assessment as of 16 August 2026**.

This announcement does not invalidate the measured results, which remain specific to DuckDB 1.5.5.
It does mean that future remote-read performance may change. DuckDB states that Parquet-backed data
lakes, including DuckLake, can benefit from asynchronous I/O automatically. Because the feature may
improve both direct Parquet and DuckLake-managed reads, its effect on the relative difference is not
yet known and must be measured rather than inferred.

## Feasibility and readiness assessment

| Criterion | Current assessment | Decision implication |
|---|---|---|
| DuckLake query efficiency | **Favourable in the tested conditions** | Supports an S3 validation rather than stopping the assessment |
| Correctness and repeatability | **Passed the controlled benchmarks** | Results matched before timing and both final benchmarks met their precision and safeguard gates |
| Current query-path relevance | **Architecturally aligned** | DuckDB remains the execution engine; JuPySQL is only the SQL interface |
| S3 transferability | **Not established** | Repeat the direct-versus-DuckLake comparison on approved S3 access |
| Concurrent access | **Not established** | Do not infer multi-user throughput or an object-store bandwidth limit |
| Production representativeness | **Not established** | Validate the real file-count distribution, schemas and query mix before adoption |
| Catalogue operations | **Not assessed in this performance assessment** | Multi-user metadata backend, ownership and recovery remain separate decisions |
| Product maturity | **Not reassessed in this performance assessment** | Continue to rely on the earlier feasibility assessment unless a production decision requires a fresh official review |

## Recommended next steps

1. **Repeat only the direct-versus-DuckLake comparison on S3** when approved access is available,
   using the same immutable files and a representative query mix.
2. **Capture request and byte metrics** so any gain can be attributed to listing, metadata reuse,
   pruning or another mechanism.
3. **Run bounded concurrency separately** only after the single-client S3 result is stable; test 1,
   2, 4 and 8 independent clients and identify the cause of any plateau before calling it an S3
   bandwidth limit.
4. **Treat the local DuckDB database as contextual evidence.** Classify its objects as native
   tables, external views or mixed only if that path becomes part of the decision.
5. **Repeat the two-lane benchmark after stable DuckDB 2.0 is released**, using the same DuckDB
   version and controls for direct Parquet and DuckLake-managed access.

## Questions for the S3 follow-up

- Is query-in-place latency the primary success measure, or should notebook wall-clock and time to
  first result also be included?
- What file-count distribution and query mix best represent the intended workload?
- Will multiple users share one catalogue, and if so, what metadata backend and access model are
  intended?

## Caveats and assumptions

The evidence is synthetic, same-region Google Compute Engine/Google Cloud Storage,
warm/default-cache and single-client. It does not establish performance for S3, production
datasets, concurrent access, DuckLake writing or compaction, or all Parquet layouts. The exact
cause of the measured improvement was not instrumented. The current-setup illustration confirms
the direct-S3 concept but does not by itself prove deployed versions, credentials, cache settings
or the contents of any local DuckDB database.

The renderable [assessment artifact](assessment-report-artifact.json) contains the same stakeholder
narrative, chart, tables and executable source queries. Supporting stage readouts are under
[`result/`](result/), while the append-only evidence chain and raw compact metrics remain under
[`benchmark/evidence/`](benchmark/evidence/).
