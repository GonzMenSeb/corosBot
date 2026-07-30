"""Drives Huella's state machine the way the browser does.

`reflex export --frontend-only` proves the component tree compiles; it never runs an event
handler. This runs `send_message` for real — the live model, the live storefront — and
asserts the four things a wrong Huella looks like: the model unconfigured, an athlete with
no demonstrated training deduced at instead of asked, a price 100x off the storefront's own
units, and a claim in the reply nothing retrieved backs — including a *training* figure,
which is Huella's own shape of that failure and the one Brújula has no equivalent of.

    PYTHONPATH=.:packages:apps/huella ./.venv/bin/python scripts/verify_huella.py

Three rules here each encode a bug that already happened in this repo.

**A WARN is granted only on evidence, and only on one event.** Two checks below are LIVE
and downgrade to a WARN when COROS's storefront is mid-lockout (`AGENTS.md`: 429s are
IP-scoped and can outlast this whole run) — a known, long-lived condition rather than a
defect in what this script checks. `throttle_evidence()` requires a `catalog.unavailable`
event carrying `rate_limited` in the turn's own trace, and nothing else in this file may
grant one. `scripts/verify_brujula.py` ran GREEN through five merged PRs while excusing
every failure as a lockout it had never observed, because it excused on `advice_kind`
alone — which the bundle sets to `insufficient_evidence` whenever *any* check could not
run, with `blocking` left empty because it ACCEPTED. So: never on `advice_kind`, never on
the shape or the wording of the answer. A refusal that is a real answer is CHECKED as one.

**An athlete with no Strava is the expected state, not a WARN and not a failure.** This
script cannot complete an OAuth round trip — `AGENTS.md`: Standard Tier now needs a paid
subscription, and that is a blocker on a person rather than on code — so every turn below
runs unconnected. That is precisely Huella's fallback path, and the assertion is that the
INTERVIEW FIRES: `questions.asked` in the trace, `interview.skipped` absent from it, and
the answer never labelled as demonstrated training. Treating it as an excuse would hide
the one behaviour a live run of an unconnected Huella can actually prove.

**The gate is checked before anything is driven.** With `HUELLA_PASSWORD` set, `GATE_ON`
is true, `send_message` returns before it appends a thing, and every var this script reads
is still its compiled-in default — a wholly silent, wholly green run over a Huella that
answered nothing. `admitted()` opens the gate through the real `unlock` handler and
`_ran()` re-checks per turn, so a gate that did not open fails the run instead of
flattering it.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from coros_core import gemini, guardrails
from huella import privacy
from huella import state as st
from huella.agent import prompts, tools
from huella.state import State

MIN_ITEM_PESOS = 10_000
MAX_ITEM_PESOS = 10_000_000

OPENING = "corro trail, salidas largas de tres horas, presupuesto de dos millones de pesos"
ANSWERING = (
    "entreno entre seis y nueve horas por semana, cinco salidas, y no tengo reloj todavía"
)


def check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{f'  — {detail}' if detail else ''}")
    return ok


def warn(label: str, detail: str = "") -> None:
    print(f"  [WARN] {label}{f'  — {detail}' if detail else ''}")


def _pesos(display: str) -> int:
    return int(display.replace("$", "").replace(".", "").replace("-", "") or "0")


def _events(state: State) -> list[str]:
    return [event.get("event", "?") for event in state._raw_trace]


def throttle_evidence(state: State) -> str:
    """The refusal this run can be excused by, read out of the trace — or "" if there is none.

    `insufficient_evidence` is not evidence of a lockout. It is what the bundle accepts
    whenever a check could not run, so a model failure, a misfired gate, an unreadable
    Strava window and a retrieval that legitimately found nothing all arrive here wearing
    the same face, and `state.blocking` is empty in every one of them because the bundle
    ACCEPTED. Excusing the run on that alone is a verifier that passes whatever it is
    handed. `catalog.unavailable` with `rate_limited` is the only thing that says COROS
    refused us — and note that a 403 does NOT set it: that one is the TLS-fingerprint
    failure, which is a defect in our client and not a throttle.
    """
    for event in state._raw_trace:
        if event.get("event") == "catalog.unavailable" and event.get("payload", {}).get(
            "rate_limited"
        ):
            payload = event["payload"]
            return f"HTTP {payload.get('status')} — {payload.get('detail')}"
    return ""


def unexplained(state: State) -> str:
    """What to print when the turn did not answer and nothing in the trace excuses it."""
    return (
        f"advice_kind={state.advice_kind!r} error={state.error!r} "
        f"blocking={list(state.blocking)} trace_tail={_events(state)[-6:]}"
    )


# ── admission, before anything is driven ──────────────────────────────────────


async def admitted() -> State:
    """A state that can actually spend a turn, or a run that stops.

    `unlocked` defaults to a hard `False` — a state var's default is compiled into the
    frontend bundle and the image is built with no password, so the derived form would bake
    in as True. In a browser `on_page_load` opens it; here the real `unlock` handler does,
    with the same password `state.py` read at import. If it does not open, this raises:
    a locked Huella reports pristine defaults for everything below, which is green and
    means nothing.
    """
    state = State(_reflex_internal_init=True)
    if not st.GATE_ON:
        # `send_message` short-circuits on `GATE_ON and …`, so nothing is gating this run.
        state.unlocked = True
        return state

    unlock = getattr(State.unlock, "fn", State.unlock)
    async for _ in unlock(state, {"password": os.environ.get("HUELLA_PASSWORD", "")}):
        pass
    if not state.unlocked:
        raise SystemExit(
            "HUELLA_PASSWORD is set and the gate did not open with it, so every turn below "
            "would return before appending a message and this script would report pristine "
            "defaults as a pass. Unset it, or export the password this process was started "
            "with."
        )
    return state


def _ran(state: State, label: str) -> bool:
    """Did the handler get past its own guard? `send_message` returns before it appends
    anything when the gate is shut, when a turn is already running, or when the message is
    empty — and every var this script reads is a default in all three cases."""
    return check(
        f"{label}: the turn actually ran",
        len(state.messages) >= 2,
        f"messages={len(state.messages)} unlocked={state.unlocked} gate_on={st.GATE_ON}",
    )


async def _the_gate_is_where_it_should_be() -> list[bool]:
    print("\nadmission …")
    results: list[bool] = []
    state = await admitted()
    results.append(
        check(
            "this run is admitted — nothing below can be vacuously green",
            state.unlocked or not st.GATE_ON,
            f"gate_on={st.GATE_ON}",
        )
    )
    if st.GATE_ON:
        wrong = State(_reflex_internal_init=True)
        unlock = getattr(State.unlock, "fn", State.unlock)
        async for _ in unlock(wrong, {"password": "no es esta"}):
            pass
        results.append(
            check("a wrong password still does not open it", wrong.unlocked is False)
        )
    results.append(
        check(
            "no Strava session is carried into this run",
            privacy.session_count() == 0,
            f"{privacy.session_count()} sessions",
        )
    )
    return results


# ── the checks ────────────────────────────────────────────────────────────────


async def _absent_model() -> list[bool]:
    """`gemini.client()` raises `GeminiUnconfigured` before any request leaves when no key
    is configured. This is the one path a live run can force deterministically."""
    print("\nsend_message with no GEMINI_API_KEY …")
    results: list[bool] = []
    real_key = os.environ.pop("GEMINI_API_KEY", None)
    gemini.client.cache_clear()
    try:
        state = await admitted()
        async for _ in state.send_message({"message": "corro trail, ¿qué me sirve?"}):
            pass
        results.append(_ran(state, "no key"))
        results.append(
            check(
                "state.error stays empty — the loop turned this into a reply, not a crash",
                state.error == "",
            )
        )
        results.append(
            check(
                "the reply is the broke template, never the raw exception",
                bool(state.messages) and state.messages[-1].content == prompts.BROKE_TEMPLATE,
            )
        )
        results.append(
            check(
                "the credential name never reaches the screen",
                "GEMINI_API_KEY" not in state.messages[-1].content if state.messages else False,
            )
        )
        results.append(check("is_thinking is cleared", state.is_thinking is False))
    finally:
        if real_key is not None:
            os.environ["GEMINI_API_KEY"] = real_key
        gemini.client.cache_clear()
    return results


async def _the_interview_fires() -> list[bool]:
    """The expected state, driven and asserted rather than excused.

    Nothing here is connected to Strava, which is the same amount of demonstrated training
    as a connected account under `tools.MIN_SAMPLE` activities: none. Huella's whole premise
    is that it then ASKS, and says which of the two readings the answer came from.
    """
    print("\nsend_message with no Strava connected — the interview has to fire …")
    results: list[bool] = []
    state = await admitted()
    async for _ in state.send_message({"message": OPENING}):
        pass

    if not _ran(state, "unconnected"):
        return [False]

    if state.error:
        throttled = throttle_evidence(state)
        if throttled:
            warn("COROS refused the catalogue read this run — the turn stopped before the "
                 "interview could run", throttled)
            return results
        results.append(
            check("the turn reached an answer at all", False,
                  f"no catalog.unavailable in the trace, so nothing excuses this. "
                  f"{unexplained(state)}")
        )
        return results

    results.append(
        check(
            "the verdict says nothing was demonstrated",
            state.grounded is False and state.confidence == "none",
            f"grounded={state.grounded} confidence={state.confidence}",
        )
    )
    results.append(
        check(
            "and it says WHY, with the flag for an unconnected account",
            st._FLAG_ES["not_connected"] in state.uncertainty_flags,
            str(state.uncertainty_flags),
        )
    )
    results.append(check("the cold-start fallback is on", state.cold_start is True))

    events = _events(state)
    if "questions.asked" not in events:
        throttled = throttle_evidence(state)
        if throttled:
            warn("COROS refused the catalogue read this run — the turn stopped before the "
                 "interview stage", throttled)
            return results
    results.append(
        check(
            "the interview ran — questions.asked is in this turn's own trace",
            "questions.asked" in events,
            f"trace_tail={events[-6:]}",
        )
    )
    skipped = "interview.skipped" not in events
    results.append(
        check(
            "nothing claimed the history answered",
            skipped,
            "" if skipped else "interview.skipped fired for an account with no Strava",
        )
    )
    results.append(
        check(
            "the answer is never labelled as demonstrated training",
            state.advice_mode != st.MODE_TRAINING,
            state.advice_mode,
        )
    )
    return results


async def _prices_and_unbacked_claims() -> list[bool]:
    """One full case: the interview, then the answers, then whatever Huella does with them.

    A refusal here is a real answer, not a missing one — buy-nothing and not-sold-locally
    both land with no cards on purpose — so the no-cards branch CHECKS the refusal instead
    of warning about it. Warning on the kind is what let Brújula's verifier pass a broken
    app for five PRs.
    """
    print("\nsend_message, a full case answered through the interview …")
    results: list[bool] = []
    state = await admitted()
    async for _ in state.send_message({"message": OPENING}):
        pass
    if not _ran(state, "full case"):
        return [False]
    if state.has_questions:
        async for _ in state.send_message({"message": ANSWERING}):
            pass

    if state.error or not state.advice_kind:
        throttled = throttle_evidence(state)
        if throttled:
            warn("COROS refused the catalogue read this run — nothing to price-check",
                 throttled)
            return results
        results.append(
            check("the turn reached an answer at all", False,
                  f"no catalog.unavailable in the trace, so nothing excuses this. "
                  f"{unexplained(state)}")
        )
        return results

    reply = state.messages[-1].content
    conversation = st._CONVERSATIONS.get(state.router.session.client_token)
    requirements = conversation.requirements() if conversation is not None else ()

    print("  the answer …")
    results.append(
        check(
            "cards and kind agree — a card beside a refusal is what the bundle blocks on",
            (state.advice_kind == "recommend") == bool(state.cards),
            f"{state.advice_kind} with {len(state.cards)} cards",
        )
    )
    results.append(check("the reply is not empty", bool(reply.strip())))

    print("  training figures …")
    # `scrub_training_prose` reads `.key` and `.value` and nothing else, and the loop ran it
    # over these same requirements before the reply was stored. Running it again has to be a
    # no-op: anything it still finds is a figure that reached the screen.
    rescrubbed = tools.scrub_training_prose(reply, requirements)
    results.append(
        check(
            "no training figure survives that is not a band this turn handed over",
            rescrubbed == reply,
            "" if rescrubbed == reply else f"a second pass would have cut: {rescrubbed[:180]!r}",
        )
    )
    results.append(
        check(
            "every requirement on the rail says which half it came from",
            all(row.source in ("strava", "user", "assumed") for row in state.requirements),
            str([(r.key, r.source) for r in state.requirements]),
        )
    )
    results.append(
        check(
            "nothing on the rail claims Strava while nothing is connected",
            not [r for r in state.requirements if r.derived],
            str([r.key for r in state.requirements if r.derived]),
        )
    )

    if not state.cards:
        results.append(
            check(
                "a refusal carries no total either",
                state.total_minor == 0,
                state.total_display,
            )
        )
        results.append(
            check(
                "the refusal is one of the typed kinds, not an improvised one",
                state.advice_kind
                in {"buy_nothing", "not_sold_locally", "insufficient_evidence", "needs_human"},
                state.advice_kind,
            )
        )
        return results

    print("  prices …")
    for card in state.cards:
        pesos = _pesos(card.price_display)
        results.append(
            check(
                f"{card.title[:40]} is priced within a real COROS range",
                MIN_ITEM_PESOS <= pesos <= MAX_ITEM_PESOS,
                card.price_display,
            )
        )
    total_pesos = _pesos(state.total_display)
    results.append(
        check(
            "the total is the sum of the cards, not a units mistake",
            total_pesos == sum(_pesos(c.price_display) for c in state.cards),
            state.total_display,
        )
    )

    print("  unbacked claims …")
    claims = guardrails.find_unbacked_claims(
        reply, [*state.cards, *tools.band_phrases(requirements)]
    )
    results.append(
        check(
            "the reply carries no spec claim nothing retrieved backs",
            claims == [],
            "; ".join(c.text for c in claims),
        )
    )
    results.append(
        check(
            "the recommendation was verified before it was shown",
            state.evidence_accepted is True,
            f"blocking={list(state.blocking)}",
        )
    )
    return results


async def main() -> int:
    results: list[bool] = []
    results += await _the_gate_is_where_it_should_be()
    results += await _absent_model()
    results += await _the_interview_fires()
    results += await _prices_and_unbacked_claims()

    print(f"\n{sum(results)}/{len(results)} passed\n")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
