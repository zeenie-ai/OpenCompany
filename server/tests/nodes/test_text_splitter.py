"""Golden compatibility tests for the dependency-free text splitters."""

from nodes.document.text_chunker._splitter import split_text


def test_recursive_splitter_golden_paragraphs():
    text = "alpha beta gamma\n\ndelta epsilon zeta\neta theta iota"
    assert split_text(text, chunk_size=20, overlap=5) == [
        "alpha beta gamma",
        "delta epsilon zeta",
        "eta theta iota",
    ]


def test_recursive_splitter_golden_character_overlap():
    text = "abcdefghij" * 8
    assert split_text(text, chunk_size=25, overlap=5) == [
        "abcdefghijabcdefghijabcde",
        "abcdefghijabcdefghijabcde",
        "abcdefghijabcdefghijabcde",
        "abcdefghijabcdefghij",
    ]


def test_markdown_splitter_golden_sections():
    text = "# One\n\nparagraph alpha beta\n\n## Two\n\nparagraph gamma delta\n"
    assert split_text(text, chunk_size=24, overlap=5, markdown=True) == [
        "# One",
        "paragraph alpha beta",
        "## Two",
        "paragraph gamma delta",
    ]
