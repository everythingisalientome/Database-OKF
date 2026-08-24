"""Hashing the C1 sample — the only form in which values leave the database.

The rule (CLAUDE.md, and specs/00 decision 2) is that production fingerprints
are KEYED: HMAC-SHA-256 under a vault-held deployment secret, truncated to
eight bytes, with the same key for every crawl because comparability requires
it. The key never appears in a bundle. An unkeyed hash of an enumerable value
space — sequential ids, known code lists, postcodes — is reversible by
exhaustion, so an unkeyed prod fingerprint is not a weaker fingerprint, it is
the raw values with extra steps.

Plain SHA-256 exists here for exactly one purpose: the fixture bundles under
``tests/fixtures/okf`` are committed to a public repository and have to be
reproducible by anyone reading them, so they are unkeyed and say so in their
``algo`` field. Fixture mode is opt-in per run and refuses to be the default;
a run without a key and without fixture mode raises rather than quietly
producing a bundle that claims to be keyed.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass

from .errors import ConfigError

#: Bytes of digest kept. Eight is the OKF vocabulary's ``/8B``.
TRUNCATION = 8

#: ``algo`` strings, the shared vocabulary of the markdown line and the
#: payload's own field (specs/04, file mechanics).
KEYED_ALGO = "hmac-sha256/8B"
UNKEYED_ALGO = "sha256/8B"


def _digest(value: str, key: bytes | None) -> str:
    encoded = value.encode("utf-8")
    if key is None:
        raw = hashlib.sha256(encoded).digest()
    else:
        raw = hmac.new(key, encoded, hashlib.sha256).digest()
    return raw[:TRUNCATION].hex()


@dataclass(frozen=True)
class Hasher:
    """How one crawl hashes values. Built once per run, from config."""

    key: bytes | None = None

    @property
    def algo(self) -> str:
        return UNKEYED_ALGO if self.key is None else KEYED_ALGO

    @property
    def keyed(self) -> bool:
        return self.key is not None

    def hash(self, value: str) -> str:
        return _digest(value, self.key)

    def hash_all(self, values) -> tuple[str, ...]:
        """Digests of ``values``, sorted.

        Sorted because the payload is a set and its file is committed: two
        crawls of unchanged data must produce byte-identical JSON or the
        refresh diff fills with reordering noise. Sorting also discards the
        selection order, which is a small mercy — that order is the engine's
        hash ranking, and it carries information about the values.
        """
        return tuple(sorted(self.hash(value) for value in values))


def hasher_for(config) -> Hasher:
    """The hasher for a crawl, or raise if the run cannot hash safely.

    ``fixture_mode`` is the only way to get an unkeyed hasher, and it is what
    the fixture bundles were built with. Everything else needs the key named
    by ``fingerprint_key_env`` to be present in the environment — which is
    how a vault gets it in, without it ever passing through a config file.
    """
    if config.fixture_mode:
        return Hasher(key=None)
    env_name = config.fingerprint_key_env
    if not env_name:
        raise ConfigError(
            "fingerprints are keyed: set fingerprint_key_env to the name of "
            "the environment variable holding the deployment key, or set "
            "fixture_mode for an unkeyed, reproducible crawl"
        )
    try:
        secret = os.environ[env_name]
    except KeyError:
        raise ConfigError(
            f"fingerprint_key_env names ${env_name}, which is not set; "
            "without the deployment key this crawl cannot produce "
            "comparable fingerprints"
        ) from None
    if not secret:
        raise ConfigError(f"${env_name} is set but empty")
    return Hasher(key=secret.encode("utf-8"))


__all__ = [
    "TRUNCATION",
    "KEYED_ALGO",
    "UNKEYED_ALGO",
    "Hasher",
    "hasher_for",
]
