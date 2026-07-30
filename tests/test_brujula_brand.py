"""Brújula's mark, and the three things its dot is allowed to say.

A dot in the corner of a logo is read as "the system is fine", so what lights it has to be
something true. Two of the three colours here are wired to state vars — a turn is running,
the storefront is throttling us — and the third is read off the environment at import: a
green dot over an app with no model key is the one lie this file could tell for free.

The rest is the drawing. Every colour the mark paints with has to be a token
`brujula/ui/theme.py` measured, every ratio this file writes down is recomputed here from
the two colours it names, and the halo is asserted to be the only thing that moves: a
compass whose needle sweeps on every question is both a second ambient animation and the
wrong claim about what Brújula does.
"""

from __future__ import annotations

import colorsys
import importlib
import io
import itertools
import os
import re
import tokenize
from collections.abc import Callable, Iterator
from types import ModuleType
from typing import Any

import pytest

from brujula import state
from brujula.ui import brand, theme
from test_brujula_theme import CLAIM, FIGURE, colours, ratio

NON_TEXT = 3.0
# What it takes for two 7px pips to be different pips. Either is enough on its own: green
# and brass sit 1.32:1 apart and nobody confuses them, because their hues do not.
TELLABLE_HUE = 25.0
TELLABLE_RATIO = 1.8

CLASS_NAME = re.compile(r'className:"([^"]*)"')
HEX_IN_PROPS = re.compile(r"#[0-9a-fA-F]{6}")
ROTATION = re.compile(r'transform:"(rotate\(-?\d+(?:\.\d+)? 16 16\))"')
# theme.py writes `TOKEN on SURFACE 4.95:1`. Two pips beside each other are neither on the
# other, so brand.py may also write `TOKEN vs TOKEN 1.17:1` — the same measurement, read as
# "these two are the same colour to the eye".
CLAIM_VS = re.compile(r"\b([A-Z][A-Z0-9_]{1,})\s+vs\s+([A-Z][A-Z0-9_]{1,})\s+(\d+\.\d\d):1")

# The surfaces the mark is placed on: the header and the gate card are CARD, the page and
# the gate's own band are PAPER. A dot has to read on both.
DOT_SURFACES = ("PAPER", "CARD")
DOTS = ("DOT_LIVE", "DOT_DEGRADED", "DOT_THINKING")


def hue_gap(first: str, second: str) -> float:
    def hue(value: str) -> float:
        channels = [int(value.lstrip("#")[i : i + 2], 16) / 255 for i in (0, 2, 4)]
        return colorsys.rgb_to_hls(*channels)[0] * 360

    return abs(hue(first) - hue(second))


def descendants(component: Any) -> Iterator[Any]:
    """Every node under one component, both branches of an `rx.cond` included.

    `rx.cond` renders as a Fragment holding a Cond whose two children are the branches, so
    walking `children` reaches the thinking dot and the idle one in the same pass — which is
    the only way to assert about a colour that is chosen in the browser.
    """
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
    return (brand.mark(), brand.wordmark(), brand.tagline())


def prose() -> list[str]:
    source = brand.__doc__ or ""
    comments = [
        token.string.lstrip("#").strip()
        for token in tokenize.generate_tokens(io.StringIO(_source()).readline)
        if token.type == tokenize.COMMENT
    ]
    return [source, *comments]


def _source() -> str:
    import inspect

    return inspect.getsource(brand)


