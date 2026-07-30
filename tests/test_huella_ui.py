"""Huella's five surfaces, checked against the tokens they are allowed to paint with.

`tests/test_huella_theme.py` proves the palette measures what it says it measures. This file
proves the interface only ever uses it that way — which is the half a palette cannot enforce
about itself. Both work off the same two registries: `theme.TYPE_ON` says which surfaces a
colour may carry words on, `theme.EDGE_ON` which ones it may draw a visible edge against.

The checks run against `Component.render()` rather than against the component objects, for two
reasons. `rx.match` renders its cases at construction time, so a walk over objects loses every
branch of every `rx.match` in the tree — here that is all four refusal panels, all three
confidence chips, every trace level and every check outcome. And the rendered form is what
actually ships: a `css:({…})` string with the declarations a browser will apply.

The walk resolves a surface the way a browser does. A node's own `background` replaces what it
inherited; a `_hover` or `_focus_within` background is a surface too, so a colour on that node
has to clear both. A translucent fill composites rather than replaces — `transparent`, and
every `theme.LEVEL_BG` tint — so the declared surface behind it stays the one of record.
Anything else that names no `theme.SURFACES` token resolves to `UNDECLARED`, and every pair
below it fails. Skipping such a subtree is what this walk used to do, and it is how the entire
audit rail sat unchecked behind one `rgba()` background.

A border is measured against what is BEHIND it, not against the fill it encloses: an edge
separates a box from the page. A border painted in the enclosing surface's own colour is a
cutout rather than an edge — the presence dot's ring is the one that needs it — so that case
passes without a pair.

**Two registers, and the transfer is the thing being policed.** Huella is dark-primary and the
sheets are a full second palette: `READOUT on SHEET` is 1.12:1 and `SUB on SHEET` is 2.81:1, so
an instrument token on a card is an unreadable sentence and a sheet token on a row is the same
sentence the other way round. `connect.py` is the one light module, because Strava's marks are
only legible unmodified on white; every other module here stays on the instrument.

`brand.py`'s own drawing is pinned by `tests/test_huella_brand.py`, which walks the lockup's
props rather than a surface tree — a mark is placed on a surface it is handed, not on one it
inherits, so there is nothing here for it to inherit from. What this file still owns about it
is the source scan for colour literals and the class names it reaches into the stylesheet for.

Where a property could be asserted from either file, it is asserted from one. A mutation probe
that trips two checks at once cannot tell you either of them is load-bearing, and this repo has
already shipped a suite that passed with five of its seven gates deleted.
"""

from __future__ import annotations

import ast
import pathlib
import re
from collections.abc import Iterator
from typing import Any, get_args

import pytest
import reflex as rx

from coros_core.evidence import Confidence, Outcome
from coros_core.models import AdviceKind
from coros_core.trace import Level

from huella import app
from huella.state import ProductCard, State
from huella.ui import advice, connect, gate, theme, trace_panel, training

UI = pathlib.Path(theme.__file__).parent
ASSETS = UI.parent.parent / "assets"
STYLESHEET = ASSETS / "huella.css"

MODULES = ("brand", "connect", "gate", "training", "advice", "trace_panel")
# The modules that stay on the instrument. `connect.py` is the exception and it is a theme
# rule, not a taste: `theme.EDGE_ON["STRAVA"]` names SHEET and nothing else. `brand.py` is
# absent because tests/test_huella_brand.py asks it the same question, and one property
# answerable by two suites is a property a mutation probe cannot isolate.
INSTRUMENT_MODULES = ("training", "advice")

_DECL = re.compile(r'\["([^"]+)"\]\s*:\s*')
_HEX = re.compile(r"#[0-9A-Fa-f]{6}")
# `rx.foreach`'s row var reaches a field through optional chaining: `item_rx_state_?.["title"]`.
_ARG = re.compile(r"(\w+)_rx_state_\??\.?\[\"(\w+)\"\]")
_ESCAPED = re.compile(r"\\u([0-9a-fA-F]{4})")
_CLASS = re.compile(r'"(hu-[a-z0-9-]+)"')
_CLASS_PROP = re.compile(r'className:"([^"]*)"')

# Props whose colours carry words, draw an edge, or become a surface.
_TYPE_PROP = ("color",)
_EDGE_PROP = "border"
_SURFACE_PROP = ("background", "backgroundColor")

_WORDS = ("Text", "Heading", "Link", "Button", "TextField")

# A fill that lets what is behind it through, and therefore does not replace the surface.
_TRANSLUCENT = re.compile(r"\btransparent\b|\brgba\(|\bhsla\(")
# The surface a colour was painted on when the background it sat on resolves to no
# theme.SURFACES token. It is in no TYPE_ON or EDGE_ON tuple on purpose: an undeclared
# surface is one nobody measured a ratio against, and it has to read as a failure rather
# than as an absence.
UNDECLARED = "an undeclared surface"


def source(module: str) -> str:
    return (UI / f"{module}.py").read_text(encoding="utf-8")


def names_by_hex() -> dict[str, tuple[str, ...]]:
    """Colour value → every token that carries it.

    `SHEET` and `ON_FILL` are both `#FFFFFF` and are deliberately different tokens: one is the
    surface COROS measured their own brand on, the other is the type that goes on a dark fill
    in either register. A value therefore resolves to a set of names, and a check passes when
    any one of them is declared for the pair.
    """
    out: dict[str, list[str]] = {}
    for name, value in vars(theme).items():
        if name.isupper() and isinstance(value, str) and _HEX.fullmatch(value):
            out.setdefault(value.upper(), []).append(name)
    return {value: tuple(names) for value, names in out.items()}


NAMES = names_by_hex()


def tokens(text: str) -> list[str]:
    """Every theme token named by the colour literals in one declaration."""
    return [name for hit in _HEX.findall(text) for name in NAMES.get(hit.upper(), ())]


