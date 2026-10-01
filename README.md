# Discord-Kneipenbot

Ein Discord-Bot mit frei wählbarem Namen und norddeutschem Kneipenhumor. Das fertige Docker-Image heißt `ghcr.io/gibbershguru/discordbot:latest`.

## Funktionen

- Reagiert auf seinen Namen (`BOT_NAME`) und unterhält sich danach pro Person und Kanal bis zu fünf Minuten ohne erneutes Aufrufen.
- Antwortet kurz, trocken und gelegentlich arrogant; variiert Begrüßungen und kurze Abschiede und nutzt gelegentlich passende Standard-Emojis.
- Begrüßt neue Mitglieder auf Wunsch in einem festgelegten Kanal mit einem zufälligen Spruch.
- Merkt sich Fakten nur auf ausdrücklichen Befehl: `merk dir: ...`, `was weißt du über mich?`, `vergiss alles`.
- Speichert Erinnerungen für „in 10 Minuten“, „in 5 Tagen“ oder „am 27.12.2026 um 18 Uhr“. Unterstützt auch „heute Abend“ und fragt nach fehlendem Tag oder Uhrzeit. Die KI formuliert beim Anlegen einen passenden Spruch. `meine Erinnerungen` zeigt offene Aufträge, `lösche Erinnerung 3` entfernt einen.
- Serververwaltung: manuelle Rollenvergabe, freigegebene Selbstrollen, Beitrittsrolle, Timeouts, Nachrichtenlöschen und optionaler Mod-Log in PostgreSQL/Discord. Diese Module sind zunächst aus.
- Module pro Server: Erinnerungen standardmäßig an, Websuche aus. Aktivierte Suche recherchiert erkannte aktuelle Fragen mit klickbaren Quellen. Modellantworten können Fehler enthalten.

## Start mit Docker Compose

