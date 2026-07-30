"""`scripts/eval_baseline.py` is the only thing in this repo that scores the pipeline
against an alternative, so a bug in its JUDGEMENT is a bug nothing else catches — and
until these tests existed nothing ran it at all. It was in neither the Makefile nor
`.github/`, which made "CI-runnable" a capability and not a fact.

What that cost, measured by breaking the code and watching the harness stay at exit 0:

  * `del loop._blocked; del loop._unread` — green. The harness re-implemented both inline
    and its copy of `_blocked` never emitted `guardrail.evidence_blocked`, so anything
    reading that event was unmeasured.
  * `del loop._names` — green. No item reached `not_sold_locally`, which is the advice
    kind behind the metric `docs/EVAL.md` §4 calls Brújula's differentiator.
  * `Item.claimed_unavailable` on one item — flipped `local_availability` from AGENT to
    tie, without touching a fixture, a check or a threshold, while the harness's own
    docstring said no field in `Item` could pre-write a result.

So these pin the judgement, not the plumbing. The six aggregate metrics, the like-for-like
control, the kind every item lands on, and the equal-budget invariant are all asserted
against a live offline run, and every number in `docs/EVAL.md` §3 and §5 is asserted
against that same run — a stale table in the doc fails the suite. The mutation tests
delete each `loop` private the harness claims to call and require the harness to die.

Offline like the harness: the fixture is `fixtures/products.json`, the model is replayed,
and `loop._model` and `loop.read_snapshot` are wired to explode in one test to prove it.
"""

from __future__ import annotations

import ast
import asyncio
import dataclasses
import importlib.util
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from brujula.agent import loop
from coros_core import trace

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "eval_baseline.py"
EVAL_MD = REPO / "docs" / "EVAL.md"
MAKEFILE = REPO / "Makefile"