def unknown(text: str) -> list[str]:
    return [hit for hit in _HEX.findall(text) if hit.upper() not in NAMES]


def decls(node: dict[str, Any]) -> Iterator[tuple[str, str, int]]:
    """Every style declaration on a node, with its nesting depth.

    Depth 1 is the node's own; deeper is inside a pseudo-selector such as `&:hover`, which
    applies to the same node under a different state.
    """
    for prop in node.get("props") or ():
        if not isinstance(prop, str) or not prop.startswith("css:"):
            continue
        hits = list(_DECL.finditer(prop))
        for index, hit in enumerate(hits):
            end = hits[index + 1].start() if index + 1 < len(hits) else len(prop)
            head = prop[: hit.start()]
            yield hit.group(1), prop[hit.end() : end], head.count("({") - head.count("})")


def kids(node: dict[str, Any]) -> Iterator[dict[str, Any]]:
    for child in node.get("children") or ():
        if isinstance(child, dict):
            yield child
    for key in ("true_value", "false_value", "default"):
        branch = node.get(key)
        if isinstance(branch, dict):
            yield branch
    for case in node.get("match_cases") or ():
        if isinstance(case, (list, tuple)) and isinstance(case[-1], dict):
            yield case[-1]


def nodes(node: dict[str, Any]) -> Iterator[dict[str, Any]]:
    yield node
    for child in kids(node):
        yield from nodes(child)


def surfaces_of(node: dict[str, Any], inherited: frozenset[str]) -> frozenset[str]:
    """The surface(s) a colour on this node is read against.

    A background naming a `theme.SURFACES` token resolves to it. A translucent one composites
    over what is behind rather than replacing it — `transparent` is a no-op and LEVEL_BG's
    tints are 12% washes — so the inherited surface stays the one of record. Anything else
    resolves to `UNDECLARED`, which appears in no TYPE_ON or EDGE_ON tuple and so fails every
    pair beneath it.

    Returning the empty set for the third case is the bug this replaces: `visit` yields
    nothing for a node with no surface, so one unresolvable background silently exempted its
    whole subtree — and `trace_panel.row()` sets `background=_level_bg(...)`, which put the
    entire audit rail behind exactly that hole.
    """
    own: set[str] = set()
    pseudo: set[str] = set()
    for prop, value, depth in decls(node):
        if prop not in _SURFACE_PROP:
            continue
        found = {name for name in tokens(value) if name in theme.SURFACES}
        if _TRANSLUCENT.search(value):
            found |= inherited
        elif not found:
            found = {UNDECLARED}
        (own if depth == 1 else pseudo).update(found)
    return frozenset((own or inherited) | pseudo)


def visit(node: dict[str, Any], inherited: frozenset[str]) -> Iterator[tuple[str, str, str, str]]:
    """Yield `(kind, node, colour, surface)` for every colour a node paints."""
    name = str(node.get("name") or "")
    here = surfaces_of(node, inherited)
    for prop, value, _ in decls(node):
        if not _HEX.search(value):
            # A radius, a width, an rgba hairline: nothing here to measure a pair for.
            continue
        if prop in _TYPE_PROP:
            kind = "words" if any(word in name for word in _WORDS) else "glyph"
            for surface in here:
                yield kind, name, value, surface
        elif prop.startswith(_EDGE_PROP):
            for surface in inherited:
                yield "edge", name, value, surface
    for child in kids(node):
        yield from visit(child, here)


def fills(node: dict[str, Any], inherited: frozenset[str]) -> Iterator[tuple[str, str, frozenset[str]]]:
    """Yield `(node, declaration, surface behind it)` for every fill a node paints.

    `visit` deliberately treats a background as a surface rather than as a pair, because
    `theme.SURFACES` says what a colour may be a background *for*, not what it may sit on. The
    vendor's orange is the one fill that has to answer that second question anyway: it arrives
    inside assets that may not be recoloured, so the surface under it is the only lever there is.
    """
    here = surfaces_of(node, inherited)
    for prop, value, _ in decls(node):
        if prop in _SURFACE_PROP and _HEX.search(value):
            yield str(node.get("name") or ""), value, inherited
    for child in kids(node):
        yield from fills(child, here)


def props_of(node: dict[str, Any]) -> Iterator[tuple[str, list[str]]]:
    yield str(node.get("name") or ""), [p for p in (node.get("props") or ()) if isinstance(p, str)]
    for child in kids(node):
        yield from props_of(child)


# The scalar keys that hold everything a browser is handed which is not a style prop: the
# rendered text, the state var a foreach walks, and the condition a branch turns on.
_SCALARS = ("contents", "iterable", "cond_state", "cond")


def flat_node(root: dict[str, Any]) -> str:
    """One rendered subtree as one searchable string, unescaped.

    `json.dumps` would be shorter and is wrong: it escapes every `"` in `role:"log"`, so a
    test looking for the prop it can see in the source never matches it here. Reflex escapes
    non-ASCII into the prop itself, so `Contraseña` arrives as `Contrase\\u00f1a` — undone
    here, because the whole interface is in Spanish and every second label carries an accent.
    """
    out: list[str] = []
    for node in nodes(root):
        out.append(str(node.get("name") or ""))
        out += [p for p in (node.get("props") or ()) if isinstance(p, str)]
        out += [str(node[key]) for key in _SCALARS if key in node]
    return _ESCAPED.sub(lambda hit: chr(int(hit.group(1), 16)), "  ".join(out))


def flat(component: rx.Component) -> str:
    return flat_node(component.render())


