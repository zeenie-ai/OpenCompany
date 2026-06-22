"""strip_ansi — terminal-output ANSI cleaning (core/ansi.py)."""

from __future__ import annotations

from core.ansi import strip_ansi


def test_strips_sgr_colour_codes():
    assert strip_ansi("\x1b[36mvite v7.3.3\x1b[39m") == "vite v7.3.3"


def test_strips_real_vite_build_line():
    # The exact shape from a `vite build` stdout (CSI colour + reset codes).
    raw = (
        "\x1b[36mvite v7.3.3 \x1b[32mbuilding client environment for "
        "production...\x1b[36m\x1b[39m transforming... \x1b[32m✓\x1b[39m "
        "8 modules transformed."
    )
    cleaned = strip_ansi(raw)
    assert "\x1b" not in cleaned
    assert "[36m" not in cleaned and "[39m" not in cleaned
    assert cleaned == (
        "vite v7.3.3 building client environment for production... "
        "transforming... ✓ 8 modules transformed."
    )


def test_plain_text_unchanged():
    assert strip_ansi("no escape codes here") == "no escape codes here"


def test_empty_and_falsy_passthrough():
    assert strip_ansi("") == ""
    assert strip_ansi(None) is None  # type: ignore[arg-type]


def test_strips_cursor_and_erase_sequences():
    # Not just colour: cursor-move (H) + erase-line (K) CSI sequences too.
    assert strip_ansi("\x1b[2K\x1b[1Gprogress") == "progress"
