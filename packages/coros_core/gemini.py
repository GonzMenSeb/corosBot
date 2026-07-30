"""The one place a Gemini request is issued.

Both apps go through `generate()`. A second call site would bring its own retry policy
and its own share of one per-project quota that Brújula, Huella and DecaBot already
share — `vault_decabot_gemini_api_key` is a single credential, by decision (see
`docs/DECISIONS.md`, 29 Jul 2026), which is also why there is no key pool here and no
`bind_key()`: DecaBot rotates a pool for a QR-code audience, and we deliberately do not.
An AST scan in `tests/test_gemini.py` keeps the door single, the model named once, and
every client built with the timeout below.

Two shapes are corrected rather than trusted, because both fail in a way that reads as
our bug:

  * **A bare `FunctionDeclaration` in `tools=`** raises `AttributeError:
    'FunctionDeclaration' object has no attribute 'function_declarations'` *inside*
    google-genai, before any HTTP call, for a typed config and a dict one alike. The
    fix is one `types.Tool` wrapper, and `as_tools()` is the only place that knows it.
  * **`.env` is looked for by absolute path, at two named roots.** `load_dotenv()` with
    no argument resolves against the CALLING file and silently finds nothing when a
    script imports us.

Retries are deliberate and bounded: `gemini-3.6-flash` answers 503 "experiencing high
demand" intermittently, and a shared quota makes 429 ordinary rather than exotic. Giving
up re-raises the SDK error with its `.code` intact, because the caller's message to a
human depends on which refusal it was — and it records `model.failed` first, so a turn
that died of quota is not indistinguishable from one that died of a crash.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterable, Mapping, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from google import genai
from google.genai import errors, types

from coros_core.trace import emit

MODEL = "gemini-3.6-flash"

RETRY_CODES = frozenset({429, 500, 503, 504})
BACKOFF = (1.0, 3.0, 7.0)

# Milliseconds — HttpOptions.timeout is "Timeout for the request in milliseconds". Without
# one a stalled request hangs the turn forever, which from the outside is indistinguishable
# from a crash.
TIMEOUT_MS = 120_000


class GeminiUnconfigured(RuntimeError):
    """No usable API key. Raised before a client exists, so nothing half-configured is
    handed out and no request leaves with a blank credential."""


def env_candidates(module: Path) -> tuple[Path, Path]:
    """The two roots `.env` is looked for in, given this module's own path.

    `parents[2]` is the repo root in a checkout; `parents[1]` is `/app` once the image
    flattens `packages/coros_core` to `/app/coros_core`, one directory shallower. A
    container is handed its environment by docker's `env_file` and ships no `.env` at all,
    so finding neither is not an error.
    """
    here = module.resolve()
    return (here.parents[2] / ".env", here.parents[1] / ".env")


def env_file(candidates: Iterable[Path] | None = None) -> Path | None:
    looked = env_candidates(Path(__file__)) if candidates is None else candidates
    return next((c for c in looked if c.is_file()), None)


def load_env(candidates: Iterable[Path] | None = None) -> Path | None:
    found = env_file(candidates)
    if found is not None:
        load_dotenv(found, override=False)
    return found


load_env()


def api_key() -> str:
    """The configured key, or `""`. Empty is a state a caller may want to render as a
    setup screen, so it is a value here and an exception one layer down in `client()`."""
    return os.environ.get("GEMINI_API_KEY", "").strip()


# The cache is not only about setup cost: a `genai.Client` nobody holds is garbage
# collected, and its `__del__` closes the httpx transport underneath it.
@lru_cache(maxsize=4)
def client(key: str) -> genai.Client:
    if not key:
        raise GeminiUnconfigured(
            "GEMINI_API_KEY is missing — put it in .env (see .env.example). The hosted "
            "apps read vault_decabot_gemini_api_key, the same credential as DecaBot."
        )
    return genai.Client(api_key=key, http_options=types.HttpOptions(timeout=TIMEOUT_MS))


def as_tools(tools: Iterable[Any]) -> list[Any]:
    """Collect bare `FunctionDeclaration`s into the one `types.Tool` the SDK requires.

    Everything else — an existing `Tool`, a dict, a plain callable the SDK declares for
    itself — passes through in place and in order.
    """
    passthrough: list[Any] = []
    declarations: list[types.FunctionDeclaration] = []
    for tool in tools:
        (declarations if isinstance(tool, types.FunctionDeclaration) else passthrough).append(tool)
    if declarations:
        passthrough.append(types.Tool(function_declarations=declarations))
    return passthrough


def _bare(tools: Any) -> bool:
    return isinstance(tools, Sequence) and any(isinstance(t, types.FunctionDeclaration) for t in tools)


def _normalized(config: Any) -> Any:
    """A config whose tools the SDK can read. Returns the original when it already is one:
    module-level configs are shared between turns, so this never edits its caller's."""
    if isinstance(config, types.GenerateContentConfig):
        if _bare(config.tools):
            return config.model_copy(update={"tools": as_tools(config.tools)})
    elif isinstance(config, Mapping) and _bare(config.get("tools")):
        return {**config, "tools": as_tools(config["tools"])}
    return config


async def generate(**kwargs: Any) -> types.GenerateContentResponse:
    """Issue one model request, retrying the four transient codes. `model` defaults to
    `MODEL`; `contents` and `config` are the only other parameters the SDK takes."""
    kwargs.setdefault("model", MODEL)
    if kwargs.get("config") is not None:
        kwargs["config"] = _normalized(kwargs["config"])

    # Held in a local for the whole ladder. `genai.Client.__del__` closes the transport,
    # and `client.aio.models` does not keep the client alive — so binding the models
    # object instead of the client makes the next attempt raise "Cannot send a request,
    # as the client has been closed." The lru_cache above is the other half of this.
    api = client(api_key())
    for attempt in range(len(BACKOFF) + 1):
        try:
            response = await api.aio.models.generate_content(**kwargs)
        except (errors.ServerError, errors.ClientError) as exc:
            code = getattr(exc, "code", None)
            if code not in RETRY_CODES or attempt == len(BACKOFF):
                emit(
                    "model.failed",
                    {"code": code, "attempts": attempt + 1, "status": getattr(exc, "status", None)},
                    level="error",
                )
                raise
            emit("model.retry", {"attempt": attempt + 1, "code": code}, level="error")
            await asyncio.sleep(BACKOFF[attempt])
        else:
            usage = response.usage_metadata
            emit(
                "model.call",
                {
                    "attempts": attempt + 1,
                    "prompt_tokens": getattr(usage, "prompt_token_count", None),
                    "candidates_tokens": getattr(usage, "candidates_token_count", None),
                    "total_tokens": getattr(usage, "total_token_count", None),
                },
            )
            return response
    raise AssertionError("unreachable: every attempt either returns or raises")
