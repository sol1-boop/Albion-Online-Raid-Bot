"""Slash commands for managing raids."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import discord
from discord import app_commands

import db
from config import TIME_FMT
from utils import ensure_permissions, make_embed, parse_roles, parse_time_local
from views import SignupView, refresh_message

raid_group = app_commands.Group(name="raid", description="Рейдовые события Albion Online")

PERMISSION_ERROR = (
    "Недостаточно прав: только создатель события или модератор с Manage Events."
)


@raid_group.command(name="create", description="Создать рейдовое событие")
@app_commands.describe(
    name="Название события",
    starts_at=f"Время старта в формате {TIME_FMT} (локальное время сервера, опционально)",
    max_participants="Общий лимит участников",
    roles="Роли и лимиты в формате: tank:2, healer:3, dps:10",
    comment="Комментарий (опционально)",
)
async def raid_create(
    interaction: discord.Interaction,
    name: str,
    max_participants: app_commands.Range[int, 1, 1000],
    roles: str,
    starts_at: Optional[str] = None,
    comment: Optional[str] = None,
) -> None:
    try:
        dt_utc = parse_time_local(starts_at) if starts_at else None
        roles_map = dict(parse_roles(roles))
    except Exception as exc:
        await interaction.response.send_message(f"Ошибка: {exc}", ephemeral=True)
        return

    raid_id = db.create_raid(
        guild_id=int(interaction.guild_id),
        channel_id=int(interaction.channel_id),
        name=name,
        starts_at=int(dt_utc.timestamp()) if dt_utc else 0,
        comment=comment or "",
        max_participants=int(max_participants),
        created_by=interaction.user.id,
        roles=roles_map,
    )

    raid = db.fetch_raid(raid_id)
    assert raid is not None
    roles_data = db.get_roles(raid_id)
    signups = db.get_signups(raid_id)
    embed = make_embed(raid, roles_data, signups)
    view = SignupView(raid.id)
    await interaction.response.send_message(embed=embed, view=view)
    msg = await interaction.original_response()
    db.update_message_id(raid_id, msg.id)


@raid_group.command(name="edit", description="Редактировать событие")
@app_commands.describe(
    raid_id="ID события",
    name="Новое название (опционально)",
    starts_at=f"Новое время {TIME_FMT} (опционально)",
    max_participants="Новый общий лимит (опционально)",
    roles="Полный список ролей и лимитов (заменит существующие)",
    comment="Новый комментарий (опционально)",
)
async def raid_edit(
    interaction: discord.Interaction,
    raid_id: int,
    name: Optional[str] = None,
    starts_at: Optional[str] = None,
    max_participants: Optional[int] = None,
    roles: Optional[str] = None,
    comment: Optional[str] = None,
) -> None:
    raid = db.fetch_raid(raid_id)
    if not raid:
        await interaction.response.send_message("Событие не найдено.", ephemeral=True)
        return
    if not ensure_permissions(interaction, raid):
        await interaction.response.send_message(PERMISSION_ERROR, ephemeral=True)
        return

    kwargs: dict[str, object] = {}
    if name:
        kwargs["name"] = name
    if starts_at:
        try:
            dt_utc = parse_time_local(starts_at)
        except Exception as exc:
            await interaction.response.send_message(f"Ошибка времени: {exc}", ephemeral=True)
            return
        kwargs["starts_at"] = int(dt_utc.timestamp())
    if max_participants is not None:
        kwargs["max_participants"] = int(max_participants)
    if comment is not None:
        kwargs["comment"] = comment

    if kwargs:
        db.update_raid(raid_id, **kwargs)

    if roles is not None:
        try:
            new_roles = dict(parse_roles(roles))
        except Exception as exc:
            await interaction.response.send_message(f"Ошибка ролей: {exc}", ephemeral=True)
            return
        db.replace_roles(raid_id, new_roles)

    removed_signups = db.enforce_signup_limits(raid_id)
    updated_raid = db.fetch_raid(raid_id)
    if updated_raid:
        await refresh_message(interaction.client, updated_raid)

    if removed_signups:
        removed_text = ", ".join(f"<@{uid}> ({role})" for uid, role in removed_signups)
        message = "Событие обновлено. Сняты из-за новых лимитов: " + removed_text
    else:
        message = "Событие обновлено."
    await interaction.response.send_message(message, ephemeral=True)


@raid_group.command(name="delete", description="Удалить событие")
@app_commands.describe(raid_id="ID события для удаления")
async def raid_delete(interaction: discord.Interaction, raid_id: int) -> None:
    raid = db.fetch_raid(raid_id)
    if not raid:
        await interaction.response.send_message("Событие не найдено.", ephemeral=True)
        return
    if not ensure_permissions(interaction, raid):
        await interaction.response.send_message(PERMISSION_ERROR, ephemeral=True)
        return

    db.delete_raid(raid_id)
    await interaction.response.send_message("Событие удалено.", ephemeral=True)

    if raid.message_id:
        channel = interaction.client.get_channel(raid.channel_id)
        try:
            if isinstance(channel, (discord.TextChannel, discord.Thread)):
                msg = await channel.fetch_message(raid.message_id)
                await msg.edit(content="(Событие удалено)", embed=None, view=None)
        except Exception:  # pragma: no cover - network errors ignored
            pass


@raid_group.command(name="view", description="Показать событие")
@app_commands.describe(raid_id="ID события")
async def raid_view(interaction: discord.Interaction, raid_id: int) -> None:
    raid = db.fetch_raid(raid_id)
    if not raid:
        await interaction.response.send_message("Событие не найдено.", ephemeral=True)
        return
    roles = db.get_roles(raid_id)
    signups = db.get_signups(raid_id)
    await interaction.response.send_message(embed=make_embed(raid, roles, signups), view=SignupView(raid.id))


@raid_group.command(name="list", description="Список ближайших событий")
@app_commands.describe(limit="Сколько показать (по умолчанию 10)")
async def raid_list(
    interaction: discord.Interaction,
    limit: app_commands.Range[int, 1, 25] = 10,
) -> None:
    now_ts = int(datetime.now(tz=timezone.utc).timestamp())
    rows = db.list_upcoming_raids(int(interaction.guild_id), now_ts, int(limit))
    if not rows:
        await interaction.response.send_message("Нет ближайших событий.", ephemeral=True)
        return
    lines = []
    for raid in rows:
        if raid.starts_at:
            dt = datetime.fromtimestamp(raid.starts_at, tz=timezone.utc).astimezone()
            when = dt.strftime(TIME_FMT)
        else:
            when = "Без даты"
        lines.append(f"`{raid.id}` • {when} • {raid.name}")
    await interaction.response.send_message("\n".join(lines))


__all__ = [
    "raid_group",
]
