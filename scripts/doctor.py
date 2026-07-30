"""Preflight for Brújula and Huella: is this checkout able to run at all?

    make doctor                                  # everything, including COROS
    ./.venv/bin/python scripts/doctor.py --no-coros    # leave coros.com.co alone
    ./.venv/bin/python scripts/doctor.py --offline     # nothing leaves this machine

    PYTHONPATH=.:packages:apps/brujula:apps/huella is required for a direct run; `make
    doctor` exports it. pytest is the only caller that gets its roots from pytest.ini.

Seven things, in the order they cost: the interpreter, the `.env` and its exposure, whether a
credential ever reached a commit, the live model list, the COROS storefront feed, COROS's UCP
endpoint, and Strava's token endpoint.

**A full run makes four network requests, one per endpoint, and nothing here retries.** That is
the whole design constraint. The single exception is not ours: `ucp.rpc` re-sends `tools/list` once if
COROS answers 429, because a trickle is served mid-lockout — that policy lives in `ucp.py`, and
the run reports the extra request when it happens rather than hiding it. The storefront's limiter
is measured to be *prolonged* by our own traffic — after one 429, thirty requests spaced 10 s
apart were all refused for five unbroken minutes, and it cleared only after ~100 s of complete
quiet (`AGENTS.md`, and `docs/DECISIONS.md`, 30 Jul, "What refuses us at the storefront is not
settled"). A preflight that polls is what breaks the thing it is checking, so
`catalog.MAX_ATTEMPTS` is pinned to 1 for the one request this makes.

**And a preflight must be able to decline to look at all**, which is what `--no-coros` and
`--offline` are for. The same entry says the experiment that would settle what refuses us needs
30+ minutes of complete quiet from this IP and then exactly ONE request as the first of the
window; a preflight request lands in the same bucket and destroys the result. So the COROS
checks are skippable *by name*, separately from connectivity, and a skip says which flag caused
it. Nothing is reported as passing that was not run.

**A refusal is not a result.** Following `docs/DECISIONS.md`, 30 Jul, "A verifier may excuse a
run only on evidence": every check here reports only what it measured. A 429 is `REFUSED —
could not check`, which is neither a failing feed nor a passing one; an absent prerequisite is
`SKIP`; and the exit code is non-zero only for a `FAIL`, which means something was measured and
was wrong.

**No credential is ever printed.** Values read out of the `.env` go into `_SECRETS`, and every
detail string this script prints is routed through `_safe()`. The "never committed" check hands
the value to `git log -S` as an argv element and prints only the variable's name and the commits
that matched.
"""

from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from dotenv import dotenv_values
from google.genai import errors

from coros_core import catalog, gemini, ucp
from huella.strava import client as strava

REPO = Path(__file__).resolve().parents[1]

MIN_PYTHON = (3, 12)

# 56 models came back in one page on 30 Jul 2026 with no continuation token, so this is one
# request rather than a pager walk. If a token ever appears, the check says the list was
# truncated instead of reporting an absent model.
MODEL_PAGE_SIZE = 1000

# The names whose values are treated as credentials: never printed, and searched for in history.
CREDENTIAL_SUFFIXES = ("_KEY", "_SECRET", "_PASSWORD", "_TOKEN")

# A pickaxe on a short string matches unrelated text in unrelated commits, so a short value is
# reported as unsearchable rather than as clean.
MIN_SEARCHABLE = 8

# Strava validates `client_id` before anything else, and this is the exact probe `AGENTS.md`
# records: a POST of `grant_type=authorization_code` with a bogus id answered 400 naming
# `client_id` invalid on both of Strava's token paths. It carries no credential of ours.
PROBE_CLIENT_ID = "0"

_OFFLINE = "--offline was given; no request left this machine"
_NO_COROS = (
    "--no-coros was given; coros.com.co was not contacted at all. A storefront-limiter "
    "measurement needs a long quiet window from this IP and then exactly one request"
)

