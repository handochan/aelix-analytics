"""Issue #8 PR-3 design input: what this runner's file system and session allow. Stdlib only.

Not a test and not the repository's CI: a one-off observation on a throwaway branch, so PR-3's design
rests on measured runner capabilities instead of inferences from the runner-images source. Every
probe catches its own failure and records it; nothing here fails the job.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

NT = os.name == "nt"


def attempt(fn):
    try:
        return {"ok": True, "value": fn()}
    except BaseException as exc:  # noqa: BLE001 — an observation records whatever happened
        return {"ok": False, "error": repr(exc), "winerror": getattr(exc, "winerror", None),
                "errno": getattr(exc, "errno", None), "trace": traceback.format_exc()[-800:]}


def links(base: Path) -> dict:
    target_dir = base / "target_dir"
    target_dir.mkdir()
    (target_dir / "inside.txt").write_text("x", encoding="utf-8")
    target_file = base / "target.txt"
    target_file.write_text("x", encoding="utf-8")
    out = {}

    def file_symlink():
        os.symlink(target_file, base / "file_link.txt")
        return {"islink": os.path.islink(base / "file_link.txt"),
                "realpath": os.path.realpath(base / "file_link.txt")}

    def dir_symlink():
        os.symlink(target_dir, base / "dir_link", target_is_directory=True)
        return {"islink": os.path.islink(base / "dir_link"),
                "realpath": os.path.realpath(base / "dir_link"),
                "listing": os.listdir(base / "dir_link")}

    def relative_dir_symlink():
        os.symlink("target_dir", base / "rel_dir_link", target_is_directory=True)
        return {"realpath": os.path.realpath(base / "rel_dir_link")}

    def junction():
        import _winapi

        _winapi.CreateJunction(str(target_dir), str(base / "junction"))
        j = base / "junction"
        walked = [d for d, _, _ in os.walk(base)]
        return {"islink": os.path.islink(j), "isjunction": getattr(os.path, "isjunction", lambda p: None)(j),
                "realpath": os.path.realpath(j), "walk_descends": any("junction" in d for d in walked)}

    def hardlink():
        os.link(target_file, base / "hard.txt")
        return os.path.samefile(target_file, base / "hard.txt")

    def symlink_loop():
        os.symlink(base / "loop_b", base / "loop_a", target_is_directory=True)
        os.symlink(base / "loop_a", base / "loop_b", target_is_directory=True)
        return {"realpath": attempt(lambda: os.path.realpath(base / "loop_a")),
                "realpath_strict": attempt(lambda: os.path.realpath(base / "loop_a", strict=True))}

    out["file_symlink"] = attempt(file_symlink)
    out["dir_symlink"] = attempt(dir_symlink)
    out["relative_dir_symlink"] = attempt(relative_dir_symlink)
    out["junction"] = attempt(junction) if NT else {"ok": False, "error": "not Windows"}
    out["hardlink"] = attempt(hardlink)
    out["symlink_loop"] = attempt(symlink_loop)
    return out


def long_paths(base: Path) -> dict:
    out = {}
    if NT:
        def registry():
            import winreg

            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem") as k:
                return winreg.QueryValueEx(k, "LongPathsEnabled")[0]

        def rtl():
            fn = ctypes.windll.ntdll.RtlAreLongPathsEnabled
            fn.restype = ctypes.c_ubyte
            return bool(fn())

        out["registry_LongPathsEnabled"] = attempt(registry)
        out["RtlAreLongPathsEnabled"] = attempt(rtl)
    deep = base
    while len(str(deep)) < 300:
        deep = deep / ("d" * 40)
    out["deep_path_length"] = len(str(deep))

    def make():
        deep.mkdir(parents=True)
        f = deep / "file.txt"
        f.write_text("x", encoding="utf-8")
        return len(str(f))

    out["mkdir_and_write"] = attempt(make)

    def replace():
        tmp = deep / "file.txt.tmp"
        tmp.write_text("y", encoding="utf-8")
        os.replace(tmp, deep / "file.txt")
        return (deep / "file.txt").read_text(encoding="utf-8")

    out["replace"] = attempt(replace)
    if NT:
        def lock():
            import msvcrt

            with open(deep / "lock", "a+b") as fh:
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            return True

        out["msvcrt_locking"] = attempt(lock)
    out["child_with_deep_cwd"] = attempt(lambda: subprocess.run(
        [sys.executable, "-c", "import os; print(len(os.getcwd()))"], cwd=deep, capture_output=True,
        text=True, encoding="utf-8", timeout=60).__dict__ | {"args": None})
    here = os.getcwd()

    def chdir():
        os.chdir(deep)
        try:
            return len(os.getcwd())
        finally:
            os.chdir(here)

    out["chdir"] = attempt(chdir)
    out["walk_finds_file"] = attempt(lambda: any("file.txt" in fs for _, _, fs in os.walk(base)))
    out["component_300"] = attempt(lambda: (base / ("n" * 300)).write_text("x", encoding="utf-8"))
    return out


def case(base: Path) -> dict:
    out = {}
    d = base / "case"
    d.mkdir()
    (d / "Mixed.txt").write_text("x", encoding="utf-8")
    out["temp_ignores_case"] = (d / "mixed.txt").exists()
    out["realpath_of_other_case"] = os.path.realpath(d / "mIXED.TXT")
    out["listdir"] = os.listdir(d)
    if NT:
        sens = base / "sensitive"
        sens.mkdir()
        r = attempt(lambda: subprocess.run(["fsutil.exe", "file", "setCaseSensitiveInfo", str(sens), "enable"],
                                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                                           timeout=60).__dict__ | {"args": None})
        out["setCaseSensitiveInfo"] = r

        def two_names():
            (sens / "A.txt").write_text("upper", encoding="utf-8")
            (sens / "a.txt").write_text("lower", encoding="utf-8")
            return {"listdir": os.listdir(sens), "upper": (sens / "A.txt").read_text(encoding="utf-8"),
                    "realpath_of_lower": os.path.realpath(sens / "a.txt")}

        out["case_sensitive_dir"] = attempt(two_names)
    # Unicode normalisation: an NFD name read back through its NFC spelling.
    import unicodedata

    nfc = "한글.txt"
    nfd = unicodedata.normalize("NFD", nfc)
    (d / nfd).write_text("x", encoding="utf-8")
    out["nfd_written_nfc_exists"] = (d / nfc).exists()
    out["nfd_listdir_is_nfc"] = [unicodedata.is_normalized("NFC", n) for n in os.listdir(d) if n.endswith(".txt")]
    return out


def session() -> dict:
    out = {"temp": tempfile.gettempdir(), "temp_realpath": os.path.realpath(tempfile.gettempdir()),
           "cwd": os.getcwd(), "python": sys.version, "executable": sys.executable,
           "platform": platform.platform(), "fs_encoding": sys.getfilesystemencoding(),
           "preferred_encoding": __import__("locale").getpreferredencoding(False),
           "image": {k: os.environ.get(k) for k in ("ImageOS", "ImageVersion", "RUNNER_OS")}}
    if NT:
        k32 = ctypes.windll.kernel32
        sid = ctypes.c_ulong()
        k32.ProcessIdToSessionId(os.getpid(), ctypes.byref(sid))
        out["console"] = {"GetConsoleCP": k32.GetConsoleCP(), "GetConsoleOutputCP": k32.GetConsoleOutputCP(),
                          "GetConsoleWindow": bool(k32.GetConsoleWindow()), "session_id": sid.value,
                          "ACP": k32.GetACP(), "OEMCP": k32.GetOEMCP()}
        out["is_admin"] = attempt(lambda: bool(ctypes.windll.shell32.IsUserAnAdmin()))

        def devmode():
            import winreg

            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"SOFTWARE\Microsoft\Windows\CurrentVersion\AppModelUnlock") as k:
                return winreg.QueryValueEx(k, "AllowDevelopmentWithoutDevLicense")[0]

        out["developer_mode"] = attempt(devmode)
        out["whoami_groups_high"] = attempt(lambda: "S-1-16-12288" in subprocess.run(
            ["whoami", "/groups"], capture_output=True, text=True, encoding="utf-8", errors="replace").stdout)
    else:
        out["euid"] = os.geteuid()
    out["git_autocrlf"] = attempt(lambda: subprocess.run(
        ["git", "config", "--show-origin", "--get", "core.autocrlf"], capture_output=True, text=True,
        encoding="utf-8").stdout.strip())
    out["git_eol"] = attempt(lambda: subprocess.run(
        ["git", "ls-files", "--eol", "kit/labkit/core.py", "kit/labkit/template/CONTEXT.md", "bin/lab"],
        capture_output=True, text=True, encoding="utf-8").stdout.strip())
    out["crlf_in_checkout"] = attempt(lambda: b"\r\n" in Path("kit/labkit/core.py").read_bytes())
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", required=True)
    a = ap.parse_args()
    base = Path(tempfile.mkdtemp(prefix="pr3-probe-"))
    result = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "session": session()}
    for name, fn in (("links", links), ("long_paths", long_paths), ("case", case)):
        sub = base / name
        sub.mkdir()
        result[name] = attempt(lambda fn=fn, sub=sub: fn(sub))
    shutil.rmtree(base, ignore_errors=True)
    Path(a.output).parent.mkdir(parents=True, exist_ok=True)
    Path(a.output).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=1)[:20000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
