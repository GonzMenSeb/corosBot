"""The monorepo's shape is a contract, not a preference. Every assertion here failed
in production somewhere before it was a test."""

from __future__ import annotations

import configparser
import importlib
import pathlib
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
APPS = ("brujula", "huella")


def tracked() -> set[str]:
    out = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files"],
        capture_output=True,
        text=True,
        check=True,
    )
    return set(out.stdout.splitlines())


def pytest_ini() -> configparser.SectionProxy:
    parser = configparser.ConfigParser()
    parser.read(ROOT / "pytest.ini")
    return parser["pytest"]


def addopts() -> str:
    return pytest_ini()["addopts"]


def requirements() -> list[str]:
    return (ROOT / "requirements.txt").read_text().split()


class TestEachAppOwnsItsOwnReflexWorkingDirectory:
    @pytest.mark.parametrize("app", APPS)
    @pytest.mark.parametrize("lockfile", ("package.json", "bun.lock"))
    def test_the_reflex_lockfile_is_committed(self, app: str, lockfile: str) -> None:
        path = f"apps/{app}/reflex.lock/{lockfile}"
        assert path in tracked(), (
            f"{path} is not tracked by git.\n"
            "Reflex resolves reflex.lock/ against the current working directory, and the\n"
            f"Dockerfile does COPY reflex.lock/ — so a clean clone without this file fails\n"
            'the image build instantly with `failed to compute cache key: "/reflex.lock":\n'
            "not found`. It is a lockfile; it belongs in git. See AGENTS.md."
        )

    def test_the_two_apps_do_not_share_a_lock_directory(self) -> None:
        dirs = {(ROOT / "apps" / app / "reflex.lock").resolve() for app in APPS}
        assert len(dirs) == len(APPS), (
            "Both apps resolved to the same reflex.lock directory.\n"
            "Two Reflex apps in one tree would then overwrite each other's frontend\n"
            "dependency pins on every compile. Each app owns its own directory."
        )


class TestTheSharedCoreIsImportableWithoutAnApp:
    def test_coros_core_imports_on_its_own(self) -> None:
        assert importlib.import_module("coros_core") is not None, (
            "`import coros_core` failed.\n"
            "packages/ is a path root, not a package: pytest.ini's pythonpath puts it on\n"
            "sys.path. The container gets the same name because the Dockerfile copies\n"
            "packages/coros_core to /app/coros_core."
        )

    @pytest.mark.parametrize("app", APPS)
    def test_both_app_packages_import_from_the_repo_root(self, app: str) -> None:
        assert importlib.import_module(app) is not None, (
            f"`import {app}` failed from the repo root.\n"
            "One flat test suite drives both apps' state machines, so both app\n"
            "directories must be import roots. See pythonpath in pytest.ini."
        )


class TestPytestIniIsTheOnlyPlaceTheImportRootsAreDeclared:
    """Every caller resolves imports through pytest.ini, or a caller that forgets a root
    passes locally and fails in CI — which is exactly how CI broke on PR #1."""

    @pytest.mark.parametrize("root", ("packages", "apps/brujula", "apps/huella"))
    def test_the_root_is_declared(self, root: str) -> None:
        assert root in pytest_ini()["pythonpath"].split(), (
            f"{root} is missing from pythonpath in pytest.ini.\n"
            "Collection then dies with ModuleNotFoundError for whatever lives there, no\n"
            "matter which runner invoked pytest."
        )

    @pytest.mark.parametrize("path", (".github/workflows/test.yml", "infra/jenkins/Jenkinsfile"))
    def test_no_ci_definition_hand_rolls_pythonpath_for_pytest(self, path: str) -> None:
        file = ROOT / path
        if not file.exists():
            pytest.skip(f"{path} does not exist yet")
        offenders = [
            line.strip()
            for line in file.read_text().splitlines()
            if "PYTHONPATH" in line and "pytest" in line
        ]
        assert not offenders, (
            f"{path} sets PYTHONPATH on a pytest command:\n  " + "\n  ".join(offenders) + "\n"
            "A second copy of the import roots drifts from pytest.ini's — a partial copy\n"
            "(`PYTHONPATH=.`) still runs, it just cannot import the core or either app."
        )

    @pytest.mark.parametrize("target", ("check", "verify"))
    def test_the_make_target_invokes_pytest_the_way_ci_does(self, target: str) -> None:
        # -n resolves $(PY)-style indirection, which a grep over the Makefile would miss.
        recipe = subprocess.run(
            ["make", "-C", str(ROOT), "-n", target],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        assert "PYTHONPATH" not in recipe, (
            f"`make {target}` exports PYTHONPATH:\n  {recipe.strip()}\n"
            "Then a local run and a CI run resolve imports by different rules, and green\n"
            "here stops meaning green there. Import roots belong to pytest.ini alone."
        )


class TestTheOfflineSuiteIsOfflineByConstruction:
    def test_a_bare_pytest_run_excludes_the_live_marker(self) -> None:
        assert '-m "not live"' in addopts(), (
            "pytest.ini stopped excluding the live marker by default.\n"
            "Without it a bare `pytest` hits COROS and Strava, and CI — which has no\n"
            "credentials and no business making those calls — starts failing on their\n"
            "rate limits instead of on our code."
        )

    def test_a_misspelled_marker_is_an_error_not_a_silent_pass(self) -> None:
        assert "--strict-markers" in addopts(), (
            "pytest.ini stopped using --strict-markers.\n"
            "A typo'd @pytest.mark.livve then registers as no marker at all, and the test\n"
            "runs in the offline suite it was written to stay out of."
        )


class TestTheDependencyPinsAreTheOnesTheHostingFactsWereMeasuredOn:
    @pytest.mark.parametrize("app", APPS)
    def test_no_app_directory_ships_its_own_requirements_file(self, app: str) -> None:
        path = f"apps/{app}/requirements.txt"
        assert path not in tracked(), (
            f"{path} is tracked by git.\n"
            "`reflex init` writes one holding nothing but the reflex pin, and the first\n"
            "`reflex run` in a fresh clone triggers it. Committed, it shadows the root file\n"
            "for anything that installs from the app directory — an image with reflex and no\n"
            "google-genai, which fails at the first model call instead of at build time.\n"
            "It is gitignored; keep it that way."
        )

    @pytest.mark.parametrize("pin", ("reflex==0.9.7", "google-genai==2.14.0"))
    def test_the_pin_is_exact(self, pin: str) -> None:
        assert pin in requirements(), (
            f"{pin} is not pinned exactly in requirements.txt.\n"
            "Reflex 0.9.7 is what every deployment fact was measured against: the\n"
            "__REFLEX_-prefixed internal env vars, the .web/backend marker, granian as the\n"
            "prod server. Moving the pin means re-verifying all of them and updating\n"
            "AGENTS.md in the same commit."
        )