_SECRETS: set[str] = set()
REQUESTS: list[str] = []
SKIPPED: list[str] = []
REFUSED: list[str] = []


def _safe(text: object) -> str:
    out = str(text)
    for secret in _SECRETS:
        out = out.replace(secret, "<redacted>")
    return out


def check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{f'  — {_safe(detail)}' if detail else ''}")
    return ok


def warn(label: str, detail: str = "") -> None:
    print(f"  [WARN] {label}{f'  — {_safe(detail)}' if detail else ''}")


def skip(label: str, detail: str = "") -> None:
    SKIPPED.append(label)
    print(f"  [SKIP] {label}{f'  — {_safe(detail)}' if detail else ''}")


def refused(label: str, detail: str = "") -> None:
    """Measured a refusal, so there is nothing to report about the thing itself."""
    REFUSED.append(label)
    print(f"  [REFUSED] {label} — could not check{f': {_safe(detail)}' if detail else ''}")


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(REPO), *args], capture_output=True, text=True, check=False
    )


def _toolchain() -> list[bool]:
    print("\ninterpreter …")
    version = sys.version_info
    return [
        check(
            f"python is {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer",
            version[:2] >= MIN_PYTHON,
            f"{version.major}.{version.minor}.{version.micro} at {sys.executable}",
        )
    ]


def _env_file() -> tuple[list[bool], Path | None]:
    print("\n.env …")
    looked = gemini.env_candidates(Path(gemini.__file__))
    found = gemini.env_file()
    if found is None:
        # Not an error: a container is handed its environment by docker's `env_file` and
        # ships no .env at all. It is only a problem for a checkout, which is what this is.
        skip("no .env in either root", " or ".join(str(path) for path in looked))
        return [], None

    results = [check("a .env is present", True, str(found))]
    try:
        relative = str(found.relative_to(REPO))
    except ValueError:
        skip("git cannot speak for this .env — it is outside the repo", str(found))
        return results, found

    results.append(
        check(
            f"{relative} is gitignored",
            _git("check-ignore", "-q", relative).returncode == 0,
            "`.env` and `.env.*` are in .gitignore, `.env.example` un-ignored",
        )
    )
    results.append(
        check(
            f"{relative} is untracked",
            _git("ls-files", "--error-unmatch", relative).returncode != 0,
            "gitignoring a file git already tracks changes nothing",
        )
    )
    return results, found


def _never_committed(env_path: Path) -> list[bool]:
    print("\ncredentials in history …")
    values = dotenv_values(env_path)
    named = {
        name: (value or "").strip()
        for name, value in values.items()
        if name.upper().endswith(CREDENTIAL_SUFFIXES)
    }
    if not named:
        skip("the .env names no credential", f"{len(values)} variable(s), none matching a credential name")
        return []

    results: list[bool] = []
    for name, value in sorted(named.items()):
        if not value:
            skip(f"{name} is unset", "nothing to look for")
            continue
        if len(value) < MIN_SEARCHABLE:
            skip(f"{name} is shorter than {MIN_SEARCHABLE} characters", "a pickaxe on it would match unrelated text")
            continue
        # -S takes its argument as a separate argv element, so the value is never in a shell
        # command line, never in an echoed command, and never in this script's output.
        found = _git("log", "--all", "--format=%h", "-S", value)
        if found.returncode != 0:
            results.append(
                check(f"history could be searched for {name}", False, _safe(found.stderr.strip()))
            )
            continue
        commits = found.stdout.split()
        results.append(
            check(
                f"{name}'s value appears in no commit, on any branch",
                not commits,
                f"{len(commits)} commit(s): {' '.join(commits[:8])}" if commits else "",
            )
        )
    return results


