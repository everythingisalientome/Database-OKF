"""Exceptions raised by the crawler."""


class CrawlerError(Exception):
    """Base class for every error this package raises."""


class AllowListError(CrawlerError):
    """An identifier was rejected before it could reach a SQL template.

    Raised by :class:`crawler.allowlist.AllowList`. Reaching this exception is
    the allow-list working: no identifier is interpolated into catalog SQL
    unless the crawl's own A1/A2 output produced it and it is a bare
    identifier. See specs/00-project-overview.md decision 1.
    """


class ConfigError(CrawlerError):
    """A crawl configuration is missing something or contradicts itself."""


class AdapterError(CrawlerError):
    """No adapter exists for the requested engine, or it was misused."""


class QueryError(CrawlerError):
    """A catalog statement failed against the live database."""

    def __init__(self, message, *, query_id=None, engine=None):
        self.query_id = query_id
        self.engine = engine
        where = "/".join(part for part in (engine, query_id) if part)
        super().__init__(f"{where}: {message}" if where else message)
