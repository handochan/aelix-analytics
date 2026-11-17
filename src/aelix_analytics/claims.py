"""The claim check (experiment: Issue #196, branch exp/session-substrate — not adopted, not a contract):
when a record is written, the numbers its text states are looked for in the results the session's
tool calls returned — any tool's: the kit's, the host's, another extension's, an MCP server's — as the
call ledger held them (calls.hold). What it finds is one line of an auxiliary log
(`<state dir>/claims.jsonl`), and the results a value was found in are kept (calls.keep), so the
record's numbers can be looked for again after the held results are pruned.

It says where a value was seen, never that the record is right: a value seen in a result may be
another quantity with the same digits, and a value not seen may be derived from ones that were (a
difference, a share). The record itself is not changed, refused or delayed by more than SECONDS.

Standard library only (the aelix process loads it with core).
"""

from __future__ import annotations

import array
import bisect
import collections
import json
import re
import time

from . import calls, observed
from .writer import append_jsonl

SCHEMA = "analytics.claim/0"
FILE = "claims.jsonl"
# The kinds whose text states something about the data.
KINDS = ("finding", "decision", "assumption", "note", "resolve")
# The kit's tools whose results repeat what records say: a number seen there was stated before, not
# measured. (A bash call that is one kit command line is passed over too: the ledger marks it.)
NOT_SOURCES = frozenset({"analytics_status", "analytics_record", "analytics_init"})
GRADES = ("all_seen", "some_seen", "none_seen")

VALUES = 24             # the numbers of one record looked for
CALLS_LOOKED = 200      # the session's latest calls looked at
BYTES_LOOKED = 32 << 20
NUMBERS = 50_000        # the numbers of one result read
SEEN_PER_VALUE = 3
KEPT_BYTES = 8 << 20    # of results kept for one record
KEPT_RESULT_BYTES = 1 << 20  # a larger result stays among the held ones only
SECONDS = 1.5
READ_LIMIT = 50
SAME = 1e-9             # two numbers are the same when they differ by less than this share of them

# A number as text states it: digits with grouping commas and a decimal part, a percent sign after
# it. Not one inside an ASCII name or a version (e3, Q4, v0.32.1, sales_2024); one that follows a
# letter of another script is a number (a Korean word written against it, say).
NUMBER = re.compile(r"(?<![A-Za-z0-9_.])\d[\d,]*(?:\.\d+)?%?")
GROUPED = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?%?")
# What reads as a number and is not a quantity: a date or time, a run ID, a count of calendar units.
NOT_VALUES = re.compile(
    r"\d{8}-\d{6}-[a-z0-9-]+"                                  # a run ID
    r"|\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?)?"  # a date, a time after it
    r"|\d{1,2}:\d{2}(?::\d{2})?"                               # a time
    # a count of calendar units in Korean: a year, a month, a quarter, a week of the year
    r"|\d+(?=\s?(?:\ub144|\uc6d4|\ubd84\uae30|\uc8fc\ucc28))"
    r"|(?<![\d.,])(?:19|20)\d{2}(?![\d.%])")                  # a year


def _plain(token: str) -> tuple[str, int, bool] | None:
    """(the digits of a number token with no grouping, its decimals, whether it is a percentage)."""
    percent = token.endswith("%")
    token = token.rstrip("%").replace(",", "")
    if not token or token.count(".") > 1:
        return None
    whole, _, fraction = token.partition(".")
    if not whole.isdigit() or (fraction and not fraction.isdigit()):
        return None
    return f"{int(whole)}" + (f".{fraction}" if fraction else ""), len(fraction), percent


def _tokens(text: str):
    """The number tokens of a text: a properly grouped one whole, any other split at its commas."""
    for match in NUMBER.finditer(text):
        token = match.group(0).strip(",")
        if "," in token and not GROUPED.fullmatch(token):
            yield from (part for part in token.split(",") if part)
        else:
            yield token


def values_of(text: str) -> list[dict]:
    """The numbers a record's text states, in order, each once: {"text", "number", "decimals",
    "percent"}. A single digit with neither a decimal part nor a percent sign is not one (it is in
    every result), nor is a date, a time, a year or a run ID. At most VALUES."""
    found, seen = [], set()
    for token in _tokens(NOT_VALUES.sub(" ", text)):
        plain = _plain(token)
        if plain is None:
            continue
        number, decimals, percent = plain
        if len(number.replace(".", "")) < 2 and not decimals and not percent:
            continue
        key = (number, percent)
        if key in seen:
            continue
        seen.add(key)
        found.append({"text": token, "number": number, "decimals": decimals, "percent": percent})
        if len(found) == VALUES:
            break
    return found


