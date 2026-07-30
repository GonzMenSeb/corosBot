"""Huella's mark, and the three things its dot is allowed to say.

A dot in the corner of a logo is read as "the system is fine", so what lights it has to be
something true. Two of the three colours here are wired to state vars — a turn is running,
we are on a partial read — and the third is read off the environment at import: a green dot
over an app with no model key is the one lie this file could tell for free.

The rest is the drawing, and Huella's is deliberately not Brújula's dial. This mark is the
trace: a plate with five readings plotted across it. Two things about that plot are load
bearing and are asserted here rather than trusted. It **falls before it rises**, because an
ascending zigzag is Strava's own silhouette and nothing in our mark may resemble any part of
their logo; and it is **never drawn in their orange**, which the accent is the far side of the
wheel from. A later hand tidying the path into a clean rise is how this regresses.

Every colour the mark paints with has to be a token `huella/ui/theme.py` measured, every ratio
this file writes down is recomputed here from the two colours it names, and the pulse on the
presence dot is asserted to be the only thing that moves — a mark whose needle sweeps is both a
second ambient animation and, for an app that reads what somebody already did, the wrong claim.

The walk is over the component objects rather than over `Component.render()`, which is the
opposite of `tests/test_huella_ui.py` and is right here for one reason: `rx.cond` renders as a
Fragment holding a Cond whose two children are the branches, so an object walk reaches the
thinking pip and the idle one in the same pass. There is no `rx.match` in this file for it to
lose.
"""

from __future__ import annotations

import ast
import colorsys
import importlib
import inspect
import io
import itertools
import os
import re
import tokenize
from collections.abc import Callable, Iterator
from types import ModuleType
from typing import Any

import pytest

from huella.ui import brand, theme
from test_huella_theme import CLAIM, FIGURE, colours, ratio

NON_TEXT = 3.0
# What it takes for two 7px pips to be different pips, and it is Brújula's bar rather than a
# new one: either is enough on its own. The accent and the amber sit 1.07:1 apart and are
# still two pips, because 145° of hue is not a distinction anybody has to squint at.
TELLABLE_HUE = 25.0
TELLABLE_RATIO = 1.8

CLASS_NAME = re.compile(r'className:"([^"]*)"')
HEX_IN_PROPS = re.compile(r"#[0-9a-fA-F]{6}")

# The surfaces the mark is placed on and the one it overlaps. `app.py` passes `surface=INK` in
# the header and in the gate card, and the pip straddles the plate's corner, which is INK_2.
DOT_SURFACES = ("INK", "INK_2")
DOTS = ("DOT_LIVE", "DOT_DEGRADED", "DOT_THINKING")


def hue_gap(first: str, second: str) -> float:
    def hue(value: str) -> float:
        channels = [int(value.lstrip("#")[i : i + 2], 16) / 255 for i in (0, 2, 4)]
        return colorsys.rgb_to_hls(*channels)[0] * 360

    gap = abs(hue(first) - hue(second))
    return min(gap, 360 - gap)


def descendants(component: Any) -> Iterator[Any]:
    """Every node under one component, both branches of an `rx.cond` included."""
    yield component
    for child in getattr(component, "children", ()) or ():
        yield from descendants(child)


def props(component: Any) -> list[str]:
    out = []
    for node in descendants(component):
        render = getattr(node, "render", None)
        if render is None:
            continue
        try:
            out.extend(render().get("props", ()) or ())
        except Exception:  # a Bare text node renders contents and no props
            continue
    return out


def conditions(component: Any) -> list[str]:
    return [
        str(node.render().get("cond_state", ""))
        for node in descendants(component)
        if type(node).__name__ == "Cond"
    ]


def texts(component: Any) -> list[str]:
    out = []
    for node in descendants(component):
        value = getattr(getattr(node, "contents", None), "_var_value", None)
        if isinstance(value, str) and value.strip():
            out.append(value)
    return out


def lockup() -> tuple[Any, ...]:
    return (brand.mark(), brand.wordmark(), brand.tagline(), brand.credit())


def _source() -> str:
    return inspect.getsource(brand)


def prose() -> list[str]:
    comments = [
        token.string.lstrip("#").strip()
        for token in tokenize.generate_tokens(io.StringIO(_source()).readline)
        if token.type == tokenize.COMMENT
    ]
    return [brand.__doc__ or "", *comments]


