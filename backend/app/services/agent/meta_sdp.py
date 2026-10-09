"""
Shaping an aiortc offer into the form Meta's validator accepts.

Only needed for **outbound** calls. Inbound, Meta sends the offer and we answer
it, and aiortc's answer is accepted as-is. Outbound, we make the offer, and
Meta compares it against the shape a browser produces — which aiortc's is not,
in several small ways that each cause a flat rejection with no detail.

Every rule below is one of those rejections, learned by the service that ran
this before:

* aiortc rewrites the `m=` port and `c=` address to the first gathered ICE
  candidate after `setLocalDescription`. Browsers leave the discard pair there
  — port 9, address 0.0.0.0 — and advertise trickle ICE instead.
* aiortc emits SHA-256, SHA-384 **and** SHA-512 fingerprints. Meta's examples
  carry SHA-256 alone, and the extra lines are refused.
* Browsers emit `a=extmap-allow-mixed` at session level; aiortc does not.
* The attribute order inside the media section differs. It should not matter
  and it does.

**Payload types and extmap IDs are never rewritten.** They have to stay aligned
with aiortc's own RTP sender state, and changing them in the string produces a
call that negotiates and then carries silence — which is far harder to diagnose
than a rejection.
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


def _lines(sdp: str) -> list[str]:
    return [line for line in sdp.replace("\r\n", "\n").split("\n") if line]


def _split(sdp: str) -> tuple[list[str], list[list[str]]]:
    """Session lines, then one list per media section."""
    session: list[str] = []
    sections: list[list[str]] = []
    for line in _lines(sdp):
        if line.startswith("m="):
            sections.append([line])
        elif sections:
            sections[-1].append(line)
        else:
            session.append(line)
    return session, sections


def prepare_outbound(sdp: str) -> str:
    """Normalise an aiortc offer into the browser-style SDP Meta expects.

    The real ICE candidates are kept, so this peer can still receive media.
    What changes is only how the session is *presented*.
    """
    session, sections = _split(sdp)
    if not sections:
        return sdp

    normalised_session: list[str] = []
    saw_extmap_mixed = False
    for line in session:
        if line.startswith("o="):
            bits = line.split()
            if len(bits) >= 6 and bits[-2] in {"IP4", "IP6"}:
                bits[-2:] = ["IP4", "127.0.0.1"]
                line = " ".join(bits)
        elif line.startswith("a=msid-semantic:"):
            # Cosmetic, but it matches the form in Meta's published example.
            line = f"a=msid-semantic: {line.split(':', 1)[1].strip()}"
        if line == "a=extmap-allow-mixed":
            saw_extmap_mixed = True
        normalised_session.append(line)

    if not saw_extmap_mixed:
        after_bundle = next(
            (i + 1 for i, line in enumerate(normalised_session)
             if line.startswith("a=group:BUNDLE")),
            len(normalised_session),
        )
        normalised_session.insert(after_bundle, "a=extmap-allow-mixed")

    normalised_sections: list[list[str]] = []
    for section in sections:
        if not section[0].startswith("m=audio "):
            normalised_sections.append(section)
            continue

        bits = section[0].split()
        if len(bits) >= 4:
            bits[1] = "9"
        buckets: dict[str, list[str]] = {
            k: [] for k in ("rtcp", "candidates", "ice", "fingerprints", "setup",
                            "mid", "extmaps", "direction", "msid", "mux",
                            "codecs", "ssrc", "other")
        }

        for line in section[1:]:
            if line.startswith("c="):
                continue                      # replaced with the discard address
            elif line.startswith("a=rtcp:"):
                buckets["rtcp"].append("a=rtcp:9 IN IP4 0.0.0.0")
            elif line.startswith("a=candidate:") or line == "a=end-of-candidates":
                buckets["candidates"].append(line)
            elif line.startswith(("a=ice-ufrag:", "a=ice-pwd:")):
                buckets["ice"].append(line)
            elif line.startswith("a=ice-options:"):
                continue                      # a canonical trickle line is added below
            elif line.startswith("a=fingerprint:sha-256 "):
                buckets["fingerprints"].append(line)
            elif line.startswith("a=fingerprint:"):
                continue                      # 384 and 512 are refused
            elif line.startswith("a=setup:"):
                buckets["setup"].append(line)
            elif line.startswith("a=mid:"):
                buckets["mid"].append(line)
            elif line.startswith("a=extmap:"):
                buckets["extmaps"].append(line)
            elif line in {"a=sendrecv", "a=sendonly", "a=recvonly", "a=inactive"}:
                buckets["direction"].append(line)
            elif line.startswith("a=msid:"):
                buckets["msid"].append(line)
            elif line == "a=rtcp-mux":
                buckets["mux"].append(line)
            elif line.startswith(("a=rtpmap:", "a=fmtp:", "a=rtcp-fb:")):
                buckets["codecs"].append(line)
            elif line.startswith(("a=ssrc:", "a=ssrc-group:")):
                buckets["ssrc"].append(line)
            else:
                buckets["other"].append(line)

        if not buckets["rtcp"]:
            buckets["rtcp"] = ["a=rtcp:9 IN IP4 0.0.0.0"]

        normalised_sections.append(
            [" ".join(bits), "c=IN IP4 0.0.0.0"]
            + buckets["rtcp"] + buckets["candidates"] + buckets["ice"]
            + ["a=ice-options:trickle"]
            + buckets["fingerprints"] + buckets["setup"] + buckets["mid"]
            + buckets["extmaps"] + buckets["direction"] + buckets["msid"]
            + buckets["mux"] + buckets["codecs"] + buckets["ssrc"] + buckets["other"]
        )

    out = list(normalised_session)
    for section in normalised_sections:
        out.extend(section)
    return "\r\n".join(out) + "\r\n"


def validate_outbound(sdp: str) -> dict[str, Any]:
    """Check an offer before sending it, and say what is wrong if anything is.

    Meta answers a malformed offer with "Parameter value is not valid" and
    nothing else, so the useful diagnosis has to happen here. Returns the
    individual checks rather than a bare pass/fail, because which one failed is
    the whole information.
    """
    lines = _lines(sdp)
    audio = next((line for line in lines if line.startswith("m=audio ")), "")
    fingerprints = [line for line in lines if line.startswith("a=fingerprint:")]

    checks = {
        "has_audio": bool(audio),
        "discard_port": audio.split()[1] == "9" if len(audio.split()) > 1 else False,
        "discard_address": "c=IN IP4 0.0.0.0" in lines,
        "rtcp_mux": "a=rtcp-mux" in lines,
        "trickle": "a=ice-options:trickle" in lines,
        "extmap_allow_mixed": "a=extmap-allow-mixed" in lines,
        "one_sha256_fingerprint": (
            len(fingerprints) > 0
            and all(f.startswith("a=fingerprint:sha-256 ") for f in fingerprints)
        ),
        "has_ice": any(line.startswith("a=ice-ufrag:") for line in lines),
        "has_candidates": any(line.startswith("a=candidate:") for line in lines),
        "opus_111": any(line.startswith("a=rtpmap:111 opus/") for line in lines),
    }
    checks["ok"] = all(checks.values())
    return checks


def describe(checks: dict[str, Any]) -> str:
    """The failing checks, for a log line that names the problem."""
    failed = [name for name, passed in checks.items() if name != "ok" and not passed]
    return ", ".join(failed) if failed else "all checks passed"