def arms(component: rx.Component) -> tuple[dict[str, Any], dict[str, Any]]:
    """The two branches of a component's outermost `rx.cond`, as separate rendered subtrees.

    `flat()` over a whole render holds both arms at once, because `rx.cond` renders its cases
    at construction time. Any prop one arm carries therefore satisfies an assertion aimed at
    the other — and `trace_panel.panel()`'s two arms both carry `aria-controls`, one on the
    expanded rail's close button and one on the collapsed pill, which is how deleting it from
    the pill went unnoticed.
    """
    for node in nodes(component.render()):
        yes, no = node.get("true_value"), node.get("false_value")
        if isinstance(yes, dict) and isinstance(no, dict):
            return yes, no
    raise AssertionError("that component renders no rx.cond to take a branch of.")


def imported(module: str) -> set[str]:
    tree = ast.parse(source(module))
    return {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("ui.theme")
        for alias in node.names
    }


def reads(module: str, attr: str) -> bool:
    """Whether a module's CODE touches `State.<attr>` — its prose does not count.

    A text scan reports this file's own docstring for saying which module may render
    `State.blocking`, which is the failure mode an AST walk exists to avoid.
    """
    return any(
        isinstance(node, ast.Attribute)
        and node.attr == attr
        and isinstance(node.value, ast.Name)
        and node.value.id == "State"
        for node in ast.walk(ast.parse(source(module)))
    )


def entries() -> dict[str, rx.Component]:
    """Every public surface of the five modules, built the way `app.py` builds it.

    A row model is taken off the state var rather than instantiated: `rx.foreach` hands the
    component a Var, and a real `ProductCard` has no `.length()` on its list fields.
    """
    return {
        "gate.screen": gate.screen(),
        "connect.notices": connect.notices(),
        "connect.panel": connect.panel(),
        "connect.attribution": connect.attribution(),
        "training.uncertainty": training.uncertainty(),
        "training.window": training.window(),
        "training.requirements": training.requirements(),
        "advice.questions": advice.questions(),
        "advice.verdict": advice.verdict(),
        "advice.kit": advice.kit(),
        "advice.card": advice.card(State.cards[0]),
        "advice.notes": advice.notes(),
        "advice.evidence": advice.evidence(),
        "trace_panel.panel": trace_panel.panel(top=app.HEADER_H),
    }


ENTRIES = entries()
# Every one of those surfaces is placed straight onto the page: `app._column()` declares no
# background of its own, so what is behind them is the instrument.
PAGE = frozenset({"DASH"})
BRANCHES = ("shell", "gate")


def branch(name: str) -> rx.Component:
    return app._shell() if name == "shell" else app._gate()


# ── the stylesheet, parsed rather than grepped ────────────────────────────────

_COMMENT = re.compile(r"/\*.*?\*/", re.S)


def rules(text: str) -> list[tuple[str, str]]:
    """`(selector, body)` for every top-level rule in one stylesheet fragment.

    A regex cannot do this: the reduced-motion block holds a nested `@keyframes`, and a
    non-nesting scan reads its `from {…}` as a rule of the media block. Brace depth is what
    tells the two apart.
    """
    out: list[tuple[str, str]] = []
    depth, start, head, selector = 0, 0, 0, ""
    for index, char in enumerate(text):
        if char == "{":
            depth += 1
            if depth == 1:
                selector, start = text[head:index].strip(), index + 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                out.append((selector, text[start:index]))
                head = index + 1
    return out


def declarations(body: str) -> list[tuple[str, str]]:
    out = []
    for chunk in body.split(";"):
        if "{" in chunk or "}" in chunk or ":" not in chunk:
            continue
        prop, _, value = chunk.partition(":")
        out.append((prop.strip().lower(), value.strip().lower()))
    return out


_TIME = re.compile(r"(\d*\.?\d+)\s*(ms|s)\b")
# Nothing under this has moved as far as a person is concerned. The shortest thing the
# reduced-motion block below genuinely keeps is 90ms and the blanket it exists to refuse is
# 0.001ms, so the floor sits in the empty gap between the two.
PERCEPTIBLE = 0.05


def seconds(value: str) -> list[float]:
    return [float(count) * (0.001 if unit == "ms" else 1.0) for count, unit in _TIME.findall(value)]


def switches_motion_off(prop: str, value: str) -> bool:
    """Whether one declaration cancels an animation rather than putting something in its place.

    It cannot simply reject the whole `animation-*` family, because a real alternative is
    still an animation: the kit's substitute is `animation: hu-kit-resolve 300ms`, which
    resolves instead of travelling and is exactly what this block is for. What it rejects is
    the two forms of the off switch — `animation: none`, and a duration nobody can perceive.
    `animation-duration: 0.001ms !important` is the second one, and it is the blanket the
    stylesheet's own comment names as the thing it was written to avoid.
    """
    if not prop.startswith("animation"):
        return False
    if "none" in value.replace("!important", "").split():
        return True
    found = seconds(value)
    if prop == "animation-duration":
        return bool(found) and min(found) < PERCEPTIBLE
    if prop == "animation":
        # In the shorthand the first <time> is the duration; a second one is the delay.
        return bool(found) and found[0] < PERCEPTIBLE
    return False


def flatten(text: str, reduced: bool = False) -> Iterator[tuple[str, str, bool]]:
    """`(selector, body, is it under prefers-reduced-motion)` for every rule, at-rules opened.

    `rules()` stops at the top level, so a rule nested in an `@media` block used to be
    invisible to every scan below — an animation could arrive inside one and the parametrised
    reduced-motion check would have nothing to say about it, because the class it animates was
    never discovered. `@keyframes` is the one at-rule not opened: its `from` and `50%` steps
    are not selectors, and reading them as rules is what brace depth already exists to avoid.
    """
    for selector, body in rules(text):
        if selector.startswith("@keyframes"):
            continue
        if selector.startswith("@"):
            yield from flatten(body, reduced or "prefers-reduced-motion" in selector)
        else:
            yield selector, body, reduced


