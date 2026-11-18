from ducklake_benchmark.provenance import hash_tree


def test_hash_tree_is_stable_and_ignores_disposable_files(tmp_path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "b.txt").write_text("bravo")
    (first / "a.txt").write_text("alpha")
    (second / "a.txt").write_text("alpha")
    (second / "b.txt").write_text("bravo")
    (second / ".pytest_cache").mkdir()
    (second / ".pytest_cache" / "noise").write_text("ignored")

    assert hash_tree(first) == hash_tree(second)

    (second / "b.txt").write_text("changed")
    assert hash_tree(first) != hash_tree(second)
