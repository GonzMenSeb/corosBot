"""Live retrieval from COROS Colombia's storefront, and the only place a raw feed dict
is allowed to exist.

Nothing here caches. The whole Colombian catalogue is 45 products on one page, so
"refresh the catalogue" costs one request; a local copy would read as mocked, and stock
served from one is a fabricated inventory claim. `fixtures/` is for offline development.

`client()` returns the `requests` MODULE, not a `Session`, so every call opens one
connection and closes it. **THAT CHOICE RESTS ON A MEASUREMENT TAKEN AGAINST A DIFFERENT
STORE, AND IT HAS NEVER BEEN REPRODUCED AGAINST COROS.** DecaBot measured it against
**Decathlon** on 28 Jul 2026 over a 24-feed burst: unpooled 24/24 at 11.4 req/s, a shared
`Session` 4/24 at 6.4 req/s, httpx 429 every time — faster unpooled and clean, slower
pooled and refused, so for *that* store it was neither rate nor the library. `ucp.py` stays
on httpx because the MCP endpoint is a different limiter, verified unaffected the same hour.

Do not read the paragraph above as a fact about coros.com.co. It is a transplanted
conclusion, and `docs/DECISIONS.md` (30 Jul 2026, "What refuses us at the storefront is not
settled") records an afternoon of COROS measurements that it does not explain — including
`requests` refused and `curl` served in the same minute, from the same IP, over the same
HTTP version. Whether this module should be on httpx is **an open question**, and the
experiment that would settle it is written down there. Settle it before changing the
transport, and do not unify the clients on the strength of the Decathlon numbers alone.

Load-bearing facts encoded here (`AGENTS.md`) — these look like bugs and are not:
  * A handle is a URL slug, not a description. `correa-de-nylon-de-24-mm-morada-para-
    apex-4-46-copia` is a 22 mm white silicone strap for the APEX 4 42. Nothing here
    reads a spec out of a handle; the title and the option labels are the sources.
  * `product_type` is empty on 24 of 45 products, PACE 4 included. Carried, never keyed.
  * `sku` is null on 30 of 126 variants, and `"Default Title"` is Shopify's label for
    the 10 products with no real options. Both normalise to empty.
  * Two products are tagged `gwp-hidden` — gift-with-purchase shirts, in stock and
    priced. Excluded by default, and flagged rather than dropped when asked for.
  * Prices arrive as decimal strings in MAJOR units; `money.py` is the only converter.
  * `blogs/blog.json` is 404. The Atom feed serves the 30 most recent posts and ignores
    `?page=`, so there is no pagination loop to write — the sitemap's 58 are not
    reachable this way.
  * In `strip_untrusted`, tags are stripped BEFORE unescaping. See the comment there.
  * A 429 is never retried and never polled. Retrying is measurably what keeps the
    storefront's limiter shut — see the transport section.

Every refusal emits, and `evidence.py` reads those events: a turn where the storefront
said no must not read downstream as a turn where it answered nothing. The sanitiser emits
only when it actually removed an injected segment, and it carries counts rather than the
matched text — an evidence bundle gets pasted into a model.
"""

from __future__ import annotations

import asyncio
import html
import random
import re
import time
from typing import Any

import feedparser
import requests

from coros_core.models import Article, CatalogProduct, CatalogVariant
from coros_core.money import major_string_to_minor
from coros_core.trace import emit

BASE = "https://coros.com.co"

# One page, no pagination, 45 products. limit=250 is Shopify's ceiling and there is
# room to spare; if the catalogue ever passes it, this needs a loop and a test.
PRODUCTS_URL = f"{BASE}/products.json?limit=250"

# `blogs/blog.json` is a 404 here — Atom is the only machine-readable blog feed.
BLOG_URL = f"{BASE}/blogs/blog.atom"

HIDDEN_TAG = "gwp-hidden"

# Shopify's placeholder for a product with no real options, on 10 of 45 products.
# Rendered as a variant label it reads to a shopper as a choice they have to make.
_PLACEHOLDER_LABELS = {"Default Title", "Title"}

REQUEST_TIMEOUT = 30.0

DESCRIPTION_CHARS = 400
ARTICLE_CHARS = 600


