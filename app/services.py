import asyncio
from dataclasses import dataclass, field

from app.config import Settings
from app.db import Database
from app.notify import Notifier
from app.translate import Translator


@dataclass
class RunTrigger:
    """Lets "Опросить сейчас" wake the main loop early and remember who to report back to."""

    event: asyncio.Event = field(default_factory=asyncio.Event)
    requesters: set[int] = field(default_factory=set)

    def request(self, chat_id: int | None = None) -> None:
        if chat_id is not None:
            self.requesters.add(chat_id)
        self.event.set()

    def take_requesters(self) -> set[int]:
        requesters, self.requesters = self.requesters, set()
        return requesters


@dataclass
class Services:
    """Shared objects injected into handlers (aiogram workflow data key: `services`)."""

    db: Database
    settings: Settings
    translator: Translator
    notifier: Notifier
    trigger: RunTrigger = field(default_factory=RunTrigger)
