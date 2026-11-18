# Part 1: Local Functional Proof of Concept

The local experiment compares CSV, plain Parquet and DuckLake on deterministic synthetic data,
and checks updates, historical snapshots and reopening the catalogue. From this directory:

```bash
uv sync --locked --all-groups --no-editable
uv run --locked --no-editable pytest -q
uv run --locked --no-editable python local-smoke-test.py
```

The smoke test may need network access to install the DuckLake extension. See the
[feasibility report](../feasibility-report.md) for results and limitations.
