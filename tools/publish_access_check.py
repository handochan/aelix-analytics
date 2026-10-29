"""Another account reads a publish target; a publish over a loopback SMB share (#21 PR-3).

    python tools/publish_access_check.py --output FILE [--account] [--require-account] [--smb]

ADR-0009 checks 1 and 2 (another account's read) and 8 and 9 (the commit and the lock on a share,
Windows). It makes a small project in a temporary folder with its own trust file, builds it twice and
publishes both builds to a folder every account can open, and on POSIX to one inside a folder only
this account can open (where the publish warns `others_cannot_read`). Then it reads each target as a
reader: latest.json, the channel page and every file the newest version's manifest names, by sha256.

- Without --account the reader is this account: what the reader does is tried, nothing is changed.
- --account makes a second OS account (Linux `useradd`, macOS `sysadminctl`, Windows `net user`) and
  reads as it: through `sudo -u`, or on Windows by a reader that takes the account's logon token
  (LogonUserW, ImpersonateLoggedOnUser) before it opens anything. The reader reports its uid or account
  name, and a read by any other account fails. The folder only this account can open (on Windows the
  publisher's own temporary folder) must then not be readable.
- --smb (Windows) shares a folder as \\\\localhost\\<name>, publishes there and reads it back, as this
  account and, with --account, as the second one.

--account and --smb change the machine, so they run only on GitHub Actions (GITHUB_ACTIONS=true) or
with --allow-machine-changes. A step the runner does not allow (an account or a share it does not
make, a process it does not start as another account) is `not_run` with its reason (OQ6); with
--require-account a not_run account is a failure. A check that ran and did not give what it should is
a failure (exit 1). It writes one JSON file.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import string
import subprocess
import sys
import tempfile
from pathlib import Path

PACK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACK / "src"))
from aelix_analytics import core  # noqa: E402
from aelix_analytics.publishing import build, checks, publish  # noqa: E402

ORIGIN = core.Origin("human", "cli")
ACCOUNT = "analyticsreader" if sys.platform == "darwin" else "analytics-reader"
CHANNEL = "access-check"

READER = r'''
import hashlib, json, os, sys
root, channel = sys.argv[1], sys.argv[2]
out = {"files": 0, "bytes": 0, "errors": []}
if os.name == "nt":
    import ctypes
    from ctypes import wintypes
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    if os.environ.get("READER_NAME"):
        # This thread takes the second account's logon token: every open below is checked as that account.
        token = wintypes.HANDLE()
        for kind in (2, 8, 3):  # interactive; network keeping the credentials (for a share); network
            if advapi.LogonUserW(os.environ["READER_NAME"], ".", os.environ["READER_LOGON"], kind, 0, ctypes.byref(token)):
                out["logon_type"] = kind
                break
            out.setdefault("logon_errors", []).append({"type": kind, "winerror": ctypes.get_last_error()})
        else:
            print(json.dumps({"not_run": {"step": "LogonUserW", **out}}))
            sys.exit(3)
        if not advapi.ImpersonateLoggedOnUser(token):
            print(json.dumps({"not_run": {"step": "ImpersonateLoggedOnUser", "winerror": ctypes.get_last_error()}}))
            sys.exit(3)
    size = wintypes.DWORD(257)
    name = ctypes.create_unicode_buffer(257)
    out["account"] = name.value if advapi.GetUserNameW(name, ctypes.byref(size)) else None
else:
    out["uid"] = os.getuid()
try:
    folder = os.path.join(root, "projects", channel)
    with open(os.path.join(folder, "latest.json"), encoding="utf-8") as fh:
        out["latest"] = json.load(fh)["latest"]
    with open(os.path.join(folder, "index.html"), encoding="utf-8") as fh:
        out["page_names_latest"] = 'content="%s"' % out["latest"] in fh.read()
    version = os.path.join(root, "publications", out["latest"])
    with open(os.path.join(version, "publication.json"), encoding="utf-8") as fh:
        files = json.load(fh)["files"]
    for entry in files:
        with open(os.path.join(version, *entry["path"].split("/")), "rb") as fh:
            data = fh.read()
        if hashlib.sha256(data).hexdigest() != entry["sha256"]:
            out["errors"].append({"path": entry["path"], "error": "sha256"})
        out["files"] += 1
        out["bytes"] += len(data)
except OSError as exc:
    out["errors"].append({"path": exc.filename, "error": type(exc).__name__, "winerror": getattr(exc, "winerror", None)})
print(json.dumps(out))
'''


def run(argv, **kwargs):
    """A command's code and output; a command that does not end in 180 seconds gives code None."""
    try:
        done = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180, **kwargs)
    except subprocess.TimeoutExpired:
        return None, "", "it did not end in 180 seconds"
    return done.returncode, (done.stdout or "")[-2000:], (done.stderr or "")[-2000:]


