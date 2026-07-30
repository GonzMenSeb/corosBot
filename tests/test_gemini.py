"""The one door to the model.

Three properties are under test, and each one exists because of a failure that is
invisible until it is live:

  * **One call site.** Two apps share a single per-project quota with DecaBot, so a
    second place that issues a request brings its own retry policy and its own share of
    that quota. An AST scan is what keeps the door single.
  * **A transient refusal is a pause.** `gemini-3.6-flash` answers 503 intermittently and
    a shared quota makes 429 ordinary, so the retry ladder is the difference between a
    slow turn and a dead one.
  * **A bare `FunctionDeclaration` never reaches the wire.** The SDK raises
    `AttributeError` *inside itself* before any HTTP call, which reads as a bug in our
    code rather than a shape error. Pinned as a fact in `tests/test_contracts.py`;
    normalised here so no call site can reproduce it.
"""

from __future__ import annotations

import ast
import asyncio
import gc
import os
from pathlib import Path
from typing import Any

import pytest
from google.genai import errors, types

from coros_core import gemini, trace

FACTS = 'AGENTS.md "load-bearing facts", the Gemini section'
REPO = Path(__file__).resolve().parent.parent
ROOTS = (REPO / "packages", REPO / "apps", REPO / "scripts")
THE_DOOR = REPO / "packages" / "coros_core" / "gemini.py"

_ISSUES_A_REQUEST = {"generate_content", "generate_content_stream"}


def _docstrings(tree: ast.Module) -> set[int]:
    """Ids of every docstring constant, so prose may name what code may not."""
    found: set[int] = set()
    holders = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    for node in ast.walk(tree):
        if not isinstance(node, holders) or not node.body:
            continue
        first = node.body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
            found.add(id(first.value))
    return found


def _sources() -> list[tuple[Path, ast.Module]]:
    out = []
    for root in ROOTS:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.py")):
            if path == THE_DOOR:
                continue
            out.append((path, ast.parse(path.read_text())))
    return out


