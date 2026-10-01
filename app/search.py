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
