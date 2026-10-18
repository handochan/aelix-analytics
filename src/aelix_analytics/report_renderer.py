"""Byte-only ReportRenderer contract (ADR-0013); no project, records or rendering imports."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol


@dataclass(frozen=True)
class ReportView:
    """Regenerable files and original download links. Support never asserts shareability."""

    files: Mapping[str, bytes]
    originals: Mapping[str, str]
    supported: bool
    reasons: tuple[str, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, "files", MappingProxyType(dict(self.files)))
        object.__setattr__(self, "originals", MappingProxyType(dict(self.originals)))
        object.__setattr__(self, "reasons", tuple(self.reasons))


class ReportRenderer(Protocol):
    def render(self, files: Mapping[str, bytes], *, entry: str, paths: Mapping[str, str]) -> ReportView:
        """Receive selected original bytes and explicit package paths, never raw records.

        No filesystem/model/network/generation lookup. Invalid inputs raise ValueError; unsupported
        formats retain original links and a reason. All emitted files are derived, never originals.
        Relative resources resolve from entry and may use only the supplied mapping. The caller
        owns original copying, project identity/status wrapping, and independent publication checks.
        """
        ...
