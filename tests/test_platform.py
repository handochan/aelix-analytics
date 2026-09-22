"""The portable boundaries: the rules that are the same everywhere, the ports' contract, the
injection seam and the loader the aelix process uses.

Running the Windows implementations is out of scope here and stays `not_run` until Issue #8's
platform verification: constructing the Windows bundle on POSIX proves wiring, never behaviour.
"""

from __future__ import annotations

import contextlib
import importlib.util
import json
import multiprocessing
import os
import re
import signal
import subprocess
import sys
import time
import types
from pathlib import Path

import pytest
from conftest import kit_env, labkit, write_sales_csv, write_script

from labkit import _platform, core

PACK = Path(__file__).resolve().parents[1]
KIT = PACK / "kit"
EXT = PACK / "extension" / "aelix_lab.py"


# --- rules that are the same on every platform ----------------------------------------------------

@pytest.mark.parametrize("name, reserved", [
    ("con", True), ("CON", True), ("con.png", True), ("nul", True), ("aux.parquet", True),
    ("com1", True), ("lpt9", True), ("console", False), ("contract", False), ("com10", False),
    ("sales", False), ("nul_data", False),
])
def test_windows_device_names_are_recognised_everywhere(name, reserved):
    assert _platform.reserved_name(name) is reserved


def test_portable_name_rejects_what_windows_cannot_store():
    assert _platform.portable_name("sales_2025")
    for bad in ("con", "a<b", 'a"b', "a|b", "a?b", "a*b", "a:b", "a\\b", "a/b", "a\x01b",
                "trailing.", "trailing ", "", ".", ".."):
        assert not _platform.portable_name(bad), bad


def test_portable_stem_keeps_a_usable_name_and_leaves_ordinary_ones_alone():
    assert _platform.portable_stem("con") == "con_"
    assert not _platform.reserved_name(_platform.portable_stem("con"))
    assert _platform.portable_stem("monthly-revenue") == "monthly-revenue"


def test_portable_stem_never_maps_two_chart_ids_onto_one_file():
    """A chart id is a slug and holds no underscore, which is what makes the mapping injective.
    `con` and the ordinary id `con-file` must not land on the same file, or two charts would
    overwrite each other's image and a rerun comparison would report them as the same."""
    ids = [core.slugify(text, 40) for text in
           ("con", "con file", "con-file", "con 1", "nul", "nul data", "aux", "revenue")]
    stems = [_platform.portable_stem(i) for i in ids]
    assert len(set(stems)) == len(set(ids)), dict(zip(ids, stems, strict=True))
    assert all("_" not in i for i in ids), "a slug with an underscore would break the mapping"


@pytest.mark.parametrize("recorded, url", [
    (r"C:\data\input.csv", "file:///C:/data/input.csv"),
    ("C:/data/input.csv", "file:///C:/data/input.csv"),
    (r"\\server\share\x.csv", "file://server/share/x.csv"),
    ("/data/sales.csv", "file:///data/sales.csv"),
])
def test_file_url_reads_a_path_the_way_the_platform_that_recorded_it_meant_it(recorded, url):
    # A record outlives the machine that wrote it, so a Windows path has to resolve on Linux too.
    assert _platform.file_url(recorded) == url


def test_file_url_percent_encodes_instead_of_mangling_non_ascii():
    assert _platform.file_url("/data/한글.csv").startswith("file:///data/%ED")


def test_file_url_refuses_a_relative_path():
    with pytest.raises(ValueError, match="absolute"):
        _platform.file_url("data/raw/sales.parquet")


@pytest.mark.parametrize("recorded, absolute", [
    (r"C:\data\x.csv", True), ("C:/data/x.csv", True), (r"\\srv\share\x", True),
    ("/data/x.csv", True), ("data/raw/x.parquet", False), ("runs/1/code/x.py", False),
])
def test_absolute_record_path_does_not_ask_the_running_platform(recorded, absolute):
    assert _platform.absolute_record_path(recorded) is absolute
    if absolute and os.name != "nt" and recorded.startswith("C:"):
        # This is the bug it exists to prevent: the report would have linked it as a relative path.
        assert not os.path.isabs(recorded)


def test_text_kwargs_pin_utf8_rather_than_the_locale():
    assert _platform.text_kwargs()["encoding"] == "utf-8"


# --- assembling a bundle ---------------------------------------------------------------------------