CSS = _COMMENT.sub(" ", STYLESHEET.read_text(encoding="utf-8"))
ALL_RULES = list(flatten(CSS))
REDUCED_RULES = [(selector, body) for selector, body, reduced in ALL_RULES if reduced]
DEFINED_CLASSES = {
    hit for selector, _, _ in ALL_RULES for hit in re.findall(r"\.(hu-[a-z0-9-]+)", selector)
}


def animated() -> tuple[str, ...]:
    """Every `hu-` class the stylesheet animates outside the reduced-motion block.

    Derived rather than typed, so a fourth animation cannot arrive without an alternative:
    the parametrised test below is what would then have nothing to say about it. Media blocks
    are walked into for the same reason — a breakpoint is not a place motion stops needing an
    answer under `prefers-reduced-motion`.
    """
    found: set[str] = set()
    for selector, body, reduced in ALL_RULES:
        if reduced:
            continue
        if not any(prop.startswith("animation") for prop, _ in declarations(body)):
            continue
        found |= set(re.findall(r"\.(hu-[a-z0-9-]+)", selector))
    return tuple(sorted(found))


ANIMATED = animated()


class TestNothingIsPaintedWithAColourTheThemeNeverDeclared:
    @pytest.mark.parametrize("module", MODULES)
    def test_no_component_module_writes_a_colour_of_its_own(self, module: str) -> None:
        literals = _HEX.findall(source(module))
        assert not literals, (
            f"huella/ui/{module}.py writes the colour literal(s) {literals}.\n"
            "Every colour is a token in huella/ui/theme.py, where it carries the contrast\n"
            "ratio it was measured at. A literal here is a colour nobody measured, and\n"
            "tests/test_huella_theme.py cannot see it to check it."
        )

    @pytest.mark.parametrize("label", sorted(ENTRIES))
    def test_every_colour_that_reaches_the_browser_is_a_token(self, label: str) -> None:
        strays = sorted(
            {hit for _, props in props_of(ENTRIES[label].render()) for p in props for hit in unknown(p)}
        )
        assert not strays, (
            f"{label} renders {strays}, which is not any token in huella/ui/theme.py.\n"
            "Add it there with its measured ratio, or use the token that already exists."
        )


class TestNothingReadsAsWordsOnASurfaceNobodyMeasuredItOn:
    @pytest.mark.parametrize("label", sorted(ENTRIES))
    def test_type_sits_only_on_a_pair_the_theme_declares(self, label: str) -> None:
        bad = []
        for kind, node, value, surface in visit(ENTRIES[label].render(), PAGE):
            if kind == "edge" or unknown(value):
                # A colour no token carries has no pair to check, and the test above is the
                # one that owns it. Reporting it twice makes one bug look like two.
                continue
            allowed = theme.TYPE_ON
            if kind == "glyph":
                # A glyph is not words: the non-text floor is 3:1, so an EDGE_ON pair clears
                # it, and GRID, AXIS and the vendor's orange are declared RULE_ONLY.
                if any(name in theme.RULE_ONLY for name in tokens(value)):
                    continue
                allowed = {
                    name: theme.TYPE_ON.get(name, ()) + theme.EDGE_ON.get(name, ())
                    for name in set(theme.TYPE_ON) | set(theme.EDGE_ON)
                }
            if not any(surface in allowed.get(name, ()) for name in tokens(value)):
                bad.append(f"  {node}: {value.strip()} on {surface}")
        assert not bad, (
            f"{label} paints a colour on a surface theme.py never measured it against:\n"
            + "\n".join(sorted(set(bad)))
            + "\nEvery pair is declared in theme.TYPE_ON (words) or theme.EDGE_ON (marks and\n"
            "edges), and tests/test_huella_theme.py recomputes the ratio for each one. Use a\n"
            "declared pair, or add the pair to theme.py with its measurement — and remember\n"
            "the two registers do not share: an instrument token on a sheet is unreadable and\n"
            f"so is a sheet token on a row. '{UNDECLARED}' means the background under this\n"
            "colour names no theme.SURFACES token at all: declare it there, or make it a\n"
            "translucent fill so the surface behind it is what the colour is measured on."
        )

    @pytest.mark.parametrize("label", sorted(ENTRIES))
    def test_a_border_clears_the_floor_against_what_is_behind_it(self, label: str) -> None:
        bad = []
        for kind, node, value, surface in visit(ENTRIES[label].render(), PAGE):
            if kind != "edge" or unknown(value):
                continue
            named = tokens(value)
            if any(name in theme.RULE_ONLY or name == surface for name in named):
                continue
            if not any(
                surface in theme.EDGE_ON.get(name, ()) + theme.TYPE_ON.get(name, ())
                for name in named
            ):
                bad.append(f"  {node}: {value.strip()} against {surface}")
        assert not bad, (
            f"{label} draws an edge nobody can see, or one nobody measured:\n"
            + "\n".join(sorted(set(bad)))
            + "\nAn edge is measured against the surface BEHIND it. theme.EDGE_ON declares the\n"
            "pairs that clear 3:1; GRID and AXIS are the decorative hairlines, and a border in\n"
            f"the enclosing surface's own colour is a cutout, not an edge. '{UNDECLARED}' means\n"
            "the background behind this edge names no theme.SURFACES token, so there is\n"
            "nothing for the edge to be measured against."
        )


