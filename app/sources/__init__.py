# Importing source modules registers them in Source.registry.
from app.sources import freiberg  # noqa: F401
from app.sources.base import NewsItem, Source, SourceError, create_source

# Seeded into the `sources` table on startup: (key, name, url).
DEFAULT_SOURCES = [
    (
        "freiberg_neuigkeiten",
        "freiberg.de — Neuigkeiten",
        "https://www.freiberg.de/stadt-und-buerger/aktuelles/neuigkeiten",
    ),
]

__all__ = ["DEFAULT_SOURCES", "NewsItem", "Source", "SourceError", "create_source"]