class _Recorder:
    """Stands in for `genai.Client`. Records what reached the SDK and replays one
    scripted outcome per attempt, so the retry ladder is testable without a key."""

    def __init__(self, *outcomes: Any) -> None:
        self._outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []
        self.aio = self

    @property
    def models(self) -> _Recorder:
        return self

    async def generate_content(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        outcome = self._outcomes.pop(0) if self._outcomes else types.GenerateContentResponse()
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


@pytest.fixture(autouse=True)
def _a_key_that_is_not_real(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every offline test runs with a key present and a fake client, so a machine with a
    real `.env` and one without behave identically."""
    monkeypatch.setenv("GEMINI_API_KEY", "offline-test-key")


def _bind(monkeypatch: pytest.MonkeyPatch, *outcomes: Any) -> _Recorder:
    recorder = _Recorder(*outcomes)
    monkeypatch.setattr(gemini, "client", lambda _key: recorder)
    return recorder


def _slept(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Records the backoff instead of serving it. Patched on the asyncio module itself:
    the only thing awaiting a sleep inside these tests is the retry ladder."""
    delays: list[float] = []

    async def record(seconds: float) -> None:
        delays.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", record)
    return delays


def _refusal(code: int) -> errors.APIError:
    body = {"error": {"code": code, "message": "the model is experiencing high demand", "status": "UNAVAILABLE"}}
    return errors.ServerError(code, body) if code >= 500 else errors.ClientError(code, body)


class TestThisIsTheOnlyDoorToTheModel:
    def test_no_other_module_issues_a_model_request(self) -> None:
        offenders = [
            f"{path.relative_to(REPO)}:{node.lineno}: .{node.func.attr}()"
            for path, tree in _sources()
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in _ISSUES_A_REQUEST
        ]
        assert not offenders, (
            "a Gemini request is issued outside coros_core.gemini:\n  "
            + "\n  ".join(offenders)
            + "\nGo through `gemini.generate()` instead. A second call site has its own retry\n"
            "policy and its own share of one quota that three deployments already share."
        )

    def test_no_other_module_builds_its_own_client(self) -> None:
        offenders = [
            f"{path.relative_to(REPO)}:{node.lineno}"
            for path, tree in _sources()
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and (
                (isinstance(node.func, ast.Attribute) and node.func.attr == "Client")
                or (isinstance(node.func, ast.Name) and node.func.id == "Client")
            )
        ]
        assert not offenders, (
            "a genai.Client is constructed outside coros_core.gemini:\n  "
            + "\n  ".join(offenders)
            + f"\nUse `gemini.client()`, which is the only thing that sets the {gemini.TIMEOUT_MS}ms\n"
            "timeout. A client without one hangs the turn forever, which looks like a crash."
        )

    def test_no_other_module_spells_the_model_name_in_code(self) -> None:
        offenders = []
        for path, tree in _sources():
            prose = _docstrings(tree)
            offenders += [
                f"{path.relative_to(REPO)}:{node.lineno}"
                for node in ast.walk(tree)
                if isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and gemini.MODEL in node.value
                and id(node) not in prose
            ]
        assert not offenders, (
            f"{gemini.MODEL!r} is spelled outside coros_core.gemini:\n  "
            + "\n  ".join(offenders)
            + "\nRead `gemini.MODEL`. A model name copied into a second file is a model name\n"
            "half the repo will still be using after the other half is upgraded."
        )


class TestAKeyThatIsNotThereIsSaidOutLoud:
    def test_an_absent_key_reads_as_absent_rather_than_as_a_blank_credential(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)

        assert gemini.api_key() == ""

    def test_whitespace_is_not_a_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GEMINI_API_KEY", "  \n ")

        assert gemini.api_key() == ""

    def test_no_client_is_handed_out_without_one(self) -> None:
        with pytest.raises(gemini.GeminiUnconfigured):
            gemini.client("")

    async def test_generate_never_reaches_the_network_without_one(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)

        with pytest.raises(gemini.GeminiUnconfigured) as caught:
            await gemini.generate(contents="hola")

        assert ".env" in str(caught.value), (
            "the missing-key error does not say where to put the key.\n"
            "  It is the first thing a fresh checkout hits; name .env and .env.example."
        )


class TestTheClientIsBuiltOncePerKey:
    def test_the_same_key_hands_back_the_same_client(self) -> None:
        assert gemini.client("key-a") is gemini.client("key-a")

    def test_a_different_key_gets_its_own_client(self) -> None:
        assert gemini.client("key-a") is not gemini.client("key-b")

    def test_the_cache_is_what_keeps_the_transport_open(self) -> None:
        """`genai.Client.__del__` closes the httpx transport, and `client.aio.models` does
        not keep the client alive. Something has to hold the wrapper; the cache does."""
        key = "key-that-nobody-holds"
        gemini.client(key)
        gc.collect()

        state = gemini.client(key)._api_client._httpx_client._state

        assert state.name != "CLOSED", (
            "the cached client's transport was closed underneath it.\n"
            "  The next request raises RuntimeError('Cannot send a request, as the client\n"
            f"  has been closed.') rather than anything a caller can read. See {FACTS}."
        )

    def test_the_request_timeout_is_two_minutes_of_milliseconds(self) -> None:
        assert gemini.TIMEOUT_MS == 120_000
        assert gemini.client("key-a")._api_client._http_options.timeout == 120_000, (
            "the client was built without the 120s timeout.\n"
            "  HttpOptions.timeout is documented in milliseconds; a stalled request with no\n"
            f"  timeout hangs the turn forever. See {FACTS}."
        )


class TestATransientRefusalIsAPauseNotADeadEnd:
    def test_the_retryable_codes_are_the_four_transient_ones(self) -> None:
        assert gemini.RETRY_CODES == frozenset({429, 500, 503, 504}), (
            "the retryable set changed.\n"
            f"  429 is ordinary here — one quota is shared by three deployments. See {FACTS}."
        )

    def test_the_ladder_is_three_delays_growing(self) -> None:
        assert gemini.BACKOFF == (1.0, 3.0, 7.0)

    @pytest.mark.parametrize("code", sorted({429, 500, 503, 504}))
    async def test_a_transient_code_is_retried_and_the_answer_still_arrives(
        self, code: int, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        answer = types.GenerateContentResponse()
        recorder = _bind(monkeypatch, _refusal(code), answer)
        _slept(monkeypatch)

        assert await gemini.generate(contents="hola") is answer
        assert len(recorder.calls) == 2

    async def test_the_backoff_is_consumed_in_order_one_delay_per_retry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(gemini, "BACKOFF", (0.5, 1.5, 3.5))
        _bind(monkeypatch, _refusal(503), _refusal(503), _refusal(429), types.GenerateContentResponse())
        delays = _slept(monkeypatch)

        await gemini.generate(contents="hola")

        assert delays == [0.5, 1.5, 3.5], (
            f"the ladder slept {delays} instead of one delay per retry, in order.\n"
            "  An off-by-one here either skips the first pause or repeats the last."
        )

    async def test_the_fourth_refusal_is_the_end_and_it_is_raised(self, monkeypatch: pytest.MonkeyPatch) -> None:
        recorder = _bind(monkeypatch, *[_refusal(503)] * 5)
        delays = _slept(monkeypatch)

        with pytest.raises(errors.ServerError) as caught:
            await gemini.generate(contents="hola")

        assert caught.value.code == 503, "the caller lost the status code it branches on"
        assert len(recorder.calls) == 4, "one attempt plus one per rung of the ladder, no more"
        assert len(delays) == 3, "the last failure must not be followed by a pointless pause"

    async def test_a_code_that_will_not_improve_is_not_retried(self, monkeypatch: pytest.MonkeyPatch) -> None:
        recorder = _bind(monkeypatch, _refusal(400))
        delays = _slept(monkeypatch)

        with pytest.raises(errors.ClientError):
            await gemini.generate(contents="hola")

        assert len(recorder.calls) == 1, "a malformed request was sent four times"
        assert delays == []

    async def test_every_retry_is_visible_in_the_trace(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _bind(monkeypatch, _refusal(429), _refusal(503), types.GenerateContentResponse())
        _slept(monkeypatch)

        await gemini.generate(contents="hola")

        retries = [e.payload for e in trace.events() if e.event == "model.retry"]
        assert retries == [{"attempt": 1, "code": 429}, {"attempt": 2, "code": 503}], (
            f"the retries were not recorded: {retries}.\n"
            "  A turn that took nine seconds and looked idle is what this event explains."
        )
        assert all(e.level == "error" for e in trace.events() if e.event == "model.retry")

    async def test_giving_up_is_recorded_and_not_only_raised(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _bind(monkeypatch, *[_refusal(504)] * 4)
        _slept(monkeypatch)

        with pytest.raises(errors.ServerError):
            await gemini.generate(contents="hola")

        failed = [e for e in trace.events() if e.event == "model.failed"]
        assert [e.payload["code"] for e in failed] == [504]
        assert failed[0].payload["attempts"] == 4
        assert failed[0].level == "error"

    async def test_a_non_retryable_failure_is_recorded_too(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _bind(monkeypatch, _refusal(400))

        with pytest.raises(errors.ClientError):
            await gemini.generate(contents="hola")

        failed = [e.payload for e in trace.events() if e.event == "model.failed"]
        assert failed == [{"code": 400, "attempts": 1, "status": "UNAVAILABLE"}]

    async def test_a_successful_call_carries_its_token_counts_and_no_text(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        usage = types.GenerateContentResponseUsageMetadata(
            prompt_token_count=11, candidates_token_count=22, total_token_count=33
        )
        _bind(monkeypatch, types.GenerateContentResponse(usage_metadata=usage))

        await gemini.generate(contents="una respuesta con texto sensible")

        calls = [e.payload for e in trace.events() if e.event == "model.call"]
        assert calls == [{"attempts": 1, "prompt_tokens": 11, "candidates_tokens": 22, "total_tokens": 33}], (
            f"the call was recorded as {calls}.\n"
            "  One shared quota is only observable if every call counts its own tokens, and\n"
            "  an evidence bundle is pasted into a model — so counts, never the text."
        )

    async def test_an_unreported_token_count_is_recorded_as_unreported(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _bind(monkeypatch, types.GenerateContentResponse())

        await gemini.generate(contents="hola")

        payload = [e.payload for e in trace.events() if e.event == "model.call"][0]
        assert payload == {"attempts": 1, "prompt_tokens": None, "candidates_tokens": None, "total_tokens": None}


class TestABareFunctionDeclarationNeverReachesTheWire:
    def _declaration(self, name: str = "list_collections") -> types.FunctionDeclaration:
        return types.FunctionDeclaration(name=name, description="a live COROS collection")

    async def test_a_bare_declaration_is_wrapped_in_the_one_tool_the_sdk_requires(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _bind(monkeypatch)
        declaration = self._declaration()

        await gemini.generate(contents="hola", config=types.GenerateContentConfig(tools=[declaration]))

        sent = recorder.calls[0]["config"].tools
        assert [type(t).__name__ for t in sent] == ["Tool"], (
            f"a bare FunctionDeclaration reached the SDK: {sent}.\n"
            "  It raises AttributeError inside google-genai before any HTTP call, which reads\n"
            f"  as our bug. See {FACTS}."
        )
        assert sent[0].function_declarations == [declaration]

    async def test_a_dict_config_is_normalised_too(self, monkeypatch: pytest.MonkeyPatch) -> None:
        recorder = _bind(monkeypatch)

        await gemini.generate(contents="hola", config={"temperature": 0.0, "tools": [self._declaration()]})

        sent = recorder.calls[0]["config"]
        assert [type(t).__name__ for t in sent["tools"]] == ["Tool"]
        assert sent["temperature"] == 0.0, "normalising the tools dropped the rest of the config"

    async def test_the_callers_config_is_left_as_it_was(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Module-level configs are shared between turns, so a normaliser that edits its
        caller's object is a second writer to something everyone reads."""
        recorder = _bind(monkeypatch)
        config = types.GenerateContentConfig(tools=[self._declaration()])

        await gemini.generate(contents="hola", config=config)

        assert [type(t).__name__ for t in config.tools] == ["FunctionDeclaration"]
        assert recorder.calls[0]["config"] is not config

    async def test_an_already_wrapped_tool_travels_untouched(self, monkeypatch: pytest.MonkeyPatch) -> None:
        recorder = _bind(monkeypatch)
        tool = types.Tool(function_declarations=[self._declaration()])
        config = types.GenerateContentConfig(tools=[tool])

        await gemini.generate(contents="hola", config=config)

        assert recorder.calls[0]["config"] is config, "a config that needed nothing was copied anyway"

    def test_a_plain_callable_is_left_for_the_sdk_to_declare(self) -> None:
        def lookup_device_compat(handle: str) -> str:
            """The SDK turns a callable into a declaration itself."""
            return handle

        assert gemini.as_tools([lookup_device_compat]) == [lookup_device_compat]

    def test_a_mixed_list_keeps_both_kinds(self) -> None:
        tool = types.Tool(google_search=types.GoogleSearch())
        declaration = self._declaration()

        out = gemini.as_tools([tool, declaration])

        assert out[0] is tool
        assert out[1].function_declarations == [declaration]

    def test_several_declarations_become_one_tool(self) -> None:
        first, second = self._declaration("a"), self._declaration("b")

        out = gemini.as_tools([first, second])

        assert len(out) == 1
        assert out[0].function_declarations == [first, second]

    def test_no_tools_stays_no_tools(self) -> None:
        assert gemini.as_tools([]) == []

    async def test_a_config_without_tools_is_passed_straight_through(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _bind(monkeypatch)
        config = types.GenerateContentConfig(response_mime_type="application/json")

        await gemini.generate(contents="hola", config=config)

        assert recorder.calls[0]["config"] is config


class TestTheModelIsNamedInOnePlace:
    async def test_a_call_that_names_no_model_gets_the_pinned_one(self, monkeypatch: pytest.MonkeyPatch) -> None:
        recorder = _bind(monkeypatch)

        await gemini.generate(contents="hola")

        assert recorder.calls[0]["model"] == gemini.MODEL

    async def test_a_caller_may_still_override_it(self, monkeypatch: pytest.MonkeyPatch) -> None:
        recorder = _bind(monkeypatch)

        await gemini.generate(model="something-else", contents="hola")

        assert recorder.calls[0]["model"] == "something-else"


class TestTheEnvFileIsFoundByAnAbsolutePath:
    def test_the_repo_root_is_the_first_place_looked(self) -> None:
        assert gemini.env_candidates(REPO / "packages" / "coros_core" / "gemini.py")[0] == REPO / ".env"

    def test_the_flattened_image_layout_is_covered_too(self) -> None:
        """The Dockerfiles copy `packages/coros_core` to `/app/coros_core`, one directory
        shallower, so a single hardcoded `parents[2]` would look at `/.env` and find
        nothing. A container is handed its environment by docker's `env_file` and ships no
        `.env` at all, so finding neither candidate is not an error."""
        assert Path("/app/.env") in gemini.env_candidates(Path("/app/coros_core/gemini.py"))

    def test_a_missing_env_file_is_not_an_error(self, tmp_path: Path) -> None:
        nowhere = tmp_path / "app" / "coros_core" / "gemini.py"

        assert gemini.env_file(gemini.env_candidates(nowhere)) is None
        assert gemini.load_env(gemini.env_candidates(nowhere)) is None

    def test_the_file_that_is_there_is_the_one_loaded(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        root = tmp_path / "repo"
        (root / "packages" / "coros_core").mkdir(parents=True)
        (root / ".env").write_text("COROS_ENV_PROBE=from-the-repo-root\n")
        monkeypatch.delenv("COROS_ENV_PROBE", raising=False)

        loaded = gemini.load_env(gemini.env_candidates(root / "packages" / "coros_core" / "gemini.py"))

        assert loaded == root / ".env"
        assert os.environ["COROS_ENV_PROBE"] == "from-the-repo-root"


@pytest.mark.live
class TestTheModelNameStillAnswers:
    def test_the_pinned_model_is_in_the_live_model_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        gemini.load_env()
        key = gemini.api_key()
        if not key:
            pytest.skip("GEMINI_API_KEY is not configured here")

        names = {m.name for m in gemini.client(key).models.list()}

        assert f"models/{gemini.MODEL}" in names, (
            f"{gemini.MODEL} is gone from the live model list.\n"
            f"  Both apps and DecaBot name it. Update gemini.MODEL and {FACTS} together."
        )

    async def test_one_real_request_answers(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        gemini.load_env()
        if not gemini.api_key():
            pytest.skip("GEMINI_API_KEY is not configured here")

        response = await gemini.generate(
            contents="Responde solo con la palabra: listo",
            config=types.GenerateContentConfig(temperature=0.0, max_output_tokens=2000),
        )

        assert (response.text or "").strip(), "the live model returned an empty body"
        assert [e.payload["total_tokens"] for e in trace.events() if e.event == "model.call"][0]