class CatalogUnavailable(Exception):
    """The storefront refused, failed, or answered with something that is not the feed.

    Distinct from an empty result, and the distinction is load-bearing: an out-of-stock
    product is a fact about stock, this is the absence of one. Reporting it as "nothing
    is available" invents an inventory claim out of a request that never completed.

    `retry_after` is reported rather than slept on — see the transport section.

    Constructing one emits `catalog.unavailable`. The emit lives here rather than at the
    six raise sites so a new failure path cannot be added without a trace event: an
    unrecorded refusal reads downstream as a catalogue that answered nothing."""

    def __init__(
        self,
        url: str,
        status: int | None = None,
        detail: str = "",
        retry_after: str | None = None,
    ) -> None:
        super().__init__(f"storefront unavailable ({detail or status}) for {url}")
        self.url = url
        self.status = status
        self.detail = detail
        self.retry_after = retry_after
        self.rate_limited = status == 429
        emit(
            "catalog.unavailable",
            {
                "url": url,
                "status": status,
                "detail": detail,
                "retry_after": retry_after,
                "rate_limited": self.rate_limited,
            },
            "error",
        )


def client() -> Any:
    """The `requests` MODULE, deliberately — NOT a `requests.Session`.

    A Session pools connections and connection REUSE is what the storefront refuses.
    See the module docstring for the measurement. The TLS handshake per request is the
    price of the feed working at all, and it costs about a second across a whole turn."""
    return requests


async def aclose() -> None:
    """Nothing to close — no pooled connections are held, on purpose. Kept because
    scripts and tests call it as part of this module's contract."""
    return None


# ── transport: a circuit breaker, because retrying is what keeps the door shut ──
#
# Measured against coros.com.co on 30 Jul 2026, and this is the fact the design turns on:
#
#   * The limiter tolerates a short burst — 4 requests over ~20 s were served — and then
#     refuses with HTTP 429, `Retry-After: 60`, Cloudflare, body `local_rate_limited`.
#   * THE HINT IS NOT HONEST, AND POLLING PROLONGS THE LOCKOUT. Once refused, 30
#     consecutive requests spaced 10 s apart were all refused, for five unbroken
#     minutes. It cleared after ~100 s of COMPLETE QUIET. Our own retries are the thing
#     keeping it shut, so a 429 is never retried and never polled — it latches, and every
#     request inside the cooldown fails immediately WITHOUT touching the network.
#   * Mid-lockout some attempts get no response at all: the connection is dropped and
#     `requests` raises ConnectTimeout. A network exception is therefore not "the network
#     broke", and it is retried only while the latch is open.
#   * The `User-Agent` is not the discriminator. A browser UA was served four times in a
#     row and then refused exactly like `python-requests/2.x`; the earlier success was
#     the cooldown expiring, not the header. Do not "fix" this with a UA.
#
# So this diverges from DecaBot twice, in the same direction: there a 429 spaces later
# calls by PACE_SECONDS and keeps sending, and the ladder retries a 429 up to its budget.
# Here both would extend the outage. The latch still DECAYS, unlike ucp.py's: the whole
# catalogue is one request per turn, so a permanent latch would tax every later turn.

# One in flight at a time. DecaBot allows 6 because it fetches ~24 collection feeds per
# turn; this module fetches one document, so the only source of a burst is several
# browser sessions at once — and a burst is the one thing measured to trip the limiter.
SEM = asyncio.Semaphore(1)

MIN_INTERVAL = 0.08  # a rate floor for back-to-back turns
MAX_ATTEMPTS = 4
RETRY_BUDGET_SECONDS = 10.0  # TOTAL per request: a turn is waiting on this
BACKOFF_BASE = 0.6
BACKOFF_CAP = 4.0

# Advertised as 60 s and observed to need ~100 s of quiet. The cost of guessing low is
# one more 429, which re-latches; the cost of guessing high is a turn that could have
# been served. 90 s splits it, and being wrong is self-correcting in the safe direction.
COOLDOWN_SECONDS = 90.0

_paced_until = 0.0

_gap_lock = asyncio.Lock()
_last_start = 0.0


def is_paced() -> bool:
    return time.monotonic() < _paced_until


def engage_pacing() -> None:
    global _paced_until
    _paced_until = time.monotonic() + COOLDOWN_SECONDS


def cooldown_remaining() -> float:
    return max(0.0, _paced_until - time.monotonic())


