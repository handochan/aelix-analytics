"""Q17 — a person's own analysis, then a real agent's handoff (Issue #23): the environment, the
zero-model measurement and the comparison of a project's records before and after each handoff.

    python tools/human_e2e.py prepare OUT [--checkout DIR] [--allow-dirty]
    python tools/human_e2e.py shell OUT [--cwd DIR]
    python tools/human_e2e.py check OUT [--project DIR]
    python tools/human_e2e.py snapshot PROJECT --out FILE
    python tools/human_e2e.py compare BEFORE AFTER [--expect-actor ACTOR] [--out FILE]
    python tools/human_e2e.py agent-env OUT --model ID --project DIR
    python tools/human_e2e.py agent-usage SESSIONS [--out FILE]

What each gate step needs (docs/BETA-QUALITY-GATES.md, Q17):

- **prepare** (macOS, network allowed: this is the installation): the release wheel built from a clean
  checkout, a new HOME, a copy of the uv binary, a uv-managed CPython 3.12 and the wheel installed with
  `uv tool install`, all under OUT/env. A throwaway project made with the installed `analytics init`
  fills OUT/env/uv-cache with the analysis libraries; it and the kit's trust list it wrote are removed
  again. OUT/data/orders.csv is the synthetic data (seeded, no network). OUT/sandbox.sb denies, each
  with OUT's tag: every outbound IP connection (TCP and UDP, the loopback included: a local model
  server is a model too) and every Unix-domain socket connection (the system resolver's among them),
  the execution of the model clients found on the caller's PATH (aelix, codex, claude, ollama,
  llama.cpp, LM Studio, ...) and the reading of their settings, logins and model stores (~/.aelix,
  ~/.codex, ~/.ollama, ~/.cache/huggingface, ...). OUT/prepare.json records what was installed and
  denied, and what the denial does not cover (`not_covered`): a model the analysis code itself loads
  from a file elsewhere, or a client not on PATH, makes no connection to deny.
- **shell**: one session of the person's work. A canary (curl to 127.0.0.1:9 under a profile of its own
  tag) proves first that a denial reaches the unified log; then /bin/zsh -i runs under OUT/sandbox.sb
  with `env -i` and the environment of OUT/prepare.json only (UV_OFFLINE, no Python downloads, managed
  Python only, HOME and the tools of OUT/env; history with times in OUT/env/home/.zsh_history). When
  the shell ends, what it left running with a working directory under OUT is waited for (30 s), then
  stopped; then the tagged denials of the session's window are read from the unified log into
  OUT/sessions/<n>.json.
- **check**: OUT/zero-model.json. For each session: the canary was logged and the log was read (else
  the measurement is unverified, never a zero). The model calls are 0 by construction (no connection
  can leave); the attempts are every tagged denial from prepare until the check, read again then (so
  that what a process left behind tried after its session ended counts too) and joined with the
  sessions' own. A session without its end record, a profile that is not the one prepare wrote and,
  with --project, a record or run not by the agent written outside every session's window fail it.
  What runs outside the sessions (the editor, the browser) is not measured: the guide asks for an
  editor without model features and owner-notes.md records it. Besides: aelix is not importable
  in the installed tool's environment or the project's, the session environment holds no credential
  variable, HOME's .aelix holds nothing but the kit's trust list, and the installed kit imports no
  network client (only socket.gethostname).
- **snapshot / compare**: a project's files by class — immutable (analytics.json, runs/, artifacts/,
  data/imported/, assets/candidates/, .analytics/context/, adhoc/, demo/, publications/, migration/),
  append-only (journal.jsonl and the .analytics/*.jsonl logs, hashed by line with its line end),
  editable (CONTEXT.md, scripts/, data/derived/ and the person's other files) and the
  kit's other state — without workspace/, .venv/, the cache and the locks; the journal's events (id,
  seq, kind, actor, actor_basis, means, whether a session is named, refs), the runs and the Report
  versions. compare passes when every immutable file is byte for byte the same, every append-only one
  starts with the earlier lines, the project is the same one and (with --expect-actor) every new
  event, run and Report version but the kit's own `system` events has that actor; it lists what is new
  and what was edited.
- **agent-env**: the separate aelix environment of Q17-b for one model of
  tools/model_eval_data/models: aelix 0.1.0b2 with the same wheel (`uv tool install`), a new agent
  directory with the model's models.json, a new HOME, a copy of the project (without .venv,
  workspace/ and the cache), its snapshot, and OUT/agents/<model>/start.sh, which starts aelix in the
  copy: through tools/model_eval_aelix.py with the caller's own aelix login for a model that uses one
  (the login is opened in place, never copied or linked), else aelix itself.
- **agent-usage**: the tokens of an aelix session directory by tools/model_eval.py's conventions, the
  requests and the tools called; nothing of the messages' text.

Nothing here is a sandbox for the agent of Q17-b: its session runs as the person's aelix would. Its
session files and the project copies stay local; only counts, hashes and ids go into a record.
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import csv
import hashlib
import importlib.util
import io
import json
import os
import platform
import random
import re
import secrets
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(__file__).resolve().parent / "human_e2e_data"
MODELS = Path(__file__).resolve().parent / "model_eval_data" / "models"
LAUNCHER = Path(__file__).resolve().parent / "model_eval_aelix.py"
AELIX = "aelix==0.1.0b2"
PYTHON = "3.12"
SNAPSHOT_SCHEMA = "aelix-analytics.q17-snapshot/1"
# Programs that call a model, looked for on the caller's PATH, and the directories of their settings
# and logins under the caller's home.
MODEL_CLIENTS = ("aelix", "codex", "claude", "ollama", "gemini", "opencode", "aider", "llm",
                 "cursor-agent", "goose", "qwen", "llama-cli", "llama-server", "llama-run", "llamafile",
                 "lms", "mlx_lm.generate", "mlx_lm.server", "vllm", "gpt4all", "jan")
MODEL_HOMES = (".aelix", ".codex", ".claude", ".ollama", ".gemini", ".config/opencode",
               ".config/aelix", ".local/share/uv/tools/aelix", ".cache/uv", ".cache/huggingface",
               ".cache/lm-studio", ".lmstudio", ".cache/llama.cpp", ".cache/gpt4all",
               "Library/Application Support/LM Studio", "Library/Application Support/Jan")
NOT_COVERED = ("a model the analysis code loads itself from a file outside the denied stores (no "
               "connection or listed program to deny)", "a model client that is not on the caller's PATH "
               "when prepare runs", "what a process left running with its working directory outside OUT "
               "tries after the check")
RESOLVER = "/private/var/run/mDNSResponder"
CREDENTIAL = re.compile(r"TOKEN|KEY|SECRET|PASSWORD|CREDENTIAL|COOKIE", re.I)
DENIAL = re.compile(r"Sandbox: (?P<process>.+?)\((?P<pid>\d+)\) deny\(\d+\) (?P<operation>\S+)\s*(?P<target>[^\n]*)")
# The kit's network use: only the host name of the publication build's record. Imports are read with
# ast (`from urllib import request` and importlib.import_module("http.client") count); asyncio counts
# by its network functions only (the extension uses it for subprocesses).
NETWORK_MODULES = ("socket", "ssl", "urllib.request", "http.client", "http.server", "ftplib", "smtplib",
                   "xmlrpc", "websocket", "websockets", "requests", "httpx", "aiohttp", "urllib3")
ASYNC_NETWORK = re.compile(r"\b(open_connection|open_unix_connection|start_server|create_connection|"
                           r"create_datagram_endpoint|sock_connect)\b")


class Fail(Exception):
    pass


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def run(argv, *, env=None, cwd=None, check=True, capture=True) -> subprocess.CompletedProcess:
    done = subprocess.run([str(a) for a in argv], env=env, cwd=cwd, text=True, encoding="utf-8",
                          errors="replace", capture_output=capture)
    if check and done.returncode != 0:
        raise Fail(f"{shlex.join(str(a) for a in argv)} failed ({done.returncode}):\n"
                   f"{(done.stderr or '')[-2000:]}")
    return done


# --- the synthetic data ---------------------------------------------------------------------------

REGIONS = {"서울": (180, 23_000), "부산": (110, 21_000), "대구": (80, 20_000), "광주": (60, 19_500)}
CHANNELS = {"앱": 0.6, "웹": 0.4}
FIRST, LAST = date(2026, 6, 1), date(2026, 9, 15)
BUSAN_WEB_DROP = date(2026, 7, 20)


def orders_csv(seed: int = 23) -> str:
    """Daily orders of an online shop, 2026-06-01 to 2026-09-15 (September is partial on purpose).
    From 2026-07-20 Busan's web orders fall by about 35%; Daegu's app refunds triple in August."""
    rng = random.Random(seed)
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(["date", "region", "channel", "orders", "revenue", "refunds"])
    day = FIRST
    while day <= LAST:
        weekend = 1.15 if day.weekday() >= 5 else 1.0
        for region, (base, price) in REGIONS.items():
            for channel, share in CHANNELS.items():
                mean = base * share * weekend
                if region == "부산" and channel == "웹" and day >= BUSAN_WEB_DROP:
                    mean *= 0.65
                orders = max(0, round(rng.gauss(mean, mean * 0.08)))
                revenue = round(orders * price * rng.uniform(0.97, 1.03))
                rate = 0.09 if (region == "대구" and channel == "앱" and day.month == 8) else 0.03
                refunds = sum(1 for _ in range(orders) if rng.random() < rate)
                w.writerow([day.isoformat(), region, channel, orders, revenue, refunds])
        day += timedelta(days=1)
    return out.getvalue()


# --- prepare --------------------------------------------------------------------------------------

def layout(out: Path) -> dict:
    env = out / "env"
    return {"env": env, "home": env / "home", "bin": env / "bin", "tools": env / "tools",
            "python": env / "python", "cache": env / "uv-cache", "tmp": env / "tmp",
            "zdot": env / "zdot", "work": out / "work", "data": out / "data", "dist": out / "dist",
            "sessions": out / "sessions"}


def session_env(paths: dict, *, offline: bool) -> dict:
    env = {"HOME": str(paths["home"]), "PATH": f"{paths['bin']}:/usr/bin:/bin:/usr/sbin:/sbin",
           "TMPDIR": str(paths["tmp"]) + "/", "LANG": "ko_KR.UTF-8", "LC_ALL": "ko_KR.UTF-8",
           "UV_TOOL_DIR": str(paths["tools"]), "UV_TOOL_BIN_DIR": str(paths["bin"]),
           "UV_CACHE_DIR": str(paths["cache"]), "UV_PYTHON_INSTALL_DIR": str(paths["python"]),
           "UV_PYTHON_PREFERENCE": "only-managed", "UV_NO_CONFIG": "1",
           "XDG_CONFIG_HOME": str(paths["home"] / ".config"),
           "XDG_DATA_HOME": str(paths["home"] / ".local" / "share"),
           "XDG_CACHE_HOME": str(paths["home"] / ".cache")}
    if offline:
        env.update(UV_OFFLINE="1", UV_PYTHON_DOWNLOADS="never", ZDOTDIR=str(paths["zdot"]))
    return env


def sbpl_string(text: str) -> str:
    return json.dumps(text, ensure_ascii=False)


def sandbox_profile(tag: str, execs: list[str], reads: list[str]) -> str:
    lines = ["(version 1)", "(allow default)",
             f"(deny network-outbound (remote ip) (with message {sbpl_string(tag)}))",
             f"(deny network-outbound (remote unix-socket) (with message {sbpl_string(tag)}))",
             f"(deny network-outbound (literal {sbpl_string(RESOLVER)}) (with message {sbpl_string(tag)}))"]
    if execs:
        literal = " ".join(f"(literal {sbpl_string(p)})" for p in execs)
        lines.append(f"(deny process-exec {literal} (with message {sbpl_string(tag)}))")
    if reads:
        subpath = " ".join(f"(subpath {sbpl_string(p)})" for p in reads)
        lines.append(f"(deny file-read* {subpath} (with message {sbpl_string(tag)}))")
    return "\n".join(lines) + "\n"


def model_clients() -> list[str]:
    """Every model client on the caller's PATH, by its name and by what it resolves to."""
    found: set[str] = set()
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        for name in MODEL_CLIENTS:
            candidate = Path(entry) / name
            if candidate.exists():
                found.add(str(candidate))
                found.add(str(candidate.resolve()))
    return sorted(found)


