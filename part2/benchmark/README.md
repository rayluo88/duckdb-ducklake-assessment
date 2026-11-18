# Part 2: Performance Harness

This harness generates synthetic Parquet data, checks result equivalence and executes paired
query timing. `src/ducklake_benchmark/queries.py` contains the query suite; `tests/` provides
unit and integration tests. See the [assessment](../result/assessment-report.md) and
[stage reports](../result/) for the historical findings and their boundaries.

```bash
uv sync --locked --all-groups --no-editable
uv run --locked --no-editable pytest -q
```

Cloud account, project and bucket identifiers are placeholders. Cloud-specific scripts are
archival research code, not an approved deployment recipe. The original raw evidence and
hash-chain register are not part of this anonymised release.