def reset_pacing() -> None:
    """Tests only. In a running process the latch decays on its own clock."""
    global _paced_until, _last_start
    _paced_until, _last_start = 0.0, 0.0


async def _space() -> None:
    """Hold the floor only long enough to stamp the slot: this bounds the rate, and SEM
    bounds the concurrency."""
    global _last_start
    async with _gap_lock:
        wait = MIN_INTERVAL - (time.monotonic() - _last_start)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_start = time.monotonic()


async def _send(url: str) -> requests.Response:
    """`requests` is synchronous and the app is not — this is the seam. SEM already
    bounds it, so the thread pool never grows past that."""
    return await asyncio.to_thread(lambda: client().get(url, timeout=REQUEST_TIMEOUT))


async def _fetch(url: str) -> requests.Response:
    async with SEM:
        await _space()
        return await _send(url)


def _delay(attempt: int, remaining: float) -> float | None:
    """None means the budget cannot cover another wait — give up and report it."""
    wait = min(BACKOFF_CAP, BACKOFF_BASE * 2**attempt)
    wait *= 0.5 + random.random() / 2  # jitter, or concurrent callers retry in lockstep
    return None if wait > remaining else wait


async def _get(url: str, what: str) -> requests.Response:
    if is_paced():
        raise CatalogUnavailable(
            url,
            429,
            f"{what}: refused, not polling for another {cooldown_remaining():.0f} s",
            retry_after=f"{cooldown_remaining():.0f}",
        )

    deadline = time.monotonic() + RETRY_BUDGET_SECONDS
    status: int | None = None
    detail = ""
    retry_after: str | None = None

    for attempt in range(MAX_ATTEMPTS):
        try:
            r = await _fetch(url)
        except requests.RequestException as exc:
            status, detail = None, f"{type(exc).__name__}: {exc}"[:160]
        else:
            if r.status_code < 400:
                return r
            status, detail = r.status_code, f"HTTP {r.status_code}"
            if r.status_code == 429:
                retry_after = r.headers.get("Retry-After")
                engage_pacing()
                emit(
                    "catalog.rate_limited",
                    {
                        "what": what,
                        "url": url,
                        "retry_after": retry_after,
                        "cooldown_s": round(cooldown_remaining()),
                    },
                    "error",
                )
                break  # retrying is measurably what keeps the limiter shut
            if r.status_code < 500:  # 404 and friends: retrying cannot help either
                break

        wait = _delay(attempt, deadline - time.monotonic())
        if wait is None or attempt == MAX_ATTEMPTS - 1:
            break
        emit(
            "catalog.retry",
            {"what": what, "attempt": attempt + 1, "wait_s": round(wait, 3), "detail": detail},
        )
        await asyncio.sleep(wait)

    raise CatalogUnavailable(url, status, f"{what}: {detail}", retry_after)


async def _get_json(url: str, what: str) -> dict[str, Any]:
    r = await _get(url, what)
    try:
        body = r.json()
    except ValueError as exc:
        raise CatalogUnavailable(url, r.status_code, f"{what} is not JSON: {exc}"[:160]) from None
    if not isinstance(body, dict):
        raise CatalogUnavailable(
            url, r.status_code, f"{what} decoded to {type(body).__name__}, expected an object"
        )
    return body


# ── untrusted free text ────────────────────────────────────────────────────────

_COMMENT = re.compile(r"(?s)<!--.*?(?:-->|\Z)")

# The content of these elements is not prose, and replacing only the tags leaves it
# reading as prose. COROS's largest description is 36 160 characters of BeeFree email
# template; without this, sanitising it returns pure CSS and truncates the real copy
# away. An unclosed element swallows the rest of the document, which is the safe
# direction. This is the divergence from DecaBot's sanitiser.
_OPAQUE = re.compile(
    r"(?is)<\s*(style|script|head|noscript|svg|template)\b[^>]*>.*?(?:<\s*/\s*\1\s*>|\Z)"
)

# A role marker makes everything up to its close tag untrusted, so the CONTENT goes too.
_ROLE = re.compile(
    r"(?is)<\s*(system|assistant|user|instructions?)\b[^>]*>.*?(?:<\s*/\s*\1\s*>|\Z)"
)

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")

