"""Server management: explicit commands, Discord permission checks and optional audit logs."""
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Literal

import discord
from discord import app_commands
from .modules import can_manage

log = logging.getLogger('winston')
PRIVILEGED = discord.Permissions(administrator=True, manage_guild=True, manage_roles=True,
    manage_channels=True, manage_messages=True, manage_webhooks=True, manage_threads=True,
    manage_events=True, manage_expressions=True, manage_nicknames=True, moderate_members=True,
    kick_members=True, ban_members=True, mute_members=True, deafen_members=True,
    move_members=True, view_audit_log=True, mention_everyone=True).value


def has_permission(member, permission):
    return member.guild_permissions.administrator or getattr(member.guild_permissions, permission)


def target_error(actor, target, guild, bot_member):
    if target.guild.id != guild.id or actor.guild.id != guild.id:
        return 'Die Person gehört nicht zu diesem Server.'
    if target.id == guild.owner_id or target.bot or target.id == actor.id:
        return 'Serverbesitzer, Bots und dich selbst bearbeite ich hier nicht.'
    if actor.id != guild.owner_id and target.top_role >= actor.top_role:
        return 'Die Person steht in der Rollenhierarchie auf deiner Höhe oder darüber.'
    if target.top_role >= bot_member.top_role:
        return 'Meine Bot-Rolle muss über der höchsten Rolle dieser Person stehen.'
    return None


def role_error(role, guild, bot_member, actor=None, public=False):
    if role.guild.id != guild.id or role.is_default() or role.managed:
        return 'Diese Rolle kann ich nicht vergeben.'
    if not has_permission(bot_member, 'manage_roles') or role >= bot_member.top_role:
        return 'Ich brauche Rollen verwalten und eine Bot-Rolle oberhalb dieser Rolle.'
    if actor is not None and actor.id != guild.owner_id:
        if role >= actor.top_role:
            return 'Die Rolle muss unter deiner höchsten Rolle stehen.'
        if not actor.guild_permissions.administrator and role.permissions.value & ~actor.guild_permissions.value:
            return 'Diese Rolle würde Rechte vergeben, die du selbst nicht hast.'
    if actor is not None and actor.id != guild.owner_id and not actor.guild_permissions.administrator:
        for channel in guild.channels:
            allowed = channel.overwrites_for(role).pair()[0].value
            if allowed & ~channel.permissions_for(actor).value:
                return 'Diese Rolle würde in einem Kanal Rechte vergeben, die du dort selbst nicht hast.'
    if public:
        if role.permissions.value & PRIVILEGED:
            return 'Rollen mit Verwaltungs- oder Moderationsrechten sind für Selbstwahl und automatische Vergabe gesperrt.'
        for channel in guild.channels:
            if channel.overwrites_for(role).pair()[0].value & PRIVILEGED:
                return 'Die Rolle erhält in einem Kanal Verwaltungsrechte und ist deshalb gesperrt.'
    return None


