# Part 3: S3 Preflight

This is preparation code, not a completed S3 performance benchmark. The default tests use
simulated S3 clients; no live S3 test is run by `pytest`. The live probe requires explicit
configuration and can create and delete one small object. Review its implementation, IAM
permissions and object scope before enabling it.

```bash
uv sync --locked --all-groups --no-editable
uv run --locked --no-editable pytest -q
```