def imported() -> set[str]:
    return {
        alias.name
        for node in ast.walk(ast.parse(_source()))
        if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("ui.theme")
        for alias in node.names
    }


@pytest.fixture
def reloaded() -> Iterator[Callable[[str], ModuleType]]:
    """Re-import `brand` under a chosen `GEMINI_API_KEY`, and put the module back after.

    `IDLE_DOT` is decided once, at import — `gemini.api_key()` re-reads the environment every
    call, so the only way to test both answers is to re-run the module. The teardown reload
    matters: a test that left the module holding the amber would hand every later test a mark
    that claims the app cannot answer.
    """
    saved = os.environ.get("GEMINI_API_KEY")

    def load(key: str) -> ModuleType:
        os.environ["GEMINI_API_KEY"] = key
        return importlib.reload(brand)

    try:
        yield load
    finally:
        if saved is None:
            os.environ.pop("GEMINI_API_KEY", None)
        else:
            os.environ["GEMINI_API_KEY"] = saved
        importlib.reload(brand)


class TestTheDotSaysOnlyWhatIsTrue:
    def test_the_idle_dot_is_green_only_when_the_app_can_actually_answer(
        self, reloaded: Callable[[str], ModuleType]
    ) -> None:
        assert reloaded("a-key").IDLE_DOT == brand.DOT_LIVE, (
            "the idle dot is not green with a key configured.\n"
            "Green is the resting state: the app can reach the model and the catalogue."
        )
        assert reloaded("").IDLE_DOT == brand.DOT_DEGRADED, (
            "the idle dot stays green with GEMINI_API_KEY unset.\n"
            "Every turn dies at `gemini.client()` without it, so a green dot promises an\n"
            "answer the process cannot produce — and the demo finds out mid-question."
        )

    def test_the_three_answers_are_three_different_colours(self) -> None:
        chosen = {name: getattr(brand, name) for name in DOTS}
        assert len(set(chosen.values())) == len(DOTS), (
            f"{chosen} — two of the dot's three states share a colour.\n"
            "Live, degraded and working are three different claims about the answer being\n"
            "assembled, and the dot is the only thing making them."
        )

    def test_no_two_of_the_three_states_can_be_mistaken_for_each_other(self) -> None:
        """Not that the three colours differ — that a person can see they differ."""
        painted = {name: getattr(brand, name) for name in DOTS}
        same = []
        for (first_name, first), (second_name, second) in itertools.combinations(painted.items(), 2):
            gap, measured = hue_gap(first, second), ratio(first, second)
            if gap < TELLABLE_HUE and measured < TELLABLE_RATIO:
                same.append(f"{first_name} {first} and {second_name} {second}: {gap:.0f}°, {measured}:1")
        assert not same, (
            "two of the dot's states are the same pip to a person:\n  " + "\n  ".join(same) + "\n"
            f"A pair needs {TELLABLE_HUE:.0f}° of hue between them or {TELLABLE_RATIO}:1 of\n"
            "contrast — Brújula's bar, not a new one. Same hue and same luminance is one dot\n"
            "with two meanings, and the person reading it has no way to know which is on\n"
            "screen. Note where theme.py's warning about the vendor's orange applies and where\n"
            "it does not: that pair cleared the angle by half a degree, and the amber and the\n"
            "accent are 145° apart."
        )

    @pytest.mark.parametrize("dot", DOTS)
    @pytest.mark.parametrize("surface", DOT_SURFACES)
    def test_each_dot_colour_clears_the_non_text_floor_on_the_surfaces_it_straddles(
        self, dot: str, surface: str
    ) -> None:
        measured = ratio(getattr(brand, dot), getattr(theme, surface))
        assert measured >= NON_TEXT, (
            f"brand.{dot} on {surface} measures {measured}:1, under WCAG 1.4.11's {NON_TEXT}:1.\n"
            "The dot carries the state on its own — there is no label beside it — so it is a\n"
            "non-text control, not decoration. It is inset 5% and 18% wide, which puts it\n"
            "across the plate's bottom-right corner: INK_2 is the plate and INK is the surface\n"
            "the mark is handed, and it has to clear both. Pick a token from theme.py that\n"
            "does, and update the ratio in brand.py's docstring."
        )

    def test_the_dot_is_wired_to_the_two_vars_that_could_make_it_lie(self) -> None:
        wired = " ".join(conditions(brand.mark()))
        for var in ("is_thinking", "throttled"):
            assert var in wired, (
                f"the mark renders no branch on State.{var} — conditions were {wired!r}.\n"
                "`is_thinking` is what earns the pulse and `throttled` is what turns the dot\n"
                "amber. A dot hard-coded to one colour is chrome, not a status."
            )

    def test_the_throttle_outranks_the_turn(self) -> None:
        wired = conditions(brand.mark())
        assert len(wired) == 2 and "is_thinking" in wired[0] and "throttled" in wired[1], (
            f"the mark's conditions nest as {wired}.\n"
            "`throttled` is checked INSIDE `is_thinking`, exactly the way state.py's caption\n"
            "orders the two: being on a partial read — of COROS's catalogue or of Strava's\n"
            "quarter-hour window — is the more important thing to be saying while a turn runs.\n"
            "Swapped, the amber only ever appears when nothing is happening, which is when it\n"
            "means least."
        )

    def test_a_mark_without_a_dot_asks_the_state_for_nothing(self) -> None:
        assert not conditions(brand.mark(dot=False)), (
            "mark(dot=False) still renders a Cond.\n"
            "The dotless mark is for the places that are not reporting status — a favicon\n"
            "source, a print header — and it should not subscribe a page to State at all."
        )


