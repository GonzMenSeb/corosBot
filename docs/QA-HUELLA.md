# QA — Huella

Audited 30 Jul 2026 against the **live instance** at `https://huella.web.vespiridion.org`,
not a dev server. That is the point: the client clears the baked port on https, and
`HUELLA_ALLOWED_ORIGINS` is templated from the same variable as the Traefik `Host()` rule, so
origin and page-origin cannot diverge the way they can locally. Live TLS is the easier target,
not the harder one.

Tooling is **playwright-mcp**, not chrome-devtools-mcp, which is not installed here. The one
capability that cost is reading the WebSocket URL: playwright's request list does not surface
`ws://`. Solved better than the original plan asked for — see §1.

---

## 1. The event channel — the single most important observation

Read two ways that cannot both be wrong in the same direction: Playwright's own `websocket`
event on the browser context, and a `WebSocket` constructor wrapper installed via
`addInitScript` so it runs before any page script. Both returned, character for character:

```
wss://huella.web.vespiridion.org/_event/?token=…&EIO=4&transport=websocket
```

- **`wss:`** — upgraded from the baked `ws:`
- **host is the page origin**, not `localhost`
- **no port**, though the bundle was compiled against `http://localhost:8001` and the
  container listens on `:8000`

That is R1 retired by measurement rather than by inheritance from DecaBot. Frames confirmed
flowing: 3 sent, 6 received, no close, no socket error.

**The "Connection Error" toast appears during load and clears.** It is Reflex's own
pre-connect state, not a defect — verified by re-reading the DOM after the socket settles
(no matching element) while frames were already flowing.

---

## 2. States driven, on live data

| state | result |
|---|---|
| Gate | unlocks; `gate.unlocked` GUARDRAIL row appears in the rail |
| **Unconfigured Strava** | renders *"Esta instancia no tiene credenciales de Strava configuradas."* — **no Connect button**, which is the whole point of the empty-string vault values |
| Attribution | "Powered by Strava" mark renders, plus *"Huella no está desarrollada ni patrocinada por Strava."* |
| Cold-start interview | fires. Trace shows `guardrail.uncertainty grounded=False confidence=none flags=["not_connected"]`, then `questions.asked` |
| Evidence panel | renders `no se ejecutó` for `not_run` checks — the distinction the theme's `OUTCOME_COLOR` draws |
| Trace rail | 13 event rows, each with level and payload |

The interview firing on an unconnected account is the expected state, not a degraded one.
Huella's premise is that it asks instead of deducing, and that is what it did.

---

## 3. Contrast, measured in the browser

`scripts/contrast_walk.js`, run against every rendered state. Its WCAG implementation is
**deliberately independent of `theme.py`'s helper**: both theme suites recompute their declared
ratios with the same function the theme uses, so a bug there moves the measurement and the
expectation together and stays invisible. It also composites `rgba` layers in paint order
rather than taking the first opaque ancestor, because this palette puts real translucent
tokens between text and surface.

| width | distinct pairs | failures | worst |
|---|---|---|---|
| 1440 | 16 | **0** | 4.7161 — `#9a9a9a` on composited `#1d333b`, 9.92px, floor 4.5 |
| 414 | 30 | **0** | same pair |

The worst pair is `SUB` on a composited instrument surface, and it clears AA by 0.216.

---

## 4. 414px

- **Horizontal overflow: 0 px** (`scrollWidth` 399 = `clientWidth` 399).
- **The Strava mark's `max_width` holds**: natural width 365 px, rendered 176 px, right edge
  inside the viewport.
- The two registers stay separated — the instrument's tokens stop at the sheet's edge, which
  is what `READOUT on SHEET 1.12:1` makes non-negotiable.

Brújula failed this check on the same run and Huella did not; the cause and fix are in
`docs/QA-BRUJULA.md` §6 and in the commit "Brújula scrolled sideways at 414".

---

## 5. What was NOT audited

- **The Strava round trip.** Blocked on a paid subscription, by decision. Everything up to the
  redirect is exercised; the token exchange is not.
- **A connected account's rendered training window.** `training.window()` and `advice.notes()`
  render state that requires a real Strava history, so they were exercised by the offline
  suite and not on screen.
- **The confidence three-way on live data.** Only `none` was reachable without a history.
- **Reflex's own connection-error toast**, which `docs/QA-BRUJULA.md` §3 records as failing AA
  at 4.35:1. Left alone for the same reason: overriding a framework component to fix a state
  the app should not be in is the worse trade. It is excluded from the walk above because it
  is not the app's markup.