def test_detect_picks_this_platform_and_can_be_asked_for_either():
    here = _platform.detect()
    assert here.name == ("windows" if os.name == "nt" else "posix")
    assert _platform.detect("posix").name == "posix"
    with pytest.raises(ValueError, match="unknown platform"):
        _platform.detect("bsd")


def test_building_the_windows_bundle_does_not_import_its_operating_system_module():
    # So the wiring can be asserted from any platform. It proves nothing about Windows behaviour.
    sys.modules.pop("msvcrt", None)
    bundle = _platform.detect("windows")
    assert type(bundle.locks).__name__ == "WindowsFileLocks"
    assert type(bundle.runs).__name__ == "WindowsRunControl"
    assert "msvcrt" not in sys.modules


# --- the file lock contract -------------------------------------------------------------------------

def _hold_lock(lock_path, started, seconds):
    with _platform.detect().locks.exclusive(Path(lock_path)):
        started.set()
        time.sleep(seconds)


def test_the_lock_excludes_another_process(tmp_path):
    lock = tmp_path / "lock"
    started = multiprocessing.Event()
    holder = multiprocessing.Process(target=_hold_lock, args=(str(lock), started, 1.5))
    holder.start()
    try:
        assert started.wait(10), "the holder never acquired the lock"
        t0 = time.perf_counter()
        with _platform.detect().locks.exclusive(lock):
            waited = time.perf_counter() - t0
        assert waited > 0.5, f"the lock did not exclude the other process (waited {waited:.2f}s)"
    finally:
        holder.join(20)


def test_the_lock_creates_its_directory_and_does_not_truncate(tmp_path):
    lock = tmp_path / "missing" / "lock"
    with _platform.detect().locks.exclusive(lock):
        lock.write_bytes(b"content")
    with _platform.detect().locks.exclusive(lock):
        pass
    assert lock.read_bytes() == b"content"


# --- the run-control contract -------------------------------------------------------------------

# The contract lets a port interrupt with either exception, and the caller classifies by the flag
# its callback set: POSIX raises Interrupted from its signal handler, Windows raises
# KeyboardInterrupt through interrupt_main.
INTERRUPTIONS = (_platform.Interrupted, KeyboardInterrupt)


def test_the_deadline_records_the_expiry_and_interrupts_the_main_thread():
    flags = {"timeout": False}
    runs = _platform.detect().runs
    with pytest.raises(INTERRUPTIONS):
        with runs.deadline(1.0, lambda: flags.__setitem__("timeout", True)):
            time.sleep(10)
    assert flags["timeout"], "the callback has to run before the interrupt"


def test_a_callback_that_raises_still_lets_the_port_interrupt(capsys):
    calls = []

    def broken():
        calls.append(1)
        raise RuntimeError("callback bug")

    runs = _platform.detect().runs
    with pytest.raises(INTERRUPTIONS):
        with runs.deadline(1.0, broken):
            time.sleep(10)
    assert calls == [1]
    assert "run-control callback failed" in capsys.readouterr().err


def test_the_deadline_disarms_when_the_body_finishes_first():
    flags = {"timeout": False}
    runs = _platform.detect().runs
    with runs.deadline(30.0, lambda: flags.__setitem__("timeout", True)):
        pass
    time.sleep(0.2)
    assert not flags["timeout"]


def test_a_cancel_scope_can_record_without_raising_and_restores_the_handler():
    before = signal.getsignal(signal.SIGINT)
    seen = {"cancel": False}
    runs = _platform.detect().runs
    with runs.cancel_scope(lambda: seen.__setitem__("cancel", True), interrupt=False):
        os.kill(os.getpid(), signal.SIGINT)
        time.sleep(0.05)
    assert seen["cancel"], "the cancel was not recorded"
    assert signal.getsignal(signal.SIGINT) is before


def test_a_raising_cancel_scope_interrupts_and_a_nested_one_restores_the_outer_handlers():
    outer, inner = {"n": 0}, {"n": 0}
    runs = _platform.detect().runs
    with runs.cancel_scope(lambda: outer.__setitem__("n", outer["n"] + 1), interrupt=False):
        with pytest.raises(INTERRUPTIONS):
            with runs.cancel_scope(lambda: inner.__setitem__("n", inner["n"] + 1), interrupt=True):
                os.kill(os.getpid(), signal.SIGINT)
                time.sleep(0.2)
        assert (inner["n"], outer["n"]) == (1, 0)
        # Leaving the inner scope must hand the signal back to the flag-only one, which is what
        # keeps a run's record from being interrupted while it is written.
        os.kill(os.getpid(), signal.SIGINT)
        time.sleep(0.05)
    assert (inner["n"], outer["n"]) == (1, 1)


