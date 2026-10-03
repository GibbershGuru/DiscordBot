"""Parse a small, predictable set of German reminder commands without a model call."""

import re
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


COMMAND = re.compile(r"^(?:erinnere|erinner)\s+mich\b[\s,:]*(.*)$", re.IGNORECASE | re.DOTALL)
RELATIVE = re.compile(r"\bin\s*(\d{1,3})\s*(min(?:uten)?|mins?|stunden?|std|tage[n]?|wochen?)\b", re.IGNORECASE)
DATE = re.compile(r"\bam\s+(\d{1,2})\.(\d{1,2})\.(\d{4})\b", re.IGNORECASE)
TODAY = re.compile(r"\bheute\b", re.IGNORECASE)
PART_OF_DAY = re.compile(r"\b(?:abends?|morgens?|mittags?|nachmittags?|nachts?)\b", re.IGNORECASE)
TOMORROW = re.compile(r"\bmorgen\b", re.IGNORECASE)
CLOCK = re.compile(r"\b(?:(?:um\s+)|(?=\d{1,2}(?::\d{2})?\s*uhr\b)|(?=\d{1,2}:\d{2}\b))(\d{1,2})(?::(\d{2}))?\s*(?:uhr)?\b", re.IGNORECASE)
CLOCK_REPLY = re.compile(r"^\s*(?:um\s*)?(\d{1,2})(?::(\d{2}))?\s*(?:uhr)?[.!]?\s*$", re.IGNORECASE)


@dataclass
class Reminder:
    text: str = ""
    due_at: datetime | None = None
    needs_time: bool = False
    needs_day: bool = False
    error: str = ""


def clock_reply(text):
    match = CLOCK_REPLY.fullmatch(text)
    if not match:
        return None
    hour, minute = int(match[1]), int(match[2] or 0)
    return f"um {hour:02d}:{minute:02d} Uhr" if hour < 24 and minute < 60 else None


def clean_reminder_text(text):
    """Keep the actual task, including when an older reminder stored a rough phrase."""
    text = text.strip(" ,.!?\n")
    text = re.sub(r"^(?:daran|dran|an)\b[\s,:]*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^(?:dass|das)\b[\s,:]*", "", text, flags=re.IGNORECASE)
    # Colloquial commands sometimes append an explanation without punctuation:
    # "ich duschen muss ich stinke" -> "duschen".
    text = re.sub(r"^ich\s+(.+?)\s+(?:muss|soll|sollte)(?:\s+ich\s+.*)?$", r"\1", text, flags=re.IGNORECASE)
    text = re.sub(r"^(.+?)\s+(?:muss|soll|sollte)\s+ich\s+.*$", r"\1", text, flags=re.IGNORECASE)
    text = re.sub(r"^ich\s+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^(.+?)\s+dann\s+vorbei\s+ist$", r"\1 ist vorbei", text, flags=re.IGNORECASE)
    return text.strip(" ,.!?\n")


def reminder_message(text):
    """Write one short reminder in the bot's voice without a model request."""
    task = clean_reminder_text(text)
    if task.casefold() == "duschen":
        return "Zeit zum Duschen. Die Seife wartet schon, du Stinktier."
    task = re.sub(r"^meine\b", "deine", task, flags=re.IGNORECASE)
    task = re.sub(r"^meinen\b", "deinen", task, flags=re.IGNORECASE)
    if re.search(r"\b(?:ist|sind|hat|haben)\b", task, re.IGNORECASE):
        sentence = task[:1].upper() + task[1:]
    elif re.search(r"\b\w+(?:en|eln|ern)\s*$", task, re.IGNORECASE) or task.lower().startswith("zum "):
        sentence = "Du wolltest " + task
    else:
        sentence = "Denk dran: " + task[:1].lower() + task[1:]
    return sentence.rstrip(".!?") + ". " + random.choice([
        "Nu mach hin, du Schiffschaukelbremser.",
        "Auf die Hufe, du Tagedieb.",
        "Dein Auftritt, du Galgenstrick.",
        "Ich hab Bescheid gesagt. Der Rest liegt bei dir.",
        "Mehr Service gibt's hier nur gegen Trinkgeld.",
    ])


