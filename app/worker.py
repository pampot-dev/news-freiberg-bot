import asyncio
import contextlib
import logging
from dataclasses import dataclass, field

import httpx

from app.db import utcnow
from app.monitor import check_sources
from app.notify import MessageSender
from app.poller import PollResult, poll_all
from app.publish.publisher import (
    Publisher,
    PublishResult,
    in_quiet_hours,
    seconds_until_quiet_end,
)
from app.services import Services
from app.translate import translate_pending
from app.translate.service import TranslateResult

log = logging.getLogger(__name__)

SKIP_REASONS = {
    "paused": "публикация на паузе",
    "quiet_hours": "тихие часы",
    "no_recipients": "нет активных получателей",
}


@dataclass
class CycleReport:
    polls: list[PollResult] = field(default_factory=list)
    translation: TranslateResult | None = None
    publishing: PublishResult | None = None
    errors: list[str] = field(default_factory=list)
    poll_skipped: bool = False

    def summary(self) -> str:
        lines = ["Цикл завершён."]
        if self.poll_skipped:
            lines.append("Опрос пропущен: тихие часы")
        for poll in self.polls:
            if poll.error:
                lines.append(f"• {poll.source_key}: ошибка — {poll.error}")
            else:
                lines.append(f"• {poll.source_key}: найдено {poll.found}, новых {poll.new}")
        if self.translation:
            t = self.translation
            line = f"Переведено: {t.translated}"
            if t.stopped:
                line += f" (перевод остановлен: {t.stopped})"
            lines.append(line)
        if self.publishing:
            p = self.publishing
            if p.skipped:
                lines.append(f"Публикация пропущена: {SKIP_REASONS.get(p.skipped, p.skipped)}")
            else:
                lines.append(
                    f"Отправлено сообщений: {p.sent}, опубликовано новостей: {p.published}"
                )
        lines.extend(f"⚠️ {e}" for e in self.errors)
        return "\n".join(lines)


class Worker:
    """poll -> translate -> publish -> monitor, every POLL_INTERVAL_MIN or on /run."""

    def __init__(
        self,
        services: Services,
        bot: MessageSender,
        client: httpx.AsyncClient,
        publisher: Publisher | None = None,
    ) -> None:
        self.services = services
        self.bot = bot
        self.client = client
        self.publisher = publisher or Publisher(
            services.db, bot, services.notifier, services.settings
        )

    async def run_cycle(self, manual: bool = False) -> CycleReport:
        """One pass. Scheduled cycles don't poll during quiet hours; /run (manual) always does."""
        s = self.services
        report = CycleReport()
        # Each step is isolated: a bug in one must not stop the others or the loop.
        if not manual and in_quiet_hours(utcnow(), s.settings.tz, s.settings.quiet_hours):
            report.poll_skipped = True
        else:
            try:
                report.polls = await poll_all(s.db, self.client, utcnow())
            except Exception as exc:
                log.exception("polling failed")
                report.errors.append(f"опрос: {exc}")
        try:
            report.translation = await translate_pending(s.db, s.translator, s.notifier, utcnow())
        except Exception as exc:
            log.exception("translation step failed")
            report.errors.append(f"перевод: {exc}")
        try:
            report.publishing = await self.publisher.run(utcnow())
        except Exception as exc:
            log.exception("publishing step failed")
            report.errors.append(f"публикация: {exc}")
        try:
            await check_sources(s.db, s.notifier, s.settings, utcnow())
        except Exception as exc:
            log.exception("monitoring step failed")
            report.errors.append(f"мониторинг: {exc}")
        return report

    async def run_forever(self) -> None:
        trigger = self.services.trigger
        interval = self.services.settings.poll_interval_min * 60
        settings = self.services.settings
        while True:
            manual = trigger.event.is_set()
            trigger.event.clear()
            requesters = trigger.take_requesters()
            report = await self.run_cycle(manual=manual)
            log.info("cycle done: %s", report.summary().replace("\n", " | "))
            for chat_id in requesters:
                with contextlib.suppress(Exception):
                    await self.bot.send_message(chat_id, report.summary(), parse_mode=None)
            # At night, sleep until quiet hours end so the morning cycle starts right on time.
            quiet_left = seconds_until_quiet_end(utcnow(), settings.tz, settings.quiet_hours)
            timeout = quiet_left + 1 if quiet_left else interval
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(trigger.event.wait(), timeout=timeout)
