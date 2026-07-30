# EVAL.md — the baseline harness

The success criterion this file serves: **the agent must beat a simple baseline at equal
budget.** An agent that wins by spending more calls has not won, so the budget is metered
per arm and printed in the output, and the harness refuses to declare a winner if the two
arms did not spend the same.

Everything here is offline. The catalogue is `fixtures/products.json`, recorded live from
COROS Colombia; nothing opens a socket. **No Strava at any depth** — Strava is paywalled
(`AGENTS.md`, Strava section) and an evaluation that blocked on a subscription would be
measuring whether somebody paid.

```bash
make check                                                   # the offline suite
PYTHONPATH=.:packages:apps/brujula ./.venv/bin/python scripts/eval_baseline.py
```

| flag | |
|---|---|
| `--verbose` | print every defect, with the taxonomy tag and the product it came from |
| `--json` | machine-readable, for CI to diff run over run |
| `--item ID` | run one case (repeatable) |
| `--allow-regression` | report a metric the baseline wins instead of exiting non-zero |

Exit codes: `0` all six metrics won or tied · `1` the baseline won one, or the arms did
not spend the same budget · `2` the metric table and `evidence.build`'s declared checks
have drifted apart.

---

## 1. The experiment

The model is the only part of a turn that needs the network, so it is **replayed, not
called**. Every case in `ITEMS` carries a recorded model proposal in exactly the shape
`loop._Selection` produces — product ids, variant ids, a rationale — plus the prose the
presentation stage produced. **Both arms receive the identical proposal.**

That is deliberate, and it is what makes the comparison mean anything. It isolates the
deterministic spine, which is the only thing this repo claims to guarantee:

> A guardrail written in the prompt is a suggestion; a guardrail written in Python is a
> guarantee.

If the arms were each given their own model turn, a difference between them could be a
lucky sample. Held fixed, a difference can only be the checks.

Both apps share that spine: `huella/agent/loop.py::_decide` and
`brujula/agent/loop.py::_decide` run the same five checks from `coros_core.guardrails` in
the same order, then the same `evidence.build`. The harness drives Brújula's, and what it
measures is the shared core.

### The two arms

**AGENT** — the shipped path, called directly rather than re-implemented:
`loop._decide` → `evidence.build` → `scrub_prose`, plus the presentation rules that decide
whether prose is generated at all. Importing the private `_decide` is intentional: a
harness with its own copy of the check order would drift from the loop and end up
certifying itself. This one breaks loudly when `loop.py` moves, which is what the
maintenance contract wants from anything watching it.

**BASELINE** — retrieval-grounded pass-through. Same snapshot, same tool calls, same
proposal. It joins each pick back to the feed by product id and renders **the feed's own**
title, URL and price. Where a pick will not join, it renders what the model claimed —
there is nothing else it could do. Ambiguity is resolved by taking the first variant. It
passes the model's `unavailable_devices` through untouched. No stock check, no budget
arithmetic, no absence derivation, no buy-nothing, no prose scrub, no evidence bundle.

---

## 2. Why that baseline is the honest one

A baseline chosen to lose measures nothing, so this one was deliberately made strong.

**It was not made to fail the easy way.** The weaker and more tempting baseline is
"render whatever JSON the model emitted". That loses on titles, on URLs, on price scale
and on provenance simultaneously, and it proves only that unvalidated model output is
unvalidated. The baseline here joins by product id, which is what any competent RAG
pipeline does, and that single move already earns it correct titles, correct URLs and
correct price **scale** for free. The agent therefore cannot win on those; it has to win
on the checks.

**It is what an ordinary implementation of this product looks like.** Retrieve the
catalogue, let the model pick, join the picks back to the source, render. Every guardrail
in `coros_core.guardrails` is an addition to that shape, not a replacement for it — which
is exactly why "does the addition earn its keep at the same budget" is the right question.

**It is allowed to get the hard cases right.** `_Selection` really does have an
`unavailable_devices` field, so a model *can* name a watch COROS Colombia does not sell
without being made to, and the baseline passes that claim straight through. One of the two
absence cases in the item set is recorded with the model getting it right, and the
baseline scores clean on it. A metric the baseline could never pass would be decoration.

