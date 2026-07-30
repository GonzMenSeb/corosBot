"""Huella's privacy boundary — the D1 gate, written in Python rather than in a prompt.

Two things live here and nothing else does: the **credentials and raw Strava payloads**,
and the **one-way gate** that turns training data into `Requirement`s. Everything the rest
of Huella is allowed to see — `Connection`, `Sync`, `Requirement` — is derived, typed and
carries no field a Strava payload could ride in on.

**Why a module-level dict and not `rx.State`.** Reflex 0.9.7 *does* hold a dataclass state
var (verified 30 jul 2026, `AGENTS.md`), so "state cannot hold it" is not the reason. The
reason is `StateManagerDisk`: it pickles state — **including backend-only `_`-prefixed
vars, which merely stay off the wire** — into `.states/`. A `TokenPair` in a state var is a
refresh token in a file on the host, and a refresh token does not expire. So the store is
`_SESSIONS`, keyed the way Brújula keys its conversations (`router.session.client_token`),
and this module imports no `reflex` at all: there is no class here a var could be declared
on. `_Session.__reduce__` raises `PrivacyLeak`, so if a future edit *does* put one in
state, the pickle fails loudly instead of writing credentials to disk quietly.

**The gate is a function, and the type is the guarantee.** `derive_requirements()` takes an
`ActivityWindow` and returns `tuple[Requirement, ...]`; no public name here returns an
`Activity`, an `Athlete`, an `ActivityWindow` or a `TokenPair`. `Requirement` is frozen with
`extra="forbid"` and a `str | int | bool` value, so there is no structural way a payload
crosses — `packages/coros_core/models.py` owns that half.

**`rationale` is the one free string, so nothing is allowed to write one.** It is a pure
function of `(key, sample_size, window_days)`: `_requirement()` generates it from
`_RATIONALES` — there is no `rationale=` parameter to hand a Strava string to — and `seal()`
**regenerates it from the requirement's own fields and compares**. A regex over the template
was the first attempt and it was not enough: an integer placeholder happily accepted
`77777777 días`, so an athlete id could have ridden out inside a phrasing that matched.
Exact regeneration leaves no slot for a third number, and the two counts that remain are
bounded by `MAX_SAMPLE` / `MAX_WINDOW_DAYS`, which an id fails. `seal()` also requires every
`value` to be a label that key may carry — `_VALUES_BY_KEY`, our own band and discipline
vocabulary — so an unbounded upstream string (`sport_type` is 56 values wide and grows)
cannot become a value either. Widening those two tables is the deliberate act that widens
the boundary; nothing else can.

**Rotation is atomic per session.** "Once a new refresh token is returned, the older
refresh token is invalidated immediately" — so a half-stored pair is an athlete locked out
of their own history with no way back but a fresh authorization. `TokenPair` is frozen, so
storing one is a single reference swap, and it happens under a **per-session**
`asyncio.Lock`: one global lock would serialise every athlete's reads behind one athlete's
refresh. `ensure_fresh()` re-checks `needs_refresh()` *inside* the lock, so a concurrent
double-refresh spends one rotation and the second caller sees the newer pair rather than
storing a stale one over it.

**`disconnect()` drops before it revokes.** The local copy is the part we control: the pop
happens under the lock with no `await` in front of it, so a revoke that raises, times out or
is cancelled cannot leave a retained token. `client.revoke()` answers 200 whether or not
the token existed, so its success was never proof of anything anyway; the failure is
reported in `Disconnect.revoked` rather than swallowed, because an athlete whose grant is
still live upstream may want to remove it in Strava's own settings.

**Sessions expire.** A process serving a public URL for weeks accumulates credentials, so
the store is bounded by `MAX_SESSIONS` (LRU, Brújula's pattern) and swept at
`SESSION_TTL_SECONDS` of idleness. An eviction drops our copy without revoking — a sync
dict access cannot make a network call, and dropping our copy is the half that protects the
athlete. Reconnecting costs one click.

A refusal is never an empty history: `sync()` carries `ToolOutcome` through verbatim, so
`RATE_LIMITED` and `TIMEOUT` stay distinguishable from "no activities in the window".
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import secrets
import time
from collections import OrderedDict
from collections.abc import Iterable, Sequence
from datetime import timezone

from pydantic import BaseModel, ConfigDict

from coros_core.models import Provenance, Requirement, RequirementKey
from coros_core.outcomes import ToolOutcome
from coros_core.trace import emit
from huella.strava import client
from huella.strava.models import Activity, ActivityWindow, TokenPair

SOURCE: Provenance = "strava"

# 90 days of history at `client.PER_PAGE * client.MAX_PAGES` = up to 120 activities. The
# window is a parameter of the read, not a clock in here deciding what "recent" means.
WINDOW_DAYS = 90

MAX_SESSIONS = 200

# One access-token lifetime of idleness. After that we hold a dead access token and a live
# refresh token nobody asked us to keep.
SESSION_TTL_SECONDS = 6 * 3600

# A second discipline is part of the mix at a quarter of the moving time, not at any share:
# one commute does not make an athlete a cyclist.
MIX_SHARE = 0.25

_INF = float("inf")

_WEEKLY_HOURS: tuple[tuple[float, str], ...] = (
    (2, "0-2"), (4, "2-4"), (6, "4-6"), (9, "6-9"), (12, "9-12"), (_INF, "12+"),
)
_LONGEST_HOURS: tuple[tuple[float, str], ...] = (
    (1, "0-1"), (2, "1-2"), (3, "2-3"), (5, "3-5"), (_INF, "5+"),
)
_WEEKLY_ELEVATION: tuple[tuple[float, str], ...] = (
    (200, "0-200"), (600, "200-600"), (1200, "600-1200"), (2500, "1200-2500"), (_INF, "2500+"),
)
_WEEKLY_SESSIONS: tuple[tuple[float, str], ...] = (
    (1, "0-1"), (2, "1-2"), (4, "2-4"), (6, "4-6"), (_INF, "6+"),
)

DISCIPLINE_OTHER = "other"

# Matched as substrings of a normalised `sport_type`, in order, INTO our own closed
# vocabulary. `sport_type` is a plain `str` on purpose — the live enum is 56 values wide and
# the changelog added six on 30 apr 2026 — so the mapping has to survive a value nobody
# here has seen, and an unrecognised one becomes `other` rather than travelling verbatim.
# Order is load-bearing: `trailrun` before `run`, `snowshoe` before `snow`/`ski`.
_DISCIPLINE_MATCHERS: tuple[tuple[str, str], ...] = (
    ("trailrun", "trail_run"),
    ("snowshoe", "hike"),
    ("run", "run"),
    ("walk", "walk"),
    ("hike", "hike"),
    ("ride", "ride"),
    ("bike", "ride"),
    ("cycl", "ride"),
    ("velomobile", "ride"),
    ("swim", "swim"),
    ("skate", "skate"),
    ("ski", "ski"),
    ("snowboard", "ski"),
    ("row", "row"),
    ("kayak", "paddle"),
    ("canoe", "paddle"),
    ("paddl", "paddle"),
    ("surf", "paddle"),
    ("weight", "strength"),
    ("crossfit", "strength"),
    ("workout", "strength"),
    ("yoga", "mobility"),
    ("pilates", "mobility"),
    ("elliptical", "indoor_cardio"),
    ("stair", "indoor_cardio"),
)

DISCIPLINES: frozenset[str] = frozenset(v for _, v in _DISCIPLINE_MATCHERS) | {DISCIPLINE_OTHER}

# Every string this gate may emit as a `Requirement.value`, per key. Finite, computed here,
# and checked by `seal()`: a Strava string can only become a value by coinciding with one of
# our own labels, and a label from the wrong band cannot cross either.
_VALUES_BY_KEY: dict[str, frozenset[str]] = {
    "discipline": DISCIPLINES,
    "sport_mix": DISCIPLINES
    | frozenset(f"{a}+{b}" for a in DISCIPLINES for b in DISCIPLINES if a != b),
    "weekly_hours_band": frozenset(label for _, label in _WEEKLY_HOURS),
    "longest_session_band": frozenset(label for _, label in _LONGEST_HOURS),
    "elevation_band": frozenset(label for _, label in _WEEKLY_ELEVATION),
    "consistency_band": frozenset(label for _, label in _WEEKLY_SESSIONS),
}

ALLOWED_VALUES: frozenset[str] = frozenset().union(*_VALUES_BY_KEY.values())

# Every template takes exactly `(sample_size, window_days)` and nothing else, which is what
# makes a rationale a pure function of two fields the `Requirement` already carries: `seal()`
# regenerates it and compares. A third number — an activity id, a distance, a heart rate —
# has no slot to sit in, so this is not a regex a leak can satisfy by looking like digits.
_RATIONALES: dict[str, str] = {
    "discipline": "Disciplina dominante por tiempo en movimiento en {} actividades de {} días.",
    "sport_mix": "Reparto por tiempo en movimiento sobre {} actividades en {} días.",
    "weekly_hours_band": (
        "Banda derivada de {} actividades en {} días. Es un rango, no una medición."
    ),
    "longest_session_band": "Salida más larga observada entre {} actividades en {} días.",
    "elevation_band": "Desnivel positivo semanal derivado de {} actividades en {} días.",
    "consistency_band": "Actividades por semana sobre {} actividades en {} días.",
}

# `client.fetch_activities` can return `PER_PAGE * MAX_PAGES` activities and no more, with
# slack for a caller that asks for more pages. A sample or a window outside these is a
# number that came from somewhere other than counting, which is the other way a figure
# reaches prose: through the two integer fields rather than through the string.
MAX_SAMPLE = 500
MAX_WINDOW_DAYS = 730

_FINGERPRINT_SALT = b"huella.privacy.v1:"


class PrivacyLeak(RuntimeError):
    """The gate produced something it is not allowed to produce. A defect, not a case — and
    the message names the key and the reason, never the offending text: an exception is read
    in a log, and a log is exactly where the leak would then live."""


class NotConnected(RuntimeError):
    """No credential for this session. Distinct from a refusal: nothing was asked upstream."""


class Connection(BaseModel):
    """What `rx.State` and the UI may hold. No token, no athlete name, no activity.

    `token_fingerprint` is a salted digest of BOTH halves at once, which is what lets a test
    prove a rotation stored one pair rather than half of two."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    connected: bool = False
    athlete_id: int | None = None
    scopes: tuple[str, ...] = ()
    expires_at: int = 0
    seconds_left: int = 0
    needs_refresh: bool = False
    rotations: int = 0
    version: int = 0
    token_fingerprint: str = ""
    has_window: bool = False


