"""Opt-in web research with visible citations and no domain filters."""
import re
from urllib.parse import urlsplit


def search_options():
    return {"tools": [{"type": "web_search", "search_context_size": "low"}],
            "tool_choice": {"type": "web_search"}}


def cited_answer(response):
    texts = []
    for item in response.output:
        if getattr(item, "type", None) != "message":
            continue
        for block in item.content:
            if getattr(block, "type", None) != "output_text":
                continue
            content = block.text
            citations = [a for a in block.annotations if getattr(a, "type", None) == "url_citation"]
            for a in sorted(citations, key=lambda a: a.start_index, reverse=True):
                url = a.url
                if urlsplit(url).scheme not in ("http", "https"):
                    continue
                label = urlsplit(url).hostname or "Quelle"
                content = content[:a.start_index] + f"[{label}](<{url}>)" + content[a.end_index:]
            texts.append(content)
    answer = "\n".join(texts).strip()
    # A searched factual answer without a clickable citation is not reliable enough.
    if not re.search(r"\]\(<https?://", answer):
        return "Weiß ich nicht sicher. Ich habe keine belastbare Quelle gefunden."
    if len(answer) > 1750:
        return "Die Recherche war zu umfangreich. Frag bitte etwas gezielter."
    return answer


def standalone_lookup(question):
    """Only share explicit, self-contained factual questions across users."""
    text = question.casefold().strip(' .!?')
    if re.search(r"\b(?:ich|mich|mir|mein\w*|dein\w*|du|dich|hier|dort|dies\w*|das|er|sie|es)\b", text.replace('was gibt es neues', 'neuigkeiten')):
        return False
    return bool(re.match(r"^(?:wann\s+(?:kommt|erscheint|startet|beginnt)\s+\S+|"
                         r"was gibt es neues\s+(?:von|zu|über)\s+\S+|"
                         r"wer\s+(?:ist|heißt)\s+(?:(?:der|die)\s+)?(?:bundeskanzler(?:in)?|bundespräsident(?:in)?|papst)\b)", text))


def cache_date(question, history, local_date):
    """Only calendar-relative requests need a new cache key after midnight."""
    text = " ".join([question] + [item.get("content", "") for item in history if item.get("role") == "user"])
    relative = r"\b(?:heute\w*|morgen|morgige\w*|gestern|gestrige\w*|übermorgen|vorgestern|" \
               r"today|tomorrow|yesterday|tonight|" \
               r"(?:diese\w*|nächste\w*|letzte\w*|kommende\w*)\s+(?:woche|monat|jahr|wochenende|montag|dienstag|mittwoch|donnerstag|freitag|samstag|sonntag)|" \
               r"am\s+wochenende|in\s+(?:\d+|einem?|zwei|drei|vier|fünf|sechs|sieben)\s+(?:tagen?|wochen?|monaten?|jahren?)|" \
               r"(?:this|next|last)\s+(?:week|month|year|weekend))\b"
    return local_date.isoformat() if re.search(relative, text, re.IGNORECASE) else None


class SearchCache:
    """PostgreSQL cache, isolated by server and exact request context."""
    def __init__(self, pool, ttl=3600):
        self.pool = pool
        self.ttl = max(60, min(86400, ttl))

    async def setup(self):
        await self.pool.execute("""CREATE TABLE IF NOT EXISTS search_cache (
            guild_id BIGINT NOT NULL, cache_key TEXT NOT NULL, question TEXT NOT NULL,
            answer TEXT NOT NULL, expires_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (guild_id,cache_key))""")
        await self.pool.execute("CREATE INDEX IF NOT EXISTS search_cache_expiry_idx ON search_cache (expires_at)")

    async def answer(self, guild_id, question, context, generate):
        import hashlib
        import json
        import unicodedata
        normalized = ' '.join(unicodedata.normalize('NFKC', question).casefold().split()).rstrip(' .!?')
        cache_key = hashlib.sha256(json.dumps(["search-cache-v1", self.ttl, normalized, context], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                # Concurrent identical requests wait, then reuse the first result.
                await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1,0))", f"search:{guild_id}:{cache_key}")
                cached = await conn.fetchval("SELECT answer FROM search_cache WHERE guild_id=$1 AND cache_key=$2 AND expires_at>now()", guild_id, cache_key)
                if cached is not None:
                    return cached
                answer = await generate()
                if re.search(r"\]\(<https?://", answer):
                    await conn.execute("DELETE FROM search_cache WHERE guild_id=$1 AND expires_at<=now()", guild_id)
                    await conn.execute("""INSERT INTO search_cache (guild_id,cache_key,question,answer,expires_at)
                        VALUES ($1,$2,$3,$4,now()+$5*interval '1 second')
                        ON CONFLICT (guild_id,cache_key) DO UPDATE SET question=EXCLUDED.question,
                        answer=EXCLUDED.answer,expires_at=EXCLUDED.expires_at""", guild_id, cache_key, question[:1500], answer, float(self.ttl))
                return answer
