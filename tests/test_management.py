import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

import discord
from discord import app_commands
from app.management import ServerManager, register_commands, role_error, target_error
from app.modules import admin_commands


class Role:
    def __init__(self, guild, position, permissions=None, managed=False, default=False):
        self.guild = guild; self.position = position; self.id = position + 100
        self.permissions = permissions or discord.Permissions.none()
        self.managed = managed; self.default = default
    def __ge__(self, other): return self.position >= other.position
    def is_default(self): return self.default


def fixture():
    guild = NS(id=1, owner_id=1, channels=[])
    def member(id, position, **permissions):
        return NS(id=id, guild=guild, bot=False, top_role=Role(guild, position),
                  guild_permissions=discord.Permissions(**permissions), add_roles=AsyncMock(), remove_roles=AsyncMock(), timeout=AsyncMock())
    actor = member(2, 10, manage_roles=True, moderate_members=True)
    target = member(3, 3)
    guild.me = member(4, 20, manage_roles=True, moderate_members=True)
    guild.fetch_member = AsyncMock(side_effect=lambda id: actor if id == actor.id else target)
    bot = NS(modules=NS(enabled=AsyncMock(return_value=True), set=AsyncMock()), db=NS(fetchval=AsyncMock(return_value=1), execute=AsyncMock()),
             manager=NS(perform=AsyncMock()), intents=NS(members=True))
    interaction = NS(user=actor, guild=guild, guild_id=1, response=NS(defer=AsyncMock(), send_message=AsyncMock()), followup=NS(send=AsyncMock()))
    group = admin_commands(bot, 'Hein', {2})
    register_commands(group, bot, {2})
    return guild, actor, target, bot, interaction, group


class HierarchyTests(unittest.TestCase):
    def test_targets(self):
        guild, actor, target, _, _, _ = fixture()
        self.assertIsNone(target_error(actor, target, guild, guild.me))
        target.top_role = Role(guild, 10)
        self.assertIsNotNone(target_error(actor, target, guild, guild.me))
        target.top_role = Role(guild, 25)
        actor.id = guild.owner_id
        self.assertIsNotNone(target_error(actor, target, guild, guild.me))
        target.id = guild.owner_id
        self.assertIsNotNone(target_error(actor, target, guild, guild.me))

    def test_public_roles_and_manual_permission_escalation(self):
        guild, actor, _, _, _, _ = fixture()
        role = Role(guild, 2)
        self.assertIsNone(role_error(role, guild, guild.me, actor, public=True))
        role.permissions = discord.Permissions(administrator=True)
        self.assertIsNotNone(role_error(role, guild, guild.me, public=True))
        self.assertIsNotNone(role_error(role, guild, guild.me, actor))
        role.permissions = discord.Permissions.none()
        channel = NS(overwrites_for=lambda _: discord.PermissionOverwrite(manage_messages=True), permissions_for=lambda _: discord.Permissions.none())
        guild.channels = [channel]
        self.assertIsNotNone(role_error(role, guild, guild.me, public=True))
        self.assertIsNotNone(role_error(role, guild, guild.me, actor))
        guild.channels = []
        role.managed = True
        self.assertIsNotNone(role_error(role, guild, guild.me))
        role.managed = False; role.default = True
        self.assertIsNotNone(role_error(role, guild, guild.me))
        role.default = False; role.position = 20
        self.assertIsNotNone(role_error(role, guild, guild.me))

    def test_command_payload_serializes(self):
        _, _, _, _, _, group = fixture()
        client = discord.Client(intents=discord.Intents.default())
        tree = app_commands.CommandTree(client)
        tree.add_command(group)
        payload = group.to_dict(tree)
        self.assertEqual(payload['name'], 'hein')
        self.assertEqual(len(payload['options']), 14)


class CommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_owner_ids_do_not_bypass_moderation_permissions(self):
        _, actor, target, bot, interaction, group = fixture()
        actor.guild_permissions = discord.Permissions.none()
        await group.get_command('timeout').callback(interaction, target, 10, 'Test')
        bot.manager.perform.assert_not_awaited()
        target.timeout.assert_not_awaited()
        self.assertIn('Berechtigung', interaction.followup.send.call_args.args[0])

    async def test_disabled_module_and_high_targets(self):
        guild, _, target, bot, interaction, group = fixture()
        bot.modules.enabled.return_value = False
        await group.get_command('rolle').callback(interaction, target, Role(guild, 2), 'geben')
        bot.manager.perform.assert_not_awaited()
        bot.modules.enabled.return_value = True
        target.top_role = Role(guild, 10)
        await group.get_command('timeout').callback(interaction, target, 10, 'Test')
        bot.manager.perform.assert_not_awaited()

    async def test_role_whitelist_and_changed_permissions(self):
        guild, actor, _, bot, interaction, group = fixture()
        role = Role(guild, 2)
        bot.db.fetchval.return_value = None
        await group.get_command('selbstrolle').callback(interaction, role, 'nehmen')
        bot.manager.perform.assert_not_awaited()
        bot.db.fetchval.return_value = 1
        role.permissions = discord.Permissions(manage_roles=True)
        await group.get_command('selbstrolle').callback(interaction, role, 'nehmen')
        bot.manager.perform.assert_not_awaited()
        role.permissions = discord.Permissions.none()
        await group.get_command('selbstrolle').callback(interaction, role, 'nehmen')
        operation = bot.manager.perform.call_args.args[5]
        await operation('Test')
        actor.add_roles.assert_awaited_once_with(role, reason='Test')

    async def test_timeout_and_remove(self):
        _, _, target, bot, interaction, group = fixture()
        await group.get_command('timeout').callback(interaction, target, 10, 'Test')
        await bot.manager.perform.call_args.args[5]('Test')
        self.assertEqual(target.timeout.call_args.args[0].total_seconds(), 600)
        await group.get_command('timeout').callback(interaction, target, 0, 'Test')
        await bot.manager.perform.call_args.args[5]('Test')
        self.assertIsNone(target.timeout.call_args.args[0])
        target.guild_permissions = discord.Permissions(administrator=True)
        before = bot.manager.perform.await_count
        await group.get_command('timeout').callback(interaction, target, 10, 'Test')
        self.assertEqual(bot.manager.perform.await_count, before)

    async def test_autorole_requires_intent_and_nonprivileged_role(self):
        guild, actor, _, bot, interaction, group = fixture()
        actor.id = guild.owner_id
        bot.intents.members = False
        await group.get_command('autorolle').callback(interaction, 'on', Role(guild, 2))
        bot.db.execute.assert_not_awaited()
        bot.intents.members = True
        await group.get_command('autorolle').callback(interaction, 'on', Role(guild, 2, discord.Permissions(administrator=True)))
        bot.db.execute.assert_not_awaited()
        await group.get_command('autorolle').callback(interaction, 'on', Role(guild, 2))
        bot.db.execute.assert_awaited_once()

    async def test_purge_checks_channel_permissions_and_preserves_pins(self):
        from datetime import datetime, timedelta, timezone
        _, actor, _, bot, interaction, group = fixture()
        class Channel:
            id = 9
            purge = AsyncMock(return_value=[NS()])
            def permissions_for(self, member):
                return discord.Permissions(manage_messages=True, view_channel=True, read_message_history=True)
        channel = Channel(); interaction.channel = channel
        interaction.created_at = datetime.now(timezone.utc)
        with patch('app.management.discord.TextChannel', Channel):
            await group.get_command('clean').callback(interaction, 10, 'Spam')
        await bot.manager.perform.call_args.args[5]('Test')
        kwargs = channel.purge.call_args.kwargs
        self.assertEqual(kwargs['before'], interaction.created_at)
        self.assertFalse(kwargs['check'](NS(pinned=True, created_at=interaction.created_at)))
        self.assertFalse(kwargs['check'](NS(pinned=False, created_at=interaction.created_at - timedelta(days=15))))
        self.assertTrue(kwargs['check'](NS(pinned=False, created_at=interaction.created_at)))
        self.assertEqual(kwargs['limit'], 10)
        channel.permissions_for = lambda _: discord.Permissions.none()
        before = bot.manager.perform.await_count
        with patch('app.management.discord.TextChannel', Channel):
            await group.get_command('clean').callback(interaction, 10, 'Spam')
        self.assertEqual(bot.manager.perform.await_count, before)


