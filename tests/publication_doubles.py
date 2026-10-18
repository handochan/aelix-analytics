"""A deliberately smaller policy for the publication injection contract; no real policy fallback."""
from collections import Counter

from aelix_analytics.publishing.model import (
    Field,
    Omission,
    Projection,
    ScopeLedger,
    ShareItem,
    ShareModel,
    Text,
)
from aelix_analytics.publishing.scope import PROFILES, scope_closure


class RecordingProjection:
    id, rules_version = "recording", 1
    capabilities = frozenset({"project", "model", "no_io"})

    def __init__(self):
        self.requests = []

    def project(self, snapshot, request):
        self.requests.append(request)
        closure = scope_closure(snapshot, request, PROFILES["read"])
        item = next(i for i in snapshot.items if i.key.category == "project" and i.key in closure)
        title = Text(item.values.get("title"), "user", (item.key, "title"))
        shared = ShareItem(item.key, item.kind, (Field("title", title),))
        counts = Counter(i.key.category for i in snapshot.items if i.key != item.key)
        omitted = tuple(Omission(c, "out_of_scope", n) for c, n in sorted(counts.items()))
        model = ShareModel(snapshot.basis.content, request.analysis_state, title, (shared,), omitted,
                           snapshot.diagnostics)
        return Projection(model, (), ScopeLedger((item.key,), omitted), snapshot.basis)
