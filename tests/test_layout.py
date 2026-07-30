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


def addopts() -> str:
    parser = configparser.ConfigParser()
    parser.read(ROOT / "pytest.ini")
    return parser["pytest"]["addopts"]


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
            "packages/ is a path root, not a package: run through `make check`, which puts\n"
            "packages/ on PYTHONPATH. The container gets the same name because the\n"
            "Dockerfile copies packages/coros_core to /app/coros_core."
        )

    @pytest.mark.parametrize("app", APPS)
    def test_both_app_packages_import_from_the_repo_root(self, app: str) -> None:
        assert importlib.import_module(app) is not None, (
            f"`import {app}` failed from the repo root.\n"
            "One flat test suite drives both apps' state machines, so both app\n"
            "directories must be on PYTHONPATH. See the PYPATH line in the Makefile."
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
    @pytest.mark.parametrize("pin", ("reflex==0.9.7", "google-genai==2.14.0"))
    def test_the_pin_is_exact(self, pin: str) -> None:
        assert pin in requirements(), (
            f"{pin} is not pinned exactly in requirements.txt.\n"
            "Reflex 0.9.7 is what every deployment fact was measured against: the\n"
            "__REFLEX_-prefixed internal env vars, the .web/backend marker, granian as the\n"
            "prod server. Moving the pin means re-verifying all of them and updating\n"
            "AGENTS.md in the same commit."
        )
