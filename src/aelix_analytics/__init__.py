"""Aelix Analytics as an installed distribution.

It holds the aelix extension (`aelix_analytics.extension`, found by aelix through the
`aelix.extensions` entry point and the manifest next to this file), the human command line `analytics`
(`aelix_analytics.cli`), and the analysis kit both of them run (`kit/labkit`, package data here).

aelix imports this package when it loads the extension, so it imports nothing. Analysis scripts use
the kit's script API as `import labkit`, which a run gets on its path from the entry point that
started it; it is not importable from this package.
"""
