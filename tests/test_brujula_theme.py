"""Brújula's palette, measured here rather than asserted there.

A colour comment is prose, and prose drifts. Every ratio written down in
`brujula/ui/theme.py` is recomputed here from the two tokens it names, so a figure that
was true when it was typed and is false now fails the build instead of reassuring a
reader. The same scan refuses a bare figure: `4.95:1` with no two colours attached is
unverifiable, which is the shape a hallucinated ratio takes.

The other half is coverage. `theme.TYPE_ON`, `theme.EDGE_ON`, `theme.SURFACES` and
`theme.RULE_ONLY` are the token file's own classification of what each colour is for, and
a token missing from all four is a token nobody measured. AA (4.5:1) is the floor for
anything that reads as words and 3:1 for an edge somebody has to see; a colour that
cannot clear either is declared `RULE_ONLY` with the reason, the way `devices.py` rows
carry the sentence they were read from.
"""

from __future__ import annotations

import ast
import colorsys
import inspect
import io
import pathlib
import re
import tokenize
from typing import get_args

import pytest

from brujula.ui import theme
from coros_core import evidence, trace

AA = 4.5
NON_TEXT = 3.0

HEX = re.compile(r"\A#[0-9a-fA-F]{6}\Z")
CLAIM = re.compile(r"\b([A-Z][A-Z0-9_]{1,})\s+on\s+([A-Z][A-Z0-9_]{1,})\s+(\d+\.\d\d):1")
FIGURE = re.compile(r"\d+\.\d+\s*:\s*1")

# DecaBot's indigo and the surfaces mixed from it. Brújula is a different product with a
# different vendor, and a shared hex is the fastest way for the two to read as one demo.
DECABOT = ("#3643BA", "#2E3998", "#272F76", "#F5F6FC", "#E7E8F7", "#151833", "#9AA3F5")

# The seven values COROS publishes as CSS custom properties on coros.com.co, each pinned
# to the role it plays here. A palette is a claim about a brand; this is the evidence.
COROS_ANCHORS = {
    "INK": "#161d25",
    "GRAPHITE": "#404040",
    "GRAPHITE_DEEP": "#212121",
    "SUB": "#9A9A9A",
    "RULE": "#EEEEE0",
    "DANGER": "#ea2e41",
    "SUCCESS": "#3a8735",
}


