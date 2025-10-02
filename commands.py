"""Slash commands for managing raids."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Sequence, TYPE_CHECKING

import discord
from discord import app_commands

import db
from config import TIME_FMT
from template_actions import (
    create_or_update_template,
    delete_template,
    describe_offsets,
    instantiate_template,
    list_templates_description,
)
from utils import (
    compute_next_occurrence,
    ensure_permissions,
    make_embed,
    parse_reminder_offsets,
    parse_roles,
    parse_time_local,
    parse_time_of_day,
)

if TYPE_CHECKING:
    from models import RaidSchedule
from views import (
    SignupView,
    TemplateManagementView,
    announce_promotions,
    refresh_message,
    sync_roster,
)

raid_group = app_commands.Group(name="raid", description="Рейдовые события Albion Online")
template_group = app_commands.Group(
    name="template", description="Шаблоны рейдов", parent=raid_group
)
schedule_group = app_commands.Group(
    name="schedule", description="Повторяющиеся события", parent=raid_group
)

WEEKDAY_CHOICES = [
    app_commands.Choice(name="Понедельник", value=0),
    app_commands.Choice(name="Вторник", value=1),
    app_commands.Choice(name="Среда", value=2),
    app_commands.Choice(name="Четверг", value=3),
    app_commands.Choice(name="Пятница", value=4),
    app_commands.Choice(name="Суббота", value=5),
    app_commands.Choice(name="Воскресенье", value=6),
]

WEEKDAY_LABELS = {
    0: "Пн",
    1: "Вт",
    2: "Ср",
    3: "Чт",
    4: "Пт",
    5: "Сб",
    6: "Вс",
}


PERMISSION_ERROR = (
    "Недостаточно прав: только создатель события или модератор с Manage Events."
)

ATTENDANCE_STATUS_LABELS = {
    db.ATTENDANCE_STATUS_MAIN: "основной состав",
    db.ATTENDANCE_STATUS_WAITLIST: "резерв",
    db.ATTENDANCE_STATUS_REMOVED: "удалён",
}


@raid_group.command(name="create", description="Создать рейдовое событие")
@app_commands.describe(
    name="Название события",
    starts_at=f"Время старта в формате {TIME_FMT} (локальное время сервера, опционально)",
    max_participants="Общий лимит участников",
    roles="Роли и лимиты в формате: tank:2, healer:3, dps:10",
    comment="Комментарий (опционально)",
    reminders="Напоминания в минутах/часах, через запятую (например 120,30m,10m)",
)
async def raid_create(
    interaction: discord.Interaction,
    name: str,
    max_participants: app_commands.Range[int, 1, 1000],
    roles: str,
    starts_at: Optional[str] = None,
    comment: Optional[str] = None,
    reminders: Optional[str] = None,
) -> None:
    try:
        dt_utc = parse_time_local(starts_at) if starts_at else None
        roles_map = dict(parse_roles(roles))
        reminder_offsets: Sequence[int] | None = None
        if reminders and reminders.strip():
            parsed_offsets = parse_reminder_offsets(reminders)
            reminder_offsets = parsed_offsets if parsed_offsets else None
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
        reminder_offsets=reminder_offsets,
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
    reminders="Напоминания по умолчанию (например 120,30m,10m)",
)
async def template_create(
    interaction: discord.Interaction,
    template_name: str,
    max_participants: app_commands.Range[int, 1, 1000],
    roles: str,
    comment: Optional[str] = None,
    reminders: Optional[str] = None,
) -> None:
    try:
        message = create_or_update_template(
            guild_id=int(interaction.guild_id),
            template_name=template_name,
            max_participants=int(max_participants),
            roles=roles,
            comment=comment,
            reminders=reminders,
        )
    except Exception as exc:
        await interaction.response.send_message(f"Ошибка: {exc}", ephemeral=True)
        return
    await interaction.response.send_message(message, ephemeral=True)


async def _respond_with_template_list(interaction: discord.Interaction) -> None:
    description, _ = list_templates_description(int(interaction.guild_id))
    view = TemplateManagementView(
        guild_id=int(interaction.guild_id),
        channel_id=int(interaction.channel_id),
    )
    await interaction.response.send_message(
        description,
        ephemeral=True,
        view=view,
    )


@template_group.command(name="list", description="Показать шаблоны сервера")
async def template_list(interaction: discord.Interaction) -> None:
    await _respond_with_template_list(interaction)


@template_group.command(name="manage", description="Управление шаблонами через меню")
async def template_manage(interaction: discord.Interaction) -> None:
    await _respond_with_template_list(interaction)


@template_group.command(name="delete", description="Удалить шаблон")
@app_commands.describe(template_name="Название шаблона")
async def template_delete(interaction: discord.Interaction, template_name: str) -> None:
    try:
        message = delete_template(int(interaction.guild_id), template_name)
    except ValueError as exc:
        await interaction.response.send_message(f"Ошибка: {exc}", ephemeral=True)
        return
    await interaction.response.send_message(message, ephemeral=True)


@template_group.command(name="use", description="Создать событие по шаблону")
@app_commands.describe(
    template_name="Название шаблона",
    name="Название события",
    starts_at=f"Время старта {TIME_FMT} (опционально)",
    max_participants="Переопределить лимит (опционально)",
    comment="Переопределить комментарий (опционально)",
    reminders="Переопределить напоминания (например 120,30m,10m)",
)
async def template_use(
    interaction: discord.Interaction,
    template_name: str,
    name: str,
    starts_at: Optional[str] = None,
    max_participants: Optional[int] = None,
    comment: Optional[str] = None,
    reminders: Optional[str] = None,
) -> None:
    try:
        raid, roles_data, signups, waitlist = instantiate_template(
            guild_id=int(interaction.guild_id),
            channel_id=int(interaction.channel_id),
            author_id=interaction.user.id,
            template_name=template_name,
            event_name=name,
            starts_at=starts_at,
            max_participants=max_participants,
            comment=comment,
            reminders=reminders,
        )
    except Exception as exc:
        await interaction.response.send_message(f"Ошибка: {exc}", ephemeral=True)
        return

    embed = make_embed(raid, roles_data, signups, waitlist)
    view = SignupView(raid.id)
    await interaction.response.send_message(embed=embed, view=view)
    msg = await interaction.original_response()
    db.update_message_id(raid.id, msg.id)

@schedule_group.command(name="create", description="Создать расписание повторяющегося рейда")
@app_commands.describe(
    name_pattern="Название события (поддерживаются плейсхолдеры strftime, например %d.%m)",
    template_name="Шаблон рейда",
    weekday="День недели старта",
    time_of_day="Время старта (ЧЧ:ММ)",
    repeat_days="Период повторения в днях (по умолчанию 7)",
    lead_time_hours="За сколько часов до старта публиковать событие",
    channel="Канал для публикации (по умолчанию текущий)",
    max_participants="Переопределить лимит участников",
    comment="Переопределить комментарий",
    reminders="Переопределить напоминания (например 120,30m,10m)",
)
async def schedule_create(
    interaction: discord.Interaction,
    name_pattern: str,
    template_name: str,
    weekday: app_commands.Choice[int],
    time_of_day: str,
    repeat_days: app_commands.Range[int, 1, 30] = 7,
    lead_time_hours: Optional[int] = None,
    channel: Optional[discord.TextChannel] = None,
    max_participants: Optional[int] = None,
    comment: Optional[str] = None,
    reminders: Optional[str] = None,
) -> None:
    template = db.fetch_template(int(interaction.guild_id), template_name)
    if not template:
        await interaction.response.send_message("Шаблон не найден.", ephemeral=True)
        return
    if not template.roles:
        await interaction.response.send_message(
            "В шаблоне нет ролей, создание расписания невозможно.", ephemeral=True
        )
        return
    try:
        hour, minute = parse_time_of_day(time_of_day)
    except Exception as exc:
        await interaction.response.send_message(f"Ошибка времени: {exc}", ephemeral=True)
        return
    interval_days = int(repeat_days)
    if lead_time_hours is None:
        lead_time_value = max(interval_days * 24 - 1, 1)
    else:
        if lead_time_hours <= 0:
            await interaction.response.send_message(
                "Интервал публикации должен быть больше нуля.", ephemeral=True
            )
            return
        lead_time_value = lead_time_hours
    if lead_time_value >= interval_days * 24:
        await interaction.response.send_message(
            "Период публикации должен быть меньше периода повторения.",
            ephemeral=True,
        )
        return
    reminder_offsets: Sequence[int] | None = template.reminder_offsets_tuple or None
    if reminders and reminders.strip():
        try:
            parsed_offsets = parse_reminder_offsets(reminders)
        except Exception as exc:
            await interaction.response.send_message(f"Ошибка напоминаний: {exc}", ephemeral=True)
            return
        reminder_offsets = parsed_offsets if parsed_offsets else None
    publish_channel: Optional[discord.abc.GuildChannel] = channel or interaction.channel
    if not isinstance(publish_channel, (discord.TextChannel, discord.Thread)):
        await interaction.response.send_message(
            "Расписания поддерживаются только для текстовых каналов и тредов.",
            ephemeral=True,
        )
        return
    starts_dt = compute_next_occurrence(weekday.value, hour, minute)
    next_run_at = int(starts_dt.timestamp())
    schedule_id = db.create_schedule(
        guild_id=int(interaction.guild_id),
        channel_id=int(publish_channel.id),
        template_id=template.id,
        name_pattern=name_pattern,
        comment=comment if comment is not None else template.comment,
        max_participants=int(max_participants) if max_participants is not None else template.max_participants,
        roles_json=template.roles_json,
        weekday=int(weekday.value),
        time_of_day=f"{hour:02d}:{minute:02d}",
        interval_days=interval_days,
        lead_time_hours=lead_time_value,
        reminder_offsets=reminder_offsets,
        next_run_at=next_run_at,
        created_by=interaction.user.id,
    )
    schedule = db.fetch_schedule(schedule_id)
    when_text = datetime.fromtimestamp(next_run_at, tz=timezone.utc).astimezone().strftime(TIME_FMT)
    offsets_desc = describe_offsets(
        schedule.reminder_offsets_tuple if schedule else (reminder_offsets or ())
    )
    created_now = False
    if schedule:
        from scheduler import maybe_generate_schedule_event

        created_now = await maybe_generate_schedule_event(interaction.client, schedule)
    channel_mention = publish_channel.mention if hasattr(publish_channel, "mention") else f"#{publish_channel}"
    message = (
        f"Расписание #{schedule_id} создано. Следующий рейд {when_text} в {channel_mention}."
        f" Напоминания: {offsets_desc}."
    )
    if created_now:
        message += " Первый рейд опубликован автоматически."
    await interaction.response.send_message(message, ephemeral=True)


def _has_schedule_permissions(
    interaction: discord.Interaction, schedule: "RaidSchedule"
) -> bool:
    if interaction.user.id == schedule.created_by:
        return True
    perms = getattr(interaction.user, "guild_permissions", None)
    return bool(perms and (getattr(perms, "manage_events", False) or getattr(perms, "manage_guild", False)))


@schedule_group.command(name="list", description="Показать расписания гильдии")
async def schedule_list(interaction: discord.Interaction) -> None:
    schedules = db.list_schedules(int(interaction.guild_id))
    if not schedules:
        await interaction.response.send_message("Расписания не найдены.", ephemeral=True)
        return
    lines: list[str] = []
    for schedule in schedules:
        template_name = "—"
        if schedule.template_id:
            template = db.fetch_template_by_id(schedule.template_id)
            if template:
                template_name = template.name
        next_text = datetime.fromtimestamp(schedule.next_run_at, tz=timezone.utc).astimezone().strftime(TIME_FMT)
        offsets_desc = describe_offsets(schedule.reminder_offsets_tuple)
        channel_obj = interaction.guild.get_channel(schedule.channel_id) if interaction.guild else None
        channel_display = channel_obj.mention if channel_obj else f"#{schedule.channel_id}"
        lines.append(
            f"#{schedule.id} • {WEEKDAY_LABELS.get(schedule.weekday, schedule.weekday)} {schedule.time_of_day}"
            f" • следующий {next_text} • канал {channel_display}"
            f" • шаблон {template_name} • каждые {schedule.interval_days} дн • создаётся за {schedule.lead_time_hours} ч"
            f" • напоминания: {offsets_desc}"
        )
    await interaction.response.send_message("\n".join(lines), ephemeral=True)


@schedule_group.command(name="delete", description="Удалить расписание")
@app_commands.describe(schedule_id="ID расписания")
async def schedule_delete(interaction: discord.Interaction, schedule_id: int) -> None:
    schedule = db.fetch_schedule(schedule_id)
    if not schedule or schedule.guild_id != int(interaction.guild_id):
        await interaction.response.send_message("Расписание не найдено.", ephemeral=True)
        return
    if not _has_schedule_permissions(interaction, schedule):
        await interaction.response.send_message(PERMISSION_ERROR, ephemeral=True)
        return
    db.delete_schedule(schedule_id)
    await interaction.response.send_message(
        f"Расписание #{schedule_id} удалено. Уже созданные события останутся.", ephemeral=True
    )


@raid_group.command(name="clone", description="Клонировать существующее событие")
@app_commands.describe(
    source_raid_id="ID события для копирования",
    name="Название нового события",
    starts_at=f"Новое время {TIME_FMT} (опционально)",
    max_participants="Новый лимит (опционально)",
    comment="Комментарий (опционально, по умолчанию как у исходного)",
    reminders="Переопределить напоминания (например 120,30m,10m)",
)
async def raid_clone(
    interaction: discord.Interaction,
    source_raid_id: int,
    name: str,
    starts_at: Optional[str] = None,
    max_participants: Optional[int] = None,
    comment: Optional[str] = None,
    reminders: Optional[str] = None,
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

    reminder_offsets: Sequence[int] | None = source.reminder_offsets_tuple or None
    if reminders and reminders.strip():
        try:
            parsed_offsets = parse_reminder_offsets(reminders)
        except Exception as exc:
            await interaction.response.send_message(f"Ошибка напоминаний: {exc}", ephemeral=True)
            return
        reminder_offsets = parsed_offsets if parsed_offsets else None

    raid_id = db.create_raid(
        guild_id=int(interaction.guild_id),
        channel_id=int(interaction.channel_id),
        name=name,
        starts_at=starts_ts,
        comment=comment if comment is not None else source.comment,
        max_participants=int(max_participants) if max_participants is not None else source.max_participants,
        created_by=interaction.user.id,
        roles=roles_map,
        reminder_offsets=reminder_offsets,
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
    reminders="Новый список напоминаний или 'default' для сброса",
)
async def raid_edit(
    interaction: discord.Interaction,
    raid_id: int,
    name: Optional[str] = None,
    starts_at: Optional[str] = None,
    max_participants: Optional[int] = None,
    roles: Optional[str] = None,
    comment: Optional[str] = None,
    reminders: Optional[str] = None,
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
    reminder_offsets_override: Sequence[int] | None = None
    if reminders is not None:
        text = reminders.strip().lower()
        if not text or text in {"default", "reset"}:
            reminder_offsets_override = tuple(db.DEFAULT_REMINDER_OFFSETS)
        else:
            try:
                parsed = parse_reminder_offsets(reminders)
            except Exception as exc:
                await interaction.response.send_message(
                    f"Ошибка напоминаний: {exc}", ephemeral=True
                )
                return
            reminder_offsets_override = parsed if parsed else tuple(db.DEFAULT_REMINDER_OFFSETS)
    if updated_raid:
        if new_starts_at is not None or reminder_offsets_override is not None:
            offsets_to_use = (
                reminder_offsets_override
                if reminder_offsets_override is not None
                else None
            )
            db.reset_raid_reminders(raid_id, updated_raid.starts_at, offsets_to_use)

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
    if reminder_offsets_override is not None:
        parts.append(
            "Напоминания обновлены: "
            + describe_offsets(reminder_offsets_override)
            + "."
        )

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


@raid_group.command(name="stats", description="Статистика посещаемости рейдов")
@app_commands.describe(
    limit="Количество записей (по умолчанию 10)",
    member="Показать историю конкретного участника",
)
async def raid_stats(
    interaction: discord.Interaction,
    limit: app_commands.Range[int, 1, 25] = 10,
    member: Optional[discord.Member] = None,
) -> None:
    if interaction.guild_id is None:
        await interaction.response.send_message(
            "Команда доступна только внутри сервера.", ephemeral=True
        )
        return
    guild_id = int(interaction.guild_id)
    if member is not None:
        history = db.get_attendance_history(guild_id, member.id, int(limit))
        if not history:
            await interaction.response.send_message(
                f"Для {member.mention} пока нет записей посещаемости.",
                ephemeral=True,
            )
            return
        lines: list[str] = []
        for record in history:
            moment = datetime.fromtimestamp(record.recorded_at, tz=timezone.utc)
            when = discord.utils.format_dt(moment, style="f")
            raid_name = record.raid_name or f"Рейд #{record.raid_id}"
            status = ATTENDANCE_STATUS_LABELS.get(record.status, record.status)
            lines.append(
                f"{when} • {raid_name} — {status} ({record.role_name})"
            )
        await interaction.response.send_message("\n".join(lines), ephemeral=True)
        return

    summaries = db.get_attendance_summary(guild_id)
    if not summaries:
        await interaction.response.send_message(
            "Статистика пока пуста — запланируйте первый рейд!",
            ephemeral=True,
        )
        return

    top_summaries = summaries[: int(limit)]
    lines = []
    for index, summary in enumerate(top_summaries, start=1):
        roles_text = ", ".join(
            f"{role}:{count}" for role, count in summary.roles.items()
        )
        if not roles_text:
            roles_text = "без указания роли"
        lines.append(
            f"{index}. <@{summary.user_id}> — {summary.total} рейдов ({roles_text})"
        )
    lines.append(
        "Укажите параметр `member`, чтобы увидеть историю конкретного игрока."
    )
    await interaction.response.send_message("\n".join(lines), ephemeral=True)


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
    "schedule_group",
]
