"""Reminder scheduler for raid start notifications."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Optional

import discord

import db
from config import TIME_FMT
from models import Reminder


class ReminderService:
    """Background task that sends scheduled raid reminders."""

    def __init__(self, client: discord.Client, interval_seconds: int = 60) -> None:
        self.client = client
        self.interval_seconds = interval_seconds
        self._task: Optional[asyncio.Task[None]] = None

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._run())

    def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None

    async def _run(self) -> None:
        try:
            while True:
                await self._tick()
                await asyncio.sleep(self.interval_seconds)
        except asyncio.CancelledError:  # pragma: no cover - shutdown path
            return

    async def _tick(self) -> None:
        now_ts = int(datetime.now(tz=timezone.utc).timestamp())
        due = db.list_due_reminders(now_ts)
        for reminder in due:
            await self._send_reminder(reminder)

    async def _send_reminder(self, reminder: Reminder) -> None:
        raid = db.fetch_raid(reminder.raid_id)
        if not raid:
            db.mark_reminder_sent(reminder.raid_id, reminder.offset)
            return
        channel = self.client.get_channel(raid.channel_id)
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            db.mark_reminder_sent(reminder.raid_id, reminder.offset)
            return

        friendly_offset = format_offset(reminder.offset)
        if raid.starts_at:
            starts_dt = datetime.fromtimestamp(raid.starts_at, tz=timezone.utc).astimezone()
            when = starts_dt.strftime(TIME_FMT)
            message = (
                f"Рейд **{raid.name}** стартует через {friendly_offset}! Начало: {when}."
            )
        else:
            message = f"Рейд **{raid.name}** стартует скоро (через {friendly_offset})."

        try:
            await channel.send(message)
        except discord.HTTPException:  # pragma: no cover - network issues ignored
            pass
        finally:
            db.mark_reminder_sent(reminder.raid_id, reminder.offset)


def format_offset(offset_seconds: int) -> str:
    minutes = max(offset_seconds // 60, 0)
    hours, minutes = divmod(minutes, 60)
    parts: list[str] = []
    if hours:
        parts.append(f"{hours} ч")
    if minutes:
        parts.append(f"{minutes} мин")
    if not parts:
        parts.append("меньше минуты")
    return " ".join(parts)


__all__ = ["ReminderService", "format_offset"]
