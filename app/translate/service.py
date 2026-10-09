import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

import deepl

from app.db import Database, utcnow
from app.notify import Notifier
from app.translate.deepl_client import Usage, is_transient

log = logging.getLogger(__name__)

# Non-transient failures (e.g. 400 Bad Request) for one item before it is marked failed.
MAX_TRANSLATE_ATTEMPTS = 3


class Translator(Protocol):
    async def translate(self, title: str, text: str) -> tuple[str, str]: ...

    async def usage(self) -> Usage | None: ...


@dataclass(slots=True)
class TranslateResult:
    translated: int = 0
    failed: int = 0
    stopped: str | None = None  # why the cycle stopped early: quota / auth / unavailable


async def translate_pending(
    db: Database, translator: Translator, notifier: Notifier, now: datetime | None = None
) -> TranslateResult:
    """Translate queued items oldest-first. Untranslated items simply stay in the queue."""
    now = now or utcnow()
    result = TranslateResult()
    for item in await db.news.by_status("pending_translation"):
        try:
            title_ru, text_ru = await translator.translate(item.title_de, item.text_de)
        except deepl.QuotaExceededException:
            result.stopped = "quota"
            log.warning("DeepL quota exceeded")
            await notifier.send_throttled(
                "deepl_quota",
                "Квота DeepL исчерпана. Новости ждут перевода и будут опубликованы, "
                "когда квота восстановится.",
                now=now,
            )
            break
        except deepl.AuthorizationException as exc:
            result.stopped = "auth"
            log.error("DeepL authorization failed: %s", exc)
            await notifier.send_throttled(
                "deepl_auth", "DeepL отклонил API-ключ. Проверьте DEEPL_API_KEY.", now=now
            )
            break
        except deepl.DeepLException as exc:
            if is_transient(exc):
                result.stopped = "unavailable"
                log.warning("DeepL unavailable: %s", exc)
                await notifier.send_throttled(
                    "deepl_unavailable",
                    f"DeepL недоступен, перевод отложен до следующего цикла: {exc}",
                    now=now,
                )
                break
            give_up = item.translate_attempts + 1 >= MAX_TRANSLATE_ATTEMPTS
            await db.news.record_translate_failure(item.id, str(exc)[:500], give_up)
            log.error("translation of item %s failed: %s", item.id, exc)
            if give_up:
                result.failed += 1
                await notifier.send(
                    f"Не удалось перевести новость после {MAX_TRANSLATE_ATTEMPTS} попыток, "
                    f"она не будет опубликована.\n{item.title_de}\n{item.url}\nОшибка: {exc}"
                )
            continue
        await db.news.set_translation(item.id, title_ru, text_ru)
        result.translated += 1
    return result
