from textkit import slugify, truncate


def test_slugify_joins_words():
    assert slugify("Hello, World!") == "hello-world"


def test_truncate_marks_cut():
    assert truncate("abcdef", 4) == "abc…"
    assert truncate("abc", 4) == "abc"
