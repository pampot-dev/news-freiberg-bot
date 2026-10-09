from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from typing import ClassVar

import httpx


@dataclass(frozen=True, slots=True)
class NewsItem:
    url: str
    title: str
    published_date: date | None = None
    label: str | None = None
    subtitle: str | None = None
    teaser: str | None = None


class SourceError(Exception):
    """The source could not be fetched (network error or non-2xx response)."""


class Source(ABC):
    """A news listing. Subclasses register themselves by `key`, matching `sources.key` in the DB."""

    key: ClassVar[str]
    registry: ClassVar[dict[str, type[Source]]] = {}

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if "key" in cls.__dict__:
            Source.registry[cls.key] = cls

    def __init__(self, url: str, client: httpx.AsyncClient) -> None:
        self.url = url
        self.client = client

    @abstractmethod
    async def fetch(self) -> list[NewsItem]:
        """Return items in page order (newest first is typical but not required)."""

    async def get_html(self) -> str:
        try:
            response = await self.client.get(self.url, follow_redirects=True)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise SourceError(f"{self.key}: {exc!r}") from exc
        return response.text


def create_source(key: str, url: str, client: httpx.AsyncClient) -> Source:
    try:
        cls = Source.registry[key]
    except KeyError:
        raise KeyError(f"no source class registered for key {key!r}") from None
    return cls(url, client)