class TestNothingInTheMarkMoves:
    def test_the_pulse_is_the_only_class_the_mark_reaches_for(self) -> None:
        used = {name for prop in props(brand.mark()) for name in CLASS_NAME.findall(prop)}
        assert used - {""} == {brand.PULSE_CLASS}, (
            f"the mark renders classes {sorted(used)}.\n"
            f"Only {brand.PULSE_CLASS!r} belongs here: it is the one thing a Reflex style prop\n"
            "cannot express, because it needs keyframes. Everything else is a style prop."
        )

    def test_nothing_here_asks_for_an_animation_or_a_transition(self) -> None:
        moving = [p for p in props(brand.mark()) if "animation" in p or "transition" in p]
        assert not moving, (
            "the mark declares motion inline:\n  " + "\n  ".join(moving) + "\n"
            "The pulse is the app's only ambient animation and it lives in\n"
            "assets/huella.css, where a prefers-reduced-motion block swaps it for a held ring.\n"
            "Motion written as a style prop cannot be answered there."
        )

    def test_nothing_in_the_mark_is_rotated(self) -> None:
        turned = [p for p in props(brand.mark()) if "rotate" in p.lower() or "transform" in p]
        assert not turned, (
            "the mark carries a transform:\n  " + "\n  ".join(turned) + "\n"
            "Brújula's dial has a bearing to hold; Huella points nowhere — it reads what\n"
            "somebody already did, and a rotating plate is the claim this app exists not to\n"
            "make. Had there been one it would go on a `<g>`: on a shape Reflex renders it\n"
            "into CSS, where `rotate(34 16 16)` is not valid syntax."
        )

    def test_the_pulse_class_is_huellas_own_and_not_brujulas(self) -> None:
        assert brand.PULSE_CLASS.startswith("hu-"), (
            f"PULSE_CLASS is {brand.PULSE_CLASS!r}.\n"
            "assets/huella.css owns the `hu-` namespace and assets/brujula.css the `bj-` one.\n"
            "Two apps on one VPS that share a class name share a stylesheet bug."
        )


