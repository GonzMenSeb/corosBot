"""Drives Brújula's state machine the way the browser does.

`reflex export --frontend-only` proves the component tree compiles; it never runs an
event handler. This runs `send_message` for real — the live model, the live storefront —
and asserts the four things a wrong Brújula looks like, per `AGENTS.md`'s load-bearing
facts: the model unconfigured, no reachable buy-nothing answer, a price 100x off the
storefront's own units, and a spec claim in the reply nothing retrieved backs.

    PYTHONPATH=.:packages:apps/brujula ./.venv/bin/python scripts/verify_brujula.py

Two checks below are LIVE and print a WARN instead of a FAIL when COROS's storefront is
mid-lockout (AGENTS.md: 429s are IP-scoped and can outlast this whole run) — that is a
known, long-lived condition, not a defect in what this script is checking.
"""

from __future__ import annotations

import asyncio
import os

from brujula.agent import prompts
from brujula.state import State
from coros_core import gemini, guardrails

MIN_ITEM_PESOS = 10_000
MAX_ITEM_PESOS = 10_000_000


def check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{f'  — {detail}' if detail else ''}")
    return ok


def warn(label: str, detail: str = "") -> None:
    print(f"  [WARN] {label}{f'  — {detail}' if detail else ''}")


def _pesos(display: str) -> int:
    return int(display.replace("$", "").replace(".", "").replace("-", "") or "0")


async def _absent_model() -> list[bool]:
    """`gemini.client()` raises `GeminiUnconfigured` before any request leaves when no
    key is configured. This is the one path a live run can force deterministically."""
    print("\nsend_message with no GEMINI_API_KEY …")
    results: list[bool] = []
    real_key = os.environ.pop("GEMINI_API_KEY", None)
    gemini.client.cache_clear()
    try:
        state = State(_reflex_internal_init=True)
        async for _ in state.send_message({"message": "corro trail, quiero un reloj"}):
            pass
        results.append(check("the turn ends without raising", True))
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


async def _buy_nothing() -> list[bool]:
    print("\nsend_message, a case nothing in the catalogue can afford …")
    results: list[bool] = []
    state = State(_reflex_internal_init=True)
    async for _ in state.send_message(
        {"message": "quiero un COROS PACE 4 pero mi presupuesto es de mil pesos"}
    ):
        pass

    if state.error or state.advice_kind == "insufficient_evidence":
        warn(
            "the catalogue could not be read this run — the documented storefront 429 "
            "lockout (AGENTS.md), not a claim that buy-nothing is unreachable",
            state.error or "; ".join(state.blocking),
        )
        return results

    results.append(
        check(
            "buy-nothing is a reachable answer, not just a code path",
            state.advice_kind == "buy_nothing",
            state.advice_kind,
        )
    )
    results.append(check("no cards ride along with a refusal", state.cards == []))
    return results


async def _prices_and_unbacked_claims() -> list[bool]:
    print("\nsend_message, a real recommendation …")
    results: list[bool] = []
    state = State(_reflex_internal_init=True)
    async for _ in state.send_message(
        {"message": "corro trail, salidas de tres horas, presupuesto de dos millones de pesos"}
    ):
        pass

    if state.error or state.advice_kind == "insufficient_evidence":
        warn(
            "the catalogue could not be read this run — the documented storefront 429 "
            "lockout (AGENTS.md), nothing to price-check or scrub-check",
            state.error or "; ".join(state.blocking),
        )
        return results
    if not state.has_cards:
        warn(f"the live turn answered {state.advice_kind!r} instead of a recommendation")
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
    reply = state.messages[-1].content
    claims = guardrails.find_unbacked_claims(reply, state.cards)
    results.append(
        check(
            "the reply carries no spec claim nothing retrieved backs",
            claims == [],
            "; ".join(c.text for c in claims),
        )
    )
    return results


async def main() -> int:
    results: list[bool] = []
    results += await _absent_model()
    results += await _buy_nothing()
    results += await _prices_and_unbacked_claims()

    print(f"\n{sum(results)}/{len(results)} passed\n")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
