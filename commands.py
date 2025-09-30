"""Slash commands for managing raids."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import discord
from discord import app_commands

import db
from config import TIME_FMT
from utils import ensure_permissions, make_embed, parse_roles, parse_time_local
from views import SignupView, announce_promotions, refresh_message, sync_roster

raid_group = app_commands.Group(name="raid", description="Рейдовые события Albion Online")
template_group = app_commands.Group(
    name="template", description="Шаблоны рейдов", parent=raid_group
)

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
    db.reset_raid_reminders(raid_id, raid.starts_at)
    roles_data = db.get_roles(raid_id)
    signups = db.get_signups(raid_id)
    waitlist = db.get_waitlist(raid_id)
    embed = make_embed(raid, roles_data, signups, waitlist)
    view = SignupView(raid.id)
    await interaction.response.send_message(embed=embed, view=view)
    msg = await interaction.original_response()
    db.update_message_id(raid_id, msg.id)


@template_group.command(name="create", description="Создать или обновить шаблон")
@app_commands.describe(
    template_name="Название шаблона",
    max_participants="Лимит участников",
    roles="Роли и лимиты в формате tank:2, healer:3",
    comment="Комментарий по умолчанию (опционально)",
)
async def template_create(
    interaction: discord.Interaction,
    template_name: str,
    max_participants: app_commands.Range[int, 1, 1000],
    roles: str,
    comment: Optional[str] = None,
) -> None:
    try:
        roles_map = dict(parse_roles(roles))
    except Exception as exc:
        await interaction.response.send_message(f"Ошибка ролей: {exc}", ephemeral=True)
        return
    db.save_template(
        guild_id=int(interaction.guild_id),
        name=template_name,
        max_participants=int(max_participants),
        roles=roles_map,
        comment=comment or "",
    )
    await interaction.response.send_message(
        f"Шаблон **{template_name}** сохранён.", ephemeral=True
    )


@template_group.command(name="list", description="Показать шаблоны сервера")
async def template_list(interaction: discord.Interaction) -> None:
    templates = db.list_templates(int(interaction.guild_id))
    if not templates:
        await interaction.response.send_message("Шаблонов пока нет.", ephemeral=True)
        return
    lines: list[str] = []
    for tpl in templates:
        roles_text = ", ".join(f"{role}:{cap}" for role, cap in tpl.roles.items())
        lines.append(
            f"**{tpl.name}** • лимит {tpl.max_participants} • роли: {roles_text}"
        )
    await interaction.response.send_message("\n".join(lines), ephemeral=True)


@template_group.command(name="delete", description="Удалить шаблон")
@app_commands.describe(template_name="Название шаблона")
async def template_delete(interaction: discord.Interaction, template_name: str) -> None:
    deleted = db.delete_template(int(interaction.guild_id), template_name)
    if deleted:
        message = f"Шаблон **{template_name}** удалён."
    else:
        message = "Шаблон не найден."
    await interaction.response.send_message(message, ephemeral=True)


@template_group.command(name="use", description="Создать событие по шаблону")
@app_commands.describe(
    template_name="Название шаблона",
    name="Название события",
    starts_at=f"Время старта {TIME_FMT} (опционально)",
    max_participants="Переопределить лимит (опционально)",
    comment="Переопределить комментарий (опционально)",
)
async def template_use(
    interaction: discord.Interaction,
    template_name: str,
    name: str,
    starts_at: Optional[str] = None,
    max_participants: Optional[int] = None,
    comment: Optional[str] = None,
) -> None:
    template = db.fetch_template(int(interaction.guild_id), template_name)
    if not template:
        await interaction.response.send_message("Шаблон не найден.", ephemeral=True)
        return
    if not template.roles:
        await interaction.response.send_message(
            "В шаблоне нет ролей, создание невозможно.", ephemeral=True
        )
        return

    starts_ts = 0
    if starts_at:
        try:
            dt_utc = parse_time_local(starts_at)
        except Exception as exc:
            await interaction.response.send_message(f"Ошибка времени: {exc}", ephemeral=True)
            return
        starts_ts = int(dt_utc.timestamp())

    raid_id = db.create_raid(
        guild_id=int(interaction.guild_id),
        channel_id=int(interaction.channel_id),
        name=name,
        starts_at=starts_ts,
        comment=comment if comment is not None else template.comment,
        max_participants=int(max_participants) if max_participants is not None else template.max_participants,
        created_by=interaction.user.id,
        roles=template.roles,
    )

    raid = db.fetch_raid(raid_id)
    assert raid is not None
    db.reset_raid_reminders(raid_id, raid.starts_at)
    roles_data = db.get_roles(raid_id)
    signups = db.get_signups(raid_id)
    waitlist = db.get_waitlist(raid_id)
    embed = make_embed(raid, roles_data, signups, waitlist)
    view = SignupView(raid.id)
    await interaction.response.send_message(embed=embed, view=view)
    msg = await interaction.original_response()
    db.update_message_id(raid_id, msg.id)


@raid_group.command(name="clone", description="Клонировать существующее событие")
@app_commands.describe(
    source_raid_id="ID события для копирования",
    name="Название нового события",
    starts_at=f"Новое время {TIME_FMT} (опционально)",
    max_participants="Новый лимит (опционально)",
    comment="Комментарий (опционально, по умолчанию как у исходного)",
)
async def raid_clone(
    interaction: discord.Interaction,
    source_raid_id: int,
    name: str,
    starts_at: Optional[str] = None,
    max_participants: Optional[int] = None,
    comment: Optional[str] = None,
) -> None:
    source = db.fetch_raid(source_raid_id)
    if not source:
        await interaction.response.send_message("Исходное событие не найдено.", ephemeral=True)
        return
    try:
        roles_map = db.get_roles(source_raid_id)
    except Exception:
        roles_map = {}
    if not roles_map:
        await interaction.response.send_message(
            "У исходного события нет ролей, клонирование невозможно.", ephemeral=True
        )
        return

    starts_ts = source.starts_at
    if starts_at:
        try:
            dt_utc = parse_time_local(starts_at)
        except Exception as exc:
            await interaction.response.send_message(f"Ошибка времени: {exc}", ephemeral=True)
            return
        starts_ts = int(dt_utc.timestamp())

    raid_id = db.create_raid(
        guild_id=int(interaction.guild_id),
        channel_id=int(interaction.channel_id),
        name=name,
        starts_at=starts_ts,
        comment=comment if comment is not None else source.comment,
        max_participants=int(max_participants) if max_participants is not None else source.max_participants,
        created_by=interaction.user.id,
        roles=roles_map,
    )

    raid = db.fetch_raid(raid_id)
    assert raid is not None
    db.reset_raid_reminders(raid_id, raid.starts_at)
    roles_data = db.get_roles(raid_id)
    signups = db.get_signups(raid_id)
    waitlist = db.get_waitlist(raid_id)
    embed = make_embed(raid, roles_data, signups, waitlist)
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
    new_starts_at: Optional[int] = None
    if name:
        kwargs["name"] = name
    if starts_at:
        try:
            dt_utc = parse_time_local(starts_at)
        except Exception as exc:
            await interaction.response.send_message(f"Ошибка времени: {exc}", ephemeral=True)
            return
        new_starts_at = int(dt_utc.timestamp())
        kwargs["starts_at"] = new_starts_at
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

    limit_changes = db.enforce_signup_limits(raid_id)
    updated_raid = db.fetch_raid(raid_id)
    if updated_raid and new_starts_at is not None:
        db.reset_raid_reminders(raid_id, updated_raid.starts_at)

    promotions: list[tuple[int, str]] = []
    if updated_raid:
        promotions = await sync_roster(interaction.client, updated_raid)
        await announce_promotions(interaction.client, updated_raid, promotions)

    parts = ["Событие обновлено."]
    waitlisted = limit_changes.get("waitlisted", [])
    removed = limit_changes.get("removed", [])
    if waitlisted:
        wait_text = ", ".join(f"<@{uid}> ({role})" for uid, role in waitlisted)
        parts.append("Перемещены в резерв: " + wait_text + ".")
    if removed:
        removed_text = ", ".join(f"<@{uid}> ({role})" for uid, role in removed)
        parts.append(
            "Удалены из состава из-за отсутствия роли: " + removed_text + "."
        )
    if promotions:
        promo_text = ", ".join(f"<@{uid}> ({role})" for uid, role in promotions)
        parts.append("Автоматически добавлены из резерва: " + promo_text + ".")

    await interaction.response.send_message(" ".join(parts), ephemeral=True)


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
    waitlist = db.get_waitlist(raid_id)
    await interaction.response.send_message(
        embed=make_embed(raid, roles, signups, waitlist), view=SignupView(raid.id)
    )


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