1. Lege im [Discord Developer Portal](https://discord.com/developers/applications) einen Bot an. Aktiviere **Message Content Intent** und lade ihn mit den Scopes **bot** und **applications.commands** sowie **Kanal anzeigen**, **Nachrichten senden** und **Nachrichtenverlauf lesen** auf deinen Server ein.
2. Erstelle einen [OpenAI-API-Schlüssel](https://platform.openai.com/api-keys).
3. Kopiere `.env.example` nach `.env`. Trage `DISCORD_TOKEN`, `OPENAI_API_KEY`, ein eigenes `POSTGRES_PASSWORD` und den gewünschten `BOT_NAME` ein. Den sichtbaren Namen stellst du separat im Discord Developer Portal ein; verwende dort denselben Namen.
4. Starte `docker compose up -d`. Für Updates: `docker compose pull && docker compose up -d`.

`compose.yaml` startet den Bot mit PostgreSQL für ausdrücklich gespeicherte Erinnerungen und Redis für laufende Gespräche. Sichere das PostgreSQL-Volume regelmäßig. Du kannst den Bot auch über Docker-Oberflächen wie Unraid als einzelnen Container starten; dann benötigt er zusätzlich `DATABASE_URL` und `REDIS_URL` für erreichbare PostgreSQL- und Redis-Dienste. Alle Einstellungen lassen sich als Umgebungsvariablen übergeben, eine `.env`-Datei ist dabei nicht nötig.

Erinnerungen liegen in PostgreSQL und werden nach einem Neustart nachgeholt, sofern der Bot im ursprünglichen Kanal noch schreiben kann. Standard-Zeitzone ist `Europe/Berlin`; mit `REMINDER_TIMEZONE` lässt sie sich ändern. Der Bot erwähnt die Person im ursprünglichen Kanal. Bis zu zehn offene Erinnerungen pro Person und Server sind möglich, höchstens ein Jahr im Voraus. Pro neuer Erinnerung erzeugt ein OpenAI-Aufruf den Text; beim Zustellen ist kein weiterer Aufruf nötig. Bei API-Problemen wird ein einfacher Ersatztext gespeichert. Bestehende Einträge bleiben erhalten.

Module verwalten: `/winston reminder status:on|off`, `/winston search status:on|off` und `/winston modules`. Discord zeigt die Auswahl **on/off** an. Der Befehlsname kommt aus `BOT_NAME` (kleingeschrieben; Leerzeichen/Sonderzeichen werden zu Bindestrichen). Änderungen gelten sofort und bleiben nach Neustarts erhalten. Serverbesitzer, Administratoren und optional in `BOT_OWNER_IDS` eingetragene Discord-Nutzer-IDs dürfen die Befehle nutzen. Normale Mitglieder können keine Einstellungen ändern. `reminder off` verhindert neue Aufträge; offene Erinnerungen lassen sich weiter anzeigen/löschen und werden zugestellt. Websuche nutzt OpenAI, benötigt keinen Google-Schlüssel und verursacht zusätzliche API-Kosten. Suchantworten mit Quellen werden in PostgreSQL für eine Stunde gespeichert (`SEARCH_CACHE_SECONDS`, 60–86400 Sekunden); passende Wiederholungen benötigen keinen KI- oder Suchaufruf. Die Gültigkeit läuft auch über Mitternacht weiter; nur kalenderbezogene Fragen wie „heute“/„morgen“ erhalten am nächsten Tag einen neuen Cache-Schlüssel. Eindeutige eigenständige Faktenfragen können innerhalb desselben Servers wiederverwendet werden; Rückfragen bleiben nach Person und Gesprächskontext getrennt. Abgelaufene Einträge werden bei neuer Recherche bereinigt. Normale Gespräche und Erinnerungsformulierungen nutzen diesen Cache nicht. Falls Slash-Befehle fehlen, die App auf demselben Server mit dem Scope **applications.commands** autorisieren.

## Serververwaltung

Alle Befehle beginnen mit dem aus `BOT_NAME` abgeleiteten Namen, hier `/winston`:

| Befehl | Zweck |
| --- | --- |
| `roles status:on`, `moderation status:on` | Module aktivieren (`off` deaktiviert sie) |
| `modlog status:on kanal:#mod-log` | Bot-Aktionen in PostgreSQL und optional einem Textkanal protokollieren |
| `rolle mitglied:@Person rolle:@Rolle aktion:geben` | Rolle geben oder mit `entfernen` ablegen |
| `rollenfreigabe rolle:@Rolle status:on` | Rolle zur Selbstauswahl freigeben (`off` sperrt sie) |
| `selbstrollen`, `selbstrolle rolle:@Rolle aktion:nehmen` | Freigaben ansehen, Rolle nehmen oder mit `ablegen` entfernen |
| `autorolle status:on rolle:@Rolle` | Beitrittsrolle setzen (`off` deaktiviert sie) |
| `timeout mitglied:@Person minuten:10 grund:…` | Timeout bis 28 Tage; `minuten:0` hebt ihn auf |
| `clean anzahl:10 grund:…` | Letzte 1–100 Nachrichten prüfen und löschen; angeheftete und über 14 Tage alte bleiben |
| `modlogs` | Letzte zehn Bot-Aktionen ansehen (Admin) |

Module/Freigaben konfigurieren dürfen Serverbesitzer, Administratoren und `BOT_OWNER_IDS`. Rollenvergabe und Moderation erfordern zusätzlich die tatsächlichen Discord-Rechte des Ausführenden; Owner-IDs umgehen diese Prüfung nicht. Der Bot benötigt **Rollen verwalten**, **Nachrichten verwalten** beziehungsweise **Mitglieder moderieren/Timeout** sowie Kanalzugriff. Die Bot-Rolle muss oberhalb der zu vergebenden Rollen und der zu moderierenden Personen stehen. Selbstwahl/Beitrittsrollen dürfen keine Verwaltungsrechte besitzen, auch nicht über Kanalüberschreibungen. Einstellungen gelten pro Server und bleiben nach Neustarts erhalten. Abschalten entfernt keine bereits vergebenen Rollen oder laufenden Timeouts.

Für Beitrittsrollen: `MEMBER_EVENTS_ENABLED=true` setzen, im Developer Portal **Server Members Intent** aktivieren und den Container neu starten. Das Rollenmodul muss ebenfalls an sein. Die Vergabe gilt nur für neu beitretende Personen, nicht rückwirkend und nicht für Bots. Mod-Logs erfassen nur diese Bot-Aktionen, keine allgemeine Überwachung oder gelöschten Nachrichteninhalte. Moderationsbefehle benötigen keinen OpenAI-Aufruf.

Für automatische Begrüßungen: Im Discord Developer Portal zusätzlich **Server Members Intent** aktivieren und `WELCOME_CHANNEL_ID` auf die ID des gewünschten Textkanals setzen. Der Bot braucht dort **Kanal anzeigen** und **Nachrichten senden**. `WELCOME_CHANNEL_ID=0` (Standard) deaktiviert die Funktion; sofern auch `MEMBER_EVENTS_ENABLED=false` ist, ist der zusätzliche Intent nicht erforderlich. Ein erneutes Einladen des Bots ist nicht nötig.

Nachrichten, die der Bot mit dem Sprachmodell beantwortet, sowie neue Erinnerungswünsche und gespeicherte Fakten werden an die OpenAI API übertragen. Teile das den Personen auf deinem Server mit. Veröffentliche niemals Token, API-Schlüssel oder `.env` im Repository.

Lizenz: MIT.
