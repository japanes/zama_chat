from app.scenarios.start import Candidate, Vote, decide_start


def cand(key, *yes_weights, position=0, threshold=2, no=(), open=False):
    votes = tuple(Vote(f"{key}.q{i}", w, True) for i, w in enumerate(yes_weights)) + tuple(
        Vote(f"{key}.n{i}", w, False) for i, w in enumerate(no)
    )
    return Candidate(key, position, threshold, votes, open=open)


def test_two_of_three_starts_one_of_three_does_not():
    assert cand("booking", 1, 1, no=(1,)).fired
    assert not cand("booking", 1, no=(1, 1)).fired


def test_weight_equal_to_the_threshold_starts_alone():
    result = decide_start([cand("booking", 2, no=(1, 1))], None)
    assert result.fired == ["booking"]
    assert result.new == ["booking"]
    assert result.focus == "booking"
    assert result.focus_changed


def test_nothing_fired_keeps_the_focus_free():
    result = decide_start([cand("booking", 1)], None)
    assert result.fired == []
    assert result.focus is None
    assert not result.focus_changed
    assert result.sums["booking"] == {
        "sum": 1,
        "threshold": 2,
        "yes": ["booking.q0"],
        "fired": False,
    }


def test_without_focus_the_biggest_sum_wins():
    result = decide_start([cand("a", 2, position=0), cand("b", 2, 1, position=1)], None)
    assert result.fired == ["a", "b"]
    assert result.new == ["a", "b"]
    assert result.focus == "b"


def test_a_tie_goes_to_the_first_in_the_channel_order():
    result = decide_start([cand("b", 2, position=1), cand("a", 2, position=0)], None)
    assert result.fired == ["a", "b"]
    assert result.focus == "a"


def test_a_busy_focus_stays_and_the_fired_one_is_paused():
    result = decide_start([cand("booking", 2)], "interview")
    assert result.new == ["booking"]
    assert result.focus == "interview"
    assert not result.focus_changed


def test_the_scenario_in_focus_is_not_a_candidate():
    result = decide_start([cand("interview", 2), cand("booking", 2, position=1)], "interview")
    assert result.fired == ["booking"]
    assert "interview" not in result.sums


def test_an_open_paused_scenario_is_not_created_again_but_takes_a_free_focus():
    result = decide_start([cand("booking", 2, open=True)], None)
    assert result.fired == ["booking"]
    assert result.new == []
    assert result.focus == "booking"
    assert result.focus_changed
