"""Where the installed distribution keeps its kit: the runtime root, `aelix_analytics/kit/`.

Standard library only: the aelix process imports this (through aelix_analytics.extension), and so
does the `analytics` command line.
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path


def kit_dir() -> Path:
    """The runtime root: the directory that holds this distribution's copy of the `aelix_analytics`
    package, `kit/aelix_analytics/` beside this module.

    A child process gets exactly this directory on PYTHONPATH and imports `aelix_analytics` from it —
    not the site-packages it is in (where this package itself refuses to run the kit), which would bring the host's packages into an analysis environment
    (docs/DESIGN-CONTINUITY-AND-REUSE.md section 8). It has to be a directory of real files, as an
    installer that unpacks the wheel leaves it: the kit reads its own files (from a zip, a run would
    record the hash of empty input as `env.kit_sha256`), and the aelix process loads `core`,
    `_platform` and `relations` from files. A zip import is refused here rather than passed on."""
    kit = resources.files(__package__) / "kit"
    if not isinstance(kit, Path) or not (kit / "aelix_analytics" / "core.py").is_file():
        raise RuntimeError(f"aelix-analytics: its kit is not installed as files ({kit}); "
                           "reinstall the aelix-analytics package")
    return kit.resolve()
