# Discord-Kneipenbot

Ein Discord-Bot mit frei wählbarem Namen und norddeutschem Kneipenhumor. Das fertige Docker-Image heißt `ghcr.io/gibbershguru/discordbot:latest`.

## Funktionen

- Reagiert auf seinen Namen (`BOT_NAME`) und unterhält sich danach pro Person und Kanal bis zu fünf Minuten ohne erneutes Aufrufen.
- Antwortet kurz und frech; verabschiedet sich nach Inaktivität oder sofort bei „Tschüss“.
- Merkt sich Fakten nur auf ausdrücklichen Befehl: `merk dir: ...`, `was weißt du über mich?`, `vergiss alles`.
- Nutzt keine Websuche. Bei erkannten Fragen nach aktuellen Fakten sagt er „Weiß ich nicht sicher.“; auch sonst soll er Wissenslücken zugeben. Modellantworten können trotzdem Fehler enthalten.

## Start mit Docker Compose

1. Lege im [Discord Developer Portal](https://discord.com/developers/applications) einen Bot an. Aktiviere **Message Content Intent** und lade ihn mit dem Scope **bot** sowie **Kanal anzeigen**, **Nachrichten senden** und **Nachrichtenverlauf lesen** auf deinen Server ein.
2. Erstelle einen [OpenAI-API-Schlüssel](https://platform.openai.com/api-keys).
3. Kopiere `.env.example` nach `.env`. Trage `DISCORD_TOKEN`, `OPENAI_API_KEY`, ein eigenes `POSTGRES_PASSWORD` und den gewünschten `BOT_NAME` ein. Den sichtbaren Namen stellst du separat im Discord Developer Portal ein; verwende dort denselben Namen.
4. Starte `docker compose up -d`. Für Updates: `docker compose pull && docker compose up -d`.

`compose.yaml` startet den Bot mit PostgreSQL für ausdrücklich gespeicherte Erinnerungen und Redis für laufende Gespräche. Sichere das PostgreSQL-Volume regelmäßig. Du kannst den Bot auch über Docker-Oberflächen wie Unraid als einzelnen Container starten; dann benötigt er zusätzlich `DATABASE_URL` und `REDIS_URL` für erreichbare PostgreSQL- und Redis-Dienste. Alle Einstellungen lassen sich als Umgebungsvariablen übergeben, eine `.env`-Datei ist dabei nicht nötig.

Nachrichten, die der Bot mit dem Sprachmodell beantwortet, und gespeicherte Fakten werden an die OpenAI API übertragen. Teile das den Personen auf deinem Server mit. Veröffentliche niemals Token, API-Schlüssel oder `.env` im Repository.

Lizenz: MIT.
