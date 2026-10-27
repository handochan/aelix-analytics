"""Immutable publication values. No record objects, project objects or I/O adapters."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal


class PublicationError(ValueError):
    def __init__(self, code: str, message: str, *, details=None):
        self.code, self.message, self.details = code, message, details or {}
        super().__init__(message)


@dataclass(frozen=True, order=True)
class ItemKey:
    category: str
    identity: str


def page_key(key) -> str:
    """The opaque page/folder name of an included item (never its identity, which may be private)."""
    return hashlib.sha256(f"{key.category}:{key.identity}".encode()).hexdigest()[:16]


@dataclass(frozen=True)
class Text:
    value: str
    origin: Literal["kit", "user"]
    source: tuple[ItemKey, str] | None = None


@dataclass(frozen=True)
class Object:
    """User-authored structured values; keys are user text too, never HTML attributes."""

    entries: tuple[tuple[Text, object], ...]


@dataclass(frozen=True)
class Field:
    name: str
    value: object


@dataclass(frozen=True)
class Reference:
    relation: str | None
    binding: str | None
    basis: str | None
    state: str
    reason: str | None
    category: str
    target: ItemKey | None
    confirmed: bool = False
    candidates: tuple[ItemKey, ...] = ()
    omitted_candidates: int = 0


@dataclass(frozen=True)
class FileSlot:
    package_path: str
    role: str
    verified: Literal["recorded_hash", "no_recorded_hash", "kit_generated"]
    format: str | None = None


@dataclass(frozen=True)
class FileNotice:
    role: str
    reason: str


@dataclass(frozen=True)
class CopyEntry:
    item: ItemKey
    source: str
    package_path: str
    role: str
    recorded_sha256: str | None
    current_sha256: str
    bytes: int


@dataclass(frozen=True)
class ShareItem:
    key: ItemKey
    kind: str
    fields: tuple[Field, ...]
    references: tuple[Reference, ...] = ()
    files: tuple[FileSlot, ...] = ()
    file_notices: tuple[FileNotice, ...] = ()
    # Fixed attachment edges inside this copy (5.2): a Visualization version's run, a run's code and data.
    attachments: tuple[ItemKey, ...] = ()


@dataclass(frozen=True)
class ContentBasis:
    content_seq: int
    content_recorded_at: str | None
    context_version: str | None


@dataclass(frozen=True)
class SnapshotBasis:
    as_of_seq: int
    content: ContentBasis


@dataclass(frozen=True)
class PublicationIdentity:
    """Coordinator-generated technical identity, never supplied by a projection."""

    publication_id: str
    built_at: str
    as_of_seq: int


@dataclass(frozen=True, order=True)
class Omission:
    category: str
    reason: str
    count: int


@dataclass(frozen=True)
class ScopeLedger:
    included: tuple[ItemKey, ...]
    not_included: tuple[Omission, ...]
    # Internal checker input. The renderer receives only ShareModel.
    outside: tuple[ItemKey, ...] = ()


@dataclass(frozen=True)
class ReaderNotice:
    path: str
    code: Literal["path_pattern", "excluded_field_line", "image_metadata"]
    metadata_kind: Literal["gps", "author", "serial", "other"] = "other"


@dataclass(frozen=True)
class RenderFacts:
    """Trusted inspection facts, filled after projection validation. No authored raw strings."""

    inspection: tuple[tuple[str, int], ...] = ()
    notices: tuple[ReaderNotice, ...] = ()
    no_recorded_hash: int = 0
    points: tuple[tuple[str, int], ...] = ()
    # (Report page key, its view document's package path or "", displayed, the renderer's reasons)
    report_views: tuple[tuple[str, str, bool, tuple[str, ...]], ...] = ()


@dataclass(frozen=True)
class ShareModel:
    basis: ContentBasis
    analysis_state: Literal["in_progress", "complete"]
    title: Text
    items: tuple[ShareItem, ...]
    not_included: tuple[Omission, ...]
    diagnostics: tuple[Omission, ...] = ()
    facts: RenderFacts = RenderFacts()


@dataclass(frozen=True)
class Projection:
    model: ShareModel
    copy_plan: tuple[CopyEntry, ...]
    ledger: ScopeLedger
    basis: SnapshotBasis


@dataclass(frozen=True)
class ScanText:
    value: str
    location: str
    name_slot: bool = False


@dataclass(frozen=True)
class ResourceLink:
    value: str
    location: str
    resource: bool = True


@dataclass(frozen=True)
class Inspection:
    format: str
    strings: tuple[ScanText, ...]
    links: tuple[ResourceLink, ...] = ()
    metadata_keys: tuple[str, ...] = ()
    ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class BanToken:
    value: str
    mode: Literal["strong", "weak", "sentence", "warning"]
    source: ItemKey | None = None
    field: str | None = None


@dataclass(frozen=True)
class LeakPolicy:
    tokens: tuple[BanToken, ...] = ()
    short_markers: int = 0
    # sha256 of an included run's code snapshot -> the names of the figures its script's runs
    # recorded, which that code may name although a figure is left out (2026-10-27 owner decisions).
    code_names: tuple[tuple[str, frozenset[str]], ...] = ()
    # Token value -> the included items' IDs it is part of (run X inside run X-2); a found token whose
    # every occurrence lies inside one of them is that ID, not a leak (2026-10-27 owner decision).
    covers: tuple[tuple[str, tuple[str, ...]], ...] = ()
