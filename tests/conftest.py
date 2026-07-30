"""Shared fixtures.

The trace ring is process-wide, so a test that reads it has to start from a known state
or it reads the previous test's verdicts.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from coros_core import trace


@pytest.fixture(autouse=True)
def _clean_trace() -> Iterator[None]:
    trace.reset()
    yield
    trace.reset()