# --- the injection seam -----------------------------------------------------------------------------

class _RecordingLocks:
    def __init__(self, calls):
        self.calls = calls
        self._held = set()

    @contextlib.contextmanager
    def exclusive(self, path):
        path = Path(path)
        self.calls.append(("locks.exclusive", path.name))
        assert path not in self._held, f"{path.name} was taken re-entrantly"
        path.parent.mkdir(parents=True, exist_ok=True)
        self._held.add(path)
        try:
            yield
        finally:
            self._held.discard(path)


class _RecordingFiles:
    def __init__(self, calls):
        self.calls = calls

    def replace(self, src, dst):
        self.calls.append(("files.replace", Path(dst).name))
        os.replace(src, dst)


class _RecordingProcesses:
    def __init__(self, calls):
        self.calls = calls

    def alive(self, pid):
        self.calls.append(("processes.alive", pid))
        return False

    def spawn_kwargs(self):
        return {}

    def base_env(self):
        return {}

    def interrupt(self, proc):
        self.calls.append(("processes.interrupt", proc.pid))

    def kill_tree(self, proc):
        self.calls.append(("processes.kill_tree", proc.pid))


class _RecordingRuns:
    def __init__(self, calls):
        self.calls = calls

    @contextlib.contextmanager
    def deadline(self, seconds, on_expire):
        self.calls.append(("runs.deadline", seconds))
        yield

    @contextlib.contextmanager
    def cancel_scope(self, on_cancel, *, interrupt):
        self.calls.append(("runs.cancel_scope", interrupt))
        yield


def recording_platform():
    calls: list[tuple] = []
    bundle = types.SimpleNamespace(
        name="recording", locks=_RecordingLocks(calls), files=_RecordingFiles(calls),
        processes=_RecordingProcesses(calls), runs=_RecordingRuns(calls))
    return bundle, calls


IMPLEMENTATIONS = (_platform.PosixFileLocks, _platform.WindowsFileLocks,
                   _platform.PosixAtomicFiles, _platform.WindowsAtomicFiles,
                   _platform.PosixProcessControl, _platform.WindowsProcessControl,
                   _platform.PosixRunControl, _platform.WindowsRunControl)


def port_methods(cls) -> set[str]:
    return {name for name, value in vars(cls).items()
            if callable(value) and not name.startswith("_")}


@pytest.fixture
def real_ports_refuse(monkeypatch):
    """Make every real implementation fail, so a path that quietly falls back to the process
    default cannot pass by producing the same record.

    Every public method of every implementation is poisoned rather than a chosen few, so a port
    that grows a method does not silently fall outside this."""
    def refuse(*_args, **_kwargs):
        raise AssertionError("the real platform implementation was used despite an injected one")

    poisoned = 0
    for cls in IMPLEMENTATIONS:
        for name in port_methods(cls):
            monkeypatch.setattr(cls, name, refuse, raising=True)
            poisoned += 1
    assert poisoned >= 18, f"only {poisoned} implementation methods were poisoned"


CONTEXT_VERSION_RE = re.compile(r"^\d{8}-\d{6}-\d{6}_")


def _stable(value):
    """Drop the wall-clock part of a context version so two runs of the same flow compare.

    The content digest that follows it is kept: that is what has to be identical."""
    if isinstance(value, dict):
        return {k: _stable(v) for k, v in value.items()}
    if isinstance(value, str):
        return CONTEXT_VERSION_RE.sub("<when>_", value)
    return value


def _representative_flow(root: Path, source: Path, platform) -> dict:
    """Create a project, import data, write a journal entry and read the project back."""
    project = core.init_project(root, title="이식 경계", goal="목표", template_dir=KIT / "template",
                                actor="test", platform=platform)
    from labkit import data  # noqa: PLC0415 - needs the analysis libraries, unlike core

    data.import_file(project, source, name="sales", description="test data", actor="test")
    core.add_note(project, "finding", "경계 뒤에서도 같은 기록", refs=["sales"], actor="test")
    status = core.status(project)
    events = [{k: _stable(e[k]) for k in ("id", "kind", "actor", "text", "refs")}
              for e in core.read_events(project)]
    return {"events": events, "datasets": [{k: d.get(k) for k in ("name", "path", "kind", "rows",
                                                                  "sha256")}
                                           for d in status["datasets"]],
            "views": status["views"], "counts": status["counts"]}