class LoggingTests(unittest.IsolatedAsyncioTestCase):
    async def test_database_failure_prevents_action_when_logging_required(self):
        _, _, _, bot, interaction, _ = fixture()
        manager = ServerManager(bot)
        manager.begin_log = AsyncMock(side_effect=RuntimeError('database offline'))
        operation = AsyncMock()
        with self.assertRaises(RuntimeError):
            await manager.perform(interaction, 'timeout', 3, 'Test', {}, operation, lambda _: 'OK')
        operation.assert_not_awaited()

    async def test_success_and_discord_failure_are_recorded(self):
        _, _, _, bot, interaction, _ = fixture()
        manager = ServerManager(bot)
        manager.begin_log = AsyncMock(return_value=4)
        manager.finish_log = AsyncMock(return_value='')
        operation = AsyncMock(return_value={'deleted': 2})
        await manager.perform(interaction, 'clean', None, 'Test', {}, operation, lambda _: 'OK')
        self.assertEqual(manager.finish_log.call_args.args[2], 'success')
        operation.side_effect = discord.Forbidden(NS(status=403, reason='Forbidden'), 'denied')
        await manager.perform(interaction, 'clean', None, 'Test', {}, operation, lambda _: 'OK')
        self.assertEqual(manager.finish_log.call_args.args[2], 'failed')
        self.assertIn('nicht vollständig', interaction.followup.send.call_args.args[0])

    async def test_autorole_skips_bots_disabled_and_changed_privileges(self):
        guild, _, target, bot, _, _ = fixture()
        manager = ServerManager(bot)
        manager.begin_log = AsyncMock(return_value=None)
        manager.finish_log = AsyncMock(return_value='')
        bot.user = NS(id=4)
        role = Role(guild, 2); guild.get_role = lambda _: role
        target.bot = True
        await manager.on_join(target)
        target.add_roles.assert_not_awaited()
        target.bot = False; bot.modules.enabled.return_value = False
        await manager.on_join(target)
        target.add_roles.assert_not_awaited()
        bot.modules.enabled.return_value = True
        role.permissions = discord.Permissions(administrator=True)
        with self.assertLogs('winston', level='WARNING'):
            await manager.on_join(target)
        target.add_roles.assert_not_awaited()
        role.permissions = discord.Permissions.none()
        await manager.on_join(target)
        target.add_roles.assert_awaited_once()

class AuditPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_modlog_off_skips_storage_and_on_stores_pending(self):
        _, _, _, bot, _, _ = fixture()
        manager = ServerManager(bot)
        bot.modules.enabled.return_value = False
        self.assertIsNone(await manager.begin_log(1, 2, 3, 'timeout', 'Test', {'minutes': 10}))
        bot.db.fetchval.assert_not_awaited()
        bot.modules.enabled.return_value = True
        await manager.begin_log(1, 2, 3, 'timeout', 'Test', {'minutes': 10})
        args = bot.db.fetchval.call_args.args
        self.assertEqual(args[1:6], (1, 2, 3, 'timeout', 'Test'))
        self.assertIn('RETURNING id', args[0])

    async def test_log_channel_failure_keeps_database_record(self):
        guild, _, _, bot, _, _ = fixture()
        bot.db.fetchrow = AsyncMock(return_value={'actor_id': 2, 'target_id': 3, 'action': 'timeout', 'reason': 'Test'})
        bot.db.fetchval.return_value = 9
        guild.get_channel = lambda _: None
        manager = ServerManager(bot)
        with self.assertLogs('winston', level='ERROR'):
            warning = await manager.finish_log(7, guild, 'success', {})
        bot.db.fetchrow.assert_awaited_once()
        self.assertEqual(bot.db.fetchrow.call_args.args[1:], (7, 'success', '{}', 1))
        self.assertIn('Datenbankeintrag ist gespeichert', warning)

    async def test_database_finalize_failure_reports_actual_success(self):
        guild, _, _, bot, _, _ = fixture()
        bot.db.fetchrow = AsyncMock(side_effect=RuntimeError('offline'))
        manager = ServerManager(bot)
        with self.assertLogs('winston', level='ERROR'):
            warning = await manager.finish_log(7, guild, 'success', {})
        self.assertIn('Aktion wurde ausgeführt', warning)

if __name__ == '__main__':
    unittest.main()
