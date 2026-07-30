# corosBot

Two agentic advisors for **COROS Colombia**, one monorepo, one shared deterministic
core. Both reason over the live COROS storefront, and both would rather refuse than
guess.

- **Brújula** (`apps/brujula/`) — *orients by asking.* A short interview, then a
  recommendation grounded in the live catalog. It says "that watch isn't sold in
  Colombia" when it isn't, and "buy nothing" when that is the honest answer.
- **Huella** (`apps/huella/`) — *orients by what you already ran.* It reads your
  Strava history and reasons from demonstrated training instead of self-report.

Both flows end at advice. Neither app has a cart tool or a checkout tool: the
human-in-the-loop boundary is enforced by **omitting the capability**, not by
instructing the model.

## Layout

```
packages/coros_core/   shared, LLM-free typed core: catalog, money, guardrails, trace
apps/brujula/          Reflex app B1 — interview-led advisor
apps/huella/           Reflex app B2 — Strava-grounded advisor
tests/                 one flat suite, offline by default
fixtures/              recorded live payloads, development-only
docs/                  decisions, deploy, runbook, visual briefs
infra/jenkins/         Jenkinsfile — Jenkins owns main, GitHub Actions owns PRs
```

## Quickstart

```sh
make setup          # python3.12 venv + pinned deps
make check          # offline suite
make verify         # live suite: COROS storefront, UCP, Strava
make dev-brujula    # localhost:3000
make dev-huella     # localhost:3001
```

## Why two Reflex apps and not one

Reflex resolves both `.web/` and `reflex.lock/` against the **current working
directory** (`Dirs.WEB = ".web"`, `Bun.ROOT_LOCKFILE_DIR = "reflex.lock"`), so two
apps in one tree would fight over the same build output. Each app therefore owns its
directory, its `rxconfig.py`, its ports, and its committed `reflex.lock/`; `reflex
export` always runs from `apps/<name>/`.

Conventions, and the registry of verified facts that must not be "fixed", live in
[AGENTS.md](AGENTS.md).
