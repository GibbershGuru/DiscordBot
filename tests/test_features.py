import asyncio
import json
import os
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

from app.reminders import parse_reminder, schedule_reply, merge_schedule
from app.modules import ModuleSettings, can_manage, command_name, admin_commands
from app.search import cited_answer, search_options
from app.main import Winston
import discord

TZ = ZoneInfo('Europe/Berlin')
NOW = datetime(2026, 10, 1, 18, 40, tzinfo=TZ)

class ParserTests(unittest.TestCase):
    def parse(self, text):
        return parse_reminder('erinnere mich ' + text, TZ, NOW)

    def test_evening_and_followup(self):
        text = 'erinnere mich heute Abend daran das ich duschen muss, weil ich stinke'
        p = parse_reminder(text, TZ, NOW)
        self.assertTrue(p.needs_time)
        self.assertFalse(p.needs_day)
        self.assertIn('weil ich stinke', p.text)
        p = parse_reminder(merge_schedule(text, schedule_reply('20 Uhr')), TZ, NOW)
        self.assertEqual(p.due_at.astimezone(TZ), NOW.replace(hour=20, minute=0))

    def test_missing_parts(self):
        self.assertTrue(self.parse('daran das ich duschen muss').needs_day)
        self.assertTrue(self.parse('daran das ich duschen muss').needs_time)
        self.assertTrue(self.parse('um 20 Uhr duschen').needs_day)
        self.assertTrue(self.parse('in 5 Tagen zum Friseur').needs_time)
        q = merge_schedule('erinnere mich in 5 Tagen zum Friseur', schedule_reply('18 Uhr'))
        self.assertEqual(parse_reminder(q, TZ, NOW).due_at.astimezone(TZ), (NOW + timedelta(days=5)).replace(hour=18, minute=0))

    def test_dates_and_relative(self):
        self.assertEqual(self.parse('in 8min duschen').due_at, (NOW + timedelta(minutes=8)).astimezone(ZoneInfo('UTC')))
        self.assertTrue(self.parse('am 27.12.2026 Weihnachten ist vorbei').needs_time)
        self.assertFalse(self.parse('am 27.12.2026 um 18 Uhr Weihnachten ist vorbei').error)
        self.assertTrue(self.parse('heute um 2 Uhr duschen').error)
        self.assertTrue(self.parse('am 31.02.2027 um 18 Uhr duschen').error)
        self.assertIsNone(schedule_reply('mach mal einen Witz'))
        self.assertIsNone(schedule_reply('25 Uhr'))

    def test_followups_and_dst(self):
        q = 'erinnere mich duschen'
        q = merge_schedule(q, schedule_reply('morgen'))
        self.assertTrue(parse_reminder(q, TZ, NOW).needs_time)
        q = merge_schedule(q, schedule_reply('morgen um 20 Uhr'))
        self.assertEqual(parse_reminder(q, TZ, NOW).due_at.astimezone(TZ).day, 2)
        self.assertTrue(self.parse('am 25.10.2026 um 2:30 Uhr duschen').error)
        self.assertTrue(self.parse('am 28.03.2027 um 2:30 Uhr duschen').error)

