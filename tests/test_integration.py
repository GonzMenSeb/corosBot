"""One regression per bug that was found across a seam, not inside a module.

Every test here exists because something passed its own module's suite and still failed —
in production, in a browser, or under an adversarial probe. They are grouped by the seam
that hid the bug rather than by the file that holds the fix, because the file is not where
the next one will be either.

None of these needs the network.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

from coros_core import catalog, evidence

REPO = pathlib.Path(__file__).resolve().parent.parent


# ── the transport seam: a constant that can only be tested where it works ─────


class TestTheStorefrontFingerprintIsNotASingleConstant:
    """Brújula served zero products in production while the whole suite was green.

    COROS's Cloudflare scores the pair (ClientHello, ASN). Measured 30 Jul 2026 from inside
    the running container on the VPS and from a residential IP in the same minutes:

        client                            residential   VPS (OVH)
        urllib, plain Python ssl          -             200, 297 399 B
        curl_cffi, no impersonation       403           403, 6 924 B
        curl_cffi impersonate="chrome"    200           429, `local_rate_limited`
        curl_cffi impersonate="safari"    200           200, 297 399 B

    So the better impersonation scores WORSE from a datacentre, the IP is not blocked at
    all, and no test running anywhere but the VPS could have caught it.
    """

    def test_there_is_more_than_one_profile_to_fall_back_to(self) -> None:
        assert len(catalog.IMPERSONATE_CHAIN) >= 2, (
            "The chain is back to a single profile, which is the shape that shipped the\n"
            "outage. CI never runs from the datacentre, so the profile cannot be tested\n"
            "where it fails."
        )

    def test_the_head_of_the_chain_is_what_goes_out_first(self) -> None:
        assert catalog.IMPERSONATE == catalog.IMPERSONATE_CHAIN[0]

    def test_an_operator_can_change_it_without_a_rebuild(self) -> None:
        source = (REPO / "packages" / "coros_core" / "catalog.py").read_text()
        assert "COROS_IMPERSONATE" in source, (
            "The env override is gone. A fingerprint that rots is then a rebuild and a\n"
            "deploy rather than a variable and a container recreate."
        )

    def test_a_refused_fingerprint_does_not_latch_before_the_others_are_tried(self) -> None:
        source = (REPO / "packages" / "coros_core" / "catalog.py").read_text()
        body = source[source.index("async def _get("):]
        fallback = body.index("_try_other_fingerprints")
        pacing = body.index("engage_pacing()")
        assert fallback < pacing, (
            "engage_pacing() now runs before the other profiles are tried, so a 429 spends\n"
            "the 90 s cooldown on a request that had a working answer one profile away.\n"
            "That is exactly what production did."
        )


# ── the copy seam: a check that ran and failed is not one nobody ran ──────────


APPS = ("brujula", "huella")


class TestAFailedCheckIsNotReportedAsOneThatNeverRan:
    """`_blocked` built its sentence from `outcome != "pass"`, collapsing `fail` into
    `not_run`. So a budget check that RAN and returned "nothing fits, the cheapest APEX 4 is
    $1.899.000" was described to the person as "me faltó comprobar la cuenta contra tu
    presupuesto" — I failed to check your budget. The refusal was safe and the sentence was
    false.

    Every other layer already drew the line: `evidence.verdict_of` returns None only for
    `not_run`, and `theme.OUTCOME_COLOR` gives `fail` the flag and `not_run` the secondary
    grey with a comment saying why. This was the one place that lost it, in BOTH apps —
    which is the argument for pinning it here rather than in either app's own suite.
    """

    @pytest.mark.parametrize("app", APPS)
    def test_the_two_outcomes_are_told_apart(self, app: str) -> None:
        source = (REPO / "apps" / app / app / "agent" / "loop.py").read_text()
        body = source[source.index("def _blocked("):]
        body = body[: body.index("\ndef ")] if "\ndef " in body[10:] else body
        assert 'outcome != "pass"' not in body, (
            f"{app}'s _blocked selects on `outcome != \"pass\"`, which lumps `fail` in with\n"
            "`not_run`. A check that ran and disagreed then gets described as one that never\n"
            "happened."
        )
        assert 'outcome == "fail"' in body and 'outcome == "not_run"' in body, (
            f"{app}'s _blocked no longer separates `fail` from `not_run` explicitly."
        )

    @pytest.mark.parametrize("app", APPS)
    def test_each_outcome_gets_its_own_sentence(self, app: str) -> None:
        source = (REPO / "apps" / app / app / "agent" / "loop.py").read_text()
        body = source[source.index("def _blocked("):]
        assert "Me faltó comprobar" in body, "the not_run sentence is gone"
        assert "Revisé" in body, (
            f"{app}'s _blocked has no sentence for a check that ran and failed. Both branches\n"
            "have to exist or one of the two outcomes borrows the other's words."
        )

    def test_the_core_agrees_that_only_not_run_is_unknown(self) -> None:
        """The seam's other side, so the two cannot drift apart silently.

        `_held` is the function the bundle uses to turn an outcome into a claim: None means
        "nobody established this", which is `not_run` alone. `fail` is False — a definite
        negative — and it is that distinction the copy layer was losing.
        """
        assert evidence._held("not_run") is None
        assert evidence._held("fail") is False
        assert evidence._held("pass") is True


# ── the layout seam: a margin is outside the width ────────────────────────────


class TestNeitherAppScrollsSidewaysOnAPhone:
    """Measured on the live instance at 414: Brújula's `scrollWidth` was 415 against a
    `clientWidth` of 399. Its audit rail carried `width: 100%` together with
    `margin: 0 1rem 1rem`, and a margin sits outside the width, so the box was its container
    plus 32 px. Huella's rail was clean because at mobile it draws a border instead of
    floating on a margin.

    `tests/test_brujula_ui.py` pins the prop pair per breakpoint. This pins the narrower
    thing that actually broke, in both apps at once.
    """

    @pytest.mark.parametrize("app", APPS)
    def test_the_rail_is_not_full_width_and_side_margined_at_once(self, app: str) -> None:
        source = (REPO / "apps" / app / app / "ui" / "trace_panel.py").read_text()
        tree = ast.parse(source)
        offenders = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            kw = {k.arg: k.value for k in node.keywords if k.arg}
            if "width" not in kw or "margin" not in kw:
                continue
            widths = _literals(kw["width"])
            margins = _literals(kw["margin"])
            for i, w in enumerate(widths):
                m = margins[min(i, len(margins) - 1)] if margins else None
                if w == "100%" and _has_side_margin(m):
                    offenders.append(f"{app} breakpoint {i}: width={w!r} margin={m!r}")
        assert not offenders, (
            "\n  ".join(offenders) + "\n"
            "A margin is outside the width, so the element is its container plus the margins\n"
            "and the page scrolls sideways by exactly that much. Use width='auto' — a\n"
            "stretched flex item fills the line minus its own margins."
        )


def _literals(node: ast.AST) -> list[str | None]:
    """The authored value(s) of a Reflex responsive prop, as written in the source."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, (ast.List, ast.Tuple)):
        out: list[str | None] = []
        for element in node.elts:
            out.append(element.value if isinstance(element, ast.Constant) else None)
        return out
    return []