class TestTheVendorsOrangeSitsOnTheOneSurfaceItClears:
    def test_the_orange_is_declared_on_exactly_one_surface(self) -> None:
        assert theme.EDGE_ON["STRAVA"] == ("SHEET",), (
            f"theme.EDGE_ON['STRAVA'] is {theme.EDGE_ON['STRAVA']}.\n"
            "SHEET alone, and it is the vendor's constraint rather than ours: the marks arrive\n"
            "inside official assets that may never be recoloured, so the surface under them is\n"
            "the only lever we have. On SHEET_2 the real #FC5200 measures 2.9977:1 — two\n"
            "thousandths under the graphic floor — and darkening it is what the brand terms\n"
            "forbid. A second surface here is a second place the mark is illegible or a second\n"
            "place it can share a screen with the flag red."
        )

    @pytest.mark.parametrize("label", sorted(ENTRIES))
    def test_nothing_paints_the_orange_off_that_surface(self, label: str) -> None:
        allowed = set(theme.EDGE_ON["STRAVA"])
        bad = [
            f"  {node}: {value.strip()} on {sorted(behind) or ['nothing measurable']}"
            for node, value, behind in fills(ENTRIES[label].render(), PAGE)
            if "STRAVA" in tokens(value) and not (behind & allowed)
        ]
        assert not bad, (
            f"{label} paints Strava's orange somewhere theme.py did not declare it:\n"
            + "\n".join(sorted(set(bad)))
            + f"\nIt is declared on {sorted(allowed)} and nowhere else. The rule beside the\n"
            "eyebrow is drawn INSIDE the sheet for exactly this reason — a border_left on the\n"
            "panel would put DASH on its outer side, which is a pair nobody measured."
        )

    @pytest.mark.parametrize("mark", ("CONNECT_MARK", "POWERED_MARK"))
    def test_each_mark_resolves_to_a_vendored_file(self, mark: str) -> None:
        path = getattr(connect, mark)
        served = ASSETS / path.lstrip("/")
        assert served.is_file(), (
            f"connect.{mark} is {path!r} and {served} does not exist.\n"
            "Reflex serves assets/ at the web root, so the src and the vendored file are one\n"
            "path. A mark that 404s is a mark nobody notices is missing until a demo."
        )

    def test_no_mark_is_retyped_as_markup_this_file_owns(self) -> None:
        assert "<svg" not in source("connect"), (
            "connect.py inlines an svg.\n"
            "Strava's marks are theirs: byte-identical files under assets/strava/, rendered as\n"
            "images. Retyping one as inline markup is a modification whatever the paths say,\n"
            "and it is the shape a later recolour arrives in."
        )


class TestTheMarkIsPlacedOnceAndNeverTwice:
    def test_the_attribution_renders_exactly_once_in_the_whole_page(self) -> None:
        count = flat(app._shell()).count(connect.POWERED_MARK)
        assert count == 1, (
            f"the shell renders the 'Powered by Strava' mark {count} times.\n"
            "connect.panel() already carries attribution(); placing it again anywhere in the\n"
            "column is two of the vendor's marks on one screen, which is precisely the\n"
            "prominence their binding rules are about — the mark may never be more prominent\n"
            "than our own name, and Huella's name appears once."
        )

    def test_the_panel_is_what_carries_it(self) -> None:
        assert connect.POWERED_MARK in flat(connect.panel()), (
            "connect.panel() no longer contains attribution().\n"
            "It is inside the panel on purpose: the mark needs the panel's own SHEET under it,\n"
            "and a caller that has to remember to place it is a caller that will forget."
        )


class TestTheRailIsTheInstrumentAndNoSheetReachesIt:
    def test_the_rail_imports_no_token_from_the_light_register(self) -> None:
        light = {
            name
            for name in set(theme.TYPE_ON) | set(theme.SURFACES) | set(theme.EDGE_ON)
            if name.startswith("SHEET") or name.startswith("GRAPHITE")
        }
        leaked = sorted(imported("trace_panel") & light)
        assert not leaked, (
            f"trace_panel.py imports {leaked} from the light register.\n"
            "The rail is the instrument — INK under the column, INK_2 for a well, GRID for the\n"
            "hairlines — and none of the sheets' vocabulary transfers: READOUT on SHEET is\n"
            "1.12:1 and a SHEET_* ink on INK is the same sentence inverted. Unlike Brújula's\n"
            "rail there is no second register to reach for here; there is nothing to invert."
        )

    @pytest.mark.parametrize("module", INSTRUMENT_MODULES)
    def test_no_instrument_module_reaches_for_a_sheet_token(self, module: str) -> None:
        leaked = sorted(
            name
            for name in imported(module)
            if name.startswith("SHEET") or name.startswith("GRAPHITE")
        )
        assert not leaked, (
            f"huella/ui/{module}.py imports {leaked}.\n"
            "The SHEET_* and GRAPHITE_* tokens are measured on white and nowhere else —\n"
            "GRAPHITE on DASH is 1.83:1, and not rescuable with a border either. connect.py is\n"
            "the one light module in this app, because Strava's marks are only legible\n"
            "unmodified on SHEET."
        )

    def test_the_bundles_own_english_is_shown_on_the_rail_and_nowhere_else(self) -> None:
        readers = [module for module in MODULES if reads(module, "blocking")]
        assert readers == ["trace_panel"], (
            f"State.blocking is rendered by {readers}.\n"
            "Those strings are the evidence bundle's own English — 'stock did not run' — an\n"
            "engineering artifact for a reviewer. loop.py already tells the athlete the same\n"
            "thing in Spanish in the transcript. The rail is the surface that vocabulary\n"
            "belongs on; a Spanish checklist is not."
        )


