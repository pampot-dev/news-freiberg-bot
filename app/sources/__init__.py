# Importing source modules registers them in Source.registry.
from app.sources import freiberg  # noqa: F401
from app.sources.base import NewsItem, Source, SourceError, create_source

__all__ = ["NewsItem", "Source", "SourceError", "create_source"]