class Sync(BaseModel):
    """One read of the history, as everything downstream sees it: an outcome, some counts,
    and the derived requirements. `outcome` is carried through from the `ToolResult`
    verbatim — a 429 is `RATE_LIMITED` and never an empty window."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: ToolOutcome
    detail: str = ""
    retry_after_s: float | None = None
    requirements: tuple[Requirement, ...] = ()
    window_days: int = 0
    sample_size: int = 0
    manual_dropped: int = 0
    pages: int = 0
    truncated: bool = False
    stale_days: int | None = None


class Disconnect(BaseModel):
    """The local drop always happened. `revoked` says whether Strava agreed."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    was_connected: bool
    revoked: bool = False
    detail: str = ""


# ── the gate ───────────────────────────────────────────────────────────────────


def discipline_of(sport_type: str) -> str:
    normalised = "".join(c for c in sport_type.lower() if c.isalnum())
    for needle, discipline in _DISCIPLINE_MATCHERS:
        if needle in normalised:
            return discipline
    return DISCIPLINE_OTHER


def seal(requirements: Iterable[Requirement]) -> tuple[Requirement, ...]:
    """The only exit. Every requirement leaving this module passes through here."""
    sealed = tuple(requirements)
    for requirement in sealed:
        reason = _violation(requirement)
        if reason:
            emit("guardrail.privacy_leak", {"key": requirement.key, "reason": reason}, "guardrail")
            raise PrivacyLeak(
                f"{requirement.key}: {reason}. Nothing leaves the gate but a value from "
                "ALLOWED_VALUES and a rationale generated from _RATIONALES."
            )
    return sealed


