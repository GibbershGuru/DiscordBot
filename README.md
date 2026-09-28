# Winston – Discord-Bot für die Kneipe

Winston antwortet erst, wenn eine Nachricht seinen Namen enthält. Im selben Channel führt er danach je Benutzer bis zu fünf Minuten nach der letzten Antwort ein eigenes Gespräch. Danach verabschiedet er sich und wartet wieder auf seinen Namen. Der Name ist frei wählbar. Er braucht keine Discord-Erwähnung als Auslöser.

Sein Stil ist norddeutscher Kneipenschnack: erst eine kurze, hilfreiche Antwort, dann wenn es passt eine trockene und ziemlich freche Pointe. Er redet die Person nicht zusätzlich mit Namen an, weil die Discord-Antwort bereits eine Erwähnung enthält.
Zusätzliche Erwähnungen am Anfang einer Modellantwort werden entfernt. Fragen nach Winstons eigener Technik, Herkunft oder Version beantwortet er mit einem Kneipenspruch statt mit Angaben zu seinem Innenleben.
Winston nutzt keine Websuche. Bei erkannten Fragen nach aktuellen Veröffentlichungen, Terminen, Neuigkeiten oder wechselnden Amtsinhabern antwortet er schlicht „Weiß ich nicht sicher.“; auch direkte Nachfragen dazu beantwortet er so. Dafür ruft er die OpenAI API nicht auf. Bei anderen Fragen soll er Wissenslücken ebenfalls offen zugeben statt zu raten. Ohne Recherche lässt sich das bei frei formulierten Modellantworten nicht vollständig garantieren. Ein „Tschüss“, „Ciao“, „Bis später“ oder ähnlicher eigener Abschied beendet die Sitzung sofort; eine fremde Nachricht mit demselben Text eröffnet danach kein Gespräch mehr.

## Inbetriebnahme auf Unraid

