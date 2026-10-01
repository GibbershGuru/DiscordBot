"""Small, user-owned preference profiles from explicit first-person statements."""
import hashlib
import json
import re
import unicodedata
from typing import Literal

import discord

TOPICS = {'drinks': 'Getränke', 'food': 'Essen', 'games': 'Spiele', 'hobbies': 'Hobbys'}
SELF_PREFERENCE = re.compile(r"^(?:ich\s+(?:mag|liebe|hasse)\b|ich\s+(?:trinke|esse|spiele|zocke|mache|lese|höre)\b[^.!?\n]{0,100}\b(?:gern|gerne|am liebsten)\b|mein\w*\s+lieblings\w*\b)", re.I)
SENSITIVE = re.compile(r"\b(?:passwort|token|api.?key|adresse|telefon|krank\w*|diagnose|medikament\w*|religion|partei|politik|sex\w*|porno\w*)\b", re.I)


def normalized(text):
    return ' '.join(unicodedata.normalize('NFKC', text).casefold().split())


def preference_candidate(question):
    return bool(SELF_PREFERENCE.search(question.strip()) and not SENSITIVE.search(question))


def valid_preferences(question, items):
    if not preference_candidate(question):
        return []
    result = []
    for item in items[:3]:
        topic, value, attitude, evidence = (item.get(key, '') for key in ('topic', 'value', 'attitude', 'evidence'))
        if not all(isinstance(v, str) for v in (topic, value, attitude, evidence)):
            continue
        if len(evidence) > 200:
            continue
        value = value.strip()
        if topic not in TOPICS or attitude not in ('like', 'dislike') or not 1 <= len(value) <= 80:
            continue
        if any(char in value for char in ('@', '\n', '<', '>')) or SENSITIVE.search(value):
            continue
        if normalized(value) not in normalized(evidence) or normalized(evidence) not in normalized(question):
            continue
        if not SELF_PREFERENCE.search(evidence.strip()):
            continue
        result.append({'topic': topic, 'value': value, 'attitude': attitude})
    return result


PROFILE_INSTRUCTIONS = (
    'Die aktuelle Nachricht kann eigene dauerhafte Vorlieben enthalten. Antworte wie gewohnt und gib zusätzlich preferences zurück. '
    'Erfasse nur ausdrücklich genannte eigene Vorlieben für Getränke, Essen, Spiele oder Hobbys aus der LETZTEN Nutzernachricht. '
    'Keine Angaben über andere Personen, Zitate, hypothetische Beispiele, momentane Wünsche oder sensible Daten. '
    'Erfinde nichts und leite nichts aus dem Verlauf oder gespeicherten Daten ab. Bei Unsicherheit preferences=[]. '
    'value muss die Bezeichnung wörtlich aus der aktuellen Nachricht übernehmen; evidence ist eine kurze wörtliche Ich-Aussage dazu (höchstens 200 Zeichen). '
    'attitude=like für Vorlieben, dislike für ausdrücklich abgelehnte Dinge. Höchstens drei Einträge. '
    'Versprich keine Speicherung in answer; der Bot speichert erst nach erfolgreicher Prüfung.'
)


def profile_format():
    return {'format': {'type': 'json_schema', 'name': 'profile_reply', 'strict': True,
        'schema': {'type': 'object', 'properties': {
            'answer': {'type': 'string'},
            'preferences': {'type': 'array', 'items': {'type': 'object', 'properties': {
                'topic': {'type': 'string', 'enum': list(TOPICS)}, 'value': {'type': 'string'},
                'attitude': {'type': 'string', 'enum': ['like', 'dislike']}, 'evidence': {'type': 'string'}},
                'required': ['topic', 'value', 'attitude', 'evidence'], 'additionalProperties': False}}},
            'required': ['answer', 'preferences'], 'additionalProperties': False}}}


def describe(rows):
    return [f"{TOPICS[row['topic']]}: {'mag' if row['attitude']=='like' else 'mag nicht'} {row['value']}" for row in rows]