# Both languages, because the storefront is Spanish and so is the likeliest attempt.
# Measured across all 45 live descriptions and all 30 articles — 1 931 segments — with
# zero false positives. A looser CSS-shaped filter was tried on the same corpus and ate
# 22 segments of real copy (store hours, an Instagram handle, a NIT), so precision here
# comes from `_OPAQUE` above and not from sniffing.
_INJECTION = re.compile(
    r"""(?ix)
    ignore \s+ (all \s+ |any \s+ |your \s+ |the \s+ )* (previous|prior|above|earlier|system) |
    disregard \s+ (all \s+ |any \s+ |your \s+ |the \s+ )* (previous|prior|above|earlier|instruction|system) |
    (you \s+ are \s+ now|from \s+ now \s+ on|new \s+ instructions?) |
    (system \s* (prompt|message|:)) |
    \[/? \s* (INST|SYSTEM|/?s) \s* \] |
    (reveal|print|repeat|output|show) \s+ (me \s+ )? (your|the) \s+ (system \s+ )? (prompt|instructions?|rules) |
    (add|put) \s+ .{0,30} \s* (to \s+ the \s+ cart|free) |
    (set|make) \s+ .{0,20} price \s+ to |
    100\s*% \s+ (off|discount) |
    developer \s+ mode | jailbreak |
    (ignora|olvida|desestima) \s+ (todas? \s+ |las? \s+ |tus \s+ |el \s+ )* (instruccion\w*|anterior\w*|reglas?|sistema) |
    (ahora \s+ eres|de \s+ ahora \s+ en \s+ adelante|nuevas \s+ instrucciones) |
    (revela|imprime|repite|muestra) \s+ (me \s+ )? (tu|el|las) \s+ (prompt|instruccion\w*|reglas?) |
    (agrega|añade|pon) \s+ .{0,30} \s* (al \s+ carrito|gratis) |
    (precio|valor) \s+ (a|en) \s+ (0|cero) |
    100\s*% \s+ de \s+ descuento
    """
)


def strip_untrusted(text: str | None, max_chars: int = DESCRIPTION_CHARS) -> str:
    """Catalog free text is untrusted input that happens to arrive from a vendor.

    TAGS ARE STRIPPED BEFORE UNESCAPING, and that order is load-bearing. One live
    article contains `href="&gt;https://…"`; unescape first and that `&gt;` becomes a
    real `>` that ends the tag early, spilling the URL and a `style="color: rgb(…)"`
    attribute into the copy as if a human had written them. Roles and tags are then
    re-checked on the unescaped text, because `&lt;system&gt;` is a role marker too.
    """
    if not text:
        return ""

    raw = _COMMENT.sub("\n", str(text))
    raw = _OPAQUE.sub("\n", raw)
    raw = _ROLE.sub("\n", raw)

    plain = _ROLE.sub("\n", html.unescape(_TAG.sub("\n", raw)))
    plain = _TAG.sub("\n", plain)

    # Tags become segment breaks, not spaces: an injection lives in its own <p>, and
    # collapsing that boundary makes the surrounding real copy collateral damage.
    kept, injected = [], 0
    for part in re.split(r"(?<=[.!?])\s+|\n+", plain):
        part = _WS.sub(" ", part).strip()
        if not part:
            continue
        if _INJECTION.search(part):
            injected += 1
            continue
        kept.append(part)

    out = _WS.sub(" ", " ".join(kept)).strip()
    if len(out) > max_chars:
        out = out[:max_chars].rsplit(" ", 1)[0] + "…"

    if injected:
        # Counts, never the matched text: an evidence bundle built from these events gets
        # pasted into a model, and carrying the attack verbatim launders it back into a
        # prompt. Silence here means nothing matched, which is the ordinary case.
        emit(
            "guardrail.untrusted_text",
            {"segments_dropped": injected, "chars_in": len(str(text)), "chars_out": len(out)},
            "guardrail",
        )
    return out


# ── normalisation ──────────────────────────────────────────────────────────────


def _label(raw: str | None) -> str:
    label = (raw or "").strip()
    return "" if label in _PLACEHOLDER_LABELS else label