class TestTheSeriesIsNotTheVendorsSilhouette:
    @staticmethod
    def plotted() -> list[float]:
        """The readings as a person sees them: SVG y grows downward, so a higher reading is a
        smaller number, and this flips it back."""
        return [-float(y) for _, y in brand._READINGS]

    def test_the_plot_falls_before_it_rises(self) -> None:
        values = self.plotted()
        steps = [b - a for a, b in zip(values, values[1:])]
        assert steps and steps[0] < 0, (
            f"the series opens {steps} — it does not descend first.\n"
            "An ascending zigzag is Strava's own silhouette and nothing in this mark may\n"
            "resemble any part of their logo. A real descent is also the honest drawing: this\n"
            "app reads a training history, and nobody's goes up every week."
        )
        assert any(step > 0 for step in steps), (
            f"the series never rises: {steps}.\n"
            "A monotone decline is a different wrong claim from a monotone climb, and it is\n"
            "the one that reads as a broken instrument."
        )

    def test_the_head_node_is_the_last_reading_rather_than_its_own_pair_of_numbers(self) -> None:
        assert (brand._HEAD_X, brand._HEAD_Y) == brand._READINGS[-1], (
            f"the head node sits at {(brand._HEAD_X, brand._HEAD_Y)} and the last reading is "
            f"{brand._READINGS[-1]}.\n"
            "It is derived from the series so a redrawn path cannot leave it behind — a ringed\n"
            "dot floating off the end of the line is the failure that has no error."
        )

    def test_the_series_is_never_drawn_in_the_vendors_orange(self) -> None:
        painted = {value.upper() for prop in props(brand.mark()) for value in HEX_IN_PROPS.findall(prop)}
        assert theme.STRAVA.upper() not in painted, (
            f"{theme.STRAVA} is painted by the mark.\n"
            "That orange is Strava's, it is declared on SHEET alone, and a plot drawn in it\n"
            "beside our own name is exactly the sponsorship their binding rules forbid us to\n"
            "imply. The accent is the far side of the wheel from it on purpose."
        )


class TestTheMarkPaintsOnlyWithMeasuredTokens:
    def test_every_colour_the_lockup_renders_is_a_token_theme_measured(self) -> None:
        known = {value.upper() for value in colours().values()}
        painted = {
            value.upper()
            for component in lockup()
            for prop in props(component)
            for value in HEX_IN_PROPS.findall(prop)
        }
        loose = sorted(painted - known)
        assert not loose, (
            f"{loose} are painted by brand.py and are not colours in theme.py.\n"
            "A colour mixed here is a colour nothing measures: theme.py is where a token\n"
            "carries its contrast ratio and its classification."
        )

    def test_the_mark_is_dark_register_only(self) -> None:
        light = sorted(
            name
            for name in imported()
            if name.startswith("SHEET") or name.startswith("GRAPHITE")
        )
        assert not light, (
            f"brand.py imports {light}.\n"
            "TRACE, AMBER_INK and SUCCESS are declared on DASH, INK and INK_2 and on neither\n"
            "sheet, so a mark handed `surface=SHEET` would paint pips nothing measured. The\n"
            "Strava attribution block is the app's one sheet and the lockup does not go on it."
        )

    def test_no_uncertainty_colour_is_spent_on_the_lockup(self) -> None:
        red = {getattr(theme, name).upper() for name in theme.UNCERTAINTY}
        painted = {
            value.upper()
            for component in lockup()
            for prop in props(component)
            for value in HEX_IN_PROPS.findall(prop)
        }
        assert not painted & red, (
            f"{sorted(painted & red)} is in the lockup and belongs to theme.UNCERTAINTY.\n"
            "Red in Huella says 'do not lean on what is on screen' — it is the confidence of\n"
            "the answer, never the health of the process. A red pip would say the advice is\n"
            "thin when what it meant was that a key is missing."
        )

    def test_every_ratio_this_file_states_is_the_one_its_colours_measure(self) -> None:
        known = colours()
        wrong = []
        for line in prose():
            for match in CLAIM.finditer(line):
                name, surface, stated = match[1], match[2], float(match[3])
                if name not in known or surface not in known:
                    wrong.append(f"{name} on {surface}: one of them is not a colour in theme.py")
                    continue
                measured = ratio(known[name], known[surface])
                if abs(measured - stated) > 0.01:
                    wrong.append(f"{name} on {surface}: says {stated}:1, measures {measured}:1")
        assert not wrong, (
            "brand.py states ratios its own tokens do not measure:\n  " + "\n  ".join(wrong) + "\n"
            "Recompute from theme.py's values, the same way tests/test_huella_theme.py does."
        )

    def test_no_ratio_is_written_here_without_naming_the_two_colours(self) -> None:
        loose = []
        for line in prose():
            spans = [match.span() for match in CLAIM.finditer(line)]
            for figure in FIGURE.finditer(line):
                if not any(start <= figure.start() and figure.end() <= end for start, end in spans):
                    loose.append(f"{figure.group()} in: {line.strip()[:90]}")
        assert not loose, (
            "brand.py states ratios nothing can check:\n  " + "\n  ".join(loose) + "\n"
            "Write them as `TOKEN on SURFACE 4.95:1` — two things beside each other included,\n"
            "which is the form theme.py uses for STRAVA on FLAG. An unverifiable figure is\n"
            "indistinguishable from an invented one."
        )