def _model(reach: bool) -> list[bool]:
    print("\nGemini …")
    if not reach:
        skip("the live model list", _OFFLINE)
        return []
    key = gemini.api_key()
    if not key:
        skip(
            "GEMINI_API_KEY is not set",
            "the live model list needs it; both apps read the one pooled credential",
        )
        return []

    try:
        REQUESTS.append("Gemini ListModels")
        pager = gemini.client(key).models.list(config={"page_size": MODEL_PAGE_SIZE})
    except errors.APIError as exc:
        if exc.code == 429:
            refused("the live model list", "HTTP 429 — one quota is shared by three deployments")
            return []
        return [check("the live model list could be read", False, f"HTTP {exc.code} {exc.status}")]
    except Exception as exc:  # transport, DNS, TLS: report it, do not diagnose it
        return [check("the live model list could be read", False, f"{type(exc).__name__}: {exc}"[:160])]

    names = [(model.name or "").split("/")[-1] for model in pager.page]
    present = gemini.MODEL in names
    if not present and (getattr(pager, "_config", None) or {}).get("page_token"):
        warn(
            f"{gemini.MODEL} is not in the first page and the list is paginated",
            f"{len(names)} models read, more remain — absent from a partial list is not absent",
        )
        return []
    return [
        check(
            f"{gemini.MODEL} is in the live model list",
            present,
            f"{len(names)} models under this credential",
        )
    ]


async def _storefront(reach: bool, why: str) -> list[bool]:
    print("\nCOROS storefront …")
    if not reach:
        skip("the storefront feed was not read", why)
        return []
    # ONE request, whatever happens. The module's ladder retries a ConnectTimeout or a 5xx up
    # to four times, which is right for a turn a person is waiting on and wrong here: mid-
    # lockout the storefront drops the connection outright, so those retries are three more
    # requests into a limiter our own traffic is holding shut.
    catalog.MAX_ATTEMPTS = 1
    try:
        REQUESTS.append("COROS products.json")
        response = await catalog._get(catalog.PRODUCTS_URL, "products")
    except catalog.CatalogUnavailable as exc:
        if exc.rate_limited:
            refused("the storefront feed", f"HTTP 429, Retry-After: {exc.retry_after}. Not retried, not polled")
            return []
        return [check("the storefront feed answered", False, exc.detail)]
    return [
        check(
            "the storefront feed answered 200",
            response.status_code == 200,
            f"HTTP {response.status_code}, {len(response.content)} bytes",
        )
    ]


async def _ucp(reach: bool, why: str) -> list[bool]:
    print("\nCOROS UCP …")
    if not reach:
        skip("UCP tools/list was not called", why)
        return []
    try:
        REQUESTS.append("COROS UCP tools/list")
        result: dict[str, Any] = await ucp.rpc("tools/list")
    except ucp.UcpRateLimited as exc:
        # rpc() re-sends once on a 429 by its own policy — a trickle is served mid-lockout —
        # so a refusal here cost two requests, not one. That policy lives in ucp.py.
        REQUESTS.append("COROS UCP tools/list (ucp.rpc's own re-send after 429)")
        refused("UCP tools/list", f"HTTP 429, Retry-After: {exc.retry_after}")
        return []
    except ucp.UcpToolError as exc:
        return [check("UCP tools/list answered", False, f"code={exc.code}: {exc}"[:200])]
    except Exception as exc:
        return [check("UCP tools/list answered", False, f"{type(exc).__name__}: {exc}"[:160])]
    finally:
        await ucp.aclose()

    if ucp.is_paced():
        REQUESTS.append("COROS UCP tools/list (ucp.rpc's own re-send after 429)")
        warn("UCP answered 429 once and served the re-send", "the process is now latched to spaced calls")
    tools = result.get("tools") or []
    return [check("UCP tools/list answered with a tool list", bool(tools), f"{len(tools)} tools")]


