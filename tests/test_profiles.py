import asyncio
import json
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

from app.profiles import Profiles, valid_preferences, preference_candidate, register_profile_commands
from app.main import Winston
from app.modules import admin_commands


def preference(value='Eistee Pfirsich', attitude='like', evidence='Ich trinke gern Eistee Pfirsich', topic='drinks'):
    return {'value': value, 'attitude': attitude, 'evidence': evidence, 'topic': topic}


class ExtractionTests(unittest.TestCase):
    def test_explicit_preferences_and_literal_evidence(self):
        q = 'Ich trinke gern Eistee Pfirsich'
        self.assertEqual(valid_preferences(q, [preference()])[0]['value'], 'Eistee Pfirsich')
        self.assertFalse(valid_preferences(q, [preference(value='Cola')]))
        self.assertFalse(valid_preferences(q, [preference(evidence='Ich trinke gern Cola')]))
        self.assertFalse(valid_preferences('Max trinkt gern Eistee Pfirsich', [preference()]))
        self.assertFalse(valid_preferences('Ich hätte Lust auf ein Getränk', [preference()]))
        self.assertFalse(preference_candidate('"Ich trinke gern Eistee Pfirsich" sagt Max'))
        self.assertFalse(preference_candidate('Ich mag Medikamente'))


class ProfileStorageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        class Pool:
            def __init__(self):
                self.preferences = {}; self.settings = {}; self.module = True; self.tick = 0
            def acquire(self):
                class Context:
                    async def __aenter__(self): return pool
                    async def __aexit__(self, *args): pass
                pool = self
                return Context()
            def transaction(self): return self.acquire()
            async def fetchrow(self, sql, guild, user): return self.settings.get((guild,user))
            async def fetchval(self, sql, *args): return self.module
            async def fetch(self, sql, guild, user):
                return [v for (g,u,k),v in self.preferences.items() if (g,u)==(guild,user)]
            async def execute(self, sql, *args):
                if 'INSERT INTO user_preferences' in sql:
                    guild,user,key,topic,value,attitude = args
                    self.tick += 1
                    self.preferences[(guild,user,key)] = {'topic':topic, 'value':value, 'attitude':attitude, 'tick':self.tick}
                elif 'DELETE FROM user_preferences' in sql:
                    guild,user = args
                    keys = [k for k in self.preferences if k[:2]==(guild,user)]
                    if 'NOT IN' in sql:
                        keys = sorted(keys,key=lambda k:self.preferences[k]['tick'],reverse=True)[20:]
                    for key in keys: del self.preferences[key]
                elif 'INSERT INTO profile_settings' in sql:
                    guild,user = args[:2]
                    old = self.settings.get((guild,user), {'enabled':True, 'revision':0})
                    self.settings[(guild,user)] = {'enabled':args[2] if len(args)==3 else old['enabled'], 'revision':old['revision']+1}
        self.pool = Pool()
        async def scan_iter(**kwargs):
            yield 'session:1:10:2'
        self.redis = NS(scan_iter=scan_iter, delete=AsyncMock(), zrem=AsyncMock())
        self.bot = NS(db=self.pool, modules=NS(enabled=AsyncMock(return_value=True)), redis=self.redis)
        self.profiles = Profiles(self.bot)

    async def test_persistence_scope_updates_and_cap(self):
        await self.profiles.apply(1,2,'Ich trinke gern Eistee Pfirsich',[preference()])
        self.assertEqual(len(await Profiles(self.bot).rows(1,2)),1)
        self.assertEqual(await self.profiles.rows(2,2),[])
        self.assertEqual(await self.profiles.rows(1,3),[])
        q = 'Ich mag Eistee Pfirsich nicht mehr'
        await self.profiles.apply(1,2,q,[preference(attitude='dislike',evidence=q)])
        rows = await self.profiles.rows(1,2)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['attitude'],'dislike')
        for i in range(22):
            value = f'Spiel {i}'
            q = f'Ich zocke gern {value}'
            await self.profiles.apply(1,2,q,[preference(value=value,evidence=q,topic='games')])
        self.assertEqual(len(await self.profiles.rows(1,2)),20)

    async def test_optout_moduleoff_and_inflight_revision(self):
        self.pool.settings[(1,2)] = {'enabled':False,'revision':1}
        await self.profiles.apply(1,2,'Ich trinke gern Eistee Pfirsich',[preference()],1)
        self.assertFalse(self.pool.preferences)
        self.pool.settings[(1,2)] = {'enabled':True,'revision':2}
        await self.profiles.apply(1,2,'Ich trinke gern Eistee Pfirsich',[preference()],1)
        self.assertFalse(self.pool.preferences)
        self.pool.module = False
        await self.profiles.apply(1,2,'Ich trinke gern Eistee Pfirsich',[preference()],2)
        self.assertFalse(self.pool.preferences)

    async def test_delete_only_own_profile_and_clear_context(self):
        for user in (2,3):
            await self.profiles.apply(1,user,'Ich trinke gern Eistee Pfirsich',[preference()])
        await self.profiles.manage(1,2,'loeschen')
        self.assertEqual(await self.profiles.rows(1,2),[])
        self.assertEqual(len(await self.profiles.rows(1,3)),1)
        self.redis.delete.assert_awaited_once_with('session:1:10:2')
        self.assertEqual(self.pool.settings[(1,2)]['revision'],1)
        await self.profiles.apply(1,2,'Ich trinke gern Eistee Pfirsich',[preference()],0)
        self.assertEqual(await self.profiles.rows(1,2),[])


class ChatIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_call_learns_preference_next_reply_receives_it(self):
        class Context:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            fetch = AsyncMock(return_value=[])
        ctx = Context()
        rows = []
        async def apply(*args):
            rows.append({'topic':'drinks','value':'Eistee Pfirsich','attitude':'like'})
        async def get_rows(*args): return rows
        first = json.dumps({'answer':'Pfirsich-Eistee also. Geschmack haste ja.', 'preferences':[preference()]})
        create = AsyncMock(side_effect=[NS(output_text=first), NS(output_text='Gönn dir deinen Pfirsich-Eistee. 🍑')])
        bot = NS(key=Winston.key, redis=NS(exists=AsyncMock(return_value=False),get=AsyncMock(return_value=None)), locks={},
                 reminder_command=AsyncMock(return_value=False), memory_command=AsyncMock(return_value=False),
                 profiles=NS(text_command=AsyncMock(return_value=False), state=AsyncMock(return_value=(True,0)),rows=get_rows,apply=apply),
                 db=NS(acquire=lambda:ctx), modules=NS(enabled=AsyncMock(return_value=False)),
                 ai=NS(responses=NS(create=create)), reply=AsyncMock(),save_session=AsyncMock())
        author = NS(id=2,bot=False,name='Tester',display_name='Tester')
        channel = NS(id=3,typing=lambda:ctx)
        message = NS(guild=NS(id=1),author=author,channel=channel,content='Winston Ich trinke gern Eistee Pfirsich')
        await Winston.on_message(bot,message)
        self.assertEqual(create.await_count,1)
        self.assertEqual(rows[0]['value'],'Eistee Pfirsich')
        message.content='Winston Ich hätte jetzt Lust auf was zu trinken'
        await Winston.on_message(bot,message)
        self.assertEqual(create.await_count,2)
        self.assertIn('Eistee Pfirsich',create.call_args.kwargs['instructions'])
        self.assertNotIn('text',create.call_args.kwargs)
        self.assertIn('Pfirsich-Eistee',bot.reply.call_args.args[1])

    async def test_profile_command_is_only_own_user(self):
        bot = NS(profiles=NS(manage=AsyncMock(return_value='Profil leer')))
        group = admin_commands(bot,'Hein',set())
        register_profile_commands(group,bot,set())
        interaction = NS(guild=NS(id=1),guild_id=1,user=NS(id=2),response=NS(defer=AsyncMock()),followup=NS(send=AsyncMock()))
        await group.get_command('meinprofil').callback(interaction,'anzeigen')
        bot.profiles.manage.assert_awaited_once_with(1,2,'anzeigen')

if __name__ == '__main__':
    unittest.main()
