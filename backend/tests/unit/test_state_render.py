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


def test_a_long_text_is_not_cut_while_it_fits():
    # 40 000 characters, a story pasted whole: the old cap kept about 6 000 of them
    text = "слово " * 6000 + "КІНЕЦЬ"
    state = render_route_state(text)
    assert state.endswith("КІНЕЦЬ")
    assert TRIMMED not in state


def test_only_a_text_longer_than_the_budget_is_cut():
    state = render_route_state("word " * 1000, max_tokens=100)
    assert estimate_tokens(state) <= 100 + 2
    assert state.endswith(TRIMMED)


def test_own_words_are_the_message_when_the_rewrite_is_shorter():
    story = "I was born in Kyiv. " * 50 + "Then I opened a bakery."
    state = render_route_state("Who am I?", original=story)
    assert state.startswith("Client's latest message:\nI was born in Kyiv.")
    assert "Then I opened a bakery.\n\nThe same message retold with the context of the dialogue:\n" in state
    assert state.endswith("Who am I?")


def test_a_rewrite_as_long_as_the_words_goes_alone():
    # «так» → «Так, мій чоловік був проти»: the rewrite has said more, the words add nothing
    state = render_route_state("Yes, my husband was against it", original="yes")
    assert state == "Client's latest message:\nYes, my husband was against it"


def test_the_retelling_is_cut_before_the_own_words():
    story = "word " * 600 + "END"
    state = render_route_state("retold " * 300, original=story, max_tokens=1200)
    assert estimate_tokens(state) <= 1200 + 2
    words, retold = state.split("\n\nThe same message retold with the context of the dialogue:\n")
    assert words.endswith("END")
    assert retold.endswith(TRIMMED)


def test_no_room_left_no_retelling():
    state = render_route_state("retold", original="word " * 1000, max_tokens=300)
    assert "retold" not in state and state.endswith(TRIMMED)


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
