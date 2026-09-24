import csv
import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from labkit import _platform

PACK = Path(__file__).resolve().parents[1]
KIT = PACK / "kit"


def kit_env(**extra) -> dict:
    # PYTHONUNBUFFERED changes how a kit lays out its run streams, which tests check; a test that
    # wants it passes it (review 8, critic-4).
    env = {k: v for k, v in os.environ.items() if k not in ("VIRTUAL_ENV", "PYTHONUNBUFFERED")}
    env.update({"PYTHONPATH": str(KIT), "MPLBACKEND": "Agg", "LAB_ACTOR": "test",
                "PYTHONDONTWRITEBYTECODE": "1"})
    env.update(extra)
    return env


def labkit(project, *args, timeout=300):
    """Run `python -m labkit` like the extension does; returns (exit code, last JSON object)."""
    cmd = [sys.executable, "-m", "labkit", "--project", str(project), *map(str, args)]
    # The records are not ASCII and this reads them through a pipe, which follows the locale
    # unless it is told otherwise.
    proc = subprocess.run(cmd, capture_output=True, env=kit_env(), timeout=timeout,
                          **_platform.text_kwargs())
    lines = [line for line in proc.stdout.splitlines() if line.startswith("{")]
    assert lines, f"no JSON from {cmd}\nstdout={proc.stdout}\nstderr={proc.stderr}"
    return proc.returncode, json.loads(lines[-1])


def write_sales_csv(path: Path, rows: int = 120) -> Path:
    regions = ["서울", "부산", "대구"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["order_date", "region", "channel", "revenue"])
        for i in range(rows):
            month = 1 + i % 12
            writer.writerow([f"2025-{month:02d}-{1 + i % 27:02d}", regions[i % 3],
                             "online" if i % 2 else "store", 1000 + (i * 37) % 500])
    return path


@pytest.fixture(autouse=True, scope="session")
def sigint_as_an_interactive_python_has_it():
    """Tests of the run guard assume SIGINT is handled, as in an interactive Python. A suite started
    in the background inherits it ignored, and the kits and children the tests start inherit that
    too (review 8, honesty-2); the ignored disposition is put back afterwards."""
    ignored = signal.getsignal(signal.SIGINT) is signal.SIG_IGN
    if ignored:
        signal.signal(signal.SIGINT, signal.default_int_handler)
    yield
    if ignored:
        signal.signal(signal.SIGINT, signal.SIG_IGN)


@pytest.fixture(autouse=True)
def isolated_trust_file(tmp_path, monkeypatch):
    """Projects created by tests must never be registered in the user's real trust file."""
    monkeypatch.setenv("AELIX_LAB_TRUST_FILE", str(tmp_path / "trust" / "lab-projects.json"))


@pytest.fixture
def project(tmp_path):
    """A new project with data/raw/sales.parquet imported."""
    sys.path.insert(0, str(KIT))
    from labkit import core

    root = tmp_path / "proj"
    core.init_project(root, title="테스트 분석", goal="테스트 목표", template_dir=KIT / "template",
                      actor="test")
    source = write_sales_csv(tmp_path / "sales.csv")
    code, result = labkit(root, "import", source, "--name", "sales", "--description", "test data")
    assert code == 0 and result["ok"], result
    return root


def write_script(project: Path, name: str, body: str) -> str:
    path = project / "scripts" / name
    path.write_text(body, encoding="utf-8")
    return f"scripts/{name}"
