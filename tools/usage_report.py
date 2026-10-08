"""Measure what the extension sends to a model, and whether a fact it leaves out can be read back.

    python tools/usage_report.py --output DIR [--tokenizer ID=PATH] [--keep-fixtures]
                                 [--only NAME,...]

Run it where aelix 0.1.0b2 is installed and nothing else, as CONTRIBUTING.md and the CI do:

    uv run --isolated --no-project --python 3.12 --with aelix==0.1.0b2 \
      python tools/usage_report.py --output DIR

Fixtures are deterministic synthetic projects, written with the kit's own functions under a fixed
clock (the records the harness writes itself), in a temporary directory; `--keep-fixtures` keeps
them under DIR/fixtures. `--only` takes fixture ids or families (F1, F3-en, ...); the static
surfaces (T) are always measured.

Surfaces: this checkout's extension is loaded the way `aelix -e` loads it and asked for every text a
model gets from it: the context block its hook appends to the system prompt (S1), the tool lines and
guidelines aelix renders from it (S2), its tool definitions as aelix's openai-completions adapter
converts them (S3), and the replies of its tools, its /analytics record command and its errors (S4-S10).
The kit command behind analytics_run, analytics_query and analytics_import is not started: `run_kit` returns a result
made with the kit's own functions (runner._tail for the log tails, data.cell for query cells,
workspace.build for the run page — a reduced build, without the analysis libraries).
AELIX_ANALYTICS_TRUST_FILE and AELIX_ANALYTICS_UV point into the temporary directory, so no trust list or uv
outside it is touched.

Sizes are exact: characters, UTF-8 bytes, lines and sha256 of the final string, and the same after
the project root is replaced by <ROOT>. Tokens are counted only by a registered tokenizer whose file
has the registered sha256 (`--tokenizer`); otherwise every token value is `not_measured` with the
reason. Bytes and characters are never turned into tokens.

Retrieval: every fixture holds facts at known places. A fact is R0 when the first text shows it, R1
when one pointer of that text, followed with aelix's own read tool or the tool it names, shows it,
and R2 when a pointer of that result does. A pointer whose claim is false fails the fact. Invariants
say what every omission of a surface has to carry.

Exit status: 0 when the report was written (an exceeded target or a lost fact is data, not a
failure), 1 when a surface could not be measured or a given token counter failed (`failed`: an
aelix API changed, a tokenizer file did not load, or counting a text raised; the report is still
written), 2 on a usage error. A tokenizer file that is not the registered one, or a missing
tokenizer library, counts nothing (`not_measured`) and is not a failure. The pass/fail judgements
are tests/test_usage_report.py's.

OUTPUT has to be new, empty or a previous report's directory (one with usage-report.json): the
command replaces its texts/ (and fixtures/ with --keep-fixtures), and refuses any other directory
that is not empty before it deletes anything.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import contextvars
import hashlib
import html
import importlib.metadata
import importlib.util
import json
import os
import platform
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.parse
import urllib.request
import uuid
import zlib
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "src"
EXT = ROOT / "extension" / "aelix_analytics.py"

SCHEMA = "aelix-analytics.usage-report/1"
# The fixtures' own version: a change to what they contain changes this.
GENERATOR = "usage-report-fixtures/1"
# The clock of the records the harness writes itself. Its day is before this tool existed, so no
# real timestamp the measured code adds while it is measured can fall on it.
CLOCK_START = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)
CLOCK_DAY = "2026-09-01"

# The measured extension's names by role: the one place to change when they are renamed (#9).
TOOL_NAMES = {"status": "analytics_status", "query": "analytics_query", "import": "analytics_import",
              "run": "analytics_run", "note": "analytics_record", "init": "analytics_init"}
COMMAND_NAME = "analytics"

# Counters this report can count tokens with, by id. A file is used only when it has this sha256.
COUNTERS = {
    "qwen3.8-27b": {
        "model": "Qwen/Qwen3.8-27B",
        "library": "tokenizers",
        "source": "hf:Qwen/Qwen3.8-27B@1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0/tokenizer.json",
        "sha256": "0997f410c57a1f4e53b09e4be8f4a172d90edd9564368fb0847030937229b9f3",
        "scope": "text",
        "add_special_tokens": False,
    },
}
# The frontier model of docs/BETA-QUALITY-GATES.md section 2, pinned by Issue #40
# (tools/model_eval_data/models/gpt-5.6-sol.json: GPT-5.6 Sol through aelix's openai-codex provider).
# OpenAI publishes no tokenizer for it, so no text here is counted for it: its tokens come from the
# backend's usage in tools/model_eval.py runs.
FRONTIER = "frontier"
FRONTIER_MODEL = "openai-codex:gpt-5.6-sol"

# docs/DESIGN-CONTINUITY-AND-REUSE.md:229-246. Token usage targets are measurement targets: they are
# compared and reported, never used to fail, refuse or cut (docs/BETA-QUALITY-GATES.md:36).
TARGETS = [
    {"id": "automatic_start_summary", "surface": "S1", "tokens": {"general": 512, "reduced": 256},
     "utf8_bytes": 1536},
    {"id": "always_on_input", "surface": "S1+S2+S3", "tokens": {"general": 2048},
     "note": "min(2,048 tokens, 15% of the context window): 15% of each pinned window (Qwen 131,072, "
             "gpt-5.6-sol 1,050,000 tokens; Issue #40) is larger, so 2,048 tokens is compared; the "
             "document gives no bytes target"},
    {"id": "explicit_detail_page", "surface": "follow-up read",
     "tokens": {"general": 2048, "reduced": 1536}},
    {"id": "general_query", "surface": "S4", "utf8_bytes": 3072,
     "note": "provisional: analytics_status stands for the general query until the search and relation "
             "surfaces exist (#15)"},
    {"id": "search_or_relation_page", "surface": None, "status": "not_applicable",
     "tokens": {"general": 1024, "reduced": 768},
     "note": "no search or relation surface yet (#15)"},
    {"id": "search_results_and_relation_depth", "surface": None, "status": "not_applicable",
     "note": "10 / 6 results, 1 hop: no such surface yet (#15)"},
]

NOT_RUN = [
    {"what": "real model task cost: cached and uncached input, output tokens, latency, 12 tasks x "
             "model x 3", "reason": "needs real model runs", "where": "#24 (B5/B6), #40"},
    {"what": "frontier model tokens", "reason": "no offline counter: the backend's usage counts them "
                                                 "in tools/model_eval.py runs, which need the owner's "
                                                 "aelix login (openai-codex)",
     "where": "#40, #24"},
    {"what": "image tokens", "reason": "vision_tokens: no offline counter", "where": "#24"},
    {"what": "chat-template framing tokens (the fixed text the pinned Qwen template adds around "
             "the tools)", "reason": "out of scope: only the payload's own strings are counted",
     "where": "#24"},
    {"what": "the same Qwen token counts on Linux and Windows",
     "reason": "one platform per invocation; compare the reports of the CI legs", "where": "#24"},
    {"what": "the host's own system prompt beyond build_system_prompt, the conversation and its "
             "compaction", "reason": "the host's, not the extension's (its base prompt is "
                                     "recorded as the host share)", "where": "-"},
    {"what": "ToolResult.details", "reason": "never sent to the model (aelix 0.1.0b2 "
                                             "aelix_agent_core/loop.py)", "where": "-"},
]

F3_CAVEAT = ("F3's run.json is synthetic: the harness writes all 503 inputs into the record. A "
             "real run that reads 503 files through one glob records 502 of them: input "
             "detection keeps at most 500 files of a glob read at run time and 200 of a glob in "
             "the code, and nothing marks the rest (N1, #46).")


# --- sizes, encodings, normalization -----------------------------------------------------------

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def size_of(text: str) -> dict:
    """The size of exactly this string: code points, UTF-8 bytes, `\\n` count + 1 (0 when empty)."""
    data = text.encode("utf-8")
    return {"chars": len(text), "utf8_bytes": len(data),
            "lines": text.count("\n") + 1 if text else 0, "sha256": sha256_bytes(data)}


def forms(value: str) -> set[str]:
    """How a string can appear in what a reader gets: as is, inside JSON, inside HTML or a link."""
    return {value, json.dumps(value, ensure_ascii=False)[1:-1], html.escape(value, quote=True),
            urllib.parse.quote(value, safe="/#:?=&%.-_~")}


def present(text: str, needles) -> bool:
    return all(any(form in text for form in forms(needle)) for needle in needles)


def collapse(text: str) -> str:
    return " ".join(text.split())


def path_forms(path: Path) -> list[str]:
    raw = {str(path), path.as_posix()}
    raw |= {json.dumps(item, ensure_ascii=False)[1:-1] for item in list(raw)}
    return sorted((item for item in raw if item), key=len, reverse=True)


TIMESTAMP = re.compile(r"\b(\d{4}-\d{2}-\d{2})[ T]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?"
                       r"(?:[+-]\d{2}:\d{2}|Z)?")
# The read analytics_status names (Issue #12): its source_revision is made of the record files'
# sizes and mtimes, so a fixture written again has another one, as it has another write time.
REVISION = re.compile(r"\bsource_revision [0-9a-f]{16}\b")


def normalize(text: str, root: Path | None, base: Path | None) -> tuple[str, dict]:
    """The text with the project root as <ROOT> and the fixture directory as <BASE> (each in its
    native, POSIX and JSON-escaped spellings), timestamps the measured code added while it was
    measured as <TIME> (those of the fixed clock stay), and the source_revision of a read, which
    follows when the files were written, as <REVISION>."""
    counts = {"root": 0, "base": 0, "masked_timestamps": 0, "masked_revisions": 0}
    for key, label, path in (("root", "<ROOT>", root), ("base", "<BASE>", base)):
        if path is None:
            continue
        for form in path_forms(path):
            found = text.count(form)
            if found:
                counts[key] += found
                text = text.replace(form, label)

    def mask(match):
        if match.group(1) == CLOCK_DAY:
            return match.group(0)
        counts["masked_timestamps"] += 1
        return "<TIME>"

    text, counts["masked_revisions"] = REVISION.subn("source_revision <REVISION>", text)
    return TIMESTAMP.sub(mask, text), counts


# --- token counters -----------------------------------------------------------------------------

def measured(value: int) -> dict:
    return {"status": "measured", "value": value}


def not_measured(reason: str) -> dict:
    return {"status": "not_measured", "reason": reason}


def failed(error: str) -> dict:
    return {"status": "failed", "error": error}


class UsageError(Exception):
    pass


class TokenCounter:
    """One counter: its id, what identifies it, and `encode` (text -> token count) when this run can
    count with it. Without `encode`, every count is `state` (not_measured with its reason, or
    failed). A text it fails to count is `failed`, and so is the counter from then on (its state
    keeps the first error): the command exits 1."""

    def __init__(self, counter_id: str, info: dict, state: dict | None = None, encode=None):
        self.id = counter_id
        self.info = info
        self.encode = encode
        self.state = state or ({"status": "in_use"} if encode else
                               not_measured("no_tokenizer_given"))
        self.failed_texts = 0

    def count(self, text: str) -> dict:
        if self.encode is None:
            return dict(self.state)
        try:
            return measured(int(self.encode(text)))
        except Exception as exc:  # noqa: BLE001 - a counter that fails is recorded, not raised
            value = failed(f"{type(exc).__name__}: {exc}")
            if self.state.get("status") != "failed":
                self.state = dict(value)
            self.failed_texts += 1
            return value

    def describe(self) -> dict:
        out = {"id": self.id, **self.info, "state": dict(self.state)}
        if self.failed_texts:
            out["failed_texts"] = self.failed_texts
        return out


def registered_counter(spec: str) -> TokenCounter:
    """`--tokenizer ID=PATH`: a registered counter, used only when the file is the registered one
    and its library can be imported (only then is `tokenizers` imported)."""
    counter_id, sep, raw_path = spec.partition("=")
    if not sep or not raw_path:
        raise UsageError(f"--tokenizer takes ID=PATH, not {spec!r}")
    if counter_id not in COUNTERS:
        raise UsageError(f"unknown counter {counter_id!r}; registered: {', '.join(COUNTERS)}")
    path = Path(raw_path).expanduser()
    if not path.is_file():
        raise UsageError(f"tokenizer file not found: {path}")
    info = dict(COUNTERS[counter_id])
    found = sha256_bytes(path.read_bytes())
    info.update(file=str(path), file_sha256=found)
    if found != info["sha256"]:
        return TokenCounter(counter_id, info, not_measured("tokenizer_file_mismatch"))
    try:
        from tokenizers import Tokenizer

        info["library_version"] = importlib.metadata.version("tokenizers")
    except ImportError:
        return TokenCounter(counter_id, info, not_measured("tokenizer_library_unavailable"))
    try:
        tokenizer = Tokenizer.from_file(str(path))
    except Exception as exc:  # noqa: BLE001 - a file that does not load is a failed counter
        return TokenCounter(counter_id, info, failed(f"{type(exc).__name__}: {exc}"))
    return TokenCounter(counter_id, info,
                        encode=lambda text: len(tokenizer.encode(text,
                                                                 add_special_tokens=False).ids))


class Counters:
    """Every registered counter and the frontier model, each with a value for every text."""

    def __init__(self, given: list[TokenCounter]):
        by_id = {c.id: c for c in given}
        self.all = [by_id.pop(cid, None) or TokenCounter(cid, dict(info))
                    for cid, info in COUNTERS.items()]
        self.all += list(by_id.values())  # counters a caller brings (the tests' own)

    def count(self, text: str) -> dict:
        tokens = {c.id: c.count(text) for c in self.all}
        tokens[FRONTIER] = not_measured("no_offline_counter")
        return tokens

    def images(self) -> dict:
        return {**{c.id: not_measured("vision_tokens") for c in self.all},
                FRONTIER: not_measured("vision_tokens")}

    def describe(self) -> list[dict]:
        return [c.describe() for c in self.all] + [
            {"id": FRONTIER, "model": FRONTIER_MODEL,
             "state": not_measured("no_offline_counter"),
             "note": "pinned in tools/model_eval_data/models/gpt-5.6-sol.json (Issue #40); OpenAI "
                     "publishes no tokenizer for it, so only the backend's usage counts it "
                     "(tools/model_eval.py)"}]

    def failed(self) -> bool:
        return any(c.state.get("status") == "failed" for c in self.all)


def token_sum(values: list[dict]) -> dict:
    """The sum of several token values of one counter: a number only when every part is one."""
    if values and all(v.get("status") == "measured" for v in values):
        return measured(sum(v["value"] for v in values))
    other = next((v for v in values if v.get("status") != "measured"), None)
    return dict(other) if other else not_measured("nothing_to_count")


# --- isolation and the clock -------------------------------------------------------------------

# The variables the measured code reads, and their names before kit 0.3.0: an old one left set in the
# shell would make the extension refuse to load (core.RENAMED_ENV), so it is unset while measuring.
ISOLATED_KEYS = ("AELIX_ANALYTICS_TRUST_FILE", "AELIX_ANALYTICS_UV", "AELIX_ANALYTICS_PROJECT",
                 "AELIX_ANALYTICS_RUNTIME_PATH", "AELIX_ANALYTICS_ACTOR", "AELIX_ANALYTICS_SESSION",
                 "AELIX_ANALYTICS_STYLE", "AELIX_LAB_TRUST_FILE", "AELIX_LAB_UV", "AELIX_LAB_PROJECT",
                 "AELIX_LAB_KIT", "LAB_PROJECT", "LAB_ACTOR", "LAB_SESSION", "LAB_STYLE")


@contextlib.contextmanager
def isolated_environment(scratch: Path):
    """A trust list and a missing uv inside `scratch` (no trust list outside it is touched, and no
    report build is started); the variables and the working directory come back afterwards."""
    saved = {key: os.environ.get(key) for key in ISOLATED_KEYS}
    cwd = os.getcwd()
    try:
        for key in ISOLATED_KEYS:
            os.environ.pop(key, None)
        os.environ["AELIX_ANALYTICS_TRUST_FILE"] = str(scratch / "trust" / "lab-projects.json")
        os.environ["AELIX_ANALYTICS_UV"] = str(scratch / "no-uv" / "uv")
        yield
    finally:
        os.chdir(cwd)
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class FixedClock:
    """One second later at every call, from CLOCK_START: the same build writes the same bytes."""

    def __init__(self) -> None:
        self.ticks = 0

    def __call__(self) -> datetime:
        self.ticks += 1
        return CLOCK_START + timedelta(seconds=self.ticks)


@contextlib.contextmanager
def fixed_clock(*modules):
    clock = FixedClock()
    saved = [(module, module.now) for module in modules]
    for module in modules:
        module.now = clock
    try:
        yield clock
    finally:
        for module, now in saved:
            module.now = now


# --- the host: aelix 0.1.0b2 as the extension meets it ------------------------------------------

def load_extension():
    """This checkout's extension/aelix_analytics.py, loaded as a file the way `aelix -e` loads it."""
    spec = importlib.util.spec_from_file_location(f"aelix_analytics_usage_{uuid.uuid4().hex[:8]}", EXT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_kit() -> SimpleNamespace:
    """The checkout's kit, for the functions the kit command would have run (stdlib at import)."""
    if str(KIT) not in sys.path:
        sys.path.insert(0, str(KIT))
    from aelix_analytics import core, data, runner, workspace

    return SimpleNamespace(core=core, data=data, runner=runner, workspace=workspace)


@dataclass
class Reply:
    text: str
    images: list
    is_error: bool
    details: object
    elapsed_ms: float
    clipped: bool = False   # the extension's 14,000-character clip cut the text it was given


@dataclass
class Followed:
    target: str
    text: str
    calls: int
    raws: list[str]
    blocked: bool = False
    error: str | None = None

    @property
    def utf8_bytes(self) -> int:
        return sum(len(raw.encode("utf-8")) for raw in self.raws)


READ_MORE_BYTES = re.compile(r"\n\n\[Showing lines (\d+)-(\d+) of (\d+)(?: \([^)]*\))?\. "
                             r"Use offset=(\d+) to continue\.\]$")
READ_MORE_LINES = re.compile(r"\n\n\[(\d+) more lines in file\. Use offset=(\d+) to continue\.\]$")
READ_LONG_LINE = re.compile(r"^\[Line \d+ is [^,]+, exceeds \S+ limit\. Use bash: ")


async def _drain(tasks) -> None:
    """Wait for cancelled tasks to end; what each of them raises is its own, not the caller's."""
    await asyncio.gather(*tasks, return_exceptions=True)


class Host:
    """The extension in aelix 0.1.0b2: its hook, tools and /analytics command, aelix's own read tool for
    following pointers, and aelix's prompt and tool conversion for S2 and S3.

    Every call runs on one event loop, as aelix runs a session on one, from `Host()` to `close()` (or
    the end of a `with` block). A loop per call, as asyncio.run made, meant a socket pair per call —
    about 2,400 in a measurement — and on Windows CPython makes each from a loopback connection whose
    accept() can wait for ever: the CI's 3.11 leg hung there twice (Issue #38)."""

    def __init__(self) -> None:
        from aelix_agent_core.harness.hooks import BeforeAgentStartHookEvent
        from aelix_ai.messages import ImageContent
        from aelix_ai.tools import ToolExecutionContext
        from aelix_coding_agent.tools.read import create_read_tool

        self._event = BeforeAgentStartHookEvent
        self._context = ToolExecutionContext
        self._image = ImageContent
        self._create_read_tool = create_read_tool
        self.module = load_extension()
        self.core = self.module.core
        self.tools = {tool.name: tool for tool in self.module.TOOLS}
        self.real_run_kit = self.module.run_kit
        self.kit_results: list[tuple[bool, dict]] = []
        self.kit_commands: list[list[str]] = []
        self.module.run_kit = self._run_kit
        self.module.sync_environment = self._sync_environment
        # Whether a reply was cut by length is read off the clip itself (what it was given and what
        # it returned), not off the words it adds.
        self.clips: list[bool] = []
        real_clip = self.module._clip

        def clip(text, *args, **kwargs):
            out = real_clip(text, *args, **kwargs)
            self.clips.append(out != text)
            return out

        self.module._clip = clip
        self.cwd = ROOT  # where the model works: the active project, as aelix runs inside it
        self._n = 0
        # The loop is made here, not in the first call, so that no timed call includes making it.
        self._runner = asyncio.Runner()
        self._loop = self._runner.get_loop()

    def __enter__(self) -> Host:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        """End the loop: what asyncio.run did after every call (cancel what is left, finish async
        generators, join the default executor's threads), once."""
        self._runner.close()

    def _run(self, coro):
        """One call to its end, in a copy of the caller's context as asyncio.run ran it. A task the
        call leaves running is cancelled and awaited before the call returns or raises, as
        asyncio.run did, so that it cannot run on into the next call; the call then fails with a
        RuntimeError whose cause is the call's own error, if it raised one."""
        error = None
        try:
            return self._runner.run(coro, context=contextvars.copy_context())
        except BaseException as exc:
            error = exc
            raise
        finally:
            self._end_tasks_left(error)

    def _end_tasks_left(self, error: BaseException | None) -> None:
        """Cancel and await what a call left on the loop, and fail the call when there was any. An
        interrupt (KeyboardInterrupt, SystemExit) goes on as it is, after the tasks have ended."""
        left = asyncio.all_tasks(self._loop)
        if not left:
            return
        names = sorted(map(repr, left))
        for task in left:
            task.cancel()
        self._runner.run(_drain(left))
        if error is None or isinstance(error, Exception):
            raise RuntimeError(f"the call left {len(left)} task(s) running on the host's loop, "
                               f"cancelled them: {names}") from error

    async def _run_kit(self, project, args, *, timeout, actor="agent"):
        """What the kit command would print, prepared by the fixture: through JSON, as the
        extension reads it from the command's last stdout line."""
        self.kit_commands.append([str(a) for a in args])
        if not self.kit_results:
            return False, {"ok": False, "error": {"code": "harness_no_result",
                                                  "message": "the fixture prepared no kit result"}}
        ok, value = self.kit_results.pop(0)
        return ok, json.loads(json.dumps(value, ensure_ascii=False, default=str))

    async def _sync_environment(self, project):
        return True, "network"

    def use(self, root: Path | None) -> None:
        self.module.STATE["project"] = None
        self.cwd = root or ROOT
        if root is None:
            os.environ.pop("AELIX_ANALYTICS_PROJECT", None)
        else:
            os.environ["AELIX_ANALYTICS_PROJECT"] = str(root)

    def _ctx(self, images: bool):
        self._n += 1
        return self._context(tool_call_id=f"usage-{self._n}",
                             model=SimpleNamespace(input=["text", "image"] if images else ["text"]))

    def hook(self, base_prompt: str) -> str | None:
        event = self._event(prompt="Continue the analysis.", system_prompt=base_prompt)
        out = self._run(self.module.on_before_agent_start(event, SimpleNamespace()))
        return None if out is None else out.system_prompt

    def tool(self, role: str, args: dict, *, images: bool = False) -> Reply:
        tool = self.tools[TOOL_NAMES[role]]
        self.clips.clear()
        started = time.perf_counter()
        result = self._run(tool.execute(dict(args), self._ctx(images)))
        elapsed = round((time.perf_counter() - started) * 1000, 1)
        text = "\n".join(c.text for c in result.content if hasattr(c, "text"))
        pictures = [c for c in result.content if isinstance(c, self._image)]
        return Reply(text, pictures, bool(result.is_error), result.details, elapsed,
                     clipped=any(self.clips))

    def command(self, text: str) -> str:
        return self._run(self.module.analytics_command(text))

    def read(self, path, offset: int | None = None, limit: int | None = None) -> tuple[str, bool]:
        reader = self._create_read_tool(str(self.cwd))
        args: dict = {"path": str(path)}
        if offset is not None:
            args["offset"] = offset
        if limit is not None:
            args["limit"] = limit
        result = self._run(reader.execute(args, self._ctx(False)))
        text = "\n".join(c.text for c in result.content if hasattr(c, "text"))
        return text, bool(result.is_error)

    def read_paged(self, path, offset: int | None = None, limit: int | None = None) -> Followed:
        """Read a range as a model would: following the read tool's own `Use offset=N to continue`
        until the range is read, each call counted."""
        target = f"read {path}" + (f" offset {offset}" if offset else "") + \
            (f" limit {limit}" if limit is not None else "")
        bodies, raws, cursor, left = [], [], offset, limit
        while len(raws) < 200:
            text, is_error = self.read(path, cursor, left)
            raws.append(text)
            if is_error:
                return Followed(target, "\n".join(bodies), len(raws), raws, error=text)
            if READ_LONG_LINE.match(text):
                return Followed(target, "\n".join(bodies), len(raws), raws, blocked=True)
            more = READ_MORE_BYTES.search(text)
            if more:
                bodies.append(text[:more.start()])
                kept = int(more.group(2)) - int(more.group(1)) + 1
                cursor = int(more.group(4))
                if left is not None:
                    left -= kept
                    if left <= 0:
                        break
                continue
            rest = READ_MORE_LINES.search(text)
            bodies.append(text[:rest.start()] if rest else text)
            break
        return Followed(target, "\n".join(bodies), len(raws), raws)

    def follow(self, pointer) -> Followed:
        if pointer.kind == "tool":
            reply = self.tool(pointer.tool, {})
            return Followed(f"call {TOOL_NAMES[pointer.tool]}", reply.text, 1, [reply.text],
                            error=reply.text if reply.is_error else None)
        return self.read_paged(pointer.path, pointer.offset, pointer.limit)


# --- fixture specifications ---------------------------------------------------------------------

@dataclass(frozen=True)
class FactSpec:
    id: str
    first: str          # the surface whose text a model has first
    scope: str          # which list or part of the text the fact belongs to
    required: bool = True


@dataclass(frozen=True)
class Spec:
    id: str
    family: str
    lang: str
    required: bool
    shape: str
    facts: tuple[FactSpec, ...] = ()
    invariants: tuple[str, ...] = ()   # surfaces whose invariants are judged (pass/fail)


def _specs() -> list[Spec]:
    out = []
    for lang in ("ascii", "en", "ko"):
        out.append(Spec(f"F1-{lang}", "F1", lang, True,
                        "a pinned section of exactly 20,000 characters (heading line included, as "
                        "the kit counts it), then two sections outside the budget",
                        (FactSpec("pinned-end", "S1", "context"),
                         FactSpec("outside-budget", "S1", "context")), ("S1", "S4")))
    for lang in ("en", "ko"):
        out.append(Spec(f"F1b-{lang}", "F1b", lang, True,
                        "20,000 characters above the first `## ` heading",
                        (FactSpec("preamble-end", "S1", "preamble"),), ("S1", "S4")))
    sections = tuple(FactSpec(f"section-{i:03d}", "S1", "context") for i in (0, 137, 199))
    for lang in ("ascii", "en", "ko"):
        out.append(Spec(f"F2-{lang}", "F2", lang, True,
                        "200 sections outside the budget, short headings (ascii: bodies of 4,100 "
                        "x, else about 1,100 characters)", sections, ("S1", "S4")))
    for lang in ("en", "ko"):
        out.append(Spec(f"F2t-{lang}", "F2t", lang, True,
                        "F2 with descriptive headings (en 77, ko 46 characters), a run, a dataset, "
                        "an open question, a key finding and a recent note",
                        (*sections,
                         FactSpec("latest-run", "S1", "runs"),
                         FactSpec("dataset", "S1", "datasets"),
                         FactSpec("recent-note", "S1", "recent"),
                         FactSpec("open-question", "S1", "journal_open"),
                         FactSpec("key-finding", "S1", "journal_key")), ("S1", "S4")))
    for variant, shape in (("crlf", "CRLF line ends"), ("cr", "a lone CR in section 5"),
                           ("u2028", "U+2028 in section 5"), ("ff", "a form feed in section 5"),
                           ("lead1", "one blank line before the title"),
                           ("lead3", "three blank lines before the title")):
        out.append(Spec(f"F2s-{variant}", "F2s", "en", True, f"F2 (en) with {shape}",
                        (FactSpec("section-137", "S1", "context"),), ("S1",)))
    inputs = tuple(FactSpec(f"input-{i:03d}", "S5", "inputs") for i in (0, 300, 502))
    for lang in ("en", "ko"):
        out.append(Spec(f"F3-{lang}", "F3", lang, True,
                        "one ok run: 503 inputs (3 outside the project, 500 inside; files exist, "
                        "sha256 match), 3 outputs (one of 20 columns), 15 charts, about 28 KB of "
                        "stdout, 61 lines of stderr",
                        (*inputs, FactSpec("chart-14", "S5", "charts"),
                         FactSpec("stdout-first", "S5", "stdout"),
                         FactSpec("stdout-last", "S5", "stdout"),
                         FactSpec("stderr-first", "S5", "stderr")), ("S1", "S4", "S5")))
    out.append(Spec("F3d-en", "F3d", "en", False,
                    "503 datasets (meta.json and a marker file each), a run, an open question and "
                    "a recent note; report only (design section 18)",
                    (FactSpec("latest-run", "S1", "runs", False),
                     FactSpec("dataset-300", "S1", "datasets", False),
                     FactSpec("recent-note", "S1", "recent", False),
                     FactSpec("open-question", "S1", "journal_open", False))))
    for lang in ("en", "ko"):
        out.append(Spec(f"J-{lang}", "J", lang, True,
                        "42 unresolved findings (a fact in the 3rd; the 42nd is 3,900 characters "
                        "with a fact at 2,500) and 23 open items",
                        (FactSpec("finding-003", "S1", "journal_key"),
                         FactSpec("long-finding-2500", "S1", "journal_text")), ("S1", "S4")))
    for lang in ("en", "ko"):
        out.append(Spec(f"N-{lang}", "N", lang, True,
                        "analytics_record and /analytics record with 4,610 characters (a fact at the end)",
                        (FactSpec("note-tail", "S8", "note_text", False),), ("S8",)))
    for lang in ("en", "ko"):
        out.append(Spec(f"Q-{lang}", "Q", lang, True,
                        "an analytics_query result of 500 rows x 6 columns with long cells", (), ("S6",)))
    out.append(Spec("I-en", "I", "en", True, "an analytics_import result of 300 columns",
                    (FactSpec("column-250", "S7", "columns"),), ("S7",)))
    out.append(Spec("E-en", "E", "en", True,
                    "errors: no_project, run_args, kit_failed (a long stderr), query_failed (503 "
                    "views)", (), ("S10",)))
    out.append(Spec("F2b-en", "F2b", "en", False,
                    "a section whose body is one line of 60 KB; report only",
                    (FactSpec("long-line", "S1", "context", False),)))
    for family in ("F1", "F2", "F3"):
        out.append(Spec(f"F4-{family}-en", "F4", "en", False,
                        f"{family} (en) under a root of 240 or more characters with a Korean "
                        "component; report only (not_run where the OS refuses it)"))
    out.append(Spec("SC-S", "SC", "-", False,
                    "scale S: 300 events, 5 datasets, 30 runs; report only"))
    out.append(Spec("SC-M", "SC", "-", False,
                    "scale M: 10,000 events, 20 datasets, 300 runs; report only"))
    out.append(Spec("B", "B", "ko", False,
                    "a project as analytics init leaves it (the template), and the analytics_init reply; "
                    "report only"))
    return out


SPECS = _specs()
SPEC_BY_ID = {spec.id: spec for spec in SPECS}


def required_facts() -> list[tuple[str, str]]:
    """(fixture, fact) of every fact that has to be read back: the tests' parameters."""
    return [(spec.id, fact.id) for spec in SPECS if spec.required for fact in spec.facts
            if fact.required]


def judged_invariants() -> list[tuple[str, str]]:
    """(fixture, surface) of every invariant that is judged."""
    return [(spec.id, surface) for spec in SPECS if spec.required for surface in spec.invariants]


def select(only: list[str] | None) -> list[Spec]:
    if not only:
        return list(SPECS)
    names = set(only) - {"T"}
    unknown = sorted(n for n in names
                     if n not in SPEC_BY_ID and n not in {spec.family for spec in SPECS})
    if unknown:
        raise UsageError(f"unknown fixture(s): {', '.join(unknown)}; fixtures: "
                         + ", ".join(spec.id for spec in SPECS))
    return [spec for spec in SPECS if spec.id in names or spec.family in names]


# --- fixture text ---------------------------------------------------------------------------------

SENTENCES = {
    "en": ["Regional revenue is the post-discount amount in KRW, and returns are applied within 30 "
           "days of the order date.",
           "The online channel is app plus web, and store pick-up orders count as online.",
           "The month is the month of the order date, and corrections after the accounting close "
           "go to the next month.",
           "Missing region codes are grouped as 'unknown' and are not dropped from totals.",
           "Promotion-period revenue is flagged but stays in the trend calculation.",
           "Overseas revenue is converted to KRW at the month-end rate published by the central "
           "bank.",
           "Inter-company sales are internal and are excluded from external revenue.",
           "A new store enters the like-for-like comparison three months after it opens."],
    "ko": ["지역별 매출은 할인 적용 후 금액(원)으로 계산하고, 반품은 주문일 기준 30일 안에 반영한다.",
           "온라인 채널은 앱과 웹을 합친 값이며, 매장 픽업 주문은 온라인으로 분류한다.",
           "월 기준은 주문일의 달이고, 회계 마감 이후 들어온 정정은 다음 달에 반영한다.",
           "결측 지역 코드는 '미상'으로 묶고, 합계에서 제외하지 않는다.",
           "프로모션 기간의 매출은 따로 표시하되 추세 계산에서는 포함한다.",
           "해외 매출은 월말 환율로 원화 환산한 뒤 합산하며, 환율 출처는 한국은행 고시를 쓴다.",
           "법인 간 거래는 내부 매출로 보고 외부 매출 합계에서 뺀다.",
           "신규 매장은 개점 후 3개월이 지나야 동일 매장 비교에 넣는다."],
    "ascii": ["x" * 72],
}
WORDS = {
    "en": {"title": "Synthetic measurement project",
           "note": "> A synthetic project for measurement. The data is invented.",
           "pin": "<!-- always -->", "goal_h": "Goal",
           "goal": "Explain what drives revenue change by region and channel.",
           "rules_h": "Definitions and rules", "concl_h": "Current conclusions",
           "src_h": "Data sources", "background_h": "Background", "rule_h": "Rule {i:03d}",
           "desc_h": "Section {i:03d}: definition of the monthly active user metric and its "
                     "denominator"},
    "ko": {"title": "합성 측정 프로젝트", "note": "> 측정용 합성 프로젝트입니다. 실제 업무 데이터가 아닙니다.",
           "pin": "<!-- 항상 -->", "goal_h": "목표", "goal": "지역·채널별 매출 변화의 원인을 설명한다.",
           "rules_h": "정의와 규칙", "concl_h": "현재 결론", "src_h": "데이터 출처",
           "background_h": "배경", "rule_h": "규칙 {i:03d}",
           "desc_h": "섹션 {i:03d}: 월간 활성 사용자 지표의 정의와 분모 및 제외 조건에 대한 상세 설명"},
    "ascii": {"title": "Synthetic measurement project", "note": "> Synthetic.",
              "pin": "<!-- always -->", "goal_h": "Goal", "goal": "Measure.",
              "rules_h": "Rules", "concl_h": "Notes", "src_h": "Sources",
              "background_h": "Background", "rule_h": "R{i:03d}", "desc_h": "R{i:03d}"},
}


def sentence(lang: str, i: int) -> str:
    return SENTENCES[lang][i % len(SENTENCES[lang])]


def tag_of(fixture_id: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", fixture_id.upper())


def marker(fixture_id: str, name: str) -> str:
    return f"FACT-{tag_of(fixture_id)}-{name}"


def prose(lang: str, n_chars: int, tag: str) -> list[str]:
    """Deterministic lines of about `n_chars` characters (joined with newlines)."""
    lines, total, i = [], 0, 0
    sentences = SENTENCES[lang]
    while total < n_chars:
        line = f"- ({tag}-{i:04d}) {sentences[i % len(sentences)]}"
        lines.append(line)
        total += len(line) + 1
        i += 1
    return lines


def fit(lines: list[str], target: int, keep_head: int = 1, keep_tail: int = 1) -> list[str]:
    """`lines` joined with newlines is exactly `target` characters: filler lines between the kept
    head and tail are dropped from the end, and the last of them cut or padded."""
    head, tail = lines[:keep_head], lines[len(lines) - keep_tail:]
    filler = list(lines[keep_head:len(lines) - keep_tail])

    def length() -> int:
        return len("\n".join([*head, *filler, *tail]))

    while len(filler) > 1 and length() - len(filler[-1]) - 1 >= target:
        filler.pop()
    over = length() - target
    if over > 0:
        filler[-1] = filler[-1][:len(filler[-1]) - over]
    elif over < 0:
        filler[-1] += "." * -over
    out = [*head, *filler, *tail]
    assert len("\n".join(out)) == target, (len("\n".join(out)), target)
    return out


def section(heading: str, body: list[str]) -> str:
    return "\n".join([f"## {heading}", *body])


def title_block(spec: Spec) -> str:
    words = WORDS[spec.lang]
    return f"# {words['title']} ({spec.id})\n\n{words['note']}"


def f1_context(spec: Spec) -> tuple[str, dict]:
    words, lang = WORDS[spec.lang], spec.lang
    start, end = marker(spec.id, "PINSTART"), marker(spec.id, "PINEND")
    body = [f"- {start}: {sentence(lang, 0)}", *prose(lang, 20_000, "rule"),
            f"- {end}: {sentence(lang, 7)}"]
    pinned_heading = f"{words['rules_h']} {words['pin']}"
    pinned = "\n".join(fit([f"## {pinned_heading}", *body], 20_000, keep_head=2))
    outside_a = section(words["concl_h"], [f"- {sentence(lang, 1)} {marker(spec.id, 'OUTA')}"])
    outside_b = section(words["src_h"], [f"- {sentence(lang, 2)} {marker(spec.id, 'OUTB')}"])
    text = "\n\n".join([title_block(spec), pinned, outside_a, outside_b]) + "\n"
    facts = {"pinned-end": (end, pinned_heading), "outside-budget": (marker(spec.id, "OUTB"),
                                                                     words["src_h"])}
    return text, facts


def f1b_context(spec: Spec) -> tuple[str, dict]:
    words, lang = WORDS[spec.lang], spec.lang
    end = marker(spec.id, "PREEND")
    lines = [f"# {words['title']} ({spec.id})", "", words["note"], "",
             *prose(lang, 20_000, "pre"), f"{end}: {sentence(lang, 7)}"]
    preamble = "\n".join(fit(lines, 20_000, keep_head=4))
    goal = section(f"{words['goal_h']} {words['pin']}", [f"- {words['goal']}"])
    return preamble + "\n\n" + goal + "\n", {"preamble-end": (end, None)}


def f2_context(spec: Spec, *, descriptive: bool, lang: str | None = None,
               fixture_id: str | None = None) -> tuple[str, dict]:
    """A title, a pinned goal, a background section that takes the rest of the budget, then 200
    sections none of which fits, so all 200 are listed with their line ranges."""
    lang = lang or spec.lang
    fixture_id = fixture_id or spec.id
    words = WORDS[lang]
    head = f"# {words['title']} ({fixture_id})\n\n{words['note']}"
    goal = section(f"{words['goal_h']} {words['pin']}", [f"- {words['goal']}"])
    left = 4000 - len(head) - len(goal)
    if lang == "ascii":
        background = section(words["background_h"], ["x" * (left - 520)])
    else:
        lines = [f"## {words['background_h']}", *prose(lang, left, "bg")]
        background = "\n".join(fit(lines, left - 500))
    facts, rules = {}, []
    for i in range(200):
        heading = (words["desc_h"] if descriptive else words["rule_h"]).format(i=i)
        fact = marker(fixture_id, f"S{i:03d}") if i in (0, 137, 199) else None
        if lang == "ascii":
            body = ["x" * 2050, *([f"{fact}: x"] if fact else ["x" * 16]), "x" * 2050]
        else:
            body = prose(lang, 1100, f"r{i:03d}")
            if fact:
                body.insert(len(body) // 2, f"- {fact}: {sentence(lang, i)}")
        if fact:
            facts[f"section-{i:03d}"] = (fact, heading)
        rules.append(section(heading, body))
    text = "\n\n".join([head, goal, background, *rules]) + "\n"
    return text, facts


def f2b_context(spec: Spec) -> tuple[str, dict]:
    words = WORDS["en"]
    fact = marker(spec.id, "LONGLINE")
    head = title_block(spec)
    goal = section(f"{words['goal_h']} {words['pin']}", [f"- {words['goal']}"])
    long_line = section("One long line", ["y" * 61_440 + f" {fact}"])
    after = section("After", [f"- {SENTENCES['en'][0]}"])
    return "\n\n".join([head, goal, long_line, after]) + "\n", {"long-line": (fact,
                                                                               "One long line")}


def png(width: int = 32, height: int = 24, rgb=(42, 120, 214)) -> bytes:
    """A small valid PNG (stored, not compressed, so the same bytes everywhere)."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    raw = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw, 0))
            + chunk(b"IEND", b""))


# --- truth: what the canonical files hold -------------------------------------------------------

@dataclass
class HostSection:
    heading: str
    start: int      # 1-based line, as aelix's read tool numbers lines ("\n" only)
    count: int
    lines: list[str]


@dataclass
class Fact:
    id: str
    first: str
    scope: str
    needles: tuple[str, ...]
    stored_in: tuple[Path, ...]
    required: bool
    section: str | None = None

    def recorded(self) -> bool:
        for path in self.stored_in:
            with contextlib.suppress(OSError):
                if present(path.read_bytes().decode("utf-8", errors="replace"), self.needles):
                    return True
        return False


@dataclass
class Truth:
    """What a claim is checked against: CONTEXT.md as aelix's read tool numbers its lines, and
    the run record."""
    context: Path | None = None
    run: dict | None = None
    _cache: dict = field(default_factory=dict)

    def lines(self) -> list[str]:
        if "lines" not in self._cache:
            text = self.context.read_bytes().decode("utf-8", errors="replace")
            lines = text.split("\n")
            if text.endswith("\n"):
                lines = lines[:-1]
            self._cache["lines"] = lines
        return self._cache["lines"]

    def sections(self) -> list[HostSection]:
        if "sections" not in self._cache:
            lines = self.lines()
            starts = [i for i, line in enumerate(lines) if line.rstrip("\r").startswith("## ")]
            out = []
            for n, start in enumerate(starts):
                end = starts[n + 1] if n + 1 < len(starts) else len(lines)
                # The file's blank last lines are no part of its last section: nothing is in them.
                while n + 1 == len(starts) and end > start + 1 and not lines[end - 1].strip():
                    end -= 1
                out.append(HostSection(lines[start].rstrip("\r"), start + 1, end - start,
                                       lines[start:end]))
            self._cache["sections"] = out
        return self._cache["sections"]

    def preamble(self) -> list[str]:
        sections = self.sections()
        return self.lines()[:sections[0].start - 1] if sections else self.lines()

    def line_of(self, needle: str) -> int | None:
        for i, line in enumerate(self.lines(), 1):
            if needle in line:
                return i
        return None

    def section_at(self, line: int) -> HostSection | None:
        return next((s for s in self.sections() if s.start <= line < s.start + s.count), None)


def run_items(record: dict | None, scope: str) -> list:
    return list((record or {}).get(scope) or [])


# What identifies an item of a run in the analytics_run reply (listed_in: on a page or record).
REPLY_ID = {
    "inputs": lambda item: f" sha256 {str(item.get('sha256'))[:12]} via ",
    "outputs": lambda item: f"  {item['path']} ",
    "charts": lambda item: f"::{item['id']} ",
    "warnings": lambda item: f"  - {item}",
}


def listed_in(text: str, scope: str, item) -> bool:
    """Whether a page or record that claims to list the run's `scope` shows this item: an input
    by its path and its sha256 prefix, an output by its path, a chart by its id (the run page's
    `chart-<id>` anchor or a JSON string) and its PNG path, a warning by its text."""
    if scope == "inputs":
        return present(text, (item["path"], str(item.get("sha256"))[:12]))
    if scope == "outputs":
        return present(text, (item["path"],))
    if scope == "charts":
        png = (item.get("files") or {}).get("png")
        named = present(text, (f'"chart-{item["id"]}"',)) or present(text, (f'"{item["id"]}"',))
        return named and (png is None or present(text, (png,)))
    return present(text, (item,))


# --- the pointer grammar ------------------------------------------------------------------------
# How the measured code says that something was left out, and where it is: the parser follows the
# texts here and nowhere else. It reads both the wording Issue #11 PR-1 measured (the base: the
# checker's hand-made cases are written in it) and the wording PR-2 gives every omission, which
# names the place to read it (docs/CONTRACTS.md, A1).

CONTEXT_HEADER = re.compile(r"read the one you need from (?P<path>.+):$")
CONTEXT_ITEM = re.compile(r"^- (?P<name>.*) \((?P<lines>\d+) lines\): read offset (?P<offset>\d+) "
                          r"limit (?P<limit>\d+)$")
TOOL_CALL = re.compile(r"\bCall (?P<tool>[a-z_]+) for (?P<what>[^.]+)\.")
RUN_MORE = re.compile(r"^  … and (?P<n>[\d,]+) more (?P<label>.+?) — the run page lists them all$")
RUN_PAGE = re.compile(r"^journal: \S* · report: (?P<page>.+)$")
IMAGES = re.compile(r"^images: (?P<shown>\d+) of (?P<total>\d+) chart PNG\(s\) attached below"
                    r"(?P<rest>; open the rest with the read tool at the paths above"
                    r"(?: and, for the (?P<unlisted>\d+) chart\(s\) not listed, at the paths in read "
                    r"(?P<path>.+))?)?$")
CLIP_MARK = "\n… (truncated; see the report page)"
STATUS_MARK = "\n… (truncated)"
PREAMBLE_MARK = re.compile(r"^… \(머리말이 [\d,]+자를 넘어 잘렸습니다")
# PR-2's wording.
PREAMBLE_READ = re.compile(r"^… \(Omitted from current context: [\d,]+ more characters above the "
                           r"first `## ` heading — all of it: read (?P<path>.+) offset "
                           r"(?P<offset>\d+) limit (?P<limit>\d+)\)$")
JOURNAL_OMITTED = re.compile(r"^- … Omitted from current context: .+ — read (?P<path>.+) and find "
                             r"them by id$")
RUN_RECORD_MORE = re.compile(r"^  … and (?P<n>[\d,]+) more (?P<label>.+?) — the run's record lists "
                             r"them all: read (?P<path>.+)$")
LOG_READ = re.compile(r"^(?P<scope>stdout|stderr) \((?:truncated: the last [\d,]+ characters|not "
                      r"shown, the run is ok); the whole log: read (?P<path>.+)\):?$")
CLIP_END = re.compile(r"\n… \(truncated: [\d,]+ more characters; (?P<where>[^\n]+)\)$")
CLIP_READ = re.compile(r"\bread (?P<path>.+)$")
STATUS_CUT = re.compile(r"^… \(truncated at [\d,]+ characters — not shown: .*?; the journal has an "
                        r"entry for every run, import and note: read (?P<path>.+?)"
                        r"(?:; call analytics_query with .*)?\)$")
STATUS_OMITTED = "CONTEXT.md sections not in the system prompt (read them from the file when " \
                 "they matter): "
ITEM_CUT = re.compile(r"^(?:- |  )e\d+ .*…(?: \(runs: [^)]*\))?$")
LOG_HEADS = {"stdout": "stdout (tail):", "stderr": "stderr (tail):"}
# The run page's row under a capped input table (report.run_page).
HTML_RUN_JSON = re.compile(r'[\d,]+ more — .*?<a href="(?P<href>[^"]+)">run\.json</a>')
MORE_LABELS = {"input file(s)": "inputs", "output file(s)": "outputs", "chart(s)": "charts",
               "warning(s)": "warnings"}
COVERS = {"datasets": "datasets", "runs": "runs", "charts": "charts",
          "the recent journal": "recent", "recent journal": "recent", "journal": "recent"}
STATUS_HEADINGS = {"datasets": "datasets (", "runs": "runs (", "charts": "charts (",
                   "open": "open questions/todos", "key": "key information",
                   "recent": "recent journal:"}
# Words that say a text was cut, for the surfaces whose cut HEAD does not mark at all.
CUT_WORDS = re.compile(r"(?i)\btruncated\b|\bomitted\b|\bnot (?:recorded|stored|saved)\b|"
                       r"\b(?:first|last) [\d,]+ characters\b")


@dataclass
class Pointer:
    kind: str                 # read | tool | generic | cut | claim
    scopes: frozenset
    line: str
    path: str | None = None
    offset: int | None = None
    limit: int | None = None
    tool: str | None = None
    claim: dict = field(default_factory=dict)

    def describe(self) -> dict:
        out = {"kind": self.kind, "scopes": sorted(self.scopes), "text": self.line[:240]}
        for key in ("path", "offset", "limit", "tool"):
            if getattr(self, key) is not None:
                out[key] = getattr(self, key)
        return out


def resolve_href(href: str, page: Path) -> Path:
    if href.startswith("file:"):
        return Path(urllib.request.url2pathname(urllib.parse.urlparse(href).path))
    target = urllib.parse.unquote(href.split("#", 1)[0].split("?", 1)[0])
    return Path(os.path.normpath(page.parent / target))


def parse_pointers(text: str, *, source: str | None = None) -> list[Pointer]:
    """Every place `text` says something was left out, and where it points."""
    out: list[Pointer] = []
    lines = text.split("\n")
    page = next((m.group("page") for m in map(RUN_PAGE.match, lines) if m), None)
    roles = {name: role for role, name in TOOL_NAMES.items()}
    context_path, part = None, ""
    for i, line in enumerate(lines):
        if line.startswith("## "):
            part = line
        if m := CONTEXT_HEADER.search(line):
            context_path = m.group("path")
        elif (m := CONTEXT_ITEM.match(line)) and context_path:
            out.append(Pointer("read", frozenset({"context"}), line, path=context_path,
                               offset=int(m.group("offset")), limit=int(m.group("limit")),
                               claim={"lines": int(m.group("lines")), "name": m.group("name")}))
        elif (m := TOOL_CALL.search(line)) and m.group("tool") in roles:
            parts = [p.strip() for p in re.split(r",|\band\b", m.group("what")) if p.strip()]
            scopes = frozenset(COVERS.get(p, p) for p in parts)
            out.append(Pointer("tool", scopes, line, tool=roles[m.group("tool")]))
        elif m := RUN_MORE.match(line):
            scope = MORE_LABELS.get(m.group("label"), m.group("label"))
            out.append(Pointer("read" if page else "generic", frozenset({scope}), line, path=page,
                               claim={"more": int(m.group("n").replace(",", "")),
                                      "lists_all": True}))
        elif m := RUN_RECORD_MORE.match(line):
            scope = MORE_LABELS.get(m.group("label"), m.group("label"))
            out.append(Pointer("read", frozenset({scope}), line, path=m.group("path"),
                               claim={"more": int(m.group("n").replace(",", "")),
                                      "lists_all": True}))
        elif m := IMAGES.match(line):
            if m.group("rest"):
                unlisted = m.group("unlisted")
                out.append(Pointer("claim", frozenset({"charts"}), line, path=m.group("path"),
                                   claim={"paths_above": True, "shown": int(m.group("shown")),
                                          "index": i,
                                          "unlisted": int(unlisted) if unlisted else None}))
        elif line.startswith(STATUS_OMITTED):
            out.append(Pointer("generic", frozenset({"context"}), line[:len(STATUS_OMITTED)]))
        elif PREAMBLE_MARK.match(line):
            out.append(Pointer("cut", frozenset({"preamble"}), line))
        elif m := PREAMBLE_READ.match(line):
            out.append(Pointer("read", frozenset({"preamble"}), line, path=m.group("path"),
                               offset=int(m.group("offset")), limit=int(m.group("limit"))))
        elif m := JOURNAL_OMITTED.match(line):
            scope = ({"journal_key"} if part.startswith("## Key information") else
                     {"journal_open"} if part.startswith("## Open questions") else set())
            out.append(Pointer("read", frozenset({"journal_text", *scope}), line,
                               path=m.group("path")))
        elif m := LOG_READ.match(line):
            out.append(Pointer("read", frozenset({m.group("scope")}), line, path=m.group("path")))
        elif m := STATUS_CUT.match(line):
            out.append(Pointer("read", frozenset({"*"}), line, path=m.group("path")))
        elif ITEM_CUT.match(line):
            out.append(Pointer("cut", frozenset({"journal_text"}), line))
    for scope, head in LOG_HEADS.items():
        if head in lines:
            i = lines.index(head)
            if i + 1 < len(lines) and lines[i + 1].startswith("…"):
                out.append(Pointer("cut", frozenset({scope}), lines[i + 1][:60]))
    clipped = CLIP_END.search(text)
    if text.endswith(CLIP_MARK):
        out.append(Pointer("generic", frozenset({"*"}), CLIP_MARK.strip()))
    elif clipped:
        read = CLIP_READ.search(clipped.group("where"))
        out.append(Pointer("read" if read else "cut", frozenset({"*"}), clipped.group(0).strip(),
                           path=read.group("path") if read else None))
    elif STATUS_MARK + "\n" in text or text.endswith(STATUS_MARK):
        out.append(Pointer("cut", frozenset({"*"}), STATUS_MARK.strip()))
    if source and source.endswith(".html"):
        # The run page's link to run.json: followed only to classify HEAD's route (design 4.2).
        for m in HTML_RUN_JSON.finditer(text):
            out.append(Pointer("read", frozenset({"inputs"}), m.group(0)[-120:],
                               path=str(resolve_href(m.group("href"), Path(source)))))
    return out


def section_name(heading: str) -> str:
    """The name the kit gives a section in its pointer list (what a claim names)."""
    name = " ".join(re.sub(r"<!--\s*(pin|always|항상)\s*-->", "", heading[3:], flags=re.I).split())
    return name if len(name) <= 48 else name[:47] + "…"


def relevant(pointers: list[Pointer], fact: Fact, truth: Truth) -> list[Pointer]:
    out = [p for p in pointers if "*" in p.scopes or fact.scope in p.scopes]
    if fact.scope != "context" or truth.context is None:
        return out
    line = truth.line_of(fact.needles[0])
    reads = [p for p in out if p.kind == "read" and "context" in p.scopes]
    covering = [p for p in reads
                if line is not None and p.offset is not None and p.limit is not None
                and p.offset <= line < p.offset + p.limit]
    if not covering and fact.section:
        name = section_name(f"## {fact.section}")
        covering = [p for p in reads if p.claim.get("name") == name]
    return [p for p in out if p not in reads] + covering


# --- claims ---------------------------------------------------------------------------------------

def claim(pointer: Pointer, what: str, holds: bool, why: str) -> dict:
    return {"pointer": pointer.line[:240], "claim": what, "holds": bool(holds), "why": why}


def context_claim(pointer: Pointer, followed: Followed, truth: Truth) -> dict:
    sec = truth.section_at(pointer.offset)
    first = followed.text.split("\n", 1)[0].rstrip("\r") if followed.text else ""
    holds = (sec is not None and pointer.offset == sec.start and pointer.limit == sec.count
             and pointer.claim.get("lines") == sec.count and first == sec.heading)
    if sec is None:
        why = f"line {pointer.offset} is not in any section"
    else:
        last = pointer.offset + pointer.limit - 1
        why = (f"section {sec.heading[:60]!r} is lines {sec.start}-{sec.start + sec.count - 1}; "
               f"the pointer reads {pointer.offset}-{last} and its first line is {first[:60]!r}")
    return claim(pointer, "the exact line range of one section", holds, why)


def lists_all_claims(pointer: Pointer, followed: Followed | None, truth: Truth,
                     first_text: str) -> list[dict]:
    scope = next(iter(pointer.scopes))
    items = run_items(truth.run, scope)
    if scope not in REPLY_ID or truth.run is None:
        return [claim(pointer, "lists them all", False, "no record to check the claim against")]
    listed = [item for item in items if REPLY_ID[scope](item) in first_text]
    hidden = len(items) - len(listed)
    out = []
    if "more" in pointer.claim:
        out.append(claim(pointer, f"{pointer.claim['more']} more {scope}",
                         pointer.claim["more"] == hidden,
                         f"{len(items)} in the record, {len(listed)} in the reply"))
    if followed is not None:
        missing = [item for item in items if not listed_in(followed.text, scope, item)]
        out.append(claim(pointer, f"{followed.target} lists every one of the {scope}", not missing,
                         f"{len(missing)} of {len(items)} are not there"))
    return out


def paths_above_claim(pointer: Pointer, first_text: str, truth: Truth) -> dict:
    """The image line's claim: every chart not attached has its path above, or — when the line
    names a record and how many charts are not listed — exactly that many have theirs only in the
    record, read from the file the line names."""
    above = "\n".join(first_text.split("\n")[:pointer.claim["index"]])
    charts = [c for c in run_items(truth.run, "charts") if (c.get("files") or {}).get("png")]
    rest = charts[pointer.claim["shown"]:]
    not_above = [c for c in rest if c["files"]["png"] not in above]
    if pointer.path is None:
        missing = [c["id"] for c in not_above]
        return claim(pointer, "every chart not attached has its path above", not missing,
                     f"{len(missing)} of {len(rest)} not attached have no path above"
                     + (f" (first: {missing[0]})" if missing else ""))
    record = ""
    with contextlib.suppress(OSError):
        record = Path(pointer.path).read_bytes().decode("utf-8", errors="replace")
    missing = [c["id"] for c in not_above if not present(record, (c["files"]["png"],))]
    said = pointer.claim.get("unlisted")
    return claim(pointer, f"every chart not attached has its path above, and the {said} not listed "
                          f"have theirs in {pointer.path}",
                 not missing and said == len(not_above),
                 f"{len(not_above)} of {len(rest)} not attached have no path above; "
                 f"{len(missing)} of them are not in the record either"
                 + (f" (first: {missing[0]})" if missing else ""))


def preamble_claim(pointer: Pointer, followed: Followed, truth: Truth) -> dict:
    """A pointer to the text above the first heading reads exactly its lines."""
    want = len(truth.preamble())
    holds = pointer.offset == 1 and pointer.limit == want and \
        followed.text.split("\n") == [line for line in truth.preamble()]
    return claim(pointer, "the exact lines above the first heading", holds,
                 f"the file has {want} lines above its first heading; the pointer reads "
                 f"{pointer.offset}-{(pointer.offset or 0) + (pointer.limit or 0) - 1}")


def covers_claim(pointer: Pointer, followed: Followed) -> dict:
    lines = followed.text.split("\n")
    missing = [s for s in sorted(pointer.scopes) if s in STATUS_HEADINGS
               and not any(line.startswith(STATUS_HEADINGS[s]) for line in lines)]
    return claim(pointer, f"{followed.target} shows {', '.join(sorted(pointer.scopes))}",
                 not missing, f"missing: {', '.join(missing)}" if missing else "all shown")


def followed_claims(pointer: Pointer, followed: Followed, truth: Truth, first_text: str):
    if pointer.kind == "tool":
        return [covers_claim(pointer, followed)]
    if "context" in pointer.scopes and pointer.offset is not None:
        return [context_claim(pointer, followed, truth)]
    if "preamble" in pointer.scopes and pointer.offset is not None:
        return [preamble_claim(pointer, followed, truth)]
    if pointer.claim.get("lists_all") or pointer.scopes & set(REPLY_ID):
        return lists_all_claims(pointer, followed, truth, first_text)
    return []


# --- retrieval ------------------------------------------------------------------------------------

FOLLOWABLE = ("read", "tool")
PASSING = ("R0", "R1", "R2")


def step(pointer: Pointer, followed: Followed, depth: int, found: bool, counters: Counters | None
         ) -> dict:
    out = {"depth": depth, "pointer": pointer.describe(), "call": followed.target,
           "host_calls": followed.calls, "utf8_bytes": followed.utf8_bytes, "found": found,
           "blocked": followed.blocked}
    if followed.error:
        out["error"] = followed.error[:300]
    if counters is not None:
        out["pages"] = [{"utf8_bytes": len(raw.encode("utf-8")), "tokens": counters.count(raw)}
                        for raw in followed.raws]
    return out


def check_fact(host: Host, fact: Fact, first_text: str, truth: Truth,
               counters: Counters | None = None) -> dict:
    """Where a model that has `first_text` finds the fact, and whether what it is told is true."""
    result = {"fact": fact.id, "required": fact.required, "first": fact.first, "scope": fact.scope,
              "needles": list(fact.needles), "recorded": fact.recorded()}
    if present(first_text, fact.needles):
        return {**result, "class": "R0", "passed": True, "found_at": 0, "host_calls": 0,
                "route": [], "claims": []}
    pointers = relevant(parse_pointers(first_text), fact, truth)
    claims = [paths_above_claim(p, first_text, truth) for p in pointers
              if p.kind == "claim" and p.claim.get("paths_above")]
    route, found_at, blocked = [], None, False
    for p in [p for p in pointers if p.kind in FOLLOWABLE]:
        out = host.follow(p)
        claims += followed_claims(p, out, truth, first_text)
        found = present(out.text, fact.needles)
        route.append(step(p, out, 1, found, counters))
        blocked = blocked or out.blocked
        if found:
            found_at = 1
            break
        for q in relevant(parse_pointers(out.text, source=p.path), fact, truth):
            if q.kind not in FOLLOWABLE:
                continue
            out2 = host.follow(q)
            claims += followed_claims(q, out2, truth, out.text)
            found = present(out2.text, fact.needles)
            route.append(step(q, out2, 2, found, counters))
            blocked = blocked or out2.blocked
            if found:
                found_at = 2
                break
        if found_at:
            break
    false_claims = [c for c in claims if not c["holds"]]
    if found_at is not None and not false_claims:
        kind = f"R{found_at}"
    elif found_at is not None or false_claims:
        kind = "wrong_pointer"
    elif blocked:
        kind = "host_blocked"
    elif not result["recorded"]:
        kind = "not_recorded"
    elif any(p.kind == "generic" for p in pointers):
        kind = "generic_pointer"
    elif any(p.kind == "cut" for p in pointers):
        kind = "cut_without_explicit_pointer"
    elif any(p.kind in FOLLOWABLE for p in pointers):
        kind = "wrong_pointer"
    else:
        kind = "silent_omission"
    return {**result, "class": kind, "passed": kind in PASSING, "found_at": found_at,
            "host_calls": sum(s["host_calls"] for s in route), "route": route, "claims": claims,
            "pointers": [p.describe() for p in pointers]}


# --- invariants -----------------------------------------------------------------------------------

def check(key: str, says: str, holds: bool, detail) -> dict:
    """One invariant check: a stable id, what it says, whether it holds, and what was seen."""
    return {"id": key, "name": says, "holds": bool(holds), "detail": detail}


# A place to read the rest: `read <path>` (a path has a separator) or a call of a named tool.
LOCATION = re.compile(r"(?i)\bread\s+\S*[/\\]\S*|\bcall\s+[a-z]+_[a-z_]+")


def located(text: str) -> bool:
    """Whether a passage names a place to read the rest."""
    return bool(LOCATION.search(text))


# analytics_status names the journal once, on a `journal: <path>` line, and its lists refer to it.
JOURNAL_LINE = re.compile(r"^journal: (?P<path>.+)$", re.M)
SAYS_JOURNAL = re.compile(r"\bin the journal\b|\bthe journal entry\b")


def status_journal(text: str) -> Path | None:
    """The journal an analytics_status reply names on its `journal:` line, when that is a file."""
    m = JOURNAL_LINE.search(text)
    return Path(m.group("path")) if m and Path(m.group("path")).is_file() else None


# A line of an analytics_status list that says what the list leaves out, not an entry of it.
STATUS_OMISSION = "  … "


def status_located(line: str, journal: Path | None) -> bool:
    """Whether an omission line of an analytics_status list names where the rest is: a path to read, a
    tool to call, or the journal, when the reply names the journal's file. An entry's own text that
    says so is no omission line, and is not asked."""
    return line.startswith(STATUS_OMISSION) and (
        located(line) or (journal is not None and bool(SAYS_JOURNAL.search(line))))


# A count an omission line gives: "latest 12 of 42" (shown of total) or "30 more" / "30 older".
OMITTED_COUNT = re.compile(r"\blatest (?P<shown>[\d,]+) of (?P<total>[\d,]+)\b|"
                           r"\b(?P<more>[\d,]+) (?:more|older)\b")
ENTRY_LINE = re.compile(r"^- e\d+ [a-z_]+:")


def omission_counts(part: str) -> list[dict]:
    """The omission lines of a list (a line that is not an entry and names where to read the
    rest), each with the counts it states, parsed as numbers: a number elsewhere, such as in an
    entry's own text, says nothing about what was left out."""
    out = []
    for line in part.split("\n"):
        if ENTRY_LINE.match(line) or not located(line):
            continue
        said: dict = {"line": line[:160]}
        for m in OMITTED_COUNT.finditer(line):
            for name in ("shown", "total", "more"):
                if m.group(name) is not None:
                    said.setdefault(name, int(m.group(name).replace(",", "")))
        out.append(said)
    return out


def block_invariants(host: Host, text: str, truth: Truth, project) -> list[dict]:
    core = host.core
    flat = collapse(text)
    pointers = [p for p in parse_pointers(text) if p.kind == "read" and "context" in p.scopes]
    by_offset: dict[int, list[Pointer]] = {}
    for p in pointers:
        by_offset.setdefault(p.offset, []).append(p)
    sections = truth.sections()
    included, exact, wrong, missing = 0, 0, [], []
    for sec in sections:
        body = collapse("\n".join(line.rstrip("\r") for line in sec.lines))
        if body and body in flat:
            included += 1
            continue
        ok = False
        for p in by_offset.get(sec.start, []):
            if p.limit == sec.count and p.claim.get("lines") == sec.count:
                got, _ = host.read(p.path, p.offset, p.limit)
                ok = ok or got.split("\n", 1)[0].rstrip("\r") == sec.heading
        if ok:
            exact += 1
        elif any(p.claim.get("name") == section_name(sec.heading) for p in pointers):
            wrong.append(sec.heading[:60])
        else:
            missing.append(sec.heading[:60])
    out = [check("sections", "every CONTEXT.md section is in the block in full or has its "
                 "exact line range", included + exact == len(sections),
                 {"sections": len(sections), "in_full": included, "exact_pointer": exact,
                  "wrong_pointer": len(wrong), "no_pointer": len(missing),
                  "first_wrong": wrong[:3], "first_missing": missing[:3]})]
    raw_preamble = "\n".join(line.rstrip("\r") for line in truth.preamble()).strip()
    preamble = collapse(raw_preamble)
    pre_pointers = [p for p in parse_pointers(text) if "preamble" in p.scopes and p.kind == "read"]
    out.append(check("preamble", "the text above the first heading is in the block in full or "
                     "has a pointer", not preamble or preamble in flat or bool(pre_pointers),
                     {"preamble_chars": len(raw_preamble),
                      "in_full": bool(preamble) and preamble in flat,
                      "pointers": len(pre_pointers)}))
    events = core.read_events(project)
    resolved = core.resolved_targets(events)
    for key, label, kinds, heading in (
            ("key", "key information", core.KEY_KINDS, "## Key information"),
            ("open", "open items", core.OPEN_KINDS, "## Open questions and todos")):
        items = [e for e in events if e.get("kind") in kinds and e["id"] not in resolved]
        part = text.split(heading, 1)[1].split("\n## ", 1)[0] if heading in text else ""
        shown = [e for e in items
                 if re.search(rf"^- {re.escape(e['id'])} {re.escape(e['kind'])}:", part, re.M)]
        hidden = len(items) - len(shown)
        said = omission_counts(part)
        exact = [s for s in said if s.get("more") == hidden
                 or (s.get("shown"), s.get("total")) == (len(shown), len(items))]
        out.append(check(f"{key}_listed", f"every unresolved {label} entry is listed, or the "
                         "count of the rest and where to read them are given",
                         hidden == 0 or bool(exact),
                         {"unresolved": len(items), "listed": len(shown),
                          "omission_lines": said[:3]}))
        cut = [e["id"] for e in shown if collapse(e.get("text") or "") not in flat]
        out.append(check(f"{key}_text", f"every listed {label} entry is in full, or says where "
                         "its full text is", not cut or located(part),
                         {"listed": len(shown), "cut": len(cut), "first_cut": cut[:3]}))
    return out


def status_sections(text: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current = None
    for line in text.split("\n"):
        key = next((k for k, head in STATUS_HEADINGS.items() if line.startswith(head)), None)
        if key:
            current = key
            sections[key] = []
        elif current and line.startswith("  "):
            sections[current].append(line)
        else:
            current = None
    return sections


def status_invariants(host: Host, text: str, project) -> list[dict]:
    core = host.core
    st = core.status(project)
    events = core.read_events(project)
    resolved = core.resolved_targets(events)
    key_total = sum(1 for e in events
                    if e.get("kind") in core.KEY_KINDS and e["id"] not in resolved)
    expected = ["datasets", "runs", "charts"] + (["open"] if st["open"] else []) \
        + (["key"] if st["key"] else []) + ["recent"]
    sections = status_sections(text)
    out = [check("headings", "every part of the overview has its heading",
                 all(k in sections for k in expected),
                 {"expected": expected, "missing": [k for k in expected if k not in sections]})]
    totals = {"datasets": st["counts"]["datasets"], "runs": st["counts"]["runs"],
              "charts": st["counts"]["charts"], "open": st["counts"]["open"], "key": key_total,
              "recent": st["counts"]["events"]}
    journal = status_journal(text)
    lists = {}
    for key, total in totals.items():
        lines = sections.get(key, [])
        shown = sum(1 for line in lines if not line.startswith(STATUS_OMISSION))
        lists[key] = {"total": total, "shown": shown,
                      "rest_located": any(status_located(line, journal) for line in lines)}
    out.append(check("lists", "every list is complete, or says how many are left out and where "
                     "they are", all(v["shown"] >= v["total"] or v["rest_located"]
                                     for k, v in lists.items() if k in sections or v["total"]),
                     lists))
    claimed = re.search(r"^CONTEXT\.md has ([\d,]+) characters above its first `## ` heading",
                        text, re.M)
    if claimed:
        context = project.context.read_text(encoding="utf-8", errors="replace")
        preamble, _ = core.context_sections(context)
        number = int(claimed.group(1).replace(",", ""))
        out.append(check("context_claim", "what the overview says about CONTEXT.md is true",
                         number == len(preamble),
                         {"said_chars_above_first_heading": number,
                          "chars_above_first_heading": len(preamble),
                          "file_chars": len(context)}))
    cut = status_cut(host, text, project)
    out.append(check("cut", "a cut is at a line end and names what it leaves out and where",
                     not cut["cut"] or (cut["line_aligned"] and located(cut["rest"])),
                     {k: v for k, v in cut.items() if k != "rest"}))
    return out


def status_cut(host: Host, text: str, project) -> dict:
    """Whether an analytics_status reply carries the whole overview, judged against the overview itself:
    the reply's body (without its first line and the note the extension appends) against the
    kit's status_text of the same status with no length limit. When it does not: how many whole
    lines it kept, whether the next line it shows is part of a line (a cut inside a line), and
    what it shows instead of the rest."""
    core = host.core
    full = core.status_text(core.status(project), max_chars=10 ** 9)
    body = text.split("\n", 1)[1] if "\n" in text else ""
    note = host.module.bypass_note(project)
    if note and body.endswith(note):
        body = body[:-len(note)]
    if body == full:
        return {"cut": False}
    got, want = body.split("\n"), full.split("\n")
    kept = 0
    while kept < min(len(got), len(want)) and got[kept] == want[kept]:
        kept += 1
    inside = kept < min(len(got), len(want)) and bool(got[kept]) and \
        want[kept].startswith(got[kept])
    return {"cut": True, "kept_lines": kept, "overview_lines": len(want),
            "line_aligned": not inside, "rest": "\n".join(got[kept:])}


def run_invariants(host: Host, text: str, truth: Truth, project_root: Path) -> list[dict]:
    record = truth.run or {}
    pointers = parse_pointers(text)
    out = []
    for scope in ("inputs", "outputs", "charts", "warnings"):
        items = run_items(record, scope)
        listed = [item for item in items if REPLY_ID[scope](item) in text]
        hidden = len(items) - len(listed)
        claims = []
        for p in pointers:
            if scope in p.scopes and p.kind in FOLLOWABLE:
                claims += lists_all_claims(p, host.follow(p), truth, text)
            elif scope in p.scopes and p.kind == "claim":
                claims.append(paths_above_claim(p, text, truth))
        covered = hidden == 0 or (bool(claims) and all(c["holds"] for c in claims))
        out.append(check(scope, f"every one of the {scope} is listed, or covered by a true "
                         "omission note", covered and all(c["holds"] for c in claims),
                         {"total": len(items), "listed": len(listed), "claims": claims}))
    columns = []
    for o in run_items(record, "outputs"):
        names = [c[0] for c in o.get("schema") or []]
        line = next((li for li in text.split("\n") if li.startswith(f"  {o['path']} ")), "")
        shown = [n for n in names if n in line]
        if len(shown) < len(names) and not re.search(r"\+[\d,]+ more column", line):
            columns.append({"output": o["path"], "columns": len(names), "shown": len(shown)})
    out.append(check("output_columns", "every output's columns are listed, or the rest are "
                     "counted", not columns, columns))
    for scope in ("stdout", "stderr"):
        rel = record.get(scope)
        full = (project_root / rel).read_bytes().decode("utf-8", errors="replace").strip() \
            if rel else ""
        whole = not full or full in text
        marked = any(scope in p.scopes for p in pointers if p.kind in ("cut", "read")) or \
            bool(CUT_WORDS.search(text.split(LOG_HEADS[scope], 1)[1][:200]
                                  if LOG_HEADS[scope] in text else ""))
        # The log's path in any spelling: a line naming both the run and the log file.
        has_path = bool(rel) and any(record.get("id", "\0") in line and Path(rel).name in line
                                     for line in text.split("\n"))
        out.append(check(scope, f"{scope} is in full, or marked as cut with the path of its log",
                         whole or (marked and has_path),
                         {"log_chars": len(full), "in_full": whole, "marked": marked,
                          "log_path": has_path}))
    return out


def cell_shown(value, width: int) -> str:
    """A value as an analytics_query table shows it in a cell `width` characters wide: its first
    width - 1 characters and "…" when it is longer."""
    whole = "null" if value is None else str(value)
    return whole if len(whole) <= width else whole[:width - 1] + "…"


def table_rows(render_table, columns: list, rows: list, width: int) -> list[str]:
    """The rows of render_table's table of `rows`, each one string: a value with a line break in
    it makes its row more than one line of the table."""
    lines = render_table(columns, rows).split("\n")[3:]
    out, at = [], 0
    for row in rows:
        n = 1 + sum(cell_shown(v, width).count("\n") for v in row)
        out.append("\n".join(lines[at:at + n]))
        at += n
    return out


def rows_shown(text: str, rows: list[str]) -> int:
    """How many of the rendered `rows` an analytics_query reply shows, each whole and in order, right
    after its first line and the table's three head lines."""
    parts = text.split("\n", 4)
    body = parts[4] if len(parts) == 5 else ""
    at, n = 0, 0
    for row in rows:
        end = at + len(row)
        if body[at:end] != row or (end < len(body) and body[end] != "\n"):
            break
        at, n = end + 1, n + 1
    return n


# How an analytics_query reply says to read a cut value whole: parts of N characters, at 1, N + 1, ….
CELL_PARTS = re.compile(r"\bsubstr\(<column>, <start>, (?P<n>\d+)\) for <start> = 1, "
                        r"(?P<next>\d+), ")


def query_invariants(text: str, result: dict, render_table, width: int) -> list[dict]:
    """An analytics_query reply against the kit's result it was made from: a row counts as shown when the
    reply has it, every line of it as the extension's render_table renders that row, in order."""
    lines = text.split("\n")
    head = re.match(r"^analytics_query: ok — ([\d,]+) row\(s\)", lines[0])
    rows = table_rows(render_table, result["columns"], result["rows"], width)
    shown = rows_shown(text, rows)
    clipped = shown < len(result["rows"])
    claimed = int(head.group(1).replace(",", "")) if head else None
    out = [check("rows", "the reply shows as many rows as its first line says, each whole",
                 claimed == shown, {"claimed": claimed, "shown": shown})]
    # Query results are not stored and the report has no page for them: the one place to read the
    # rows left out is the query itself, paged (design G7). The reply has to give their true count,
    # the rows before them, and the paging.
    said = re.search(r"^… ([\d,]+) more row\(s\) not shown, after the first ([\d,]+):.*\bLIMIT/"
                     r"OFFSET\b", text, re.M)
    paged = bool(said) and (int(said.group(1).replace(",", "")),
                            int(said.group(2).replace(",", ""))) == (len(result["rows"]) - shown,
                                                                     shown)
    out.append(check("location", "a reply that leaves rows out says how many, after which rows, "
                     "and how to page to them (query results are not stored)",
                     not clipped or paged,
                     {"result_rows": len(result["rows"]), "rows_shown": shown,
                      "paging_said": said.group(0)[:200] if said else None}))
    types = [cell.strip() for cell in lines[2].split(" | ")] if len(lines) > 2 else []
    wanted = [c["type"] for c in result["columns"]]
    cut_types = [w for w, t in zip(wanted, types, strict=False) if t != w]
    out.append(check("types", "every column type is shown whole", not cut_types,
                     {"cut": cut_types[:6]}))
    # A cut value is read whole by following the reply's hint: each part, as render_table shows
    # it, has to be whole, and the parts in order are the value.
    cut = [v for row in result["rows"][:shown] for v in row
           if cell_shown(v, width) != ("null" if v is None else str(v))]
    hint = CELL_PARTS.search(text)
    size = int(hint.group("n")) if hint else 0
    followed = bool(hint) and size > 0 and int(hint.group("next")) == size + 1
    for value in cut[:20] if followed else []:
        whole = str(value)
        parts = [whole[i:i + size] for i in range(0, len(whole), size)]
        got = table_rows(render_table, [{"name": "part", "type": "VARCHAR"}],
                         [[part] for part in parts], width)
        followed = followed and "".join(parts) == whole and all(
            row[:len(part)] == part and not row[len(part):].strip(" ")
            for row, part in zip(got, parts, strict=True))
    out.append(check("cells", "a cut cell says how to read the whole value, and following it "
                     "gives the value whole", not cut or followed,
                     {"cut_cells": len(cut), "part_chars": size or None,
                      "parts_whole": followed if cut else None}))
    return out


def import_invariants(host: Host, text: str, meta: dict) -> list[dict]:
    columns = meta["columns"]
    listed = [c for c in columns if f"  {c['name']}: {c['dtype']}" in text]
    hidden = len(columns) - len(listed)
    reads = [p for p in parse_pointers(text) if p.kind == "read"]
    targets = [host.follow(p).text for p in reads]
    rest_found = any(all(present(target, (c["name"],)) for c in columns) for target in targets)
    return [check("columns", "every column is listed, or the rest can be read where the reply says",
                  hidden == 0 or rest_found,
                  {"columns": len(columns), "listed": len(listed),
                   "pointers": [p.describe() for p in parse_pointers(text)]})]


def note_invariants(given: str, stored: str, reply: str) -> list[dict]:
    cut = len(stored) < len(given.strip())
    return [check("note_cut", "a note stored shorter than it was given says so",
                  not cut or bool(CUT_WORDS.search(reply)),
                  {"given_chars": len(given), "stored_chars": len(stored)})]


# --- fixture building -----------------------------------------------------------------------------

class Ctx:
    """One measurement: the host, the kit, the counters, where the texts go and what was found."""

    def __init__(self, kit: SimpleNamespace, counters: Counters, output: Path, base: Path):
        self.host: Host | None = None
        self.coding_tools: list = []
        self.kit = kit
        self.counters = counters
        self.output = output
        self.base = base
        self.surfaces: list[dict] = []
        self.fixtures: list[dict] = []
        self.facts: list[dict] = []
        self.invariants: list[dict] = []
        self.omissions: list[dict] = []
        self.all_injected: list[dict] = []
        self.scale: list[dict] = []
        self.failures: list[dict] = []
        self.not_run: list[dict] = []

    def connect(self) -> None:
        """aelix and the extension. A changed aelix API fails here, and every surface with it."""
        from aelix_coding_agent.tools import create_coding_tools

        self.host = Host()
        tools = create_coding_tools(str(ROOT))
        self.coding_tools = list(tools.values()) if isinstance(tools, dict) else list(tools)

    def close(self) -> None:
        """The host's one event loop, once the measurement is over."""
        if self.host is not None:
            self.host.close()

    def text(self, spec_id: str, surface: str, text: str, *, variant: str | None = None,
             root: Path | None = None, base: Path | None = None, cuts=(), **extra) -> dict:
        name = surface + (f"-{variant}" if variant else "")
        rel = f"texts/{spec_id}/{name}.txt"
        path = self.output / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
        normal, counts = normalize(text, root, base)
        entry = {"fixture": spec_id, "surface": surface, "variant": variant,
                 "size": size_of(text),
                 "root_normalized": {"size": size_of(normal), "root_occurrences": counts["root"],
                                     "base_occurrences": counts["base"],
                                     "masked_timestamps": counts["masked_timestamps"],
                                     "masked_revisions": counts["masked_revisions"]},
                 "tokens": self.counters.count(text), "length_cuts": sorted(cuts),
                 "text_file": rel, **extra}
        self.surfaces.append(entry)
        return entry

    def project(self, spec: Spec, *, title: str | None = None, root: Path | None = None):
        """A new project made by the kit's own init, under the fixed clock."""
        root = root or self.base / spec.id / "project"
        root.parent.mkdir(parents=True, exist_ok=True)
        words = WORDS.get(spec.lang, WORDS["en"])
        return self.host.core.init_project(root, title=title or f"{words['title']} ({spec.id})",
                                           goal=words["goal"], actor="test")

    def write_context(self, project, text: str | bytes) -> None:
        data = text.encode("utf-8") if isinstance(text, str) else text
        project.context.write_bytes(data)
        self.host.core.sync_context(project, actor="test")


def fact_of(spec: Spec, fact_id: str, needles, stored, section: str | None = None
            ) -> Fact | None:
    """The fact `fact_id` of `spec` with what it is and where it is stored, when `spec` has it."""
    fs = next((f for f in spec.facts if f.id == fact_id), None)
    if fs is None:
        return None
    return Fact(fact_id, fs.first, fs.scope, tuple(needles), tuple(stored), fs.required, section)


def build_context_fixture(ctx: Ctx, spec: Spec, root: Path | None = None) -> SimpleNamespace:
    """F1, F1b, F2, F2t, F2s, F2b: a CONTEXT.md, and F2t's run, dataset and journal entries."""
    family = spec.family if spec.family != "F4" else spec.id.split("-")[1]
    project = ctx.project(spec, root=root)
    if family == "F1":
        text, facts = f1_context(spec)
    elif family == "F1b":
        text, facts = f1b_context(spec)
    elif family in ("F2", "F2t"):
        text, facts = f2_context(spec, descriptive=family == "F2t")
    elif family == "F2s":
        text, facts = f2_context(spec, descriptive=False, lang="en")
    else:
        text, facts = f2b_context(spec)
    data = text.encode("utf-8")
    variant = spec.id.split("-", 1)[1] if family == "F2s" else None
    if variant == "crlf":
        data = text.replace("\n", "\r\n").encode("utf-8")
    elif variant in ("cr", "u2028", "ff"):
        odd = {"cr": "\r", "u2028": "\u2028", "ff": "\x0c"}[variant]
        anchor = "\n## Rule 005\n"
        cut = text.index(anchor) + len(anchor)
        line_end = text.index("\n", cut)
        mid = (cut + line_end) // 2
        data = (text[:mid] + odd + text[mid:]).encode("utf-8")
    elif variant in ("lead1", "lead3"):
        data = ("\n" * int(variant[-1]) + text).encode("utf-8")
    ctx.write_context(project, data)
    found = {fact_id: fact_of(spec, fact_id, (needle,), (project.context,), heading)
             for fact_id, (needle, heading) in facts.items()}
    run = None
    if family == "F2t":
        run, extra = f2t_records(ctx, spec, project)
        found.update(extra)
    return SimpleNamespace(project=project, facts=found, truth=Truth(project.context, run),
                           base=project.root.parent)


def dataset_meta(project, name: str, *, rows: int, columns: list[dict], description: str,
                 source: str, sha: str, size: int, imported_at: str) -> dict:
    return {"schema": "lab.dataset/1", "name": name, "path": f"data/raw/{name}.parquet",
            "sha256": sha, "bytes": size, "rows": rows, "columns": columns,
            "description": description, "imported_at": imported_at,
            "source": {"path": source, "sha256": sha, "bytes": size, "format": "csv",
                       "modified_at": CLOCK_START.isoformat(timespec="seconds")}}


def write_dataset(ctx: Ctx, project, name: str, *, rows: int, columns: list[dict],
                  description: str, source: str, event: bool = True) -> dict:
    """A dataset as `analytics import` leaves it: data/raw/<name>.parquet (a marker file here: nothing in
    the aelix process reads its content) and the meta.json next to it."""
    core = ctx.host.core
    project.raw.mkdir(parents=True, exist_ok=True)
    content = f"PAR1 synthetic dataset {name}\n".encode()
    (project.raw / f"{name}.parquet").write_bytes(content)
    meta = dataset_meta(project, name, rows=rows, columns=columns, description=description,
                        source=source, sha=sha256_bytes(content), size=len(content),
                        imported_at=core.iso())
    core.write_json(project.raw / f"{name}.meta.json", meta)
    if event:
        core.append_event(project, "import",
                          f"imported {name}: {rows:,} rows × {len(columns)} columns from "
                          f"{Path(source).name} — {description}",
                          refs={"data": [meta["path"]]}, actor="test")
    return meta


def write_run(ctx: Ctx, project, run_id: str, *, title: str, script: str, code: str,
              inputs: list, outputs: list, charts: list, stdout: str, stderr: str) -> dict:
    """An ok run's directory as the runner leaves it (record, code snapshot, logs) and its journal
    entry; its warnings are the runner's own (run_warnings)."""
    core, kit = ctx.host.core, ctx.kit
    run_dir = project.runs / run_id
    snapshot = run_dir / "code" / script
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    # Bytes, not text mode: a Windows newline translation would make the script differ from the
    # sha256 the record keeps, and analytics_status would call every chart's script edited.
    snapshot.write_bytes(code.encode("utf-8"))
    (project.root / script).parent.mkdir(parents=True, exist_ok=True)
    (project.root / script).write_bytes(snapshot.read_bytes())
    (run_dir / "stdout.txt").write_bytes(stdout.encode("utf-8"))
    (run_dir / "stderr.txt").write_bytes(stderr.encode("utf-8"))
    record = {
        "schema": "lab.run/1", "id": run_id, "title": title, "status": "ok",
        "started_at": core.iso(), "actor": "test", "session": None, "rerun_of": None,
        "script": {"path": script, "sha256": sha256_bytes((project.root / script).read_bytes()),
                   "snapshot": f"runs/{run_id}/code/{script}"},
        "ended_at": core.iso(), "duration_s": 3.1, "modules": [],
        "inputs": sorted(inputs, key=lambda i: i["path"]), "outputs": outputs, "charts": charts,
        "error": None, "stdout": f"runs/{run_id}/stdout.txt",
        "stderr": f"runs/{run_id}/stderr.txt",
        "env": {"python": "3.12.13", "platform": "synthetic", "packages": {
            "duckdb": "1.4.1", "polars": "1.34.0", "pyarrow": "21.0.0", "matplotlib": "3.10.6",
            "plotly": "6.3.1", "numpy": "2.3.3"}, "uv_lock_sha256": None,
            "kit_version": core.KIT_VERSION, "kit_sha256": kit.runner.kit_sha()}}
    record["warnings"] = kit.runner.run_warnings(record, kit.core.Project(project.root))
    core.write_json(run_dir / "run.json", record)
    written = sorted({o["path"] for o in outputs if o.get("change") != "deleted"})
    read = sorted({i["path"] for i in inputs} - set(written))
    refs = {"runs": [run_id], "charts": [core.chart_key(record, c) for c in charts],
            "data": (written + read)[:core.MAX_EVENT_REFS]}
    text = f"{title} — ok"
    if len(written) + len(read) > len(refs["data"]):
        text += f"; {len(read):,} input file(s), {len(written)} output(s) — see the run page"
    event = core.append_event(project, "run", text, refs=refs, actor="test")
    return {"record": record, "event": event["id"], "dir": run_dir}


def f2t_records(ctx: Ctx, spec: Spec, project) -> tuple[dict, dict]:
    core = ctx.host.core
    lang = spec.lang
    name = f"fact_orders_{lang}"
    meta = write_dataset(ctx, project, name, rows=1200, columns=[
        {"name": "order_date", "dtype": "Date"}, {"name": "region", "dtype": "String"},
        {"name": "revenue", "dtype": "Int64"}], description="synthetic orders",
        source=str(ctx.base / spec.id / "sources" / f"{name}.csv"))
    run_id = "20260901-090100-orders-by-region"
    run = write_run(ctx, project, run_id, title="Orders by region" if lang == "en" else "지역별 주문",
                    script="scripts/01_orders.py", code="import aelix_analytics as analytics\n",
                    inputs=[{"path": meta["path"], "sha256": meta["sha256"],
                             "bytes": meta["bytes"], "via": ["lab.read"], "external": False}],
                    outputs=[], charts=[], stdout="rows 1200\n", stderr="")
    core.add_note(project, "question", f"{marker(spec.id, 'OPEN')}: {sentence(lang, 1)}",
                  actor="test")
    core.add_note(project, "finding", f"{marker(spec.id, 'KEY')}: {sentence(lang, 2)}",
                  actor="test")
    core.add_note(project, "note", f"{marker(spec.id, 'RECENT')}: {sentence(lang, 3)}",
                  actor="test")
    journal = (project.journal,)
    facts = {
        "latest-run": fact_of(spec, "latest-run", (run_id,), (run["dir"] / "run.json",)),
        "dataset": fact_of(spec, "dataset", (name,), (project.raw / f"{name}.meta.json",)),
        "recent-note": fact_of(spec, "recent-note", (marker(spec.id, "RECENT"),), journal),
        "open-question": fact_of(spec, "open-question", (marker(spec.id, "OPEN"),), journal),
        "key-finding": fact_of(spec, "key-finding", (marker(spec.id, "KEY"),), journal),
    }
    return run["record"], facts


F3_SCRIPT = """\
import matplotlib.pyplot as plt
import polars as pl

import aelix_analytics as analytics

frames = [pl.read_ndjson(p) for p in sorted(analytics.path("data/raw").rglob("*.jsonl"))]
print(f"read {len(frames)} files")
for i in range(15):
    fig, ax = plt.subplots()
    ax.plot([1, 2, 3], [i, i + 1, i + 2])
    plt.show()
"""


def build_f3(ctx: Ctx, spec: Spec, root: Path | None = None) -> SimpleNamespace:
    kit = ctx.kit
    lang = spec.lang
    ko = lang == "ko"
    project = ctx.project(spec, root=root)
    words = WORDS[lang]
    ctx.write_context(project, "\n\n".join([
        f"# {words['title']} ({spec.id})",
        section(f"{words['goal_h']} {words['pin']}", [f"- {words['goal']}"])]) + "\n")
    external = Path(os.path.realpath(project.root.parent / "external"))
    external.mkdir(parents=True, exist_ok=True)
    inputs = []
    for i in range(3):
        path = external / (f"지역_{i}.csv" if ko else f"regions_{i}.csv")
        path.write_bytes(f"region,code\nr{i},{i}\n".encode())
        inputs.append({"path": str(path), "sha256": sha256_bytes(path.read_bytes()),
                       "bytes": path.stat().st_size, "via": ["duckdb.read_csv"], "external": True})
    folder = "data/raw/세션로그" if ko else "data/raw/logs"
    (project.root / folder).mkdir(parents=True, exist_ok=True)
    for i in range(500):
        rel = f"{folder}/{'세션' if ko else 'session'}_{i:03d}.jsonl"
        content = json.dumps({"session": i, "lang": lang}).encode() + b"\n"
        (project.root / rel).write_bytes(content)
        inputs.append({"path": rel, "sha256": sha256_bytes(content), "bytes": len(content),
                       "via": ["polars.read_ndjson"], "external": False})
    run_id = f"20260901-090200-{'sessions' if not ko else 'sessions-ko'}"
    outputs = []
    for name, rows, n_cols in (("session_summary", 503, 20), ("daily_counts", 365, 4),
                               ("errors", 12, 3)):
        rel = f"data/derived/{name}.parquet"
        (project.root / rel).parent.mkdir(parents=True, exist_ok=True)
        content = f"PAR1 synthetic output {name}\n".encode()
        (project.root / rel).write_bytes(content)
        schema = [[f"{name}_col_{j:02d}", "Int64" if j % 2 else "String"] for j in range(n_cols)]
        outputs.append({"path": rel, "change": "created", "bytes": len(content),
                        "sha256": sha256_bytes(content), "rows": rows, "schema": schema,
                        "via": "diff"})
    charts = []
    for i in range(15):
        rel = f"runs/{run_id}/charts/trend-{i:02d}.png"
        (project.root / rel).parent.mkdir(parents=True, exist_ok=True)
        (project.root / rel).write_bytes(png(rgb=(42, 120 + i, 214)))
        charts.append({"id": f"trend-{i:02d}", "title": f"{'추세' if ko else 'Trend'} {i:02d}",
                       "kind": "matplotlib", "via": "plt.show", "data": None,
                       "files": {"png": rel}, "line": 11})
    head = marker(spec.id, "STDOUTFIRST")
    last = marker(spec.id, "STDOUTLAST")
    progress = "진행 로그 — 지역별 합계를 계산하는 중입니다" if ko else \
        "progress — computing the regional totals for the sessions"
    stdout_lines = [f"{head}: {'stdout의 첫 줄' if ko else 'the first stdout line'}"]
    lines = int(28_000 / (len(progress.encode()) + 6))  # about 28 KB, in either language
    stdout_lines += [f"{i:04d} {progress}" for i in range(lines)]
    stdout_lines.append(f"{last}: {'stdout의 마지막 줄' if ko else 'the last stdout line'}")
    stdout = "\n".join(stdout_lines) + "\n"
    warn = "경고: 일부 행의 날짜 형식이 달라 변환했습니다" if ko else \
        "warning: some rows had another date format and were converted"
    stderr = f"{marker(spec.id, 'STDERRFIRST')}: {warn}\n" + "".join(f"{warn} ({i})\n"
                                                                     for i in range(60))
    title = "세션 로그 503개 요약" if ko else "Summarize 503 session logs"
    run = write_run(ctx, project, run_id, title=title, script="scripts/03_sessions.py",
                    code=F3_SCRIPT, inputs=inputs, outputs=outputs, charts=charts, stdout=stdout,
                    stderr=stderr)
    record = run["record"]
    built = kit.workspace.build(kit.core.Project(project.root))
    kit_result = {"ok": True, "run": record, "event": run["event"],
                  "stdout_tail": kit.runner._tail(run["dir"] / "stdout.txt"),
                  "stderr_tail": kit.runner._tail(run["dir"] / "stderr.txt"),
                  "page": f"report/runs/{run_id}.html", "workspace": built}
    sorted_inputs = record["inputs"]
    run_json = run["dir"] / "run.json"
    facts = {f"input-{i:03d}": fact_of(spec, f"input-{i:03d}", (
        sorted_inputs[i]["path"], sorted_inputs[i]["sha256"][:12]), (run_json,))
        for i in (0, 300, 502)}
    facts["chart-14"] = fact_of(spec, "chart-14", (charts[14]["files"]["png"],), (run_json,))
    facts["stdout-first"] = fact_of(spec, "stdout-first", (head,), (run["dir"] / "stdout.txt",))
    facts["stdout-last"] = fact_of(spec, "stdout-last", (last,), (run["dir"] / "stdout.txt",))
    facts["stderr-first"] = fact_of(spec, "stderr-first", (marker(spec.id, "STDERRFIRST"),),
                                    (run["dir"] / "stderr.txt",))
    return SimpleNamespace(project=project, facts=facts, truth=Truth(project.context, record),
                           base=project.root.parent, kit_result=kit_result, title=title,
                           script="scripts/03_sessions.py", run_dir=run["dir"])


# --- measuring ------------------------------------------------------------------------------------

def host_prompt(ctx: Ctx, root: Path) -> str:
    from aelix_coding_agent.cli.agent_context import build_system_prompt

    return build_system_prompt(str(root), tools=[*ctx.coding_tools, *ctx.host.module.TOOLS])


# Length-based cuts are judged against what was cut, never by the words a cut adds: the block
# against CONTEXT.md, analytics_status against the kit's overview with no length limit (status_cut), and
# a tool reply by the extension's clip itself (Reply.clipped).

def preamble_cut(core, project, block: str) -> bool:
    """Whether the block lacks part of CONTEXT.md's text above its first heading (the kit's own
    split of the file)."""
    if not project.context.is_file():
        return False
    text = project.context.read_text(encoding="utf-8", errors="replace").strip()
    preamble, _ = core.context_sections(text)
    return bool(preamble) and collapse(preamble) not in collapse(block)


def measure_context(ctx: Ctx, spec: Spec, built) -> dict:
    """S1 (hook output minus the base prompt it was given) and S4 (analytics_status)."""
    host, root, base = ctx.host, built.project.root, built.base
    host.use(root)
    prompt = host_prompt(ctx, root)
    started = time.perf_counter()
    out = host.hook(prompt)
    elapsed = round((time.perf_counter() - started) * 1000, 1)
    if out is None or not out.startswith(prompt):
        raise RuntimeError("the hook returned no context block")
    block = out[len(prompt):]
    reply = host.tool("status", {})
    status_cut_ = not reply.is_error and status_cut(host, reply.text, built.project)["cut"]
    built.entries = {
        "S1": ctx.text(spec.id, "S1", block, root=root, base=base,
                       cuts=["preamble_4000"] if preamble_cut(host.core, built.project, block)
                       else [], hook_returned_block=True, elapsed_ms=elapsed),
        "S4": ctx.text(spec.id, "S4", reply.text, root=root, base=base,
                       cuts=["status_9000"] if status_cut_ else [], is_error=reply.is_error,
                       elapsed_ms=reply.elapsed_ms)}
    return {"S1": block, "S4": reply.text}


def judge_facts(ctx: Ctx, spec: Spec, built, texts: dict) -> None:
    for fs in spec.facts:
        fact = built.facts.get(fs.id)
        if fact is None:
            continue
        result = check_fact(ctx.host, fact, texts[fact.first], built.truth, ctx.counters)
        first_entry = next(e for e in reversed(ctx.surfaces)
                           if e["fixture"] == spec.id and e["surface"] == fact.first
                           and e["variant"] is None)
        ctx.facts.append({"fixture": spec.id, "family": spec.family, "lang": spec.lang,
                          "first_text_bytes": first_entry["size"]["utf8_bytes"],
                          "first_text_tokens": first_entry["tokens"], **result})


def judge(ctx: Ctx, spec: Spec, surface: str, checks: list[dict]) -> None:
    ctx.invariants.append({"fixture": spec.id, "family": spec.family, "lang": spec.lang,
                           "surface": surface,
                           "judged": spec.required and surface in spec.invariants,
                           "holds": all(c["holds"] for c in checks), "checks": checks})


def omission_figures(ctx: Ctx, spec: Spec, texts: dict, built) -> None:
    block = texts.get("S1", "")
    part = block.split("### CONTEXT.md sections not repeated here", 1)
    pointer_list = ("### CONTEXT.md sections not repeated here" + part[1].split("\n## ", 1)[0]
                    if len(part) > 1 else "")
    core = ctx.host.core
    st = core.status(built.project)
    status_cut_ = "status_9000" in built.entries["S4"]["length_cuts"]
    entry = {"fixture": spec.id, "block_sections_listed": sum(
        1 for p in parse_pointers(block) if p.kind == "read" and "context" in p.scopes),
        "block_pointer_list": size_of(pointer_list),
        "status_cut": status_cut_,
        "status_omitted_names_line": size_of(next(
            (line for line in texts.get("S4", "").split("\n") if line.startswith(STATUS_OMITTED)),
            "")),
        "counts": st["counts"]}
    ctx.omissions.append(entry)
    # The kit's own block with no budget and no item limit, as the hook would append it.
    full_block = "\n\n" + core.context_block(built.project, max_context_chars=10 ** 9,
                                              max_items=10 ** 9)
    # analytics_status serialized as its reply is (the reply's first line, the overview, the note the
    # extension appends), with no item limit and no length limit.
    every = core.status(built.project, limit=10 ** 6)
    header = texts["S4"].split("\n", 1)[0]
    full_status = (header + "\n" + core.status_text(every, max_chars=10 ** 9)
                   + ctx.host.module.bypass_note(built.project))
    ctx.all_injected.append({"fixture": spec.id, "S1_everything": size_of(full_block),
                             "S4_everything": size_of(full_status),
                             "S1_measured": size_of(block), "S4_measured": size_of(texts["S4"]),
                             "S4_left_out": {
                                 "length_cut": status_cut_,
                                 "lists_capped": [k for k in ("runs", "charts", "open", "key")
                                                  if len(every[k]) > len(st[k])]},
                             "journal_utf8_bytes": built.project.journal.stat().st_size})


def measure_context_family(ctx: Ctx, spec: Spec) -> None:
    built = build_context_fixture(ctx, spec)
    fixture_info(ctx, spec, built)
    texts = measure_context(ctx, spec, built)
    judge_facts(ctx, spec, built, texts)
    judge(ctx, spec, "S1", block_invariants(ctx.host, texts["S1"], built.truth, built.project))
    if spec.family != "F2s":
        judge(ctx, spec, "S4", status_invariants(ctx.host, texts["S4"], built.project))
    omission_figures(ctx, spec, texts, built)
    if spec.family == "F1":
        pinned = next(s for s in built.truth.sections() if "PINEND" in "\n".join(s.lines))
        built.entries["S1"]["pinned_section_in_block"] = collapse(
            "\n".join(pinned.lines)) in collapse(texts["S1"])


def measure_f3(ctx: Ctx, spec: Spec, root: Path | None = None, report_only: bool = False) -> None:
    built = build_f3(ctx, spec, root)
    fixture_info(ctx, spec, built)
    texts = measure_context(ctx, spec, built)
    host = ctx.host
    host.kit_results.append((True, built.kit_result))
    reply = host.tool("run", {"title": built.title, "script": built.script}, images=True)
    texts["S5"] = reply.text
    images = []
    for image in reply.images:
        raw = image.data.encode("ascii")
        images.append({"mime_type": image.mime_type, "png_bytes": len(base64.b64decode(raw)),
                       "base64_chars": len(image.data), "tokens": ctx.counters.images()})
    ctx.text(spec.id, "S5", reply.text, root=built.project.root, base=built.base,
             cuts=["reply_14000"] if reply.clipped else [], is_error=reply.is_error, images=images,
             kit_command=host.kit_commands[-1], elapsed_ms=reply.elapsed_ms,
             run_page=size_of((built.project.root / built.kit_result["page"]).read_text(
                 encoding="utf-8")), run_page_build="reduced (no analysis libraries)",
             run_json_bytes=(built.run_dir / "run.json").stat().st_size)
    if report_only:
        return
    judge_facts(ctx, spec, built, texts)
    judge(ctx, spec, "S1", block_invariants(host, texts["S1"], built.truth, built.project))
    judge(ctx, spec, "S4", status_invariants(host, texts["S4"], built.project))
    judge(ctx, spec, "S5", run_invariants(host, texts["S5"], built.truth, built.project.root))
    omission_figures(ctx, spec, texts, built)
    module = host.module
    saved = (module.MAX_LIST, module.MAX_CHART_LIST, module._clip)
    try:
        module.MAX_LIST = module.MAX_CHART_LIST = 10 ** 9
        # Not MAX_TEXT: _clip took its limit when it was defined, so the clip itself is replaced.
        module._clip = lambda text, *args, **kwargs: text
        value = json.loads(json.dumps(built.kit_result, ensure_ascii=False, default=str))
        everything = module.render_run(built.project, value, (len(reply.images), 15))
    finally:
        module.MAX_LIST, module.MAX_CHART_LIST, module._clip = saved
    logs = sum((built.run_dir / name).stat().st_size for name in ("stdout.txt", "stderr.txt"))
    page = built.project.root / built.kit_result["page"]
    ctx.all_injected.append({"fixture": spec.id, "S5_every_list_item": size_of(everything),
                             "S5_measured": size_of(reply.text), "logs_utf8_bytes": logs,
                             "run_page_reduced_build_utf8_bytes": page.stat().st_size,
                             "run_json_utf8_bytes": (built.run_dir / "run.json").stat().st_size})


def measure_f3d(ctx: Ctx, spec: Spec) -> None:
    core = ctx.host.core
    project = ctx.project(spec)
    ctx.write_context(project, "\n\n".join([title_block(spec), section(
        f"Goal {WORDS['en']['pin']}", [f"- {WORDS['en']['goal']}"])]) + "\n")
    for i in range(503):
        write_dataset(ctx, project, f"part_{i:03d}", rows=12, columns=[
            {"name": "date", "dtype": "Date"}, {"name": "region", "dtype": "String"},
            {"name": "revenue", "dtype": "Int64"}], description=f"synthetic sales part {i:03d}",
            source=str(ctx.base / spec.id / "sources" / f"sales_region_{i:03d}.csv"))
    run_id = "20260901-090300-read-all-parts"
    run = write_run(ctx, project, run_id, title="Read all parts", script="scripts/01_parts.py",
                    code="import aelix_analytics as analytics\n", inputs=[], outputs=[], charts=[],
                    stdout="503\n", stderr="")
    core.add_note(project, "question", f"{marker(spec.id, 'OPEN')}: {SENTENCES['en'][1]}",
                  actor="test")
    core.add_note(project, "note", f"{marker(spec.id, 'RECENT')}: {SENTENCES['en'][3]}",
                  actor="test")
    journal = (project.journal,)
    facts = {"latest-run": fact_of(spec, "latest-run", (run_id,), (run["dir"] / "run.json",)),
             "dataset-300": fact_of(spec, "dataset-300", ("part_300",),
                                    (project.raw / "part_300.meta.json",)),
             "recent-note": fact_of(spec, "recent-note", (marker(spec.id, "RECENT"),), journal),
             "open-question": fact_of(spec, "open-question", (marker(spec.id, "OPEN"),), journal)}
    built = SimpleNamespace(project=project, facts=facts,
                            truth=Truth(project.context, run["record"]), base=project.root.parent)
    fixture_info(ctx, spec, built)
    texts = measure_context(ctx, spec, built)
    judge_facts(ctx, spec, built, texts)
    judge(ctx, spec, "S4", status_invariants(ctx.host, texts["S4"], project))
    omission_figures(ctx, spec, texts, built)


def measure_journal(ctx: Ctx, spec: Spec) -> None:
    core = ctx.host.core
    lang = spec.lang
    project = ctx.project(spec)
    words = WORDS[lang]
    ctx.write_context(project, "\n\n".join([title_block(spec), section(
        f"{words['goal_h']} {words['pin']}", [f"- {words['goal']}"])]) + "\n")
    sentences = SENTENCES[lang]
    for i in range(1, 42):
        text = f"{'발견' if lang == 'ko' else 'Finding'} {i:02d}: {sentences[i % 8]}"
        if i == 3:
            text = f"{marker(spec.id, 'KEY003')}: {text}"
        core.add_note(project, "finding", text, actor="test")
    for i in range(1, 24):
        kind = "question" if i % 3 else "todo"
        core.add_note(project, kind, f"{kind} {i:02d}: {sentences[(i + 3) % 8]}", actor="test")
    pad = "근거와 한계를 함께 적는다. " if lang == "ko" else \
        "The evidence and its limits are written down together. "
    long_text = (pad * (2500 // len(pad) + 1))[:2500] + marker(spec.id, "LONG2500") + " "
    long_text += (pad * (1400 // len(pad) + 1))[:3900 - len(long_text)]
    core.add_note(project, "finding", long_text, actor="test")
    journal = (project.journal,)
    facts = {"finding-003": fact_of(spec, "finding-003", (marker(spec.id, "KEY003"),), journal),
             "long-finding-2500": fact_of(spec, "long-finding-2500",
                                          (marker(spec.id, "LONG2500"),), journal)}
    built = SimpleNamespace(project=project, facts=facts, truth=Truth(project.context, None),
                            base=project.root.parent)
    fixture_info(ctx, spec, built)
    texts = measure_context(ctx, spec, built)
    judge_facts(ctx, spec, built, texts)
    judge(ctx, spec, "S1", block_invariants(ctx.host, texts["S1"], built.truth, project))
    judge(ctx, spec, "S4", status_invariants(ctx.host, texts["S4"], project))
    omission_figures(ctx, spec, texts, built)


def measure_note(ctx: Ctx, spec: Spec) -> None:
    core, host = ctx.host.core, ctx.host
    lang = spec.lang
    project = ctx.project(spec)
    built = SimpleNamespace(project=project, facts={}, truth=Truth(project.context, None),
                            base=project.root.parent)
    fixture_info(ctx, spec, built)
    host.use(project.root)
    pad = "근거와 한계를 함께 적는다. " if lang == "ko" else \
        "The evidence and its limits are written down together. "
    checks = []
    for variant in ("tool", "command"):
        tail = marker(spec.id, f"TAIL{variant.upper()}")
        text = (pad * (4610 // len(pad) + 1))[:4610 - len(tail) - 1] + " " + tail
        if variant == "tool":
            reply = host.tool("note", {"kind": "finding", "text": text})
            reply_text, is_error = reply.text, reply.is_error
        else:
            reply_text, is_error = host.command(f"record finding {text}"), False
        event = core.read_events(project)[-1]
        ctx.text(spec.id, "S8", reply_text, variant=variant, root=project.root,
                 base=project.root.parent, is_error=is_error, given_chars=len(text),
                 stored_chars=len(event["text"]))
        fact = Fact("note-tail", "S8", "note_text", (tail,), (project.journal,), False)
        result = check_fact(host, fact, reply_text, built.truth, ctx.counters)
        ctx.facts.append({"fixture": spec.id, "family": spec.family, "lang": lang,
                          "variant": variant,
                          "first_text_bytes": len(reply_text.encode("utf-8")), **result})
        checks += [{**c, "id": f"{c['id']}_{variant}", "name": f"{variant}: {c['name']}"}
                   for c in note_invariants(text, event["text"], reply_text)]
    judge(ctx, spec, "S8", checks)


QUERY_COLUMNS = [("n", "BIGINT"), ("region", "VARCHAR"), ("note", "VARCHAR"),
                 ("amount", "DECIMAL(18,3)"), ("day", "DATE"), ("share", "DOUBLE")]


def query_result(ctx: Ctx, lang: str) -> dict:
    """An analytics_query result as the kit prints it: 500 rows, cells through data.cell, a value with a
    line break in every fifth."""
    from datetime import date
    from decimal import Decimal

    cell = ctx.kit.data.cell
    regions = ["서울", "부산", "대구", "인천"] if lang == "ko" else ["Seoul", "Busan", "Daegu",
                                                                  "Incheon"]
    long_note = ("반품과 취소가 반영되지 않은 주문이며, 할인 쿠폰과 적립금 사용분은 따로 집계했다. " * 6
                 if lang == "ko" else
                 "Orders before returns and cancellations; coupons and points are counted apart. "
                 * 4)
    # Every fifth note has a line break near its start: its row is two lines of the table.
    two_lines = "두 줄로 적은 메모:\n" if lang == "ko" else "a note on two lines:\n"
    rows = []
    for i in range(500):
        note = f"{i:03d} {two_lines if i % 5 == 2 else ''}{long_note}"
        rows.append([cell(v) for v in (i, regions[i % 4], note,
                                       Decimal(f"{1000 + i * 7}.125"),
                                       date(2025, 1 + i % 12, 1 + i % 28), round(i / 499, 4))])
    return {"ok": True, "columns": [{"name": n, "type": t} for n, t in QUERY_COLUMNS],
            "rows": rows, "truncated": True, "limit": 500, "tables": ["orders"],
            "views": {"orders": "data/raw/orders.parquet"}, "elapsed_ms": 12.5}


def measure_query(ctx: Ctx, spec: Spec) -> None:
    host = ctx.host
    project = ctx.project(spec)
    built = SimpleNamespace(project=project, facts={}, truth=Truth(project.context, None),
                            base=project.root.parent)
    fixture_info(ctx, spec, built)
    host.use(project.root)
    result = query_result(ctx, spec.lang)
    host.kit_results.append((True, result))
    reply = host.tool("query", {"sql": "SELECT * FROM orders", "limit": 500})
    # The result as the extension got it (through JSON, as _run_kit hands it over).
    given = json.loads(json.dumps(result, ensure_ascii=False, default=str))
    checks = query_invariants(reply.text, given, host.module.render_table,
                              host.module.MAX_CELL)
    rows = next(c for c in checks if c["id"] == "location")["detail"]
    # A reply that shows fewer rows than the result has was cut to its length, by its clip or by
    # rendering only the rows that fit.
    cut = reply.clipped or rows["rows_shown"] < rows["result_rows"]
    ctx.text(spec.id, "S6", reply.text, root=project.root, base=project.root.parent,
             cuts=["reply_14000"] if cut else [], is_error=reply.is_error,
             kit_command=host.kit_commands[-1])
    judge(ctx, spec, "S6", checks)


def measure_import(ctx: Ctx, spec: Spec) -> None:
    core, host = ctx.host.core, ctx.host
    project = ctx.project(spec)
    source = ctx.base / spec.id / "sources" / "wide_table.csv"
    source.parent.mkdir(parents=True, exist_ok=True)
    names = [f"col_{j:03d}_regional_revenue_total" for j in range(300)]
    source.write_bytes((",".join(names) + "\n" + ",".join(str(j) for j in range(300)) + "\n")
                       .encode())
    columns = [{"name": n, "dtype": "Int64" if j % 3 else "Float64"} for j, n in enumerate(names)]
    meta = write_dataset(ctx, project, "wide_table", rows=50, columns=columns,
                         description="300 columns", source=str(source))
    cell = ctx.kit.data.cell
    profile = {"sha256": meta["sha256"], "bytes": meta["bytes"], "rows": 50,
               "columns": [{"name": c["name"], "dtype": c["dtype"], "nulls": 0,
                            "min": cell(j, 60), "max": cell(j + 49_000, 60),
                            "mean": cell(j + 24_500.5, 60), "distinct": None}
                           for j, c in enumerate(columns)],
               "preview": {"columns": names, "rows": [[cell(r * 1000 + j, 120) for j in range(300)]
                                                      for r in range(5)]}}
    event = core.read_events(project)[-1]["id"]
    result = {"ok": True, "dataset": meta, "profile": profile, "event": event, "warnings": [],
              "elapsed_s": 0.4}
    facts = {"column-250": fact_of(spec, "column-250", (columns[250]["name"],
                                                        columns[250]["dtype"]),
                                   (project.raw / "wide_table.meta.json",))}
    built = SimpleNamespace(project=project, facts=facts, truth=Truth(project.context, None),
                            base=project.root.parent)
    fixture_info(ctx, spec, built)
    host.use(project.root)
    host.kit_results.append((True, result))
    reply = host.tool("import", {"source": str(source), "name": "wide_table",
                                 "description": "300 columns"})
    ctx.text(spec.id, "S7", reply.text, root=project.root, base=project.root.parent,
             cuts=["reply_14000"] if reply.clipped else [], is_error=reply.is_error,
             kit_command=host.kit_commands[-1])
    judge_facts(ctx, spec, built, {"S7": reply.text})
    judge(ctx, spec, "S7", import_invariants(host, reply.text, meta))


WINDOWS_TRACEBACK = (
    '  File "C:\\Users\\runneradmin\\AppData\\Local\\uv\\cache\\archive-v0\\{i:04d}\\Lib\\'
    'site-packages\\duckdb\\__init__.py", line {i}, in <module>\n')


def measure_errors(ctx: Ctx, spec: Spec) -> None:
    host = ctx.host
    module = host.module
    project = ctx.project(spec)
    for i in range(503):
        write_dataset(ctx, project, f"part_{i:03d}", rows=12, columns=[
            {"name": "revenue", "dtype": "Int64"}], description=f"part {i:03d}",
            source=str(ctx.base / spec.id / "sources" / f"part_{i:03d}.csv"), event=False)
    built = SimpleNamespace(project=project, facts={}, truth=Truth(project.context, None),
                            base=project.root.parent)
    fixture_info(ctx, spec, built)
    root, base = project.root, project.root.parent
    checks = []

    def record(variant: str, reply: Reply, **extra) -> None:
        ctx.text(spec.id, "S10", reply.text, variant=variant, root=root, base=base,
                 is_error=reply.is_error, **extra)

    empty = base / "elsewhere"
    empty.mkdir(parents=True, exist_ok=True)
    host.use(None)
    cwd = os.getcwd()
    os.chdir(empty)  # no project is found from here: none is given, and none is above it
    try:
        reply = host.tool("status", {})
    finally:
        os.chdir(cwd)
    record("no_project", reply)
    host.use(root)
    reply = host.tool("run", {"title": "x"})
    record("run_args", reply)
    views = host.core.view_names(host.core.list_datasets(project))
    host.kit_results.append((False, {"ok": False, "error": {
        "code": "query_failed",
        "message": "Catalog Error: Table with name no_such_table does not exist!\nDid you mean "
                   "\"part_001\"?\n\nLINE 1: SELECT * FROM no_such_table\n                      ^",
        "hint": "views: " + ", ".join(views)}}))
    reply = host.tool("query", {"sql": "SELECT * FROM no_such_table"})
    record("query_failed", reply, kit_command=host.kit_commands[-1])
    cut = details_cut(reply)
    checks.append(check("query_failed_details", "query_failed: a cut of details is marked",
                        not cut["cut"] or cut["marked"], cut))
    # kit_failed comes from run_kit itself: the real one runs, with a uv that is found and a kit
    # that exits 1 without a result line, its stderr a Windows traceback.
    stderr = "Traceback (most recent call last):\n" + "".join(
        WINDOWS_TRACEBACK.format(i=i) for i in range(60)) + "ImportError: DLL load failed\n"
    saved = (module.run_kit, module.resolve_uv, module._spawn)

    async def exited_without_a_result(argv, cwd, env, timeout, processes, request=None):
        return False, 1, b"", stderr.encode("utf-8")

    try:
        module.run_kit = host.real_run_kit
        module.resolve_uv = lambda processes=None: "uv"
        module._spawn = exited_without_a_result
        reply = host.tool("query", {"sql": "SELECT 1"})
    finally:
        module.run_kit, module.resolve_uv, module._spawn = saved
    record("kit_failed", reply, stderr_chars=len(stderr))
    kit_failed_reply = reply
    full = base / "not-empty"
    try:
        full.mkdir(parents=True, exist_ok=True)
        for i in range(12):
            name = f"{i:02d}-" + "quarterly-revenue-by-region-and-channel-" * 5
            (full / f"{name[:196]}.csv").write_bytes(b"x\n")
    except OSError as exc:
        ctx.not_run.append({"what": "E-en init_not_empty", "reason": f"the OS refused a file name "
                                                                    f"of 200 characters: {exc}"})
    else:
        host.use(None)
        reply = host.tool("init", {"path": str(full), "title": "x"})
        host.use(root)
        record("init_not_empty", reply)
        cut = details_cut(reply)
        checks.append(check("init_not_empty_details", "init_not_empty: a cut of details is marked",
                            not cut["cut"] or cut["marked"], cut))
    reply = kit_failed_reply
    cut = details_cut(reply)
    shown = (reply.details or {}).get("error", {}).get("details", {}).get("stderr", "") \
        if isinstance(reply.details, dict) else ""
    checks.append(check("kit_failed_details", "kit_failed: a cut of details is marked",
                        not cut["cut"] or cut["marked"], cut))
    checks.append(check("kit_failed_stderr", "kit_failed: a stderr shown in part says it is the "
                        "last part",
                        len(shown) >= len(stderr) or bool(CUT_WORDS.search(reply.text)),
                        {"stderr_chars": len(stderr), "shown_chars": len(shown)}))
    judge(ctx, spec, "S10", checks)


def details_cut(reply: Reply) -> dict:
    """Whether the `details:` line of an error reply shows the error's details whole, and if not,
    whether the reply says so."""
    error = reply.details.get("error") or {} if isinstance(reply.details, dict) else {}
    if not error.get("details"):
        return {"details_chars": 0, "cut": False, "marked": False}
    full = json.dumps(error["details"], ensure_ascii=False)
    shown = reply.text.split("\ndetails: ", 1)[1] if "\ndetails: " in reply.text else ""
    same = 0
    while same < min(len(full), len(shown)) and full[same] == shown[same]:
        same += 1
    return {"details_chars": len(full), "shown_chars": same, "cut": same < len(full),
            "marked": bool(CUT_WORDS.search(shown[same:]))}


def measure_long_root(ctx: Ctx, spec: Spec) -> None:
    family = spec.id.split("-")[1]
    long_dir = ctx.base / spec.id / ("긴경로-" + "d" * 40) / ("하위-" + "e" * 60) / ("깊은-" + "f" * 70)
    root = long_dir / "project"
    try:
        while len(str(root.resolve())) < 240:
            long_dir = long_dir / ("g" * 30)
            root = long_dir / "project"
        root.parent.mkdir(parents=True, exist_ok=True)
        (root.parent / "probe.txt").write_bytes(b"x")
    except OSError as exc:
        ctx.not_run.append({"what": spec.id, "reason": f"the OS refused a root of "
                                                       f"{len(str(root))} characters: {exc}"})
        return
    try:
        if family == "F3":
            measure_f3(ctx, spec, root=root, report_only=True)
        else:
            sub = Spec(spec.id, family, "en", False, spec.shape)
            built = build_context_fixture(ctx, sub, root=root)
            fixture_info(ctx, spec, built)
            texts = measure_context(ctx, spec, built)
            omission_figures(ctx, spec, texts, built)
    except OSError as exc:
        ctx.not_run.append({"what": spec.id, "reason": f"the OS refused a path under a root of "
                                                       f"{len(str(root))} characters: {exc}"})


def measure_long_line(ctx: Ctx, spec: Spec) -> None:
    built = build_context_fixture(ctx, spec)
    fixture_info(ctx, spec, built)
    texts = measure_context(ctx, spec, built)
    judge_facts(ctx, spec, built, texts)


def measure_scale(ctx: Ctx, spec: Spec) -> None:
    """S and M of docs/BETA-QUALITY-GATES.md:212-216: block and overview sizes and the time to make
    them. The journal lines and records are written directly, in the kit's formats."""
    core = ctx.host.core
    events_n, datasets_n, runs_n = (300, 5, 30) if spec.id == "SC-S" else (10_000, 20, 300)
    project = ctx.project(spec, title=f"scale {spec.id}")
    ctx.write_context(project, "\n\n".join([
        "# scale", section("Goal <!-- always -->", ["- compare regional growth"])]) + "\n")
    datasets = [write_dataset(ctx, project, f"t_{i:03d}", rows=100, columns=[
        {"name": "v", "dtype": "Int64"}], description=f"table {i}",
        source=str(ctx.base / spec.id / "sources" / f"t_{i:03d}.csv"), event=False)
        for i in range(datasets_n)]
    kinds = ("finding", "note", "question", "decision", "todo")
    start = core._last_seq(project.journal)
    lines = []
    for i in range(events_n):
        seq = start + i + 1
        lines.append(json.dumps({"schema": "lab.event/1", "seq": seq, "id": f"e{seq}",
                                 "ts": (CLOCK_START + timedelta(seconds=i)).isoformat(
                                     timespec="milliseconds"), "kind": kinds[i % 5],
                                 "actor": "test",
                                 "text": f"entry {i}: region {i % 17} grew {i % 9}% apart",
                                 "refs": {}}, ensure_ascii=False))
    with open(project.journal, "ab") as fh:  # bytes: "\n" line ends on every OS
        fh.write(("\n".join(lines) + "\n").encode("utf-8"))
    for i in range(runs_n):
        run_id = f"20260901-{9 + i // 3600:02d}{(i // 60) % 60:02d}{i % 60:02d}-run-{i:03d}"
        ds = datasets[i % datasets_n]
        run_dir = project.runs / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        core.write_json(run_dir / "run.json", {
            "schema": "lab.run/1", "id": run_id, "title": f"run {i}", "status": "ok",
            "started_at": (CLOCK_START + timedelta(seconds=i)).isoformat(),
            "duration_s": 1.0, "script": {"path": f"scripts/s{i % 10}.py", "sha256": "0" * 64},
            "inputs": [{"path": ds["path"], "sha256": ds["sha256"], "via": ["lab.read"]}],
            "outputs": [], "charts": [{"id": f"c{i % 10}", "title": f"chart {i % 10}",
                                       "kind": "matplotlib", "via": "lab.chart", "data": None,
                                       "files": {}}], "warnings": []})
    built = SimpleNamespace(project=project, facts={}, truth=Truth(project.context, None),
                            base=project.root.parent)
    fixture_info(ctx, spec, built)
    texts = measure_context(ctx, spec, built)
    block, status = built.entries["S1"], built.entries["S4"]
    journal = ctx.host.read_paged(project.journal)  # all of it, as a model would page through it
    ctx.scale.append({"fixture": spec.id, "events": events_n, "datasets": datasets_n,
                      "runs": runs_n, "journal_utf8_bytes": project.journal.stat().st_size,
                      "journal_read_host_calls": journal.calls,
                      "S1": block["size"], "S1_ms": block["elapsed_ms"],
                      "S4": status["size"], "S4_ms": status["elapsed_ms"],
                      "S4_length_cuts": status["length_cuts"]})
    omission_figures(ctx, spec, texts, built)


def measure_baseline(ctx: Ctx, spec: Spec) -> None:
    project = ctx.project(spec, title="새 분석")
    built = SimpleNamespace(project=project, facts={}, truth=Truth(project.context, None),
                            base=project.root.parent)
    fixture_info(ctx, spec, built)
    measure_context(ctx, spec, built)
    host = ctx.host
    target = project.root.parent / "made-by-lab-init"
    host.use(None)
    host.kit_results.append((True, {"ok": True, "workspace": {"path": "report/index.html"}}))
    reply = host.tool("init", {"path": str(target), "title": "새 분석", "goal": "측정"})
    host.use(None)
    ctx.text(spec.id, "S9", reply.text, root=Path(os.path.realpath(target)),
             base=project.root.parent, is_error=reply.is_error,
             environment_sync="synthetic: reported as synced from network")


def fixture_info(ctx: Ctx, spec: Spec, built) -> None:
    root = built.project.root
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if not path.is_file() or rel.startswith((".lab/cache/", "report/")):
            continue
        data = path.read_bytes()
        if path.suffix in (".json", ".jsonl", ".md", ".txt", ".py", ".csv", ".toml"):
            text, _ = normalize(data.decode("utf-8", errors="replace"), root, built.base)
            data = text.encode("utf-8")
        digest.update(rel.encode("utf-8") + b"\0" + data + b"\0")
    ctx.fixtures.append({"id": spec.id, "family": spec.family, "lang": spec.lang,
                         "required": spec.required, "shape": spec.shape, "generator": GENERATOR,
                         "content_sha256": digest.hexdigest(), "root_chars": len(str(root)),
                         "facts": [f.id for f in spec.facts],
                         "judged_invariants": list(spec.invariants) if spec.required else []})


MEASURE = {"F1": measure_context_family, "F1b": measure_context_family,
           "F2": measure_context_family, "F2t": measure_context_family,
           "F2s": measure_context_family, "F3": measure_f3, "F3d": measure_f3d,
           "J": measure_journal, "N": measure_note, "Q": measure_query, "I": measure_import,
           "E": measure_errors, "F2b": measure_long_line, "F4": measure_long_root,
           "SC": measure_scale, "B": measure_baseline}


# --- static surfaces (T) ------------------------------------------------------------------------

def template_form(entries: list[dict]) -> str:
    """The tools as the pinned Qwen chat template renders them: `tool | tojson` for each, one per
    line (tojson as transformers defines it: json.dumps with ', ' and ': ', non-ASCII kept)."""
    return "\n".join(json.dumps(entry, ensure_ascii=False) for entry in entries)


def compact_form(entries: list[dict]) -> str:
    return json.dumps(entries, ensure_ascii=False, separators=(",", ":"))


def measure_static(ctx: Ctx) -> dict:
    from aelix_ai.providers._openai_compat import OpenAICompletionsCompat
    from aelix_ai.providers.openai_completions import convert_tools
    from aelix_coding_agent.cli.agent_context import _tool_section, build_system_prompt
    from aelix_coding_agent.tools import create_all_tools

    host = ctx.host
    lab = list(host.module.TOOLS)
    compat = OpenAICompletionsCompat()
    wire = convert_tools(lab, compat)
    cwd = str(ROOT)
    base = build_system_prompt(cwd, tools=ctx.coding_tools)
    with_lab = build_system_prompt(cwd, tools=[*ctx.coding_tools, *lab])
    block, guidelines = _tool_section(lab)
    s2 = "".join(f"{line}\n" for line in block.split("\n")
                 if any(line.startswith(f"- {t.name}: ") for t in lab))
    s2 += "".join(f"- {g}\n" for g in guidelines)
    ctx.text("T", "S2", s2, s2_equals_prompt_difference=(len(with_lab) - len(base) == len(s2)
                                                        and all(li in with_lab for li in
                                                                s2.splitlines())))
    compact, template = compact_form(wire), template_form(wire)
    ctx.text("T", "S3", compact, variant="compact",
             serialization="json.dumps(convert_tools(TOOLS, OpenAICompletionsCompat()), "
                           "separators=(',', ':'), ensure_ascii=False)",
             strict_field=all("strict" in e["function"] for e in wire),
             compat={"supports_strict_mode": compat.supports_strict_mode})
    ctx.text("T", "S3", template, variant="template",
             serialization="'\\n'.join(json.dumps(entry, ensure_ascii=False)) — the pinned Qwen "
                           "chat template's `tool | tojson`, one tool per line",
             tokens_counted_on="this form")
    ctx.text("T", "host-base-prompt", base, root=ROOT,
             note="build_system_prompt(cwd, tools=read, bash, edit, write): the host's share, "
                  "not the extension's")
    tools = []
    for tool, entry in zip(lab, wire, strict=True):
        params = tool.parameters
        props = params.get("properties", {})
        tools.append({"name": tool.name, "description": size_of(tool.description),
                      "parameters_compact": size_of(json.dumps(params, ensure_ascii=False,
                                                               separators=(",", ":"))),
                      "properties": len(props), "required": len(params.get("required", [])),
                      "enum_values": sum(len(p.get("enum", [])) for p in props.values()),
                      "snippet_chars": len(tool.prompt_snippet),
                      "guidelines": len(tool.prompt_guidelines),
                      "guideline_chars": sum(len(g) for g in tool.prompt_guidelines),
                      "entry_compact": size_of(json.dumps(entry, ensure_ascii=False,
                                                          separators=(",", ":"))),
                      "entry_template": size_of(json.dumps(entry, ensure_ascii=False)),
                      "tokens_template": ctx.counters.count(json.dumps(entry, ensure_ascii=False))})
    every = create_all_tools(cwd)
    every = list(every.values()) if isinstance(every, dict) else list(every)
    host_wire = convert_tools(ctx.coding_tools, compat)
    all_wire = convert_tools(every, compat)
    share = {
        "coding_tools": [t.name for t in ctx.coding_tools],
        "all_tools": [t.name for t in every],
        "coding_compact_bytes": len(compact_form(host_wire).encode("utf-8")),
        "all_compact_bytes": len(compact_form(all_wire).encode("utf-8")),
        "lab_compact_bytes": len(compact.encode("utf-8")),
    }
    lab_bytes = share["lab_compact_bytes"]
    share["lab_share_of_coding_plus_lab"] = round(
        lab_bytes / (share["coding_compact_bytes"] + lab_bytes), 3)
    share["lab_share_of_all_plus_lab"] = round(
        lab_bytes / (share["all_compact_bytes"] + lab_bytes), 3)
    host_share = {"base_prompt": size_of(base), "base_prompt_tokens": ctx.counters.count(base),
                  "tool_definitions_compact": size_of(compact_form(host_wire)),
                  "tool_definitions_template_tokens": ctx.counters.count(template_form(host_wire)),
                  "base_prompt_with_lab_tools": size_of(with_lab)}
    return {"tools": tools, "share": share, "host": host_share,
            "s2": s2, "s3_compact": compact, "s3_template": template}


# --- report ---------------------------------------------------------------------------------------

def entry_of(surfaces: list[dict], fixture: str, surface: str, variant: str | None = None):
    return next((e for e in surfaces if e["fixture"] == fixture and e["surface"] == surface
                 and e["variant"] == variant), None)


def always_on(ctx: Ctx, static: dict) -> list[dict]:
    """S1 + S2 + S3 per fixture: the extension's part of every model request of a prompt."""
    if static is None:
        return []
    s2 = entry_of(ctx.surfaces, "T", "S2")
    s3c = entry_of(ctx.surfaces, "T", "S3", "compact")
    s3t = entry_of(ctx.surfaces, "T", "S3", "template")
    out = []
    for entry in ctx.surfaces:
        if entry["surface"] != "S1" or entry["fixture"] == "T":
            continue
        text = (ctx.output / entry["text_file"]).read_bytes().decode("utf-8")
        joined = static["s2"] + text + "\n" + static["s3_template"]
        parts = [entry, s2, s3t]
        tokens = {cid: token_sum([p["tokens"][cid] for p in parts]) for cid in entry["tokens"]}
        out.append({
            "fixture": entry["fixture"],
            "utf8_bytes": entry["size"]["utf8_bytes"] + s2["size"]["utf8_bytes"]
            + s3c["size"]["utf8_bytes"],
            "chars": entry["size"]["chars"] + s2["size"]["chars"] + s3c["size"]["chars"],
            "parts_utf8_bytes": {"S1": entry["size"]["utf8_bytes"], "S2": s2["size"]["utf8_bytes"],
                                 "S3_compact": s3c["size"]["utf8_bytes"],
                                 "S3_template": s3t["size"]["utf8_bytes"]},
            "tokens_sum_of_parts": tokens,
            "tokens_concatenated": ctx.counters.count(joined),
            "concatenated": "S2 + S1 + newline + S3 (template form); S3 bytes in utf8_bytes are "
                            "the compact form",
            "host_share": {"base_prompt_utf8_bytes": static["host"]["base_prompt"]["utf8_bytes"],
                           "tool_definitions_compact_utf8_bytes":
                               static["host"]["tool_definitions_compact"]["utf8_bytes"]},
            "label": "extension-attributable; the host's share is recorded next to it, not added"})
    return out


def task_cost(ctx: Ctx, always: list[dict]) -> list[dict]:
    """A model-free proxy of what reading a required fact back costs, per fact and route."""
    by_fixture = {a["fixture"]: a for a in always}
    out = []
    for fact in ctx.facts:
        if not fact["required"]:
            continue
        a = by_fixture.get(fact["fixture"])
        always_bytes = a["utf8_bytes"] if a else None
        first = fact["first"] != "S1"
        responses = [fact["first_text_bytes"]] if first else []
        response_tokens = [fact.get("first_text_tokens") or {}] if first else []
        for s in fact["route"]:
            pages = s.get("pages") or []
            responses += [p["utf8_bytes"] for p in pages] or [s["utf8_bytes"]]
            response_tokens += [p["tokens"] for p in pages] or [{}]
        requests = 1 + len(responses)
        cost = {"fixture": fact["fixture"], "fact": fact["fact"], "class": fact["class"],
                "requests": requests, "tool_calls": len(responses),
                "delivered_utf8_bytes": sum(responses), "always_on_utf8_bytes": always_bytes}
        if always_bytes is not None:
            cost["resend_utf8_bytes"] = requests * always_bytes
            cost["no_compaction_input_utf8_bytes"] = sum(
                always_bytes + sum(responses[:i]) for i in range(requests))
            cost["tokens_approximate"] = {
                cid: cost_tokens(value, [t.get(cid) for t in response_tokens], requests)
                for cid, value in a["tokens_concatenated"].items()}
        out.append(cost)
    return out


def cost_tokens(always: dict, responses: list[dict | None], requests: int) -> dict:
    """The proxy's sums in one counter's tokens: each text counted on its own and added up, so
    approximate (a counter does not split joined texts at the same places). A sum is a number
    only when every part is one."""
    parts = [r or not_measured("not_counted") for r in responses]
    nothing = measured(0) if always.get("status") == "measured" else dict(always)

    def total(values: list[dict]) -> dict:
        return token_sum(values) if values else dict(nothing)

    return {"delivered": total(parts), "always_on": dict(always),
            "resend": total([always] * requests),
            "no_compaction_input": total([always] * requests
                                         + [p for i in range(requests) for p in parts[:i]])}


def compare_targets(ctx: Ctx, always: list[dict]) -> list[dict]:
    out = []
    for entry in ctx.surfaces:
        if entry["surface"] == "S1" and entry["fixture"] != "T":
            tokens = {cid: compare(v, {"general": 512, "reduced": 256})
                      for cid, v in entry["tokens"].items()}
            out.append({"target": "automatic_start_summary", "fixture": entry["fixture"],
                        "surface": "S1", "utf8_bytes": {
                            "actual": entry["size"]["utf8_bytes"], "target": 1536,
                            "exceeds": entry["size"]["utf8_bytes"] > 1536}, "tokens": tokens})
        if entry["surface"] == "S4":
            out.append({"target": "general_query", "fixture": entry["fixture"], "surface": "S4",
                        "utf8_bytes": {"actual": entry["size"]["utf8_bytes"], "target": 3072,
                                       "exceeds": entry["size"]["utf8_bytes"] > 3072},
                        "provisional": True})
    for a in always:
        out.append({"target": "always_on_input", "fixture": a["fixture"], "surface": "S1+S2+S3",
                    "tokens": {cid: compare(v, {"general": 2048})
                               for cid, v in a["tokens_concatenated"].items()},
                    "context_window_share": "not compared: 2,048 tokens is below 15% of each pinned "
                                            "window (131,072 and 1,050,000 tokens, Issue #40)"})
    pages = []
    for fact in ctx.facts:
        for s in fact.get("route", []):
            for page in s.get("pages", []):
                pages.append({"fixture": fact["fixture"], "fact": fact["fact"], "call": s["call"],
                              "utf8_bytes": page["utf8_bytes"],
                              "tokens": {cid: compare(v, {"general": 2048, "reduced": 1536})
                                         for cid, v in page["tokens"].items()}})
    out.append({"target": "explicit_detail_page", "surface": "follow-up read", "pages": pages})
    out.append({"target": "search_or_relation_page", "status": "not_applicable"})
    out.append({"target": "search_results_and_relation_depth", "status": "not_applicable"})
    return out


def compare(value: dict, targets: dict) -> dict:
    if value.get("status") != "measured":
        return dict(value)
    return {"actual": value["value"], **{f"target_{k}": t for k, t in targets.items()},
            **{f"exceeds_{k}": value["value"] > t for k, t in targets.items()}}


def recall(ctx: Ctx) -> list[dict]:
    """AC3: required facts read back / all required facts, per family x first surface x language."""
    groups: dict[tuple, list[dict]] = {}
    for fact in ctx.facts:
        if fact["required"]:
            groups.setdefault((fact["family"], fact["first"], fact["lang"]), []).append(fact)
    return [{"family": family, "surface": surface, "lang": lang,
             "recalled": sum(1 for f in facts if f["passed"]), "total": len(facts)}
            for (family, surface, lang), facts in sorted(groups.items())]


def git(*args: str) -> str | None:
    with contextlib.suppress(Exception):
        done = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, timeout=30,
                              text=True, encoding="utf-8", errors="replace")
        if done.returncode == 0:
            return done.stdout.strip()
    return None


def environment() -> dict:
    def version(name: str) -> str | None:
        with contextlib.suppress(importlib.metadata.PackageNotFoundError):
            return importlib.metadata.version(name)
        return None

    return {"python": platform.python_version(), "implementation": platform.python_implementation(),
            "platform": platform.platform(), "system": platform.system(),
            "machine": platform.machine(),
            "packages": {name: version(name) for name in ("aelix", "aelix-ai", "aelix-agent-core",
                                                          "aelix-coding-agent", "tokenizers")}}


def conventions() -> dict:
    return {
        "size": "chars = Unicode code points; utf8_bytes = UTF-8 bytes; lines = number of \\n plus "
                "one (0 for the empty string); sha256 of the UTF-8 bytes. The final string is "
                "measured whole: headers, ids, references, omission lists and pointers included.",
        "tokens": "{counter id: value}; a value is {status: measured, value} only from a counter "
                  "of this report's registry whose file has the registered sha256; otherwise "
                  "{status: not_measured, reason} or {status: failed, error}. Never derived from "
                  "bytes, characters or an estimate.",
        "root_normalized": "the same text with the project root as <ROOT> and the fixture "
                           "directory as <BASE> (native, POSIX and JSON-escaped spellings), and "
                           "timestamps the measured code added while it was measured as <TIME> "
                           "(the fixtures' own come from a fixed clock and stay)",
        "S1": "the hook's output minus the base prompt it was given (its leading blank line "
              "included); the base prompt is aelix's build_system_prompt for that project",
        "S2": "the tool lines and guidelines aelix renders from the extension's tools into the "
              "system prompt (each normalized to one line of at most 200 characters)",
        "S3": "convert_tools(TOOLS, OpenAICompletionsCompat()) as compact JSON (the size) and as "
              "the pinned Qwen template's tojson lines (the tokens); not a claim about the bytes "
              "an SDK or server sends",
        "length_cuts": "a length-based cut the measured code made in that text, judged against "
                       "what was cut (never by the words a cut adds): status_9000 (the reply's "
                       "overview differs from the kit's status_text with no length limit), "
                       "reply_14000 (the extension's clip returned less than it was given, or "
                       "an analytics_query reply shows fewer rows than the result has), "
                       "preamble_4000 (the block lacks part of CONTEXT.md's text above its first "
                       "heading)",
        "retrieval": "R0 in the first text; R1 through one pointer of it, followed with aelix's "
                     "read tool or the tool it names (the read tool's own continuation counted "
                     "as host calls); R2 through a pointer of that result. A false claim on the "
                     "way fails the fact (wrong_pointer). Otherwise: generic_pointer, "
                     "cut_without_explicit_pointer, silent_omission, not_recorded, host_blocked.",
        "task_cost_proxy": "no model: requests = 1 + tool calls (the call that returned the first "
                           "text included); request i carries the always-on bytes and every "
                           "response before it. tokens_approximate gives the same sums in each "
                           "counter's tokens, each text counted on its own and added up (the "
                           "always-on part is its concatenated count): approximate, since a "
                           "counter does not split joined texts at the same places",
        "fixtures": "built by code from fixed synthetic text with the kit's own functions. Files "
                    "the harness writes itself (CONTEXT.md, scripts, logs, CSV and parquet "
                    "stand-ins, PNGs, journal lines of the scale fixtures) are written as UTF-8 "
                    "bytes, so their line ends do not depend on the OS, and a recorded sha256 is "
                    "of the bytes written; files the kit's own functions write (records, journal "
                    "entries, the project template) are written as the kit writes them",
        "clock": f"while a fixture is built and measured, the kit's clock (core.now) is a fixed "
                 f"clock from {CLOCK_START.isoformat()}, one second per call: records the harness "
                 "writes and records the measured code adds while it is measured get the same "
                 "timestamps on every run; any other timestamp is masked as <TIME> in the "
                 "root-normalized text and counted in masked_timestamps. The source_revision "
                 "analytics_status names is made of the files' mtimes, which the clock does not "
                 "fix: it is masked as <REVISION> there and counted in masked_revisions",
    }


def build_report(ctx: Ctx, static: dict, counters: Counters, argv: list[str], specs: list[Spec],
                 elapsed: float) -> dict:
    always = always_on(ctx, static)
    head = git("rev-parse", "HEAD")
    status = git("status", "--porcelain")
    checks = self_checks(ctx, specs)
    return {
        "schema": SCHEMA,
        "generated_by": {"tool": "tools/usage_report.py",
                         "sha256": sha256_bytes(Path(__file__).read_bytes()),
                         "command": ["python", "tools/usage_report.py", *argv],
                         "seconds": round(elapsed, 1)},
        "measured": {"commit": head, "dirty": None if status is None else bool(status),
                     "extension_sha256": sha256_bytes(EXT.read_bytes()),
                     "kit_sha256": ctx.kit.runner.kit_sha()},
        "environment": environment(),
        "conventions": conventions(),
        "counters": counters.describe(),
        "targets": TARGETS,
        "fixtures": ctx.fixtures,
        "surfaces": ctx.surfaces,
        "tool_surface": None if static is None else {
            "tools": static["tools"], "share": static["share"], "host": static["host"]},
        "always_on": always,
        "omissions": ctx.omissions,
        "all_injected": ctx.all_injected,
        "scale": ctx.scale,
        "retrieval": {"facts": ctx.facts, "invariants": ctx.invariants, "recall": recall(ctx)},
        "task_cost_proxy": {"label": "proxy · no model · extension-attributable",
                            "tokens_note": "tokens_approximate: per-text counts added up",
                            "facts": task_cost(ctx, always)},
        "targets_comparison": compare_targets(ctx, always),
        "caveats": [F3_CAVEAT,
                    "The run page is the kit's report.build run without the analysis libraries "
                    "(a reduced build): no highlighted code and no data previews.",
                    "analytics_run, analytics_query and analytics_import replies are rendered by the extension from "
                    "kit results the harness prepares with the kit's own functions; no kit "
                    "command runs. analytics_init's environment sync is reported as done."],
        "checks": checks,
        "failures": ctx.failures,
        "not_run": NOT_RUN + ctx.not_run,
    }


def self_checks(ctx: Ctx, specs: list[Spec]) -> dict:
    sizes_ok = all(size_of((ctx.output / e["text_file"]).read_bytes().decode("utf-8")) == e["size"]
                   for e in ctx.surfaces)
    required = [f for f in ctx.facts if f["required"]]
    judged = [i for i in ctx.invariants if i["judged"]]
    blocks = [e for e in ctx.surfaces if e["surface"] == "S1"]
    replies = [e for e in ctx.surfaces if e["surface"] in ("S4", "S5", "S6", "S7")]
    return {
        "P1_sizes_recomputed_from_saved_texts": sizes_ok,
        "P2_required_facts_read_back": {"passed": sum(f["passed"] for f in required),
                                        "total": len(required)},
        "P2_invariants_hold": {"holding": sum(i["holds"] for i in judged), "total": len(judged)},
        "P3_hook_returned_a_block": all(e.get("hook_returned_block") for e in blocks),
        "P3_tools_replied_without_error": all(not e.get("is_error") for e in replies),
        "P3_pinned_section_whole": all(e.get("pinned_section_in_block") for e in blocks
                                       if "pinned_section_in_block" in e),
        "fixtures_measured": len(ctx.fixtures),
        "fixtures_selected": len(specs),
    }


# --- markdown -----------------------------------------------------------------------------------

def token_cell(value: dict) -> str:
    if value.get("status") == "measured":
        return f"{value['value']:,}"
    if value.get("status") == "not_measured":
        return f"not measured ({value['reason']})"
    return f"failed ({value.get('error', '')[:60]})"


def counted(report: dict) -> str:
    """The counter whose values the tables show: the first registered one."""
    return report["counters"][0]["id"]


def md_table(head: list[str], rows: list[list]) -> list[str]:
    def cell(value) -> str:
        return str(value).replace("|", "\\|").replace("\n", " ")

    out = ["| " + " | ".join(head) + " |", "|" + "|".join("---" for _ in head) + "|"]
    out += ["| " + " | ".join(cell(v) for v in row) + " |" for row in rows]
    return out


def tool_surface_markdown(report: dict, cid: str) -> list[str]:
    share = report["tool_surface"]["share"]
    host = report["tool_surface"]["host"]
    lines = [f"- tool definitions: {share['lab_compact_bytes']:,} bytes compact; aelix "
             f"{', '.join(share['coding_tools'])}: {share['coding_compact_bytes']:,} bytes "
             f"(lab share {share['lab_share_of_coding_plus_lab']}); with "
             f"{', '.join(share['all_tools'][4:])}: {share['all_compact_bytes']:,} bytes (lab "
             f"share {share['lab_share_of_all_plus_lab']})",
             f"- host share: base prompt {host['base_prompt']['utf8_bytes']:,} bytes, host tool "
             f"definitions {host['tool_definitions_compact']['utf8_bytes']:,} bytes", ""]
    lines += md_table(["tool", "description chars", "properties", "required", "enum values",
                       "entry bytes (compact)", "entry bytes (template)", "tokens (template)"],
                      [[t["name"], t["description"]["chars"], t["properties"], t["required"],
                        t["enum_values"], t["entry_compact"]["utf8_bytes"],
                        t["entry_template"]["utf8_bytes"], token_cell(t["tokens_template"][cid])]
                       for t in report["tool_surface"]["tools"]])
    return lines


def render_markdown(report: dict) -> str:
    """The Markdown report, made from the JSON report and nothing else."""
    cid = counted(report)
    gen, measured_at = report["generated_by"], report["measured"]
    env = report["environment"]
    lines = ["# Usage report — context, tool replies and retrieval", "",
             "generated by tools/usage_report.py — do not edit", "",
             f"- command: `{' '.join(gen['command'])}`",
             f"- commit: `{measured_at['commit']}` (dirty: {measured_at['dirty']}), extension "
             f"sha256 `{measured_at['extension_sha256'][:16]}`, kit sha256 "
             f"`{measured_at['kit_sha256'][:16]}`",
             f"- environment: Python {env['python']} ({env['platform']}), aelix "
             f"{env['packages'].get('aelix')}, tokenizers {env['packages'].get('tokenizers')}",
             f"- schema: `{report['schema']}`", ""]
    checks = report["checks"]
    lines += ["## Summary", "",
              f"- required facts read back: {checks['P2_required_facts_read_back']['passed']} of "
              f"{checks['P2_required_facts_read_back']['total']}",
              f"- judged invariants holding: {checks['P2_invariants_hold']['holding']} of "
              f"{checks['P2_invariants_hold']['total']}",
              f"- sizes recomputed from the saved texts: "
              f"{checks['P1_sizes_recomputed_from_saved_texts']}",
              f"- hook returned a block every time: {checks['P3_hook_returned_a_block']}; tools "
              f"replied without error: {checks['P3_tools_replied_without_error']}",
              f"- failures: {len(report['failures'])}", ""]
    lines += ["## Counters", ""]
    lines += md_table(["counter", "state", "source"], [
        [c["id"], token_cell(c["state"]) if c["state"].get("status") != "in_use" else "in use",
         c.get("source") or c.get("note") or "-"] for c in report["counters"]])
    lines += ["", "## Surfaces", "",
              f"Sizes of the exact strings; tokens by `{cid}`. Root-normalized bytes replace the "
              "project root with <ROOT>.", ""]
    rows = []
    for e in report["surfaces"]:
        name = e["surface"] + (f" ({e['variant']})" if e["variant"] else "")
        rows.append([e["fixture"], name, f"{e['size']['chars']:,}", f"{e['size']['utf8_bytes']:,}",
                     f"{e['size']['lines']:,}",
                     f"{e['root_normalized']['size']['utf8_bytes']:,}",
                     token_cell(e["tokens"][cid]), ", ".join(e["length_cuts"]) or "-"])
    lines += md_table(["fixture", "surface", "chars", "bytes", "lines", "bytes (<ROOT>)",
                       "tokens", "length cuts"], rows)
    lines += ["", "## Tool surface (T)", ""]
    if report["tool_surface"] is None:
        lines.append("not measured: see Failures")
    else:
        lines += tool_surface_markdown(report, cid)
    lines += ["", "## Always-on input (S1 + S2 + S3)", "",
              "Extension-attributable. Token sums of the parts are approximate; the concatenated "
              "count is of one string.", ""]
    lines += md_table(["fixture", "bytes", "S1", "S2", "S3 (compact)", "tokens (sum of parts)",
                       "tokens (concatenated)"],
                      [[a["fixture"], f"{a['utf8_bytes']:,}", f"{a['parts_utf8_bytes']['S1']:,}",
                        f"{a['parts_utf8_bytes']['S2']:,}",
                        f"{a['parts_utf8_bytes']['S3_compact']:,}",
                        token_cell(a["tokens_sum_of_parts"][cid]),
                        token_cell(a["tokens_concatenated"][cid])] for a in report["always_on"]])
    retrieval = report["retrieval"]
    lines += ["", "## Retrieval (AC3)", ""]
    lines += md_table(["family", "first surface", "lang", "required facts read back"],
                      [[r["family"], r["surface"], r["lang"], f"{r['recalled']} / {r['total']}"]
                       for r in retrieval["recall"]])
    lines += ["", "### Facts", ""]
    lines += md_table(["fixture", "fact", "required", "first", "class", "host calls",
                       "false claims"],
                      [[f["fixture"] + (f" ({f['variant']})" if f.get("variant") else ""),
                        f["fact"], "yes" if f["required"] else "report only", f["first"],
                        f["class"], f["host_calls"],
                        "; ".join(c["claim"] for c in f["claims"] if not c["holds"]) or "-"]
                       for f in retrieval["facts"]])
    lines += ["", "### Invariants", ""]
    lines += md_table(["fixture", "surface", "judged", "holds", "failing checks"],
                      [[i["fixture"], i["surface"], "yes" if i["judged"] else "report only",
                        "yes" if i["holds"] else "no",
                        "; ".join(c["name"] for c in i["checks"] if not c["holds"]) or "-"]
                       for i in retrieval["invariants"]])
    cost = report["task_cost_proxy"]
    lines += ["", f"## Task cost ({cost['label']})", ""]
    lines += [f"Tokens by `{cid}` are approximate: each text is counted on its own and the "
              "counts are added up.", ""]
    lines += md_table(["fixture", "fact", "class", "requests", "delivered bytes",
                       "always-on bytes", "resend bytes", "input bytes without compaction",
                       "delivered tokens (approx.)", "input tokens without compaction (approx.)"],
                      [[c["fixture"], c["fact"], c["class"], c["requests"],
                        f"{c['delivered_utf8_bytes']:,}",
                        f"{c['always_on_utf8_bytes']:,}" if c["always_on_utf8_bytes"] else "-",
                        f"{c.get('resend_utf8_bytes', 0):,}",
                        f"{c.get('no_compaction_input_utf8_bytes', 0):,}",
                        *((token_cell(t["delivered"]), token_cell(t["no_compaction_input"]))
                          if (t := (c.get("tokens_approximate") or {}).get(cid)) else ("-", "-"))]
                       for c in cost["facts"]])
    lines += ["", "## Everything injected, for comparison", ""]
    rows = []
    for a in report["all_injected"]:
        for key, value in a.items():
            if isinstance(value, dict) and "utf8_bytes" in value:
                rows.append([a["fixture"], key, f"{value['utf8_bytes']:,}"])
            elif key.endswith("_utf8_bytes"):
                rows.append([a["fixture"], key, f"{value:,}"])
    lines += md_table(["fixture", "what", "bytes"], rows)
    lines += ["", "## Targets (report only: never a failure, a refusal or a cut)", ""]
    rows = []
    for t in report["targets_comparison"]:
        if t.get("status") == "not_applicable":
            rows.append([t["target"], "-", "-", "not applicable", "-"])
        elif t["target"] == "explicit_detail_page":
            measured_pages = [p for p in t["pages"] if p["tokens"][cid].get("actual") is not None]
            rows.append([t["target"], f"{len(t['pages'])} pages", "-",
                         f"{sum(1 for p in measured_pages if p['tokens'][cid]['exceeds_general'])}"
                         f" of {len(measured_pages)} measured pages over 2,048 tokens"
                         if measured_pages else token_cell(t["pages"][0]["tokens"][cid])
                         if t["pages"] else "-", "-"])
        else:
            b = t.get("utf8_bytes") or {}
            tok = (t.get("tokens") or {}).get(cid)
            rows.append([t["target"], t["fixture"],
                         f"{b['actual']:,} / {b['target']:,}" + (" over" if b["exceeds"] else "")
                         if b else "-",
                         (f"{tok['actual']:,}" + "".join(
                             f" / {v:,}" for k, v in tok.items() if k.startswith("target_"))
                          if tok and "actual" in tok else token_cell(tok) if tok else "-"),
                         t.get("context_window_share") or ("provisional" if t.get("provisional")
                                                           else "-")])
    lines += md_table(["target", "fixture", "bytes (actual / target)", "tokens (actual / targets)",
                       "note"], rows)
    if report["scale"]:
        lines += ["", "## Scale", ""]
        lines += md_table(["fixture", "events", "datasets", "runs", "S1 bytes", "S1 ms",
                           "S4 bytes", "S4 ms", "S4 cut", "read calls for the whole journal"],
                          [[s["fixture"], f"{s['events']:,}", s["datasets"], s["runs"],
                            f"{s['S1']['utf8_bytes']:,}", s["S1_ms"], f"{s['S4']['utf8_bytes']:,}",
                            s["S4_ms"], ", ".join(s["S4_length_cuts"]) or "-",
                            s["journal_read_host_calls"]] for s in report["scale"]])
    lines += ["", "## Omissions", ""]
    lines += md_table(["fixture", "sections listed in the block", "block pointer list bytes",
                       "analytics_status cut", "omitted-names line bytes"],
                      [[o["fixture"], o["block_sections_listed"],
                        f"{o['block_pointer_list']['utf8_bytes']:,}",
                        "yes" if o["status_cut"] else "no",
                        f"{o['status_omitted_names_line']['utf8_bytes']:,}"]
                       for o in report["omissions"]])
    lines += ["", "## Caveats", ""] + [f"- {c}" for c in report["caveats"]]
    if report["failures"]:
        lines += ["", "## Failures", ""]
        lines += [f"- {f['fixture']}: {f['error'].splitlines()[-1] if f['error'] else ''}"
                  for f in report["failures"]]
    lines += ["", "## Not run", ""]
    lines += md_table(["what", "reason", "where"],
                      [[n["what"], n["reason"], n.get("where", "-")] for n in report["not_run"]])
    return "\n".join(lines) + "\n"


# --- running --------------------------------------------------------------------------------------

def run(output: Path, *, only: list[str] | None = None, counters: list[TokenCounter] | None = None,
        keep_fixtures: bool = False, base: Path | None = None, argv: list[str] | None = None
        ) -> tuple[int, dict]:
    """Build the fixtures, measure, write usage-report.json, usage-report.md and texts/. Returns
    (exit status, report)."""
    started = time.perf_counter()
    specs = select(only)
    output = Path(output)
    # Everything that can refuse the run is checked before anything is deleted.
    if importlib.util.find_spec("aelix_coding_agent") is None:
        raise UsageError("aelix is not installed here: run this in aelix's isolated "
                         "environment (CONTRIBUTING.md)")
    if output.exists() and not output.is_dir():
        raise UsageError(f"{output} is not a directory")
    if output.is_dir() and any(output.iterdir()) and not (output / "usage-report.json").is_file():
        raise UsageError(f"{output} is not empty and holds no usage-report.json: give a new or "
                         "empty directory, or a previous report's (its texts/ and fixtures/ are "
                         "replaced)")
    output.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(output / "texts", ignore_errors=True)
    for name in ("usage-report.json", "usage-report.md"):
        with contextlib.suppress(FileNotFoundError):
            (output / name).unlink()
    given = list(counters or [])
    counter_set = Counters(given)
    with tempfile.TemporaryDirectory(prefix="usage-report-", ignore_cleanup_errors=True) as tmp:
        scratch = Path(tmp)
        if base is None:
            base = output / "fixtures" if keep_fixtures else scratch / "fixtures"
        base = Path(base)
        shutil.rmtree(base, ignore_errors=True)
        base.mkdir(parents=True, exist_ok=True)
        base = Path(os.path.realpath(base))
        with isolated_environment(scratch):
            kit = load_kit()
            ctx = Ctx(kit, counter_set, output, base)
            with contextlib.closing(ctx):  # the host's one event loop ends with the measurement
                static = None
                try:
                    ctx.connect()
                    static = measure_static(ctx)
                except Exception as exc:  # noqa: BLE001 - the report says what could not be measured
                    ctx.failures.append({"fixture": "host" if ctx.host is None else "T",
                                         "error": "".join(traceback.format_exception(exc))[-3000:]})
                for spec in specs if ctx.host is not None else []:
                    print(f"… {spec.id}", flush=True)
                    with fixed_clock(ctx.host.core, kit.core):
                        try:
                            MEASURE[spec.family](ctx, spec)
                        except Exception as exc:  # noqa: BLE001 - a surface that fails is a result
                            ctx.failures.append({"fixture": spec.id, "error": "".join(
                                traceback.format_exception(exc))[-3000:]})
                        finally:
                            ctx.host.use(None)
                            ctx.host.kit_results.clear()
            for spec in SPECS:
                if spec not in specs:
                    ctx.not_run.append({"what": spec.id, "reason": "not selected (--only)"})
            report = build_report(ctx, static, counter_set, list(argv or []), specs,
                                  time.perf_counter() - started)
        if not keep_fixtures and base.is_relative_to(Path(os.path.realpath(scratch))):
            shutil.rmtree(base, ignore_errors=True)
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    (output / "usage-report.json").write_text(text, encoding="utf-8", newline="\n")
    report = json.loads(text)  # the Markdown is made from the JSON as written, and nothing else
    (output / "usage-report.md").write_text(render_markdown(report), encoding="utf-8",
                                            newline="\n")
    code = 1 if report["failures"] or counter_set.failed() else 0
    return code, report


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(description="Measure the extension's context and tool "
                                                 "replies, and whether left-out facts can be read "
                                                 "back.")
    parser.add_argument("--output", required=True,
                        help="directory for the report and texts/: new, empty, or a previous "
                             "report's")
    parser.add_argument("--tokenizer", action="append", default=[], metavar="ID=PATH",
                        help=f"count tokens with a registered counter ({', '.join(COUNTERS)}) "
                             "from its tokenizer.json")
    parser.add_argument("--keep-fixtures", action="store_true",
                        help="keep the fixture projects under OUTPUT/fixtures")
    parser.add_argument("--only", help="comma-separated fixture ids or families, e.g. F1,F3-en")
    args = parser.parse_args(argv)
    only = [n.strip() for n in (args.only or "").split(",") if n.strip()] or None
    try:
        counters = [registered_counter(spec) for spec in args.tokenizer]
        code, report = run(Path(args.output), only=only, counters=counters,
                           keep_fixtures=args.keep_fixtures, argv=argv)
    except UsageError as exc:
        parser.error(str(exc))
    checks = report["checks"]
    print(json.dumps({"output": str(Path(args.output) / "usage-report.json"),
                      "required_facts": checks["P2_required_facts_read_back"],
                      "invariants": checks["P2_invariants_hold"],
                      "failures": len(report["failures"]), "exit": code}, ensure_ascii=False))
    return code


if __name__ == "__main__":
    sys.exit(main())
