# DuckDB and DuckLake: Synthetic Data Assessment

Research materials containing functional examples, benchmark code and reports for
DuckDB, Parquet and DuckLake. Data used for the assessment was synthetic.

| Part | Contents | Status |
| --- | --- | --- |
| 1 | [Local functional proof of concept](part1/poc/) and [feasibility report](part1/feasibility-report.md) | Completed; local synthetic data |
| 2 | [Performance harness](part2/benchmark/) and [assessment and stage reports](part2/result/) | Completed; local and GCE/GCS synthetic data |
| 3 | [S3 preflight code](part3/benchmark/), [plan](part3/planning/performance-plan.md) and [preparation record](part3/stages/2026-08-24-stage-0-local-preparation.md) | Preparation only; no S3 performance result |

Part 2 compares direct Parquet and DuckLake reads of identical objects. Its results apply to the
stated single-client synthetic workload and GCE/GCS environment; they do not establish production,
S3 or concurrent-user performance.

## Local setup and tests

Install [uv](https://docs.astral.sh/uv/). In each project directory (`part1/poc`,
`part2/benchmark`, `part3/benchmark`), run:

```bash
uv sync --locked --all-groups --no-editable
uv run --locked --no-editable pytest -q
```

Part 1 also provides `uv run --locked --no-editable python local-smoke-test.py`. It uses synthetic
data and may download a DuckLake extension. See [Part 1 setup](part1/poc/) for details.

## Reproducibility

Cloud account, project and bucket values in guardrail constants and test fixtures are placeholders;
the published cloud scripts are **not deployable** without independent review and configuration.
Raw run artifacts, generated datasets and the original
hash-chain register are not included, so this release alone cannot independently verify every
historical evidence hash. Python dependency locks preserve the historical dependency versions;
DuckDB extensions are installed separately and their exact builds are not guaranteed by the locks.

The S3 preflight has not been validated against a live S3 environment in this release. It can
write and delete a small object when explicitly enabled; inspect its code and permissions before
any live use. Re-evaluate the latest stable DuckDB and DuckLake versions before a new campaign.

No reuse licence is specified. Public availability does not itself grant a licence.
