"""Shared helpers for working with raid templates."""
from __future__ import annotations

from datetime import datetime
from typing import Optional, Sequence, Tuple

import db
from config import TIME_FMT
from models import Raid, Signup, WaitlistEntry
from utils import parse_reminder_offsets, parse_roles, parse_time_local


def _format_offset(offset_seconds: int) -> str:
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


def _parse_positive_int(value: int | str, field: str) -> int:
    if isinstance(value, int):
        result = value
    else:
        text = value.strip()
        if not text:
            raise ValueError(f"{field} не может быть пустым.")
        result = int(text)
    if result <= 0:
        raise ValueError(f"{field} должен быть больше нуля.")
    return result


def _parse_optional_positive_int(value: Optional[int | str], field: str) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, int):
        result = value
    else:
        text = value.strip()
        if not text:
            return None
        result = int(text)
    if result <= 0:
        raise ValueError(f"{field} должен быть больше нуля.")
    return result


def _parse_optional_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    text = value.strip()
    return text if text else None


def describe_offsets(offsets: Sequence[int]) -> str:
    if not offsets:
        default = ", ".join(str(int(v // 60)) for v in db.DEFAULT_REMINDER_OFFSETS)
        return f"по умолчанию ({default} мин)"
    formatted = [_format_offset(value) for value in sorted(offsets, reverse=True)]
    return ", ".join(formatted)


def create_or_update_template(
    *,
    guild_id: int,
    template_name: str,
    max_participants: int | str,
    roles: str,
    comment: Optional[str] = None,
    reminders: Optional[str] = None,
) -> str:
    roles_map = dict(parse_roles(roles))
    if not roles_map:
        raise ValueError("Нужно указать хотя бы одну роль.")
    max_participants_value = _parse_positive_int(max_participants, "Лимит участников")
    reminder_offsets: Optional[Sequence[int]] = None
    if reminders is not None:
        reminders_text = reminders.strip()
        if reminders_text:
            parsed_offsets = parse_reminder_offsets(reminders_text)
            reminder_offsets = parsed_offsets if parsed_offsets else None
    db.save_template(
        guild_id=guild_id,
        name=template_name,
        max_participants=max_participants_value,
        roles=roles_map,
        comment=_parse_optional_text(comment) or "",
        reminder_offsets=reminder_offsets,
    )
    return f"Шаблон **{template_name}** сохранён."


def list_templates_description(guild_id: int) -> Tuple[str, bool]:
    templates = db.list_templates(guild_id)
    if not templates:
        return ("Шаблонов пока нет.", False)
    lines: list[str] = []
    for tpl in templates:
        roles_text = ", ".join(f"{role}:{cap}" for role, cap in tpl.roles.items())
        offsets_desc = describe_offsets(tpl.reminder_offsets_tuple)
        lines.append(
            f"**{tpl.name}** • лимит {tpl.max_participants} • роли: {roles_text} • напоминания: {offsets_desc}"
        )
    return ("\n".join(lines), True)


def delete_template(guild_id: int, template_name: str) -> str:
    deleted = db.delete_template(guild_id, template_name)
    if deleted:
        return f"Шаблон **{template_name}** удалён."
    raise ValueError("Шаблон не найден.")


def instantiate_template(
    *,
    guild_id: int,
    channel_id: int,
    author_id: int,
    template_name: str,
    event_name: str,
    starts_at: Optional[str] = None,
    max_participants: Optional[int | str] = None,
    comment: Optional[str] = None,
    reminders: Optional[str] = None,
) -> Tuple[Raid, dict[str, int], list[Signup], list[WaitlistEntry]]:
    template = db.fetch_template(guild_id, template_name)
    if not template:
        raise ValueError("Шаблон не найден.")
    if not template.roles:
        raise ValueError("В шаблоне нет ролей, создание невозможно.")

    starts_ts = 0
    if starts_at:
        dt_utc = parse_time_local(starts_at)
        starts_ts = int(dt_utc.timestamp())

    reminder_offsets: Optional[Sequence[int]] = template.reminder_offsets_tuple or None
    if reminders is not None:
        reminders_text = reminders.strip()
        if reminders_text:
            parsed_offsets = parse_reminder_offsets(reminders_text)
            reminder_offsets = parsed_offsets if parsed_offsets else None

    max_participants_value = _parse_optional_positive_int(
        max_participants, "Лимит участников"
    )
    comment_text = _parse_optional_text(comment)

    raid_id = db.create_raid(
        guild_id=guild_id,
        channel_id=channel_id,
        name=event_name,
        starts_at=starts_ts,
        comment=comment_text if comment_text is not None else template.comment,
        max_participants=(
            max_participants_value
            if max_participants_value is not None
            else template.max_participants
        ),
        created_by=author_id,
        roles=template.roles,
        reminder_offsets=reminder_offsets,
    )

    raid = db.fetch_raid(raid_id)
    if raid is None:  # pragma: no cover - defensive
        raise RuntimeError("Не удалось создать рейд по шаблону.")
    db.reset_raid_reminders(raid_id, raid.starts_at)
    roles_data = db.get_roles(raid_id)
    signups = db.get_signups(raid_id)
    waitlist = db.get_waitlist(raid_id)
    return raid, roles_data, signups, waitlist


def format_schedule_summary(next_run_at: int, reminder_offsets: Sequence[int]) -> str:
    when_text = datetime.fromtimestamp(next_run_at).astimezone().strftime(TIME_FMT)
    offsets_desc = describe_offsets(reminder_offsets)
    return f"Следующий рейд {when_text}. Напоминания: {offsets_desc}."


__all__ = [
    "create_or_update_template",
    "delete_template",
    "describe_offsets",
    "format_schedule_summary",
    "instantiate_template",
    "list_templates_description",
]