**Where it is NOT charged for something.** It is not penalised for failing to filter
out-of-stock items — it is penalised for what it put on screen. The model saw `available`
per variant in `tools._slim` and proposed a sold-out one anyway. Filtering that is a check,
and the baseline is the no-check arm.

### The one assumption

Everything in an `Item` is a situation or a replay. Ground truth is derived from
`fixtures/products.json` at scoring time; there is no field in which a result could be
pre-written, which is the structural reason the harness cannot be tuned by annotation.

The exception, stated plainly: **whether the replayed model filled `unavailable_devices` is
a choice made in this file.** On `absent-vertix-2` it did not; on `absent-pace-3-with-strap`
it did. That pair is a modelling assumption, not a measurement, and it is the assumption
this evaluation is most exposed on. It is set that way because a prompt-level rule that
holds once and not the next time is the entire argument for moving the rule into Python —
and because setting it to "never" would be straw. What the local-availability metric can
therefore show is that the baseline is **unreliable** here, not that it is incapable. How
often a real model forgets is not measurable offline and this file does not claim it.

---

## 3. Equal budget, and how it is enforced

Three units are actually scarce in this system, and all three are metered per arm:

| unit | cap | why it is scarce |
|---|---|---|
| storefront reads | 1/turn | the harshest limiter here: ~4 requests before an IP-level lockout that outlasts the conversation (`AGENTS.md`, storefront section) |
| retrieval tool calls | `loop.MAX_TOOL_CALLS_PER_TURN` = 6 | the 7th is answered `TIMEOUT`, not dropped |
| model calls | `loop.MAX_MODEL_CALLS` = 25/conversation | one Gemini quota shared by three deployments |

`Ledger` is the only route to the feed and the only route to a tool, so an arm cannot spend
without it being counted. The two arms get **separate ledgers and each runs retrieval for
itself** — sharing one retrieval would make the budgets equal by construction and prove
nothing. The harness then compares the ledgers, and if they differ it prints `[FAIL]` and
stops: nothing below that line would be a comparison. It also cross-checks that the two
arms came back with identical candidate sets.

Offline the storefront read is served from disk, but it is metered as the request it stands
for. The point of the ledger is the comparison, not the clock.

**Equality is reachable because the guarantees are free.** `guardrails.py` is pure — no
I/O, no clock, no model — and `evidence.build` reads a trace that was already written. The
five checks and the bundle cost zero additional reads, zero tool calls and zero model
calls. The ledger is how that is demonstrated instead of asserted.

Measured, 30 Jul 2026, 12 items:

```
baseline  catalog_reads=12 tool_calls=15 model_calls=0 replayed_model_stages=12
agent     catalog_reads=12 tool_calls=15 model_calls=0 replayed_model_stages=12
```

---

## 4. The six metrics are the six declared checks

They were not invented for this file. `evidence.build` declares exactly six checks, and
each metric scores an arm on the axis one of those checks exists to guarantee — **whether
or not that arm ran it**. The harness reads the declared names back out of a real bundle
and exits `2` if the metric table and that list have drifted apart, so a seventh declared
check forces a seventh metric rather than going quietly unmeasured.

| metric | the check it comes from | a defect is |
|---|---|---|
| `provenance` | `guardrail.provenance` | a rendered item not in the feed, tagged `gwp-hidden`, on a variant nobody named, or whose title/URL disagree with the feed |
| `stock` | `guardrail.stock` | a rendered item whose named variant is not `available` in the feed |
| `budget` | `guardrail.budget` | a rendered price that is not the feed's minor-unit price for that variant, or a selection over a stated budget that does not say so |
| `local_availability` | `guardrail.local_availability` | the need names a device COROS Colombia does not sell and the answer does not name it |
| `buy_nothing` | `guardrail.buy_nothing` | "we could not look" reported as "there is nothing", or a product list where nothing purchasable existed |
| `prose` | `guardrail.prose` | a spec figure in the presented text that no retrieval-derived field backs |

The defect taxonomies are the repo's own — `DroppedItem.reason` and
`StockRejection.reason` — not a new vocabulary.

**These are counts, not rates.** "False things that reached a screen" is the quantity that
matters, and it is the one that cannot be gamed by an arm rendering fewer items. The
denominators are printed alongside and they differ between the arms, which is itself one of
the results.

