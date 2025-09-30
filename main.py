"""Raid bot entrypoint."""
from __future__ import annotations

import sys

import discord
from discord.ext import commands as discord_commands

import commands as raid_commands
import db
from config import TOKEN, log
from views import SignupView


def create_bot() -> discord_commands.Bot:
    intents = discord.Intents.default()
    intents.message_content = False
    intents.members = True
    bot = discord_commands.Bot(command_prefix="!", intents=intents)
    return bot


bot = create_bot()


@bot.event
async def on_ready() -> None:
    db.init_db()
    try:
        bot.tree.add_command(raid_commands.raid_group)
    except Exception:
        pass
    await bot.tree.sync()
    log.info("Logged in as %s (ID: %s)", bot.user, getattr(bot.user, "id", "unknown"))
    for raid_id in db.list_raid_ids():
        bot.add_view(SignupView(raid_id))


def main(argv: list[str] | None = None) -> None:
    if argv is None:
        argv = sys.argv[1:]
    if not TOKEN:
        raise SystemExit(
            "Не найден токен: укажите DISCORD_TOKEN в .env/окружении или положите его в token.txt",
        )
    bot.run(TOKEN)


if __name__ == "__main__":
    main()