def test_the_injected_platform_is_the_one_actually_used(tmp_path, real_ports_refuse):
    # Every real implementation is armed to fail, so a path that quietly falls back to the process
    # default cannot pass by producing the same record.
    source = write_sales_csv(tmp_path / "sales.csv")
    fake, calls = recording_platform()
    _representative_flow(tmp_path / "injected", source, fake)

    assert {name for name, _ in calls} >= {"locks.exclusive", "files.replace"}, calls
    assert any(arg == "sales.parquet" for name, arg in calls if name == "files.replace"), calls
    assert any(arg == "lock" for name, arg in calls if name == "locks.exclusive"), calls
    assert any(arg == "lab.json" for name, arg in calls if name == "files.replace"), calls


def test_the_real_and_the_injected_platform_record_the_same_thing(tmp_path):
    source = write_sales_csv(tmp_path / "sales.csv")
    fake, _calls = recording_platform()
    with_real = _representative_flow(tmp_path / "real", source, _platform.detect())
    with_fake = _representative_flow(tmp_path / "fake", source, fake)
    assert with_real == with_fake


def test_a_run_goes_through_the_injected_platform_too(tmp_path, real_ports_refuse):
    from labkit import runner  # noqa: PLC0415 - needs the analysis libraries

    fake, calls = recording_platform()
    project = core.init_project(tmp_path / "p", title="실행 경계", template_dir=KIT / "template",
                                actor="test", platform=fake)
    project.scripts.mkdir(parents=True, exist_ok=True)
    (project.scripts / "01_min.py").write_text("print('ok')\n", encoding="utf-8")
    result = runner.execute(project, script="scripts/01_min.py", title="최소 실행", actor="test")

    assert result["run"]["status"] == "ok", result["run"].get("error")
    names = {name for name, _ in calls}
    assert {"runs.cancel_scope", "runs.deadline", "locks.exclusive", "files.replace"} <= names, calls
    assert ("files.replace", "run.json") in calls, calls


def test_the_hash_cache_writes_through_the_project_platform(tmp_path, real_ports_refuse):
    fake, calls = recording_platform()
    project = core.init_project(tmp_path / "p", title="캐시", template_dir=KIT / "template",
                                actor="test", platform=fake)
    cache = core.HashCache(project)
    assert cache.sha(project.marker)  # marks the cache dirty
    cache.save()
    assert ("files.replace", "hashes.json") in calls, calls


def test_a_run_is_listed_through_the_project_platform(tmp_path, real_ports_refuse):
    fake, calls = recording_platform()
    project = core.init_project(tmp_path / "p", title="실행 목록", template_dir=KIT / "template",
                                actor="test", platform=fake)
    run_dir = project.runs / "20260922-101010-x"
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(json.dumps({"schema": core.SCHEMA_RUN, "status": "running",
                                                  "pid": 999_999}), encoding="utf-8")
    runs = core.list_runs(project)
    assert [r["status"] for r in runs] == ["incomplete"]
    assert ("processes.alive", 999_999) in calls, calls


# --- new names stay portable, existing records keep working ------------------------------------

def test_a_new_dataset_name_that_windows_cannot_store_is_refused(project, tmp_path):
    source = write_sales_csv(tmp_path / "con.csv")
    from labkit import data  # noqa: PLC0415

    with pytest.raises(core.LabError) as exc:
        data.import_file(core.Project(project), source, name="con", actor="test")
    assert exc.value.code == "name_not_portable"
    assert "con" in exc.value.message


def test_a_script_that_writes_a_reserved_name_still_runs_and_is_warned_about(project):
    # Refusing inside a run would stop a preserved script from re-executing, including one whose
    # output file has since been deleted, so the run is recorded with the limit stated instead.
    body = ("import polars as pl\nimport labkit as lab\n"
            "lab.save(pl.DataFrame({'a': [1]}), 'con')\n")
    code, result = labkit(project, "run", write_script(project, "21_reserved_save.py", body),
                          "--title", "예약 이름 저장")
    assert code == 0 and result["run"]["status"] == "ok", result
    assert (Path(project) / "data/derived/con.parquet").is_file()
    assert any("reserved device name on Windows" in w for w in result["run"]["warnings"]), \
        result["run"]["warnings"]


