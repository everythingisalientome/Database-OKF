"""Shared fixtures. The bundles under tests/fixtures/okf are ground truth:
tests assert the parser matches them, never the other way round."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).parent
PROJECT_ROOT = TESTS_DIR.parent
FIXTURE_ROOT = TESTS_DIR / "fixtures" / "okf"

sys.path.insert(0, str(PROJECT_ROOT / "src"))

from okf import read_root  # noqa: E402  (needs the path insert above)

#: Every fixture document, as (path, id) pairs for parametrisation.
FIXTURE_DOCUMENTS = sorted(FIXTURE_ROOT.rglob("*.md"))
FIXTURE_FINGERPRINTS = sorted(FIXTURE_ROOT.rglob("fingerprints/*.json"))


def _doc_id(path: Path) -> str:
    return path.relative_to(FIXTURE_ROOT).as_posix()


def pytest_generate_tests(metafunc):
    if "fixture_document" in metafunc.fixturenames:
        metafunc.parametrize(
            "fixture_document", FIXTURE_DOCUMENTS, ids=[_doc_id(p) for p in FIXTURE_DOCUMENTS]
        )
    if "fixture_fingerprint" in metafunc.fixturenames:
        metafunc.parametrize(
            "fixture_fingerprint",
            FIXTURE_FINGERPRINTS,
            ids=[_doc_id(p) for p in FIXTURE_FINGERPRINTS],
        )


@pytest.fixture(scope="session")
def fixture_root() -> Path:
    return FIXTURE_ROOT


@pytest.fixture(scope="session")
def root():
    """The whole fixture tree, parsed once."""
    return read_root(FIXTURE_ROOT)


@pytest.fixture(scope="session")
def db_bundles(root):
    return root.databases


@pytest.fixture(scope="session")
def rel_bundles(root):
    return root.relationships


@pytest.fixture(scope="session")
def all_bundles(root):
    return root.bundles