def _violation(requirement: Requirement) -> str:
    if requirement.source != SOURCE or not requirement.derived:
        return "not declared as derived from strava"
    if not 0 < requirement.sample_size <= MAX_SAMPLE:
        return "sample size is not a count of activities we could have read"
    if not 0 < requirement.window_days <= MAX_WINDOW_DAYS:
        return "window is not a number of days we could have asked for"
    if not isinstance(requirement.value, str):
        return f"value is {type(requirement.value).__name__}, not a label"
    allowed = _VALUES_BY_KEY.get(requirement.key)
    if allowed is None or requirement.key not in _RATIONALES:
        return "this key is not one the gate derives"
    if requirement.value not in allowed:
        return "value is not a label this key may carry"
    expected = _generated(requirement.key, requirement.sample_size, requirement.window_days)
    if requirement.rationale != expected:
        return "rationale is not the phrasing this key generates"
    return ""


def _band(value: float, edges: tuple[tuple[float, str], ...]) -> str:
    return next(label for edge, label in edges if value < edge)


def _days(window_days: int) -> int:
    days = max(1, int(window_days))
    if days > MAX_WINDOW_DAYS:
        raise ValueError(
            f"a window of {days} days is wider than MAX_WINDOW_DAYS={MAX_WINDOW_DAYS}. The "
            "rationale states the window, so it cannot state one the seal would refuse."
        )
    return days


