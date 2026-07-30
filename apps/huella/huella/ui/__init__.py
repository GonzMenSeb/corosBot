"""Huella's view layer. Deliberately re-exports nothing.

Every component module here imports `huella.state`, and a re-export would make importing
a single token pull the whole state machine — and, in this app, the Strava session store —
in with it. `theme` in particular has to stay importable on its own: it is flat data with
no imports at all, so anything — a component, a test, a stylesheet generator — can read it.
Import the submodules directly.
"""
