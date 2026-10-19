"""Redis Stream ID parsing & numeric comparison (C7, T14)."""

from __future__ import annotations

import re

_ID_RE = re.compile(r"^(\d{1,20})-(\d{1,20})$")
ZERO_ID = "0-0"


def parse_stream_id(value: str) -> tuple[int, int]:
    """Parse ``ms-seq`` into a tuple of two non-negative ints. Raises ValueError."""
    if not isinstance(value, str):
        raise ValueError("stream id must be a string")
    m = _ID_RE.match(value.strip())
    if not m:
        raise ValueError(f"invalid stream id: {value!r}")
    return int(m.group(1)), int(m.group(2))


def is_valid_stream_id(value: str | None) -> bool:
    if value is None:
        return False
    try:
        parse_stream_id(value)
        return True
    except ValueError:
        return False


def compare_ids(a: str, b: str) -> int:
    pa, pb = parse_stream_id(a), parse_stream_id(b)
    return (pa > pb) - (pa < pb)


def max_id(*ids: str | None) -> str | None:
    best: str | None = None
    for i in ids:
        if i is None:
            continue
        if best is None or compare_ids(i, best) > 0:
            best = i
    return best