def _generated(key: RequirementKey, sample_size: int, window_days: int) -> str:
    if type(sample_size) is not int or type(window_days) is not int:
        raise PrivacyLeak(f"{key}: a rationale interpolates two counts, nothing else")
    return _RATIONALES[key].format(sample_size, window_days)


def _requirement(
    key: RequirementKey, value: str, *, sample_size: int, window_days: int
) -> Requirement:
    """The only constructor. `rationale` is generated here rather than passed in, so there
    is no parameter a caller could hand a Strava string to."""
    return Requirement(
        key=key,
        value=value,
        source=SOURCE,
        derived=True,
        sample_size=sample_size,
        window_days=window_days,
        rationale=_generated(key, sample_size, window_days),
    )


def _shares(activities: Sequence[Activity]) -> tuple[tuple[str, int, int], ...]:
    totals: dict[str, list[int]] = {}
    for activity in activities:
        slot = totals.setdefault(discipline_of(activity.sport_type), [0, 0])
        slot[0] += max(0, activity.moving_time_s)
        slot[1] += 1
    return tuple(
        sorted(
            ((name, seconds, count) for name, (seconds, count) in totals.items()),
            key=lambda row: (-row[1], -row[2], row[0]),
        )
    )


def _mix(shares: tuple[tuple[str, int, int], ...], activities: int) -> str:
    first, *rest = shares
    if not rest:
        return first[0]
    seconds = sum(row[1] for row in shares)
    share = rest[0][1] / seconds if seconds > 0 else rest[0][2] / max(1, activities)
    return f"{first[0]}+{rest[0][0]}" if share >= MIX_SHARE else first[0]