class TestTheWordmarkIsOneWordAndTheCreditIsTwo:
    def test_the_name_renders_as_a_single_run(self) -> None:
        assert texts(brand.wordmark()) == [brand.NAME], (
            f"the wordmark renders {texts(brand.wordmark())}.\n"
            "DecaBot tints the second half of its name because 'Deca|Bot' has a seam.\n"
            "'Huella' has none, and splitting it would put a seam inside a kerning pair."
        )

    def test_the_wordmark_is_the_pages_one_h1_unless_a_caller_says_otherwise(self) -> None:
        assert 'as:"h1"' in " ".join(props(brand.wordmark())), (
            "brand.wordmark() no longer defaults to an h1.\n"
            "It is the page's top-level heading in both branches of app.py — the shell and the\n"
            "gate — and `as_` exists so a second placement can step down rather than so the\n"
            "first can forget."
        )

    def test_there_is_no_second_family_for_the_wordmark_to_be_set_in(self) -> None:
        assert theme.FONT_DISPLAY == theme.FONT, (
            "theme.FONT_DISPLAY has diverged from theme.FONT, so the wordmark is now set in a\n"
            "display face. An instrument does not have an editorial voice: Brújula's Fraunces\n"
            "is Brújula's, and here the weight and the tracking do that work."
        )
        assert theme.FONT_DISPLAY.split(",")[0] in " ".join(props(brand.wordmark())), (
            f"the wordmark's props do not name {theme.FONT_DISPLAY.split(',')[0]}.\n"
            "Barlow stands in for COROS's own licensed PF Din Text Pro, and the wordmark is\n"
            "the one place a fallback would be visible beside their storefront."
        )

    def test_the_credit_keeps_the_seam_between_what_we_do_and_whose_catalogue_we_read(self) -> None:
        assert texts(brand.credit()) == [brand.ROLE, brand.VENDOR], (
            f"the credit renders {texts(brand.credit())}, not {[brand.ROLE, brand.VENDOR]}.\n"
            "The seam is the whole point: what we do, then whose catalogue we read. Collapsing\n"
            "it to one run loses the only place the two are distinguished."
        )
        rendered = " ".join(props(brand.credit()))
        assert theme.READOUT in rendered and theme.SUB in rendered, (
            f"the credit is painted with {rendered}.\n"
            "Brújula sets COROS's name in COROS's own --color-primary-darker; here that token\n"
            "is a sheet button and nothing else, so the seam is the step from SUB up to\n"
            "READOUT instead. Theirs is the strong one, ours is the quiet one."
        )

    def test_the_role_we_claim_is_equipment_and_not_coaching(self) -> None:
        assert "entren" not in brand.ROLE.lower(), (
            f"brand.ROLE is {brand.ROLE!r}.\n"
            "Huella reads training and recommends equipment; it does not coach anybody. A role\n"
            "that said 'asesor de entrenamiento' is the claim this app is likeliest to be\n"
            "believed about and least able to stand behind — the tagline is where training is\n"
            "named, and it names it as something the athlete already did."
        )

    def test_the_tagline_is_a_sentence_rather_than_an_eyebrow(self) -> None:
        assert texts(brand.tagline()) == [brand.TAGLINE], (
            f"the tagline renders {texts(brand.tagline())}, not [{brand.TAGLINE!r}]."
        )
        assert "textTransform" not in " ".join(props(brand.tagline())), (
            "the tagline is transformed.\n"
            "It keeps its case and its accent: 'ya demostró' is the whole difference between\n"
            "this app and one that asks, and uppercasing it strips the accent's meaning along\n"
            "with the sentence's."
        )


class TestThePlateCarriesNoWordsOfItsOwn:
    def test_the_drawing_is_hidden_from_a_screen_reader_because_the_wordmark_says_the_name(
        self,
    ) -> None:
        svgs = [node for node in descendants(brand.mark()) if type(node).__name__ == "Svg"]
        assert svgs, "mark() renders no <svg> at all — the plate is drawn, not borrowed."
        for svg in svgs:
            assert '"aria-hidden":"true"' in svg.render()["props"], (
                f"the plate's svg is not aria-hidden: {svg.render()['props']}\n"
                "It sits beside the wordmark, so a reader that announces it says the name\n"
                "twice; and the dot's state is already in State.status, in words."
            )
