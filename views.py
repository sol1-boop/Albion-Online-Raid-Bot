"""Discord UI components and interaction handlers."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List

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
        )
        self.raid_id = raid_id

    async def callback(self, interaction: discord.Interaction) -> None:  # pragma: no cover - UI callback
        role = self.values[0]
        await handle_signup(interaction, self.raid_id, role)


class LeaveButton(discord.ui.Button):
    def __init__(self, raid_id: int):
        super().__init__(label="Снять запись", style=discord.ButtonStyle.secondary)
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
    signups = db.get_signups(raid_id)
    signups_without_user = [s for s in signups if s.user_id != interaction.user.id]

    total_after = len(signups_without_user) + 1
    if total_after > raid.max_participants:
        await interaction.response.send_message("Лимит участников достигнут.", ephemeral=True)
        return

    role_after = len([s for s in signups_without_user if s.role_name == role_name]) + 1
    if role_after > roles[role_name]:
        await interaction.response.send_message("Лимит по этой роли достигнут.", ephemeral=True)
        return

    if current_signup:
        db.update_signup_role(raid_id, interaction.user.id, role_name)
    else:
        db.add_signup(
            raid_id,
            interaction.user.id,
            role_name,
            int(datetime.now(tz=timezone.utc).timestamp()),
        )

    await refresh_message(interaction.client, raid)
    await interaction.response.send_message(
        f"Вы записались как **{role_name}**.", ephemeral=True
    )


async def handle_unsubscribe(interaction: discord.Interaction, raid_id: int) -> None:
    raid = db.fetch_raid(raid_id)
    if not raid:
        await interaction.response.send_message("Событие не найдено.", ephemeral=True)
        return
    db.remove_signup(raid_id, interaction.user.id)
    await refresh_message(interaction.client, raid)
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
    await msg.edit(embed=make_embed(raid, roles, signups), view=SignupView(raid.id))


__all__ = [
    "LeaveButton",
    "RoleSelect",
    "SignupView",
    "handle_signup",
    "handle_unsubscribe",
    "refresh_message",
]