1. Erstelle im [Discord Developer Portal](https://discord.com/developers/applications) eine eigene Anwendung mit Bot, aktiviere unter **Bot → Privileged Gateway Intents** den **Message Content Intent**. Wähle im OAuth2-URL-Generator nur den Scope **bot** und die Berechtigungen **Kanal anzeigen** (View Channels), **Nachrichten senden** (Send Messages) und **Nachrichtenverlauf lesen** (Read Message History). **Administrator**, **Links einbetten** und `applications.commands` benötigt die aktuelle Version nicht. Im Portal legst du auch den sichtbaren Benutzernamen fest; `BOT_NAME` unten bestimmt das gesuchte Triggerwort und die Persönlichkeit. Setze beides gleich.
2. Erzeuge einen API-Key im [OpenAI Dashboard](https://platform.openai.com/api-keys) und setze dort ein für dich passendes Ausgabenlimit. Der Bot ruft die API nur für Gespräche auf, nicht für jede Nachricht.
3. Lege auf Unraid ein Verzeichnis für `compose.yaml` und `.env` an. Kopiere `.env.example` nach `.env`; trage Discord-Token, API-Key und ein eigenes langes PostgreSQL-Passwort ein. Setze `WINSTON_IMAGE=ghcr.io/gibbershguru/discordbot:latest` (bei einem Fork deinen eigenen GitHub- und Repository-Namen in Kleinbuchstaben) und bei Bedarf `BOT_NAME=EuerName`. Ein öffentlicher GHCR-Paketstatus ist für das Laden ohne Anmeldung nötig; siehe unten.
4. Starte im Verzeichnis `docker compose up -d`. Auf Unraid geht das auch mit dem Compose Manager. Für spätere Updates: `docker compose pull && docker compose up -d`. Die PostgreSQL-Daten liegen im benannten Docker-Volume `winston_postgres` und bleiben bei Image-Updates erhalten. Sichere dieses Volume regelmäßig. Redis verwahrt nur laufende Sitzungen.

Die Compose-Datei startet eine eigene PostgreSQL-17- und Redis-Instanz. Wer bereits eigene Dienste betreibt, kann beide Compose-Services entfernen und `DATABASE_URL` und `REDIS_URL` direkt auf die vorhandenen Dienste setzen. Im Docker-Netzwerk ist `localhost` im Bot-Container nicht der Unraid-Host. Es sind keine Ports nach außen erforderlich.

## Verhalten

```text
Max: Wie hoch ist der höchste Berg?          → keine Antwort
Max: Winston, wie hoch ist der höchste Berg? → @Max kurze Antwort
Max: Und wie kalt ist es dort?                → @Max Antwort mit Gesprächskontext
Lisa: Was ist mit mir?                        → keine Antwort, bis Lisa Winston anspricht
```

Jede Sitzung ist an Server, Channel und Benutzer-ID gebunden. Während einer aktiven Sitzung beantwortet Winston *jede* weitere Textnachricht dieser Person in diesem Channel, auch wenn sie für andere bestimmt war. Nach fünf Minuten ohne Antwort folgt ein lokaler Abschiedsgruß. Verlauf: maximal vier Austauschpaare in Redis, ohne dauerhafte Chat-Protokolle. Nach einem Neustart bleiben Redis-Sitzungen nicht erhalten; explizite Erinnerungen in PostgreSQL schon.

Erinnerungen schreibt Winston **nur auf ausdrücklichen Befehl** und nur für die Person selbst (maximal zehn Fakten, je 200 Zeichen):

```text
Winston, merk dir: Ich mag Gin.
Winston, was weißt du über mich?
Winston, vergiss alles
```

Die gespeicherten Fakten werden bei Modellantworten an OpenAI übermittelt. Informiere deine Freunde darüber. Gesprächsnachrichten, für die Winston das Modell benötigt, werden an die OpenAI API gesendet; erkannte Abschiede und aktuelle Fragen werden lokal beantwortet. Discord-Nachrichten außerhalb aktiver Sitzungen oder ohne Triggerwort werden lokal ignoriert.

## GitHub und Image

Dieses öffentliche Repository enthält alle Projektdateien. Jeder Push auf `main` baut mit GitHub Actions das Image `ghcr.io/gibbershguru/discordbot:latest` für x86-64 und ARM64. Git-Tags wie `v1.0.0` erzeugen zusätzlich eine feste Versionsmarke. Der Workflow benötigt keinen eigenen Registry-Schlüssel; er verwendet das GitHub-`GITHUB_TOKEN`.

**Wichtig:** Ein öffentliches Repository macht ein GHCR-Image bei der ersten Veröffentlichung nicht zwingend öffentlich. Stelle nach dem ersten erfolgreichen Workflow-Lauf unter **Packages → discordbot → Package settings → Change visibility** das Paket auf **Public**. Erst danach kann Unraid es ohne Registry-Anmeldung laden. `latest` wird bei jedem Push auf `main` überschrieben; auf Unraid bleiben Updates manuell kontrolliert, bis du `docker compose pull` ausführst.

Im öffentlichen Repository niemals `.env`, Discord-Token, OpenAI-Key oder Datenbankpasswort committen. `.gitignore` ignoriert `.env` bereits.

## Lokal entwickeln

```bash
cp .env.example .env
# Eigene Werte in .env setzen; für einen lokalen Build in compose.yaml image durch build: . ersetzen.
docker compose up --build -d
docker compose logs -f bot
```

Konfiguration: `BOT_NAME` (Trigger), `OPENAI_MODEL` (Standard `gpt-4.1-mini`), `MAX_OUTPUT_TOKENS` (Standard 120), `SESSION_SECONDS` (Standard 300), `DISCORD_TOKEN`, `OPENAI_API_KEY`, `POSTGRES_PASSWORD`. Der Token-Deckel begrenzt die *Ausgabe je Anfrage*, nicht die gesamten API-Ausgaben. Keine automatische Kostengarantie: Setze zusätzlich ein Budget im OpenAI Dashboard. Eine alte `WEB_SEARCH`-Variable in Unraid hat keine Wirkung mehr und kann entfernt werden.

Lizenz: MIT (siehe `LICENSE`).
