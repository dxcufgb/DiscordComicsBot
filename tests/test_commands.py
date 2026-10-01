from comicsbot.commands import split_message


def test_split_message_respects_limit():
    text = "\n".join(f"line {i:04d}" for i in range(1000))
    chunks = split_message(text, limit=100)
    assert all(len(c) <= 100 for c in chunks)
    assert "\n".join(chunks) == text


def test_split_message_long_line_and_empty():
    assert split_message("x" * 250, limit=100) == ["x" * 100, "x" * 100, "x" * 50]
    assert split_message("") == ["(nothing to show)"]
