"""Keyed hashing, and the refusal to produce an unkeyed bundle by accident.

The constraint (CLAUDE.md): fingerprints are HMAC-SHA-256 under a vault-held
key, truncated to eight bytes; plain sha256 only in fixture mode. The reason
is that the value spaces this crawler meets are enumerable — sequential
account ids, postcodes, two-letter status codes — and an unkeyed hash of an
enumerable space is the values back again after an afternoon of brute force.
"""

from __future__ import annotations

import hashlib
import hmac

import pytest

from crawler import ConfigError, MeasureSettings
from crawler.fingerprint import KEYED_ALGO, UNKEYED_ALGO, Hasher, hasher_for

ENV = "OKF_FINGERPRINT_KEY_TEST"


def settings(**kwargs) -> MeasureSettings:
    return MeasureSettings(**kwargs)


# -- the digests ------------------------------------------------------------


def test_the_unkeyed_digest_is_sha256_truncated_to_eight_bytes():
    digest = Hasher(key=None).hash("ROCK")
    assert digest == hashlib.sha256(b"ROCK").hexdigest()[:16]
    assert len(digest) == 16


def test_the_keyed_digest_is_hmac_under_the_deployment_key():
    hasher = Hasher(key=b"vault-secret")
    assert hasher.hash("ROCK") == hmac.new(
        b"vault-secret", b"ROCK", hashlib.sha256
    ).hexdigest()[:16]


def test_the_two_algos_disagree_about_every_value():
    """If they agreed anywhere, the key would not be doing anything."""
    values = [str(n) for n in range(100)]
    unkeyed = Hasher(key=None).hash_all(values)
    keyed = Hasher(key=b"vault-secret").hash_all(values)
    assert set(unkeyed).isdisjoint(keyed)


def test_a_different_key_gives_a_different_fingerprint():
    """Which is why the key is the same for every crawl in a deployment:
    comparability is the whole point of the fingerprint."""
    values = ["A", "B", "C"]
    assert Hasher(key=b"one").hash_all(values) != Hasher(key=b"two").hash_all(values)


def test_the_algo_string_says_which_was_used():
    assert Hasher(key=None).algo == UNKEYED_ALGO == "sha256/8B"
    assert Hasher(key=b"k").algo == KEYED_ALGO == "hmac-sha256/8B"


def test_digests_come_back_sorted():
    """The payload is a set and its file is committed: two crawls of
    unchanged data have to produce byte-identical JSON, or the refresh diff
    fills up with reordering noise."""
    digests = Hasher(key=None).hash_all(["zebra", "apple", "mango"])
    assert list(digests) == sorted(digests)


def test_unicode_hashes_by_its_utf8_bytes():
    assert Hasher(key=None).hash("Último") == hashlib.sha256(
        "Último".encode("utf-8")
    ).hexdigest()[:16]


# -- choosing a hasher from config ------------------------------------------


def test_fixture_mode_gives_an_unkeyed_hasher():
    hasher = hasher_for(settings(fixture_mode=True))
    assert not hasher.keyed
    assert hasher.algo == UNKEYED_ALGO


def test_a_configured_key_is_read_from_the_environment(monkeypatch):
    monkeypatch.setenv(ENV, "vault-secret")
    hasher = hasher_for(settings(fingerprint_key_env=ENV))
    assert hasher.keyed
    assert hasher.hash("A") == Hasher(key=b"vault-secret").hash("A")


def test_a_crawl_with_no_key_and_no_fixture_mode_refuses_to_run():
    """The failure this prevents is a production bundle full of unkeyed
    hashes that says ``hmac-sha256/8B`` on the tin."""
    with pytest.raises(ConfigError, match="fingerprints are keyed"):
        hasher_for(settings())


def test_a_key_variable_that_is_not_set_is_an_error(monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    with pytest.raises(ConfigError, match="not set"):
        hasher_for(settings(fingerprint_key_env=ENV))


def test_an_empty_key_variable_is_an_error(monkeypatch):
    monkeypatch.setenv(ENV, "")
    with pytest.raises(ConfigError, match="empty"):
        hasher_for(settings(fingerprint_key_env=ENV))


def test_fixture_mode_and_a_key_together_are_a_contradiction():
    with pytest.raises(ConfigError, match="contradict"):
        settings(fixture_mode=True, fingerprint_key_env=ENV)


def test_the_key_never_appears_in_anything_serialisable(monkeypatch):
    """It is a deployment secret; a bundle that carried it would undo the
    keying for everyone who can read the bundle."""
    monkeypatch.setenv(ENV, "vault-secret")
    config = settings(fingerprint_key_env=ENV)
    assert "vault-secret" not in repr(config.to_obj())
    assert config.to_obj()["fingerprint_key_env"] == ENV
