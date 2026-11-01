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


def test_state_without_timing_still_loads(tmp_path):
    path = tmp_path / "state.json"
    path.write_text('{"issue": 7, "branch": "b", "stage": "done", "round": 0}\n')
    assert load_state(path).timing is None


def test_timing_round_trips(tmp_path):
    path = tmp_path / "state.json"
    timing = {"started_at": "2026-10-25T16:21:32+00:00", "stages": {"plan": {"seconds": 12.5}}}
    save_state(path, State(issue=7, branch="b", timing=timing))
    assert load_state(path).timing == timing
