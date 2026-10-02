from app.core.state_render import TRIMMED, render_route_state, trim_to_tokens
from app.decisions.limits import estimate_tokens


def test_short_text_is_kept():
    assert trim_to_tokens("hello", 10) == "hello"


def test_long_text_keeps_the_start():
    text = "a" * 300 + "END"
    out = trim_to_tokens(text, 20)
    assert out.startswith("aaa")
    assert out.endswith(TRIMMED)
    assert "END" not in out
    assert estimate_tokens(out) <= 20


def test_state_is_only_the_rewritten_text():
    state = render_route_state("  How much is a manicure?  ")
    assert state == "Client's latest message:\nHow much is a manicure?"


def test_sections_follow_the_head():
    state = render_route_state("hi", sections=[("Open scenarios", " booking: focus "), ("X", "y")])
    assert state == "Client's latest message:\nhi\n\nOpen scenarios:\nbooking: focus\n\nX:\ny"


def test_client_text_gets_at_most_half_of_the_budget():
    state = render_route_state("word " * 1000, max_tokens=100)
    head = state.split("\n\n")[0]
    assert estimate_tokens(head) <= 50 + estimate_tokens("Client's latest message:\n")
    assert head.endswith(TRIMMED)


def test_whole_state_fits_the_budget():
    state = render_route_state(
        "word " * 1000, sections=[("A", "x " * 1000), ("B", "y " * 1000)], max_tokens=200
    )
    assert estimate_tokens(state) <= 200 + 5  # joins ("\n\n") are not counted per part


def test_sections_are_dropped_when_the_budget_is_out():
    state = render_route_state(
        "hi", sections=[("A", "x" * 3000), ("B", "never shown")], max_tokens=200
    )
    assert "A:\n" in state
    assert "B:" not in state
