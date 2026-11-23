"""Source errors (Wikipedia and HTTP client): the only exceptions a source raises towards the research."""


class SourceError(Exception):
    """Error of a source, with an English message. A failing source never stops the research."""


class SourceUnavailable(SourceError):
    """The source cannot be used (access denied, no network...)."""


class ReachedLimit(SourceError):
    """Too many requests: the source must be left to rest."""


class UnreadableContent(SourceError):
    """This content cannot be read (private, no text, too large, domain not allowed...)."""