def _load() -> Any:
    """A FRESH copy of the harness per call.

    By path, like `tests/test_verify_brujula.py`: `scripts/` is not a package and must not
    become one. Fresh per call because half of these tests mutate `ITEMS`, `METRICS` or an
    arm, and a shared module would hand the mutation to the next test.

    The `sys.modules` round-trip is not optional — `@dataclass` resolves `Ledger.__add__`'s
    string annotation through `sys.modules[cls.__module__]` — and the name is popped again
    so a mutated copy is never importable.
    """
    spec = importlib.util.spec_from_file_location("eval_baseline", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


async def _score_all(module: Any) -> SimpleNamespace:
    products = module.load_products()
    runs = [await module.run_item(item, products) for item in module.ITEMS]
    return SimpleNamespace(module=module, runs=runs, json=module._as_json(runs))


@pytest.fixture(scope="module")
def harness() -> Any:
    return _load()


@pytest.fixture(scope="module")
def result(harness: Any) -> SimpleNamespace:
    """One offline run of all 13 items, shared by every test that only reads it."""
    return asyncio.run(_score_all(harness))


# The recorded judgement. These are `docs/EVAL.md` §5's two tables, and the tests below
# assert the doc against the same run, so the three cannot drift apart in pairs.
AGGREGATE = {
    "provenance": ("3/12", "0/6", "AGENT"),
    "stock": ("3/12", "0/6", "AGENT"),
    "budget": ("2/14", "0/7", "AGENT"),
    "local_availability": ("1/3", "0/3", "AGENT"),
    "buy_nothing": ("3/3", "0/3", "AGENT"),
    "prose": ("5/13", "0/13", "AGENT"),
}

CONTROL = {
    "provenance": ("1/7", "0/6", "AGENT"),
    "stock": ("0/7", "0/6", "tie"),
    "budget": ("0/8", "0/7", "tie"),
    "local_availability": ("1/2", "0/2", "AGENT"),
    "buy_nothing": ("—", "—", "not exercised"),
    "prose": ("4/6", "0/6", "AGENT"),
}

# (baseline kind, agent kind, agent bundle accepted). The agent column is the refusal
# taxonomy: change a check's order or a template's branch and an item moves here first.
KINDS = {
    "clean-strap": ("recommend", "recommend", True),
    "clean-watch-in-budget": ("recommend", "recommend", True),
    "ambiguous-case-apex-4": ("recommend", "buy_nothing", True),
    "unretrieved-product": ("recommend", "buy_nothing", True),
    "out-of-stock-dura": ("recommend", "buy_nothing", True),
    "out-of-stock-cadence": ("recommend", "buy_nothing", True),
    "gift-with-purchase": ("recommend", "recommend", True),
    "absent-vertix-2": ("recommend", "recommend", True),
    "over-budget-apex-4": ("recommend", "insufficient_evidence", False),
    "storefront-rate-limited": ("buy_nothing", "insufficient_evidence", True),
    "invented-specs": ("recommend", "recommend", True),
    "absent-pace-3-with-strap": ("recommend", "recommend", True),
    "absent-pace-pro": ("buy_nothing", "not_sold_locally", True),
}

SPENT = {"catalog_reads": 13, "tool_calls": 16, "model_calls": 0, "replayed_model_stages": 13}


class TestTheTwoArmsSpendTheSame:
    """The claim the whole evaluation stands on. An agent that wins by spending more has
    not won, so the invariant has to be a measurement and not an assertion — which means
    something has to prove the comparison still fails when an arm overspends."""

    def test_every_item_spends_identically(self, result: SimpleNamespace) -> None:
        unequal = [
            (r.item.id, r.baseline_ledger.spent(), r.agent_ledger.spent())
            for r in result.runs
            if not r.equal_budget
        ]
        assert not unequal, (
            f"the arms diverged on {unequal}. Nothing the harness prints below the ledger "
            "is a comparison once this is false: the guarantees are supposed to be free "
            "because guardrails.py is pure, and this is the only thing that shows it."
        )

    def test_the_totals_are_the_ones_recorded(self, result: SimpleNamespace) -> None:
        assert result.json["budget"] == {"baseline": SPENT, "agent": SPENT}, (
            f"the ledger totals are {result.json['budget']}, not {SPENT} per arm. If an "
            "item was added or removed, re-record docs/EVAL.md §3 and this constant "
            "together; if it was not, something started spending."
        )

    def test_the_model_is_replayed_on_both_arms_and_never_called(
        self, result: SimpleNamespace
    ) -> None:
        for arm in ("baseline", "agent"):
            spent = result.json["budget"][arm]
            assert spent["model_calls"] == 0 and spent["replayed_model_stages"] == len(
                result.runs
            ), (
                f"the {arm} arm spent {spent}. Both arms must replay the identical proposal "
                "exactly once per item: a replay counter below the item count means a stage "
                "was skipped, and a model call means the arms are not being held fixed."
            )

    async def test_an_arm_that_takes_one_extra_tool_call_is_caught(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        module = _load()
        real = module._retrieve

        async def greedy(item: Any, products: Any, ledger: Any) -> Any:
            out = await real(item, products, ledger)
            if ledger.arm == "baseline":
                ledger.call_tool()
            return out

        module._retrieve = greedy
        code = await module.main([])
        out = capsys.readouterr().out
        assert code == 1, (
            "the baseline spent one tool call per item more than the agent and the harness "
            f"still exited {code}. The equal-budget check is decorative if it cannot fail."
        )
        assert "[FAIL] the arms did not spend the same" in out, (
            "the run overspent and the report does not say so. Whoever reads the metric "
            "tables above that line has to be told they are not a comparison."
        )

    async def test_a_ledger_divergence_also_fails_the_json_output(self) -> None:
        """CI reads `--json`, and that path returns before the report is ever printed."""
        module = _load()
        real = module._retrieve

        async def greedy(item: Any, products: Any, ledger: Any) -> Any:
            out = await real(item, products, ledger)
            if ledger.arm == "agent":
                ledger.call_tool()
            return out

        module._retrieve = greedy
        assert await module.main(["--json"]) == 1, (
            "--json exited 0 with unequal ledgers. The machine-readable path is the one CI "
            "diffs run over run, so it cannot be the forgiving one."
        )


class TestTheJudgementIsPinned:
    """A silent change in what the harness scores is the failure this file exists for."""

    @pytest.mark.parametrize("metric", sorted(AGGREGATE))
    def test_the_aggregate_metric_is_unchanged(
        self, result: SimpleNamespace, metric: str
    ) -> None:
        totals = {
            arm: result.module._totals(result.runs, arm) for arm in ("baseline", "agent")
        }
        actual = (
            totals["baseline"][metric].cell(),
            totals["agent"][metric].cell(),
            result.json["verdicts"][metric],
        )
        assert actual == AGGREGATE[metric], (
            f"{metric} scored {actual}, recorded {AGGREGATE[metric]}. This is defects/"
            "opportunities per arm and the winner. A change here is a change in what the "
            "harness believes; re-record docs/EVAL.md §5 in the same commit or find out why."
        )

    @pytest.mark.parametrize("metric", sorted(CONTROL))
    def test_the_like_for_like_control_is_unchanged(
        self, result: SimpleNamespace, metric: str
    ) -> None:
        subset = result.module._like_for_like(result.runs)
        totals = {arm: result.module._totals(subset, arm) for arm in ("baseline", "agent")}
        actual = (
            totals["baseline"][metric].cell(),
            totals["agent"][metric].cell(),
            result.json["like_for_like"]["verdicts"][metric],
        )
        assert actual == CONTROL[metric], (
            f"the control subset scored {metric} {actual}, recorded {CONTROL[metric]}. This "
            "is the table that matters: nobody abstained on these items, so a win here "
            "cannot be abstention. Wins shrinking toward the headline's means the agent is "
            "refusing more, which is not the same thing as being right more."
        )

    def test_the_control_subset_is_the_same_six_items(self, result: SimpleNamespace) -> None:
        subset = [r.item.id for r in result.module._like_for_like(result.runs)]
        expected = [
            "clean-strap",
            "clean-watch-in-budget",
            "gift-with-purchase",
            "absent-vertix-2",
            "invented-specs",
            "absent-pace-3-with-strap",
        ]
        assert subset == expected, (
            f"the both-arms-answered subset is {subset}, not {expected}. The control table "
            "is only a control while the membership is known: an item dropping out of it "
            "moves its defects into the headline, where abstention can hide them."
        )

    @pytest.mark.parametrize("item_id", sorted(KINDS))
    def test_each_item_lands_on_the_recorded_kind(
        self, result: SimpleNamespace, item_id: str
    ) -> None:
        run = next(r for r in result.runs if r.item.id == item_id)
        actual = (run.baseline.kind, run.agent.kind, run.agent.accepted)
        assert actual == KINDS[item_id], (
            f"{item_id} rendered {actual}, recorded {KINDS[item_id]} — (baseline kind, agent "
            "kind, agent bundle accepted). The metrics score a refusal as clean, so the kind "
            "is the only place a changed refusal path shows up as a number."
        )

    def test_the_answered_counters_are_unchanged(self, result: SimpleNamespace) -> None:
        assert result.json["answered_with_products"] == {"baseline": 11, "agent": 6}, (
            f"{result.json['answered_with_products']} answered with products, recorded "
            "baseline 11 / agent 6. Higher is not better and neither is lower: this counter "
            "is what stops a defect count improving because an arm stopped answering."
        )

    def test_the_metric_table_is_evidence_builds_declared_checks(
        self, harness: Any
    ) -> None:
        assert set(harness.METRICS) == set(harness.declared_checks()), (
            f"METRICS is {sorted(harness.METRICS)} and evidence.build declares "
            f"{sorted(harness.declared_checks())}. Every declared check gets a metric or it "
            "goes unmeasured, which is the whole reason the harness reads the names back out "
            "of a real bundle instead of hardcoding them."
        )

    async def test_a_seventh_declared_check_with_no_metric_exits_2(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        module = _load()
        del module.METRICS["prose"]
        code = await module.main([])
        assert code == 2, (
            f"a metric was removed and the harness exited {code}. It has to refuse to run: "
            "a declared check with no metric is a guarantee nothing scores."
        )
        assert "prose" in capsys.readouterr().err


class TestItCallsTheShippedPathRatherThanACopy:
    """`docs/EVAL.md` §9 promises a changed refusal path shows up here before it shows up
    live. That is only true while the harness calls the real functions — and for `_blocked`
    and `_unread` it was not: both were re-implemented inline, and deleting them off the
    module left the harness at exit 0."""

    @pytest.mark.parametrize(
        "name",
        [
            "_decide",
            "_blocked",
            "_unread",
            "_names",
            "_CHECKS_ES",
            "_Selection",
            "_Pick",
            "ConversationSession",
            "TurnResult",
            "_harvest",
            "MAX_TOOL_CALLS_PER_TURN",
        ],
    )
    async def test_deleting_it_off_loop_kills_the_harness(
        self, monkeypatch: pytest.MonkeyPatch, name: str
    ) -> None:
        module = _load()
        monkeypatch.delattr(loop, name)
        # NameError for the module-level `_CHECKS_ES`, which `loop._blocked` reads as a
        # global rather than off the module object; AttributeError for everything the
        # harness reaches through `loop.`.
        with pytest.raises((AttributeError, NameError), match=name):
            await module.main([])

    async def test_the_blocked_path_emits_the_event_production_emits(self) -> None:
        """`loop._blocked` emits `guardrail.evidence_blocked` and the inline copy did not,
        so a panel or a verifier reading that event was scored against nothing."""
        module = _load()
        products = module.load_products()
        blocked = next(i for i in module.ITEMS if i.id == "over-budget-apex-4")
        await module.run_item(blocked, products)

        emitted = [e for e in trace.events() if e.event == "guardrail.evidence_blocked"]
        assert emitted, (
            "the agent arm blocked on over-budget-apex-4 and never emitted "
            "guardrail.evidence_blocked. That is the event loop._blocked emits in "
            "production, so the harness is measuring a refusal path that is not the "
            "shipped one."
        )
        payload = emitted[-1].payload
        assert payload["blocking"], f"the event carries {payload}, with nothing blocking in it"
        assert emitted[-1].level == "guardrail"

    async def test_an_accepted_item_emits_no_blocked_event(self) -> None:
        """The negative control: without it the assertion above passes on any run that
        blocked anywhere, including one that blocks everything."""
        module = _load()
        products = module.load_products()
        clean = next(i for i in module.ITEMS if i.id == "clean-strap")
        await module.run_item(clean, products)
        assert not [e for e in trace.events() if e.event == "guardrail.evidence_blocked"], (
            "clean-strap is accepted and something emitted guardrail.evidence_blocked on it"
        )

    def test_the_bundle_covers_the_trace_from_before_retrieval(self, harness: Any) -> None:
        """`run_turn` marks the trace at the top of the turn, before the catalogue is read.
        A harness that marks it after retrieval builds a bundle over a shorter span than the
        one production builds — benign only for as long as no declared guardrail event is
        emitted during retrieval, which is not a property anybody guaranteed."""
        source = SCRIPT.read_text()
        run_item = source[source.index("async def run_item(") :]
        mark = run_item.index("trace.mark()")
        retrieve = run_item.index("await _retrieve(")
        assert mark < retrieve, (
            "run_item takes its trace mark after retrieval. run_turn takes it before, so the "
            "agent arm's evidence bundle no longer covers the same events the shipped one "
            "does — guardrail.case_unspecified fires inside retrieval."
        )


class TestTheHarnessCanFailTheAgent:
    """A harness that cannot report the agent losing is a rubber stamp. Exit 0 has to mean
    something, which means a broken agent arm has to produce something else."""

    @staticmethod
    def _off_by_one_price(module: Any) -> None:
        real = module.run_agent

        def wrong(*args: Any, **kwargs: Any) -> Any:
            rendered = real(*args, **kwargs)
            rendered.items = tuple(
                i.model_copy(update={"price_minor": i.price_minor + 1}) for i in rendered.items
            )
            return rendered

        module.run_agent = wrong

    async def test_an_agent_rendering_a_price_the_feed_does_not_have_loses_budget(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        module = _load()
        self._off_by_one_price(module)
        code = await module.main([])
        out = capsys.readouterr().out
        assert code == 1, (
            f"the agent rendered a price no variant has on every item and the harness exited "
            f"{code}. `budget` is the metric that carries the COP 100x boundary; if it "
            "cannot be lost it cannot be won either."
        )
        assert "REGRESSION — the baseline is better on: budget" in out

    async def test_allow_regression_reports_the_loss_instead_of_hiding_it(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        module = _load()
        self._off_by_one_price(module)
        assert await module.main(["--allow-regression"]) == 0
        assert "REGRESSION" in capsys.readouterr().out, (
            "--allow-regression is documented as 'report instead of exiting non-zero'. It "
            "silently dropped the report, which makes it a way to not know."
        )

    async def test_an_agent_that_is_only_the_baseline_wins_nothing(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The uncomfortable one, and the reason exit 0 is not the headline. Ties are not
        losses, so an agent stripped of every check exits 0 — with six ties and zero wins.
        Read `won N`, not the exit code."""
        module = _load()

        def unguarded(item: Any, snapshot: Any, candidates: Any, conclusive: bool, mark: int) -> Any:
            rendered = module.run_baseline(item, snapshot, candidates)
            rendered.arm = "agent"
            return rendered

        module.run_agent = unguarded
        code = await module.main([])
        out = capsys.readouterr().out
        assert code == 0 and "won 0 · tied 6 · lost 0" in out, (
            f"an agent arm with no checks at all exited {code} and the summary line is not "
            "'won 0 · tied 6 · lost 0'. Either the arms are no longer comparable, or the "
            "harness has started scoring something other than what reached the screen."
        )


class TestGroundTruthComesFromTheFixture:
    """The structural claim: an item carries a situation and a replay, and every expectation
    is derived from `fixtures/products.json` at scoring time. It is true of every field but
    one, and the one is the thing to keep stated."""

    def test_item_carries_only_the_situation_and_the_replay(self, harness: Any) -> None:
        fields = {f.name for f in dataclasses.fields(harness.Item)}
        assert fields == {
            "id",
            "need",
            "picks",
            "prose",
            "tool_calls",
            "budget_minor",
            "snapshot",
            "claimed_unavailable",
        }, (
            f"Item now carries {sorted(fields)}. A new field is a situation, a replay, or an "
            "annotation — and an annotation is a result pre-written into the case, which is "
            "how an evaluation gets tuned until it says what its author wanted."
        )

    async def test_claimed_unavailable_alone_can_move_a_verdict(self) -> None:
        """The concession `docs/EVAL.md` §2 makes, pinned so it cannot quietly stop being
        made. Nothing else in the harness has this property."""
        module = _load()
        module.ITEMS = tuple(
            dataclasses.replace(item, claimed_unavailable=("vertix-2",))
            if item.id == "absent-vertix-2"
            else item
            for item in module.ITEMS
        )
        verdicts = (await _score_all(module)).json["verdicts"]
        assert verdicts["local_availability"] == "tie", (
            "filling the replayed model's unavailable_devices on absent-vertix-2 no longer "
            f"moves local_availability (it is {verdicts['local_availability']!r}). Either the "
            "metric stopped reading that field — in which case §2's concession is stale — or "
            "the baseline stopped rendering it."
        )

    def test_the_docstring_names_the_field_instead_of_denying_it(self, harness: Any) -> None:
        doc = harness.__doc__ or ""
        assert "claimed_unavailable" in doc, (
            "the module docstring does not name claimed_unavailable. It is the one input a "
            "maintainer can tune to change a verdict, and it has to be named where somebody "
            "reading the harness will see it."
        )
        assert not re.search(r"no field in", doc, re.IGNORECASE), (
            "the docstring claims again that no field in Item can pre-write a result. That "
            "was false when it was written: claimed_unavailable is exactly that field."
        )

    def test_eval_md_does_not_repeat_the_claim_either(self) -> None:
        text = EVAL_MD.read_text()
        assert "claimed_unavailable" in text
        assert "no field in which a result could be pre-written" not in text, (
            "docs/EVAL.md §2 is back to claiming no field can pre-write a result."
        )


class TestTheItemSetReachesTheRefusalsItClaimsTo:
    def test_not_sold_locally_is_exercised(self, result: SimpleNamespace) -> None:
        """It was not, for the whole of this harness's first life — which left
        `_KIND_TEMPLATE['not_sold_locally']`, `prompts.NOT_SOLD_TEMPLATE`, `loop._names` and
        that branch of `loop._decide` dead, behind the metric §4 calls the differentiator."""
        kinds = {r.agent.kind for r in result.runs}
        assert kinds >= {
            "recommend",
            "buy_nothing",
            "not_sold_locally",
            "insufficient_evidence",
        }, (
            f"the agent arm only ever produced {sorted(kinds)}. `needs_human` is unreachable "
            "here — it is `loop._dead_end`, upstream of retrieval — but the other four are "
            "the outcomes this pipeline can end a turn on and each needs an item."
        )

    def test_the_absence_copy_is_the_shipped_template_and_names_the_watch(
        self, result: SimpleNamespace
    ) -> None:
        run = next(r for r in result.runs if r.item.id == "absent-pace-pro")
        text = run.detail["agent"].text
        assert text.startswith("COROS Colombia no vende el COROS PACE Pro."), (
            f"the not_sold_locally copy is {text[:120]!r}. It has to be "
            "prompts.NOT_SOLD_TEMPLATE over loop._names — a device named by anything else is "
            "a name this harness invented, and naming the wrong watch is the substitution "
            "the whole check exists to refuse."
        )
        assert run.detail["agent"].unavailable_devices == ("pace-pro",)

    def test_the_rate_limited_item_is_not_reported_as_an_empty_catalogue(
        self, result: SimpleNamespace
    ) -> None:
        run = next(r for r in result.runs if r.item.id == "storefront-rate-limited")
        assert run.agent.kind == "insufficient_evidence"
        assert run.baseline.scores["buy_nothing"].defects == 1, (
            "the baseline reported a 429 as something other than 'we could not look' and "
            "the buy_nothing metric did not charge it. 'we could not look' being reported "
            "as 'there is nothing' is the fabrication that check exists to stop."
        )


class TestExitCodes:
    async def test_a_clean_run_exits_0(self) -> None:
        assert await _load().main([]) == 0

    async def test_one_named_item_runs_only_that_item(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert await _load().main(["--item", "clean-strap"]) == 0
        out = capsys.readouterr().out
        assert "1 items · model replayed" in out and "absent-pace-pro" not in out

    async def test_an_item_id_that_matches_nothing_exits_2_and_not_0(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The second meaning of exit 2, and the one docs/EVAL.md used to omit. A typo'd id
        runs zero items, and zero items is a clean sweep of nothing."""
        code = await _load().main(["--item", "clean-strapp"])
        assert code == 2, (
            f"--item with a typo exited {code}. Anything but 2 lets a CI job that ran no "
            "cases at all report the same colour as one that ran thirteen."
        )
        assert "no item matched" in capsys.readouterr().err

    async def test_verbose_prints_the_defect_notes_with_their_taxonomy(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert await _load().main(["--verbose"]) == 0
        out = capsys.readouterr().out
        assert "not_in_catalog:gid://shopify/Product/7155761184811" in out, (
            "--verbose no longer names the unjoinable pick by id. The notes are the only "
            "thing that says WHICH product a defect count is about."
        )
        assert "·x100" in out or "·div100" in out, (
            "no price defect carries the COP 100x signature. unretrieved-product exists to "
            "put $3.400 on screen where $340.000 belongs; if the tag is gone, either the "
            "item stopped covering it or money.major_string_to_minor stopped detecting it."
        )


class TestItStaysOffline:
    async def test_no_arm_reaches_the_model_or_the_storefront(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        module = _load()

        async def boom(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError(
                "the harness reached the network. The model is replayed and the catalogue "
                "is fixtures/products.json; a live call here costs the storefront's "
                "IP-scoped limiter and makes the run unrepeatable."
            )

        monkeypatch.setattr(loop, "_model", boom)
        monkeypatch.setattr(loop, "read_snapshot", boom)
        assert await module.main([]) == 0

    def test_the_harness_imports_nothing_that_can_open_a_socket(self) -> None:
        tree = ast.parse(SCRIPT.read_text())
        roots: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots.add(node.module.split(".")[0])
        allowed = {
            "__future__",
            "argparse",
            "asyncio",
            "collections",
            "dataclasses",
            "json",
            "pathlib",
            "sys",
            "typing",
            "brujula",
            "coros_core",
        }
        assert roots <= allowed, (
            f"scripts/eval_baseline.py imports {sorted(roots - allowed)}. It is the thing "
            "that runs in CI: an http client, a clock or a socket in here is how an offline "
            "evaluation quietly becomes a live one."
        )

    def test_no_strava_at_any_depth(self) -> None:
        tree = ast.parse(SCRIPT.read_text())
        body = [n for n in tree.body if not isinstance(n, ast.Expr)]
        code = "\n".join(ast.unparse(n) for n in body)
        assert "strava" not in code.lower(), (
            "Strava is paywalled per AGENTS.md, so an evaluation that touched it would be "
            "measuring whether somebody paid. Only the docstring may mention it."
        )


def _table(heading: str) -> dict[str, tuple[str, str, str]]:
    """The `| metric | baseline | agent | winner |` rows under a heading in docs/EVAL.md."""
    text = EVAL_MD.read_text()
    start = text.index(heading)
    end = text.find("\n#", start + len(heading))
    rows = {}
    for line in text[start : end if end != -1 else len(text)].splitlines():
        match = re.match(r"\|\s*`(\w+)`\s*\|\s*(\S+)\s*\|\s*(\S+)\s*\|\s*(.+?)\s*\|", line)
        if match:
            metric, base, agent, winner = match.groups()
            rows[metric] = (base, agent, winner.replace("*", ""))
    return rows


class TestTheDocumentIsTheMeasurement:
    """`docs/EVAL.md` is the artifact a reader is asked to believe, and it is written by
    hand. Every number in it that the harness also produces is asserted here, so a table
    that goes stale fails `make check` instead of quietly becoming a claim."""

    def test_section_5_headline_matches_the_run(self, result: SimpleNamespace) -> None:
        recorded = _table("### Headline")
        actual = {
            metric: (base, agent, verdict.lower())
            for metric, (base, agent, verdict) in AGGREGATE.items()
        }
        assert recorded == actual, (
            f"docs/EVAL.md §5's headline table says {recorded}; the harness scores {actual}. "
            "The doc is the only place most readers meet these numbers."
        )

    def test_section_5_control_table_matches_the_run(self, result: SimpleNamespace) -> None:
        recorded = _table("### The control that matters more")
        actual = {
            metric: (base, agent, verdict.lower())
            for metric, (base, agent, verdict) in CONTROL.items()
        }
        assert recorded == actual, (
            f"docs/EVAL.md §5's control table says {recorded}; the harness scores {actual}. "
            "This is the table §5 tells the reader to quote."
        )

    def test_section_3_ledger_block_is_the_measured_one(self, result: SimpleNamespace) -> None:
        text = EVAL_MD.read_text()
        for arm in ("baseline", "agent"):
            total = result.module.Ledger(arm)
            for run in result.runs:
                total = total + getattr(run, f"{arm}_ledger")
            assert total.line() in text, (
                f"docs/EVAL.md §3 does not carry the {arm} arm's measured ledger "
                f"{total.line()!r}. Equal budget is the claim the evaluation stands on and "
                "the block is how it is shown rather than asserted."
            )

    def test_section_5_counters_match_the_run(self, result: SimpleNamespace) -> None:
        text = EVAL_MD.read_text()
        answered = re.search(
            r"items answered with products\s+baseline (\d+)\s+agent (\d+)", text
        )
        accepted = re.search(r"evidence bundle accepted\s+n/a\s+agent (\d+)", text)
        assert answered and accepted, "docs/EVAL.md §5's counters block is gone or reshaped"
        assert (int(answered.group(1)), int(answered.group(2))) == (
            result.json["answered_with_products"]["baseline"],
            result.json["answered_with_products"]["agent"],
        )
        assert int(accepted.group(1)) == sum(1 for r in result.runs if r.agent.accepted), (
            "the accepted-bundle counter in docs/EVAL.md §5 is not what the harness counts."
        )

    def test_the_item_count_in_the_doc_is_the_item_count(self, result: SimpleNamespace) -> None:
        text = EVAL_MD.read_text()
        counts = {int(n) for n in re.findall(r"(\d+) items, `fixtures/products\.json`", text)}
        counts |= {int(n) for n in re.findall(r"### Headline, all (\d+) items", text)}
        assert counts == {len(result.runs)}, (
            f"docs/EVAL.md says {sorted(counts)} items and the harness runs "
            f"{len(result.runs)}. §8's re-baseline note is what to follow."
        )

    def test_the_doc_states_both_things_exit_2_means(self) -> None:
        text = EVAL_MD.read_text()
        window = text[: text.index("## 1.")]
        assert "--item` matched nothing" in window or "`--item` matched nothing" in window, (
            "the exit-code table at the top of docs/EVAL.md defines 2 as metric drift only. "
            "An unmatched --item returns it too, and that one means zero cases ran."
        )


class TestSomethingRunsIt:
    """Before these existed the harness was in neither the Makefile nor .github/, so
    'CI-runnable' was a capability and nothing more."""

    def test_the_makefile_has_an_eval_target(self) -> None:
        text = MAKEFILE.read_text()
        target = re.search(r"^eval:\n\t(.+)$", text, re.MULTILINE)
        assert target, (
            "there is no `eval` target in the Makefile. Every other entry point in this repo "
            "goes through make, which is where PYPATH is exported."
        )
        assert "scripts/eval_baseline.py" in target.group(1)
        assert target.group(1).lstrip().startswith("$(PY)"), (
            f"the eval target runs {target.group(1)!r}. It has to go through $(PY) or it "
            "runs without the import roots and fails on `import brujula`."
        )

    def test_this_file_runs_the_harness_the_way_the_makefile_does(self, harness: Any) -> None:
        """Same script, same fixture, no arguments the target does not pass."""
        assert harness.FIXTURE == REPO / "fixtures" / "products.json"
        assert harness.FIXTURE.exists()
