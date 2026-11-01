from blacksmith.timing import StageTiming, Timing, fmt_duration, seconds_between

T0 = "2026-10-25T16:18:27+00:00"


def test_fmt_duration():
    assert fmt_duration(0) == "0s"
    assert fmt_duration(37.4) == "37s"
    assert fmt_duration(242) == "4m 02s"
    assert fmt_duration(3723) == "1h 02m 03s"
    assert fmt_duration(-5) == "0s"


def test_seconds_between_accepts_github_z_suffix():
    assert seconds_between("2026-10-25T16:18:27Z", "2026-10-25T16:28:03+00:00") == 576
    assert seconds_between("garbage", T0) is None


def _timed_run() -> Timing:
    t = Timing.begin(started_at="2026-10-25T16:21:32+00:00", mono=100.0,
                     labeled_at="2026-10-25T16:18:27Z")
    t.start_stage("plan", 110.0)
    t.add_session(90_000, 0.5)
    t.end_stage(230.0)
    t.start_stage("build", 230.0)
    t.add_session(600_000, 2.25)
    t.add_session(None, None)
    return t


def test_stage_and_session_accounting():
    t = _timed_run()
    assert t.stages["plan"] == StageTiming(seconds=120.0, agent_seconds=90.0, sessions=1)
    assert t.stages["build"].sessions == 2 and t.stages["build"].agent_seconds == 600.0
    assert t.sessions == 3 and t.agent_seconds == 690.0 and t.cost_usd == 2.75


def test_render_hides_the_open_stage_and_total_until_finished():
    t = _timed_run()
    text = t.render()
    assert "| plan | 2m 00s | 1m 30s in 1 session |" in text
    assert "build" not in text and "total" not in text and "label" not in text


def test_render_is_empty_before_any_stage_finished():
    t = Timing.begin("2026-10-25T16:21:32+00:00", 0.0)
    t.start_stage("plan", 1.0)
    assert t.render() == ""


def test_finish_closes_the_stage_and_renders_totals():
    t = _timed_run()
    t.finish(1000.0, "2026-10-25T16:36:32+00:00")
    assert t.stages["build"].seconds == 770.0
    assert t.total_seconds == 900.0
    assert t.end_stage(2000.0) is None  # nothing left open; a later call is harmless
    text = t.render()
    assert "| build | 12m 50s | 10m 00s in 2 sessions |" in text
    assert "| **total** | **15m 00s** | **11m 30s in 3 sessions, $2.75** |" in text
    assert ("From the label to the finish: 18m 05s, of which 3m 05s passed before the runner "
            "started.") in text


def test_render_without_label_time_or_cost():
    t = Timing.begin("2026-10-25T16:21:32+00:00", 0.0)
    t.start_stage("plan", 0.0)
    t.finish(60.0, "2026-10-25T16:22:32+00:00")
    text = t.render()
    assert "| plan | 1m 00s | none |" in text
    assert "| **total** | **1m 00s** | **none** |" in text
    assert "$" not in text and "label" not in text


def test_session_outside_a_stage_lands_in_a_dash_bucket():
    t = Timing.begin(T0, 0.0)
    t.add_session(1000, None)
    assert t.stages["-"].sessions == 1


def test_as_dict_drops_monotonic_fields_and_round_trips_stages():
    t = _timed_run()
    t.finish(1000.0, "2026-10-25T16:36:32+00:00")
    d = t.as_dict()
    assert set(d) == {"started_at", "labeled_at", "finished_at", "total_seconds", "cost_usd", "stages"}
    assert d["stages"]["plan"] == {"seconds": 120.0, "agent_seconds": 90.0, "sessions": 1}
