"""Brújula's five surfaces, checked against the tokens they are allowed to paint with.

`tests/test_brujula_theme.py` proves the palette measures what it says it measures. This file
proves the interface only ever uses it that way — which is the half a palette cannot enforce
about itself. Both work off the same two registries: `theme.TYPE_ON` says which surfaces a
colour may carry words on, `theme.EDGE_ON` which ones it may draw a visible edge against.

The checks run against `Component.render()` rather than against the component objects, for two
reasons. `rx.match` renders its cases at construction time, so a walk over objects loses every
branch of every `rx.match` in the tree — including all four refusal panels. And the rendered
form is what actually ships: a `css:({…})` string with the declarations a browser will apply.

The walk resolves a surface the way a browser does. A node's own `background` replaces what it
inherited; a `_hover` or `_focus_within` background is a surface too, so a colour on that node
has to clear both. A gradient or an `rgba()` resolves to no token, and the pair checks below it
are skipped rather than guessed at — which is exactly why `gate.py` puts its brass on the
card's own rule instead of washing the page with it.

A border is measured against what is BEHIND it, not against the fill it encloses: an edge
separates a box from the page. A border painted in the enclosing surface's own colour is a
cutout rather than an edge — the presence dot's ring is the one that needs it — so that case
passes without a pair.
"""

from __future__ import annotations

import ast
import json
import pathlib
import re
from collections.abc import Iterator
from typing import Any, get_args

import pytest
import reflex as rx

from brujula import app
from brujula.state import ProductCard, State
from brujula.ui import advice, chat, gate, product, theme, trace_panel
from coros_core.evidence import Confidence
from coros_core.models import AdviceKind

UI = pathlib.Path(theme.__file__).parent
MODULES = ("chat", "product", "advice", "trace_panel", "gate")

_DECL = re.compile(r'\["([^"]+)"\]\s*:\s*')
_HEX = re.compile(r"#[0-9A-Fa-f]{6}")
# `rx.foreach`'s row var reaches a field through optional chaining: `item_rx_state_?.["title"]`.
_ARG = re.compile(r"(\w+)_rx_state_\??\.?\[\"(\w+)\"\]")
_ESCAPED = re.compile(r"\\u([0-9a-fA-F]{4})")

# Props whose colours carry words, draw an edge, or become a surface.
_TYPE_PROP = ("color",)
_EDGE_PROP = "border"
_SURFACE_PROP = ("background", "backgroundColor")

_WORDS = ("Text", "Heading", "Link", "Button", "TextField")


def source(module: str) -> str:
    return (UI / f"{module}.py").read_text(encoding="utf-8")


