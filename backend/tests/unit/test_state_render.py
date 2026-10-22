from app.core.state_render import (
    CLIENT,
    RETOLD,
    TRIMMED,
    render_route_parts,
    split_state,
    split_to_tokens,
    trim_to_tokens,
)
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


def _words(text: str) -> list[str]:
    return text.split()


def test_a_text_that_fits_is_one_piece():
    assert split_to_tokens("  one two  ", 500) == ["one two"]


def test_pieces_fit_and_lose_nothing():
    text = "\n\n".join(f"Абзац {i}. " + "слово " * 300 for i in range(10))
    pieces = split_to_tokens(text, 1000)
    assert len(pieces) > 1
    assert all(estimate_tokens(p) <= 1000 for p in pieces)
    assert _words(" ".join(pieces)) == _words(text)


def test_paragraphs_are_kept_whole_when_they_fit():
    paragraphs = [f"P{i} " + "word " * 500 for i in range(6)]
    pieces = split_to_tokens("\n\n".join(paragraphs), 1000)
    for piece in pieces:
        for line in piece.split("\n\n"):
            assert line.strip() in [p.strip() for p in paragraphs]


def test_a_long_paragraph_is_cut_on_sentences():
    text = " ".join(f"Sentence {i} " + "word " * 100 + "end." for i in range(20))
    pieces = split_to_tokens(text, 400)
    assert len(pieces) > 1
    assert all(p.endswith("end.") for p in pieces)
    assert all(estimate_tokens(p) <= 400 for p in pieces)


def test_a_word_longer_than_a_piece_is_cut():
    pieces = split_to_tokens("я" * 2000, 300)
    assert "".join(pieces) == "я" * 2000
    assert all(estimate_tokens(p) <= 300 for p in pieces)


def test_split_state_labels_the_parts():
    parts = split_state("Client's request", "слово " * 2000, 1000, after="Tail:\nx")
    assert len(parts) > 1
    assert parts[0].startswith(f"Client's request, part 1 of {len(parts)}:\n")
    assert parts[-1].startswith(f"Client's request, part {len(parts)} of {len(parts)}:\n")
    assert all(p.endswith("\n\nTail:\nx") for p in parts)
    assert all(estimate_tokens(p) <= 1000 for p in parts)


def test_split_state_that_fits_has_no_part_label():
    assert split_state("Client's request", " a b ", 100) == ["Client's request:\na b"]


def test_state_is_only_the_rewritten_text():
    assert render_route_parts("  How much is a manicure?  ") == [
        "Client's latest message:\nHow much is a manicure?"
    ]


def test_sections_follow_the_head():
    parts = render_route_parts("hi", sections=[("Open scenarios", " booking: focus "), ("X", "y")])
    assert parts == ["Client's latest message:\nhi\n\nOpen scenarios:\nbooking: focus\n\nX:\ny"]


def test_a_long_text_is_one_part_while_it_fits():
    # 40 000 characters of Latin text, a story pasted whole, fit Jev's window
    text = "word " * 8000 + "THE END"
    parts = render_route_parts(text)
    assert len(parts) == 1
    assert parts[0].endswith("THE END")
    assert TRIMMED not in parts[0]


def test_a_text_over_the_window_is_split_not_cut():
    # 39 000 Cyrillic characters: about 35 000 tokens, over the window
    text = "\n\n".join(f"Абзац {i}. " + "слово " * 500 for i in range(13))
    parts = render_route_parts(text, sections=[("Open scenarios", "dna: focus")])
    assert len(parts) == 2
    assert all(estimate_tokens(p) <= 30_000 for p in parts)
    assert all(p.endswith("Open scenarios:\ndna: focus") for p in parts)
    assert parts[0].startswith(f"{CLIENT}, part 1 of 2:\nАбзац 0.")
    assert parts[1].startswith(f"{CLIENT}, part 2 of 2:\n")
    assert "Абзац 12." in parts[1]
    assert TRIMMED not in "".join(parts)


def test_own_words_are_the_message_when_the_rewrite_is_shorter():
    story = "I was born in Kyiv. " * 50 + "Then I opened a bakery."
    (state,) = render_route_parts("Who am I?", original=story)
    assert state.startswith("Client's latest message:\nI was born in Kyiv.")
    assert f"Then I opened a bakery.\n\n{RETOLD}:\n" in state
    assert state.endswith("Who am I?")


def test_a_rewrite_as_long_as_the_words_goes_alone():
    # «так» → «Так, мій чоловік був проти»: the rewrite has said more, the words add nothing
    parts = render_route_parts("Yes, my husband was against it", original="yes")
    assert parts == ["Client's latest message:\nYes, my husband was against it"]


def test_a_short_retelling_goes_with_every_part():
    story = "\n\n".join(f"P{i}. " + "word " * 300 for i in range(10))
    parts = render_route_parts("Who am I?", original=story, max_tokens=1000)
    assert len(parts) > 1
    assert all(p.startswith(f"{CLIENT}, part ") for p in parts)
    assert all(p.endswith(f"{RETOLD}:\nWho am I?") for p in parts)
    assert all(estimate_tokens(p) <= 1000 for p in parts)


def test_a_long_retelling_comes_in_parts_of_its_own():
    story = "word " * 3000 + "END"
    retold = "retold " * 1000
    parts = render_route_parts(retold, original=story, max_tokens=1000)
    words = [p for p in parts if p.startswith(CLIENT)]
    rest = [p for p in parts if p.startswith(RETOLD)]
    assert words and rest and len(words) + len(rest) == len(parts)
    assert words[-1].endswith("END")
    assert all(estimate_tokens(p) <= 1000 for p in parts)
    assert _words(" ".join(p.split(":\n", 1)[1] for p in rest)) == _words(retold)


def test_sections_get_half_a_part_when_the_message_is_split():
    parts = render_route_parts(
        "word " * 3000, sections=[("A", "x " * 2000), ("B", "never shown")], max_tokens=1000
    )
    assert len(parts) > 1
    assert all(estimate_tokens(p) <= 1000 for p in parts)
    assert all("A:\n" in p and "B:" not in p for p in parts)
    assert all(estimate_tokens(p.split("\n\nA:\n", 1)[1]) >= 450 for p in parts)