class ServerManager:
    def __init__(self, bot):
        self.bot = bot

    async def setup(self):
        await self.bot.db.execute('''CREATE TABLE IF NOT EXISTS server_management (
            guild_id BIGINT PRIMARY KEY, autorole_id BIGINT, log_channel_id BIGINT)''')
        await self.bot.db.execute('''CREATE TABLE IF NOT EXISTS self_roles (
            guild_id BIGINT NOT NULL, role_id BIGINT NOT NULL, PRIMARY KEY (guild_id,role_id))''')
        await self.bot.db.execute('''CREATE TABLE IF NOT EXISTS moderation_log (
            id BIGSERIAL PRIMARY KEY, guild_id BIGINT NOT NULL, actor_id BIGINT NOT NULL,
            target_id BIGINT, action TEXT NOT NULL, reason TEXT NOT NULL, details JSONB NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending', created_at TIMESTAMPTZ NOT NULL DEFAULT now())''')
        await self.bot.db.execute('CREATE INDEX IF NOT EXISTS moderation_log_guild_idx ON moderation_log (guild_id,id DESC)')

    async def begin_log(self, guild_id, actor_id, target_id, action, reason, details):
        if not await self.bot.modules.enabled(guild_id, 'modlog'):
            return None
        return await self.bot.db.fetchval('''INSERT INTO moderation_log
            (guild_id,actor_id,target_id,action,reason,details) VALUES ($1,$2,$3,$4,$5,$6) RETURNING id''',
            guild_id, actor_id, target_id, action, reason, json.dumps(details, ensure_ascii=False))

    async def finish_log(self, entry_id, guild, status, details):
        if entry_id is None:
            return ''
        try:
            row = await self.bot.db.fetchrow('''UPDATE moderation_log SET status=$2, details=details || $3::jsonb
                WHERE id=$1 AND guild_id=$4 RETURNING actor_id,target_id,action,reason''',
                entry_id, status, json.dumps(details, ensure_ascii=False), guild.id)
        except Exception:
            log.exception('Could not finalize moderation log %s', entry_id)
            return ' Die Aktion wurde ausgeführt, aber der Logstatus konnte nicht aktualisiert werden.' if status == 'success' else ' Der Logstatus konnte nicht aktualisiert werden.'
        if not row:
            return ' Der Logeintrag wurde nicht gefunden.'
        channel_id = await self.bot.db.fetchval('SELECT log_channel_id FROM server_management WHERE guild_id=$1', guild.id)
        if not channel_id:
            return ''
        channel = guild.get_channel(channel_id)
        try:
            if not isinstance(channel, discord.TextChannel):
                raise ValueError('Log channel unavailable')
            text = (f"Mod-Log #{entry_id} · {row['action']} · {status}\n"
                    f"Ausgeführt von: {row['actor_id']} · Ziel: {row['target_id'] or '–'}\n"
                    f"Grund: {discord.utils.escape_markdown(row['reason'])}\n"
                    f"Details: {json.dumps(details, ensure_ascii=False)}")
            await channel.send(text[:1900], allowed_mentions=discord.AllowedMentions.none())
        except (discord.HTTPException, ValueError):
            log.exception('Could not send moderation log %s', entry_id)
            return ' Im Logkanal konnte ich nicht schreiben; der Datenbankeintrag ist gespeichert.'
        return ''

    async def perform(self, interaction, action, target_id, reason, details, operation, success):
        reason = reason.strip()[:300] or 'Kein Grund angegeben'
        entry = await self.begin_log(interaction.guild_id, interaction.user.id, target_id, action, reason, details)
        try:
            result = await operation(f"{interaction.user.id}: {reason}"[:512])
        except discord.HTTPException:
            warning = await self.finish_log(entry, interaction.guild, 'failed', {'error': 'Discord request failed'})
            await interaction.followup.send('Discord konnte die Aktion nicht vollständig ausführen. Prüfe Rechte und Rollenhierarchie.' + warning, ephemeral=True)
            return
        extra = result if isinstance(result, dict) else {}
        warning = await self.finish_log(entry, interaction.guild, 'success', extra)
        await interaction.followup.send(success(extra) + warning, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    async def on_join(self, member):
        if member.bot or not await self.bot.modules.enabled(member.guild.id, 'roles'):
            return
        role_id = await self.bot.db.fetchval('SELECT autorole_id FROM server_management WHERE guild_id=$1', member.guild.id)
        role = member.guild.get_role(role_id) if role_id else None
        if not role:
            return
        error = role_error(role, member.guild, member.guild.me, public=True)
        if error:
            log.warning('Autorole %s in guild %s rejected: %s', role_id, member.guild.id, error)
            return
        entry = await self.begin_log(member.guild.id, self.bot.user.id, member.id, 'autorole', 'Automatische Beitrittsrolle', {'role_id': role.id})
        try:
            await member.add_roles(role, reason='Automatische Beitrittsrolle')
        except discord.HTTPException:
            await self.finish_log(entry, member.guild, 'failed', {'error': 'Discord request failed'})
            log.exception('Autorole assignment failed')
        else:
            await self.finish_log(entry, member.guild, 'success', {})


def register_commands(group, bot, owner_ids):
    async def start(interaction, module=None, permission=None, admin=False):
        if interaction.guild is None:
            await interaction.response.send_message('Das geht nur auf einem Server.', ephemeral=True)
            return None
        await interaction.response.defer(ephemeral=True)
        actor = await interaction.guild.fetch_member(interaction.user.id)
        if admin and not can_manage(actor, interaction.guild, owner_ids):
            await interaction.followup.send('Das dürfen nur Serverbesitzer, Administratoren und freigeschaltete Bot-Owner.', ephemeral=True)
            return None
        if permission and not has_permission(actor, permission):
            await interaction.followup.send('Dir fehlt die passende Discord-Berechtigung für diese Aktion.', ephemeral=True)
            return None
        if module and not await bot.modules.enabled(interaction.guild_id, module):
            await interaction.followup.send(f'Das Modul {module} ist ausgeschaltet.', ephemeral=True)
            return None
        return actor

    async def reject(interaction, text):
        await interaction.followup.send(text, ephemeral=True)

    @group.command(name='roles', description='Rollenmodul ein- oder ausschalten')
    async def roles(interaction: discord.Interaction, status: Literal['on', 'off']):
        if await start(interaction, admin=True):
            await bot.modules.set(interaction.guild_id, 'roles', status == 'on')
            await reject(interaction, f'Rollenmodul: {status}.')

    @group.command(name='moderation', description='Nachrichtenlöschen und Timeouts ein- oder ausschalten')
    async def moderation(interaction: discord.Interaction, status: Literal['on', 'off']):
        if await start(interaction, admin=True):
            await bot.modules.set(interaction.guild_id, 'moderation', status == 'on')
            await reject(interaction, f'Moderation: {status}.')

    @group.command(name='modlog', description='Mod-Log aktivieren, deaktivieren und optional Textkanal festlegen')
    async def modlog(interaction: discord.Interaction, status: Literal['on', 'off'], kanal: discord.TextChannel | None = None):
        if not await start(interaction, admin=True):
            return
        if kanal:
            perms = kanal.permissions_for(interaction.guild.me)
            if kanal.guild.id != interaction.guild_id or not perms.view_channel or not perms.send_messages:
                return await reject(interaction, 'Im Logkanal brauche ich Kanal anzeigen und Nachrichten senden.')
            await bot.db.execute('''INSERT INTO server_management (guild_id,log_channel_id) VALUES ($1,$2)
                ON CONFLICT (guild_id) DO UPDATE SET log_channel_id=EXCLUDED.log_channel_id''', interaction.guild_id, kanal.id)
        await bot.modules.set(interaction.guild_id, 'modlog', status == 'on')
        await reject(interaction, f'Mod-Log: {status}. Mit Kanal werden Einträge dort und in PostgreSQL gespeichert; ohne Kanal nur in PostgreSQL.')

    @group.command(name='rolle', description='Einem Mitglied eine Rolle geben oder entfernen')
    async def rolle(interaction: discord.Interaction, mitglied: discord.Member, rolle: discord.Role,
                    aktion: Literal['geben', 'entfernen'], grund: str = 'Rollenverwaltung'):
        actor = await start(interaction, 'roles', 'manage_roles')
        if not actor:
            return
        member = await interaction.guild.fetch_member(mitglied.id)
        error = target_error(actor, member, interaction.guild, interaction.guild.me) or role_error(rolle, interaction.guild, interaction.guild.me, actor)
        if error:
            return await reject(interaction, error)
        async def operation(reason):
            method = member.add_roles if aktion == 'geben' else member.remove_roles
            await method(rolle, reason=reason)
        await bot.manager.perform(interaction, 'role_' + aktion, member.id, grund, {'role_id': rolle.id}, operation, lambda _: 'Rolle geändert. Ordnung muss sein.')

    @group.command(name='rollenfreigabe', description='Unprivilegierte Rolle für die Selbstauswahl freigeben oder sperren')
    async def rollenfreigabe(interaction: discord.Interaction, rolle: discord.Role, status: Literal['on', 'off']):
        actor = await start(interaction, admin=True)
        if not actor:
            return
        if status == 'on':
            error = role_error(rolle, interaction.guild, interaction.guild.me, actor, public=True)
            if error:
                return await reject(interaction, error)
            await bot.db.execute('INSERT INTO self_roles (guild_id,role_id) VALUES ($1,$2) ON CONFLICT DO NOTHING', interaction.guild_id, rolle.id)
        else:
            await bot.db.execute('DELETE FROM self_roles WHERE guild_id=$1 AND role_id=$2', interaction.guild_id, rolle.id)
        await reject(interaction, f'Selbstauswahl für diese Rolle: {status}.')

    @group.command(name='selbstrolle', description='Eine freigegebene Rolle selbst nehmen oder ablegen')
    async def selbstrolle(interaction: discord.Interaction, rolle: discord.Role, aktion: Literal['nehmen', 'ablegen']):
        actor = await start(interaction, 'roles')
        if not actor:
            return
        allowed = await bot.db.fetchval('SELECT 1 FROM self_roles WHERE guild_id=$1 AND role_id=$2', interaction.guild_id, rolle.id)
        if not allowed:
            return await reject(interaction, 'Diese Rolle ist nicht zur Selbstauswahl freigegeben.')
        error = role_error(rolle, interaction.guild, interaction.guild.me, public=True)
        if error:
            return await reject(interaction, error)
        async def operation(reason):
            method = actor.add_roles if aktion == 'nehmen' else actor.remove_roles
            await method(rolle, reason=reason)
        await bot.manager.perform(interaction, 'self_role_' + aktion, actor.id, 'Selbstauswahl', {'role_id': rolle.id}, operation, lambda _: 'Rolle geändert. Steht dir bestimmt.')

    @group.command(name='selbstrollen', description='Freigegebene Rollen anzeigen')
    async def selbstrollen(interaction: discord.Interaction):
        if not await start(interaction, 'roles'):
            return
        rows = await bot.db.fetch('SELECT role_id FROM self_roles WHERE guild_id=$1 ORDER BY role_id LIMIT 50', interaction.guild_id)
        names = []
        for row in rows:
            role = interaction.guild.get_role(row['role_id'])
            if role and not role_error(role, interaction.guild, interaction.guild.me, public=True):
                names.append(discord.utils.escape_markdown(role.name)[:60])
        await interaction.followup.send(('Freigegeben: ' + ', '.join(names))[:1800] if names else 'Keine Rollen freigegeben.', ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    @group.command(name='autorolle', description='Rolle für neue Mitglieder festlegen oder automatische Vergabe ausschalten')
    async def autorolle(interaction: discord.Interaction, status: Literal['on', 'off'], rolle: discord.Role | None = None):
        actor = await start(interaction, admin=True)
        if not actor:
            return
        if status == 'on':
            if not bot.intents.members:
                return await reject(interaction, 'Aktiviere MEMBER_EVENTS_ENABLED=true und im Developer Portal Server Members Intent. Danach den Bot neu starten.')
            if not rolle:
                return await reject(interaction, 'Wähle die Rolle für neue Mitglieder.')
            error = role_error(rolle, interaction.guild, interaction.guild.me, actor, public=True)
            if error:
                return await reject(interaction, error)
        await bot.db.execute('''INSERT INTO server_management (guild_id,autorole_id) VALUES ($1,$2)
            ON CONFLICT (guild_id) DO UPDATE SET autorole_id=EXCLUDED.autorole_id''', interaction.guild_id, rolle.id if status == 'on' else None)
        await reject(interaction, f'Automatische Beitrittsrolle: {status}. Vergabe erfolgt nur bei eingeschaltetem Rollenmodul, nur für neue Mitglieder.')

    @group.command(name='timeout', description='Mitglied zeitlich sperren; 0 Minuten hebt einen Timeout auf')
    async def timeout(interaction: discord.Interaction, mitglied: discord.Member,
                      minuten: app_commands.Range[int, 0, 40320], grund: str):
        actor = await start(interaction, 'moderation', 'moderate_members')
        if not actor:
            return
        if not 0 <= minuten <= 40320:
            return await reject(interaction, 'Timeouts reichen von 0 bis 40320 Minuten.')
        member = await interaction.guild.fetch_member(mitglied.id)
        error = target_error(actor, member, interaction.guild, interaction.guild.me)
        if error:
            return await reject(interaction, error)
        if member.guild_permissions.administrator or not has_permission(interaction.guild.me, 'moderate_members'):
            return await reject(interaction, 'Administratoren kann ich nicht sperren. Außerdem brauche ich Mitglieder in Timeout versetzen.')
        if not grund.strip():
            return await reject(interaction, 'Gib einen Grund an.')
        async def operation(reason):
            await member.timeout(timedelta(minutes=minuten) if minuten else None, reason=reason)
        await bot.manager.perform(interaction, 'timeout', member.id, grund, {'minutes': minuten}, operation, lambda _: f'Timeout gesetzt: {minuten} Minuten.' if minuten else 'Timeout aufgehoben.')

    @group.command(name='clean', description='Letzte Nachrichten im aktuellen Textkanal löschen; angeheftete bleiben')
    async def clean(interaction: discord.Interaction, anzahl: app_commands.Range[int, 1, 100], grund: str):
        actor = await start(interaction, 'moderation')
        if not actor:
            return
        if not 1 <= anzahl <= 100:
            return await reject(interaction, 'Wähle zwischen 1 und 100 Nachrichten.')
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            return await reject(interaction, 'Das geht nur in einem normalen Server-Textkanal.')
        perms = channel.permissions_for(actor)
        bot_perms = channel.permissions_for(interaction.guild.me)
        if not (perms.manage_messages and perms.view_channel and perms.read_message_history and bot_perms.manage_messages and bot_perms.view_channel and bot_perms.read_message_history):
            return await reject(interaction, 'Du und ich brauchen hier Nachrichten verwalten, Kanal anzeigen und Nachrichtenverlauf lesen.')
        if not grund.strip():
            return await reject(interaction, 'Gib einen Grund an.')
        async def operation(reason):
            cutoff = datetime.now(timezone.utc) - timedelta(days=14)
            removed = await channel.purge(limit=anzahl, before=interaction.created_at,
                check=lambda msg: not msg.pinned and msg.created_at > cutoff, reason=reason)
            return {'deleted': len(removed), 'channel_id': channel.id}
        await bot.manager.perform(interaction, 'clean', None, grund, {'requested': anzahl, 'channel_id': channel.id}, operation, lambda data: f"{data['deleted']} Nachrichten gelöscht. Tresen ist sauber.")

    @group.command(name='modlogs', description='Letzte zehn protokollierte Bot-Aktionen anzeigen')
    async def modlogs(interaction: discord.Interaction):
        if not await start(interaction, admin=True):
            return
        rows = await bot.db.fetch('SELECT id,actor_id,target_id,action,status,created_at FROM moderation_log WHERE guild_id=$1 ORDER BY id DESC LIMIT 10', interaction.guild_id)
        lines = [f"#{row['id']} · {row['created_at']:%d.%m.%Y %H:%M} UTC · {row['action']} · {row['status']} · von {row['actor_id']} an {row['target_id'] or '–'}" for row in rows]
        await reject(interaction, '\n'.join(lines) if lines else 'Noch keine Mod-Logs gespeichert.')

    @group.error
    async def command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
        log.error('Server management command failed', exc_info=(type(error), error, error.__traceback__))
        text = 'Die Aktion wurde nicht sicher abgeschlossen. Prüfe Bot-Rechte, Rollenhierarchie und den Mod-Log, bevor du sie wiederholst.'
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)