def parse_reminder(question, tz, now=None):
    match = COMMAND.fullmatch(question.strip())
    if not match:
        return None
    now = now or datetime.now(tz)
    body = match[1].strip()
    relative, date, tomorrow, today, clock = (pattern.search(body) for pattern in (RELATIVE, DATE, TOMORROW, TODAY, CLOCK))
    if relative and clock and (clock.start() < relative.end() or body[relative.end():clock.start()].strip(" ,")):
        clock = None
    spans = [item.span() for item in (relative, date, tomorrow, today, clock) if item]
    text = body
    for start, end in sorted(spans, reverse=True):
        text = text[:start] + text[end:]
    text = PART_OF_DAY.sub("", text)
    text = clean_reminder_text(text)
    if not text:
        return Reminder(error="Woran soll ich dich erinnern? Sag's noch mal mit dem Anlass dazu.")
    if len(text) > 500:
        return Reminder(error="Mach's kürzer, du Romanheld. 500 Zeichen reichen.")
    day_count = sum(bool(x) for x in (relative, date, tomorrow, today))
    if day_count > 1:
        return Reminder(error="Sag bitte einen eindeutigen Tag oder einen Zeitraum.")
    if clock:
        hour, minute = int(clock[1]), int(clock[2] or 0)
        if hour >= 24 or minute >= 60:
            return Reminder(error="Die Uhrzeit gibt's nicht. Versuch's mit 'um 18:30 Uhr'.")
    if not day_count:
        return Reminder(text=text, needs_day=True, needs_time=not bool(clock))
    if relative:
        count = int(relative[1])
        if count == 0:
            return Reminder(error="Null Minuten? Da bin ich ja schon zu spät.")
        unit = relative[2].lower()
        if unit.startswith("min"):
            due = now.astimezone(timezone.utc) + timedelta(minutes=count)
        elif unit.startswith("st"):
            due = now.astimezone(timezone.utc) + timedelta(hours=count)
        else:
            if not clock:
                return Reminder(text=text, needs_time=True)
            target = (now + timedelta(days=count * (7 if unit.startswith("woch") else 1))).date()
            due = datetime(target.year, target.month, target.day, hour, minute, tzinfo=tz)
    else:
        if not clock:
            return Reminder(text=text, needs_time=True)
        try:
            target = (now + timedelta(days=1)).date() if tomorrow else now.date() if today else datetime(int(date[3]), int(date[2]), int(date[1])).date()
            due = datetime(target.year, target.month, target.day, hour, minute, tzinfo=tz)
        except ValueError:
            return Reminder(error="Das Datum gibt's nicht. Nimm bitte TT.MM.JJJJ.")
    short_relative = relative and relative[2].lower().startswith(("min", "st"))
    if not short_relative and due.astimezone(timezone.utc).astimezone(tz).replace(tzinfo=None) != due.replace(tzinfo=None):
        return Reminder(error="Diese Uhrzeit fällt durch die Zeitumstellung aus. Nimm bitte eine andere.")
    if not short_relative and due.fold == 0 and due.replace(fold=1).utcoffset() != due.utcoffset():
        return Reminder(error="Diese Uhrzeit gibt es durch die Zeitumstellung zweimal. Nimm bitte eine eindeutige Uhrzeit davor oder danach.")
    due_utc = due.astimezone(timezone.utc)
    if due_utc <= now.astimezone(timezone.utc):
        return Reminder(error="Der Zeitpunkt ist schon vorbei. Auch ich kann die Uhr nicht zurückdrehen.")
    if due_utc > now.astimezone(timezone.utc) + timedelta(days=365):
        return Reminder(error="So weit plane ich nicht voraus. Ein Jahr ist das Limit.")
    return Reminder(text=text, due_at=due_utc)


def schedule_reply(text):
    """Only accept a day/time answer, never silently append unrelated conversation."""
    clock = clock_reply(text)
    if clock:
        return clock
    match = re.fullmatch(r"\s*(heute|morgen|(?:am\s+)?\d{1,2}\.\d{1,2}\.\d{4})(?:\s+(?:um\s*)?(\d{1,2})(?::(\d{2}))?\s*(?:Uhr)?)?[.!]?\s*", text, re.IGNORECASE)
    if not match:
        return None
    day = match[1]
    if day[0].isdigit():
        day = "am " + day
    if not match[2]:
        return day
    clock = clock_reply(f"{match[2]}:{match[3] or '00'}")
    return day + " " + clock if clock else None


def merge_schedule(question, addition):
    if TODAY.search(addition) or TOMORROW.search(addition) or DATE.search(addition):
        for pattern in (TODAY, TOMORROW, DATE, RELATIVE):
            question = pattern.sub("", question)
    if CLOCK.search(addition):
        question = CLOCK.sub("", question)
    if CLOCK.search(addition) and RELATIVE.search(question):
        match = RELATIVE.search(question)
        return question[:match.end()] + " " + addition + question[match.end():]
    return question + " " + addition
