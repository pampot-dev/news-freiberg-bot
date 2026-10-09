import asyncio
import logging
from dataclasses import dataclass

import deepl

from app.translate.glossary import Glossary

log = logging.getLogger(__name__)

SOURCE_LANG = "DE"
TARGET_LANG = "RU"
GLOSSARY_PREFIX = "news-freiberg-bot"

# The deepl library retries 429/5xx/network errors itself with exponential backoff.
deepl.http_client.max_network_retries = 3


def is_transient(exc: deepl.DeepLException) -> bool:
    """429, 5xx and network errors: worth retrying on the next cycle."""
    return isinstance(exc, deepl.TooManyRequestsException | deepl.ConnectionException) or bool(
        exc.should_retry
    )


@dataclass(frozen=True, slots=True)
class Usage:
    used: int
    limit: int


class DeeplTranslator:
    """Async wrapper around the (synchronous) official deepl client."""

    def __init__(self, api_key: str, glossary: Glossary) -> None:
        self.client = deepl.Translator(api_key)
        self.glossary = glossary
        self.glossary_id: str | None = None
        self._prepared = False

    async def prepare(self) -> None:
        """Set up the DeepL glossary once; falls back to post-replacement on any problem."""
        if self._prepared:
            return
        try:
            self.glossary_id = await asyncio.to_thread(self._sync_glossary)
        except deepl.DeepLException as exc:
            log.warning("DeepL glossary unavailable, using post-replacement: %s", exc)
            self.glossary_id = None
            if is_transient(exc):
                return  # try again on the next call
        self._prepared = True

    def _sync_glossary(self) -> str | None:
        if not self.glossary.terms:
            return None
        pairs = self.client.get_glossary_languages()
        if not any(
            p.source_lang.upper() == SOURCE_LANG and p.target_lang.upper() == TARGET_LANG
            for p in pairs
        ):
            log.info("DeepL has no DE->RU glossaries, using post-replacement")
            return None
        name = f"{GLOSSARY_PREFIX}-{self.glossary.digest}"
        current = None
        for existing in self.client.list_glossaries():
            if existing.name == name:
                current = existing
            elif existing.name.startswith(GLOSSARY_PREFIX):
                self.client.delete_glossary(existing)
        if current is None:
            current = self.client.create_glossary(
                name, SOURCE_LANG, TARGET_LANG, self.glossary.terms
            )
            log.info("created DeepL glossary %s", name)
        return current.glossary_id

    async def translate(self, title: str, text: str) -> tuple[str, str]:
        await self.prepare()
        texts = [title] + ([text] if text else [])
        results = await asyncio.to_thread(
            self.client.translate_text,
            texts,
            source_lang=SOURCE_LANG,
            target_lang=TARGET_LANG,
            glossary=self.glossary_id,
            preserve_formatting=True,
        )
        translated = [
            self.glossary.post_process(r.text, replace_terms=self.glossary_id is None)
            for r in results
        ]
        return translated[0], translated[1] if text else ""

    async def usage(self) -> Usage | None:
        usage = await asyncio.to_thread(self.client.get_usage)
        if usage.character.valid:
            return Usage(usage.character.count, usage.character.limit)
        return None