def numbers_of(text: str, limit: int = NUMBERS) -> tuple[list[float], bool]:
    """(the numbers in a result's text, whether there were more than `limit`). `12,345` is read both
    as one number and as two: a table groups digits with the comma a CSV line separates cells with."""
    out, more = [], False
    for match in NUMBER.finditer(text):
        token = match.group(0).strip(",").rstrip("%")
        parts = [token.replace(",", "")] if "," not in token else [token.replace(",", ""), *token.split(",")]
        for part in parts:
            try:
                out.append(float(part))
            except ValueError:
                continue
        if len(out) >= limit:
            more = True
            break
    return out, more


class _Result:
    """One held result's numbers, sorted, so a value is looked for by where it would lie: as it is,
    as a longer number that rounds to it, or — a percentage — as the share that is that percentage."""

    def __init__(self, text: str):
        numbers, self.more = numbers_of(text)
        self.numbers = array.array("d", sorted(abs(n) for n in numbers))

    def _holds(self, low: float, high: float) -> bool:
        at = bisect.bisect_left(self.numbers, low)
        return at < len(self.numbers) and self.numbers[at] <= high

    def how(self, value: dict) -> str | None:
        """How the result holds the value: `exact` (the number as stated, or with more zeros),
        `rounded` (a longer number that rounds to it), `share` (a share that is that percentage)."""
        number = float(value["number"])
        tiny = max(number, 1.0) * SAME
        half = 0.5 * 10.0 ** -value["decimals"]
        if self._holds(number - tiny, number + tiny):
            return "exact"
        if self._holds(number - half - tiny, number + half - tiny):
            return "rounded"
        if value["percent"] and self._holds((number - half) / 100 - tiny, (number + half) / 100 - tiny):
            return "share"
        return None


# The numbers of the results looked at lately, by the result's hash: a session's records look at the
# same results again and again. Bounded by how many numbers it holds (8 bytes each).
_PARSED: collections.OrderedDict[str, _Result] = collections.OrderedDict()
PARSED_NUMBERS = 1_000_000


def _parse(digest: str, text: str) -> _Result:
    result = _PARSED[digest] = _Result(text)
    total = sum(len(kept.numbers) for kept in _PARSED.values())
    while total > PARSED_NUMBERS and len(_PARSED) > 1:
        total -= len(_PARSED.popitem(last=False)[1].numbers)
    return result


def result_text(raw: bytes) -> str:
    """The text of a held result (calls.digest_result's canonical bytes): its text blocks, in order."""
    try:
        blocks = json.loads(raw)
    except ValueError:
        return ""
    if not isinstance(blocks, list):
        return ""
    return "\n".join(block["text"] for block in blocks
                     if isinstance(block, dict) and isinstance(block.get("text"), str))


def check(project, text: str, *, session=None, deadline: float | None = None) -> dict | None:
    """Where the numbers `text` states were seen among the session's latest calls: {"values": [{"text",
    "seen": [{"call", "tool", "source", "how", "sha256"}]}], "grade", "looked": {"calls", "results",
    "bytes" (read now: a result read before in this process is not read again), "partial"}}, or None
    when the text states none. Newest calls first; a value is looked for
    until SEEN_PER_VALUE calls hold it, in a call's result text and in its details. `partial` says a
    bound stopped the look (the time, the bytes, a result's numbers) or a result was not held. The
    kit's tools that repeat records (NOT_SOURCES) are not looked at."""
    values = values_of(text)
    if not values:
        return None
    deadline = time.monotonic() + SECONDS if deadline is None else deadline
    lines = calls.read(project, session=session, limit=CALLS_LOOKED)
    seen: list[list[dict]] = [[] for _ in values]
    looked = {"calls": len(lines["calls"]), "results": 0, "bytes": 0, "partial": bool(lines["more"])}
    done: set[str] = set()
    stopped = False
    for line in reversed(lines["calls"]):
        if stopped or all(len(found) >= SEEN_PER_VALUE for found in seen):
            break
        if line.get("tool") in NOT_SOURCES or line.get("kit_command"):
            continue
        result = line.get("result") or {}
        for digest, part in ((result.get("sha256"), "result"),
                             ((result.get("details") or {}).get("sha256"), "details")):
            if not digest or digest in done:
                continue
            if time.monotonic() > deadline or looked["bytes"] > BYTES_LOOKED:
                looked["partial"] = stopped = True
                break
            numbers = _PARSED.get(digest)
            if numbers is None:
                raw = calls.held(project, digest)
                if raw is None:
                    # Not held (too large, holding off, pruned): the look is then not a whole one.
                    looked["partial"] = True
                    continue
                looked["bytes"] += len(raw)
                numbers = _parse(digest, result_text(raw) if part == "result" else raw.decode("utf-8", "replace"))
            else:
                _PARSED.move_to_end(digest)
            done.add(digest)
            looked["results"] += 1
            looked["partial"] = looked["partial"] or numbers.more
            for value, found in zip(values, seen, strict=True):
                if len(found) >= SEEN_PER_VALUE:
                    continue
                how = numbers.how(value)
                if how:
                    found.append({"call": line.get("call"), "tool": line.get("tool"),
                                  "source": line.get("source"), "how": how, "sha256": digest,
                                  **({"in": "details"} if part == "details" else {})})
    with_any = sum(1 for found in seen if found)
    grade = "all_seen" if with_any == len(values) else "some_seen" if with_any else "none_seen"
    return {"values": [{"text": value["text"], "seen": found} for value, found in zip(values, seen, strict=True)],
            "grade": grade, "looked": looked}