async def _strava(reach: bool) -> list[bool]:
    print("\nStrava …")
    results: list[bool] = []
    if not strava.client_id():
        skip(
            "STRAVA_CLIENT_ID is not set",
            "Standard Tier now needs a paid subscription and a registered app — a blocker on a "
            "person, not on code. Huella runs without it; its live tests skip.",
        )
    else:
        results.append(
            check(
                "the Strava app is fully configured",
                strava.is_configured(),
                "client id, client secret and redirect uri",
            )
        )

    if not reach:
        skip("the token endpoint was not probed", _OFFLINE)
        return results

    # Needs no credential of ours, so it runs whether or not an app is registered: this is the
    # measurement AGENTS.md records, and it is the reason `client.py` keeps the `/oauth/token`
    # spelling that looks wrong against Strava's own docs. Do not "correct" the URL — read the
    # registry entry first.
    try:
        REQUESTS.append("Strava token endpoint")
        response = await strava.client().post(
            strava.TOKEN_URL,
            data={"client_id": PROBE_CLIENT_ID, "grant_type": "authorization_code"},
        )
    except Exception as exc:
        results.append(
            check("the token endpoint is reachable", False, f"{type(exc).__name__}: {exc}"[:160])
        )
        return results
    finally:
        await strava.aclose()

    if response.status_code == 429:
        refused("the token endpoint", "HTTP 429")
        return results

    faults = []
    try:
        body = response.json()
    except ValueError:
        body = {}
    if isinstance(body, dict):
        faults = [error for error in (body.get("errors") or []) if isinstance(error, dict)]
    rejected_the_bogus_id = response.status_code == 400 and any(
        error.get("field") == "client_id" and error.get("code") == "invalid" for error in faults
    )
    results.append(
        check(
            "the token endpoint is reachable and processing OAuth",
            rejected_the_bogus_id,
            f"HTTP {response.status_code}, {response.text[:120]!r}",
        )
    )
    return results


def _parse(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="doctor.py",
        description="Preflight for Brújula and Huella. Reports only what it measured.",
    )
    parser.add_argument(
        "--offline",
        "--no-network",
        action="store_true",
        help=(
            "Skip every check that leaves this machine — the model list, both COROS endpoints "
            "and Strava — and report each as SKIP rather than as a failure. For CI, which holds "
            "no credential and has no business calling any of them, and for a checkout with no "
            "connectivity: an absent network is not a broken checkout."
        ),
    )
    parser.add_argument(
        "--no-coros",
        action="store_true",
        help=(
            "Skip the two coros.com.co checks only, leaving Gemini and Strava to run. The "
            "storefront's limiter is under measurement and the experiment that would settle "
            "what refuses us needs a long quiet window from this IP and then exactly ONE "
            "request as the first of it (docs/DECISIONS.md, 30 Jul, 'What refuses us at the "
            "storefront is not settled'). A preflight request lands in the same bucket and "
            "destroys the result, so this is how you run the rest without spending it."
        ),
    )
    return parser.parse_args(argv)


async def main(argv: Sequence[str] | None = None) -> int:
    args = _parse(argv)
    reach_coros = not (args.offline or args.no_coros)
    coros_why = _OFFLINE if args.offline else _NO_COROS

    print("preflight — Brújula and Huella")
    results = _toolchain()

    env_results, env_path = _env_file()
    results += env_results
    if env_path is not None:
        _SECRETS.update(
            value.strip()
            for name, value in dotenv_values(env_path).items()
            if value and value.strip() and name.upper().endswith(CREDENTIAL_SUFFIXES)
        )
        results += _never_committed(env_path)
    else:
        skip("credentials in history", "no .env to read a value out of")

    results += _model(not args.offline)
    results += await _storefront(reach_coros, coros_why)
    results += await _ucp(reach_coros, coros_why)
    results += await _strava(not args.offline)

    print(
        f"\n{sum(results)}/{len(results)} checks passed"
        f", {len(SKIPPED)} skipped, {len(REFUSED)} refused"
    )
    print(f"{len(REQUESTS)} network request(s): {'; '.join(REQUESTS) or 'none'}")
    if REFUSED:
        print("A refusal is not a failure of what it refused: " + "; ".join(REFUSED))
    # A skipped check is not a passing one. The exit code answers "was anything measured and
    # found wrong", which is the only question a preflight can answer honestly.
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