def names_by_hex() -> dict[str, tuple[str, ...]]:
    """Colour value → every token that carries it.

    `CARD` and `ON_DARK` are both `#FFFFFF` and are deliberately different tokens: one is a
    surface, the other is the type that goes on dark fills. A value therefore resolves to a
    set of names, and a check passes when any one of them is declared for the pair.
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
    own: set[str] = set()
    pseudo: set[str] = set()
    opaque = False
    for prop, value, depth in decls(node):
        if prop not in _SURFACE_PROP:
            continue
        found = {name for name in tokens(value) if name in theme.SURFACES}
        if depth == 1:
            opaque = True
            own |= found
        else:
            pseudo |= found
    if opaque and not own:
        # A gradient, or an rgba fill: something is behind the words and it is not a token.
        return frozenset(pseudo)
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


def props_of(node: dict[str, Any]) -> Iterator[tuple[str, list[str]]]:
    yield str(node.get("name") or ""), [p for p in (node.get("props") or ()) if isinstance(p, str)]
    for child in kids(node):
        yield from props_of(child)


# The scalar keys that hold everything a browser is handed which is not a style prop: the
# rendered text, the state var a foreach walks, and the condition a branch turns on.
_SCALARS = ("contents", "iterable", "cond_state", "cond")


def flat(component: rx.Component) -> str:
    """The rendered tree as one searchable string, unescaped.

    `json.dumps` would be shorter and is wrong: it escapes every `"` in `role:"log"`, so a
    test looking for the prop it can see in the source never matches it here. Reflex escapes
    non-ASCII into the prop itself, so `Contraseña` arrives as `Contrase\\u00f1a` — undone
    here, because the whole interface is in Spanish and every second label carries an accent.
    """
    out: list[str] = []
    for node in nodes(component.render()):
        out.append(str(node.get("name") or ""))
        out += [p for p in (node.get("props") or ()) if isinstance(p, str)]
        out += [str(node[key]) for key in _SCALARS if key in node]
    return _ESCAPED.sub(lambda hit: chr(int(hit.group(1), 16)), "  ".join(out))


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
    """Every public surface, built the way `app.py` builds it.

    A row model is taken off the state var rather than instantiated: `rx.foreach` hands the
    component a Var, and a real `ProductCard` has no `.length()` on its list fields.
    """
    return {
        "chat.transcript": chat.transcript(),
        "chat.composer": chat.composer(),
        "chat.thinking": chat.thinking(),
        "chat.bubble": chat.bubble(State.messages[0]),
        "product.kit": product.kit(),
        "product.card": product.card(State.cards[0]),
        "advice.questions": advice.questions(),
        "advice.verdict": advice.verdict(),
        "advice.notes": advice.notes(),
        "advice.evidence": advice.evidence(),
        "trace_panel.panel": trace_panel.panel(top=app.HEADER_H),
        "gate.screen": gate.screen(),
        "app.header": app._header(),
        "app.column": app._column(),
        # The shell declares the page's own gradient, so walking it is what checks every
        # surface against PAPER_DEEP — the stop a card two thousand pixels down sits on.
        "app.shell": app._shell(),
    }


ENTRIES = entries()
PAGE = frozenset({"PAPER"})


class TestNothingIsPaintedWithAColourTheThemeNeverDeclared:
    @pytest.mark.parametrize("module", MODULES)
    def test_no_component_module_writes_a_colour_of_its_own(self, module: str) -> None:
        literals = _HEX.findall(source(module))
        assert not literals, (
            f"brujula/ui/{module}.py writes the colour literal(s) {literals}.\n"
            "Every colour is a token in brujula/ui/theme.py, where it carries the contrast\n"
            "ratio it was measured at. A literal here is a colour nobody measured, and\n"
            "tests/test_brujula_theme.py cannot see it to check it."
        )

    @pytest.mark.parametrize("label", sorted(ENTRIES))
    def test_every_colour_that_reaches_the_browser_is_a_token(self, label: str) -> None:
        strays = sorted(
            {hit for _, props in props_of(ENTRIES[label].render()) for p in props for hit in unknown(p)}
        )
        assert not strays, (
            f"{label} renders {strays}, which is not any token in brujula/ui/theme.py.\n"
            "Add it there with its measured ratio, or use the token that already exists."
        )


class TestNothingReadsAsWordsOnASurfaceNobodyMeasuredItOn:
    @pytest.mark.parametrize("label", sorted(ENTRIES))
    def test_type_sits_only_on_a_pair_the_theme_declares(self, label: str) -> None:
        bad = []
        for kind, node, value, surface in visit(ENTRIES[label].render(), PAGE):
            if kind == "edge":
                continue
            allowed = theme.TYPE_ON
            if kind == "glyph":
                # A glyph is not words: the non-text floor is 3:1, so an EDGE_ON pair clears
                # it, and SUB and RULE are declared for inert marks on any surface.
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
            + "\n".join(sorted(bad))
            + "\nEvery pair is declared in theme.TYPE_ON (words) or theme.EDGE_ON (marks and\n"
            "edges), and tests/test_brujula_theme.py recomputes the ratio for each one. Use a\n"
            "declared pair, or add the pair to theme.py with its measurement."
        )

    @pytest.mark.parametrize("label", sorted(ENTRIES))
    def test_a_border_clears_the_floor_against_what_is_behind_it(self, label: str) -> None:
        bad = []
        for kind, node, value, surface in visit(ENTRIES[label].render(), PAGE):
            if kind != "edge":
                continue
            named = tokens(value)
            if any(name in theme.RULE_ONLY or name == surface for name in named):
                continue
            floor = theme.EDGE_ON.get
            if not any(
                surface in floor(name, ()) + theme.TYPE_ON.get(name, ()) for name in named
            ):
                bad.append(f"  {node}: {value.strip()} against {surface}")
        assert not bad, (
            f"{label} draws an edge nobody can see, or one nobody measured:\n"
            + "\n".join(sorted(bad))
            + "\nAn edge is measured against the surface BEHIND it. theme.EDGE_ON declares the\n"
            "pairs that clear 3:1; RULE and SUB are the decorative hairlines, and a border in\n"
            "the enclosing surface's own colour is a cutout, not an edge."
        )


class TestTheRailIsASecondRegisterAndNothingTransfers:
    @staticmethod
    def imported(module: str) -> set[str]:
        tree = ast.parse(source(module))
        return {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("ui.theme")
            for alias in node.names
        }

    def test_the_rail_imports_no_colour_from_the_light_surfaces(self) -> None:
        light = {
            name
            for name in set(theme.TYPE_ON) | set(theme.SURFACES) | set(theme.EDGE_ON) | set(theme.RULE_ONLY)
            if not name.startswith("RAIL_")
        }
        leaked = sorted(self.imported("trace_panel") & light)
        assert not leaked, (
            f"trace_panel.py imports {leaked} from the light palette.\n"
            "The rail is the paper's negative and NONE of those tokens transfer — the two\n"
            "closest are BRASS on RAIL_BG 3.10:1 and DANGER_INK on RAIL_BG 3.00:1, both\n"
            "invisible there. theme.py's RAIL_* set is the rail's whole vocabulary."
        )

    @pytest.mark.parametrize("module", ("chat", "product", "advice", "gate"))
    def test_no_light_surface_reaches_for_a_rail_token(self, module: str) -> None:
        leaked = sorted(name for name in self.imported(module) if name.startswith("RAIL_"))
        assert not leaked, (
            f"brujula/ui/{module}.py imports {leaked}.\n"
            "The RAIL_* tokens are measured on the dark slab and nowhere else: RAIL_MUTED on\n"
            "PAPER is a smudge. The rail is the only module that reads them."
        )

    def test_the_bundles_own_english_is_shown_on_the_rail_and_nowhere_else(self) -> None:
        readers = [module for module in MODULES if reads(module, "blocking")]
        assert readers == ["trace_panel"], (
            f"State.blocking is rendered by {readers}.\n"
            "Those strings are the evidence bundle's own English — 'stock did not run' — an\n"
            "engineering artifact for a reviewer. loop.py already tells the person the same\n"
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
                "'No compres nada' and 'eso no se vende aquí' are different answers. Two\n"
                "panels that read the same are a paragraph with extra steps."
            )

    def test_only_the_two_hardest_sentences_are_allowed_the_red(self) -> None:
        red = {getattr(theme, name).upper() for name in theme.REFUSAL}
        spent = {
            kind
            for kind, spec in advice._KINDS.items()
            if red & {hit.upper() for hit in _HEX.findall(flat(advice._panel(spec)))}
        }
        assert spent == {"buy_nothing", "not_sold_locally"}, (
            f"the refusal red is spent on {sorted(spent)}.\n"
            "theme.py reserves #ea2e41 and its family for exactly two sentences — 'COROS\n"
            "Colombia no vende eso' and 'no compres nada'. A rate limit is not a refusal and\n"
            "a dead end is not a refusal either; they have their own amber and brass."
        )

    def test_the_panel_appears_only_when_the_kind_is_a_refusal(self) -> None:
        rendered = flat(advice.verdict())
        assert "is_refusal" in rendered, (
            "advice.verdict() does not branch on State.is_refusal.\n"
            "`advice_kind` is '' before the first turn and 'recommend' when there are cards,\n"
            "and a panel headlined 'No compres nada.' beside a kit is the disagreement the\n"
            "evidence bundle exists to prevent."
        )

    def test_the_devices_it_names_come_from_the_registry(self) -> None:
        rendered = flat(advice._panel(advice._KINDS["not_sold_locally"]))
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
        return {
            field
            for arg, field in _ARG.findall(flat(product.kit()))
            if arg == "item"
        }

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
        quoted = [
            node
            for node in nodes(product.card(State.cards[0]).render())
            if "rationale" in json.dumps(node.get("children"), default=str)
            and any(prop.startswith("border") for prop, _, _ in decls(node))
        ]
        assert quoted, (
            "the rationale is rendered without the quote rule down its left edge.\n"
            "Every other value on the card came off COROS's feed; that one is Brújula's own\n"
            "sentence, and the rule is what says so without a caption."
        )

    def test_a_product_with_no_photo_still_gets_a_card(self) -> None:
        rendered = flat(product.card(State.cards[0]))
        assert "image_url" in rendered and "Sin foto" in rendered, (
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

    def test_every_outcome_the_bundle_can_report_has_a_glyph(self) -> None:
        assert set(advice._OUTCOME_ICON) == set(theme.OUTCOME_COLOR), (
            f"advice._OUTCOME_ICON covers {sorted(advice._OUTCOME_ICON)}, theme.OUTCOME_COLOR "
            f"{sorted(theme.OUTCOME_COLOR)}.\n"
            "A row with a colour and no glyph, or a glyph and no colour, is a row whose\n"
            "outcome you have to guess at. 'not_run' in particular is not 'fail'."
        )

    def test_the_checklist_never_renders_the_bundles_own_detail(self) -> None:
        shown = {field for arg, field in _ARG.findall(flat(advice.evidence())) if arg == "check"}
        assert "detail" not in shown, (
            f"the checklist renders {sorted(shown)}.\n"
            "CheckRow.detail is the bundle's own English. The rail carries that register;\n"
            "this card is read by somebody who asked a question in Spanish."
        )


class TestTheGateIsOneDoorAndOneForm:
    def test_the_animated_class_is_not_a_var_on_the_form(self) -> None:
        for node in ast.walk(ast.parse(source("gate"))):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "form":
                assert not any(kw.arg == "class_name" for kw in node.keywords), (
                    "rx.form is handed a class_name.\n"
                    "rx.form is a Radix primitive and its render does `self.class_name or ''`,\n"
                    "which raises on a Var. The entrance and the shake go on a wrapper box."
                )

    def test_the_reveal_toggle_cannot_submit_the_form(self) -> None:
        rendered = flat(gate.screen())
        assert rendered.count('type:"submit"') == 1 and 'type:"button"' in rendered, (
            "the gate has more than one submit, or a button that did not opt out.\n"
            "A button inside a form submits it unless it says type='button', so the reveal\n"
            "toggle would send the password on the first click."
        )

    def test_the_password_field_is_named_for_a_screen_reader(self) -> None:
        assert '"aria-label":"Contraseña"' in flat(gate.screen()), (
            "the password field has no aria-label.\n"
            "A placeholder is not a label: it disappears the moment you type, and a screen\n"
            "reader announces the field as unnamed."
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

    def test_the_waiting_row_is_a_live_region_on_both_paths(self) -> None:
        rendered = flat(chat.thinking())
        assert rendered.count('role:"status"') == 2 and rendered.count('"aria-live":"polite"') == 2, (
            "one of the two waiting rows is not a live region.\n"
            "A turn runs for the better part of a minute and `status` changes several times\n"
            "inside it. Without a live region that whole minute is silent — and the throttled\n"
            "path is the one where the wait is longest."
        )

    def test_the_transcript_announces_itself_as_a_log(self) -> None:
        rendered = flat(chat.transcript())
        assert 'role:"log"' in rendered and 'role:"alert"' not in rendered, (
            "the transcript is not a log.\n"
            "The reply arrives asynchronously and nothing else on the page announces it. A\n"
            "log is an ordered running transcript; an alert would interrupt whatever the\n"
            "status region is in the middle of saying."
        )

    @pytest.mark.parametrize("branch", ("shell", "gate"))
    def test_each_branch_of_the_page_keeps_exactly_one_top_level_heading(self, branch: str) -> None:
        tree = app._shell() if branch == "shell" else gate.screen()
        count = flat(tree).count('as:"h1"')
        assert count == 1, (
            f"the {branch} branch renders {count} h1 headings.\n"
            "The header owns the one that is mounted for the whole session; the empty state's\n"
            "heading is an h2 because it unmounts the moment the first message lands."
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

    def test_the_collapsed_rail_says_what_it_will_open(self) -> None:
        rendered = flat(trace_panel.panel())
        assert f'"aria-controls":"{trace_panel.RAIL_ID}"' in rendered, (
            "the pill that reopens the rail does not name what it controls.\n"
            "aria-controls is what tells a screen reader the button and the panel are the\n"
            "same thing; the panel carries that id."
        )


class TestNothingIsWiderThanTheViewportItSitsIn:
    """A `100%` width and a horizontal margin on the same element is a sideways scrollbar.

    Found in production, not here: at 414 the live instance measured `scrollWidth` 415
    against `clientWidth` 399 — the audit rail was `width: 100%` with `margin: 0 1rem 1rem`,
    and a margin sits OUTSIDE the width, so the box is the container plus 32 px. Huella's
    rail never had it because it uses a border rather than a floating margin at mobile.

    The rendered-tree walk cannot see a computed layout, so this pins the pair of props
    that produces it, per breakpoint, which is the thing an author actually edits.
    """

    RESPONSIVE = ("width", "margin", "margin_left", "margin_right")

    @staticmethod
    def _components(node: Any) -> Iterator[Any]:
        """Every Component in the tree, including both arms of an rx.cond.

        Unlike the rendered-string walks elsewhere in this file, this one wants the live
        objects: the props are what an author edits and what the bug lived in, and a
        serialised style string would have to be re-parsed to find them again.
        """
        seen: set[int] = set()
        stack = [node]
        while stack:
            item = stack.pop()
            if id(item) in seen:
                continue
            seen.add(id(item))
            if isinstance(item, (list, tuple)):
                stack.extend(item)
                continue
            if not isinstance(item, rx.Component):
                continue
            yield item
            stack.extend(getattr(item, "children", None) or [])
            # rx.cond keeps its branches off `children`, and the mobile rail is one branch.
            for attr in ("comp1", "comp2", "cond_component"):
                branch = getattr(item, attr, None)
                if branch is not None:
                    stack.append(branch)

    @staticmethod
    def _plain(value: Any) -> Any:
        """Reflex wraps every style value in a Var, and `str()` on one is its repr.

        The first version of this test compared `str(width) == "100%"` against a
        `LiteralStringVar`, so it passed with the bug reinstated — vacuous in exactly the
        way it was written to catch. `_var_value` is the authored literal.
        """
        return getattr(value, "_var_value", value)

    @classmethod
    def _at(cls, value: Any, index: int) -> Any:
        """Reflex responsive props are lists indexed by breakpoint; a scalar applies to all."""
        if isinstance(value, (list, tuple)):
            if not value:
                return None
            return cls._plain(value[min(index, len(value) - 1)])
        return cls._plain(value)

    @staticmethod
    def _has_side_margin(margin: Any) -> bool:
        if not isinstance(margin, str) or not margin.strip():
            return False
        parts = margin.split()
        if len(parts) == 1:
            return parts[0] not in ("0", "0px", "0rem", "auto")
        # `top side bottom` and `top right bottom left` both put the sides at index 1.
        return parts[1] not in ("0", "0px", "0rem")

    @pytest.mark.parametrize("breakpoint_index", range(4))
    def test_no_full_width_box_also_carries_a_side_margin(self, breakpoint_index: int) -> None:
        offenders = []
        for name, node in (
            ("trace_panel.panel", trace_panel.panel("3rem")),
            ("trace_panel._collapsed", trace_panel.panel("3rem")),
            ("app._shell", app._shell()),
        ):
            for component in self._components(node):
                style = getattr(component, "style", None) or {}
                props = {**{k: getattr(component, k, None) for k in self.RESPONSIVE}, **dict(style)}
                width = self._at(props.get("width"), breakpoint_index)
                margin = self._at(props.get("margin"), breakpoint_index)
                if str(width) == "100%" and self._has_side_margin(margin):
                    offenders.append(f"{name}: width={width!r} margin={margin!r}")
        assert not offenders, (
            f"at breakpoint {breakpoint_index} these boxes are 100% wide AND side-margined:\n  "
            + "\n  ".join(sorted(set(offenders)))
            + "\nA margin is outside the width, so the box is its container plus the margins\n"
            "and the page scrolls sideways by exactly that much. Use width='auto' — a\n"
            "stretched flex item fills the line minus its own margins — or drop the margin\n"
            "and let the parent's padding do the inset."
        )
