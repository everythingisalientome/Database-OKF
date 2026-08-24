"""Canonical type vocabulary.

Every engine spells its types differently (``character varying`` /
``nvarchar`` / ``CV``). Step 2's type-compatibility gate and step 3's
expected-type filter both compare types across databases that may sit on
different engines, so the crawler records two things per column: the engine's
own word (``raw_type``, kept because it is the ground truth) and one
canonical rendering (``type``) drawn from the vocabulary below.

The vocabulary stays close to the engine words rather than folding into
families: ``DECIMAL`` and ``NUMERIC`` stay distinct, as do ``VARCHAR`` and
``NVARCHAR``, because a fold discards information the OKF reader cannot get
back. Only genuinely synonymous spellings collapse (``int4`` -> ``INT``,
``character varying`` -> ``VARCHAR``).
"""

from __future__ import annotations

#: Engine spellings that mean exactly the same type, lowercased.
ALIASES = {
    # integers
    "int": "INT",
    "int4": "INT",
    "integer": "INT",
    "bigint": "BIGINT",
    "int8": "BIGINT",
    "smallint": "SMALLINT",
    "int2": "SMALLINT",
    "tinyint": "TINYINT",
    "byteint": "BYTEINT",
    # exact numerics
    "decimal": "DECIMAL",
    "numeric": "NUMERIC",
    "money": "MONEY",
    "smallmoney": "SMALLMONEY",
    "number": "NUMBER",
    # approximate numerics
    "real": "REAL",
    "float": "FLOAT",
    "float4": "REAL",
    "float8": "DOUBLE PRECISION",
    "double": "DOUBLE PRECISION",
    "double precision": "DOUBLE PRECISION",
    # character
    "char": "CHAR",
    "character": "CHAR",
    "bpchar": "CHAR",
    "nchar": "NCHAR",
    "varchar": "VARCHAR",
    "varchar2": "VARCHAR",
    "character varying": "VARCHAR",
    "nvarchar": "NVARCHAR",
    "national character varying": "NVARCHAR",
    "text": "TEXT",
    "ntext": "TEXT",
    "clob": "CLOB",
    # binary
    "binary": "BINARY",
    "varbinary": "VARBINARY",
    "bytea": "VARBINARY",
    "blob": "BLOB",
    "byte": "BYTE",
    "varbyte": "VARBYTE",
    # temporal
    "date": "DATE",
    "time": "TIME",
    "time without time zone": "TIME",
    "time with time zone": "TIME WITH TIME ZONE",
    "timestamp": "TIMESTAMP",
    "timestamp without time zone": "TIMESTAMP",
    "timestamp with time zone": "TIMESTAMP WITH TIME ZONE",
    "datetime": "TIMESTAMP",
    "datetime2": "TIMESTAMP",
    "smalldatetime": "TIMESTAMP",
    "datetimeoffset": "TIMESTAMP WITH TIME ZONE",
    # other
    "boolean": "BOOLEAN",
    "bool": "BOOLEAN",
    "bit": "BIT",
    "uniqueidentifier": "UUID",
    "uuid": "UUID",
    "xml": "XML",
    "json": "JSON",
    "jsonb": "JSONB",
}

#: Canonical names that carry a single length argument.
LENGTH_TYPES = (
    "CHAR", "NCHAR", "VARCHAR", "NVARCHAR", "BINARY", "VARBINARY",
    "BYTE", "VARBYTE",
)

#: Canonical names that carry precision and scale.
PRECISION_TYPES = ("DECIMAL", "NUMERIC", "NUMBER")

#: SQL Server reports ``varchar(max)`` as character_maximum_length = -1.
MAX_LENGTH_SENTINEL = -1


def canonical_type(raw_type, *, length=None, precision=None, scale=None) -> str:
    """Render one engine type as its canonical string.

    ``canonical_type("character varying", length=160)`` -> ``VARCHAR(160)``.
    An unrecognised spelling is upper-cased and kept rather than dropped;
    losing it would hide a real column from step 3's type filter.
    """
    if raw_type is None:
        return "UNKNOWN"
    base = ALIASES.get(str(raw_type).strip().lower(), str(raw_type).strip().upper())
    if base in LENGTH_TYPES and length is not None:
        if int(length) == MAX_LENGTH_SENTINEL:
            return f"{base}(MAX)"
        return f"{base}({int(length)})"
    if base in PRECISION_TYPES and precision is not None:
        if scale is None:
            return f"{base}({int(precision)})"
        return f"{base}({int(precision)},{int(scale)})"
    return base
