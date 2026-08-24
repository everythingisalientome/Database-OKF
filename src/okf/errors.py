"""Exceptions raised by the okf package."""


class OkfError(Exception):
    """Base class for every error this package raises."""


class ParseError(OkfError):
    """A document does not conform to the OKF grammar.

    Parsing is strict on purpose: a line the grammar does not cover is an
    error, never a silently dropped line. Bundles are git-committed and
    diffed, so a lossy read would corrupt the drift signal on the next write.
    """

    def __init__(self, message, *, path=None, line=None):
        self.path = path
        self.line = line
        where = ""
        if path is not None:
            where = f"{path}"
            if line is not None:
                where = f"{where}:{line}"
        elif line is not None:
            where = f"line {line}"
        super().__init__(f"{where}: {message}" if where else message)


class BundleError(OkfError):
    """A bundle directory is not laid out the way the conventions require."""