def lum(value: str) -> float:
    h = value.lstrip("#")
    channels = [int(h[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    channels = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def ratio(a: str, b: str) -> float:
    la, lb = lum(a), lum(b)
    return round((max(la, lb) + 0.05) / (min(la, lb) + 0.05), 2)


def colours() -> dict[str, str]:
    return {n: v for n, v in vars(theme).items() if isinstance(v, str) and HEX.match(v)}


def prose() -> list[str]:
    """Everything in theme.py a person reads rather than runs.

    The `RULE_ONLY` reasons and the `SURFACES` labels are strings in code, not comments,
    and a stale ratio hides in one just as well — so they are scanned with the docstring
    and the comments.
    """
    source = inspect.getsource(theme)
    comments = [
        token.string.lstrip("#").strip()
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type == tokenize.COMMENT
    ]
    return [theme.__doc__ or "", *comments, *theme.RULE_ONLY.values(), *theme.SURFACES.values()]


def claims() -> list[tuple[str, str, float]]:
    return [
        (match[1], match[2], float(match[3])) for line in prose() for match in CLAIM.finditer(line)
    ]


def pairs(registry: dict[str, tuple[str, ...]]) -> list[tuple[str, str]]:
    return [(name, surface) for name, surfaces in registry.items() for surface in surfaces]


TYPE_PAIRS = pairs(theme.TYPE_ON)
EDGE_PAIRS = pairs(theme.EDGE_ON)


class TestEveryColourTokenSaysWhatItIsFor:
    def test_a_token_nobody_classified_is_a_token_nobody_measured(self) -> None:
        declared = set(theme.TYPE_ON) | set(theme.EDGE_ON) | set(theme.SURFACES) | set(theme.RULE_ONLY)
        loose = sorted(set(colours()) - declared)
        assert not loose, (
            f"{loose} are colours in theme.py that no registry claims.\n"
            "Put each one in TYPE_ON (words, with the surfaces it sits on), EDGE_ON (a line\n"
            "somebody has to see), SURFACES (a background) or RULE_ONLY (with the reason it\n"
            "is not type). An unclassified colour is one nothing in this file measures."
        )

    def test_nothing_is_declared_both_words_and_never_words(self) -> None:
        both = sorted(set(theme.TYPE_ON) & set(theme.RULE_ONLY))
        assert not both, (
            f"{both} are in TYPE_ON and in RULE_ONLY. RULE_ONLY is the file saying a colour\n"
            "cannot carry words; TYPE_ON is the file saying where it does. Pick one."
        )

    def test_every_declared_name_is_a_colour_that_exists(self) -> None:
        declared = set(theme.TYPE_ON) | set(theme.EDGE_ON) | set(theme.SURFACES) | set(theme.RULE_ONLY)
        ghosts = sorted(declared - set(colours()))
        assert not ghosts, (
            f"{ghosts} are classified in theme.py and are not opaque hex tokens there.\n"
            "A renamed or deleted colour leaves its classification behind, and the registry\n"
            "then measures something that is gone."
        )

    def test_every_surface_a_colour_names_is_declared_a_surface(self) -> None:
        unknown = sorted({s for _, s in TYPE_PAIRS + EDGE_PAIRS} - set(theme.SURFACES))
        assert not unknown, (
            f"{unknown} are named as backgrounds by TYPE_ON/EDGE_ON and are not in SURFACES.\n"
            "SURFACES is the list of things a token can legitimately sit on, and it is what\n"
            "docs/VISUAL-BRIEF-BRUJULA.md renders as the contrast table."
        )

    def test_every_rule_only_colour_says_what_it_is_instead(self) -> None:
        silent = sorted(name for name, reason in theme.RULE_ONLY.items() if len(reason.strip()) < 12)
        assert not silent, (
            f"{silent} are RULE_ONLY with no reason worth reading.\n"
            "The reason is the whole point: the next person to reach for one as type needs\n"
            "to find out from this file, not from a projector at a demo."
        )


class TestNothingThatReadsAsWordsIsUnderAA:
    @pytest.mark.parametrize(("name", "surface"), TYPE_PAIRS, ids=[f"{n}-on-{s}" for n, s in TYPE_PAIRS])
    def test_a_type_colour_clears_aa_on_every_surface_it_is_set_on(self, name: str, surface: str) -> None:
        measured = ratio(colours()[name], colours()[surface])
        assert measured >= AA, (
            f"{name} on {surface} measures {measured}:1, under AA's {AA}:1 for body text.\n"
            "Either darken the colour or move it to RULE_ONLY with the reason. Update the\n"
            "ratio comment in brujula/ui/theme.py and the table in\n"
            "docs/VISUAL-BRIEF-BRUJULA.md in the same edit."
        )


class TestAnEdgeSomebodyHasToSeeClearsThreeToOne:
    @pytest.mark.parametrize(("name", "surface"), EDGE_PAIRS, ids=[f"{n}-on-{s}" for n, s in EDGE_PAIRS])
    def test_a_declared_edge_clears_the_non_text_floor(self, name: str, surface: str) -> None:
        measured = ratio(colours()[name], colours()[surface])
        assert measured >= NON_TEXT, (
            f"{name} on {surface} measures {measured}:1, under WCAG 1.4.11's {NON_TEXT}:1 for a\n"
            "control boundary or a focus ring. A field whose edge nobody can see is a field\n"
            "nobody knows is focused. Decorative hairlines belong in RULE_ONLY instead."
        )


class TestEveryRatioWrittenDownIsTheMeasuredOne:
    def test_the_file_states_ratios_at_all(self) -> None:
        assert len(claims()) >= len(TYPE_PAIRS), (
            f"theme.py states {len(claims())} measured ratios for {len(TYPE_PAIRS)} type pairs.\n"
            "Every pair that carries words carries its ratio in a comment — that is the\n"
            "convention this file is built on."
        )

    def test_each_stated_ratio_is_what_the_two_colours_actually_measure(self) -> None:
        wrong = []
        for name, surface, stated in claims():
            known = colours()
            if name not in known or surface not in known:
                wrong.append(f"{name} on {surface}: one of them is not a colour in theme.py")
                continue
            measured = ratio(known[name], known[surface])
            if abs(measured - stated) > 0.01:
                wrong.append(f"{name} on {surface}: comment says {stated}:1, measures {measured}:1")
        assert not wrong, (
            "theme.py's comments disagree with its own colours:\n  " + "\n  ".join(wrong) + "\n"
            "A ratio is a measurement, not a memory. Recompute with the helper in\n"
            "docs/VISUAL-BRIEF-BRUJULA.md and write the number the colours give."
        )

    def test_no_ratio_is_written_without_naming_the_two_colours(self) -> None:
        loose = []
        for line in prose():
            spans = [m.span() for m in CLAIM.finditer(line)]
            for figure in FIGURE.finditer(line):
                if not any(start <= figure.start() and figure.end() <= end for start, end in spans):
                    loose.append(f"{figure.group()} in: {line.strip()[:90]}")
        assert not loose, (
            "theme.py states ratios nothing can check:\n  " + "\n  ".join(loose) + "\n"
            "Write them as `TOKEN on SURFACE 4.95:1` so this test recomputes them. An\n"
            "unverifiable figure is indistinguishable from an invented one."
        )

    def test_every_pair_that_carries_words_or_a_visible_edge_states_its_ratio(self) -> None:
        stated = {(name, surface) for name, surface, _ in claims()}
        silent = sorted(set(TYPE_PAIRS + EDGE_PAIRS) - stated)
        assert not silent, (
            f"{silent} are measured by this test and stated nowhere in theme.py.\n"
            "Every token that reads as words carries its measured ratio in a comment; the\n"
            "comment is what a designer reads before reaching for it."
        )


class TestTheSubGreyCorosPublishesIsNeverType:
    def test_coros_own_sub_grey_cannot_clear_aa_on_our_paper(self) -> None:
        measured = ratio(theme.SUB, theme.PAPER)
        assert measured < AA, (
            f"SUB on PAPER now measures {measured}:1, which clears AA — so the RULE_ONLY entry\n"
            "explaining why it is icon-and-rule-only is stale. Re-read it before deleting it."
        )

    def test_and_the_file_says_so_where_somebody_will_look(self) -> None:
        assert "SUB" in theme.RULE_ONLY, (
            "SUB is COROS's own secondary grey and the obvious reach for muted copy. It is\n"
            "2.7:1 on our paper, and a demo projector is worse than any monitor. Keep it in\n"
            "RULE_ONLY, and use QUIET for words."
        )


class TestRedIsReservedForTheRefusalMoment:
    def test_no_colour_outside_the_refusal_family_is_a_saturated_red(self) -> None:
        strays = []
        for name, value in colours().items():
            r, g, b = (int(value.lstrip("#")[i : i + 2], 16) / 255 for i in (0, 2, 4))
            hue, light, sat = colorsys.rgb_to_hls(r, g, b)
            degrees = hue * 360
            if (degrees >= 340 or degrees <= 14) and sat > 0.30 and 0.12 < light < 0.88:
                if name not in theme.REFUSAL:
                    strays.append(f"{name}={value} (hue {degrees:.0f}°, sat {sat:.2f})")
        assert not strays, (
            "Red is Brújula's honest-refusal colour and nothing else:\n  " + "\n  ".join(strays) + "\n"
            "Spending it on a button or a badge means 'COROS Colombia does not sell that'\n"
            "arrives in the same colour as a call to action. Add it to REFUSAL only if it\n"
            "really is part of that moment."
        )

    def test_the_refusal_family_is_named_and_real(self) -> None:
        assert theme.REFUSAL, "REFUSAL is empty, so the test above can never fail — and never protects anything."
        missing = sorted(set(theme.REFUSAL) - set(colours()))
        assert not missing, f"{missing} are named in REFUSAL and are not colours in theme.py."


class TestTheRailIsASecondRegister:
    @pytest.mark.parametrize("name", sorted(n for n, s in theme.TYPE_ON.items() if "PAPER" in s))
    def test_no_light_surface_type_colour_transfers_to_the_dark_rail(self, name: str) -> None:
        measured = ratio(colours()[name], theme.RAIL_BG)
        assert measured < AA, (
            f"{name} on RAIL_BG measures {measured}:1. If a paper token now works on the rail,\n"
            "the rail stopped being a second register — check RAIL_BG did not drift light.\n"
            "The rail exists because none of these transfer; that is why RAIL_* exists at all."
        )

    def test_the_rail_names_a_colour_for_every_level_trace_can_emit(self) -> None:
        assert set(theme.LEVEL_COLOR) == set(get_args(trace.Level)), (
            "LEVEL_COLOR's keys and trace.Level have diverged. A level with no colour renders\n"
            "as whatever the last row left behind, and guardrail verdicts are the rows the\n"
            "audit rail exists to show."
        )
        assert set(theme.LEVEL_BG) == set(get_args(trace.Level)), "LEVEL_BG must cover the same levels as LEVEL_COLOR."

    @pytest.mark.parametrize("level", sorted(get_args(trace.Level)))
    def test_a_level_colour_is_readable_on_the_rail_it_is_read_on(self, level: str) -> None:
        measured = ratio(theme.LEVEL_COLOR[level], theme.RAIL_BG)
        assert measured >= AA, (
            f"LEVEL_COLOR[{level!r}] measures {measured}:1 on RAIL_BG, under AA's {AA}:1.\n"
            "The level is a word on the row, not a dot."
        )

    def test_the_checks_name_a_colour_for_every_outcome_the_bundle_can_report(self) -> None:
        assert set(theme.OUTCOME_COLOR) == set(get_args(evidence.Outcome)), (
            "OUTCOME_COLOR's keys and evidence.Outcome have diverged. `not_run` is a distinct\n"
            "answer from `fail` — a check that never ran is not a check that failed — and the\n"
            "rail has to be able to say so."
        )

    @pytest.mark.parametrize("outcome", sorted(get_args(evidence.Outcome)))
    def test_an_outcome_icon_clears_the_non_text_floor_on_a_card(self, outcome: str) -> None:
        measured = ratio(theme.OUTCOME_COLOR[outcome], theme.CARD)
        assert measured >= NON_TEXT, (
            f"OUTCOME_COLOR[{outcome!r}] measures {measured}:1 on CARD, under {NON_TEXT}:1.\n"
            "These are the tick, the cross and the dash on the verification list, and they\n"
            "are the only thing carrying the outcome when the label is read aloud."
        )


class TestTheAnchorsAreTheOnesCorosPublishes:
    @pytest.mark.parametrize(("name", "value"), sorted(COROS_ANCHORS.items()))
    def test_each_coros_value_is_still_in_the_role_it_was_verified_for(self, name: str, value: str) -> None:
        assert getattr(theme, name).lower() == value.lower(), (
            f"theme.{name} is {getattr(theme, name)} and COROS publishes {value}.\n"
            "These seven come off coros.com.co's own CSS custom properties. Changing one is a\n"
            "claim about their brand, so re-read the live stylesheet and record it in\n"
            "AGENTS.md's facts registry in the same commit."
        )

    def test_none_of_decabots_indigo_survives_here(self) -> None:
        source = inspect.getsource(theme).upper()
        shared = sorted(hexes for hexes in DECABOT if hexes.upper() in source)
        assert not shared, (
            f"{shared} are DecaBot's colours. Two products on the same VPS that share a\n"
            "palette read as one product with two front doors. Brújula's register is warm\n"
            "paper over COROS's monochrome; the indigo belongs to Decathlon."
        )


class TestTheTokenFileStaysReadableFromAnywhere:
    def test_it_imports_nothing_and_defines_nothing(self) -> None:
        path = pathlib.Path(inspect.getfile(theme))
        tree = ast.parse(path.read_text(encoding="utf-8"))
        offenders = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "__future__":
                continue
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                offenders.append(f"line {node.lineno}: an import")
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                offenders.append(f"line {node.lineno}: {node.name}")
        assert not offenders, (
            "theme.py is flat module-level data and nothing else:\n  " + "\n  ".join(offenders) + "\n"
            "`rxconfig.py` is imported with sys.path cut down to its own directory, so a token\n"
            "file that imports anything is a token file the config can never read. Helpers go\n"
            "in the component modules that need them."
        )