class TestARefusalIsAnObjectNotAParagraph:
    def test_every_kind_that_recommends_nothing_has_its_own_panel(self) -> None:
        refusals = set(get_args(AdviceKind)) - {"recommend"}
        assert set(advice._KINDS) == refusals, (
            f"advice._KINDS covers {sorted(advice._KINDS)}; AdviceKind refuses to recommend\n"
            f"on {sorted(refusals)}.\n"
            "A kind with no row falls through to _UNNAMED, which is honest but says less than\n"
            "the answer deserves. Give the new kind its own glyph, eyebrow and headline."
        )

    def test_no_two_refusals_look_or_read_alike(self) -> None:
        for field in ("icon", "eyebrow", "headline"):
            values = [getattr(spec, field) for spec in advice._KINDS.values()]
            assert len(set(values)) == len(values), (
                f"two refusals share a {field}: {values}.\n"
                "'No compres nada' and 'eso aquí no está' are different answers. Two panels\n"
                "that read the same are a paragraph with extra steps."
            )

    def test_a_refusal_arrived_at_correctly_is_never_coloured_red(self) -> None:
        red = {getattr(theme, name).upper() for name in theme.UNCERTAINTY}
        spent = sorted(
            kind
            for kind, spec in advice._KINDS.items()
            if red & {hit.upper() for hit in _HEX.findall(flat(advice._refusal(spec)))}
        )
        assert not spent, (
            f"the refusal panels for {spent} paint a colour from theme.UNCERTAINTY.\n"
            "Red in Huella says 'do not lean on what is on screen' — a window too thin or too\n"
            "stale to reason from, a check that ran and failed, a turn that broke. A refusal\n"
            "is the opposite claim: 'no compres nada' and 'eso aquí no está' are answers\n"
            "arrived at correctly and worth leaning on, so they are the neutral register, and\n"
            "'no pude confirmarlo' and 'hasta aquí llego yo' are usable-with-reservations, so\n"
            "they are amber."
        )

    def test_the_panel_appears_only_when_the_kind_is_a_refusal(self) -> None:
        assert "is_refusal" in flat(advice.verdict()), (
            "advice.verdict() does not branch on State.is_refusal.\n"
            "`advice_kind` is '' before the first turn and 'recommend' when there are cards,\n"
            "and a panel headlined 'No compres nada.' beside a kit is the disagreement the\n"
            "evidence bundle exists to prevent."
        )

    def test_the_devices_it_names_come_from_the_registry(self) -> None:
        rendered = flat(advice._refusal(advice._KINDS["not_sold_locally"]))
        assert "unavailable" in rendered, (
            "the not_sold_locally panel does not read State.unavailable.\n"
            "state.py resolves those slugs to COROS's own device names through devices.py. A\n"
            "name typed here, or taken from the model's prose, is a name COROS may not use."
        )
        for model in ("PACE", "APEX", "VERTIX", "NOMAD", "DURA"):
            assert model not in source("advice"), (
                f"advice.py names the {model} in its own source.\n"
                "Device names live in devices.py and reach the screen through state.py."
            )


class TestACardCarriesOnlyWhatTheCatalogueSaid:
    @staticmethod
    def fields() -> set[str]:
        return {field for arg, field in _ARG.findall(flat(advice.kit())) if arg == "item"}

    def test_every_value_a_card_renders_is_a_field_of_the_card_model(self) -> None:
        strays = sorted(self.fields() - set(ProductCard.model_fields))
        assert not strays, (
            f"a card renders {strays}, which state.ProductCard does not carry.\n"
            "The card is built from a feed-backed AdviceItem in state._card(). A field that\n"
            "is not on the model is a specification the catalogue never stated."
        )

    def test_the_price_is_the_one_state_already_formatted(self) -> None:
        rendered = self.fields()
        assert "price_display" in rendered and "price_minor" not in rendered, (
            f"the card renders {sorted(rendered)}.\n"
            "money.minor_to_display is a Python function and cannot run on a Var, so a price\n"
            "reaches this file already formatted. Rendering minor units puts COROS's own\n"
            "centavos on screen — a hundred times the real number."
        )

    def test_the_only_prose_on_the_card_is_marked_as_prose(self) -> None:
        """The rule has to be on the element that renders the sentence, not on an ancestor.

        Serialising a node's whole subtree and looking for `rationale` in it matches the card
        itself, which carries a `border` and a `borderRadius` of its own — and `borderRadius`
        starts with `border`. Both of those made the rule look present with the rule deleted.
        So: the node whose OWN text is the rationale, and `borderLeft` spelled out.
        """
        rendered = advice.card(State.cards[0]).render()
        prose = [
            node
            for node in nodes(rendered)
            if any(
                "rationale" in str(child.get("contents") or "")
                for child in (node.get("children") or ())
                if isinstance(child, dict)
            )
        ]
        assert len(prose) == 1, (
            f"{len(prose)} nodes render item.rationale as their own text.\n"
            "It is one sentence in one place; a walk that finds none has lost it and a walk\n"
            "that finds several has nothing left to pin the quote rule to."
        )
        ruled = [value.strip() for prop, value, _ in decls(prose[0]) if prop == "borderLeft"]
        assert ruled and _HEX.search(ruled[0]), (
            f"the rationale renders with {sorted(prop for prop, _, _ in decls(prose[0]))} and\n"
            "no coloured borderLeft among them — the quote rule down its left edge is gone.\n"
            "Every other value on the card came off COROS's feed; that one is Huella's own\n"
            "sentence, and the rule is what says so without a caption. The card's own border\n"
            "and border-radius are not it: they enclose everything, so they distinguish\n"
            "nothing."
        )

    def test_a_product_with_no_photo_still_gets_a_card(self) -> None:
        rendered = flat(advice.card(State.cards[0]))
        assert "image_url" in rendered and advice._NO_PHOTO in rendered, (
            "the card has no branch for a product COROS ships no photo for.\n"
            "image_url is '' and never None precisely so there is something to test: an\n"
            "in-stock product without a photo is still buyable."
        )