**The COP 100× boundary** lives inside the `budget` metric, reported as a `·x100` /
`·div100` tag on a price defect. The rescale that detects it is done by
`money.major_string_to_minor` and nowhere else — both because `tests/test_money.py` fails
the build on a bare `*100` in any other module, and because it is the clearer statement of
the bug: reading a value that is already minor units *as though it were the feed's major
string* is exactly what that function does. It is a signature, not a proof.

---

## 5. What it measured — 30 Jul 2026

12 items, `fixtures/products.json`, model replayed, budgets identical.

### Headline, all 12 items

| metric | baseline | agent | winner |
|---|---|---|---|
| `provenance` | 3/12 | 0/6 | **agent** |
| `stock` | 3/12 | 0/6 | **agent** |
| `budget` | 2/14 | 0/7 | **agent** |
| `local_availability` | 1/2 | 0/2 | **agent** |
| `buy_nothing` | 3/3 | 0/3 | **agent** |
| `prose` | 5/12 | 0/12 | **agent** |

### The control that matters more

Six of those twelve are items where **the agent refused and the baseline answered**. An arm
that refuses has nothing to be wrong about, so the headline alone would be worth very
little. The harness therefore also scores the subset where **both arms put products on
screen** — nobody abstained, so nothing below can be abstention:

| metric | baseline | agent | winner |
|---|---|---|---|
| `provenance` | 1/7 | 0/6 | **agent** |
| `stock` | 0/7 | 0/6 | tie |
| `budget` | 0/8 | 0/7 | tie |
| `local_availability` | 1/2 | 0/2 | **agent** |
| `buy_nothing` | — | — | not exercised |
| `prose` | 4/6 | 0/6 | **agent** |

**So: three metrics are won on merit, two are ties, and the `stock` / `buy_nothing` wins in
the headline come entirely from items where the agent declined to answer.** That is the
honest reading, and it is the one to quote.

### Counters — reported, never scored

```
items answered with products      baseline 11      agent 6
evidence bundle accepted                  n/a      agent 11
```

The baseline answers on 11 of 12 and the agent on 6. Higher is not better here — an answer
beats a refusal only when it is true, which is what the six metrics measure — but it is the
agent's real cost and it is printed at the same size as the wins.

---

## 6. Where the agent is NOT better

The harness prints all of this; none of it is inferred.

**It abstains where a better answer existed.** On `unretrieved-product`, retrieval had
surfaced two real, purchasable COROS heart-rate monitors and the agent still answered *"de
lo que revisé, nada resuelve lo que me contaste"*. The only pick was unjoinable,
`check_provenance` dropped it, and `_decide` has no path from surviving candidates back to
an answer. **The agent's floor is refusal, not recovery.**

**An ambiguity becomes a buy-nothing.** On `ambiguous-case-apex-4` the need is "quiero el
COROS APEX 4 de 42mm" and the answer is *"lo honesto es que no compres nada todavía"*. The
correct answer is the case question, and this system **has** it —
`tools.lookup_device_compat` raises `CaseUnspecified` and emits `guardrail.case_unspecified`
during retrieval, which the harness confirms fires. But nothing downstream of the model
preserves that question, so when the model proposes the bare product anyway,
`_pick_variant` drops it for `variant_ambiguous` and the turn ends in "buy nothing". The
guardrail prevented the wrong price ($2.099.000 for the 46 mm case against a 42 mm request)
and lost the right question.

**A failed check is reported as a check that did not run.** On `over-budget-apex-4` the
bundle correctly blocks, and `loop._blocked` tells the person *"Me faltó comprobar la
cuenta contra tu presupuesto"* — "I failed to check your budget". `check_budget` ran, and
returned a precise verdict: nothing fits, the cheapest APEX 4 is $1.899.000 against a
$1.000.000 cap. `_blocked` selects on `c.outcome != "pass"`, which lumps `fail` in with
`not_run`, so a check that ran and disagreed is described as one that never happened. The
refusal is safe and the sentence is wrong.

**Two of the baseline's prose "defects" are true sentences.** `nylon` and `46 mm` are
counted unbacked because `guardrails._BACKING_FIELDS` over an `AdviceItem` is effectively
the title — the variant label that says exactly those words is not carried. The `prose`
metric measures adherence to the rule, not truthfulness, and the rule over-excises on
purpose. `evidence._DECLARED` already says so in that check's own `cannot_verify`. The
agent's scrub is deleting true words to hold the line.

