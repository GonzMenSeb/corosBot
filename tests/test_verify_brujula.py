"""`scripts/verify_brujula.py` is the only thing that runs a real turn, so a bug in its
judgement is a bug nothing else catches.

It ran green for five merged PRs while excusing every unanswered turn as a COROS lockout,
including on runs where COROS was never asked. These pin the judgement, not the plumbing:
the live checks reach the network and cannot be unit-tested, but *what counts as an excuse*
is pure and must stay that way.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "verify_brujula.py"


def _load() -> Any:
    """By path: `scripts/` is not a package and must not become one — `pytest.ini`'s
    `pythonpath` roots are `.`, `packages` and the two app dirs, and adding a fourth to
    reach one helper would put every script name in the suite's import namespace."""
    spec = importlib.util.spec_from_file_location("verify_brujula", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def verify() -> Any:
    return _load()


class _Turn:
    """The three fields the judgement reads. Not an `rx.State`: instantiating one pulls in
    Reflex's event plumbing, and none of it is what is under test here."""

    def __init__(self, trace: list[dict[str, Any]], **kwargs: Any) -> None:
        self._raw_trace = trace
        self.advice_kind = kwargs.get("advice_kind", "insufficient_evidence")
        self.error = kwargs.get("error", "")
        self.blocking = kwargs.get("blocking", [])


def _refused(status: int = 429, rate_limited: bool = True) -> dict[str, Any]:
    return {
        "event": "catalog.unavailable",
        "level": "error",
        "payload": {
            "status": status,
            "detail": "products: refused, not polling for another 82 s",
            "rate_limited": rate_limited,
            "url": "https://coros.com.co/products.json?limit=250",
        },
    }


class TestOnlyTheStorefrontsOwnRefusalExcusesAnUnansweredTurn:
    def test_a_rate_limited_catalog_read_is_an_excuse(self, verify) -> None:
        excuse = verify.throttle_evidence(_Turn([{"event": "turn.start"}, _refused()]))
        assert excuse, "a real 429 in the trace no longer excuses the run, so a lockout now fails"
        assert "429" in excuse and "not polling" in excuse, (
            f"the excuse is {excuse!r}. It is the only thing printed beside the WARN, so it "
            "has to carry the status and the detail or the WARN is unactionable again."
        )

    def test_insufficient_evidence_alone_is_not_an_excuse(self, verify) -> None:
        """The regression itself. The bundle ACCEPTS `insufficient_evidence` whenever a check
        could not run, and leaves `blocking` empty when it does — so this state is reached by
        a broken model and a misfired gate too, and it says nothing about COROS."""
        turn = _Turn([{"event": "turn.start"}, {"event": "turn.done"}])
        assert verify.throttle_evidence(turn) == "", (
            "an unanswered turn with no catalog.unavailable in its trace is being excused "
            "again. That is a verifier that passes whatever it is handed: a wholly broken "
            "Brújula reaches this state and would be reported green."
        )

    def test_a_rate_limit_from_somewhere_other_than_the_catalog_is_not_an_excuse(
        self, verify
    ) -> None:
        """Deliberately carries `rate_limited` so that only the event NAME can reject it.
        Written the obvious way — a `model.error` with a bare status — the test passed even
        with the name check deleted, which a mutation probe caught on 30 Jul 2026. Gemini and
        the UCP endpoint both throttle us too, and neither says anything about the storefront."""
        turn = _Turn(
            [
                {"event": "turn.start"},
                {
                    "event": "model.error",
                    "payload": {"status": 429, "rate_limited": True, "detail": "gemini refused"},
                },
            ]
        )
        assert verify.throttle_evidence(turn) == "", (
            "a 429 from somewhere other than the storefront is being read as a storefront "
            "lockout. Only catalog.unavailable says COROS refused us — every other throttled "
            "subsystem is a defect this script exists to report."
        )

    def test_a_catalog_failure_that_is_not_a_rate_limit_is_not_an_excuse(self, verify) -> None:
        """A 503, a DNS failure or a dropped connection is a real defect somewhere. The
        lockout is long-lived and expected; nothing else here is."""
        turn = _Turn([_refused(status=503, rate_limited=False)])
        assert verify.throttle_evidence(turn) == "", (
            "a non-429 storefront failure is being excused as the documented lockout. Only "
            "rate_limited says the limiter refused us."
        )

    def test_an_empty_trace_is_not_an_excuse(self, verify) -> None:
        assert verify.throttle_evidence(_Turn([])) == "", (
            "a turn that emitted nothing at all is being excused. A turn with no trace is "
            "the most broken outcome available, not the most forgivable."
        )


class TestAnUnexplainedRefusalPrintsWhatItActuallyKnows:
    def test_the_report_names_the_kind_the_error_and_the_trace_tail(self, verify) -> None:
        turn = _Turn(
            [{"event": f"e{n}"} for n in range(9)],
            advice_kind="insufficient_evidence",
            error="",
            blocking=[],
        )
        report = verify.unexplained(turn)
        assert "insufficient_evidence" in report and "blocking=[]" in report, (
            f"the failure report is {report!r}. The empty blocking list IS the finding — it "
            "is what proves the bundle accepted rather than blocked."
        )
        assert "e8" in report and "e0" not in report, (
            f"the failure report is {report!r}; it should carry the tail of the trace, which "
            "is where the last thing that happened is, and not the whole ring."
        )