class TestNoEnglishReachesAPersonWhoOnlyReadsSpanish:
    def test_every_confidence_the_bundle_can_report_has_a_phrase(self) -> None:
        assert set(advice._CONFIDENCE_ES) == set(get_args(Confidence)), (
            f"advice._CONFIDENCE_ES covers {sorted(advice._CONFIDENCE_ES)}, "
            f"evidence.Confidence is {sorted(get_args(Confidence))}.\n"
            "A confidence with no phrase renders as the fallback, which reports less\n"
            "certainty than the check actually had — or, worse, as the English literal."
        )

    def test_every_outcome_the_bundle_can_report_has_a_glyph_and_a_word(self) -> None:
        declared = set(get_args(Outcome))
        assert set(advice._OUTCOME_ICON) == declared and set(advice._OUTCOME_ES) == declared, (
            f"advice._OUTCOME_ICON covers {sorted(advice._OUTCOME_ICON)} and _OUTCOME_ES "
            f"{sorted(advice._OUTCOME_ES)}; evidence.Outcome is {sorted(declared)}.\n"
            "The glyph is the whole answer for a sighted reader and the word is the whole\n"
            "answer for everybody else, so an outcome needs both. 'not_run' in particular is\n"
            "not 'fail': a check nobody ran is not a check that failed."
        )

    def test_every_level_the_rail_can_receive_has_a_glyph_of_its_own(self) -> None:
        assert set(trace_panel._LEVEL_ICON) == set(get_args(Level)), (
            f"trace_panel._LEVEL_ICON covers {sorted(trace_panel._LEVEL_ICON)}, trace.Level is "
            f"{sorted(get_args(Level))}.\n"
            "Hue is one signal and it is the one a monochrome screenshot in an issue, and a\n"
            "reader who cannot separate cyan from grey, both lose. The glyph is the second."
        )

    def test_the_checklist_never_renders_the_bundles_own_detail(self) -> None:
        shown = {field for arg, field in _ARG.findall(flat(advice.evidence())) if arg == "check"}
        assert "detail" not in shown, (
            f"the checklist renders {sorted(shown)}.\n"
            "CheckRow.detail is the bundle's own English. The rail carries that register;\n"
            "this panel is read by somebody who asked a question in Spanish."
        )


class TestTheWholeInterfaceIsOperableWithoutAMouse:
    @pytest.mark.parametrize("label", sorted(ENTRIES))
    def test_no_icon_only_control_is_left_unnamed(self, label: str) -> None:
        bare = []
        for node in nodes(ENTRIES[label].render()):
            if "Button" not in str(node.get("name") or ""):
                continue
            props = " ".join(p for p in (node.get("props") or ()) if isinstance(p, str))
            if "aria-label" in props:
                continue
            if not any("contents" in child for child in nodes(node) if child is not node):
                bare.append(str(node.get("name")))
        assert not bare, (
            f"{label} renders {bare} with neither a label nor any words in it.\n"
            "Several controls hide their word below md, which is exactly the viewport a phone\n"
            "uses — so every one of them carries an aria_label as well."
        )

    @pytest.mark.parametrize("name", BRANCHES)
    def test_each_branch_of_the_page_keeps_exactly_one_top_level_heading(self, name: str) -> None:
        count = flat(branch(name)).count('as:"h1"')
        assert count == 1, (
            f"the {name} branch renders {count} h1 headings.\n"
            "brand.wordmark() is the page's one h1 and both branches mount exactly one of it;\n"
            "the empty state's heading is an h2 because it unmounts the moment the first\n"
            "message lands, and a refusal's headline is an h2 for the same reason."
        )

    def test_each_notice_is_a_live_region_of_the_right_urgency(self) -> None:
        rendered = flat(connect.notices())
        assert rendered.count('role:"alert"') == 2 and rendered.count('role:"status"') == 1, (
            "connect.notices() no longer renders two alerts and one status.\n"
            "A redirect Strava refused and a turn that broke are both 'do not lean on what is\n"
            "on screen'; 'conectado' is the ordinary day and interrupting for it is noise."
        )
        assert (
            rendered.count('"aria-live":"assertive"') == 2
            and rendered.count('"aria-live":"polite"') == 1
        ), (
            "a notice carries a role and no explicit aria-live.\n"
            "Each region is on screen only while it has something to say, so without the\n"
            "explicit value the whole announcement depends on the role's default being read by\n"
            "a reader that only just mounted the node."
        )

    def test_the_rail_announces_itself_as_a_log(self) -> None:
        rendered = flat(trace_panel.panel())
        assert 'role:"log"' in rendered and 'role:"alert"' not in rendered, (
            "the rail is not a log.\n"
            "Events arrive asynchronously through the whole turn. A log is an ordered running\n"
            "record; an alert would interrupt whatever else is mid-sentence, several times a\n"
            "turn."
        )

    def test_the_collapsed_rail_says_what_it_will_open(self) -> None:
        _, pill = arms(trace_panel.panel())
        rendered = flat_node(pill)
        assert '"aria-expanded":"false"' in rendered, (
            "the false branch of trace_panel.panel() is not the collapsed pill.\n"
            "This test takes one arm of the rail's rx.cond rather than the whole render, and\n"
            "aria-expanded is what tells the two apart. If the branches have swapped, the\n"
            "assertion below is aimed at the wrong one."
        )
        assert f'"aria-controls":"{trace_panel.RAIL_ID}"' in rendered, (
            "the pill that reopens the rail does not name what it controls.\n"
            "aria-controls is what tells a screen reader the button and the panel are the\n"
            "same thing; the panel carries that id. The expanded rail's close button declares\n"
            "the same attribute, so asserting it against the whole render is a check the\n"
            "collapsed pill can fail on its own without anything going red."
        )

    def test_a_status_glyph_carries_its_word_for_a_reader_that_cannot_see_it(self) -> None:
        assert "hu-sr-only" in flat(advice.evidence()), (
            "the checklist's outcome glyphs carry no visually-hidden word.\n"
            "A tick, a cross and a dash are the entire answer for a sighted reader and total\n"
            "silence for everybody else."
        )