def powershell(script, env=None):
    return run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
               env={**os.environ, **(env or {})})


# --- the second account ----------------------------------------------------------------------------

def make_account():
    """A second OS account; returns (how to read as it, what happened) or (None, the reason)."""
    if os.name == "nt":
        # 14 characters at most: above that `net user` asks a yes/no question no one answers here.
        logon = "Aa1!" + "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(10))
        code, out, err = run(["net", "user", ACCOUNT, logon, "/add"])
        if code != 0 and "already" not in (out + err).lower():
            return None, {"step": "net user /add", "code": code, "stderr": err or out}
        return {"name": ACCOUNT, "logon": logon}, {"made": "net user"}
    if sys.platform == "darwin":
        logon = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(24))
        code, out, err = run(["sudo", "-n", "sysadminctl", "-addUser", ACCOUNT, "-password", logon])
        if code != 0 or run(["id", "-u", ACCOUNT])[0] != 0:
            return None, {"step": "sysadminctl -addUser", "code": code, "stderr": err or out}
    else:
        if run(["id", "-u", ACCOUNT])[0] != 0:
            code, out, err = run(["sudo", "-n", "useradd", "--no-create-home", "--shell", "/usr/sbin/nologin", ACCOUNT])
            if code != 0:
                return None, {"step": "useradd", "code": code, "stderr": err or out}
    python = None
    tried = []
    for candidate in ("/usr/bin/python3", "/opt/homebrew/bin/python3", "/usr/local/bin/python3",
                      "/Library/Frameworks/Python.framework/Versions/Current/bin/python3"):
        if not os.path.exists(candidate):
            continue
        code, out, err = run(["sudo", "-n", "-u", ACCOUNT, candidate, "-c", "import os; print(os.getuid())"], cwd="/")
        tried.append({"python": candidate, "code": code, "stderr": err[-300:]})
        if code == 0:
            python = candidate
            break
    if python is None:
        return None, {"step": "a Python the account can run", "tried": tried}
    return {"name": ACCOUNT, "python": python}, {"made": "sysadminctl" if sys.platform == "darwin" else "useradd",
                                                 "uid": int(run(["id", "-u", ACCOUNT])[1].strip() or -1)}


