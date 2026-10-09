# SwitchBot Lock Logs

<img src="custom_components/switchbot_lock_logs/brand/icon.png" alt="SwitchBot Lock Logs" width="100">

See who last unlocked your SwitchBot lock with a fingerprint, and keep a history
of accesses in Home Assistant. Names are assigned in a guided setup dialog.
The integration uses the official SwitchBot Bluetooth integration and native
Home Assistant entities, cards and automations.

[English](#english) · [Deutsch](#deutsch)

## English

### Requirements

- Home Assistant **2026.9.4 or later**, with PySwitchbot provided by the
  official SwitchBot Bluetooth integration. The encrypted transport is verified
  against **2.4.1, 2.9.0 and 3.0.0**. New versions with the same transport contract
  are accepted automatically; incompatible changes require an integration update.
  No separate Python package installation is needed.
- Your lock is already configured in **SwitchBot Bluetooth** and reachable
  through Home Assistant Bluetooth or a compatible Bluetooth proxy.
- For fingerprint access names: a **SwitchBot Lock Pro** and **Keypad Touch**.
  Other recognized SwitchBot lock models can expose their available history;
  the access view requires a fingerprint record with a user ID.
- **HACS** for the recommended installation.

A Matter-only connection does not provide the Bluetooth history used here.
The integration does not require a SwitchBot cloud account or API token.

### Install and set up

1. Confirm that your lock works in the official **SwitchBot Bluetooth** integration.
2. Open **HACS → ⋮ → Custom repositories**. Add
   `https://github.com/kaikretzberg/ha-switchbot-lock-logs-companion`
   with type **Integration**.
3. Find **SwitchBot Lock Logs** in HACS, download it and **restart Home Assistant**.
4. Open **Settings → Devices & services → Add integration → SwitchBot Lock Logs**.
5. Select your lock. The wizard downloads its history; this can take up to
   **3 minutes**. Keep the dialog open.
6. Enter a name for each user ID. Match the displayed times against
   **SwitchBot app → Home → select the lock → Event history**.
7. Check the history and finish setup.

SwitchBot manages names in its app; the lock history provides IDs. Your name
mappings are stored locally. Later, use **Configure** to download history again,
rename IDs. A blank name removes a mapping.

For manual installation, download the GitHub release's **Source code (zip)**,
extract it and copy `custom_components/switchbot_lock_logs` to
`/config/custom_components/switchbot_lock_logs`. Restart HA and continue at step 4.

### Entities and history

A linked **Logs** device contains:

| Entity | Purpose |
| --- | --- |
| Last access | Last fingerprint user's name, with time, user ID and history attributes |
| Last activity | Time of the latest fingerprint opening |
| Last user | User attached to the latest applicable event |
| Last action | Latest fingerprint opening action |
| Log count | Number of archived fingerprint accesses |

The **Last access** entity has `last_access_time` (ISO timestamp),
`last_access_local` (time in your HA time zone), `last_access_user_id`,
`history` (newest 100 fingerprint accesses), `access_count` (total accesses) and
`sync_pending` (a new access is being fetched). The archive continues growing
beyond the 100 entries exposed in the entity.

When HA detects an unlock, the integration starts a small Bluetooth history
request immediately and retries delayed records. No separate automation is
required. At **02:00 local time** it makes up to 100 history read requests.
There is no periodic polling interval. The Bluetooth connection is released
after each history request, including failures and between retries. The lock's response
and Bluetooth connection determine when a name becomes available.
New downloads merge into the archive; duplicates and empty responses do not
remove older records. Unlock and matching unlatch records appear once in the
fingerprint access view. Locking and other methods remain in the raw archive.
The history records unlocking, rather than detecting a physical entry.

### Native dashboard and Activity

Use standard **Entities**, **Markdown** or **Activity** cards. No custom card or
JavaScript resource is needed.

- For the latest person, add an **Entities** card and select **Last access**.
- For names and times from the imported history, add a **Markdown** card,
  open its code editor, paste [dashboard.yaml](docs/dashboard.yaml) and replace
  `sensor.YOUR_LOCK_LAST_ACCESS` with your Last access entity ID. It follows the
  HA instance language automatically and displays the latest 30 accesses.
- For imported and future accesses, use an **Activity** card with the Last access entity:
  [activity.yaml](docs/activity.yaml).

Archived fingerprint accesses are imported automatically into the Last access
entity's and companion device's **Activity** with their original dates and times.
Messages are short (e.g. “Kai unlocked”); HA displays the date and time separately.
Last access retains the confirmed person during connection failures. Its
`lock_available` and `last_sync_success` attributes expose connection and read status.
New accesses, including gaps found during the nightly sync, are appended.
Delivered accesses are remembered across restarts; unlock/unlatch pairs appear once.
Names are resolved when viewing Activity, so later name changes also apply there.
Recorder and Logbook must be enabled and include the entity and events. Activity
uses Recorder's retention period; the separate raw archive remains intact. Historical
imports use `switchbot_lock_logs_imported_access`, so they never replay the live
`switchbot_lock_logs_access` automation trigger.
See [all native card examples](docs/DASHBOARD.md).

### Automations and actions

Trigger on **`switchbot_lock_logs_access`** to respond to each live fingerprint
access, including repeated accesses by the same person. The event contains
`user_name`, `user_id`, `timestamp` and `entity_id`. Filter by `entity_id` if you
have multiple locks. A state trigger on the entity's **`last_access_time`**
attribute is another option; the name alone does not change when the same user
opens twice.

Under **Developer tools → Actions**:

| Action | Purpose |
| --- | --- |
| `switchbot_lock_logs.get_lock_logs` | Read history, return only fingerprint openings and update the raw archive |
| `switchbot_lock_logs.get_stored_lock_logs` | Return the full archive without a Bluetooth request |
| `switchbot_lock_logs.set_lock_user_name` | Set one user-ID mapping |
| `switchbot_lock_logs.delete_lock_user_name` | Remove one mapping |

Choose the **official lock's device ID**, rather than the linked Logs device.
Fresh history requests accept `max_entries: 1–100` (maximum read requests) and
`base_time: 0` (no time filter) or a Unix timestamp. The response contains
`device_id`, `count` and `logs`. Use Developer tools' response view or a script's
`response_variable` to inspect it.

### Language, updates and help

HA loads the German or English dialog and action translations according to your
profile language. Generated history text and Activity descriptions use the HA
instance language; other languages fall back to English. User names stay as entered.

For updates, use **HACS → SwitchBot Lock Logs → Update**, restart HA and reload
the browser. Keep the integration configured: stored history and names remain.
If upgrading from the earlier 2.0.x development releases to the new **2.0.0**
release, use **Redownload → 2.0.0** once because the version number is lower.
Replace any old `custom:switchbot-access-card` with a native card above.

If no lock is listed, set up **SwitchBot Bluetooth** first. If a download fails,
check the lock's Bluetooth reachability and retry from **Configure**. If names
are missing, assign the IDs in the wizard. The included logo is used by HA;
HACS logo display depends on HACS's handling of local brand images.

Report problems in [GitHub Issues](https://github.com/kaikretzberg/ha-switchbot-lock-logs-companion/issues).

## Deutsch

### Voraussetzungen

- Home Assistant **ab 2026.9.4** mit PySwitchbot aus der offiziellen
  SwitchBot-Bluetooth-Integration. Die verschlüsselte Schnittstelle ist mit
  **2.4.1, 2.9.0 und 3.0.0** geprüft. Neue Versionen mit demselben Ablauf werden
  automatisch akzeptiert; inkompatible Änderungen benötigen ein Integrationsupdate.
  Du musst kein Python-Paket zusätzlich installieren.
- Dein Schloss ist in **SwitchBot Bluetooth** eingerichtet und über Bluetooth
  oder einen kompatiblen Bluetooth-Proxy erreichbar.
- Für Namen bei Fingerabdruck-Zutritten: **SwitchBot Lock Pro** und **Keypad Touch**.
  Andere erkannte SwitchBot-Schlossmodelle können ihren verfügbaren Verlauf
  liefern; die Zutrittsansicht benötigt Fingerabdruck-Ereignisse mit Benutzer-ID.
- **HACS** für die empfohlene Installation.

Eine reine Matter-Verbindung liefert diese Bluetooth-Historie nicht.
Ein SwitchBot-Cloudkonto oder API-Token ist für diese Integration nicht nötig.

### Installation und Einrichtung

1. Prüfe, dass dein Schloss in **SwitchBot Bluetooth** funktioniert.
2. Öffne **HACS → ⋮ → Benutzerdefinierte Repositories**. Füge
   `https://github.com/kaikretzberg/ha-switchbot-lock-logs-companion`
   mit Typ **Integration** hinzu.
3. Suche **SwitchBot Lock Logs**, lade die Integration herunter und
   **starte Home Assistant neu**.
4. Öffne **Einstellungen → Geräte & Dienste → Integration hinzufügen → SwitchBot Lock Logs**.
5. Wähle dein Schloss. Der Assistent lädt die Historie; das kann **bis zu
   3 Minuten** dauern. Lass das Fenster geöffnet.
6. Trage für jede Benutzer-ID einen Namen ein. Vergleiche die angezeigten Zeiten
   mit **SwitchBot-App → Startseite → Lock auswählen → Ereignisprotokoll**.
7. Prüfe den Verlauf und schließe die Einrichtung ab.

Die SwitchBot-App verwaltet Namen; das Schlossprotokoll liefert IDs. Deine
Zuordnungen werden lokal gespeichert. Unter **Konfigurieren** kannst du später
erneut die Historie abrufen oder Namen ändern.
Ein leeres Namensfeld entfernt die Zuordnung.

Für eine manuelle Installation lade **Source code (zip)** des GitHub-Releases
herunter, entpacke es und kopiere `custom_components/switchbot_lock_logs` nach
`/config/custom_components/switchbot_lock_logs`. Starte HA neu und fahre bei Schritt 4 fort.

### Entitäten und Verlauf

Ein mit dem Schloss verknüpftes **Logs**-Gerät enthält:

| Entität | Funktion |
| --- | --- |
| Letzter Zutritt | Letzter Fingerabdruck-Benutzer mit Zeitpunkt, ID und Verlauf als Attribute |
| Letzte Aktivität | Zeitpunkt der letzten Fingerabdruck-Öffnung |
| Letzter Benutzer | Benutzer des zuletzt zuordenbaren Ereignisses |
| Letzte Aktion | Aktion der letzten Fingerabdruck-Öffnung |
| Anzahl Protokolleinträge | Anzahl gespeicherter Fingerabdruck-Zutritte |

Die Entität **Letzter Zutritt** enthält `last_access_time` (ISO-Zeitstempel),
`last_access_local` (Datum und Uhrzeit in deiner HA-Zeitzone),
`last_access_user_id`, `history` (neueste 100 Fingerabdruck-Zutritte),
`access_count` (Gesamtzahl Zutritte) und `sync_pending` (ein neuer Zutritt wird
abgerufen). Das Archiv wächst über die 100 Einträge im Attribut hinaus weiter.

Sobald HA eine Entriegelung erkennt, startet ein kleiner Bluetooth-Abruf.
Verzögert verfügbare Einträge werden erneut abgefragt. Eine eigene Automation
brauchst du dafür nicht. Um **02:00 Uhr** erfolgen bis zu 100 Leseabfragen.
Einen Intervallabruf gibt es nicht mehr. Nach jedem Historienabruf wird die
Bluetooth-Verbindung freigegeben, auch bei Fehlern und zwischen Wiederholungen.
Wie schnell der Name erscheint, hängt vom Schloss und der Bluetooth-Verbindung ab.
Neue Abrufe ergänzen das Archiv. Duplikate und leere Antworten löschen keine
älteren Einträge. Entriegelung und zugehörige Riegelöffnung werden in der
Zutrittsansicht zusammengefasst; Verriegelungen und andere Methoden bleiben im
Roharchiv. Erfasst wird die Entriegelung, kein tatsächlicher Eintritt.

### Native Karten und Aktivität

Nutze ausschließlich die Standardkarten **Entitäten**, **Markdown** oder
**Aktivität**. Eine Custom-Karte oder JavaScript-Ressource ist nicht nötig.

- Letzter Name: **Entitäten**-Karte hinzufügen und **Letzter Zutritt** auswählen.
- Importierter Verlauf mit Namen und Zeiten: **Markdown**-Karte hinzufügen,
  Code-Editor öffnen, [dashboard.yaml](docs/dashboard.yaml) einfügen und
  `sensor.YOUR_LOCK_LAST_ACCESS` durch deine Sensor-ID ersetzen. Die Vorlage
  folgt automatisch der HA-Instanzsprache und zeigt die neuesten 30 Zutritte.
- Importierte und neue Zutritte: **Aktivität**-Karte mit dem Sensor verwenden:
  [activity.yaml](docs/activity.yaml).

Archivierte Fingerabdruck-Zutritte werden automatisch mit ihrem ursprünglichen
Datum und ihrer Uhrzeit in die **Aktivität** der Entität „Letzter Zutritt“ und des
zugehörigen Geräts importiert. Die Meldung lautet kurz „Kai hat geöffnet“; Datum
und Uhrzeit zeigt HA daneben. „Letzter Zutritt“ bleibt bei Verbindungsproblemen
sichtbar. Die Attribute `lock_available` und `last_sync_success` zeigen den
Verbindungs- und Abrufstatus separat. Neue Zutritte und beim nächtlichen
Abgleich gefundene Lücken werden ergänzt. Der Importstand bleibt über Neustarts
hinweg gespeichert; Entriegelung und zugehörige Riegelöffnung erscheinen einmal.
Spätere Namensänderungen gelten auch für die Aktivitätsansicht. Recorder und Logbook
müssen aktiviert sein und die Entität sowie Ereignisse einschließen. Die Aktivität
unterliegt der Aufbewahrungsdauer des Recorders; das separate Roharchiv bleibt erhalten.
Historische Importe verwenden `switchbot_lock_logs_imported_access` und lösen den
Live-Automationstrigger `switchbot_lock_logs_access` nicht aus.
Siehe [alle nativen Kartenbeispiele](docs/DASHBOARD.md).

### Automationen und Aktionen

Das Ereignis **`switchbot_lock_logs_access`** wird für jeden neuen Live-Zutritt
per Fingerabdruck ausgelöst, auch wenn dieselbe Person erneut öffnet.
Es enthält `user_name`, `user_id`, `timestamp` und `entity_id`. Bei mehreren
Schlössern nach `entity_id` filtern. Alternativ auf Änderungen des Attributs
**`last_access_time`** reagieren. Der Name allein ändert sich bei zwei
Öffnungen derselben Person nicht.

Unter **Entwicklerwerkzeuge → Aktionen** stehen bereit:

| Aktion | Funktion |
| --- | --- |
| `switchbot_lock_logs.get_lock_logs` | Verlauf abrufen, nur Fingerabdruck-Öffnungen ausgeben und Roharchiv ergänzen |
| `switchbot_lock_logs.get_stored_lock_logs` | Vollständiges Archiv ohne Bluetooth-Abfrage ausgeben |
| `switchbot_lock_logs.set_lock_user_name` | Einen Namen zuordnen |
| `switchbot_lock_logs.delete_lock_user_name` | Eine Zuordnung entfernen |

Verwende die **Geräte-ID des offiziellen Schlosses**, nicht die des Logs-Geräts.
Aktuelle Abrufe akzeptieren `max_entries: 1–100` (maximale Leseabfragen) und
`base_time: 0` (ohne Zeitfilter) oder einen Unix-Zeitstempel. Die Antwort enthält
`device_id`, `count` und `logs`. Nutze die Antwortansicht oder
`response_variable` in einem Skript.

### Sprache, Updates und Hilfe

HA lädt deutsche oder englische Dialoge und Aktionsbeschreibungen passend zur
Profilsprache. Erzeugte Verlaufstexte und Aktivitätsmeldungen verwenden die
Sprache der HA-Instanz. Andere Sprachen verwenden Englisch. Eingetragene Namen
bleiben unverändert.

Updates: **HACS → SwitchBot Lock Logs → Aktualisieren**, HA neu starten und
Browser neu laden. Die Integration bleibt eingerichtet; Namen und Archiv bleiben
erhalten. Beim Wechsel von den bisherigen 2.0.x-Entwicklungsversionen auf
**2.0.0** einmal **Erneut herunterladen → 2.0.0** wählen, da die Versionsnummer
kleiner ist. Alte `custom:switchbot-access-card`-Karten durch Standardkarten ersetzen.

Wird kein Schloss angeboten, zuerst **SwitchBot Bluetooth** einrichten.
Schlägt der Abruf fehl, Bluetooth-Erreichbarkeit prüfen und unter
**Konfigurieren** erneut versuchen. Fehlen Namen, IDs im Assistenten benennen.
Das Logo ist enthalten und wird von HA verwendet; die Anzeige in HACS hängt
von dessen Unterstützung lokaler Markenbilder ab.

Probleme bitte unter [GitHub Issues](https://github.com/kaikretzberg/ha-switchbot-lock-logs-companion/issues) melden.

## License

MIT. Original notices are retained in [LICENSE](LICENSE) and
[PySwitchbot license](docs/PYSWITCHBOT_LICENSE).

### Activity entries and diagnostic export / Aktivität und Diagnose-Export

A plain name in Activity is a native sensor state change. “Name unlocked” is
an actual archived access event. HA can display both in the same entity view;
this does not mean that the archive contains a duplicate. Two access events
with different timestamps may be separate openings. Compare them with the
SwitchBot app before changing their interpretation.

Download diagnostics under **Settings → Devices & services → SwitchBot Lock
Logs → integration entry menu → Download diagnostics**. The JSON includes the
complete raw archive, timestamps, IDs, sync status and delivered Activity records.
Names are replaced by user IDs; account credentials and encryption keys are
not included. The export does not make a Bluetooth request. If the device has
been offline, first fetch fresh history under Configure.

Ein einzelner Name in „Aktivität“ ist eine native Sensoränderung. „Name hat
geöffnet“ ist ein Ereignis aus dem Schlossprotokoll. HA kann beide in derselben
Entitätsansicht anzeigen; das bedeutet keinen doppelten Archiveintrag.
Zwei Öffnungsereignisse mit verschiedenen Zeiten können verschiedene
Entriegelungen sein. Vergleiche sie mit dem Ereignisprotokoll der SwitchBot-App.

Unter **Einstellungen → Geräte & Dienste → SwitchBot Lock Logs → Menü des
Integrationseintrags → Diagnosedaten herunterladen** kannst du das vollständige
Roharchiv mit Zeitstempeln, IDs, Abrufstatus und importierten Aktivitätseinträgen
als JSON exportieren. Namen werden durch Benutzer-IDs ersetzt; Zugangsdaten und
Bluetooth-Schlüssel sind nicht enthalten. Der Export startet keinen Bluetooth-
Abruf. Bei veralteter Historie zuerst unter Konfigurieren erneut abrufen.
