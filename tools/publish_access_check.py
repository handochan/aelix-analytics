"""Another account reads a publish target; a publish over a loopback SMB share (#21 PR-3).

    python tools/publish_access_check.py --output FILE [--account] [--require-account] [--smb]

ADR-0009 checks 1 and 2 (another account's read) and 8 and 9 (the commit and the lock on a share,
Windows). It makes a small project in a temporary folder with its own trust file, builds it twice and
publishes both builds to a folder every account can open, and on POSIX to one inside a folder only
this account can open (where the publish warns `others_cannot_read`). Then it reads each target as a
reader: latest.json, the channel page and every file the newest version's manifest names, by sha256.

- Without --account the reader is this account: what the reader does is tried, nothing is changed.
- --account makes a second OS account (Linux `useradd`, macOS `sysadminctl`, Windows `net user`) and
  reads as it (`sudo -u`; on Windows `Start-Process -Credential` and a PowerShell reader). The folder
  only this account can open must then not be readable.
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
import time
from pathlib import Path

PACK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACK / "src"))
from aelix_analytics import core  # noqa: E402
from aelix_analytics.publishing import build, checks, publish  # noqa: E402

ORIGIN = core.Origin("human", "cli")
ACCOUNT = "analyticsreader" if sys.platform == "darwin" else "analytics-reader"
CHANNEL = "access-check"

POSIX_READER = r'''
import hashlib, json, os, sys
root, channel = sys.argv[1], sys.argv[2]
out = {"uid": os.getuid(), "files": 0, "bytes": 0, "errors": []}
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
    out["errors"].append({"path": exc.filename, "error": type(exc).__name__})
print(json.dumps(out))
'''

WINDOWS_READER = r'''
param([string]$Root, [string]$Channel, [string]$Out)
# Windows PowerShell 5.1 writes some arrays as {"value": [...], "Count": n} through this type data.
Remove-TypeData System.Array -ErrorAction SilentlyContinue
$r = [ordered]@{account = [Environment]::UserName; files = 0; bytes = 0; errors = @()}
try {
    $folder = Join-Path (Join-Path $Root "projects") $Channel
    $r.latest = (Get-Content -LiteralPath (Join-Path $folder "latest.json") -Raw -Encoding UTF8 -ErrorAction Stop | ConvertFrom-Json).latest
    $page = Get-Content -LiteralPath (Join-Path $folder "index.html") -Raw -Encoding UTF8 -ErrorAction Stop
    $r.page_names_latest = $page.Contains('content="' + $r.latest + '"')
    $version = Join-Path (Join-Path $Root "publications") $r.latest
    $manifest = Get-Content -LiteralPath (Join-Path $version "publication.json") -Raw -Encoding UTF8 -ErrorAction Stop | ConvertFrom-Json
    foreach ($entry in $manifest.files) {
        $path = Join-Path $version ($entry.path -replace "/", "\")
        $hash = (Get-FileHash -LiteralPath $path -Algorithm SHA256 -ErrorAction Stop).Hash.ToLowerInvariant()
        if ($hash -ne $entry.sha256) { $r.errors += @{path = $entry.path; error = "sha256"} }
        $r.files += 1
        $r.bytes += (Get-Item -LiteralPath $path -ErrorAction Stop).Length
    }
} catch {
    $r.errors += @{path = $null; error = $_.Exception.GetType().Name; message = $_.Exception.Message}
}
$r | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $Out -Encoding UTF8
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
    if os.name == "nt":
        script = work / "reader.ps1"
        script.write_text(WINDOWS_READER, encoding="utf-8-sig")
        out = work / f"read-{time.monotonic_ns()}.json"
        if account is None:
            code, stdout, err = run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                     "-File", str(script), str(root), CHANNEL, str(out)])
        else:
            code, stdout, err = powershell(
                "$c = New-Object System.Management.Automation.PSCredential($env:READER_NAME, "
                "(ConvertTo-SecureString $env:READER_LOGON -AsPlainText -Force)); "
                "$p = Start-Process -FilePath powershell.exe -Credential $c -Wait -PassThru -WindowStyle Hidden "
                "-WorkingDirectory $env:READER_WORK -ArgumentList @('-NoProfile','-NonInteractive','-ExecutionPolicy',"
                "'Bypass','-File',$env:READER_SCRIPT,$env:READER_ROOT,$env:READER_CHANNEL,$env:READER_OUT); "
                "exit $p.ExitCode",
                env={"READER_NAME": account["name"], "READER_LOGON": account["logon"], "READER_WORK": str(work),
                     "READER_SCRIPT": str(script), "READER_ROOT": str(root), "READER_CHANNEL": CHANNEL,
                     "READER_OUT": str(out)})
        if not out.exists():
            return None, {"step": "start the reader", "code": code, "stderr": err or stdout}
        read = json.loads(out.read_text(encoding="utf-8-sig"))
        for key in ("errors",):  # the 5.1 shape, should the type data have stayed
            if isinstance(read.get(key), dict) and "value" in read[key]:
                read[key] = read[key]["value"]
        errors = read.get("errors")
        read["errors"] = errors if isinstance(errors, list) else [] if errors is None else [errors]
        return read, None
    script = work / "reader.py"
    script.write_text(POSIX_READER, encoding="utf-8")
    os.chmod(script, 0o644)
    argv = [sys.executable, str(script), str(root), CHANNEL] if account is None else [
        "sudo", "-n", "-u", account["name"], account["python"], str(script), str(root), CHANNEL]
    code, stdout, err = run(argv, cwd="/")
    try:
        return json.loads(stdout.strip().splitlines()[-1]), None
    except (ValueError, IndexError):
        return None, {"step": "run the reader", "code": code, "stderr": err or stdout}


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


def judged(case, read, why, *, expect_read):
    """A case passes when the reader got what it should: every file by its hash, or (expect_read
    False) a refusal of access and nothing else."""
    if read is None:
        case.update(reader="not_run", reader_reason=why, result="not_run")
        return
    case["reader"] = read
    whole = not read["errors"] and read["files"] > 0 and read.get("page_names_latest") is True
    refused = bool(read["errors"]) and all(e.get("error") in REFUSED for e in read["errors"])
    case["result"] = "pass" if (whole if expect_read else refused) else "fail"


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
    base = Path(tempfile.mkdtemp(prefix="analytics-access-", dir=os.environ.get("SystemDrive", "C:") + "\\")
                if os.name == "nt" else tempfile.mkdtemp(prefix="analytics-access-", dir="/tmp"))
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
    for name, folder in folders.items():
        case = out["cases"][name] = {"folder": os.fspath(folder), **publish_both(project, ids, folder)}
        if case["outcomes"] != ["published", "published"]:
            case["result"] = "fail"
        elif args.account and account is None:
            # What this account reads still says whether the reader works here (not judged).
            self_read, why = read_as(None, folder, work)
            case.update(reader="not_run", result="not_run", self_reader=self_read if self_read is not None else why)
        else:
            read, why = read_as(account, folder, work)
            judged(case, read, why, expect_read=name == "open" or account is None)
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
                    read, why = read_as(account, unc, work)
                    smb["second_account"] = read if read is not None else {"state": "not_run", "reason": why}
                    if read is not None and (read["errors"] or not read["files"]):
                        smb["result"] = "fail"
            except Exception as exc:  # noqa: BLE001 - what a share does is the measurement
                smb.update(result="fail", error=f"{type(exc).__name__}: {exc}")
            finally:
                powershell(f"Remove-SmbShare -Name '{share_name}' -Force")
        failed |= smb["result"] == "fail"
    return failed


if __name__ == "__main__":
    sys.exit(main())
