"""Engine system schemas — excluded from every crawl, with or without config.

The catalog's schema-scope policy: crawl all schemas the service account can
see, narrow with config include/exclude when needed, and always drop the
engine's own system schemas without being asked. A dictionary crawl that
cataloged ``pg_catalog`` would bury eleven business tables under seventy
system ones and hand step 3 a semantic matching surface made of
``pg_attrdef``.

The exclusions are recorded rather than silent (:class:`crawler.results.Scope`)
so the scope of a bundle is auditable: "these schemas existed and were
deliberately skipped" is a different statement from "these schemas were not
there".

The lists are the catalog's, transcribed. Oracle and DB2 are here because the
policy covers them; their adapters arrive with their engines.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SystemSchemas:
    """How one engine spells its own internals."""

    #: Exact names, compared case-insensitively.
    exact: tuple[str, ...] = ()
    #: Name prefixes, compared case-insensitively (Teradata ``Sys*``).
    prefixes: tuple[str, ...] = ()
    #: Name suffixes, compared case-insensitively (Oracle ``*AUX``).
    suffixes: tuple[str, ...] = ()

    def matches(self, schema: str) -> bool:
        if not schema:
            return False
        folded = str(schema).strip().casefold()
        if any(folded == name.casefold() for name in self.exact):
            return True
        if any(folded.startswith(p.casefold()) for p in self.prefixes):
            return True
        return any(folded.endswith(s.casefold()) for s in self.suffixes)

    def describe(self) -> str:
        parts = [
            *self.exact,
            *(f"{p}*" for p in self.prefixes),
            *(f"*{s}" for s in self.suffixes),
        ]
        return "/".join(parts)


#: engine -> its system schemas, per the catalog's schema-scope policy.
#: PostgreSQL's pg_toast and pg_temp_* are absent on purpose: they are not in
#: the policy, and information_schema.tables does not return them anyway.
SYSTEM_SCHEMAS: dict[str, SystemSchemas] = {
    "postgres": SystemSchemas(exact=("pg_catalog", "information_schema")),
    "sqlserver": SystemSchemas(exact=("sys", "INFORMATION_SCHEMA")),
    "teradata": SystemSchemas(exact=("DBC",), prefixes=("Sys",)),
    "oracle": SystemSchemas(exact=("SYS", "SYSTEM"), suffixes=("AUX",)),
    "db2": SystemSchemas(exact=("SYSCAT", "SYSIBM", "SYSSTAT")),
}


def is_system_schema(engine: str, schema: str) -> bool:
    """True when ``schema`` is ``engine``'s own, and never crawled."""
    rule = SYSTEM_SCHEMAS.get(engine)
    return bool(rule and rule.matches(schema))


def describe(engine: str) -> str:
    """The excluded names for ``engine``, for the audit note."""
    rule = SYSTEM_SCHEMAS.get(engine)
    return rule.describe() if rule else ""