def test_reject_unportable_name_never_refuses_a_name_the_project_already_has():
    core.reject_unportable_name("con", kind="dataset", exists=True)
    core.reject_unportable_name("sales", kind="dataset")
    with pytest.raises(core.LabError) as exc:
        core.reject_unportable_name("con", kind="script")
    assert exc.value.code == "name_not_portable" and "script" in exc.value.message


def test_two_chart_ids_that_differ_keep_two_files(project):
    # `con-file` is an ordinary id and has to keep its own file. If the reserved one were mapped
    # onto it the two charts would overwrite each other's image and plotted data, and a rerun
    # comparison would then report two different charts as the same.
    body = ("import matplotlib.pyplot as plt\nimport polars as pl\nimport labkit as lab\n"
            "for cid, y in (('con', 1), ('con-file', 2)):\n"
            "    fig, ax = plt.subplots()\n    ax.plot([1, 2], [y, y])\n"
            "    lab.chart(fig, cid, title=cid, data=pl.DataFrame({'y': [y]}))\n")
    code, result = labkit(project, "run", write_script(project, "20_reserved.py", body),
                          "--title", "예약 이름 차트")
    assert code == 0, result
    charts = {c["id"]: c for c in result["run"]["charts"]}
    assert set(charts) == {"con", "con-file"}, "the chart identity is what records point at"
    assert Path(charts["con"]["files"]["png"]).name == "con_.png"
    assert Path(charts["con-file"]["files"]["png"]).name == "con-file.png"
    files = [c["files"]["png"] for c in charts.values()] + [c["data"] for c in charts.values()]
    assert len(set(files)) == len(files), files
    for rel in files:
        assert (Path(project) / rel).is_file(), rel


def test_a_title_that_slugifies_to_a_device_name_keeps_the_id_it_always_had(project):
    # A generated id is still `script::id` identity in the records, so it is not rewritten either:
    # an unchanged historical script must not start a second chart history.
    body = ("import matplotlib.pyplot as plt\nimport labkit as lab\n"
            "fig, ax = plt.subplots()\nax.plot([1, 2], [1, 2])\n"
            "lab.chart(fig, title='CON')\n")
    code, result = labkit(project, "run", write_script(project, "22_generated.py", body),
                          "--title", "생성된 id")
    assert code == 0, result
    (chart,) = result["run"]["charts"]
    assert chart["id"] == "con"
    assert Path(chart["files"]["png"]).name == "con_.png"


# --- the loader the aelix process uses ---------------------------------------------------------

def _aelix_stubs() -> dict:
    """Just enough of the aelix API to import the extension without aelix installed.

    The loader is what is under test; the tools and hooks only have to construct."""
    def module(name, **attrs):
        mod = types.ModuleType(name)
        for key, value in attrs.items():
            setattr(mod, key, value)
        return mod

    class Any:
        def __init__(self, *args, **kwargs):
            self.__dict__.update(kwargs)

    core_pkg = module("aelix_agent_core")
    core_pkg.harness = module("aelix_agent_core.harness")
    ai_pkg = module("aelix_ai")
    return {
        "aelix_agent_core": core_pkg,
        "aelix_agent_core.harness": core_pkg.harness,
        "aelix_agent_core.harness.hooks": module("aelix_agent_core.harness.hooks",
                                                 BeforeAgentStartResult=Any),
        "aelix_agent_core.types": module("aelix_agent_core.types", AgentTool=Any),
        "aelix_ai": ai_pkg,
        "aelix_ai.messages": module("aelix_ai.messages", ImageContent=Any, TextContent=Any),
        "aelix_ai.tools": module("aelix_ai.tools", ToolExecutionContext=Any, ToolResult=Any),
    }