def read_as(account, root: Path, work: Path):
    """The reader run as `account` (None: this account). Returns its report, or None and why not."""
    script = work / "reader.py"
    script.write_text(READER, encoding="utf-8")
    if os.name == "nt":
        env = {**os.environ, **({"READER_NAME": account["name"], "READER_LOGON": account["logon"]} if account else {})}
        argv = [sys.executable, "-I", str(script), str(root), CHANNEL]
    else:
        os.chmod(script, 0o644)
        env = None
        argv = [sys.executable, "-I", str(script), str(root), CHANNEL] if account is None else [
            "sudo", "-n", "-u", account["name"], account["python"], "-I", str(script), str(root), CHANNEL]
    code, stdout, err = run(argv, cwd=str(work) if os.name == "nt" else "/", env=env)
    try:
        read = json.loads(stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None, {"step": "run the reader", "code": code, "stderr": err or stdout}
    if "not_run" in read:
        return None, read["not_run"]
    if any(e.get("winerror") == 1346 for e in read["errors"]):  # an identification-level token opens nothing
        return None, {"step": "ImpersonateLoggedOnUser", "reader": read}
    return read, None


# --- the cases -------------------------------------------------------------------------------------

def publish_both(project, ids, folder):
    """Both builds published to `folder`; the last one's result and how its version was committed."""
    results = [publish.publish(project, pid, folder, origin=ORIGIN, channel=CHANNEL) for pid in ids]
    last = results[-1]
    receipt = json.loads(project.journal.read_text(encoding="utf-8").splitlines()[-1])
    return {"outcomes": [r["outcome"] for r in results], "warnings": [w["code"] for w in last.get("warnings") or ()],
            "lock": last["lock"], "steps": last["steps"], "pointers": last.get("pointers"),
            "commit_method": receipt["details"].get("commit_method")}


REFUSED = frozenset({"PermissionError", "UnauthorizedAccessException"})


def read_by(read, account) -> bool:
    """The reader ran as `account` (None: as this one), by the uid or the name it reports."""
    if os.name == "nt":
        name = str(read.get("account") or "").lower()
        return name == account["name"].lower() if account else bool(name)
    return read.get("uid") != os.getuid() if account else read.get("uid") == os.getuid()


def judged(case, read, why, *, expect_read, account):
    """A case passes when the reader, as `account`, got what it should: every file by its hash, or
    (expect_read False) a refusal of access and nothing else."""
    if read is None:
        case.update(reader="not_run", reader_reason=why, result="not_run")
        return
    case["reader"] = read
    whole = not read["errors"] and read["files"] > 0 and read.get("page_names_latest") is True
    refused = bool(read["errors"]) and all(e.get("error") in REFUSED for e in read["errors"])
    case["result"] = "pass" if read_by(read, account) and (whole if expect_read else refused) else "fail"


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--account", action="store_true")
    parser.add_argument("--require-account", action="store_true")
    parser.add_argument("--smb", action="store_true")
    parser.add_argument("--allow-machine-changes", action="store_true")
    args = parser.parse_args()
    if (args.account or args.smb) and os.environ.get("GITHUB_ACTIONS") != "true" and not args.allow_machine_changes:
        parser.error("--account and --smb change this machine: they run on GitHub Actions or with --allow-machine-changes")
    if args.smb and os.name != "nt":
        parser.error("--smb is the Windows loopback share")
    if os.name == "nt":
        # Not mkdtemp: from Python 3.13 its mode 0o700 gives a Windows folder only this account can open.
        base = Path(os.environ.get("SystemDrive", "C:") + "\\", f"analytics-access-{secrets.token_hex(6)}")
        base.mkdir()
    else:
        base = Path(tempfile.mkdtemp(prefix="analytics-access-", dir="/tmp"))
    private = Path(tempfile.mkdtemp(prefix="analytics-access-project-"))
    os.environ["AELIX_ANALYTICS_TRUST_FILE"] = str(private / "trust" / "lab-projects.json")
    for name in ("AELIX_LAB_HOME", "AELIX_LAB_TRUST_FILE"):
        os.environ.pop(name, None)
    old_umask = os.umask(0o022) if os.name != "nt" else None
    out = {"kit_version": core.KIT_VERSION, "platform": sys.platform, "python": sys.version.split()[0],
           "account": None, "cases": {}}
    failed = False
    try:
        failed = cases(args, out, base, private)
    except Exception as exc:  # noqa: BLE001 - the evidence says how the check itself ended
        import traceback

        out["error"] = {"type": type(exc).__name__, "message": str(exc), "traceback": traceback.format_exc()[-4000:]}
        failed = True
    finally:
        if old_umask is not None:
            os.umask(old_umask)
        out["failed"] = failed
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        for folder in (base, private):
            shutil.rmtree(folder, ignore_errors=True)
    print(json.dumps({"ok": not failed, "output": str(args.output), "error": out.get("error", {}).get("message"),
                      "cases": {k: v.get("result") for k, v in out["cases"].items()}}))
    return 1 if failed else 0


def cases(args, out, base: Path, private: Path) -> bool:
    """Every case; True when one ran and did not give what it should (or a required one did not run)."""
    failed = False
    if os.name != "nt":
        os.chmod(base, 0o755)
    work = base / "reader"
    work.mkdir()
    if os.name != "nt":
        os.chmod(work, 0o755)
    project = core.init_project(private / "project", title="Access check", origin=ORIGIN)
    ids = [build.build_publication(project, origin=ORIGIN)["publication_id"] for _ in range(2)]
    account, made = (make_account() if args.account else (None, {"made": "none: this account reads"}))
    out["account"] = {"name": account["name"] if account else None, **(made or {})}
    if args.account and account is None:
        out["account"]["state"] = "not_run"
        failed |= args.require_account
    folders = {"open": base / "share"}
    if os.name != "nt":
        shut = base / "shut"
        shut.mkdir(mode=0o700)
        os.chmod(shut, 0o700)
        folders["shut"] = shut / "share"
    elif account is not None:
        folders["shut"] = private / "share"  # the publisher's own profile folder
    stuck = []  # a reader as the account that did not end: later cases do not wait for it again

    def read_account(folder):
        if stuck:
            return None, {"step": "run the reader", "not_tried": "it did not end in an earlier case"}
        read, why = read_as(account, folder, work)
        if read is None and why.get("code") is None and why.get("step") == "run the reader":
            stuck.append(why)
        return read, why

    for name, folder in folders.items():
        case = out["cases"][name] = {"folder": os.fspath(folder), **publish_both(project, ids, folder)}
        if case["outcomes"] != ["published", "published"]:
            case["result"] = "fail"
        elif args.account and account is None:
            # What this account reads still says whether the reader works here (not judged).
            self_read, why = read_as(None, folder, work)
            case.update(reader="not_run", result="not_run", self_reader=self_read if self_read is not None else why)
        else:
            read, why = read_account(folder)
            judged(case, read, why, expect_read=name == "open" or account is None, account=account)
            if name == "shut" and os.name != "nt" and "others_cannot_read" not in case["warnings"]:
                case["result"] = "fail"  # design 9.2: the publish says others may not read it
        if case["result"] == "fail" or (case["result"] == "not_run" and args.require_account):
            failed = True
    if args.smb:
        out["cases"]["smb"] = smb = {}
        share_name = base.name
        folder = base / "smb"
        folder.mkdir()
        code, stdout, err = powershell(f"New-SmbShare -Name '{share_name}' -Path '{folder}' -FullAccess 'Everyone' | Out-Null")
        if code != 0:
            smb.update(result="not_run", reason={"step": "New-SmbShare", "code": code, "stderr": err or stdout})
        else:
            unc = Path(f"\\\\localhost\\{share_name}\\target")
            try:
                smb.update(folder=os.fspath(unc), **publish_both(project, ids, unc))
                version = unc / "publications" / ids[-1]
                smb["reread"] = checks.verify_package(version)["package_sha256"]
                smb["result"] = "pass" if smb["outcomes"] == ["published", "published"] else "fail"
                if account is not None and smb["result"] == "pass":
                    read, why = read_account(unc)
                    smb["second_account"] = read if read is not None else {"state": "not_run", "reason": why}
                    if read is not None and (read["errors"] or not read["files"] or not read_by(read, account)):
                        smb["result"] = "fail"
            except Exception as exc:  # noqa: BLE001 - what a share does is the measurement
                smb.update(result="fail", error=f"{type(exc).__name__}: {exc}")
            finally:
                powershell(f"Remove-SmbShare -Name '{share_name}' -Force")
        failed |= smb["result"] == "fail"
    return failed


if __name__ == "__main__":
    sys.exit(main())