def derive_requirements(
    window: ActivityWindow, *, window_days: int = WINDOW_DAYS
) -> tuple[Requirement, ...]:
    """Training data in, `Requirement`s out. The whole gate, and a pure function of the
    window so a test can drive it without a session or a socket.

    Manual entries are dropped: Huella reasons from demonstrated training, and a typed-in
    activity is self-report wearing an activity's clothes. Bands rather than figures —
    an exact weekly volume identifies an athlete and a derived number must never read as
    a measured one.
    """
    demonstrated = tuple(a for a in window.activities if not a.manual)
    manual = window.count - len(demonstrated)
    count = len(demonstrated)
    days = _days(window_days)
    if count == 0:
        emit(
            "guardrail.privacy_gate",
            {"activities": 0, "manual_dropped": manual, "window_days": days, "keys": []},
            "guardrail",
        )
        return ()

    weeks = max(1.0, days / 7.0)
    moving_hours = sum(max(0, a.moving_time_s) for a in demonstrated) / 3600.0
    longest_hours = max(max(0, a.moving_time_s) for a in demonstrated) / 3600.0
    elevation = sum(max(0.0, a.total_elevation_gain_m) for a in demonstrated)
    shares = _shares(demonstrated)
    dominant = shares[0][0]

    built = (
        _requirement("discipline", dominant, sample_size=count, window_days=days),
        _requirement("sport_mix", _mix(shares, count), sample_size=count, window_days=days),
        _requirement(
            "weekly_hours_band",
            _band(moving_hours / weeks, _WEEKLY_HOURS),
            sample_size=count,
            window_days=days,
        ),
        _requirement(
            "longest_session_band",
            _band(longest_hours, _LONGEST_HOURS),
            sample_size=count,
            window_days=days,
        ),
        _requirement(
            "elevation_band",
            _band(elevation / weeks, _WEEKLY_ELEVATION),
            sample_size=count,
            window_days=days,
        ),
        _requirement(
            "consistency_band",
            _band(count / weeks, _WEEKLY_SESSIONS),
            sample_size=count,
            window_days=days,
        ),
    )
    sealed = seal(built)
    emit(
        "guardrail.privacy_gate",
        {
            "activities": count,
            "manual_dropped": manual,
            "window_days": days,
            "keys": [r.key for r in sealed],
        },
        "guardrail",
    )
    return sealed


# ── the store: tokens and raw payloads, and nowhere else ───────────────────────


class _Session:
    """One athlete's credential and the last window read with it. Never returned, never
    pickled: `__reduce__` refuses, so a future `rx.State` var holding one fails loudly
    instead of writing a refresh token into `.states/`."""

    __slots__ = ("pair", "window", "summary", "lock", "touched_at", "rotations", "version",
                 "state", "dropped")

    def __init__(self, now: float) -> None:
        self.pair: TokenPair | None = None
        self.window: ActivityWindow | None = None
        self.summary: Sync | None = None
        self.lock = asyncio.Lock()
        self.touched_at = now
        self.rotations = 0
        self.version = 0
        self.state = ""
        self.dropped = False

    def __reduce__(self):
        raise PrivacyLeak(
            "a Strava session is not serialisable by design. StateManagerDisk pickles every "
            "state var, backend-only ones included, so reaching this is a token on its way "
            "to .states/ — keep it in privacy._SESSIONS."
        )

    def __repr__(self) -> str:
        return f"<_Session tokens={'yes' if self.pair else 'no'} version={self.version}>"


_SESSIONS: OrderedDict[str, _Session] = OrderedDict()
_evicted = 0


def _drop(key: str, session: _Session | None = None) -> bool:
    found = _SESSIONS.pop(key, None)
    target = session if session is not None else found
    if target is None:
        return False
    target.dropped = True
    target.pair = None
    target.window = None
    target.summary = None
    target.state = ""
    return True


def sweep(*, now: float | None = None) -> int:
    """Drop idle sessions. Our copy of the credential goes; the grant upstream is left
    alone, because a dict access cannot make a network call and dropping what we hold is
    the half that protects the athlete."""
    global _evicted
    stamp = time.time() if now is None else now
    stale = [k for k, s in _SESSIONS.items() if stamp - s.touched_at > SESSION_TTL_SECONDS]
    for key in stale:
        _drop(key)
    while len(_SESSIONS) > MAX_SESSIONS:
        oldest, session = _SESSIONS.popitem(last=False)
        _drop(oldest, session)
        stale.append(oldest)
    if stale:
        _evicted += len(stale)
        emit("privacy.evicted", {"dropped": len(stale), "sessions": len(_SESSIONS)})
    return len(stale)