def map_product(raw: dict[str, Any]) -> CatalogProduct:
    """One feed product to the only product shape anything downstream sees.

    Every field is copied or converted. Nothing is inferred, and in particular nothing
    is inferred from `handle` — see the module docstring."""
    images = raw.get("images") or []
    variants = tuple(
        CatalogVariant(
            variant_id=str(v["id"]),
            label=_label(v.get("title")),
            sku=(v.get("sku") or "").strip(),
            price_minor=major_string_to_minor(v["price"]),
            available=bool(v.get("available")),
        )
        for v in raw.get("variants") or []
    )
    tags = tuple(str(t) for t in raw.get("tags") or [])
    return CatalogProduct(
        product_id=str(raw["id"]),
        handle=raw["handle"],
        title=raw["title"],
        product_url=f"{BASE}/products/{raw['handle']}",
        image_url=(images[0].get("src") if images else None),
        product_type=(raw.get("product_type") or "").strip(),
        vendor=(raw.get("vendor") or "").strip(),
        tags=tags,
        option_names=tuple(
            name for o in raw.get("options") or [] if (name := _label(o.get("name")))
        ),
        variants=variants,
        description=strip_untrusted(raw.get("body_html")),
        hidden=HIDDEN_TAG in tags,
    )


def normalize(
    payload: dict[str, Any], *, include_hidden: bool = False
) -> tuple[CatalogProduct, ...]:
    """A malformed product costs that product and not the other 44. Everything being
    malformed, or the whole feed arriving empty, is a shape change and fails closed:
    COROS Colombia has 45 products, and "nothing is available" is an inventory claim."""
    raw = payload.get("products")
    if not isinstance(raw, list) or not raw:
        raise CatalogUnavailable(PRODUCTS_URL, 200, "the feed carried no products")

    products = []
    for item in raw:
        try:
            products.append(map_product(item))
        except Exception as exc:
            emit(
                "catalog.unmappable",
                {
                    "product_id": str(item.get("id", "") if isinstance(item, dict) else ""),
                    "handle": (item.get("handle", "") if isinstance(item, dict) else ""),
                    "error": f"{type(exc).__name__}: {exc}"[:200],
                },
                "error",
            )
            continue
    if not products:
        raise CatalogUnavailable(PRODUCTS_URL, 200, f"none of {len(raw)} products could be read")

    kept = tuple(p for p in products if include_hidden or not p.hidden)
    if not kept:
        raise CatalogUnavailable(
            PRODUCTS_URL, 200, f"all {len(products)} products are tagged {HIDDEN_TAG}"
        )
    return kept


async def get_products(*, include_hidden: bool = False) -> tuple[CatalogProduct, ...]:
    """The whole catalogue, live, every time. Two products are gift-with-purchase
    shirts and are excluded unless asked for, which is why 43 is the usual count."""
    payload = await _get_json(PRODUCTS_URL, "products")
    return normalize(payload, include_hidden=include_hidden)


# ── blog: grounding for a claim about training, never about a product ──────────


def map_article(entry: Any) -> Article:
    """`content` over `summary`: the summary is the first two sentences plus a "Más"
    read-more link, and the lede is what a claim gets grounded in."""
    body = next((c.get("value") for c in entry.get("content") or [] if c.get("value")), None)
    return Article(
        article_id=entry.get("id") or entry.get("link") or "",
        title=(entry.get("title") or "").strip(),
        url=entry.get("link"),
        summary=strip_untrusted(body or entry.get("summary"), ARTICLE_CHARS),
        published=(entry.get("published") or entry.get("updated") or "").strip(),
        author=(entry.get("author") or "").strip(),
    )


def parse_articles(feed: bytes | str) -> tuple[Article, ...]:
    """An entry that cannot be turned into a citation is dropped rather than carried:
    a grounded claim needs a URL a human can open."""
    parsed = feedparser.parse(feed)
    articles = []
    for entry in parsed.entries:
        try:
            articles.append(map_article(entry))
        except Exception as exc:
            emit(
                "catalog.unmappable_article",
                {
                    "article_id": str(entry.get("id") or entry.get("link") or ""),
                    "error": f"{type(exc).__name__}: {exc}"[:200],
                },
                "error",
            )
            continue
    return tuple(articles)


async def get_articles() -> tuple[Article, ...]:
    """The 30 most recent posts. There is no pagination to write: `?page=2` returns the
    same 30, and the 58 the sitemap lists are not reachable through this feed."""
    r = await _get(BLOG_URL, "blog")
    return await asyncio.to_thread(parse_articles, r.content)