class Profiles:
    def __init__(self, bot):
        self.bot = bot

    async def setup(self):
        await self.bot.db.execute('''CREATE TABLE IF NOT EXISTS profile_settings (
            guild_id BIGINT NOT NULL, user_id BIGINT NOT NULL, enabled BOOLEAN NOT NULL, revision BIGINT NOT NULL DEFAULT 0,
            PRIMARY KEY (guild_id,user_id))''')
        await self.bot.db.execute('''CREATE TABLE IF NOT EXISTS user_preferences (
            guild_id BIGINT NOT NULL, user_id BIGINT NOT NULL, preference_key TEXT NOT NULL,
            topic TEXT NOT NULL, value TEXT NOT NULL, attitude TEXT NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY (guild_id,user_id,preference_key))''')

    async def state(self, guild_id, user_id):
        module = await self.bot.modules.enabled(guild_id, 'profile')
        row = await self.bot.db.fetchrow('SELECT enabled,revision FROM profile_settings WHERE guild_id=$1 AND user_id=$2', guild_id, user_id)
        return bool(module and (row is None or row['enabled'])), row['revision'] if row else 0

    async def enabled(self, guild_id, user_id):
        return (await self.state(guild_id, user_id))[0]

    async def rows(self, guild_id, user_id):
        return await self.bot.db.fetch('SELECT topic,value,attitude FROM user_preferences WHERE guild_id=$1 AND user_id=$2 ORDER BY updated_at DESC LIMIT 20', guild_id, user_id)

    async def apply(self, guild_id, user_id, question, items, expected_revision=0):
        updates = valid_preferences(question, items)
        if not updates:
            return
        async with self.bot.db.acquire() as conn:
            async with conn.transaction():
                await conn.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))', f'profile:{guild_id}:{user_id}')
                setting = await conn.fetchrow('SELECT enabled,revision FROM profile_settings WHERE guild_id=$1 AND user_id=$2', guild_id, user_id)
                module = await conn.fetchval("SELECT enabled FROM bot_modules WHERE guild_id=$1 AND module='profile'", guild_id)
                if module is False or (setting and (not setting['enabled'] or setting['revision'] != expected_revision)) or (setting is None and expected_revision != 0):
                    return
                for item in updates:
                    key = hashlib.sha256((item['topic'] + ':' + normalized(item['value'])).encode()).hexdigest()
                    await conn.execute('''INSERT INTO user_preferences (guild_id,user_id,preference_key,topic,value,attitude)
                        VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT (guild_id,user_id,preference_key)
                        DO UPDATE SET value=EXCLUDED.value,attitude=EXCLUDED.attitude,updated_at=now()''',
                        guild_id, user_id, key, item['topic'], item['value'], item['attitude'])
                await conn.execute('''DELETE FROM user_preferences WHERE guild_id=$1 AND user_id=$2 AND preference_key NOT IN
                    (SELECT preference_key FROM user_preferences WHERE guild_id=$1 AND user_id=$2 ORDER BY updated_at DESC,preference_key LIMIT 20)''', guild_id, user_id)

    async def clear_context(self, guild_id, user_id):
        # Remove all sessions of this person on this server, not other people's data.
        async for key in self.bot.redis.scan_iter(match=f'session:{guild_id}:*:{user_id}'):
            await self.bot.redis.delete(key)
            await self.bot.redis.zrem('session_deadlines', key)
        await self.bot.db.execute('DELETE FROM search_cache WHERE guild_id=$1 AND user_id=$2', guild_id, user_id)

    async def manage(self, guild_id, user_id, action):
        if action == 'anzeigen':
            facts = describe(await self.rows(guild_id, user_id))
            active = await self.enabled(guild_id, user_id)
            return (f"Dein Profil ({'an' if active else 'aus'}):\n" + '\n'.join(facts))[:1800] if facts else f"Dein Profil ist leer ({'an' if active else 'aus'})."
        async with self.bot.db.acquire() as conn:
            async with conn.transaction():
                await conn.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))', f'profile:{guild_id}:{user_id}')
                if action == 'loeschen':
                    await conn.execute('''INSERT INTO profile_settings (guild_id,user_id,enabled,revision) VALUES ($1,$2,TRUE,1)
                        ON CONFLICT (guild_id,user_id) DO UPDATE SET revision=profile_settings.revision+1''', guild_id, user_id)
                    await conn.execute('DELETE FROM user_preferences WHERE guild_id=$1 AND user_id=$2', guild_id, user_id)
                else:
                    await conn.execute('''INSERT INTO profile_settings (guild_id,user_id,enabled,revision) VALUES ($1,$2,$3,1)
                        ON CONFLICT (guild_id,user_id) DO UPDATE SET enabled=EXCLUDED.enabled,revision=profile_settings.revision+1''', guild_id, user_id, action == 'on')
        await self.clear_context(guild_id, user_id)
        return {'loeschen': 'Dein Profil und der laufende Gesprächskontext sind gelöscht. Neue eigene Vorlieben kann ich weiterhin lernen.',
                'off': 'Dein Profil ist aus. Ich lerne keine neuen Vorlieben und nutze die gespeicherten nicht.',
                'on': 'Dein Profil ist an, sofern das Servermodul ebenfalls an ist.'}[action]

    async def text_command(self, message, question):
        text = question.casefold().strip(' .!?')
        action = {'mein profil': 'anzeigen', 'zeig mein profil': 'anzeigen', 'profil löschen': 'loeschen',
                  'profil loeschen': 'loeschen', 'profil aus': 'off', 'profil an': 'on'}.get(text)
        if action is None:
            return False
        answer = await self.manage(message.guild.id, message.author.id, action)
        await self.bot.reply(message, answer)
        return True


def register_profile_commands(group, bot, owner_ids):
    from .modules import can_manage

    @group.command(name='profile', description='Automatische Vorlieben-Profile für diesen Server ein- oder ausschalten')
    async def profile(interaction: discord.Interaction, status: Literal['on', 'off']):
        if not can_manage(interaction.user, interaction.guild, owner_ids):
            return await interaction.response.send_message('Das dürfen nur Serverbesitzer, Administratoren und freigeschaltete Bot-Owner.', ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        await bot.modules.set(interaction.guild_id, 'profile', status == 'on')
        await interaction.followup.send(f'Profilmodul: {status}.', ephemeral=True)

    @group.command(name='meinprofil', description='Eigenes Vorlieben-Profil ansehen, löschen, aktivieren oder abschalten')
    async def meinprofil(interaction: discord.Interaction, aktion: Literal['anzeigen', 'loeschen', 'on', 'off']):
        if interaction.guild is None:
            return await interaction.response.send_message('Das geht nur auf einem Server.', ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        answer = await bot.profiles.manage(interaction.guild_id, interaction.user.id, aktion)
        await interaction.followup.send(answer, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
