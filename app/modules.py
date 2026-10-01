"""Persistent, server-scoped feature switches and their administration commands."""
import re
from typing import Literal

import discord
from discord import app_commands

DEFAULTS = {"reminder": True, "search": False}


def command_name(name):
    return re.sub(r"[^a-z0-9_-]+", "-", name.casefold()).strip("-")[:32] or "bot"


def can_manage(user, guild, owner_ids):
    return bool(guild and (user.id == guild.owner_id or user.id in owner_ids or
                           getattr(user, "guild_permissions", discord.Permissions.none()).administrator))


class ModuleSettings:
    def __init__(self, pool):
        self.pool = pool

    async def setup(self):
        await self.pool.execute("""CREATE TABLE IF NOT EXISTS bot_modules (
            guild_id BIGINT NOT NULL, module TEXT NOT NULL, enabled BOOLEAN NOT NULL,
            PRIMARY KEY (guild_id, module))""")

    async def enabled(self, guild_id, module):
        value = await self.pool.fetchval("SELECT enabled FROM bot_modules WHERE guild_id=$1 AND module=$2", guild_id, module)
        return DEFAULTS[module] if value is None else value

    async def set(self, guild_id, module, enabled):
        await self.pool.execute("""INSERT INTO bot_modules (guild_id,module,enabled) VALUES ($1,$2,$3)
            ON CONFLICT (guild_id,module) DO UPDATE SET enabled=EXCLUDED.enabled""", guild_id, module, enabled)


def admin_commands(bot, name, owner_ids):
    # Keep visible for explicitly permitted IDs too; every invocation is checked here.
    group = app_commands.Group(name=command_name(name), description="Bot-Module verwalten", guild_only=True)

    async def allowed(interaction):
        if not can_manage(interaction.user, interaction.guild, owner_ids):
            await interaction.response.send_message("Das dürfen nur Serverbesitzer, Administratoren und freigeschaltete Bot-Owner.", ephemeral=True)
            return False
        await interaction.response.defer(ephemeral=True)
        return True

    async def toggle(interaction, module, state):
        if not await allowed(interaction):
            return
        await bot.modules.set(interaction.guild_id, module, state == "on")
        suffix = " Bereits gespeicherte Erinnerungen werden weiterhin zugestellt." if module == "reminder" and state == "off" else ""
        await interaction.followup.send(f"{module}: {'an' if state == 'on' else 'aus'}.{suffix}", ephemeral=True)

    @group.command(name="reminder", description="Neue Erinnerungen ein- oder ausschalten")
    async def reminder(interaction: discord.Interaction, status: Literal["on", "off"]):
        await toggle(interaction, "reminder", status)

    @group.command(name="search", description="Websuche ein- oder ausschalten")
    async def search(interaction: discord.Interaction, status: Literal["on", "off"]):
        await toggle(interaction, "search", status)

    @group.command(name="modules", description="Modulstatus dieses Servers anzeigen")
    async def modules(interaction: discord.Interaction):
        if not await allowed(interaction):
            return
        lines = [f"{key}: {'an' if await bot.modules.enabled(interaction.guild_id, key) else 'aus'}" for key in DEFAULTS]
        await interaction.followup.send("Module auf diesem Server:\n" + "\n".join(lines), ephemeral=True)

    return group
