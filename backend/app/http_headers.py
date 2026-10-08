"""
Header values that survive the wire.

HTTP header values are latin-1. A company called "Hôpital Saint-Étienne" — or
any filename containing an em dash — raises UnicodeEncodeError on the way out,
which turns a working download into a 500 for exactly the customers whose names
are not plain ASCII.
"""
from __future__ import annotations

import re
from urllib.parse import quote


def content_disposition(filename: str, *, inline: bool = False) -> str:
    """A Content-Disposition value for a filename in any script.

    Two forms, as RFC 6266 prescribes: an ASCII fallback that every client
    understands, and the UTF-8 form that modern browsers prefer. Clients that
    know `filename*` use it; the rest get something readable rather than
    nothing.
    """
    disposition = "inline" if inline else "attachment"
    ascii_name = _ascii_fallback(filename)
    utf8_name = quote(filename, safe="")
    return f'{disposition}; filename="{ascii_name}"; filename*=UTF-8\'\'{utf8_name}'


def _ascii_fallback(filename: str) -> str:
    """Something latin-1 can carry and a file system will accept.

    Quotes and backslashes would end the quoted string early, and control
    characters would split the header, so both are removed rather than escaped.
    """
    folded = (
        filename.replace("—", "-")
        .replace("–", "-")
        .replace("’", "'")
        .replace("“", '"')
        .replace("”", '"')
    )
    ascii_only = folded.encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r'[\\"\x00-\x1f\x7f]', "", ascii_only).strip()
    return cleaned or "download"