class FeatureTests(unittest.IsolatedAsyncioTestCase):
    async def test_settings_and_permissions(self):
        class Pool:
            values = {}
            async def execute(self, sql, *args):
                if args:
                    self.values[args[:2]] = args[2]
            async def fetchval(self, sql, *args):
                return self.values.get(args)
        settings = ModuleSettings(Pool())
        await settings.setup()
        self.assertTrue(await settings.enabled(1, 'reminder'))
        self.assertFalse(await settings.enabled(1, 'search'))
        await settings.set(1, 'search', True)
        self.assertTrue(await ModuleSettings(settings.pool).enabled(1, 'search'))
        self.assertFalse(await settings.enabled(2, 'search'))
        guild = NS(owner_id=10)
        self.assertTrue(can_manage(NS(id=10), guild, set()))
        self.assertTrue(can_manage(NS(id=11), guild, {11}))
        self.assertTrue(can_manage(NS(id=12, guild_permissions=discord.Permissions(administrator=True)), guild, set()))
        self.assertFalse(can_manage(NS(id=13), guild, set()))
        self.assertFalse(can_manage(NS(id=10), None, {10}))
        group = admin_commands(NS(modules=settings), 'Hein der Große!', {11})
        self.assertEqual(group.name, 'hein-der-grosse')
        self.assertEqual({c.name for c in group.commands}, {'reminder', 'search', 'modules'})
        intr = NS(user=NS(id=13), guild=guild, response=NS(send_message=AsyncMock()))
        await group.get_command('search').callback(intr, 'on')
        intr.response.send_message.assert_awaited_once()
        self.assertFalse(await settings.enabled(2, 'search'))

    async def test_ai_wording_and_fallback(self):
        author = NS(name='myS4D', display_name='myS4D')
        create = AsyncMock(return_value=NS(output_text=json.dumps({'label': 'Duschen', 'message': 'Ab unter die Dusche. Die Seife wartet schon.'})))
        bot = NS(ai=NS(responses=NS(create=create)))
        label, message = await Winston.compose_reminder(bot, 'heute Abend duschen weil ich stinke', 'duschen', author)
        self.assertEqual(label, 'Duschen')
        self.assertIn('Die Seife', message)
        self.assertIn('weil ich stinke', create.call_args.kwargs['input'])
        self.assertNotIn('tools', create.call_args.kwargs)
        create.side_effect = RuntimeError('offline')
        with self.assertLogs('winston', level='ERROR'):
            self.assertEqual(await Winston.compose_reminder(bot, 'duschen', 'duschen', author), ('duschen', 'Denk dran: duschen.'))

    async def test_search_sources(self):
        block = NS(type='output_text', text='Datum. [Quelle]', annotations=[NS(type='url_citation', start_index=7, end_index=15, url='https://example.org/info')])
        response = NS(output=[NS(type='message', content=[block])])
        self.assertIn('[example.org](<https://example.org/info>)', cited_answer(response))
        block.annotations = []
        self.assertIn('Weiß ich nicht sicher', cited_answer(response))
        self.assertNotIn('filters', search_options()['tools'][0])


class FlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_followup_saves_generated_text_once_and_disabled_blocks_new(self):
        from unittest.mock import patch
        class FixedDate(datetime):
            @classmethod
            def now(cls, tz=None):
                return NOW
        class Context:
            async def __aenter__(self): return self.conn
            async def __aexit__(self, *args): pass
        class Conn:
            inserted = None
            async def execute(self, *args): pass
            async def fetchval(self, sql, *args):
                if 'INSERT INTO' in sql:
                    self.inserted = args
                    return 7
                return 0
            def transaction(self):
                ctx = Context(); ctx.conn = self; return ctx
        conn = Conn()
        ctx = Context(); ctx.conn = conn
        pool = NS(acquire=lambda: ctx, fetchval=AsyncMock(return_value=0))
        author = NS(id=3, name='Tester', display_name='Tester')
        msg = NS(guild=NS(id=1), channel=NS(id=2), author=author)
        bot = NS(db=pool, modules=NS(enabled=AsyncMock(return_value=True)), reply=AsyncMock(), save_session=AsyncMock(),
                 compose_reminder=AsyncMock(return_value=('Duschen', 'Ab unter die Dusche. Die Seife wartet.')))
        original = 'erinnere mich heute Abend daran das ich duschen muss, weil ich stinke'
        with patch('app.main.datetime', FixedDate):
            await Winston.reminder_command(bot, msg, original, 'session', None)
            bot.compose_reminder.assert_not_awaited()
            pending = bot.save_session.call_args.args[4]
            self.assertEqual(pending['original'], original)
            await Winston.reminder_command(bot, msg, '20 Uhr', 'session', {'history': [], 'pending_reminder': pending})
            bot.compose_reminder.assert_awaited_once_with(original, 'duschen muss, weil ich stinke', author)
            self.assertEqual(conn.inserted[3], 'Duschen')
            self.assertEqual(conn.inserted[5], 'Ab unter die Dusche. Die Seife wartet.')
            bot.modules.enabled.return_value = False
            await Winston.reminder_command(bot, msg, original, 'session', None)
            self.assertEqual(bot.compose_reminder.await_count, 1)

    async def test_delivery_uses_stored_message_without_model_call(self):
        from unittest.mock import patch
        class Channel:
            guild = NS(id=1)
            send = AsyncMock()
        class Conn:
            async def fetch(self, *args):
                return [{'id': 7, 'guild_id': 1, 'channel_id': 2, 'user_id': 3, 'reminder_text': 'Duschen', 'delivery_text': 'Ab unter die Dusche. Die Seife wartet.'}]
            execute = AsyncMock()
            def transaction(self): return self
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
        conn = Conn(); channel = Channel()
        bot = NS(wait_until_ready=AsyncMock(), db=NS(acquire=lambda: conn), get_channel=lambda _: channel)
        with patch('app.main.discord.TextChannel', Channel), patch('app.main.asyncio.sleep', side_effect=asyncio.CancelledError):
            with self.assertRaises(asyncio.CancelledError):
                await Winston.deliver_reminders(bot)
        self.assertEqual(channel.send.call_args.args[0], '<@3> Ab unter die Dusche. Die Seife wartet.')
        self.assertIn("status='delivered'", conn.execute.call_args.args[0])

class CacheTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from app.search import SearchCache
        class Conn:
            def __init__(self, pool):
                self.pool = pool
            def transaction(self): return self
            async def __aenter__(self):
                await self.pool.lock.acquire()
                return self
            async def __aexit__(self, *args):
                self.pool.lock.release()
            async def fetchval(self, sql, guild, key):
                entry = self.pool.entries.get((guild, key))
                return entry[0] if entry and entry[1] > self.pool.now else None
            async def execute(self, sql, *args):
                if sql.startswith('INSERT'):
                    guild, key, question, answer, ttl = args
                    self.pool.entries[(guild,key)] = (answer, self.pool.now + ttl)
                if sql.startswith('DELETE'):
                    self.pool.entries = {key: value for key,value in self.pool.entries.items() if value[1] > self.pool.now or key[0] != args[0]}
        class Context:
            async def __aenter__(self): return self.conn
            async def __aexit__(self, *args): pass
        class Pool:
            def __init__(self):
                self.entries = {}; self.now = 0; self.lock = asyncio.Lock()
            def acquire(self):
                ctx = Context(); ctx.conn = Conn(self); return ctx
        self.pool = Pool()
        self.cache = SearchCache(self.pool, 3600)
        self.generate = AsyncMock(return_value='Release am 1. Januar. [Quelle](<https://example.org/info>)')

    async def test_repeat_normalization_restart_and_expiry(self):
        from app.search import SearchCache
        a = await self.cache.answer(1, 'Wann erscheint Enshrouded 1.0?', {'model':'mini'}, self.generate)
        b = await SearchCache(self.pool).answer(1, '  wann erscheint ENSHROUDED 1.0! ', {'model':'mini'}, self.generate)
        self.assertEqual(a, b)
        self.generate.assert_awaited_once()
        self.pool.now = 3601
        await self.cache.answer(1, 'Wann erscheint Enshrouded 1.0?', {'model':'mini'}, self.generate)
        self.assertEqual(self.generate.await_count, 2)

    async def test_context_server_model_and_ttl_isolation(self):
        from app.search import SearchCache
        await self.cache.answer(1, 'Wann kommt es?', {'user': 1, 'history':['game A']}, self.generate)
        await self.cache.answer(1, 'Wann kommt es?', {'user': 1, 'history':['game B']}, self.generate)
        await self.cache.answer(2, 'Wann kommt es?', {'user': 1, 'history':['game A']}, self.generate)
        await self.cache.answer(1, 'Wann kommt es?', {'user': 2, 'history':['game A']}, self.generate)
        await SearchCache(self.pool, 60).answer(1, 'Wann kommt es?', {'user': 1, 'history':['game A']}, self.generate)
        self.assertEqual(self.generate.await_count, 5)

    async def test_parallel_requests_and_uncertain_answers(self):
        await asyncio.gather(*(self.cache.answer(1, 'release?', {}, self.generate) for _ in range(4)))
        self.generate.assert_awaited_once()
        self.generate.return_value = 'Weiß ich nicht sicher.'
        await self.cache.answer(1, 'Andere Frage?', {}, self.generate)
        await self.cache.answer(1, 'Andere Frage?', {}, self.generate)
        self.assertEqual(self.generate.await_count, 3)

    async def test_standalone_detection(self):
        from app.search import standalone_lookup
        self.assertTrue(standalone_lookup('Wann erscheint Enshrouded 1.0?'))
        self.assertTrue(standalone_lookup('Was gibt es Neues von WoW Forever?'))
        self.assertFalse(standalone_lookup('Und wann kommt das?'))
        self.assertFalse(standalone_lookup('Wann kommt meine Lieferung?'))

if __name__ == '__main__':
    unittest.main()