---

## 7. What this does NOT measure

- **Whether the model picks good products.** The proposal is replayed and identical across
  arms by design. Nothing here evaluates retrieval quality, ranking, or taste.
- **Anything live.** No COROS UCP, no storefront, no Gemini, no Strava. `make verify` and
  `scripts/verify_brujula.py` are what touch the real thing; this is what runs in CI.
- **Whether a refusal is well worded.** Six metrics score a refusal as clean regardless of
  whether its copy fits the situation, which is why §6 exists and why the harness prints
  every refusal with its need, its copy and its candidate count.
- **How often a real model makes each mistake.** The item set covers the failure taxonomy;
  it is not a frequency estimate, and 12 hand-built cases are not a sample.
- **Latency, tokens, or cost in currency.** The ledger counts calls, which is what the
  limiters count.
- **Huella's own layer.** The uncertainty flags, the privacy boundary and the training view
  are covered by `tests/test_privacy_boundary.py` and `tests/test_huella_state.py`. This
  harness exercises the shared spine both apps route through.
- **The evidence bundle as a metric.** An arm that emits no guardrail events fails a bundle
  derived from guardrail events; that is circular, so bundle acceptance is a counter and
  never one of the six.

---

## 8. What a regression looks like

| symptom | what it means |
|---|---|
| exit `1`, `REGRESSION — the baseline is better on: …` | a check stopped firing, or stopped disagreeing when it should. Re-run with `--verbose`; the defect tags name the product |
| exit `1`, `[FAIL] the arms did not spend the same` | the harness itself is broken, or a check started doing I/O. Nothing else in the output is a comparison until this is fixed |
| exit `2`, metric table vs. declared checks | someone added a check to `evidence._DECLARED` without a metric, or removed one. Both move together |
| `RuntimeError: the arms retrieved different candidates` | retrieval became non-deterministic. Harness bug |
| a metric flips to `not exercised` | the item that covered it stopped covering it — usually because the fixture was re-dumped and a product's stock or price moved |
| the control table's wins shrink toward the headline's | the agent is abstaining more. Check the answered counter before celebrating the defect counts |

**A fixture re-dump is a re-baseline.** `scripts/dump_fixtures.py` pulls live COROS data,
and several items depend on facts that can change: the DURA and the Bike Cadence Sensor are
out of stock, the two `gwp-hidden` shirts are in stock, the APEX 4's first variant is the
46 mm case, the real heart-rate monitor is $340.000. If any of those move, items go
`not exercised` rather than failing — read the per-item table, do not read only the
aggregate.

**The numbers in §5 are a record, not a target.** If a change makes the agent win more,
check the control table and the answered counter first: winning by refusing more is not
winning. A harness tuned until the agent wins measures nothing.

---

## 9. Maintenance

| if you change… | you must also… |
|---|---|
| `packages/coros_core/evidence.py`'s declared checks | add or remove the matching metric in `scripts/eval_baseline.py::METRICS` — the harness exits `2` otherwise — and the table in §4 |
| `packages/coros_core/guardrails.py` (a verdict shape, a taxonomy) | re-run the harness; the defect tags are that module's own `reason` literals |
| `apps/brujula/brujula/agent/loop.py::_decide` or `_blocked` | re-run the harness. It calls both directly, so a reordered check or a changed refusal path shows up here before it shows up live |
| `loop.MAX_TOOL_CALLS_PER_TURN` / `MAX_MODEL_CALLS` | nothing — the harness reads them — but re-run it, because an item replaying more tool calls than the cap now raises |
| `fixtures/products.json` | re-run with `--verbose` and re-record §5. See the re-baseline note in §8 |

Two things this lane could not add and the next one should. **A `make eval` target** —
every other entry point in this repo goes through `make`, which is where `PYPATH` is
exported; until then the invocation at the top of this file is the only supported one. And
**`tests/test_eval_baseline.py`**, the counterpart to
`tests/test_verify_brujula.py`. This script is the only thing that scores the pipeline
against an alternative, so a bug in its judgement is a bug nothing else catches. It should
pin at minimum that the two ledgers are equal on every item, that `METRICS` and
`evidence.build`'s declared checks agree, that a deliberately broken arm is detected, and
that no item's expectation can be satisfied by an annotation rather than by the fixture.
