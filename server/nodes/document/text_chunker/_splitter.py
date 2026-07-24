"""Dependency-free character splitters used by the text chunker node."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence


RECURSIVE_SEPARATORS = ("\n\n", "\n", " ", "")
MARKDOWN_SEPARATORS = (
    "\n#{1,6} ",
    "```\n",
    "\n\\*\\*\\*+\n",
    "\n---+\n",
    "\n___+\n",
    "\n\n",
    "\n",
    " ",
    "",
)


def _split_with_separator(text: str, separator: str) -> list[str]:
    """Split while retaining separators at the start of the next fragment."""
    if not separator:
        return list(text)
    pieces = re.split(f"({re.escape(separator)})", text)
    splits = [pieces[i] + pieces[i + 1] for i in range(1, len(pieces), 2)]
    if len(pieces) % 2 == 0:
        splits += pieces[-1:]
    splits = [pieces[0], *splits]
    return [item for item in splits if item]


def _join(parts: Sequence[str], separator: str) -> str | None:
    text = separator.join(parts).strip()
    return text or None


def _merge(
    splits: Iterable[str],
    *,
    chunk_size: int,
    overlap: int,
    separator: str,
) -> list[str]:
    separator_len = len(separator)
    chunks: list[str] = []
    current: list[str] = []
    total = 0
    for split in splits:
        split_len = len(split)
        projected = total + split_len + (separator_len if current else 0)
        if projected > chunk_size and current:
            chunk = _join(current, separator)
            if chunk is not None:
                chunks.append(chunk)
            while total > overlap or (
                total
                + split_len
                + (separator_len if current else 0)
                > chunk_size
                and total > 0
            ):
                total -= len(current[0]) + (
                    separator_len if len(current) > 1 else 0
                )
                current = current[1:]
        current.append(split)
        total += split_len + (separator_len if len(current) > 1 else 0)
    chunk = _join(current, separator)
    if chunk is not None:
        chunks.append(chunk)
    return chunks


def _recursive_split(
    text: str,
    separators: Sequence[str],
    *,
    chunk_size: int,
    overlap: int,
) -> list[str]:
    separator = separators[-1]
    remaining: Sequence[str] = ()
    for index, candidate in enumerate(separators):
        if not candidate:
            separator = candidate
            break
        if re.search(re.escape(candidate), text):
            separator = candidate
            remaining = separators[index + 1 :]
            break

    splits = _split_with_separator(text, separator)
    final: list[str] = []
    small: list[str] = []
    for split in splits:
        if len(split) < chunk_size:
            small.append(split)
            continue
        if small:
            final.extend(
                _merge(
                    small,
                    chunk_size=chunk_size,
                    overlap=overlap,
                    separator="",
                )
            )
            small = []
        if remaining:
            final.extend(
                _recursive_split(
                    split,
                    remaining,
                    chunk_size=chunk_size,
                    overlap=overlap,
                )
            )
        else:
            final.append(split)
    if small:
        final.extend(
            _merge(
                small,
                chunk_size=chunk_size,
                overlap=overlap,
                separator="",
            )
        )
    return final


def split_text(
    text: str,
    *,
    chunk_size: int,
    overlap: int,
    markdown: bool = False,
) -> list[str]:
    """Split text with the historical recursive-character semantics."""
    if chunk_size <= 0:
        raise ValueError(f"chunk_size must be > 0, got {chunk_size}")
    if overlap < 0:
        raise ValueError(f"chunk_overlap must be >= 0, got {overlap}")
    if overlap > chunk_size:
        raise ValueError(
            f"Got a larger chunk overlap ({overlap}) than chunk size "
            f"({chunk_size}), should be smaller."
        )
    separators = MARKDOWN_SEPARATORS if markdown else RECURSIVE_SEPARATORS
    return _recursive_split(
        text,
        separators,
        chunk_size=chunk_size,
        overlap=overlap,
    )
