"""Parse a small, predictable set of German reminder commands without a model call."""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


COMMAND = re.compile(r"^(?:erinnere|erinner)\s+mich\b[\s,:]*(.*)$", re.IGNORECASE | re.DOTALL)
RELATIVE = re.compile(r"\bin\s*(\d{1,3})\s*(min(?:uten)?|mins?|stunden?|std|tage[n]?|wochen?)\b", re.IGNORECASE)
DATE = re.compile(r"\bam\s+(\d{1,2})\.(\d{1,2})\.(\d{4})\b", re.IGNORECASE)
TOMORROW = re.compile(r"\bmorgen\b", re.IGNORECASE)
CLOCK = re.compile(r"\bum\s+(\d{1,2})(?::(\d{2}))?\s*(?:uhr)?\b", re.IGNORECASE)
CLOCK_REPLY = re.compile(r"^\s*(?:um\s*)?(\d{1,2})(?::(\d{2}))?\s*(?:uhr)?[.!]?\s*$", re.IGNORECASE)


@dataclass
class Reminder:
    text: str = ""
    due_at: datetime | None = None
    needs_time: bool = False
    error: str = ""


def clock_reply(text):
    match = CLOCK_REPLY.fullmatch(text)
    if not match:
        return None
    hour, minute = int(match[1]), int(match[2] or 0)
    return f"um {hour:02d}:{minute:02d} Uhr" if hour < 24 and minute < 60 else None


def parse_reminder(question, tz, now=None):
    match = COMMAND.fullmatch(question.strip())
    if not match:
        return None
    now = now or datetime.now(tz)
    body = match[1].strip()
    relative, date, tomorrow, clock = (pattern.search(body) for pattern in (RELATIVE, DATE, TOMORROW, CLOCK))
    if relative and clock and (clock.start() < relative.end() or body[relative.end():clock.start()].strip(" ,")):
        # A clock later in the message may describe the event, not the reminder.
        clock = None
    spans = [item.span() for item in (relative, date, tomorrow, clock) if item]
    text = body
    for start, end in sorted(spans, reverse=True):
        text = text[:start] + text[end:]
    text = re.sub(r"^(?:daran|dran|an)\b[\s,:]*", "", text.strip(), flags=re.IGNORECASE)
    text = re.sub(r"^(?:dass|das)\b[\s,:]*", "", text, flags=re.IGNORECASE).strip(" ,.!?\n")
    text = re.sub(r"^ich\s+(.+?)\s+(?:muss|soll|sollte)$", r"\1", text, flags=re.IGNORECASE)
    text = re.sub(r"^ich\s+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^(.+?)\s+dann\s+vorbei\s+ist$", r"\1 ist vorbei", text, flags=re.IGNORECASE)
    if not text:
        return Reminder(error="Woran soll ich dich erinnern? Sag's noch mal mit dem Anlass dazu.")
    if len(text) > 200:
        return Reminder(error="Mach's kürzer, du Romanheld. 200 Zeichen reichen.")
    if sum(bool(x) for x in (relative, date, tomorrow)) != 1:
        return Reminder(error="Sag eine klare Zeit: 'in 10 Minuten', 'morgen um 18 Uhr' oder 'am 27.12.2026 um 18 Uhr'.")
    if clock:
        hour, minute = int(clock[1]), int(clock[2] or 0)
        if hour >= 24 or minute >= 60:
            return Reminder(error="Die Uhrzeit gibt's nicht. Versuch's mit 'um 18:30 Uhr'.")
    if relative:
        count = int(relative[1])
        if count == 0:
            return Reminder(error="Null Minuten? Da bin ich ja schon zu spät.")
        unit = relative[2].lower()
        if unit.startswith(("min",)):
            due = now.astimezone(timezone.utc) + timedelta(minutes=count)
        elif unit.startswith(("st",)):
            due = now.astimezone(timezone.utc) + timedelta(hours=count)
        else:
            due = now + timedelta(days=count * (7 if unit.startswith("woch") else 1))
        if clock:
            due = due.astimezone(tz).replace(hour=hour, minute=minute, second=0, microsecond=0)
    else:
        if not clock:
            return Reminder(text=text, needs_time=True)
        try:
            target = (now + timedelta(days=1)).date() if tomorrow else datetime(int(date[3]), int(date[2]), int(date[1])).date()
            due = datetime(target.year, target.month, target.day, hour, minute, tzinfo=tz)
        except ValueError:
            return Reminder(error="Das Datum gibt's nicht. Nimm bitte TT.MM.JJJJ.")
    due_utc = due.astimezone(timezone.utc)
    if due_utc <= now.astimezone(timezone.utc):
        return Reminder(error="Der Zeitpunkt ist schon vorbei. Auch ich kann die Uhr nicht zurückdrehen.")
    if due_utc > now.astimezone(timezone.utc) + timedelta(days=365):
        return Reminder(error="So weit plane ich nicht voraus. Ein Jahr ist das Limit.")
    return Reminder(text=text, due_at=due_utc)
