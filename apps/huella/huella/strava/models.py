"""The Strava wire, parsed. Every shape `client.py` is allowed to hand upwards.

Untrusted boundary: nothing reaches Huella's state, its guardrails or the model except
through one of these. `client.py` owns the transport and the refusals; this file owns the
shapes, and it imports nothing of ours so a test — or `privacy.py` — can read a type
without opening a socket.

Verified 30 Jul 2026 against `developers.strava.com/docs/authentication` and the
published swagger (`swagger.json`, `activity.json`, `athlete.json`). These look like bugs
and are not:

  * **`expires_at` is Strava's own absolute epoch second**, carried through as-is.
    Recomputing it from `expires_in` puts our clock skew inside a credential's lifetime.
  * **The refresh response carries no `athlete` and no `scope`** — only the code exchange
    does — so `TokenResponse` declares both optional and `client.refresh()` carries them
    forward off the old pair.
  * **The granted scope can be narrower than the requested one** (the athlete may uncheck
    boxes) and it arrives space-delimited in the token body, comma-delimited in the
    callback query string. `TokenPair.scopes` splits on either.
  * **Heart rate and power are not declared at all.** They are what turns a training
    window into a medical record, no `Requirement` can carry them, and a field that is
    never parsed cannot leak.
  * **`start_date_local` is local wall time wearing a `Z` it has not earned** — the
    swagger's own example has `12:15:09Z` arriving as `05:15:09Z` for a GMT-08:00
    athlete — so it stays a string. As a datetime it is silently wrong by the offset,
    and `start_date` is the real instant.
  * **`sport_type` is a plain `str`.** The live enum is 56 values wide and grows by
    design — the changelog's 30 Apr 2026 entry adds six (Basketball, Cricket, Dance,
    Padel, Physical Therapy, Volleyball). A Literal would hand the model invented
    categories and reject real activities the week Strava adds the next one.
  * **Unknown fields are ignored, and a null in a string field is not a failure.**
    Strava's own documented example for `SummaryActivity` carries `has_heartrate`,
    `average_heartrate` and `suffer_score`, none of which appear in the schema it is an
    example of — `extra="forbid"` here is a self-inflicted outage. The same example sends
    `"country": null`, and a `str` field handed an explicit null is a hard validation
    error, which would cost the whole window. Every field declared here IS validated:
    `client.py` turns a failure into `UPSTREAM_ERROR` with detail rather than a quietly
    shorter history.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

# Athlete-authored free text, bounded before it can reach a prompt.
NAME_CHARS = 200

# Strava returns the SAME access token while more than an hour is left on it, so an hour
# is also the point at which refreshing starts doing anything.
REFRESH_SKEW_SECONDS = 3600


def _text(value: Any) -> Any:
    return "" if value is None else value


def _prose(value: Any) -> Any:
    text = _text(value)
    return text[:NAME_CHARS] if isinstance(text, str) else text


_Text = Annotated[str, BeforeValidator(_text)]
_Prose = Annotated[str, BeforeValidator(_prose)]


class TokenPair(BaseModel):
    """Both halves of the credential, and the only thing `exchange()`/`refresh()` return.

    Frozen because a rotation is a NEW pair. Mutating one in place is how one half reaches
    disk while the other stays in memory, and by then Strava has already invalidated
    whichever half got left behind. `model_dump()` still yields the tokens — persistence
    needs them — so the redaction below is about the accidental paths: a traceback, an
    f-string, a pydantic error.
    """

    model_config = ConfigDict(frozen=True)

    access_token: str
    refresh_token: str
    expires_at: int
    scope: str = ""
    athlete_id: int | None = None

    def __repr_args__(self) -> list[tuple[str | None, Any]]:
        return [
            (name, "…redacted…" if str(name).endswith("_token") else value)
            for name, value in super().__repr_args__()
        ]

    @property
    def scopes(self) -> tuple[str, ...]:
        return tuple(s for s in self.scope.replace(",", " ").split() if s)

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes

    def seconds_left(self, now: float | None = None) -> int:
        return int(self.expires_at - (time.time() if now is None else now))

    def is_expired(self, now: float | None = None) -> bool:
        return self.seconds_left(now) <= 0

    def needs_refresh(self, now: float | None = None) -> bool:
        return self.seconds_left(now) <= REFRESH_SKEW_SECONDS


class TokenResponse(BaseModel):
    """The exchange body and the refresh body in one shape — the refresh half carries
    neither `athlete` nor `scope`. Only the three credential fields are required: a
    profile field is not worth losing a working credential over."""

    model_config = ConfigDict(extra="ignore")

    access_token: str = Field(min_length=1)
    refresh_token: str = Field(min_length=1)
    expires_at: int = Field(gt=0)
    scope: _Text = ""
    athlete: dict[str, Any] | None = None

    @property
    def athlete_id(self) -> int | None:
        found = (self.athlete or {}).get("id")
        return found if isinstance(found, int) else None


class RateLimit(BaseModel):
    """One header pair's four numbers: `X-RateLimit-Limit: 200,2000` read against
    `X-RateLimit-Usage: 17,412`."""

    model_config = ConfigDict(frozen=True)

    fifteen_min_limit: int
    fifteen_min_usage: int
    daily_limit: int
    daily_usage: int

    @property
    def exhausted(self) -> bool:
        return (
            self.fifteen_min_usage >= self.fifteen_min_limit > 0
            or self.daily_usage >= self.daily_limit > 0
        )


class RateLimits(BaseModel):
    """Both families as the last response reported them. Either can be absent: the token
    endpoint sends these headers not at all."""

    model_config = ConfigDict(frozen=True)

    overall: RateLimit | None = None
    read: RateLimit | None = None

    @property
    def exhausted(self) -> bool:
        return any(limit.exhausted for limit in (self.read, self.overall) if limit is not None)


class Athlete(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore", populate_by_name=True)

    athlete_id: int = Field(alias="id")
    username: _Text = ""
    firstname: _Text = ""
    lastname: _Text = ""
    city: _Text = ""
    state: _Text = ""
    country: _Text = ""
    # feet or meters, and display only: every figure below is metric either way.
    measurement_preference: _Text = ""


class Activity(BaseModel):
    """One activity as Huella reasons about it: what was done, how long, how far, how
    much climbing, and whether it was demonstrated or typed in."""

    model_config = ConfigDict(frozen=True, extra="ignore", populate_by_name=True)

    activity_id: int = Field(alias="id")
    # The one field here a person can write a prompt into. Bounded, and never an
    # instruction: sanitising it before it reaches the model is the agent layer's job
    # (`catalog.strip_untrusted` is the repo's sanitiser).
    name: _Prose = ""
    sport_type: _Text = ""
    distance_m: float = Field(default=0.0, alias="distance")
    moving_time_s: int = Field(default=0, alias="moving_time")
    elapsed_time_s: int = Field(default=0, alias="elapsed_time")
    total_elevation_gain_m: float = Field(default=0.0, alias="total_elevation_gain")
    start_date: datetime | None = None
    start_date_local: _Text = ""
    trainer: bool = False
    commute: bool = False
    # Typed in by hand rather than recorded. Huella reasons from demonstrated training,
    # and a manual entry is self-report wearing an activity's clothes.
    manual: bool = False


class ActivityWindow(BaseModel):
    """What one read of the history actually covered. `truncated` is the load-bearing
    half: the uncertainty layer has to be able to say "this is what we saw" rather than
    "this is what there is"."""

    model_config = ConfigDict(frozen=True)

    activities: tuple[Activity, ...] = ()
    pages: int = 0
    truncated: bool = False
    after: int | None = None
    before: int | None = None

    @property
    def count(self) -> int:
        return len(self.activities)