class TestTheRailIsExactlyAsShortAsTheHeaderIsTall:
    def test_the_rail_sticks_below_the_header_and_not_under_it(self) -> None:
        rendered = flat(trace_panel.panel(top=app.HEADER_H))
        assert f"calc(100vh - {app.HEADER_H})" in rendered, (
            f"the rail is not {app.HEADER_H} shorter than the viewport.\n"
            "app.HEADER_H is the sticky header's height and app.py hands it to panel(top=…).\n"
            "Two numbers that disagree put the rail's last rows below the fold with no way to\n"
            "scroll to them."
        )


class TestEveryClassNamedHereIsARuleTheStylesheetHas:
    @staticmethod
    def named() -> dict[str, set[str]]:
        return {module: set(_CLASS.findall(source(module))) for module in MODULES}

    def test_the_modules_name_classes_at_all(self) -> None:
        assert set().union(*self.named().values()), (
            "no module names a `hu-` class any more.\n"
            "The stylesheet is the craft layer — keyframes, the tabular figures, the clamp,\n"
            "the instrument's scrollbar — and a component that stopped reaching for it either\n"
            "lost a behaviour or wrote it as a style prop, where reduced motion cannot answer."
        )

    @pytest.mark.parametrize("module", MODULES)
    def test_every_class_a_module_names_exists_in_the_stylesheet(self, module: str) -> None:
        missing = sorted(self.named()[module] - DEFINED_CLASSES)
        assert not missing, (
            f"huella/ui/{module}.py names {missing} and assets/huella.css defines no rule for\n"
            "them. A class with nothing behind it is a silent no-op: the pip simply stops\n"
            "moving, the figures stop aligning, and nothing warns you."
        )

    def test_no_class_the_page_assembles_elsewhere_is_missing_a_rule(self) -> None:
        """The classes the source scan above cannot see.

        It reads string literals, so a name built at runtime — an f-string, a lookup, a class
        `app.py` adds around one of these surfaces — is invisible to it and visible here. The
        two sets are kept disjoint so one missing rule is one failure.
        """
        named = set().union(*self.named().values())
        used = {
            name
            for label in BRANCHES
            for value in _CLASS_PROP.findall(flat(branch(label)))
            for name in value.split()
            if name.startswith("hu-")
        }
        missing = sorted(used - named - DEFINED_CLASSES)
        assert not missing, (
            f"the page renders {missing}, which assets/huella.css does not define.\n"
            "app.py lists that stylesheet in `stylesheets=` and Reflex serves assets/ at the\n"
            "web root, so the class names and the file are one contract."
        )

    def test_the_animated_classes_are_the_three_this_suite_knows_about(self) -> None:
        assert ANIMATED == ("hu-kit", "hu-pulse", "hu-shake"), (
            f"assets/huella.css animates {list(ANIMATED)}.\n"
            "A new animation needs a reduced-motion alternative of its own, and the test below\n"
            "is parametrised off this list — so widening it silently is how one arrives\n"
            "without one."
        )

    @pytest.mark.parametrize("name", ANIMATED)
    def test_each_animation_has_a_reduced_motion_alternative_not_an_off_switch(self, name: str) -> None:
        """A contract between the two halves of assets/huella.css, and now for all three.

        `hu-shake` used to be applied by nothing: the gate's error arm was a plain
        `rx.text(..., role="alert")` with no class on the card, so this parameter checked a
        pair of rules no browser ever reached. `huella/ui/gate.py` wires it — the class sits
        on the wrapper box around the form, conditioned on `State.gate_error`, and stays on
        while the error stands so the reduced-motion alternative outlives the lurch it
        replaces. All three are real coverage now: `advice.kit()`, `brand.PULSE_CLASS` and
        `gate.SHAKE_CLASS`.
        """
        replacements = [
            (selector, body) for selector, body in REDUCED_RULES if f".{name}" in selector
        ]
        assert replacements, (
            f".{name} animates and the prefers-reduced-motion block never mentions it.\n"
            "Reduced motion means no vestibular triggers, not no information."
        )
        kept = [
            f"{prop}: {value}"
            for _, body in replacements
            for prop, value in declarations(body)
            if not switches_motion_off(prop, value)
        ]
        assert kept, (
            f"under prefers-reduced-motion, .{name} is switched off and nothing takes its\n"
            "place:\n  " + "\n  ".join(f"{s} {{{' '.join(b.split())}}}" for s, b in replacements) + "\n"
            "That is the blanket this stylesheet was written to avoid, in one of its two\n"
            "spellings: `animation: none` with nothing after it, or a duration nobody can\n"
            f"perceive — `animation-duration: 0.001ms !important` is under the {PERCEPTIBLE}s\n"
            "floor and is the exact declaration the stylesheet's own comment refuses. The\n"
            "'working' pulse becomes a held ring, the kit resolves instead of travelling and\n"
            "the gate's refusal becomes a held flag outline — every one of them keeps its\n"
            "meaning and drops its movement. An off switch tells an athlete waiting on a turn\n"
            "less than they were being told before."
        )

    def test_no_module_writes_an_animation_as_a_style_prop(self) -> None:
        inline = sorted(
            f"{label}: {prop}"
            for label in sorted(ENTRIES)
            for node in nodes(ENTRIES[label].render())
            for prop, _, _ in decls(node)
            if prop.startswith("animation")
        )
        assert not inline, (
            "motion is declared inline:\n  " + "\n  ".join(inline) + "\n"
            "Keyframes are the one thing a Reflex style prop cannot express, and an animation\n"
            "written as a prop is one assets/huella.css cannot swap for a still alternative\n"
            "under prefers-reduced-motion. Name a class instead. A `transition` is fine — the\n"
            "stylesheet shortens every one of those globally."
        )
