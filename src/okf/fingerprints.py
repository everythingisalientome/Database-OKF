"""Fingerprint payloads — the hashed value samples referenced from table files.

Payloads live outside the markdown (``/okf/db/<db>/fingerprints/...``) so the
markdown stays diffable, and they hold keyed hashes only: the HMAC key is
vault-held and never appears in a bundle, so a payload is irreversible even
for a guessable value space.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import ParseError

#: Keys every fingerprint payload must carry.
REQUIRED_KEYS = ("algo", "normalization", "count")

#: A payload holds exactly one of these, depending on its representation.
PAYLOAD_KEYS = ("hashes", "signature")


@dataclass
class Fingerprint:
    """A parsed fingerprint payload."""

    algo: str
    normalization: list[str]
    count: int
    hashes: list[str] | None = None
    signature: list[Any] | None = None
    sample_cap: int | None = None
    #: Any fields this version of the package does not model. Consumers must
    #: tolerate unknown fields (OKF v0.1); keeping them makes writes lossless.
    extra: dict[str, Any] = field(default_factory=dict)
    path: Any = None

    @classmethod
    def from_dict(cls, data: dict, *, path=None) -> Fingerprint:
        if not isinstance(data, dict):
            raise ParseError("fingerprint payload is not a JSON object", path=path)
        missing = [k for k in REQUIRED_KEYS if k not in data]
        if missing:
            raise ParseError(
                f"fingerprint payload is missing {', '.join(missing)}", path=path
            )
        if not any(k in data for k in PAYLOAD_KEYS):
            raise ParseError(
                f"fingerprint payload has neither {' nor '.join(PAYLOAD_KEYS)}",
                path=path,
            )
        known = set(REQUIRED_KEYS) | set(PAYLOAD_KEYS) | {"sample_cap"}
        return cls(
            algo=data["algo"],
            normalization=list(data["normalization"]),
            count=data["count"],
            hashes=data.get("hashes"),
            signature=data.get("signature"),
            sample_cap=data.get("sample_cap"),
            extra={k: v for k, v in data.items() if k not in known},
            path=path,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise, preserving the field order fixtures use."""
        out: dict[str, Any] = {"algo": self.algo, "normalization": self.normalization}
        if self.sample_cap is not None:
            out["sample_cap"] = self.sample_cap
        out["count"] = self.count
        if self.hashes is not None:
            out["hashes"] = self.hashes
        if self.signature is not None:
            out["signature"] = self.signature
        out.update(self.extra)
        return out

    @property
    def values(self) -> list:
        """The stored payload, whichever representation it uses."""
        return self.hashes if self.hashes is not None else (self.signature or [])

    @property
    def is_truncated(self) -> bool:
        """True when the sample cap bit and the payload is a partial slice."""
        return self.sample_cap is not None and self.count > self.sample_cap


def read_fingerprint(path) -> Fingerprint:
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ParseError(f"invalid JSON: {exc}", path=path) from None
    return Fingerprint.from_dict(data, path=path)


def write_fingerprint(fingerprint: Fingerprint, path=None) -> Path:
    target = Path(path if path is not None else fingerprint.path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(fingerprint.to_dict()), encoding="utf-8")
    return target
