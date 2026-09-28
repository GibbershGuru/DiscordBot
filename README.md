# Discord-Kneipenbot

Ein Discord-Bot mit frei wählbarem Namen und norddeutschem Kneipenhumor. Das fertige Docker-Image heißt `ghcr.io/gibbershguru/discordbot:latest`.

## Funktionen

- Reagiert auf seinen Namen (`BOT_NAME`) und unterhält sich danach pro Person und Kanal bis zu fünf Minuten ohne erneutes Aufrufen.
- Antwortet kurz, trocken und gelegentlich arrogant; variiert Begrüßungen und kurze Abschiede.
- Begrüßt neue Mitglieder auf Wunsch in einem festgelegten Kanal mit einem zufälligen Spruch.
- Merkt sich Fakten nur auf ausdrücklichen Befehl: `merk dir: ...`, `was weißt du über mich?`, `vergiss alles`.
- Speichert Erinnerungen für „in 10 Minuten“, „in 5 Tagen“ oder „am 27.12.2026 um 18 Uhr“. Fehlt bei „morgen“ oder einem Datum die Uhrzeit, fragt er nach. `meine Erinnerungen` zeigt offene Aufträge, `lösche Erinnerung 3` entfernt einen.
- Nutzt keine Websuche. Bei erkannten Fragen nach aktuellen Fakten sagt er „Weiß ich nicht sicher.“; auch sonst soll er Wissenslücken zugeben. Modellantworten können trotzdem Fehler enthalten.

## Start mit Docker Compose

1. Lege im [Discord Developer Portal](https://discord.com/developers/applications) einen Bot an. Aktiviere **Message Content Intent** und lade ihn mit dem Scope **bot** sowie **Kanal anzeigen**, **Nachrichten senden** und **Nachrichtenverlauf lesen** auf deinen Server ein.
2. Erstelle einen [OpenAI-API-Schlüssel](https://platform.openai.com/api-keys).
3. Kopiere `.env.example` nach `.env`. Trage `DISCORD_TOKEN`, `OPENAI_API_KEY`, ein eigenes `POSTGRES_PASSWORD` und den gewünschten `BOT_NAME` ein. Den sichtbaren Namen stellst du separat im Discord Developer Portal ein; verwende dort denselben Namen.
4. Starte `docker compose up -d`. Für Updates: `docker compose pull && docker compose up -d`.

`compose.yaml` startet den Bot mit PostgreSQL für ausdrücklich gespeicherte Erinnerungen und Redis für laufende Gespräche. Sichere das PostgreSQL-Volume regelmäßig. Du kannst den Bot auch über Docker-Oberflächen wie Unraid als einzelnen Container starten; dann benötigt er zusätzlich `DATABASE_URL` und `REDIS_URL` für erreichbare PostgreSQL- und Redis-Dienste. Alle Einstellungen lassen sich als Umgebungsvariablen übergeben, eine `.env`-Datei ist dabei nicht nötig.

Erinnerungen liegen in PostgreSQL und werden nach einem Neustart nachgeholt, sofern der Bot im ursprünglichen Kanal noch schreiben kann. Standard-Zeitzone ist `Europe/Berlin`; mit `REMINDER_TIMEZONE` lässt sie sich ändern. Der Bot erwähnt die Person im ursprünglichen Kanal. Bis zu zehn offene Erinnerungen pro Person und Server sind möglich, höchstens ein Jahr im Voraus. Erinnerungen werden ohne OpenAI-Aufruf verarbeitet.

Für automatische Begrüßungen: Im Discord Developer Portal zusätzlich **Server Members Intent** aktivieren und `WELCOME_CHANNEL_ID` auf die ID des gewünschten Textkanals setzen. Der Bot braucht dort **Kanal anzeigen** und **Nachrichten senden**. `WELCOME_CHANNEL_ID=0` (Standard) deaktiviert die Funktion; dann ist der zusätzliche Intent nicht erforderlich. Ein erneutes Einladen des Bots ist nicht nötig.

Nachrichten, die der Bot mit dem Sprachmodell beantwortet, und gespeicherte Fakten werden an die OpenAI API übertragen. Teile das den Personen auf deinem Server mit. Veröffentliche niemals Token, API-Schlüssel oder `.env` im Repository.

Lizenz: MIT.