def model_homes() -> list[str]:
    home = Path.home()
    return sorted(str((home / rel).resolve()) for rel in MODEL_HOMES if (home / rel).exists())


def checkout_state(checkout: Path) -> dict:
    head = run(["git", "-C", checkout, "rev-parse", "HEAD"]).stdout.strip()
    dirty = run(["git", "-C", checkout, "status", "--porcelain", "--untracked-files=no"]).stdout
    kit = re.search(r'^KIT_VERSION = "([^"]+)"',
                    (checkout / "src/aelix_analytics/core.py").read_text(encoding="utf-8"), re.M)
    return {"path": str(checkout), "commit": head, "dirty": bool(dirty.strip()),
            "kit_version": kit.group(1) if kit else None}


ZSHRC = """\
HISTFILE={history}
HISTSIZE=100000
SAVEHIST=100000
setopt EXTENDED_HISTORY INC_APPEND_HISTORY
PROMPT='[q17a] %~ %# '
print -r -- "Q17-a: 네트워크·모델 프로그램·모델 설정이 막힌 셸입니다. 끝내려면 exit."
"""


def prepare(out: Path, checkout: Path, allow_dirty: bool) -> dict:
    if sys.platform != "darwin":
        raise Fail("prepare uses macOS's sandbox-exec and unified log: run it on macOS")
    if out.exists() and any(out.iterdir()):
        raise Fail(f"{out} is not empty")
    state = checkout_state(checkout)
    if state["dirty"] and not allow_dirty:
        raise Fail(f"{checkout} has uncommitted changes: the release wheel is built from a clean checkout")
    host_uv = shutil.which("uv")
    if host_uv is None:
        raise Fail("uv is not on PATH")
    paths = layout(out)
    for key in ("home", "bin", "tools", "python", "cache", "tmp", "zdot", "work", "data", "dist", "sessions"):
        paths[key].mkdir(parents=True, exist_ok=True)
    uv = paths["bin"] / "uv"
    shutil.copy2(Path(host_uv).resolve(), uv)
    env = session_env(paths, offline=False)
    run([uv, "build", "--wheel", "--out-dir", paths["dist"], checkout], env=env, cwd=checkout)
    wheel = next(paths["dist"].glob("aelix_analytics-*.whl"))
    run([uv, "python", "install", PYTHON], env=env)
    run([uv, "tool", "install", "--python", PYTHON, wheel], env=env)
    tool_python = paths["tools"] / "aelix-analytics" / "bin" / "python"
    warm = out / "warm"
    run([paths["bin"] / "analytics", "init", warm, "--title", "warm"], env=env)
    packages = json.loads(run([uv, "pip", "list", "--format", "json", "--python", warm / ".venv" / "bin" / "python"],
                              env=env).stdout)
    shutil.rmtree(warm)
    shutil.rmtree(paths["home"] / ".aelix", ignore_errors=True)  # the trust list the warm project wrote
    data = paths["data"] / "orders.csv"
    data.write_text(orders_csv(), encoding="utf-8")
    tag = f"q17a-deny-{secrets.token_hex(4)}"
    canary = f"q17a-canary-{secrets.token_hex(4)}"
    execs, reads = model_clients(), model_homes()
    (out / "sandbox.sb").write_text(sandbox_profile(tag, execs, reads), encoding="utf-8")
    (out / "canary.sb").write_text(sandbox_profile(canary, [], []), encoding="utf-8")
    (paths["zdot"] / ".zshrc").write_text(ZSHRC.format(history=paths["home"] / ".zsh_history"), encoding="utf-8")
    shutil.copy2(DATA / "owner-notes.md", out / "owner-notes.md")
    record = {
        "schema": "aelix-analytics.q17-prepare/1", "prepared_at": now(), "checkout": state,
        "machine": {"platform": platform.platform(), "machine": platform.machine()},
        "wheel": {"file": wheel.name, "sha256": sha256_file(wheel), "bytes": wheel.stat().st_size},
        "uv": {"copied_from": str(Path(host_uv).resolve()), "version": run([uv, "--version"], env=env).stdout.strip()},
        "python": run([tool_python, "-c", "import platform; print(platform.python_version())"], env=env).stdout.strip(),
        "analysis_packages": {p["name"]: p["version"] for p in packages},
        "cache_bytes": sum(f.stat().st_size for f in paths["cache"].rglob("*") if f.is_file() and not f.is_symlink()),
        "data": {"file": data.relative_to(out).as_posix(), "sha256": sha256_file(data),
                 "rows": data.read_text(encoding="utf-8").count("\n") - 1, "seed": 23},
        "sandbox": {"tag": tag, "canary_tag": canary, "profile_sha256": sha256_file(out / "sandbox.sb"),
                    "canary_sha256": sha256_file(out / "canary.sb"),
                    "denied_network": "every outbound IP connection (loopback included) and every Unix-domain "
                                      "socket connection (the resolver's included)",
                    "denied_exec": execs, "denied_read": reads, "not_covered": list(NOT_COVERED)},
        "session_env": session_env(paths, offline=True),
    }
    write_json(out / "prepare.json", record)
    return record


