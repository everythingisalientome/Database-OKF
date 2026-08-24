"""Bundle-level reading and writing.

Layout (specs/04-okf-conventions.md):

    /okf/db/<dbname>/index.md
    /okf/db/<dbname>/<schema>/<table>.md
    /okf/db/<dbname>/fingerprints/...
    /okf/rel/<dbA>--<dbB>/index.md
    /okf/rel/<dbA>--<dbB>/<tableA>--<tableB>.md

Path is identity: a bundle's directory name *is* its database name (or its
sorted database pair), so nothing has to agree with a registry to be found.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .documents import (
    Document,
    FingerprintRef,
    IndexDocument,
    RelationshipDocument,
    TableDocument,
)
from .errors import BundleError
from .fingerprints import Fingerprint, read_fingerprint
from .parse import read_document, write_document

INDEX_FILENAME = "index.md"
FINGERPRINT_DIRNAME = "fingerprints"
DB_DIRNAME = "db"
REL_DIRNAME = "rel"
PAIR_SEPARATOR = "--"


def _child_documents(root: Path) -> list[Path]:
    """Every markdown child of a bundle, index excluded, in stable order."""
    return sorted(
        p for p in root.rglob("*.md")
        if p.name != INDEX_FILENAME and FINGERPRINT_DIRNAME not in p.relative_to(root).parts
    )


def _rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


@dataclass
class Bundle:
    """Common base: a directory with an index.md and typed children."""

    root: Path
    index: IndexDocument
    children: list[Document] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.root.name

    @property
    def completeness(self) -> str | None:
        return self.index.completeness

    @property
    def is_complete(self) -> bool:
        return self.index.is_complete

    def child_paths(self) -> list[str]:
        """Bundle-relative posix paths of the child documents, as index entries
        should reference them."""
        return [_rel(self.root, Path(c.path)) for c in self.children]

    @property
    def needs_review(self):
        """Every line the conventions put in the HITL queue, across the bundle."""
        out = list(self.index.needs_review)
        for child in self.children:
            out.extend(child.needs_review)
        return out

    def write(self, root=None) -> Path:
        """Write index and children. Child paths are kept bundle-relative."""
        target = Path(root) if root is not None else self.root
        target.mkdir(parents=True, exist_ok=True)
        write_document(self.index, target / INDEX_FILENAME)
        for child in self.children:
            write_document(child, target / _rel(self.root, Path(child.path)))
        return target


@dataclass
class DatabaseBundle(Bundle):
    """Step 1 output: one database."""

    @property
    def database(self) -> str:
        return self.index.database or self.root.name

    @property
    def tables(self) -> list[TableDocument]:
        return list(self.children)

    @property
    def fingerprint_dir(self) -> Path:
        return self.root / FINGERPRINT_DIRNAME

    def table(self, name: str) -> TableDocument | None:
        """Look up by ``<schema>.<table>`` or by bare table name."""
        for doc in self.tables:
            if doc.name == name or doc.table == name:
                return doc
        return None

    def resolve(self, ref: FingerprintRef | str) -> Path:
        """Resolve a fingerprint reference against the bundle root."""
        path = ref.path if isinstance(ref, FingerprintRef) else ref
        return self.root / path

    def fingerprint(self, ref: FingerprintRef | str) -> Fingerprint:
        return read_fingerprint(self.resolve(ref))

    def fingerprint_refs(self) -> list[tuple[TableDocument, object, FingerprintRef]]:
        out = []
        for doc in self.tables:
            for column, ref in doc.fingerprint_refs:
                out.append((doc, column, ref))
        return out

    def orphan_fingerprints(self) -> list[Path]:
        """Payload files on disk that no table file references."""
        if not self.fingerprint_dir.is_dir():
            return []
        referenced = {self.resolve(ref).resolve() for _, _, ref in self.fingerprint_refs()}
        return sorted(
            p for p in self.fingerprint_dir.iterdir()
            if p.is_file() and p.suffix == ".json" and p.resolve() not in referenced
        )


@dataclass
class RelationshipBundle(Bundle):
    """Step 2 output: one database pair."""

    @property
    def databases(self) -> list[str]:
        declared = self.index.databases
        return declared if declared else self.root.name.split(PAIR_SEPARATOR)

    @property
    def path_databases(self) -> list[str]:
        """The pair encoded in the directory name."""
        return self.root.name.split(PAIR_SEPARATOR)

    @property
    def relationships(self) -> list[RelationshipDocument]:
        return list(self.children)

    def edges(self):
        for doc in self.relationships:
            yield from doc.edges


def read_database_bundle(root) -> DatabaseBundle:
    root = Path(root)
    index = _read_index(root)
    children = [
        read_document(p, expected_type=TableDocument.TYPE) for p in _child_documents(root)
    ]
    return DatabaseBundle(root=root, index=index, children=children)


def read_relationship_bundle(root) -> RelationshipBundle:
    root = Path(root)
    index = _read_index(root)
    children = [
        read_document(p, expected_type=RelationshipDocument.TYPE)
        for p in _child_documents(root)
    ]
    return RelationshipBundle(root=root, index=index, children=children)


def _read_index(root: Path) -> IndexDocument:
    path = root / INDEX_FILENAME
    if not path.is_file():
        raise BundleError(f"bundle has no {INDEX_FILENAME}: {root}")
    return read_document(path, expected_type=IndexDocument.TYPE)


def read_bundle(root) -> Bundle:
    """Read a bundle, choosing its kind from the index frontmatter."""
    root = Path(root)
    index = _read_index(root)
    if "databases" in index.frontmatter:
        return read_relationship_bundle(root)
    if "database" in index.frontmatter:
        return read_database_bundle(root)
    raise BundleError(
        f"{root / INDEX_FILENAME}: index declares neither 'database' nor 'databases'"
    )


@dataclass
class BundleRoot:
    """An ``/okf`` tree holding db/ and rel/ bundles."""

    root: Path
    databases: list[DatabaseBundle] = field(default_factory=list)
    relationships: list[RelationshipBundle] = field(default_factory=list)

    def database(self, name: str) -> DatabaseBundle | None:
        for bundle in self.databases:
            if bundle.name == name:
                return bundle
        return None

    def pair(self, a: str, b: str) -> RelationshipBundle | None:
        want = PAIR_SEPARATOR.join(sorted([a, b]))
        for bundle in self.relationships:
            if bundle.name == want:
                return bundle
        return None

    @property
    def bundles(self) -> list[Bundle]:
        return [*self.databases, *self.relationships]


def read_root(root) -> BundleRoot:
    """Read every bundle under an ``/okf`` tree."""
    root = Path(root)
    if not root.is_dir():
        raise BundleError(f"not a directory: {root}")
    databases = [
        read_database_bundle(p)
        for p in sorted((root / DB_DIRNAME).iterdir())
        if p.is_dir()
    ] if (root / DB_DIRNAME).is_dir() else []
    relationships = [
        read_relationship_bundle(p)
        for p in sorted((root / REL_DIRNAME).iterdir())
        if p.is_dir()
    ] if (root / REL_DIRNAME).is_dir() else []
    return BundleRoot(root=root, databases=databases, relationships=relationships)


def canonical_pair_name(a: str, b: str) -> str:
    """Pair directory name: database names sorted, so one canonical path."""
    return PAIR_SEPARATOR.join(sorted([a, b]))