def keepable(raw: bytes) -> bool:
    """Whether a held result may be kept past the held ones' pruning: at most KEPT_RESULT_BYTES, and
    the kit's redaction rules (observed.redact, a deny list) find no credential in it. A kept result
    stays until its owner removes it and may be committed if the owner lets git see the directory;
    a held one is gone in two weeks."""
    return len(raw) <= KEPT_RESULT_BYTES and observed.redact(raw.decode("utf-8", "replace"))[1] == 0


def link(project, event: dict, *, session=None, at=None, deadline: float | None = None) -> dict | None:
    """The claim check for a record just written, as a line of the claims log; the results its values
    were seen in are kept (those `keepable`), the first of each value first, up to KEPT_BYTES. None
    for a kind that states nothing (KINDS) and for a text without numbers. `event` is the record's
    journal line."""
    if event.get("kind") not in KINDS:
        return None
    found = check(project, str(event.get("text") or ""), session=session, deadline=deadline)
    if found is None:
        return None
    kept, kept_bytes = {}, 0
    for rank in range(SEEN_PER_VALUE):
        for value in found["values"]:
            if rank >= len(value["seen"]):
                continue
            digest = value["seen"][rank]["sha256"]
            if digest in kept:
                continue
            raw = calls.held(project, digest)
            if raw is None or not keepable(raw) or kept_bytes + len(raw) > KEPT_BYTES:
                kept[digest] = False
                continue
            kept[digest] = calls.keep(project, digest)
            kept_bytes += len(raw) if kept[digest] else 0
    for value in found["values"]:
        for where in value["seen"]:
            where["kept"] = bool(kept.get(where["sha256"]))
    line = {"schema": SCHEMA, "event": event.get("id"), "kind": event.get("kind"), "session": session,
            "at": at, **found}
    append_jsonl(project.state_dir / FILE, line)
    return line


def said(line: dict) -> str:
    """The claim check's line as one clause of the record's answer."""
    values = line["values"]
    unseen = [value["text"] for value in values if not value["seen"]]
    count = len(values) - len(unseen)
    text = f"values seen in this session's tool results: {count} of {len(values)}"
    if unseen:
        text += f" (not seen: {', '.join(unseen[:6])}{', …' if len(unseen) > 6 else ''})"
    if line["looked"]["partial"]:
        text += "; not every result was looked at"
    return text


def read(project, *, event=None, limit: int = READ_LIMIT) -> dict:
    """The claims log's latest lines, oldest first: {"claims": [...], "more", "grades": counts}."""
    lines, cut = calls._tail(project.state_dir / FILE, calls.TAIL_BYTES)
    found = []
    for text in lines:
        try:
            line = json.loads(text)
        except ValueError:
            continue
        if not isinstance(line, dict) or line.get("schema") != SCHEMA:
            continue
        if event is not None and line.get("event") != event:
            continue
        found.append(line)
    kept = found[-max(1, int(limit)):]
    grades = {grade: sum(1 for line in kept if line.get("grade") == grade) for grade in GRADES}
    return {"claims": kept, "more": cut or len(found) > len(kept), "grades": grades}


def text_of(found: dict) -> str:
    out = []
    for line in found["claims"]:
        out.append(f"{line.get('event')} {line.get('kind')}: {line.get('grade')}")
        for value in line.get("values") or []:
            where = "; ".join(f"{s.get('tool')} {s.get('call')} ({s.get('how')}{'' if s.get('kept') else ', not kept'})"
                              for s in value.get("seen") or []) or "not seen"
            out.append(f"  {value.get('text')}: {where}")
    grades = found["grades"]
    out.append(f"{len(found['claims'])} record(s): " + ", ".join(f"{name} {grades[name]}" for name in GRADES)
               + ("; earlier ones not shown" if found["more"] else ""))
    return "\n".join(out)
