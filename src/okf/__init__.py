"""okf — read and write OKF bundles.

OKF is markdown + YAML frontmatter with path-as-identity: a bundle's
directory layout *is* its addressing scheme, and every content line carries a
provenance tag so a consumer can tell measurement from model guesswork from
human judgement. See specs/04-okf-conventions.md.

Reading is strict and lossless -- ``render(parse(text)) == text`` byte for
byte, comments and all -- because bundles are git-committed per refresh and
the diff between runs is the drift signal. A reformatting writer would drown
that signal in noise.

    >>> from okf import read_root, validate_root
    >>> root = read_root("okf")
    >>> issues = validate_root(root)

Layers, lowest first:
    frontmatter  -- the key/value header
    provenance   -- the [observed]/[inferred:conf]/[confirmed] tags
    documents    -- table / index / relationship models
    parse        -- read_document / write_document
    fingerprints -- the out-of-band hashed payloads
    bundle       -- directories of documents
    validate     -- the conventions, as checkable rules
"""

from .bundle import (
    Bundle,
    BundleRoot,
    DatabaseBundle,
    RelationshipBundle,
    canonical_pair_name,
    read_bundle,
    read_database_bundle,
    read_relationship_bundle,
    read_root,
)
from .documents import (
    BuiltFrom,
    Column,
    ContentLine,
    Document,
    Edge,
    FingerprintRef,
    IndexDocument,
    IndexEntry,
    RelationshipDocument,
    TableDocument,
)
from .errors import BundleError, OkfError, ParseError
from .fingerprints import Fingerprint, read_fingerprint, write_fingerprint
from .frontmatter import Field, FrontMatter
from .parse import parse_document, read_document, render_document, write_document
from .provenance import Provenance
from .validate import (
    SUPPRESSION_REASONS,
    Issue,
    errors,
    validate_bundle,
    validate_document,
    validate_index,
    validate_relationship,
    validate_root,
    validate_table,
)

__version__ = "0.1.0"

__all__ = [
    "Bundle", "BundleRoot", "DatabaseBundle", "RelationshipBundle",
    "canonical_pair_name", "read_bundle", "read_database_bundle",
    "read_relationship_bundle", "read_root",
    "BuiltFrom", "Column", "ContentLine", "Document", "Edge", "FingerprintRef",
    "IndexDocument", "IndexEntry", "RelationshipDocument", "TableDocument",
    "BundleError", "OkfError", "ParseError",
    "Fingerprint", "read_fingerprint", "write_fingerprint",
    "Field", "FrontMatter",
    "parse_document", "read_document", "render_document", "write_document",
    "Provenance",
    "SUPPRESSION_REASONS",
    "Issue", "errors", "validate_bundle", "validate_document", "validate_index",
    "validate_relationship", "validate_root", "validate_table",
    "__version__",
]