# --- shell ----------------------------------------------------------------------------------------

def log_time(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%d %H:%M:%S")


def read_denials(start: datetime, end: datetime, tags: list[str]) -> dict:
    predicate = " OR ".join(f'eventMessage CONTAINS "{t}"' for t in tags)
    argv = ["/usr/bin/log", "show", "--start", log_time(start), "--end", log_time(end),
            "--style", "ndjson", "--predicate", predicate]
    try:
        done = run(argv, check=False)
    except OSError as exc:  # no unified log here: nothing was read, which is never a zero
        done = subprocess.CompletedProcess(argv, 127, "", f"not started: {exc}")
    found: dict[str, list] = {t: [] for t in tags}
    for line in done.stdout.splitlines():
        with contextlib.suppress(json.JSONDecodeError):
            event = json.loads(line)
            message = event.get("eventMessage") or ""
            match = DENIAL.search(message)
            for t in tags:
                if t in message and match:
                    found[t].append({"time": event.get("timestamp"), "process": match["process"],
                                     "pid": int(match["pid"]), "operation": match["operation"],
                                     "target": match["target"].strip()})
    return {"command": shlex.join(argv), "returncode": done.returncode, "found": found}


def left_running(out: Path) -> list[dict]:
    """The caller's processes whose working directory is under OUT, but this one (lsof)."""
    done = run(["lsof", "-a", "-u", str(os.getuid()), "-d", "cwd", "-Fpcn"], check=False)
    found, pid, command = [], None, None
    for line in done.stdout.splitlines():
        kind, value = line[:1], line[1:]
        if kind == "p":
            pid = int(value)
        elif kind == "c":
            command = value
        elif kind == "n" and pid not in (os.getpid(), os.getppid()):
            with contextlib.suppress(ValueError):
                if Path(value).resolve().is_relative_to(out.resolve()):
                    found.append({"pid": pid, "command": command, "cwd": value})
    return found


def end_left_running(out: Path, wait: float = 30.0) -> list[dict]:
    """Wait for what the session left running, then stop it: its attempts must fall in the window."""
    deadline = time.monotonic() + wait
    seen: dict[int, dict] = {}
    left = left_running(out)
    while left:
        for proc in left:
            seen.setdefault(proc["pid"], {**proc, "action": "waited for"})
        if time.monotonic() >= deadline:
            break
        time.sleep(1)
        left = left_running(out)
    for proc in left:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(proc["pid"], signal.SIGTERM)
        seen[proc["pid"]]["action"] = "SIGTERM"
    if left:
        time.sleep(5)
        for proc in left_running(out):
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.kill(proc["pid"], signal.SIGKILL)
            seen.setdefault(proc["pid"], proc)["action"] = "SIGKILL"
    return list(seen.values())


def reserve_session(sessions: Path) -> int:
    """The next session number, taken with O_EXCL so that two shells never share one."""
    number = len(list(sessions.glob("*.json"))) + 1
    while True:
        try:
            os.close(os.open(sessions / f"{number}.json", os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            return number
        except FileExistsError:
            number += 1


def shell(out: Path, cwd: Path | None, script: Path | None = None) -> dict:
    record = read_json(out / "prepare.json")
    paths = layout(out)
    tag, canary = record["sandbox"]["tag"], record["sandbox"]["canary_tag"]
    number = reserve_session(paths["sessions"])
    start = datetime.now().astimezone() - timedelta(seconds=1)
    probe = run(["sandbox-exec", "-f", out / "canary.sb", "/usr/bin/curl", "-s", "-m", "2", "http://127.0.0.1:9/"],
                check=False)
    env = record["session_env"]
    argv = ["sandbox-exec", "-f", str(out / "sandbox.sb"), "/usr/bin/env", "-i",
            *[f"{k}={v}" for k, v in env.items()], f"TERM={os.environ.get('TERM', 'xterm-256color')}",
            "/bin/zsh", *(["-i"] if script is None else [str(script)])]
    started = now()
    target = paths["sessions"] / f"{number}.json"
    write_json(target, {"schema": "aelix-analytics.q17-session/1", "number": number, "started_at": started,
                        "ended_at": None, "cwd": str(cwd or paths["work"])})
    # Ctrl+C is the shell's; a closed terminal (SIGHUP) ends the shell, and this still records the session.
    previous = {sig: signal.signal(sig, signal.SIG_IGN) for sig in (signal.SIGINT, signal.SIGHUP)}
    try:
        code = subprocess.call(argv, cwd=cwd or paths["work"])
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    ended = now()
    left = end_left_running(out)
    time.sleep(2)  # the unified log is written asynchronously
    end = datetime.now().astimezone() + timedelta(seconds=1)
    denials = read_denials(start, end, [tag, canary])
    session = {"schema": "aelix-analytics.q17-session/1", "number": number, "started_at": started,
               "ended_at": ended, "exit_code": code, "cwd": str(cwd or paths["work"]), "left_running": left,
               "script": None if script is None else {"file": str(script), "sha256": sha256_file(script)},
               "env_names": sorted(env), "canary": {"curl_returncode": probe.returncode,
                                                    "logged": len(denials["found"][canary])},
               "denials": denials["found"][tag], "log": {"command": denials["command"],
                                                         "returncode": denials["returncode"]}}
    write_json(target, session)
    with contextlib.suppress(OSError):  # the terminal may be gone
        print(f"session {number}: {len(session['denials'])} denial(s), canary logged "
              f"{session['canary']['logged']} time(s), log {'read' if denials['returncode'] == 0 else 'NOT read'}, "
              f"{len(left)} process(es) left running (waited for or stopped) -> {target}")
    return session


# --- check ----------------------------------------------------------------------------------------

def importable(python: Path, module: str, env: dict) -> bool | None:
    if not python.exists():
        return None
    done = run([python, "-c", f"import importlib.util, sys; sys.exit(0 if importlib.util.find_spec({module!r}) else 3)"],
               env=env, check=False)
    return done.returncode == 0


def network_module(name: str) -> str | None:
    return next((m for m in NETWORK_MODULES if name == m or name.startswith(m + ".")), None)


def imported_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.add(node.module)
            names.update(f"{node.module}.{a.name}" for a in node.names)
        elif isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant) \
                and isinstance(node.args[0].value, str):
            func = node.func
            called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if called in ("import_module", "__import__"):
                names.add(node.args[0].value)
    return names


def kit_network_imports(site: Path) -> list[dict]:
    found = []
    for path in sorted((site / "aelix_analytics").rglob("*.py")):
        text = path.read_text(encoding="utf-8", errors="replace")
        rel = path.relative_to(site).as_posix()
        try:
            tree = ast.parse(text)
        except SyntaxError:
            found.append({"file": rel, "module": None, "uses": ["unparsed"]})
            continue
        for module in sorted({m for n in imported_names(tree) if (m := network_module(n))}):
            uses = sorted({node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
                           and isinstance(node.value, ast.Name) and node.value.id == module.split(".")[-1]})
            found.append({"file": rel, "module": module, "uses": uses})
        for name in sorted(set(ASYNC_NETWORK.findall(text))):
            found.append({"file": rel, "module": "asyncio", "uses": [name]})
    return found


def recorded_outside(project: Path, sessions: list[dict], slack: float = 5.0) -> list[str]:
    """The project's events and runs not written by an agent whose time is in no session's window."""
    windows = [(datetime.fromisoformat(s["started_at"]) - timedelta(seconds=slack),
                datetime.fromisoformat(s["ended_at"]) + timedelta(seconds=slack)) for s in sessions]
    snap = snapshot(project)
    timed = [(e.get("id"), e.get("actor"), e.get("recorded_at")) for e in snap["events"] if not e.get("damaged")]
    timed += [(f"run {k}", v.get("actor"), v.get("started_at")) for k, v in snap["runs"].items()]
    out = []
    for name, actor, when in timed:
        if actor in ("agent", "system"):  # the agent's are Q17-b's; the kit's own follow a writer's
            continue
        try:
            moment = datetime.fromisoformat(when)
        except (TypeError, ValueError):
            out.append(f"{name} (no time)")
            continue
        if not any(a <= moment <= b for a, b in windows):
            out.append(f"{name} at {when}")
    return out


def check(out: Path, project: Path | None) -> dict:
    record = read_json(out / "prepare.json")
    paths = layout(out)
    env = record["session_env"]
    problems: list[str] = []
    sessions, unfinished = [], []
    for path in sorted(paths["sessions"].glob("*.json"), key=lambda p: int(p.stem)):
        try:
            s = read_json(path)
        except json.JSONDecodeError:
            s = {}
        if s.get("ended_at") and "log" in s:
            sessions.append(s)
        else:
            unfinished.append(int(path.stem))
    if unfinished:
        problems.append(f"session(s) {unfinished} have no end record (still open, or their launcher was killed)")
    if not sessions:
        problems.append("no session was recorded")
    for name, key in (("sandbox.sb", "profile_sha256"), ("canary.sb", "canary_sha256")):
        expected = record["sandbox"].get(key)
        if expected is None or not (out / name).is_file() or sha256_file(out / name) != expected:
            problems.append(f"{name} is not the profile prepare wrote")
    denied: dict[tuple, dict] = {}
    for s in sessions:
        if s["canary"]["logged"] < 1:
            problems.append(f"session {s['number']}: the canary's denial was not logged, so a zero is unverified")
        if s["log"]["returncode"] != 0:
            problems.append(f"session {s['number']}: the unified log was not read ({s['log']['returncode']})")
        for d in s["denials"]:
            denied[(d["time"], d["pid"], d["operation"], d["target"])] = d
        credential = [n for n in s["env_names"] if CREDENTIAL.search(n)]
        if credential:
            problems.append(f"session {s['number']}: credential variables {credential}")
    reread = None
    if sessions:
        first = datetime.fromisoformat(record.get("prepared_at") or sessions[0]["started_at"]) - timedelta(seconds=5)
        again = read_denials(first, datetime.now().astimezone() + timedelta(seconds=1), [record["sandbox"]["tag"]])
        reread = {"from": first.isoformat(timespec="seconds"), "returncode": again["returncode"],
                  "found": len(again["found"][record["sandbox"]["tag"]])}
        if again["returncode"] != 0:
            problems.append(f"the unified log was not read again ({again['returncode']})")
        for d in again["found"][record["sandbox"]["tag"]]:
            denied[(d["time"], d["pid"], d["operation"], d["target"])] = d
    attempts = len(denied)
    by_operation: dict[str, int] = {}
    for d in denied.values():
        by_operation[d["operation"]] = by_operation.get(d["operation"], 0) + 1
    if attempts:
        problems.append(f"{attempts} denied attempt(s): {by_operation}")
    outside = recorded_outside(project, sessions) if project is not None else []
    if outside:
        problems.append(f"{len(outside)} record(s) not by the agent were written outside the measured sessions: "
                        f"{outside[:5]}")
    tool_python = paths["tools"] / "aelix-analytics" / "bin" / "python"
    imports = {"tool_env": {m: importable(tool_python, m, env) for m in ("aelix_coding_agent", "aelix_ai", "aelix")}}
    if project is not None:
        imports["project_env"] = {m: importable(project / ".venv" / "bin" / "python", m, env)
                                  for m in ("aelix_coding_agent", "aelix_ai", "aelix")}
    for where, found in imports.items():
        if any(v for v in found.values()):
            problems.append(f"aelix is importable in the {where}: {found}")
    aelix_home = paths["home"] / ".aelix"
    home_files = sorted(p.relative_to(paths["home"]).as_posix() for p in aelix_home.rglob("*") if p.is_file()) \
        if aelix_home.is_dir() else []
    other = [f for f in home_files if f != ".aelix/agent/analytics-projects.json"]
    if other:
        problems.append(f"HOME's .aelix holds more than the kit's trust list: {other}")
    site = next((tool_python.parent.parent / "lib").glob("python3.*/site-packages"))
    network = kit_network_imports(site)
    unexpected = [n for n in network if n["module"] != "socket" or n["uses"] != ["gethostname"]]
    if unexpected:
        problems.append(f"the installed kit imports a network client: {unexpected}")
    result = {"schema": "aelix-analytics.q17-zero-model/1", "checked_at": now(),
              "wheel": record["wheel"], "checkout": record["checkout"],
              "model_calls": 0, "model_calls_basis": "inside the sandboxed sessions: no outbound IP or Unix-domain "
                                                       "socket connection can be made and no listed model client can be "
                                                       "executed by its path; what runs outside them (the editor, the "
                                                       "browser) is the owner's statement in owner-notes.md",
              "records_outside_sessions": outside,
              "attempts": attempts, "attempts_by_operation": by_operation, "denials": list(denied.values()),
              "log_read_again": reread,
              "sessions": [{k: s.get(k) for k in ("number", "started_at", "ended_at", "exit_code", "canary",
                                                   "left_running")}
                           | {"denials": len(s["denials"])} for s in sessions],
              "aelix_importable": imports, "home_aelix_files": home_files, "kit_network_imports": network,
              "denied": {k: record["sandbox"].get(k) for k in ("denied_network", "denied_exec", "denied_read",
                                                                 "not_covered")},
              "problems": problems, "passed": not problems}
    write_json(out / "zero-model.json", result)
    return result


# --- snapshot / compare ---------------------------------------------------------------------------

EXCLUDED_TOP = {"workspace", ".venv"}
EXCLUDED_KIT = {".analytics/cache", ".analytics/lock", ".analytics/run.lock", ".analytics/workspace.lock"}
IMMUTABLE = ("runs/", "artifacts/", "data/imported/", "assets/candidates/", ".analytics/context/",
             ".analytics/adhoc/", ".analytics/demo/", ".analytics/publications/", ".analytics/migration/")


def file_class(rel: str) -> str:
    if rel == "journal.jsonl" or (rel.startswith(".analytics/") and rel.endswith(".jsonl") and "/" not in rel[11:]):
        return "append"
    if rel == "analytics.json" or rel.startswith(IMMUTABLE):
        return "immutable"
    if rel.startswith(".analytics/"):
        return "kit_other"
    return "editable"


def excluded(rel: str) -> bool:
    parts = rel.split("/")
    return (parts[0] in EXCLUDED_TOP or "__pycache__" in parts or parts[-1] == ".DS_Store"
            or any(rel == e or rel.startswith(e + "/") for e in EXCLUDED_KIT))


def walk(project: Path):
    """Every file and link under the project but the excluded ones; excluded directories are not entered."""
    for directory, dirs, names in os.walk(project):
        base = Path(directory)
        rel = base.relative_to(project).as_posix()
        keep = []
        for d in dirs:
            path = d if rel == "." else f"{rel}/{d}"
            if excluded(path):
                continue
            if (base / d).is_symlink():
                yield base / d
            else:
                keep.append(d)
        dirs[:] = keep
        for name in names:
            path = name if rel == "." else f"{rel}/{name}"
            if not excluded(path):
                yield base / name


def line_hashes(data: bytes) -> list[str]:
    """Each line's hash with its LF, the last one's without when the file does not end in LF."""
    return [hashlib.sha256(line).hexdigest() for line in re.split(rb"(?<=\n)", data) if line]


def appended(before: dict, after: dict) -> bool:
    """after starts with before's bytes: every earlier line the same, the last one also when the
    writer ended it with the LF it lacked (the kit's append does: ADR-0006 6)."""
    old, new = before.get("lines", []), after.get("lines", [])
    if len(new) < len(old) or new[:len(old) - 1] != old[:len(old) - 1]:
        return False
    if not old:
        return True
    last = old[-1]
    return new[len(old) - 1] in (last, before.get("open_line_with_lf"))


def event_of(line: bytes, number: int) -> dict:
    out: dict = {"line": number, "sha256": hashlib.sha256(line).hexdigest()}
    try:
        e = json.loads(line)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {**out, "damaged": True}
    for key in ("id", "seq", "kind", "actor", "actor_basis", "means", "recorded_at"):
        out[key] = e.get(key)
    out["session"] = bool(e.get("session"))
    refs = []
    for r in e.get("refs") or []:
        target = next((f"{k}={r[k]}" for k in ("event_id", "run_id", "visualization_id", "report_id", "key", "version", "path")
                       if r.get(k)), r.get("token"))
        refs.append({"type": r.get("type"), "relation": r.get("relation"), "target": target,
                     "binding": r.get("version_binding")})
    out["refs"] = refs
    return out


def snapshot(project: Path) -> dict:
    project = project.resolve()
    if not (project / "analytics.json").is_file():
        raise Fail(f"{project} has no analytics.json")
    files: dict[str, dict] = {}
    for path in sorted(walk(project)):
        rel = path.relative_to(project).as_posix()
        if path.is_symlink():
            files[rel] = {"class": file_class(rel), "link": os.readlink(path)}
            continue
        if not stat.S_ISREG(path.lstat().st_mode):  # a FIFO or a device: never opened
            files[rel] = {"class": file_class(rel), "special": stat.filemode(path.lstat().st_mode)}
            continue
        entry = {"class": file_class(rel), "sha256": sha256_file(path), "bytes": path.stat().st_size}
        if entry["class"] == "append":
            data = path.read_bytes()
            entry["lines"] = line_hashes(data)
            if data and not data.endswith(b"\n"):
                entry["open_line_with_lf"] = hashlib.sha256(data[data.rfind(b"\n") + 1:] + b"\n").hexdigest()
        files[rel] = entry
    raw = (project / "journal.jsonl").read_bytes() if (project / "journal.jsonl").is_file() else b""
    events = [event_of(line, n) for n, line in enumerate(raw.split(b"\n"), 1) if line.strip()]
    runs = {}
    for run_json in sorted(project.glob("runs/*/run.json")):
        with contextlib.suppress(json.JSONDecodeError, UnicodeDecodeError):
            r = json.loads(run_json.read_bytes())
            runs[run_json.parent.name] = {k: r.get(k) for k in ("id", "status", "actor", "actor_basis", "means",
                                                                "rerun_of", "started_at")}
            runs[run_json.parent.name]["session"] = bool(r.get("session"))
            runs[run_json.parent.name]["code"] = sorted(
                f"{p.relative_to(run_json.parent / 'code').as_posix()}={sha256_file(p)}"
                for p in (run_json.parent / "code").rglob("*") if p.is_file())
    reports = {}
    for manifest in sorted(project.glob("artifacts/reports/*/*/manifest.json")):
        with contextlib.suppress(json.JSONDecodeError, UnicodeDecodeError):
            m = json.loads(manifest.read_bytes())
            reports[f"{manifest.parent.parent.name}@{manifest.parent.name}"] = {
                "report_id": m.get("report_id"), "revision_id": m.get("revision_id"),
                "previous_revision": m.get("previous_revision"), "title": m.get("title"),
                "manifest_sha256": sha256_file(manifest), "actor": m.get("actor"),
                "actor_basis": m.get("actor_basis"), "means": m.get("means")}
    identity = read_json(project / "analytics.json")
    return {"schema": SNAPSHOT_SCHEMA, "taken_at": now(), "project": str(project),
            "project_id": identity.get("project_id"), "files": files, "events": events, "runs": runs,
            "reports": reports}


def compare(before: dict, after: dict, expect_actor: str | None = None) -> dict:
    problems: list[str] = []
    if before.get("project_id") != after.get("project_id"):
        problems.append(f"another project: {before.get('project_id')} -> {after.get('project_id')}")
    edited, removed_editable = [], []
    for rel, b in before["files"].items():
        a = after["files"].get(rel)
        if b["class"] == "immutable":
            if a is None:
                problems.append(f"removed: {rel}")
            elif a.get("sha256") != b.get("sha256") or a.get("link") != b.get("link"):
                problems.append(f"changed: {rel}")
        elif b["class"] == "append":
            if a is None:
                problems.append(f"removed: {rel}")
            elif not appended(b, a):
                problems.append(f"earlier lines changed: {rel}")
        elif a is None:
            removed_editable.append(rel)
        elif a.get("sha256") != b.get("sha256") or a.get("link") != b.get("link"):
            edited.append(rel)
    added = sorted(set(after["files"]) - set(before["files"]))
    new_events = after["events"][len(before["events"]):]
    new_runs = {k: v for k, v in after["runs"].items() if k not in before["runs"]}
    for k, v in before["runs"].items():
        if k in after["runs"] and after["runs"][k] != v:
            problems.append(f"run {k} reads differently: {v} -> {after['runs'][k]}")
    new_reports = {k: v for k, v in after["reports"].items() if k not in before["reports"]}
    for k, v in new_reports.items():
        prev = v.get("previous_revision")
        if prev and f"{v['report_id']}@{prev}" not in after["reports"]:
            problems.append(f"report {k}: previous revision {prev} is not kept")
    ids = [e.get("id") for e in after["events"] if not e.get("damaged")]
    if len(ids) != len(set(ids)):
        problems.append("an event id appears twice")
    if expect_actor:
        for e in new_events:
            if e.get("actor") not in (expect_actor, "system"):
                problems.append(f"event {e.get('id')} ({e.get('kind')}) has actor {e.get('actor')}, not {expect_actor}")
        for k, v in new_runs.items():
            if v.get("actor") != expect_actor:
                problems.append(f"run {k} has actor {v.get('actor')}, not {expect_actor}")
        for k, v in new_reports.items():
            if v.get("actor") != expect_actor:
                problems.append(f"report {k} has actor {v.get('actor')}, not {expect_actor}")
    by_actor: dict[str, int] = {}
    for e in new_events:
        key = f"{e.get('actor')}/{e.get('actor_basis')}/{e.get('means')}"
        by_actor[key] = by_actor.get(key, 0) + 1
    return {"schema": "aelix-analytics.q17-compare/1", "compared_at": now(),
            "before": {"taken_at": before["taken_at"], "events": len(before["events"]), "runs": len(before["runs"]),
                       "reports": len(before["reports"])},
            "after": {"taken_at": after["taken_at"], "events": len(after["events"]), "runs": len(after["runs"]),
                      "reports": len(after["reports"])},
            "kept": {"immutable": sum(1 for f in before["files"].values() if f["class"] == "immutable"),
                     "append": sum(1 for f in before["files"].values() if f["class"] == "append")},
            "new_events": new_events, "new_events_by_origin": by_actor, "new_runs": new_runs,
            "new_reports": new_reports, "edited": edited, "removed_editable": removed_editable,
            "added_files": added, "expect_actor": expect_actor, "problems": problems, "passed": not problems}


# --- agent-env / agent-usage ----------------------------------------------------------------------

def load_model_eval():
    spec = importlib.util.spec_from_file_location("model_eval", Path(__file__).resolve().parent / "model_eval.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("model_eval", module)
    spec.loader.exec_module(module)
    return module


def copy_project(source: Path, target: Path) -> None:
    def ignore(directory, names):
        rel = Path(directory).resolve().relative_to(source.resolve()).as_posix()
        skip = set()
        for name in names:
            path = name if rel == "." else f"{rel}/{name}"
            mode = os.lstat(Path(directory) / name).st_mode
            if excluded(path) or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode) or stat.S_ISLNK(mode)):
                skip.add(name)
        return skip
    shutil.copytree(source, target, symlinks=True, ignore=ignore)


def agent_env(out: Path, model_id: str, project: Path) -> dict:
    record = read_json(out / "prepare.json")
    model_path = MODELS / f"{model_id}.json"
    if not model_path.is_file():
        raise Fail(f"no model configuration {model_path}")
    model = read_json(model_path)
    base = out / "agents" / model_id
    if base.exists():
        raise Fail(f"{base} exists: one agent environment per model")
    dirs = {k: base / k for k in ("agent", "home", "tools", "bin", "tmp", "uv-cache", "python")}
    for d in dirs.values():
        d.mkdir(parents=True)
    (dirs["agent"] / "sessions").mkdir()
    paths = layout(out)
    wheel = paths["dist"] / record["wheel"]["file"]
    # Its own uv cache and Python: aelix must never reach the cache of the Q17-a sessions.
    uv = dirs["bin"] / "uv"
    shutil.copy2(paths["bin"] / "uv", uv)
    env = {"HOME": str(dirs["home"]), "PATH": f"{dirs['bin']}:/usr/bin:/bin:/usr/sbin:/sbin",
           "TMPDIR": str(dirs["tmp"]) + "/", "LANG": "ko_KR.UTF-8", "UV_TOOL_DIR": str(dirs["tools"]),
           "UV_TOOL_BIN_DIR": str(dirs["bin"]), "UV_CACHE_DIR": str(dirs["uv-cache"]),
           "UV_PYTHON_INSTALL_DIR": str(dirs["python"]), "UV_PYTHON_PREFERENCE": "only-managed",
           "UV_NO_CONFIG": "1", "XDG_CONFIG_HOME": str(dirs["home"] / ".config"),
           "AELIX_CODING_AGENT_DIR": str(dirs["agent"]),
           "AELIX_CODING_AGENT_SESSION_DIR": str(dirs["agent"] / "sessions")}
    run([uv, "python", "install", PYTHON], env=env)
    run([uv, "tool", "install", "--python", PYTHON, AELIX, "--with", wheel,
         "--with-executables-from", "aelix-analytics"], env=env)
    load_model_eval().write_models_json(model, dirs["agent"], None)
    copy = base / "project"
    copy_project(project, copy)
    before = snapshot(copy)
    write_json(base / "before.json", before)
    source = compare(snapshot(project), before)
    python = dirs["tools"] / "aelix" / "bin" / "python"
    aelix_args = ["--provider", model["provider"], "--model", model["model"], "--thinking", model["thinking"]]
    if model.get("auth"):
        auth = Path(os.environ.get("AELIX_CODING_AGENT_DIR") or Path.home() / ".aelix" / "agent") / "auth.json"
        launcher = base / LAUNCHER.name  # a copy: start.sh must not depend on this checkout staying
        shutil.copy2(LAUNCHER, launcher)
        head = [str(python), str(launcher), str(auth)]
    else:
        head = [str(dirs["bin"] / "aelix")]
    command = [*head, *aelix_args]
    exports = " ".join(shlex.quote(f"{k}={v}") for k, v in env.items())
    (base / "start.sh").write_text(
        "#!/bin/sh\n# Q17-b: start aelix in the project's copy (tools/human_e2e.py agent-env)\n"
        f"cd {shlex.quote(str(copy))} || exit 1\n"
        f"exec /usr/bin/env -i {exports} TERM=\"${{TERM:-xterm-256color}}\" {shlex.join(command)} \"$@\"\n",
        encoding="utf-8")
    (base / "start.sh").chmod(0o755)
    listed = run([*head, "--offline", "--list-models",
                  f"{model['provider']} {model['model']}"], env=env, cwd=dirs["agent"], check=False)
    result = {"schema": "aelix-analytics.q17-agent-env/1", "prepared_at": now(), "model": model_id,
              "model_sha256": sha256_file(model_path), "provider": model["provider"], "model_name": model["model"],
              "thinking": model["thinking"], "auth": model.get("auth", {}).get("source") if model.get("auth") else None,
              "aelix": run([dirs["bin"] / "aelix", "--version"], env=env, check=False).stdout.strip().splitlines()[-1:],
              "wheel": record["wheel"], "project_source": str(project), "project_copy": str(copy),
              "copy_matches_source": source["passed"] and not source["edited"], "copy_problems": source["problems"],
              "list_models": {"returncode": listed.returncode, "stdout": listed.stdout[-2000:],
                              "stderr": listed.stderr[-2000:]},
              "launcher_sha256": sha256_file(LAUNCHER) if model.get("auth") else None,
              "start": str(base / "start.sh"), "env_names": sorted(env)}
    write_json(base / "agent-env.json", result)
    return result


def agent_usage(sessions: Path) -> dict:
    me = load_model_eval()
    messages, tools, users, files = [], {}, 0, []
    times: list[str] = []
    for path in sorted(sessions.rglob("*.jsonl")):
        files.append(path.relative_to(sessions).as_posix())
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            with contextlib.suppress(json.JSONDecodeError):
                entry = json.loads(line)
                if entry.get("type") != "message":
                    continue
                message = entry.get("message") or {}
                if message.get("timestamp"):
                    times.append(str(message["timestamp"]))
                if message.get("role") == "user":
                    users += 1
                elif message.get("role") == "assistant":
                    messages.append({"usage": message.get("usage"), "model": message.get("model"),
                                     "api": message.get("api"), "provider": message.get("provider"),
                                     "stop_reason": message.get("stop_reason"), "turn": users})
                    for block in message.get("content") or []:
                        if isinstance(block, dict) and block.get("type") == "toolCall":
                            name = block.get("tool_name") or "?"
                            tools[name] = tools.get(name, 0) + 1
    apis = sorted({m["api"] for m in messages if m.get("api")})
    totals = me.token_totals(messages, apis[0]) if len(apis) == 1 else None
    if totals is not None:
        totals.pop("per_request", None)
    return {"schema": "aelix-analytics.q17-agent-usage/1", "read_at": now(), "session_files": files,
            "user_messages": users, "requests": len(messages), "models": sorted({f"{m.get('provider')}/{m.get('model')}"
                                                                                for m in messages}),
            "apis": apis, "tokens": totals, "tool_calls": dict(sorted(tools.items())),
            "first_timestamp": min(times) if times else None, "last_timestamp": max(times) if times else None}


# --- command line ---------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="human_e2e.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("out", type=Path)
    p.add_argument("--checkout", type=Path, default=ROOT)
    p.add_argument("--allow-dirty", action="store_true")
    p = sub.add_parser("shell")
    p.add_argument("out", type=Path)
    p.add_argument("--cwd", type=Path)
    p.add_argument("--script", type=Path, help="run this zsh script instead of an interactive shell (a rehearsal)")
    p = sub.add_parser("check")
    p.add_argument("out", type=Path)
    p.add_argument("--project", type=Path)
    p = sub.add_parser("snapshot")
    p.add_argument("project", type=Path)
    p.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("compare")
    p.add_argument("before", type=Path)
    p.add_argument("after", type=Path)
    p.add_argument("--expect-actor")
    p.add_argument("--out", type=Path)
    p = sub.add_parser("agent-env")
    p.add_argument("out", type=Path)
    p.add_argument("--model", required=True)
    p.add_argument("--project", type=Path, required=True)
    p = sub.add_parser("agent-usage")
    p.add_argument("sessions", type=Path)
    p.add_argument("--out", type=Path)
    args = ap.parse_args(argv)
    try:
        if args.command == "prepare":
            result = prepare(args.out.resolve(), args.checkout.resolve(), args.allow_dirty)
            print(json.dumps({k: result[k] for k in ("wheel", "python", "sandbox")}, ensure_ascii=False, indent=2))
        elif args.command == "shell":
            shell(args.out.resolve(), args.cwd.resolve() if args.cwd else None,
                  args.script.resolve() if args.script else None)
        elif args.command == "check":
            result = check(args.out.resolve(), args.project.resolve() if args.project else None)
            print(json.dumps({k: result[k] for k in ("model_calls", "attempts", "sessions", "problems", "passed")},
                             ensure_ascii=False, indent=2))
            return 0 if result["passed"] else 1
        elif args.command == "snapshot":
            write_json(args.out, snapshot(args.project))
        elif args.command == "compare":
            result = compare(read_json(args.before), read_json(args.after), args.expect_actor)
            if args.out:
                write_json(args.out, result)
            print(json.dumps({k: result[k] for k in ("before", "after", "new_events_by_origin", "edited",
                                                     "added_files", "problems", "passed")},
                             ensure_ascii=False, indent=2))
            return 0 if result["passed"] else 1
        elif args.command == "agent-env":
            result = agent_env(args.out.resolve(), args.model, args.project.resolve())
            print(json.dumps({k: result[k] for k in ("model", "aelix", "project_copy", "copy_matches_source",
                                                     "list_models", "start")}, ensure_ascii=False, indent=2))
        elif args.command == "agent-usage":
            result = agent_usage(args.sessions)
            if args.out:
                write_json(args.out, result)
            print(json.dumps(result, ensure_ascii=False, indent=2))
    except Fail as exc:
        print(f"human_e2e: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
