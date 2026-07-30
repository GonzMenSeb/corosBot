"""The monorepo's shape is a contract, not a preference. Every assertion here failed
in production somewhere before it was a test."""

from __future__ import annotations

import configparser
import importlib
import pathlib
import re
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
APPS = ("brujula", "huella")
JENKINSFILE = ROOT / "infra" / "jenkins" / "Jenkinsfile"
SHIPPING_STAGES = ("Build & Push", "Deploy", "Health")


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


def jenkins_stages() -> dict[str, str]:
    """Split the Jenkinsfile on its top-level `stage('X') {` headers rather than brace-match
    it: the Deploy stage carries a Go template whose `{{...}}` a naive brace counter reads as
    two extra opens. There are no `parallel` blocks, so every stage sits at the same
    eight-space indent and a split on that is exact."""
    parts = re.split(r"\n {8}stage\('([^']+)'\) \{", JENKINSFILE.read_text())
    return dict(zip(parts[1::2], parts[2::2]))


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


class TestABranchBuildShipsNothing:
    """One Jenkinsfile serves two jobs that both fire on every push, so the only thing
    standing between a branch build and the VPS is the ON_MAIN gate. Groovy-parsed clean
    30 Jul 2026 (`groovy:4-jdk17`, `new GroovyShell().parse(...)`); these pin the semantics
    the parser cannot see."""

    @pytest.mark.parametrize("stage", SHIPPING_STAGES)
    def test_the_stage_is_gated_on_on_main(self, stage: str) -> None:
        body = jenkins_stages()[stage]
        assert "expression { env.ON_MAIN == 'true' }" in body, (
            f"The {stage} stage is not gated on ON_MAIN.\n"
            "Both jobs are cpsScm pipelineJobs pointing at this one scriptPath, so an\n"
            "ungated shipping stage pushes and deploys from whatever branch was built."
        )

    @pytest.mark.parametrize("stage", ("Test", "Live contract tests"))
    def test_the_test_stages_are_not_gated_on_on_main(self, stage: str) -> None:
        assert "ON_MAIN" not in jenkins_stages()[stage], (
            f"The {stage} stage is gated on ON_MAIN.\n"
            "A branch build must deploy nothing, but it must still run the suite — that is\n"
            "the whole value of building a branch at all."
        )

    def test_on_main_compares_refs_rather_than_reading_branch_name(self) -> None:
        text = JENKINSFILE.read_text()
        assert "refs/remotes/origin/main" in text, (
            "ON_MAIN is no longer derived by comparing HEAD to refs/remotes/origin/main.\n"
            "Measured in this workspace 30 Jul 2026: the comparison yields false on a\n"
            "feature branch and false again when the ref is missing, so it fails closed."
        )
        code = "\n".join(
            line for line in text.splitlines() if not line.lstrip().startswith("//")
        )
        assert not re.search(r"when\s*\{\s*branch\s", code), (
            "A `when { branch ... }` condition appeared in the Jenkinsfile.\n"
            "That matches on BRANCH_NAME, which only a multibranch job sets. In a cpsScm\n"
            "pipelineJob it is undefined, the condition never fires, and the build goes\n"
            "green having deployed nothing. DecaBot's build #1 did exactly that."
        )


class TestRollbackHasATargetToRollBackTo:
    """PREV_SHA is read off the running image's own label, so the label has to be written by
    the build, the tag it names has to still exist in the registry, and an unlabelled image
    has to be told apart from a labelled one."""

    LABEL = "org.opencontainers.image.revision"

    def test_the_build_writes_the_revision_label(self) -> None:
        assert f"--label {self.LABEL}=$GIT_SHA" in jenkins_stages()["Build & Push"], (
            f"The build no longer stamps {self.LABEL}.\n"
            "The Deploy stage reads that label off the running image to compute PREV_SHA.\n"
            "Unstamped, `docker inspect --format '{{index .Config.Labels ...}}'` returns an\n"
            "empty string — measured 30 Jul 2026 against an image built without it — so\n"
            "every deploy would report no rollback target."
        )

    def test_the_deploy_reads_that_label_to_compute_prev_sha(self) -> None:
        body = jenkins_stages()["Deploy"]
        assert "env.PREV_SHA" in body and self.LABEL in body, (
            f"The Deploy stage no longer derives PREV_SHA from {self.LABEL}.\n"
            "Reading it off the running image before the pull is what makes the rollback\n"
            "target the revision that was actually live, rather than a guess."
        )

    def test_every_build_pushes_the_sha_tag_beside_latest(self) -> None:
        body = jenkins_stages()["Build & Push"]
        assert "name=$IMAGE:$GIT_SHA,$IMAGE:latest" in body, (
            "The build no longer pushes :$GIT_SHA alongside :latest.\n"
            "PREV_SHA names a tag the rollback pulls back, so a build that only pushes\n"
            ":latest leaves the previous revision unreachable the moment it is replaced."
        )

    def test_the_two_names_stay_csv_quoted_for_buildx(self) -> None:
        assert r'--output type=image,\\"name=' in JENKINSFILE.read_text(), (
            "The escaped quotes came off the buildx --output value.\n"
            "Groovy turns \\\\\" in a '''...''' literal into \\\", the shell turns that into a\n"
            "real quote, and buildx parses --output as CSV where a comma-bearing field must\n"
            "be quoted. Measured 30 Jul 2026 against docker 28.3.2: quoted exports fine,\n"
            "unquoted fails `ERROR: invalid value <second name>`."
        )

    def test_an_unlabelled_previous_image_blocks_the_rollback_loudly(self) -> None:
        body = jenkins_stages()["Health"]
        assert "if (!env.PREV_SHA)" in body, (
            "The Health stage no longer checks PREV_SHA before rolling back.\n"
            "An unlabelled or absent image yields an empty string, and an unguarded\n"
            "rollback would `docker pull $IMAGE:` — a failure that reads as a registry\n"
            "problem rather than as the missing rollback target it is."
        )


