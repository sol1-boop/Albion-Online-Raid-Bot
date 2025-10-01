"""Discord UI components and interaction handlers."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional, Tuple

import discord

import db
from config import TIME_FMT
from models import Raid
from template_actions import (
    create_or_update_template,
    delete_template,
    instantiate_template,
    list_templates_description,
)
from utils import make_embed


class SignupView(discord.ui.View):
    def __init__(self, raid_id: int):
        super().__init__(timeout=None)
        self.raid_id = raid_id
        options = [
            discord.SelectOption(label=name, description=f"Лимит {cap}")
            for name, cap in db.get_roles(raid_id).items()
        ]
        self.add_item(RoleSelect(raid_id, options))
        self.add_item(LeaveButton(raid_id))


class RoleSelect(discord.ui.Select):
    def __init__(self, raid_id: int, options: List[discord.SelectOption]):
        super().__init__(
            placeholder="Выберите роль и запишитесь",
            min_values=1,
            max_values=1,
            options=options,
            custom_id=f"raid:{raid_id}:role",
        )
        self.raid_id = raid_id

    async def callback(self, interaction: discord.Interaction) -> None:  # pragma: no cover - UI callback
        role = self.values[0]
        await handle_signup(interaction, self.raid_id, role)


class LeaveButton(discord.ui.Button):
    def __init__(self, raid_id: int):
        super().__init__(
            label="Снять запись",
            style=discord.ButtonStyle.secondary,
            custom_id=f"raid:{raid_id}:leave",
        )
        self.raid_id = raid_id

    async def callback(self, interaction: discord.Interaction) -> None:  # pragma: no cover - UI callback
        await handle_unsubscribe(interaction, self.raid_id)


async def handle_signup(interaction: discord.Interaction, raid_id: int, role_name: str) -> None:
    raid = db.fetch_raid(raid_id)
    if not raid:
        await interaction.response.send_message("Событие не найдено.", ephemeral=True)
        return

    roles = db.get_roles(raid_id)
    if role_name not in roles:
        await interaction.response.send_message("Такой роли нет в этом событии.", ephemeral=True)
        return

    current_signup = db.get_user_signup(raid_id, interaction.user.id)
    wait_entry = db.get_waitlist_entry(raid_id, interaction.user.id)
    signups = db.get_signups(raid_id)
    other_signups = [s for s in signups if s.user_id != interaction.user.id]
    total_count = len(other_signups) + (1 if current_signup else 0)
    role_counts: dict[str, int] = {name: 0 for name in roles}
    for signup in other_signups:
        if signup.role_name in role_counts:
            role_counts[signup.role_name] += 1

    if current_signup:
        if current_signup.role_name == role_name:
            await interaction.response.send_message(
                "Вы уже записаны на эту роль.", ephemeral=True
            )
            return
        if role_counts[role_name] >= roles[role_name]:
            await interaction.response.send_message("Лимит по этой роли достигнут.", ephemeral=True)
            return
        db.update_signup_role(raid_id, interaction.user.id, role_name)
        promotions = await sync_roster(interaction.client, raid)
        await announce_promotions(interaction.client, raid, promotions)
        await interaction.response.send_message(
            f"Вы записались как **{role_name}** (обновлено).", ephemeral=True
        )
        return

    available_slot = total_count < raid.max_participants and role_counts[role_name] < roles[role_name]

    if wait_entry:
        if available_slot:
            db.remove_waitlist_entry(raid_id, interaction.user.id, suppress_log=True)
            db.add_signup(
                raid_id,
                interaction.user.id,
                role_name,
                int(datetime.now(tz=timezone.utc).timestamp()),
            )
            promotions = await sync_roster(interaction.client, raid)
            await announce_promotions(interaction.client, raid, promotions)
            await interaction.response.send_message(
                "Место освободилось, вы добавлены в основной состав!", ephemeral=True
            )
        else:
            db.update_waitlist_role(raid_id, interaction.user.id, role_name)
            await refresh_message(interaction.client, raid)
            await interaction.response.send_message(
                "Ваш запрос обновлён, вы остаетесь в резерве.", ephemeral=True
            )
        return

    if available_slot:
        db.add_signup(
            raid_id,
            interaction.user.id,
            role_name,
            int(datetime.now(tz=timezone.utc).timestamp()),
        )
        promotions = await sync_roster(interaction.client, raid)
        await announce_promotions(interaction.client, raid, promotions)
        await interaction.response.send_message(
            f"Вы записались как **{role_name}**.", ephemeral=True
        )
        return

    db.add_waitlist_entry(
        raid_id,
        interaction.user.id,
        role_name,
        int(datetime.now(tz=timezone.utc).timestamp()),
    )
    await refresh_message(interaction.client, raid)
    await interaction.response.send_message(
        "Лимит достигнут, вы добавлены в резерв и получите место автоматически.",
        ephemeral=True,
    )


async def handle_unsubscribe(interaction: discord.Interaction, raid_id: int) -> None:
    raid = db.fetch_raid(raid_id)
    if not raid:
        await interaction.response.send_message("Событие не найдено.", ephemeral=True)
        return
    db.remove_signup(raid_id, interaction.user.id)
    db.remove_waitlist_entry(raid_id, interaction.user.id)
    promotions = await sync_roster(interaction.client, raid)
    await announce_promotions(interaction.client, raid, promotions)
    await interaction.response.send_message("Запись снята.", ephemeral=True)


async def refresh_message(client: discord.Client, raid: Raid) -> None:
    if not raid.message_id:
        return
    channel = client.get_channel(raid.channel_id)
    if not isinstance(channel, (discord.TextChannel, discord.Thread)):
        return
    try:
        msg = await channel.fetch_message(raid.message_id)
    except discord.NotFound:
        return
    roles = db.get_roles(raid.id)
    signups = db.get_signups(raid.id)
    waitlist = db.get_waitlist(raid.id)
    await msg.edit(
        embed=make_embed(raid, roles, signups, waitlist),
        view=SignupView(raid.id),
    )


async def sync_roster(client: discord.Client, raid: Raid) -> List[Tuple[int, str]]:
    promoted = db.promote_waitlist(raid.id)
    await refresh_message(client, raid)
    return promoted


async def announce_promotions(
    client: discord.Client, raid: Raid, promotions: List[Tuple[int, str]]
) -> None:
    if not promotions:
        return
    channel = client.get_channel(raid.channel_id)
    if not isinstance(channel, (discord.TextChannel, discord.Thread)):
        return
    mentions = ", ".join(f"<@{user_id}>" for user_id, _ in promotions)
    roles = ", ".join(f"{role}" for _, role in promotions)
    try:
        await channel.send(
            f"{mentions}, для вас освободились места ({roles}). Добро пожаловать в состав!"
        )
    except discord.HTTPException:  # pragma: no cover - ignore send errors
        pass


class TemplateManagementView(discord.ui.View):
    def __init__(self, *, guild_id: int, channel_id: int):
        super().__init__(timeout=600)
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.selected_template: Optional[str] = None
        self.template_select = TemplateChoiceSelect(self)
        self.action_select = TemplateActionSelect(self)
        self.add_item(self.template_select)
        self.add_item(self.action_select)

    def build_options(self) -> List[discord.SelectOption]:
        templates = db.list_templates(self.guild_id)
        return [
            discord.SelectOption(
                label=tpl.name,
                value=tpl.name,
                description=f"Лимит {tpl.max_participants}"
            )
            for tpl in templates
        ]


class TemplateChoiceSelect(discord.ui.Select):
    def __init__(self, management_view: TemplateManagementView):
        self.management_view = management_view
        options = management_view.build_options()
        disabled = False
        if not options:
            options = [
                discord.SelectOption(
                    label="Нет шаблонов",
                    value="__empty__",
                    description="Создайте шаблон через меню действий",
                )
            ]
            disabled = True
        super().__init__(
            placeholder="Выберите шаблон",
            min_values=1,
            max_values=1,
            options=options,
            custom_id=f"template:{management_view.guild_id}:select",
            disabled=disabled,
        )

    def reload_options(self) -> None:
        options = self.management_view.build_options()
        if options:
            self.options = options
            self.disabled = False
        else:
            self.options = [
                discord.SelectOption(
                    label="Нет шаблонов",
                    value="__empty__",
                    description="Создайте шаблон через меню действий",
                )
            ]
            self.disabled = True

    async def callback(self, interaction: discord.Interaction) -> None:  # pragma: no cover - UI callback
        if self.disabled:
            await interaction.response.send_message(
                "Сначала создайте шаблон через меню действий.", ephemeral=True
            )
            return
        value = self.values[0]
        self.management_view.selected_template = value
        await interaction.response.send_message(
            f"Шаблон **{value}** выбран. Теперь выберите действие.", ephemeral=True
        )


class TemplateActionSelect(discord.ui.Select):
    ACTION_CREATE = "create"
    ACTION_USE = "use"
    ACTION_DELETE = "delete"
    ACTION_LIST = "list"

    def __init__(self, management_view: TemplateManagementView):
        self.management_view = management_view
        super().__init__(
            placeholder="Выберите действие",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(label="Создать шаблон", value=self.ACTION_CREATE),
                discord.SelectOption(label="Использовать шаблон", value=self.ACTION_USE),
                discord.SelectOption(label="Удалить шаблон", value=self.ACTION_DELETE),
                discord.SelectOption(label="Показать список", value=self.ACTION_LIST),
            ],
            custom_id=f"template:{management_view.guild_id}:action",
        )

    async def callback(self, interaction: discord.Interaction) -> None:  # pragma: no cover - UI callback
        action = self.values[0]
        if action == self.ACTION_LIST:
            description, _ = list_templates_description(self.management_view.guild_id)
            await interaction.response.send_message(description, ephemeral=True)
            return
        if action == self.ACTION_CREATE:
            await interaction.response.send_modal(TemplateCreateModal(self.management_view))
            return
        template_name = self.management_view.selected_template
        if not template_name:
            await interaction.response.send_message(
                "Сначала выберите шаблон в выпадающем списке.", ephemeral=True
            )
            return
        if action == self.ACTION_USE:
            await interaction.response.send_modal(
                TemplateUseModal(self.management_view, template_name)
            )
            return
        if action == self.ACTION_DELETE:
            await interaction.response.defer(ephemeral=True)
            try:
                message = delete_template(self.management_view.guild_id, template_name)
            except ValueError as exc:
                await interaction.followup.send(f"Ошибка: {exc}", ephemeral=True)
                return
            await interaction.followup.send(message, ephemeral=True)
            self.management_view.selected_template = None
            self.management_view.template_select.reload_options()
            await interaction.message.edit(view=self.management_view)


class TemplateCreateModal(discord.ui.Modal):
    def __init__(self, management_view: TemplateManagementView):
        super().__init__(title="Создать шаблон")
        self.management_view = management_view
        self.template_name = discord.ui.TextInput(label="Название шаблона", max_length=100)
        self.max_participants = discord.ui.TextInput(
            label="Лимит участников", placeholder="Например 20"
        )
        self.roles = discord.ui.TextInput(
            label="Роли и лимиты", placeholder="tank:2, healer:3"
        )
        self.comment = discord.ui.TextInput(
            label="Комментарий", style=discord.TextStyle.paragraph, required=False
        )
        self.reminders = discord.ui.TextInput(
            label="Напоминания", required=False, placeholder="120,30m,10m"
        )

    async def on_submit(self, interaction: discord.Interaction) -> None:  # pragma: no cover - UI callback
        try:
            message = create_or_update_template(
                guild_id=self.management_view.guild_id,
                template_name=self.template_name.value,
                max_participants=self.max_participants.value,
                roles=self.roles.value,
                comment=self.comment.value,
                reminders=self.reminders.value,
            )
        except Exception as exc:  # pragma: no cover - handled via Discord UI
            await interaction.response.send_message(f"Ошибка: {exc}", ephemeral=True)
            return
        description, _ = list_templates_description(self.management_view.guild_id)
        view = TemplateManagementView(
            guild_id=self.management_view.guild_id,
            channel_id=self.management_view.channel_id,
        )
        await interaction.response.send_message(
            f"{message}\n\n{description}", ephemeral=True, view=view
        )


class TemplateUseModal(discord.ui.Modal):
    def __init__(self, management_view: TemplateManagementView, template_name: str):
        super().__init__(title=f"Рейд по шаблону {template_name}")
        self.management_view = management_view
        self.template_name = template_name
        self.event_name = discord.ui.TextInput(
            label="Название рейда", default=template_name, max_length=100
        )
        self.starts_at = discord.ui.TextInput(
            label="Время старта", required=False, placeholder=TIME_FMT
        )
        self.max_participants = discord.ui.TextInput(
            label="Лимит участников", required=False
        )
        self.comment = discord.ui.TextInput(
            label="Комментарий", style=discord.TextStyle.paragraph, required=False
        )
        self.reminders = discord.ui.TextInput(
            label="Напоминания", required=False, placeholder="120,30m,10m"
        )

    async def on_submit(self, interaction: discord.Interaction) -> None:  # pragma: no cover - UI callback
        starts_at_value = self.starts_at.value.strip() or None
        max_participants_value = self.max_participants.value.strip() or None
        comment_value = self.comment.value if self.comment.value.strip() else None
        reminders_value = self.reminders.value.strip() or None
        try:
            raid, roles_data, signups, waitlist = instantiate_template(
                guild_id=self.management_view.guild_id,
                channel_id=self.management_view.channel_id,
                author_id=interaction.user.id,
                template_name=self.template_name,
                event_name=self.event_name.value,
                starts_at=starts_at_value,
                max_participants=max_participants_value,
                comment=comment_value,
                reminders=reminders_value,
            )
        except Exception as exc:  # pragma: no cover - handled via Discord UI
            await interaction.response.send_message(f"Ошибка: {exc}", ephemeral=True)
            return
        embed = make_embed(raid, roles_data, signups, waitlist)
        view = SignupView(raid.id)
        await interaction.response.send_message(embed=embed, view=view)
        msg = await interaction.original_response()
        db.update_message_id(raid.id, msg.id)


__all__ = [
    "LeaveButton",
    "RoleSelect",
    "SignupView",
    "TemplateManagementView",
    "announce_promotions",
    "handle_signup",
    "handle_unsubscribe",
    "refresh_message",
    "sync_roster",
]
