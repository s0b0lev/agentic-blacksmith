import pytest

from blacksmith.state import State, load_state, save_state


def test_state_roundtrip(tmp_path):
    path = tmp_path / "state.json"
    state = State(issue=7, branch="issue-7", pr_number=3, pr_url="http://x/3", stage="review", round=2)
    save_state(path, state)
    assert load_state(path) == state
    assert path.read_text().endswith("\n")


def test_save_is_atomic_leaves_no_tmp(tmp_path):
    path = tmp_path / "state.json"
    save_state(path, State(issue=1, branch="b"))
    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]


def test_load_state_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_state(tmp_path / "nope.json")