def _ensure(key: str, *, now: float | None = None) -> _Session:
    stamp = time.time() if now is None else now
    session = _SESSIONS.pop(key, None)
    if session is None or session.dropped:
        session = _Session(stamp)
    session.touched_at = stamp
    _SESSIONS[key] = session
    sweep(now=stamp)
    return session


def _live(key: str, *, now: float | None = None) -> _Session:
    session = _SESSIONS.get(key)
    if session is None or session.pair is None:
        raise NotConnected(
            "esta sesión no tiene una cuenta de Strava conectada. El atleta tiene que "
            "autorizar de nuevo — no se guarda nada entre reinicios."
        )
    session.touched_at = time.time() if now is None else now
    _SESSIONS.move_to_end(key)
    return session


def fingerprint(pair: TokenPair) -> str:
    """A salted digest of both halves together. Irreversible, and the only thing about a
    credential this module will state out loud."""
    digest = hashlib.sha256(
        _FINGERPRINT_SALT + pair.access_token.encode() + b"|" + pair.refresh_token.encode()
    )
    return digest.hexdigest()[:16]


def _view(session: _Session | None, *, now: float | None = None) -> Connection:
    if session is None or session.pair is None:
        return Connection()
    pair = session.pair
    return Connection(
        connected=True,
        athlete_id=pair.athlete_id,
        scopes=pair.scopes,
        expires_at=pair.expires_at,
        seconds_left=pair.seconds_left(now),
        needs_refresh=pair.needs_refresh(now),
        rotations=session.rotations,
        version=session.version,
        token_fingerprint=fingerprint(pair),
        has_window=session.window is not None,
    )


def connection(key: str, *, now: float | None = None) -> Connection:
    return _view(_SESSIONS.get(key), now=now)


def is_connected(key: str) -> bool:
    session = _SESSIONS.get(key)
    return session is not None and session.pair is not None


def requirements(key: str) -> tuple[Requirement, ...]:
    """What the catalog path may read. The window it was derived from stays in here."""
    session = _SESSIONS.get(key)
    return () if session is None or session.summary is None else session.summary.requirements


def last_sync(key: str) -> Sync | None:
    session = _SESSIONS.get(key)
    return None if session is None else session.summary


def forget(key: str) -> bool:
    """Drop everything held for one session without asking Strava anything. What the
    person's own "delete my data" click runs, and what an eviction runs."""
    dropped = _drop(key)
    if dropped:
        emit("privacy.forgotten", {"sessions": len(_SESSIONS)})
    return dropped


def forget_all() -> int:
    count = len(_SESSIONS)
    for key in list(_SESSIONS):
        _drop(key)
    if count:
        emit("privacy.forgotten", {"dropped": count, "sessions": 0})
    return count


def session_count() -> int:
    return len(_SESSIONS)


def diagnostics() -> dict[str, int]:
    """Counts only. There is deliberately no accessor that answers with a token, an
    athlete or an activity."""
    return {
        "sessions": len(_SESSIONS),
        "with_token": sum(1 for s in _SESSIONS.values() if s.pair is not None),
        "with_window": sum(1 for s in _SESSIONS.values() if s.window is not None),
        "rotations": sum(s.rotations for s in _SESSIONS.values()),
        "evicted": _evicted,
    }


# ── the credential path ────────────────────────────────────────────────────────


def issue_state(key: str) -> str:
    """The OAuth `state`, kept beside the credential rather than in a state var. Single
    use: `consume_state` clears it whether or not it matched."""
    session = _ensure(key)
    session.state = secrets.token_urlsafe(24)
    return session.state