@pytest.fixture
def reloaded() -> Iterator[Callable[[str], ModuleType]]:
    """Re-import `brand` under a chosen `GEMINI_API_KEY`, and put the module back after.

    `IDLE_DOT` is decided once, at import, the way DecaBot decides its own idle colour — so
    the only way to test both answers is to re-run the module. The teardown reload matters:
    a test that leaves the module holding the amber would hand every later test a mark that
    claims the app cannot answer.
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
        """Not that the three colours differ — that a person can see they differ.

        `DOT_DEGRADED` and `DOT_THINKING` are 1.17:1 apart in the same hue family, which is
        why the throttle pip is hollow: what is compared here is the disc a person actually
        looks at, so the amber's centre is `DOT_DEGRADED_FILL` and its rim does the rest.
        """
        painted = {
            "live": brand.DOT_LIVE,
            "degraded": brand.DOT_DEGRADED_FILL,
            "thinking": brand.DOT_THINKING,
        }
        same = []
        for (a, first), (b, second) in itertools.combinations(painted.items(), 2):
            apart = min(hue_gap(first, second), 360 - hue_gap(first, second))
            measured = ratio(first, second)
            if apart < TELLABLE_HUE and measured < TELLABLE_RATIO:
                same.append(f"{a} {first} and {b} {second}: {apart:.0f}° apart, {measured}:1")
        assert not same, (
            "two of the dot's states are the same pip to a person:\n  " + "\n  ".join(same) + "\n"
            f"A pair needs {TELLABLE_HUE:.0f}° of hue between them or {TELLABLE_RATIO}:1 of\n"
            "contrast. Same hue and same luminance is one dot with two meanings, and the\n"
            "person reading it has no way to know which one is on screen. A hollow pip —\n"
            "a light fill with the ink as its rim — is how the throttle gets out of this."
        )

    @pytest.mark.parametrize("dot", DOTS)
    @pytest.mark.parametrize("surface", DOT_SURFACES)
    def test_each_dot_colour_clears_the_non_text_floor_on_the_surfaces_it_sits_on(
        self, dot: str, surface: str
    ) -> None:
        measured = ratio(getattr(brand, dot), getattr(theme, surface))
        assert measured >= NON_TEXT, (
            f"brand.{dot} on {surface} measures {measured}:1, under WCAG 1.4.11's {NON_TEXT}:1.\n"
            "The dot carries the state on its own — there is no label beside it — so it is a\n"
            "non-text control, not decoration. Pick a token from theme.py that clears the\n"
            "floor on both PAPER and CARD, and update the ratio in brand.py's docstring."
        )

    def test_the_dot_is_wired_to_the_two_vars_that_could_make_it_lie(self) -> None:
        wired = " ".join(conditions(brand.mark()))
        for var in ("is_thinking", "throttled"):
            assert var in wired, (
                f"the mark renders no branch on State.{var} — conditions were {wired!r}.\n"
                "`is_thinking` is what earns the halo and `throttled` is what turns the dot\n"
                "amber. A dot hard-coded to one colour is chrome, not a status."
            )

    def test_there_is_no_fixture_replay_for_the_amber_to_mean_instead(self) -> None:
        assert not hasattr(state, "FIXTURE_MODE"), (
            "brujula.state now has a FIXTURE_MODE, and the dot's amber means the throttle.\n"
            "DecaBot's amber meant a fixture replay; here `fixtures/` is never read by the\n"
            "running app (AGENTS.md), so that amber could never light. If a replay mode is\n"
            "genuinely arriving, decide which of the two the amber says — and say the other\n"
            "one somewhere else, because one colour cannot carry both."
        )

    def test_a_mark_without_a_dot_asks_the_state_for_nothing(self) -> None:
        assert not conditions(brand.mark(dot=False)), (
            "mark(dot=False) still renders a Cond.\n"
            "The dotless mark is for the places that are not reporting status — a favicon\n"
            "source, a print header — and it should not subscribe a page to State at all."
        )


class TestTheNeedleDoesNotTurn:
    def test_the_halo_is_the_only_class_the_mark_reaches_for(self) -> None:
        used = {name for prop in props(brand.mark()) for name in CLASS_NAME.findall(prop)}
        assert used - {""} == {brand.HALO_CLASS}, (
            f"the mark renders classes {sorted(used)}.\n"
            f"Only {brand.HALO_CLASS!r} belongs here: it is the one thing a Reflex style prop\n"
            "cannot express, because it needs keyframes. Everything else is a style prop."
        )

    def test_nothing_here_asks_for_an_animation_or_a_transition(self) -> None:
        moving = [p for p in props(brand.mark()) if "animation" in p or "transition" in p]
        assert not moving, (
            "the mark declares motion inline:\n  " + "\n  ".join(moving) + "\n"
            "The halo is the app's only ambient animation and it lives in\n"
            "assets/brujula.css, where a prefers-reduced-motion block can switch it for a\n"
            "static ring. Motion written as a style prop cannot be turned off there."
        )

    def test_the_bearing_is_a_static_rotation_of_the_needle(self) -> None:
        rotations = {m for prop in props(brand.mark()) for m in ROTATION.findall(prop)}
        assert rotations == {f"rotate({brand.BEARING} 16 16)"}, (
            f"the mark's rotations are {sorted(rotations)}.\n"
            "The needle and its index mark share one static bearing about the dial's centre.\n"
            "Anything else is either a second bearing or a needle that moves."
        )

    def test_the_needle_is_off_north_because_it_is_taking_a_reading(self) -> None:
        assert 10 <= brand.BEARING <= 80, (
            f"BEARING is {brand.BEARING}°.\n"
            "On north the needle reads as an arrow — a direction somebody was given. Off\n"
            "north it reads as an instrument that took a bearing, which is the whole claim:\n"
            "Brújula answers by measuring, not by pointing."
        )

    def test_the_halo_class_is_brujulas_own_and_not_decabots(self) -> None:
        assert brand.HALO_CLASS.startswith("bj-"), (
            f"HALO_CLASS is {brand.HALO_CLASS!r}.\n"
            "assets/brujula.css owns the `bj-` namespace. DecaBot's `db-halo` pulses in its\n"
            "indigo, and two apps on one VPS that share a class name share a stylesheet bug."
        )


class TestTheMarkPaintsOnlyWithMeasuredTokens:
    def test_every_colour_the_lockup_renders_is_a_token_theme_measured(self) -> None:
        known = set(colours().values())
        painted = {
            value
            for component in lockup()
            for prop in props(component)
            for value in HEX_IN_PROPS.findall(prop)
        }
        loose = sorted(v for v in painted if v not in known and v.upper() not in {k.upper() for k in known})
        assert not loose, (
            f"{loose} are painted by brand.py and are not colours in theme.py.\n"
            "A colour mixed here is a colour nothing measures: theme.py is where a token\n"
            "carries its contrast ratio and its classification."
        )

    def test_no_refusal_colour_is_spent_on_the_lockup(self) -> None:
        refusal = {getattr(theme, name).upper() for name in theme.REFUSAL}
        painted = {
            value.upper()
            for component in lockup()
            for prop in props(component)
            for value in HEX_IN_PROPS.findall(prop)
        }
        assert not painted & refusal, (
            f"{sorted(painted & refusal)} is in the lockup and belongs to the refusal.\n"
            "Red says 'COROS Colombia no vende eso' and nothing else. On the mark it would\n"
            "read as an alarm about the app instead of an answer about the catalogue."
        )

    def test_every_ratio_this_file_states_is_the_one_its_colours_measure(self) -> None:
        known = colours()
        wrong = []
        for line in prose():
            for match in [*CLAIM.finditer(line), *CLAIM_VS.finditer(line)]:
                name, surface, stated = match[1], match[2], float(match[3])
                if name not in known or surface not in known:
                    wrong.append(f"{name} on {surface}: one of them is not a colour in theme.py")
                    continue
                measured = ratio(known[name], known[surface])
                if abs(measured - stated) > 0.01:
                    wrong.append(f"{name} on {surface}: says {stated}:1, measures {measured}:1")
        assert not wrong, (
            "brand.py states ratios its own tokens do not measure:\n  " + "\n  ".join(wrong) + "\n"
            "Recompute from theme.py's values, the same way tests/test_brujula_theme.py does."
        )

    def test_no_ratio_is_written_here_without_naming_the_two_colours(self) -> None:
        loose = []
        for line in prose():
            spans = [m.span() for m in [*CLAIM.finditer(line), *CLAIM_VS.finditer(line)]]
            for figure in FIGURE.finditer(line):
                if not any(start <= figure.start() and figure.end() <= end for start, end in spans):
                    loose.append(f"{figure.group()} in: {line.strip()[:90]}")
        assert not loose, (
            "brand.py states ratios nothing can check:\n  " + "\n  ".join(loose) + "\n"
            "Write them as `TOKEN on SURFACE 4.95:1`, or `TOKEN vs TOKEN 4.95:1` for two\n"
            "things beside each other. An unverifiable figure is indistinguishable from an\n"
            "invented one."
        )


class TestTheWordmarkIsOneWord:
    def test_the_accented_name_renders_as_a_single_run(self) -> None:
        assert texts(brand.wordmark()) == [brand.NAME], (
            f"the wordmark renders {texts(brand.wordmark())}.\n"
            "DecaBot tints the second half of its name because 'Deca|Bot' has a seam.\n"
            "'Brújula' has none: tinting the ú would cut the word into three inline boxes\n"
            "and put the seam inside a kerning pair. The mark carries the brass instead."
        )

    def test_the_wordmark_is_set_in_the_editorial_face_not_the_interface_one(self) -> None:
        rendered = " ".join(props(brand.wordmark()))
        assert theme.FONT_DISPLAY.split(",")[0] in rendered, (
            f"the wordmark's props do not name {theme.FONT_DISPLAY.split(',')[0]}: {rendered}\n"
            "Fraunces is Brújula's own voice and is reserved for the wordmark and the\n"
            "headings; Barlow stands in for COROS's own licensed DIN face everywhere else."
        )

    def test_the_vendors_name_is_the_one_thing_in_the_tagline_set_in_their_own_grey(self) -> None:
        assert texts(brand.tagline()) == [brand.ROLE, brand.VENDOR], (
            f"the tagline renders {texts(brand.tagline())}, not {[brand.ROLE, brand.VENDOR]}.\n"
            "The seam is real and it is the whole point: what we do, then whose catalogue we\n"
            "read. Collapsing it to one run loses the only place the two are distinguished."
        )
        rendered = " ".join(props(brand.tagline()))
        assert theme.GRAPHITE_DEEP in rendered and theme.QUIET in rendered, (
            f"the tagline is painted with {rendered}.\n"
            "COROS's name goes in COROS's own --color-primary-darker and ours in QUIET —\n"
            "the relationship stated in type: we read their catalogue, we are not them."
        )


class TestTheDialCarriesNoWordsOfItsOwn:
    def test_the_drawing_is_hidden_from_a_screen_reader_because_the_wordmark_says_the_name(
        self,
    ) -> None:
        svgs = [n for n in descendants(brand.mark()) if type(n).__name__ == "Svg"]
        assert svgs, "mark() renders no <svg> at all — the compass is drawn, not borrowed."
        for svg in svgs:
            assert '"aria-hidden":"true"' in svg.render()["props"], (
                f"the dial's svg is not aria-hidden: {svg.render()['props']}\n"
                "It sits beside the wordmark, so a reader that announces it says the name\n"
                "twice; and the dot's state is already in State.status, in words."
            )