def _has_side_margin(margin: str | None) -> bool:
    if not margin:
        return False
    parts = margin.split()
    if len(parts) == 1:
        return parts[0] not in ("0", "0px", "0rem", "auto")
    return parts[1] not in ("0", "0px", "0rem")


# ── the serving seam: the OAuth route must outrank the compiled mount ─────────


class TestTheOAuthCallbackStillOutranksTheFrontendMount:
    """Verified in production (303 / 405 / 404) and by `make spike-oauth` under granian, but
    neither runs in CI. What CI can hold is the ordering the shape depends on: the route has
    to be registered on the api_transformer BEFORE App.__call__ mounts the compiled frontend
    at '/', or Starlette matches the catch-all first and Strava gets a 404.
    """

    def test_the_route_is_registered_on_the_api_transformer(self) -> None:
        source = (REPO / "apps" / "huella" / "huella" / "app.py").read_text()
        assert "api_transformer=" in source, (
            "The OAuth route is no longer handed to rx.App as an api_transformer. Registered\n"
            "any other way it sits behind the compiled frontend's catch-all mount and Strava\n"
            "receives a 404 for a redirect that was correct."
        )

    def test_the_callback_path_is_spelled_once(self) -> None:
        oauth = (REPO / "apps" / "huella" / "huella" / "oauth.py").read_text()
        assert re.search(r'^ROUTE\s*=\s*"/oauth/strava/callback"', oauth, re.M), (
            "The callback path moved or gained a second spelling. It has to match the\n"
            "STRAVA_REDIRECT_URI the Ansible role templates, and Strava validates the\n"
            "registered domain against it."
        )