def consume_state(key: str, state: str) -> bool:
    session = _SESSIONS.get(key)
    if session is None or not session.state or not state:
        return False
    expected, session.state = session.state, ""
    # `hmac.compare_digest` raises TypeError on a non-ASCII str, and this one arrives off a
    # query string — a refusal is the answer, not a 500.
    return bool(state.isascii()) and hmac.compare_digest(expected, state)


async def connect(key: str, code: str, *, scope: str = "") -> Connection:
    """Exchange the callback's code and store the pair. Raises the typed `client` errors —
    the callback route decides what the athlete is told.

    A reconnect replaces the pair and does NOT revoke the old one: revoking a token
    revokes the grant it belongs to, which would take the credential just issued with it.
    """
    pair = await client.exchange(code, scope=scope)
    session = _ensure(key)
    async with session.lock:
        session.pair = pair
        session.window = None
        session.summary = None
        session.version += 1
    emit(
        "privacy.connected",
        {
            "athlete_id": pair.athlete_id,
            "scopes": list(pair.scopes),
            "expires_at": pair.expires_at,
        },
    )
    return _view(session)


async def ensure_fresh(key: str, *, now: float | None = None) -> bool:
    """Rotate if the pair is within `TokenPair.needs_refresh()`'s hour, and store the new
    one as one visible transition. True when a rotation happened.

    Concurrency: the `needs_refresh` re-check is inside the lock, so the second of two
    concurrent callers finds the pair already fresh, spends no rotation and cannot store a
    stale pair over the newer one.
    """
    session = _live(key, now=now)
    async with session.lock:
        pair = session.pair
        if session.dropped or pair is None:
            raise NotConnected("la sesión se cerró mientras se esperaba el candado")
        if not pair.needs_refresh(now):
            return False
        fresh = await client.refresh(pair)
        if session.dropped or _SESSIONS.get(key) is not session:
            # Evicted or forgotten mid-rotation. Storing it would resurrect a session
            # somebody dropped; the pair is discarded instead.
            raise NotConnected("la sesión se cerró mientras el par rotaba")
        session.pair = fresh
        session.rotations += 1
        session.version += 1
    emit(
        "privacy.rotated",
        {
            "athlete_id": fresh.athlete_id,
            "rotations": session.rotations,
            "expires_at": fresh.expires_at,
        },
    )
    return True


async def disconnect(key: str) -> Disconnect:
    """Drop first, then revoke.

    The pop happens under the lock with no `await` in front of it, so a revoke that raises,
    times out or is cancelled cannot leave a retained token — and taking the same lock a
    rotation takes means the pair being revoked is the newest one, never the one a
    concurrent refresh just replaced.
    """
    session = _SESSIONS.get(key)
    if session is None:
        return Disconnect(was_connected=False, detail="no había sesión que cerrar")

    async with session.lock:
        pair = session.pair
        _drop(key, session)

    if pair is None:
        emit("privacy.disconnected", {"revoked": False, "had_token": False})
        return Disconnect(was_connected=False, detail="la sesión no tenía credencial")

    try:
        await client.revoke(pair)
    except (client.StravaError, client.StravaUnconfigured) as exc:
        detail = str(exc)[:200]
        emit(
            "privacy.disconnected",
            {"revoked": False, "had_token": True, "error": type(exc).__name__},
            "error",
        )
        return Disconnect(
            was_connected=True,
            revoked=False,
            detail=(
                f"se borró todo lo que teníamos, pero Strava no confirmó la revocación "
                f"({detail}). El atleta puede quitar el permiso en su cuenta de Strava."
            ),
        )
    emit("privacy.disconnected", {"revoked": True, "had_token": True})
    return Disconnect(was_connected=True, revoked=True)


# ── the data path ──────────────────────────────────────────────────────────────


