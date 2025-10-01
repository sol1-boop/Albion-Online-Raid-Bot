"""Discord UI components and interaction handlers."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Tuple

import discord

import db
from models import Raid
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
            db.remove_waitlist_entry(raid_id, interaction.user.id)
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


__all__ = [
    "LeaveButton",
    "RoleSelect",
    "SignupView",
    "announce_promotions",
    "handle_signup",
    "handle_unsubscribe",
    "refresh_message",
    "sync_roster",
]
