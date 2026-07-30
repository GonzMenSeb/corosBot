# Brújula — visual and functional QA

Playwright MCP against a running app, 30 Jul 2026. **Partial: the gate is audited, the shell
behind it is not.** What that means and why is in §4.

Screenshots: `docs/screenshots/brujula-gate-{1440,414}.png`.

---

## 1. What was measured

Contrast was computed **in the browser, from `getComputedStyle`**, walking every element with a
text node and resolving each one's background up the ancestor chain to the first opaque one. That
matters: `tests/test_brujula_theme.py` recomputes the theme's ratios with the theme's *own*
helper, so a bug in the helper would be invisible to it. This measured the rendered CSS instead,
with an independently written WCAG implementation.

Every token Brújula authors passes, and the figures agree with `theme.py`'s comments:

| text | size / weight | measured | floor |
|---|---|---|---|
| `Brújula` wordmark | 32 / 600 | **16.98:1** | 3.0 |
| `Asesor de equipos` | 10.6 / 600 | **5.72:1** | 4.5 |
| `COROS` | 10.6 / 700 | **16.10:1** | 4.5 |
| gate body copy | 14 / 400 | **5.72:1** | 4.5 |
| `Entrar` on the button | 14 / 700 | **10.37:1** | 4.5 |
| footer line | 12 / 400 | **5.38:1** | 4.5 |

Layout holds at both widths. At 414 px the card scales, nothing overflows horizontally, the
password field keeps its focus ring, and the type hierarchy still reads — the wordmark, the
tagline and the body are distinguishable by size and weight, not only by hue.

---

## 2. Defects found and fixed

**P1 — a third-party badge on a client-facing page.** Reflex injects a sticky "Built with
Reflex" badge into every page. `show_built_with_reflex` defaults to `None`, which
`reflex/compiler/compiler.py:1225-1232` resolves to `True` for anyone not on a paid Reflex tier,
and `_setup_sticky_badge()` then runs. It was bottom-right in the first screenshot. The brief for
this project is a distinct brand identity, so this is not cosmetic. Fixed by setting
`show_built_with_reflex=False` explicitly in `rxconfig.py` — `None` is not off. **DecaBot never
set it and ships the badge**, so this is a defect not to inherit rather than a convention to
follow. Pinned by `tests/test_brujula_app.py`.

**P2 — `/favicon.ico` 404 on every page load.** A browser requests it unprompted and Reflex
serves `assets/` at the web root, so an absent file is a console error in front of a judge.
Fixed with a real 32×32 ICO rasterised from the mark's own geometry — `theme.PAPER`,
`theme.BRASS`, `theme.BRASS_SOFT`, `theme.INK` read out of `theme.py` rather than retyped, and
the needle at `brand.BEARING` — so it cannot drift from the mark it stands for. Pinned by
`tests/test_brujula_app.py`, which checks the ICO magic bytes as well as the file's existence,
because a PNG renamed `.ico` is the obvious wrong fix.

---

## 3. Defects found and NOT fixed

**Reflex's own connection-error toast fails AA.** `rgb(230, 0, 0)` on its pale red panel measures
**4.35:1** against a 4.5 floor, on both lines. It is Reflex's component, not one of our tokens,
and it only appears when the backend is unreachable. Recorded rather than patched: overriding a
framework component's colours to fix a state the app should not be in is a worse trade than
knowing about it. If it ever needs fixing, the honest fix is our own connection banner.

---

## 4. What was NOT audited, and why

**Everything behind the gate**: the chat transcript, product cards, the advice panels
(recommendation, absent-model refusal, buy-nothing), and the dark trace rail. Those are the
substance of the interface and the audit is not finished without them.

Two reasons, both real:

1. **The storefront was refusing us for the whole session.** Product cards are built only from
   the server-side catalogue cache, so with no feed there are no cards to audit and the advice
   panels cannot be driven into their interesting states. The cause was found — Cloudflare
   classifying Python's TLS fingerprint, see `DECISIONS.md`, 30 Jul, "It is the TLS fingerprint"
   — and the fix is in flight, but it landed after this pass.
2. **The only running app was a hung dev server.** A `reflex` process from an earlier session had
   been listening on `:8000` for 2h45m, accepting TCP and answering nothing — blocked on a futex,
   with all of `/ping`, `/` and `/_event/` timing out at 8 s. Its frontend on `:3000` served the
   page, which is why the gate could be audited at all, and why both mobile screenshots carry a
   "Cannot connect to server" toast. It was killed.

**A note on that toast**, because it looks like a bug and is not: it reads `ws://localhost:8000/_event`
even when the page is served from another port. `api_url` is baked into the bundle and the
compiled client rewrites a same-domain host to `window.location.hostname` but **only clears the
port on https**. Over plain http on a non-8000 port the baked port survives, so the client looks
for a backend that is not there. In production, over TLS on 443, the port is cleared and it
connects. This was reproduced deliberately while trying to serve the container on `:18000`.

---

## 5. To finish this audit

- Land the `curl_cffi` transport fix so the catalogue reads.
- Run one container on `:8000` (so the baked `api_url` matches) with `BRUJULA_PASSWORD` set to a
  known value and `BRUJULA_ALLOWED_ORIGINS=http://localhost:8000` — the origin must match or the
  `/_event` handshake gets a `403` and the page renders perfectly and does nothing.
- Unlock the gate, drive a real turn, and audit: a recommendation with cards, the "not sold in
  Colombia" refusal (ask for a PACE Pro), the buy-nothing outcome (ask with a thousand-peso
  budget), and the trace rail — which is a second, dark register where none of the light tokens
  transfer, so it needs its own contrast pass.
- Re-run the in-browser contrast walk on each of those states, not just the gate.

---

## 6. Closing §4 and §5 — audited live, 30 Jul 2026

The two sections above recorded that everything behind the gate was unaudited, because there
was no catalogue to produce cards from and no hosted instance to drive. Both are now false.
Audited against `https://brujula.web.vespiridion.org` over real TLS.

**The event channel.** `wss://brujula.web.vespiridion.org/_event/?token=…`, no port, 101 —
read both from Playwright's own `websocket` event and from a `WebSocket` wrapper installed
before any page script. The bundle bakes `http://localhost:8000`; the compiled client rewrote
the host and dropped the port on https, exactly as `rxconfig.py`'s comment claims.

**Live data.** `turn.snapshot outcome=ok products=45 visible=43` — 45 less the two
`gwp-hidden`. `scripts/verify_brujula.py` scores **10/10** against live COROS and Gemini:
COROS PACE 4 at `$1.099.000`, total equal to the sum of the cards, no unbacked spec claim,
buy-nothing reachable with zero cards, and a missing API key turning into a reply rather than
a crash.

**Contrast, both widths, 0 failures.** Re-run with `scripts/contrast_walk.js`, whose WCAG
implementation is independent of `theme.py`'s helper — the point being that the unit suite
recomputes with the theme's own function, so a bug in it is invisible there. Worst pair at
414 is 5.072 against a 4.5 floor.

**One P1, found and fixed.** At 414 the page scrolled sideways by 16 px: `scrollWidth` 415
against `clientWidth` 399. The audit rail carried `width: 100%` together with
`margin: 0 1rem 1rem`, and a margin sits outside the width. Huella's rail never had it — at
mobile it uses a border instead of a floating margin. Fixed to `width: auto` at the three
mobile breakpoints, redeployed, re-measured at 0 px.

**What §3's standard still excludes.** Reflex's own connection-error toast still fails AA at
4.35:1 and is still not patched, for the reason given there.