def _refused(exc: Exception, window_days: int) -> Sync:
    if isinstance(exc, client.StravaRateLimited):
        return Sync(
            outcome=ToolOutcome.RATE_LIMITED,
            detail=exc.detail,
            retry_after_s=exc.retry_after_s,
            window_days=window_days,
        )
    if isinstance(exc, client.StravaAuthDenied):
        return Sync(
            outcome=ToolOutcome.NEEDS_HUMAN,
            detail=(
                f"{exc.detail}. El par de tokens ya no sirve: el atleta tiene que "
                "reconectar su cuenta de Strava."
            ),
            window_days=window_days,
        )
    if isinstance(exc, client.StravaScopeDenied):
        return Sync(
            outcome=ToolOutcome.NOT_ELIGIBLE, detail=exc.detail, window_days=window_days
        )
    if isinstance(exc, client.StravaTimeout):
        return Sync(outcome=ToolOutcome.TIMEOUT, detail=exc.detail, window_days=window_days)
    if isinstance(exc, client.StravaError):
        return Sync(
            outcome=ToolOutcome.UPSTREAM_ERROR, detail=exc.detail, window_days=window_days
        )
    return Sync(
        outcome=ToolOutcome.UPSTREAM_ERROR, detail=str(exc)[:200], window_days=window_days
    )


def _stale_days(window: ActivityWindow, now: float) -> int | None:
    stamps = [a.start_date for a in window.activities if a.start_date is not None]
    if not stamps:
        return None
    newest = max(stamps)
    if newest.tzinfo is None:
        newest = newest.replace(tzinfo=timezone.utc)
    return max(0, int((now - newest.timestamp()) // 86400))


async def sync(key: str, *, window_days: int = WINDOW_DAYS, now: float | None = None) -> Sync:
    """Refresh if needed, read the window, keep it here, and answer with derived values.

    The raw `ActivityWindow` is stored in `_SESSIONS` and returned to nobody. A refusal is
    carried through as its own `ToolOutcome`: "we could not look" and "there is nothing"
    are different sentences, and the uncertainty layer needs to tell them apart.
    """
    stamp = time.time() if now is None else now
    days = _days(window_days)
    session = _live(key, now=stamp)

    try:
        await ensure_fresh(key, now=stamp)
    except (client.StravaError, client.StravaUnconfigured) as exc:
        return _refused(exc, days)

    pair = session.pair
    if pair is None:
        raise NotConnected("la sesión se cerró durante la rotación del par")
    if pair.scopes and not (pair.has_scope("activity:read") or pair.has_scope("activity:read_all")):
        return Sync(
            outcome=ToolOutcome.NOT_ELIGIBLE,
            detail=(
                "la autorización no incluye el permiso de actividades, así que no hay "
                "historial que leer. No es un historial vacío."
            ),
            window_days=days,
        )

    result = await client.fetch_activities(pair, after=int(stamp - days * 86400))
    if result.outcome is not ToolOutcome.OK:
        return Sync(
            outcome=result.outcome,
            detail=result.detail,
            retry_after_s=result.retry_after_s,
            window_days=days,
        )
    window = result.data
    if not isinstance(window, ActivityWindow):
        return Sync(
            outcome=ToolOutcome.UPSTREAM_ERROR,
            detail=f"la ventana llegó como {type(window).__name__}, no como ActivityWindow",
            window_days=days,
        )

    demonstrated = sum(1 for a in window.activities if not a.manual)
    summary = Sync(
        outcome=ToolOutcome.OK,
        requirements=derive_requirements(window, window_days=days),
        window_days=days,
        sample_size=demonstrated,
        manual_dropped=window.count - demonstrated,
        pages=window.pages,
        truncated=window.truncated,
        stale_days=_stale_days(window, stamp),
    )
    async with session.lock:
        if session.dropped:
            raise NotConnected("la sesión se cerró mientras se leía el historial")
        session.window = window
        session.summary = summary
    emit(
        "privacy.synced",
        {
            "activities": summary.sample_size,
            "manual_dropped": summary.manual_dropped,
            "pages": summary.pages,
            "truncated": summary.truncated,
            "stale_days": summary.stale_days,
            "requirements": len(summary.requirements),
        },
    )
    return summary
