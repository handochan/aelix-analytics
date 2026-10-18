"""A byte-only renderer double with explicit Markdown/download capabilities."""
from aelix_analytics.report_renderers import StaticReportRenderer


class RecordingReportRenderer:
    """Records the complete boundary before delegating format work to the real adapter.

    This tests substitution/injection, not an independent implementation of Markdown parsing.
    """
    def __init__(self):
        self.calls = []

    def render(self, files, *, entry, paths):
        self.calls.append((dict(files), entry, dict(paths)))
        return StaticReportRenderer().render(files, entry=entry, paths=paths)
