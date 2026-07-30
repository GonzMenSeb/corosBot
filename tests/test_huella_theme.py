"""Huella's palette, measured here rather than asserted there.

Same job as `tests/test_brujula_theme.py`, on the app that runs in the opposite register.
A colour comment is prose, and prose drifts: every ratio written down in
`huella/ui/theme.py` is recomputed here from the two tokens it names, so a figure that was
true when it was typed and is false now fails the build instead of reassuring a reader. A
bare figure is refused the same way — `4.95:1` with no two colours attached is
unverifiable, which is the shape a hallucinated ratio takes.

Coverage is the other half. `theme.TYPE_ON`, `theme.EDGE_ON`, `theme.SURFACES` and
`theme.RULE_ONLY` are the token file's own classification of what each colour is for, and a
token missing from all four is a token nobody measured.

Two things this suite checks that Brújula's cannot. **The two registers must not
transfer**: Huella has a full dark palette and a full light one, and a token that works on
both is a token in the wrong register — the light sheets exist precisely because none of
the instrument's tokens survive on white. And **Strava's orange is not ours**: it may not
be recoloured to reach a floor, it may not be the only thing carrying a meaning, and it is
close enough to COROS's red in hue that the two must never share a surface.
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

from coros_core import evidence, trace
from huella.ui import theme

AA = 4.5
NON_TEXT = 3.0

HEX = re.compile(r"\A#[0-9a-fA-F]{6}\Z")
CLAIM = re.compile(r"\b([A-Z][A-Z0-9_]{1,})\s+on\s+([A-Z][A-Z0-9_]{1,})\s+(\d+\.\d\d):1")
FIGURE = re.compile(r"\d+\.\d+\s*:\s*1")

# DecaBot's indigo and the surfaces mixed from it. Huella is a different product with a
# different vendor, and a shared hex is the fastest way for the two to read as one demo.
DECABOT = ("#3643BA", "#2E3998", "#272F76", "#F5F6FC", "#E7E8F7", "#151833", "#9AA3F5")

# The seven values COROS publishes on coros.com.co, each pinned to the role it plays here.
# Six are CSS custom properties; `#ea2e41` is a literal repeated across their own section
# styles as a call to action, and Huella spells it FLAG because the uncertainty flag is
# the one thing it is spent on.
COROS_ANCHORS = {
    "INK": "#161d25",
    "GRAPHITE": "#404040",
    "GRAPHITE_DEEP": "#212121",
    "SUB": "#9A9A9A",
    "RULE": "#EEEEE0",
    "FLAG": "#ea2e41",
    "SUCCESS": "#3a8735",
}

# Strava's own value, from their brand assets. It is a vendor's constant, not a choice of
# ours, which is why it is asserted rather than measured into place.
#
# Read 30 Jul 2026 out of the two bundles developers.strava.com/guidelines links —
# `1.1-Connect-with-Strava-Buttons.zip` and `1.2-Strava-API-Logos.zip`. All six orange SVGs
# in them contain exactly one colour, `#FC5200`; the horizontal "Powered by Strava" PNG
# decodes to `#FC5200` for every opaque non-black pixel; and the guidelines page names
# `#FC5200` as the link treatment. This file used to say `#FC4C02` — Strava's older orange,
# which is still what most of the web repeats. It was a memory, not a measurement.
STRAVA_ORANGE = "#FC5200"

# The two registers, named once. Everything about non-transfer is derived from these.
DARK_SURFACES = ("DASH", "INK", "INK_2")
LIGHT_SURFACES = ("SHEET", "SHEET_2")


def lum(value: str) -> float:
    h = value.lstrip("#")
    channels = [int(h[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    channels = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def ratio(a: str, b: str) -> float:
    """Four decimals, not two, and that is load-bearing rather than fussy.

    A floor is compared against this value. Rounded to the two decimals a comment states,
    anything in [2.995, 3.0) becomes exactly 3.0 and passes a `>= NON_TEXT` assertion it
    should fail — which is precisely where Strava's real orange lands on SHEET_2. Four
    places still sit well inside the 0.01 tolerance the stated-ratio comparison allows.
    """
    la, lb = lum(a), lum(b)
    return round((max(la, lb) + 0.05) / (min(la, lb) + 0.05), 4)


def hue(value: str) -> float:
    h = value.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))
    return colorsys.rgb_to_hls(r, g, b)[0] * 360


def apart(a: str, b: str) -> float:
    """Degrees between two hues the short way round."""
    gap = abs(hue(a) - hue(b))
    return min(gap, 360 - gap)


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
            "docs/VISUAL-BRIEF-HUELLA.md renders as the contrast table."
        )

    def test_every_rule_only_colour_says_what_it_is_instead(self) -> None:
        silent = sorted(name for name, reason in theme.RULE_ONLY.items() if len(reason.strip()) < 12)
        assert not silent, (
            f"{silent} are RULE_ONLY with no reason worth reading.\n"
            "The reason is the whole point: the next person to reach for one as type needs\n"
            "to find out from this file, not from a projector at a demo."
        )

    def test_every_semantic_well_has_an_ink_declared_on_it(self) -> None:
        wells = {name for name in theme.SURFACES if name.endswith("_WELL")}
        inked = {surface for _, surface in TYPE_PAIRS}
        mute = sorted(wells - inked)
        assert not mute, (
            f"{mute} are declared surfaces and no token in TYPE_ON sits on them.\n"
            "A well is a tint behind a sentence — 'this leans on thin data', 'the window is\n"
            "thick'. A well with no ink measured on it is a coloured box with unreadable\n"
            "words in it, which is the failure this whole file exists to prevent."
        )


class TestNothingThatReadsAsWordsIsUnderAA:
    @pytest.mark.parametrize(("name", "surface"), TYPE_PAIRS, ids=[f"{n}-on-{s}" for n, s in TYPE_PAIRS])
    def test_a_type_colour_clears_aa_on_every_surface_it_is_set_on(self, name: str, surface: str) -> None:
        measured = ratio(colours()[name], colours()[surface])
        assert measured >= AA, (
            f"{name} on {surface} measures {measured}:1, under AA's {AA}:1 for body text.\n"
            "On the instrument that means lighten the colour; on a sheet it means darken it.\n"
            "Either way move it to RULE_ONLY with the reason instead if it cannot get there,\n"
            "and update the ratio comment in huella/ui/theme.py and the table in\n"
            "docs/VISUAL-BRIEF-HUELLA.md in the same edit."
        )


class TestAnEdgeSomebodyHasToSeeClearsThreeToOne:
    @pytest.mark.parametrize(("name", "surface"), EDGE_PAIRS, ids=[f"{n}-on-{s}" for n, s in EDGE_PAIRS])
    def test_a_declared_edge_clears_the_non_text_floor(self, name: str, surface: str) -> None:
        measured = ratio(colours()[name], colours()[surface])
        assert measured >= NON_TEXT, (
            f"{name} on {surface} measures {measured}:1, under WCAG 1.4.11's {NON_TEXT}:1 for a\n"
            "control boundary, a focus ring or a status glyph. A row whose selected edge\n"
            "nobody can see is a row nobody knows is selected. Decorative hairlines belong\n"
            "in RULE_ONLY instead — that is what GRID is."
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
            "docs/VISUAL-BRIEF-BRUJULA.md — there is one implementation for both apps — and\n"
            "write the number the colours give."
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


class TestTheSubGreyInvertsBetweenTheTwoApps:
    def test_coros_own_sub_grey_carries_words_on_the_instrument(self) -> None:
        measured = ratio(theme.SUB, theme.INK)
        assert measured >= AA, (
            f"SUB on INK now measures {measured}:1, under AA. Brújula keeps this grey out of\n"
            "type because it fails on paper; Huella uses it for a column label because on a\n"
            "near-black it clears. If that stopped being true, INK drifted light — and the\n"
            "docstring's claim about the one anchor that inverts between the apps is stale."
        )

    def test_and_still_cannot_carry_them_on_a_sheet(self) -> None:
        measured = ratio(theme.SUB, theme.SHEET)
        assert measured < AA, (
            f"SUB on SHEET measures {measured}:1, which clears AA — so SHEET_QUIET's whole\n"
            "reason to exist is gone and the light half of this palette is not a second\n"
            "palette any more. Re-read the docstring before deleting anything."
        )

    def test_the_sheets_have_their_own_muted_ink_rather_than_borrowing_it(self) -> None:
        assert "SHEET_QUIET" in theme.TYPE_ON, (
            "SHEET_QUIET is the muted type colour for the light register. Removing it means\n"
            "something else carries muted copy on a card, and the only candidates are SUB\n"
            "(2.81:1 on white) and an instrument token that measures worse."
        )
        assert set(theme.TYPE_ON["SHEET_QUIET"]) <= set(LIGHT_SURFACES), (
            "SHEET_QUIET is declared on a surface outside the light register. A SHEET_* token\n"
            "on the instrument is the register confusion this file is organised to prevent."
        )


class TestRedIsReservedForTheUncertaintyMoment:
    def test_no_colour_outside_the_uncertainty_family_is_a_saturated_red(self) -> None:
        strays = []
        for name, value in colours().items():
            r, g, b = (int(value.lstrip("#")[i : i + 2], 16) / 255 for i in (0, 2, 4))
            degrees, light, sat = (
                hue(value),
                *colorsys.rgb_to_hls(r, g, b)[1:],
            )
            if (degrees >= 340 or degrees <= 14) and sat > 0.30 and 0.12 < light < 0.88:
                if name not in theme.UNCERTAINTY:
                    strays.append(f"{name}={value} (hue {degrees:.0f}°, sat {sat:.2f})")
        assert not strays, (
            "Red in Huella says 'do not lean on what is on screen', and these are not it:\n  "
            + "\n  ".join(strays) + "\n"
            "Five tokens already carry that answer between them. A sixth red is a second way\n"
            "of saying the same thing, and it lands on a delete or a badge that means nothing\n"
            "of the sort. Add it to UNCERTAINTY only if it really is that answer."
        )

    def test_the_uncertainty_family_is_named_and_real(self) -> None:
        assert theme.UNCERTAINTY, (
            "UNCERTAINTY is empty, so the test above can never fail — and never protects anything."
        )
        missing = sorted(set(theme.UNCERTAINTY) - set(colours()))
        assert not missing, f"{missing} are named in UNCERTAINTY and are not colours in theme.py."

    def test_the_flag_never_carries_the_sentence_itself(self) -> None:
        assert "FLAG" not in theme.TYPE_ON, (
            "FLAG is COROS's call-to-action red, and FLAG on INK 4.02:1 is under AA. It is\n"
            "the glyph and the rule; FLAG_INK says it in words on the instrument and\n"
            "SHEET_FLAG_INK on a sheet."
        )
        assert "FLAG" in theme.EDGE_ON, (
            "FLAG is in no registry that measures it. The uncertainty glyph is the one mark\n"
            "on the row carrying the answer when the label is read aloud, so it has to clear\n"
            "the non-text floor on every surface it is drawn on."
        )


class TestTheSheetsAreASecondPalette:
    @pytest.mark.parametrize(
        "name",
        sorted(n for n, s in theme.TYPE_ON.items() if set(s) & set(DARK_SURFACES)),
    )
    def test_no_instrument_type_colour_transfers_to_a_sheet(self, name: str) -> None:
        measured = ratio(colours()[name], theme.SHEET)
        assert measured < AA, (
            f"{name} on SHEET measures {measured}:1. If an instrument token now works on a\n"
            "card, the sheets stopped being a second register — check SHEET did not drift\n"
            "dark. The SHEET_* palette exists because none of these transfer; that is the\n"
            "whole reason it is a second palette and not a reuse."
        )

    @pytest.mark.parametrize(
        "name",
        sorted(n for n, s in theme.TYPE_ON.items() if set(s) & set(LIGHT_SURFACES)),
    )
    def test_no_sheet_type_colour_transfers_to_the_instrument(self, name: str) -> None:
        measured = ratio(colours()[name], theme.DASH)
        assert measured < AA, (
            f"{name} on DASH measures {measured}:1. A light-register token that reads on the\n"
            "instrument invites exactly the mistake this file is arranged to stop: a card's\n"
            "ink reused on a row because it 'looked fine' on one monitor."
        )

    def test_coros_own_button_grey_cannot_be_a_control_on_the_instrument(self) -> None:
        fill = ratio(theme.GRAPHITE, theme.DASH)
        border = ratio(theme.EDGE, theme.GRAPHITE)
        assert fill < NON_TEXT and border < NON_TEXT, (
            f"GRAPHITE on DASH measures {fill}:1 and EDGE on GRAPHITE {border}:1. The docstring\n"
            "says COROS's --color-primary is a light-register value because both are under the\n"
            "non-text floor — a fill nobody can see, and not rescuable with a border. If that\n"
            "changed, GRAPHITE can be an instrument control and the docstring is wrong."
        )
        for name in ("GRAPHITE", "GRAPHITE_DEEP"):
            on_it = {n for n, surfaces in theme.TYPE_ON.items() if name in surfaces}
            assert on_it <= {"ON_FILL"}, (
                f"{sorted(on_it)} are set on {name}. It is a button inside a sheet, and the only\n"
                "thing that goes on it is ON_FILL — an instrument token there is a token that\n"
                "measured well against the wrong background."
            )


class TestTheVendorsOrangeIsNotOurs:
    def test_strava_orange_is_the_value_strava_publishes(self) -> None:
        assert theme.STRAVA == STRAVA_ORANGE, (
            f"theme.STRAVA is {theme.STRAVA} and Strava's brand assets are {STRAVA_ORANGE}.\n"
            "This is a vendor constant, not a palette choice. Changing it means the hex in\n"
            "the file no longer matches the marks rendered beside it."
        )

    def test_no_second_orange_exists_to_be_mistaken_for_a_recoloured_one(self) -> None:
        band = sorted(
            f"{name}={value} (hue {hue(value):.0f}°)"
            for name, value in colours().items()
            if 14 < hue(value) < 30 and name != "STRAVA"
        )
        assert not band, (
            "theme.py holds another orange in Strava's own hue band:\n  " + "\n  ".join(band) + "\n"
            "Strava's assets may never be recoloured, so a second orange is either a copy of\n"
            "theirs that will drift or a darkened version of it that violates the brand terms.\n"
            "A word beside the mark is SHEET_TRACE or SHEET_QUIET."
        )

    def test_the_mark_has_a_declared_surface_it_is_legible_on_unmodified(self) -> None:
        assert "STRAVA" in theme.EDGE_ON, (
            "STRAVA is in no registry that measures it against a background. The attribution\n"
            "block, the Connect button and the View-on-Strava glyph all render the official\n"
            "assets, and the only lever we have is which surface we put them on — so that\n"
            "surface has to be declared and measured."
        )
        for surface in theme.EDGE_ON["STRAVA"]:
            measured = ratio(theme.STRAVA, colours()[surface])
            assert measured >= NON_TEXT, (
                f"STRAVA on {surface} measures {measured}:1, under the {NON_TEXT}:1 graphic floor.\n"
                "The fix is the surface, never the orange: the assets cannot be recoloured, so\n"
                "a mark that fails here has to move to a surface that clears it."
            )

    def test_the_mark_and_the_uncertainty_flag_never_share_a_surface(self) -> None:
        gap = apart(theme.STRAVA, theme.FLAG)
        clash = sorted(set(theme.EDGE_ON["STRAVA"]) & set(theme.EDGE_ON["FLAG"]))
        assert not clash, (
            f"STRAVA and FLAG are both declared on {clash}. They are {gap:.1f}° of hue apart and\n"
            f"STRAVA on FLAG measures {ratio(theme.STRAVA, theme.FLAG)}:1 — as two glyphs on one\n"
            "surface they are one glyph. Keep the vendor's mark on the sheets and the flag on\n"
            "the instrument, and keep the flag a sentence rather than a dot."
        )

    def test_what_makes_them_one_glyph_is_the_contrast_not_the_hue(self) -> None:
        """The tripwire this replaces asserted `gap < 25`, and Strava's real orange is 25.6°
        from FLAG — so it fired, correctly, on a claim that had gone stale.

        Re-read, the docstring's reason survives but rests on the other figure. Brújula's
        mark treats 25° of hue **or** 1.8:1 of contrast as tellable-apart; these two clear
        the hue bar by half a degree and fail the contrast one by a mile. At 1.28:1 two
        small marks are one mark whatever the angle between them, which is why the guarantee
        is the surfaces and not the separation.
        """
        contrast = ratio(theme.STRAVA, theme.FLAG)
        assert contrast < 1.8, (
            f"STRAVA and FLAG now measure {contrast}:1, past the 1.8:1 Brújula's brand suite\n"
            "treats as tellable-apart. That is the figure the never-share-a-surface rule\n"
            "rests on, so re-read huella/ui/theme.py's docstring before relying on it — the\n"
            f"hue gap ({apart(theme.STRAVA, theme.FLAG):.1f}°) was never what made them one glyph."
        )

    def test_the_inset_sheet_tint_is_excluded_because_it_measures_under_the_floor(self) -> None:
        """The one place the vendor's real orange costs us a surface.

        `#FC4C02` cleared SHEET_2 at 3.08:1 and `#FC5200` does not, by two thousandths. The
        exclusion is recomputed here rather than written in a comment because at the two
        decimals a comment states it rounds to exactly the floor and reads as passing.
        """
        measured = ratio(theme.STRAVA, colours()["SHEET_2"])
        assert measured < NON_TEXT, (
            f"STRAVA on SHEET_2 now measures {measured}:1 and clears {NON_TEXT}:1.\n"
            "Strava changed their orange, or SHEET_2 moved. Re-measure both, then decide\n"
            "whether the attribution block may sit on the inset tint after all — and update\n"
            "theme.EDGE_ON['STRAVA'] and the docstring together if so."
        )
        assert "SHEET_2" not in theme.EDGE_ON["STRAVA"], (
            f"STRAVA is declared on SHEET_2, where it measures {measured}:1 — under the\n"
            f"{NON_TEXT}:1 graphic floor. The fix is never the orange: the assets carrying it\n"
            "may not be recoloured, so the mark moves to SHEET or it does not render."
        )

    def test_the_file_says_the_orange_is_never_type_where_somebody_will_look(self) -> None:
        assert "STRAVA" in theme.RULE_ONLY, (
            "STRAVA belongs in RULE_ONLY. It is the one colour here we do not own: it cannot\n"
            "be darkened to reach AA, so the next person who reaches for it as a link colour\n"
            "has to find out from this file rather than from Strava's brand team."
        )


class TestTheInstrumentRendersEveryVerdictItCanReceive:
    def test_the_panels_name_a_colour_for_every_level_trace_can_emit(self) -> None:
        assert set(theme.LEVEL_COLOR) == set(get_args(trace.Level)), (
            "LEVEL_COLOR's keys and trace.Level have diverged. A level with no colour renders\n"
            "as whatever the last row left behind, and guardrail verdicts are the rows the\n"
            "trace panel exists to show."
        )
        assert set(theme.LEVEL_BG) == set(get_args(trace.Level)), (
            "LEVEL_BG must cover the same levels as LEVEL_COLOR."
        )

    @pytest.mark.parametrize("level", sorted(get_args(trace.Level)))
    def test_a_level_colour_is_readable_on_the_panel_it_is_read_on(self, level: str) -> None:
        measured = ratio(theme.LEVEL_COLOR[level], theme.INK)
        assert measured >= AA, (
            f"LEVEL_COLOR[{level!r}] measures {measured}:1 on INK, under AA's {AA}:1.\n"
            "The level is a word on the row, not a dot."
        )

    def test_the_checks_name_a_colour_for_every_outcome_the_bundle_can_report(self) -> None:
        assert set(theme.OUTCOME_COLOR) == set(get_args(evidence.Outcome)), (
            "OUTCOME_COLOR's keys and evidence.Outcome have diverged. `not_run` is a distinct\n"
            "answer from `fail` — a check that never ran is not a check that failed — and the\n"
            "panel has to be able to say so."
        )

    @pytest.mark.parametrize("outcome", sorted(get_args(evidence.Outcome)))
    def test_an_outcome_icon_clears_the_non_text_floor_on_a_panel(self, outcome: str) -> None:
        measured = ratio(theme.OUTCOME_COLOR[outcome], theme.INK)
        assert measured >= NON_TEXT, (
            f"OUTCOME_COLOR[{outcome!r}] measures {measured}:1 on INK, under {NON_TEXT}:1.\n"
            "These are the tick, the cross and the dash on the verification list, and they\n"
            "are the only thing carrying the outcome when the label is read aloud."
        )

    def test_every_confidence_the_bundle_can_report_has_a_colour_and_a_well(self) -> None:
        declared = set(get_args(evidence.Confidence))
        assert set(theme.CONFIDENCE_COLOR) == declared, (
            "CONFIDENCE_COLOR's keys and evidence.Confidence have diverged. Huella's whole\n"
            "reason to be a separate app is that it says how much the advice leans on the\n"
            "data; a confidence with no colour is that answer rendered as the last one."
        )
        assert set(theme.CONFIDENCE_BG) == declared, (
            "CONFIDENCE_BG must cover the same three answers as CONFIDENCE_COLOR — each is an\n"
            "ink on its own well, and an ink with no well is measured against nothing."
        )

    @pytest.mark.parametrize("level", sorted(get_args(evidence.Confidence)))
    def test_a_confidence_reads_on_its_own_well(self, level: str) -> None:
        measured = ratio(theme.CONFIDENCE_COLOR[level], theme.CONFIDENCE_BG[level])
        assert measured >= AA, (
            f"CONFIDENCE_COLOR[{level!r}] on CONFIDENCE_BG[{level!r}] measures {measured}:1, under\n"
            f"AA's {AA}:1. The chip carries a sentence about how thin the window is; a chip\n"
            "nobody can read is the flag not being raised."
        )

    def test_the_three_confidences_are_tellable_apart_by_hue(self) -> None:
        keys = sorted(theme.CONFIDENCE_COLOR)
        close = [
            f"{a} vs {b}: {apart(theme.CONFIDENCE_COLOR[a], theme.CONFIDENCE_COLOR[b]):.0f}°"
            for i, a in enumerate(keys)
            for b in keys[i + 1 :]
            if apart(theme.CONFIDENCE_COLOR[a], theme.CONFIDENCE_COLOR[b]) < 25
        ]
        assert not close, (
            "Two confidence answers are within Brújula's own tellable-apart separation:\n  "
            + "\n  ".join(close)
            + "\nThree shades of one hue is a gradient, and 'thick window' and 'this leans on\n"
            "thin data' are not points on a gradient — they are different sentences."
        )


class TestTheAnchorsAreTheOnesCorosPublishes:
    @pytest.mark.parametrize(("name", "value"), sorted(COROS_ANCHORS.items()))
    def test_each_coros_value_is_still_in_the_role_it_was_verified_for(self, name: str, value: str) -> None:
        assert getattr(theme, name).lower() == value.lower(), (
            f"theme.{name} is {getattr(theme, name)} and COROS publishes {value}.\n"
            "These seven come off coros.com.co's own stylesheet and are shared with\n"
            "brujula/ui/theme.py. Changing one is a claim about their brand, so re-read the\n"
            "live stylesheet and record it in AGENTS.md's facts registry in the same commit."
        )

    def test_none_of_decabots_indigo_survives_here(self) -> None:
        source = inspect.getsource(theme).upper()
        shared = sorted(hexes for hexes in DECABOT if hexes.upper() in source)
        assert not shared, (
            f"{shared} are DecaBot's colours. Two products on the same VPS that share a\n"
            "palette read as one product with two front doors. Huella's register is a dark\n"
            "instrument over COROS's monochrome; the indigo belongs to Decathlon."
        )

    def test_the_second_red_coros_publishes_is_still_unspent(self) -> None:
        # The docstring is free to name the value and say why it is unused; a token may not be it.
        spent = sorted(n for n, v in colours().items() if v.lower() == "#ff706b")
        assert not spent, (
            f"{spent} are COROS's --color-warning. It is a second red, and in an app whose\n"
            "one flag colour means 'this advice leans on thin or stale data' a second red is\n"
            "how a rate limit ends up looking like a warning about the data. AMBER_INK is the\n"
            "middle answer."
        )


class TestHuellaHasNoEditorialVoice:
    def test_brujulas_display_face_is_not_here(self) -> None:
        # The docstring explains the omission; the stacks are what may not name it.
        stacks = " ".join((theme.FONT, theme.FONT_DISPLAY, theme.MONO, theme.FONT_HREF))
        assert "Fraunces" not in stacks, (
            "Fraunces is Brújula's editorial voice. An instrument does not have one: a serif\n"
            "over a table of splits is a magazine pretending to be a dashboard. If Huella is\n"
            "getting a display face it needs a stated reason of its own, not Brújula's."
        )

    def test_the_display_face_is_the_interface_face(self) -> None:
        assert theme.FONT_DISPLAY == theme.FONT, (
            "FONT_DISPLAY has diverged from FONT. Committing to one family and letting weight\n"
            "and tracking do the work is the instrument-like choice and it is deliberate —\n"
            "a second family here is a decision that belongs in docs/DECISIONS.md."
        )

    def test_the_font_request_names_exactly_the_families_the_tokens_use(self) -> None:
        wanted = {"Barlow", "JetBrains+Mono"}
        asked = set(re.findall(r"family=([A-Za-z+]+)", theme.FONT_HREF))
        assert asked == wanted, (
            f"FONT_HREF requests {sorted(asked)} and theme.py's stacks name {sorted(wanted)}.\n"
            "A family in the stack and not the request falls back silently; a family in the\n"
            "request and not the stack is a blocking round trip for nothing."
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

    def test_the_radix_handoff_is_values_reflex_actually_accepts(self) -> None:
        assert theme.APPEARANCE == "dark", (
            "APPEARANCE is what rx.theme() is handed, and Huella is dark-primary. A light\n"
            "Radix appearance under this palette draws every component's insides in the\n"
            "opposite register to the page they sit on."
        )
        assert theme.RADIX_GRAY == "slate", (
            "RADIX_GRAY must stay a cool scale. Radix's warm greys (sand, olive) are what\n"
            "Brújula's paper uses, and a warm component interior on this blue-black page is\n"
            "the muddy inversion the docstring rejects."
        )
        assert theme.RADIX_SCALING in ("90%", "95%"), (
            "RADIX_SCALING carries the data density. Radix's own Literal allows up to 110%;\n"
            "a training history is a table of weeks and the rows have to fit."
        )
