"""Immutable publication values. No record objects, project objects or I/O adapters."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


class PublicationError(ValueError):
    def __init__(self, code: str, message: str):
        self.code, self.message = code, message
        super().__init__(message)


@dataclass(frozen=True, order=True)
class ItemKey:
    category: str
    identity: str


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


@dataclass(frozen=True)
class ContentBasis:
    content_seq: int
    content_recorded_at: str | None
    context_version: str | None


@dataclass(frozen=True)
class SnapshotBasis:
    as_of_seq: int
    content: ContentBasis


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
class ShareModel:
    basis: ContentBasis
    analysis_state: Literal["in_progress", "complete"]
    title: Text
    items: tuple[ShareItem, ...]
    not_included: tuple[Omission, ...]
    diagnostics: tuple[Omission, ...] = ()


@dataclass(frozen=True)
class Projection:
    model: ShareModel
    copy_plan: tuple[CopyEntry, ...]
    ledger: ScopeLedger
    basis: SnapshotBasis
