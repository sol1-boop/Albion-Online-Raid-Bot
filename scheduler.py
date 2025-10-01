"""Reminder scheduler for raid start notifications and recurring raids."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional, Sequence

import discord

import db
from config import TIME_FMT, log
from models import RaidSchedule, Reminder
from utils import compute_next_occurrence, make_embed
from views import SignupView


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
        await self._process_schedules(now_ts)

    async def _process_schedules(self, now_ts: int) -> None:
        schedules = db.list_due_schedules(now_ts)
        for schedule in schedules:
            try:
                await maybe_generate_schedule_event(self.client, schedule)
            except Exception:
                log.exception("Failed to generate raid for schedule %s", schedule.id)

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


async def maybe_generate_schedule_event(
    client: discord.Client, schedule: RaidSchedule
) -> bool:
    now_ts = int(datetime.now(tz=timezone.utc).timestamp())
    if schedule.generate_at > now_ts:
        return False
    try:
        hour_str, minute_str = schedule.time_of_day.split(":", 1)
        hour = int(hour_str)
        minute = int(minute_str)
    except Exception:
        hour, minute = 0, 0
    base_for_next = datetime.fromtimestamp(schedule.next_run_at, tz=timezone.utc) + timedelta(seconds=60)
    next_occurrence = compute_next_occurrence(
        schedule.weekday, hour, minute, now=base_for_next
    )
    next_run_at = int(next_occurrence.timestamp())
    template = (
        db.fetch_template_by_id(schedule.template_id)
        if schedule.template_id is not None
        else None
    )
    roles = template.roles if template and template.roles else schedule.roles
    if not roles:
        db.update_schedule_next_run(
            schedule.id, next_run_at=next_run_at, lead_time_hours=schedule.lead_time_hours
        )
        return False
    comment = schedule.comment or (template.comment if template else "")
    max_participants = (
        schedule.max_participants
        if schedule.max_participants
        else (template.max_participants if template else len(roles))
    )
    offsets = schedule.reminder_offsets_tuple
    if not offsets and template:
        offsets = template.reminder_offsets_tuple
    offsets_param: Optional[Sequence[int]] = offsets if offsets else None
    start_dt_local = datetime.fromtimestamp(schedule.next_run_at, tz=timezone.utc).astimezone()
    try:
        raid_name = start_dt_local.strftime(schedule.name_pattern)
    except Exception:
        raid_name = schedule.name_pattern
    raid_id = db.create_raid(
        guild_id=schedule.guild_id,
        channel_id=schedule.channel_id,
        name=raid_name,
        starts_at=schedule.next_run_at,
        comment=comment,
        max_participants=max_participants,
        created_by=schedule.created_by,
        roles=roles,
        reminder_offsets=offsets_param,
    )
    raid = db.fetch_raid(raid_id)
    if not raid:
        db.update_schedule_next_run(
            schedule.id, next_run_at=next_run_at, lead_time_hours=schedule.lead_time_hours
        )
        return False
    db.reset_raid_reminders(raid_id, raid.starts_at, offsets_param)
    embed = make_embed(raid, roles, [], [])
    view = SignupView(raid.id)
    channel = client.get_channel(schedule.channel_id)
    if not isinstance(channel, (discord.TextChannel, discord.Thread)):
        db.update_schedule_next_run(
            schedule.id, next_run_at=next_run_at, lead_time_hours=schedule.lead_time_hours
        )
        return False
    try:
        message = await channel.send(embed=embed, view=view)
    except discord.HTTPException:
        db.update_schedule_next_run(
            schedule.id, next_run_at=next_run_at, lead_time_hours=schedule.lead_time_hours
        )
        return False
    client.add_view(view)
    db.update_message_id(raid_id, message.id)
    db.update_schedule_next_run(
        schedule.id, next_run_at=next_run_at, lead_time_hours=schedule.lead_time_hours
    )
    log.info(
        "Created raid %s from schedule %s for %s",
        raid_id,
        schedule.id,
        start_dt_local.strftime(TIME_FMT),
    )
    return True


__all__ = ["ReminderService", "format_offset", "maybe_generate_schedule_event"]
