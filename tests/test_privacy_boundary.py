"""Huella's D1 boundary, attacked rather than described.

Three of these are release blockers by name — `test_no_strava_field_can_reach_the_catalog_call`,
`test_tokens_are_never_written_to_disk`, `test_a_refreshed_token_pair_is_stored_atomically`.
The rest exist because the module's promises are only worth what an adversary cannot get past,
and because every one of them is the kind of thing a later refactor loosens by accident.

Nothing here touches the network: `client.exchange` / `refresh` / `revoke` are stubbed, and a
stub that is never called is itself an assertion in some of these.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import pickle
from pathlib import Path
from typing import Any

import pytest
import reflex as rx
from coros_core.models import Requirement
from pydantic import ValidationError

from huella import privacy
from huella.strava import client
from huella.strava.models import Activity, ActivityWindow, TokenPair

FACTS = 'AGENTS.md "load-bearing facts"'
HUELLA = Path(__file__).resolve().parent.parent / "apps" / "huella" / "huella"

# A name a person really could give an activity, and the two things that must never leave:
# a location that identifies where somebody trains, and the id that identifies who they are.
SECRET = "Morning run — Carrera 7 #45-12, Bogotá, with Ana"
ATHLETE = 40217788
ACCESS = "access-token-do-not-leak"
REFRESH = "refresh-token-do-not-leak"


def _pair(*, expires_at: int = 4_000_000_000, scope: str = "read,activity:read_all") -> TokenPair:
    return TokenPair(
        access_token=ACCESS,
        refresh_token=REFRESH,
        expires_at=expires_at,
        scope=scope,
        athlete_id=ATHLETE,
    )


def _window() -> ActivityWindow:
    return ActivityWindow(
        activities=(
            Activity(
                id=1,
                name=SECRET,
                sport_type="TrailRun",
                distance=21_000.0,
                moving_time=7_200,
                elapsed_time=7_400,
                total_elevation_gain=800.0,
            ),
            Activity(
                id=2,
                name=f"athlete {ATHLETE} tempo",
                sport_type="Run",
                distance=10_000.0,
                moving_time=2_700,
                elapsed_time=2_700,
            ),
        ),
        pages=1,
        truncated=False,
    )


def _carries_a_secret(blob: str) -> bool:
    return any(
        needle in blob for needle in (SECRET, str(ATHLETE), ACCESS, REFRESH, "Bogotá", "Carrera 7")
    )


@pytest.fixture(autouse=True)
def _no_sessions_left_behind():
    privacy.forget_all()
    yield
    privacy.forget_all()


class TestNoStravaFieldCanReachTheCatalogCall:
    """`seal()` is the only exit, and `Requirement` is the only shape allowed through it. Six
    ways to smuggle a payload, all of them things a model or an injected activity name could
    plausibly produce."""

    # Each attack is valid as far as `Requirement` is concerned AND violates exactly ONE of the
    # gate's rules — every other field, the rationale included, is regenerated to be correct.
    # That isolation is the whole point: `_violation` is a chain of early returns, so an attack
    # that breaks three rules at once still gets refused after two of the three checks are
    # deleted. A mutation probe caught this suite doing precisely that on 30 Jul 2026, which is
    # why the expected REASON is asserted and not merely `PrivacyLeak`.
    ATTACKS = [
        ("prose in the rationale", {"rationale": SECRET}, "rationale is not the phrasing"),
        ("prose as the value", {"value": SECRET}, "not a label this key may carry"),
        ("an off-allowlist value", {"value": "not-a-discipline"}, "not a label this key may carry"),
        # "value is int, not a label" and not the shorter "not a label": the allowlist's own
        # message contains that phrase too, so the loose version passed with the type check
        # deleted. `Requirement.value` is `str | int | bool`, so an int really does get this far.
        ("a value smuggled as an int", {"value": ATHLETE}, "value is int, not a label"),
        ("the athlete id as a sample size", {"sample_size": ATHLETE}, "sample size is not a count"),
        ("the athlete id as a window", {"window_days": ATHLETE}, "window is not a number of days"),
        ("a key the gate does not derive", {"key": "budget_minor"}, "not one the gate derives"),
        ("provenance laundered to the user's own words", {"source": "user"}, "not declared as derived"),
    ]

    @staticmethod
    def _otherwise_valid(**overrides: Any) -> Requirement:
        """A requirement the gate would accept, with `overrides` applied. The rationale is
        regenerated for whatever `key`/`sample_size`/`window_days` end up being, so a mutation
        of one of those numbers does not also trip the rationale check by accident."""
        key = overrides.pop("key", "discipline")
        sample_size = overrides.pop("sample_size", 12)
        window_days = overrides.pop("window_days", 90)
        rationale = overrides.pop(
            "rationale",
            privacy._generated(key, sample_size, window_days) if key in privacy._RATIONALES else "",
        )
        return Requirement(
            key=key,
            value=overrides.pop("value", "trail_run"),
            source=overrides.pop("source", "strava"),
            derived=True,
            sample_size=sample_size,
            window_days=window_days,
            rationale=rationale,
            **overrides,
        )

    @pytest.mark.parametrize(
        ("attack", "overrides", "reason"), ATTACKS, ids=[a for a, _, _ in ATTACKS]
    )
    def test_no_strava_field_can_reach_the_catalog_call(
        self, attack: str, overrides: dict[str, Any], reason: str
    ) -> None:
        with pytest.raises(privacy.PrivacyLeak) as caught:
            privacy.seal([self._otherwise_valid(**overrides)])
        assert reason in str(caught.value), (
            f"{attack!r} was refused, but for the wrong reason: {caught.value}\n"
            f"  expected the gate to object that the {reason!r}.\n"
            "  Being refused by a different rule means THIS rule is no longer pinned — the "
            "  attack is over-determined and deleting its check would go unnoticed."
        )

    def test_the_gate_accepts_the_baseline_these_attacks_are_built_from(self) -> None:
        """Without this, every test above could pass because the baseline itself is rejected."""
        baseline = self._otherwise_valid()
        assert privacy.seal([baseline]) == (baseline,), (
            "the requirement the attacks are derived from is itself refused, so each attack "
            "above proves nothing about the field it mutates."
        )

    def test_a_derived_requirement_with_no_sample_never_gets_as_far_as_the_gate(self) -> None:
        """The layer under `seal()`, and worth knowing about: `Requirement` itself refuses to be
        constructed `derived=True` with `sample_size=0`. So the crudest smuggling attempts fail
        in `models.py` and never reach this module at all. `seal()` is not the only defence, it
        is the last one."""
        with pytest.raises(ValidationError):
            Requirement(key="discipline", value="trail_run", source="strava", derived=True)

    def test_the_rationale_is_regenerated_and_compared_not_merely_checked(self) -> None:
        """The one field that could carry free text. `seal()` rebuilds it from `sample_size`
        and `window_days` and compares, so there is no phrasing a caller can supply — not even
        the right phrasing with one extra character."""
        good = privacy.derive_requirements(_window())[0]
        assert privacy.seal([good]) == (good,)

        tampered = good.model_copy(update={"rationale": good.rationale + " Ana."})
        with pytest.raises(privacy.PrivacyLeak):
            privacy.seal([tampered])

        renumbered = good.model_copy(update={"sample_size": good.sample_size + 1})
        with pytest.raises(privacy.PrivacyLeak):
            privacy.seal([renumbered])
        assert good.rationale != privacy._generated(
            good.key, good.sample_size + 1, good.window_days
        ), (
            "a rationale generated for one sample size is identical for another, so comparing "
            "it proves nothing. The rationale is only safe because it is a pure function of two "
            f"integers already on the requirement — see {FACTS}."
        )

    def test_derive_requirements_launders_nothing_out_of_a_real_window(self) -> None:
        derived = privacy.derive_requirements(_window())
        assert derived, "the gate produced nothing at all from two activities"
        blob = repr(derived)
        assert not _carries_a_secret(blob), (
            f"a Strava field reached the far side of the gate: {blob[:400]}\n"
            "Activity names are the injection surface — this is the release blocker."
        )
        assert all(r.source == "strava" and r.derived for r in derived), (
            "a derived requirement is not labelled as derived from Strava. Provenance is what "
            "lets the uncertainty layer say a number was inferred rather than measured."
        )
        assert all(r.value in privacy.ALLOWED_VALUES for r in derived), (
            "the gate emitted a value outside its own allowlist, which seal() would reject — "
            "so derive_requirements and seal disagree about what is allowed out."
        )

    def test_everything_the_gate_emits_survives_its_own_seal(self) -> None:
        """Belt and braces: the producer and the checker are separate code, and a change to
        either that the other does not follow would strand every turn behind a PrivacyLeak."""
        derived = privacy.derive_requirements(_window())
        assert privacy.seal(derived) == derived


class TestTokensAreNeverWrittenToDisk:
    """`StateManagerDisk` pickles `rx.State` — including `_`-prefixed backend-only vars — into
    `.states/`. So the guarantee is not "we are careful with tokens"; it is that no `rx.State`
    can reach one."""

    def test_tokens_are_never_written_to_disk(self) -> None:
        """Every `rx.State` subclass Huella defines, checked for a field that could hold a
        credential or a raw payload. Vacuous until `huella/state.py` lands, and it becomes real
        the moment it does — which is the point of scanning rather than listing."""
        banned = {"TokenPair", "Activity", "ActivityWindow", "Athlete", "TokenResponse", "_Session"}
        offenders: list[str] = []
        for path in sorted(HUELLA.rglob("*.py")):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                bases = {ast.unparse(b) for b in node.bases}
                if not bases & {"rx.State", "reflex.State", "State"}:
                    continue
                for stmt in node.body:
                    if isinstance(stmt, ast.AnnAssign) and any(
                        name in ast.unparse(stmt.annotation) for name in banned
                    ):
                        offenders.append(f"{path.name}:{node.name}.{ast.unparse(stmt.target)}")
        assert not offenders, (
            f"a Reflex state var is typed to hold a Strava payload or a token: {offenders}.\n"
            "StateManagerDisk pickles state to .states/, and backend-only vars are pickled too.\n"
            f"Tokens live in privacy._SESSIONS and nowhere else — see {FACTS}."
        )

    def test_the_session_store_is_module_level_and_not_reachable_from_a_state(self) -> None:
        assert isinstance(privacy._SESSIONS, dict), "the session store is no longer a plain dict"
        assert not any(
            isinstance(getattr(rx.State, name, None), dict) and name.endswith("SESSIONS")
            for name in dir(rx.State)
        ), "something attached a session store to rx.State, where it would be pickled"

    def test_a_token_is_redacted_everywhere_a_traceback_would_show_it(self) -> None:
        pair = _pair()
        nested = repr({"session": pair, "note": "what a traceback frame looks like"})
        for label, rendered in (
            ("repr", repr(pair)),
            ("str", str(pair)),
            ("f-string", f"{pair}"),
            ("format", format(pair)),
            ("inside a container", nested),
        ):
            assert ACCESS not in rendered and REFRESH not in rendered, (
                f"{label} of a TokenPair shows the credential: {rendered[:200]}\n"
                "A traceback, a log line or an f-string is how a token escapes without anybody "
                "deciding to write it down."
            )

    def test_redaction_did_not_break_persistence(self) -> None:
        """The redaction has to leave `model_dump()` alone. A pair that cannot be serialised
        cannot be stored, and a rotation that cannot be stored locks the athlete out — the
        expensive failure this whole module exists to avoid."""
        dumped = _pair().model_dump()
        assert dumped["access_token"] == ACCESS and dumped["refresh_token"] == REFRESH, (
            "model_dump() no longer yields the tokens. Redaction belongs on the accidental "
            "paths — repr, str, f-strings — never on the one that persists a rotation."
        )

    def test_nothing_the_merchant_side_can_call_returns_a_credential(self) -> None:
        privacy._ensure("sess")
        privacy._SESSIONS["sess"].pair = _pair()
        privacy._SESSIONS["sess"].window = _window()

        for name in ("requirements", "last_sync", "is_connected", "connection", "issue_state"):
            rendered = repr(getattr(privacy, name)("sess"))
            assert ACCESS not in rendered and REFRESH not in rendered and SECRET not in rendered, (
                f"privacy.{name}() hands back something carrying a credential or a raw payload: "
                f"{rendered[:200]}"
            )
        assert ACCESS not in repr(privacy.diagnostics())

    def test_the_fingerprint_identifies_a_pair_without_carrying_it(self) -> None:
        one, two = _pair(), _pair(expires_at=4_000_000_001)
        assert privacy.fingerprint(one) == privacy.fingerprint(one), "the fingerprint is unstable"
        assert ACCESS not in privacy.fingerprint(one), "the fingerprint carries the token"
        assert privacy.fingerprint(one) == privacy.fingerprint(two), (
            "the fingerprint changed with expires_at. It names the credential so a log can "
            "correlate two events; if a rotation changes it, it cannot do that job."
        )

    def test_a_pickled_session_view_carries_no_token(self) -> None:
        """What the app hands outward has to survive the one thing that would write it down."""
        privacy._ensure("sess")
        privacy._SESSIONS["sess"].pair = _pair()
        blob = pickle.dumps(privacy.connection("sess"))
        assert ACCESS.encode() not in blob and REFRESH.encode() not in blob, (
            "the Connection view pickles a token. It is the object the UI holds, so it is the "
            "one most likely to end up inside a state var."
        )


class TestARefreshedTokenPairIsStoredAtomically:
    """A refresh invalidates the previous refresh token the instant it returns, so a rotation
    that is stored twice, stored late, or stored for a session somebody dropped locks the
    athlete out with no way back but a fresh authorization."""

    @staticmethod
    def _seed(key: str = "sess", *, stale: bool = True) -> None:
        privacy._ensure(key)
        privacy._SESSIONS[key].pair = _pair(expires_at=0 if stale else 4_000_000_000)

    async def test_a_refreshed_token_pair_is_stored_atomically(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Two concurrent callers, one rotation. The second must find the pair already fresh
        rather than spend a second rotation — which would invalidate the first one's result."""
        self._seed()
        calls: list[int] = []

        async def refresh(pair: TokenPair) -> TokenPair:
            calls.append(1)
            await asyncio.sleep(0.05)
            return _pair(expires_at=4_000_000_000)

        monkeypatch.setattr(client, "refresh", refresh)
        results = await asyncio.gather(
            privacy.ensure_fresh("sess"), privacy.ensure_fresh("sess")
        )

        assert len(calls) == 1, (
            f"{len(calls)} rotations were spent on one session. Strava invalidates the previous "
            "refresh token immediately, so the second rotation kills the first one's pair and "
            "whichever is stored last may already be dead."
        )
        assert sorted(results) == [False, True], (
            f"got {results}. Exactly one caller should report having rotated; the other must "
            "find the pair already fresh inside the lock."
        )
        assert privacy._SESSIONS["sess"].rotations == 1

    async def test_a_fresh_pair_is_not_rotated_at_all(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._seed(stale=False)

        async def refresh(pair: TokenPair) -> TokenPair:  # pragma: no cover - must not run
            raise AssertionError("a pair outside the refresh skew was rotated anyway")

        monkeypatch.setattr(client, "refresh", refresh)
        assert await privacy.ensure_fresh("sess") is False

    async def test_a_rotation_for_a_session_dropped_mid_flight_is_discarded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Storing it would resurrect a session somebody asked to forget — and leave a live
        credential behind after a disconnect."""
        self._seed()

        async def refresh(pair: TokenPair) -> TokenPair:
            privacy.forget("sess")
            return _pair(expires_at=4_000_000_000)

        monkeypatch.setattr(client, "refresh", refresh)
        with pytest.raises(privacy.NotConnected):
            await privacy.ensure_fresh("sess")
        assert "sess" not in privacy._SESSIONS, (
            "a rotation resurrected a forgotten session. After forget() there must be nothing "
            "left for a rotation to store into."
        )

    async def test_a_failed_revoke_still_leaves_nothing_behind(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`disconnect()` drops under the lock before it awaits the revoke, so an upstream
        failure cannot turn into a retained token. The athlete is told the truth instead."""
        self._seed(stale=False)

        async def revoke(pair: TokenPair) -> None:
            raise client.StravaUnavailable("strava is down")

        monkeypatch.setattr(client, "revoke", revoke)
        result = await privacy.disconnect("sess")

        assert privacy.session_count() == 0 and "sess" not in privacy._SESSIONS, (
            "a failed revoke left the session in place, which means it left the token in place. "
            "The pop has to happen before the await, not after it."
        )
        assert result.was_connected and not result.revoked, (
            f"got {result}. A failed revoke must be reported as unrevoked, not swallowed — the "
            "athlete has to know to remove the permission in Strava themselves."
        )

    async def test_a_successful_disconnect_revokes_the_newest_pair(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._seed(stale=False)
        privacy._SESSIONS["sess"].pair = _pair(expires_at=4_000_000_123)
        revoked: list[int] = []

        async def revoke(pair: TokenPair) -> None:
            revoked.append(pair.expires_at)

        monkeypatch.setattr(client, "revoke", revoke)
        result = await privacy.disconnect("sess")

        assert revoked == [4_000_000_123], f"revoked {revoked}, not the pair the session held"
        assert result.revoked and privacy.session_count() == 0

    async def test_disconnecting_nothing_is_not_an_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def revoke(pair: TokenPair) -> None:  # pragma: no cover - must not run
            raise AssertionError("revoked a credential for a session that never existed")

        monkeypatch.setattr(client, "revoke", revoke)
        result = await privacy.disconnect("never-connected")
        assert result.was_connected is False


class TestTheGateIsTheOnlyExitAndTheModuleSaysSo:
    def test_sync_returns_no_raw_activity_to_its_caller(self) -> None:
        """An AST check, because the failure is a `return` statement, not a value: `sync()` may
        hand back derived requirements and a typed outcome, never the `ActivityWindow` it read."""
        source = inspect.getsource(privacy.sync)
        assert "return window" not in source and "activities=" not in source, (
            "sync() appears to return the raw window it read. The raw payload stays in "
            f"_SESSIONS and derived values come out — see {FACTS}."
        )

    def test_every_session_field_holding_raw_data_is_private(self) -> None:
        view = privacy.connection("nobody")
        for field in type(view).model_fields if hasattr(type(view), "model_fields") else {}:
            assert field not in {"pair", "window", "summary"}, (
                f"the Connection view exposes {field!r}, which is where the raw payload lives"
            )