def load_extension(monkeypatch, name: str):
    for key, value in _aelix_stubs().items():
        monkeypatch.setitem(sys.modules, key, value)
    spec = importlib.util.spec_from_file_location(name, EXT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ANALYSIS_MODULES = ("polars", "duckdb", "matplotlib", "plotly", "pyarrow", "numpy")


def test_the_extension_loads_the_kit_without_leaving_module_names_behind(monkeypatch):
    before = {k for k in sys.modules if k.startswith("aelix_lab_kit")}
    first = load_extension(monkeypatch, "ext_one")
    second = load_extension(monkeypatch, "ext_two")

    assert first.core is not second.core, "each load has to get its own kit"
    assert first.core._platform is not second.core._platform, "a cached sibling could mix packs"
    assert first.core.SCHEMA_RUN == core.SCHEMA_RUN
    assert {k for k in sys.modules if k.startswith("aelix_lab_kit")} == before
    assert first.core.PLATFORM.name == _platform.detect().name


def test_loading_the_kit_pulls_no_analysis_library_into_the_host_process(monkeypatch):
    # The extension runs inside aelix. Polars or matplotlib arriving there is the failure this
    # whole boundary exists to prevent, whichever loader is used.
    code = (
        "import importlib.util, sys, types\n"
        f"sys.path.insert(0, {str(PACK / 'tests')!r})\n"
        "stub = types.ModuleType\n"
        "class Any:\n"
        "    def __init__(self, *a, **k): self.__dict__.update(k)\n"
        "for name, attrs in (('aelix_agent_core', {}), ('aelix_agent_core.harness', {}),\n"
        "                    ('aelix_agent_core.harness.hooks', {'BeforeAgentStartResult': Any}),\n"
        "                    ('aelix_agent_core.types', {'AgentTool': Any}),\n"
        "                    ('aelix_ai', {}), ('aelix_ai.messages', {'ImageContent': Any, 'TextContent': Any}),\n"
        "                    ('aelix_ai.tools', {'ToolExecutionContext': Any, 'ToolResult': Any})):\n"
        "    m = stub(name)\n"
        "    [setattr(m, k, v) for k, v in attrs.items()]\n"
        "    sys.modules[name] = m\n"
        f"spec = importlib.util.spec_from_file_location('ext', {str(EXT)!r})\n"
        "mod = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(mod)\n"
        f"loaded = [n for n in {ANALYSIS_MODULES!r} if n in sys.modules]\n"
        "print('LOADED:' + ','.join(loaded))\n"
        "print('KIT_OK:' + mod.core.SCHEMA_RUN)\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, timeout=120,
                          env=kit_env(), **_platform.text_kwargs())
    assert "KIT_OK:lab.run/1" in proc.stdout, proc.stdout + proc.stderr
    loaded = next(line for line in proc.stdout.splitlines() if line.startswith("LOADED:"))
    assert loaded == "LOADED:", f"the aelix process would have imported {loaded[len('LOADED:'):]}"


def test_a_kit_that_is_not_there_is_a_clear_error(monkeypatch, tmp_path):
    monkeypatch.setenv("AELIX_LAB_KIT", str(tmp_path / "nowhere"))
    with pytest.raises(RuntimeError, match="kit not found"):
        load_extension(monkeypatch, "ext_missing")
    assert not [k for k in sys.modules if k.startswith("aelix_lab_kit")]


# --- behaviour the review reproduced as broken ---------------------------------------------------

def test_a_cancel_while_the_capture_hooks_install_stops_the_script(project, monkeypatch):
    """The cancel has to reach the script's own phase, not only the record's.

    Installing the capture hooks used to sit outside the interrupting scope, so a cancel arriving
    there set the flag and the script ran anyway — the record said `cancelled` while its side
    effects were on disk."""
    from labkit import capture, runner  # noqa: PLC0415 - needs the analysis libraries

    real_install = capture.install

    def install_then_cancel(state):
        os.kill(os.getpid(), signal.SIGINT)
        return real_install(state)

    monkeypatch.setattr(capture, "install", install_then_cancel)
    body = ("from pathlib import Path\n"
            "Path('data/derived/ran.txt').write_text('x', encoding='utf-8')\n")
    script = write_script(project, "23_cancel_setup.py", body)
    result = runner.execute(core.Project(project), script=script, title="설치 중 취소", actor="test")

    assert result["run"]["status"] == "cancelled", result["run"]
    assert not (Path(project) / "data/derived/ran.txt").exists(), "the script ran after the cancel"


def test_output_written_through_the_original_stdout_still_reaches_the_log(project):
    """Anything still holding the stream the run replaced has to be flushed while fd 1 is the log.

    Otherwise the bytes surface after the descriptors are restored and land in front of the CLI's
    own JSON line, which is the line the extension parses."""
    body = "import sys\nsys.__stdout__.write('BUFFERED-OUTPUT')\n"
    code, result = labkit(project, "run", write_script(project, "24_buffered.py", body),
                          "--title", "원래 스트림")
    assert code == 0, result
    assert "BUFFERED-OUTPUT" in result["stdout_tail"], result["stdout_tail"]


# --- text encoding is explicit everywhere -----------------------------------------------------

# A lookbehind for a dot would have excluded exactly the calls this is for, so `.read_text(` and
# `io.open(` match. Deliberately out of scope: tests/ (PR-3 cleans the regression suite up),
# `bin/lab` (PR-2 rewrites the entry points) and the non-Python template files.
ENCODING_CALLS = re.compile(
    r"\b(?:open|read_text|write_text)\s*\(|\bsubprocess\.(?:run|Popen)\s*\(")
TEXT_MODE = re.compile(r"\b(?:text|universal_newlines)\s*=\s*True")
# `open` is not always a file: these take a URL, a socket or an archive and have no encoding.
NON_FILE_OPENERS = frozenset({"webbrowser", "request", "urlopen", "socket", "zipfile", "tarfile",
                              "gzip", "bz2", "lzma", "shelve", "dbm", "sqlite3"})
BINARY_MODE = re.compile(r"""["'][rwax+]*b[rwax+]*["']""")
SOURCE_DIRS = ("kit", "extension", "tools")


def _call_source(text: str, start: int) -> str:
    depth, i = 0, text.index("(", start)
    for j in range(i, min(len(text), i + 2000)):
        if text[j] == "(":
            depth += 1
        elif text[j] == ")":
            depth -= 1
            if depth == 0:
                return text[i:j + 1]
    return text[i:i + 2000]


def _receiver(text: str, start: int) -> str:
    """The name the call is made on, so `webbrowser.open(...)` is not read as a file open."""
    if start == 0 or text[start - 1] != ".":
        return ""
    end = start - 1
    begin = end
    while begin > 0 and (text[begin - 1].isalnum() or text[begin - 1] == "_"):
        begin -= 1
    return text[begin:end]


def unencoded_text_calls(paths) -> list[str]:
    offenders = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        for match in ENCODING_CALLS.finditer(text):
            call = _call_source(text, match.start())
            if _receiver(text, match.start()) in NON_FILE_OPENERS:
                continue
            if "subprocess." in match.group():
                if not TEXT_MODE.search(call):
                    continue  # bytes come back undecoded, so there is nothing to get wrong
            elif BINARY_MODE.search(call):
                continue
            if "encoding=" in call or "_platform.text_kwargs()" in call:
                continue
            line = text[:match.start()].count("\n") + 1
            offenders.append(f"{path}:{line} {call.splitlines()[0][:90]}")
    return offenders


def test_every_text_call_in_the_shipped_code_names_its_encoding():
    """A locale-dependent default turns Korean records into an exception on a Windows machine, and
    reconfiguring this process's streams does not reach a child's pipe or a file."""
    paths = [p for directory in SOURCE_DIRS
             for p in sorted((PACK / directory).rglob("*.py")) if "__pycache__" not in p.parts]
    offenders = unencoded_text_calls(paths)
    assert not offenders, "text without an explicit encoding:\n" + "\n".join(offenders)


def test_the_encoding_scan_catches_the_calls_it_claims_to(tmp_path):
    planted = tmp_path / "planted.py"
    planted.write_text(
        "import io\nimport subprocess\nfrom pathlib import Path\n"
        "Path('a').write_text('한글')\n"            # 1
        "Path('b').read_text()\n"                  # 2
        "io.open('c').read()\n"                    # 3
        "subprocess.run(['x'], text=True)\n"       # 4
        "subprocess.run(['y'], universal_newlines=True)\n"  # 5
        "Path('d').write_text('ok', encoding='utf-8')\n"    # fine
        "open('e', 'rb').read()\n"                          # fine
        "subprocess.run(['z'], capture_output=True)\n"      # fine: bytes
        "webbrowser.open('https://example.invalid')\n",     # fine: not a file
        encoding="utf-8")
    offenders = unencoded_text_calls([planted])
    assert len(offenders) == 5, offenders
