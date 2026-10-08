# Native Home Assistant cards / Native Home-Assistant-Karten

## Deutsch

Die Integration verwendet Standardkarten von Home Assistant.
Vorhandene `custom:switchbot-access-card`-Karten durch eine Standardkarte ersetzen.
Nach dem HACS-Update Home Assistant neu starten und die Browserseite neu laden.

Der Sensor **Letzter Zutritt** zeigt als Zustand den zuletzt erkannten Namen.
Beim Öffnen seiner Details findest du:

- `last_access_time`: Zeitpunkt als ISO-Zeit für Automationen.
- `last_access_local`: Datum und Uhrzeit in deiner HA-Zeitzone.
- `last_access_user_id`: Benutzer-ID des letzten Zutritts.
- `history`: automatisch ergänzte Ansicht der neuesten 100 Zutritte mit Namen und Zeiten.
- `access_count`: Anzahl aller gespeicherten Fingerabdruck-Zutritte.
- `sync_pending`: Ein neuer Zutritt wird gerade abgefragt; der letzte bestätigte Name bleibt sichtbar.

Dashboard → Bearbeiten → Karte hinzufügen → **Entitäten**: Sensor auswählen.
Für den Namen plus Zeitpunkt nutze [last-access.yaml](last-access.yaml).
Für den importierten Verlauf eine Standardkarte **Markdown** hinzufügen,
im Code-Editor [dashboard.yaml](dashboard.yaml) einfügen und
`sensor.YOUR_LOCK_LAST_ACCESS` durch deine tatsächliche Sensor-ID ersetzen.
Sie zeigt die neuesten 30 Zutritte; für bis zu 100 den Wert `history[:30]` ändern.
Die Vorlage folgt der HA-Instanzsprache (Deutsch oder Englisch).
Alternativ stehen feste [deutsche](dashboard-de.yaml) und [englische](dashboard-en.yaml) Vorlagen bereit.

Archivierte und neue Zutritte erscheinen automatisch unter **Aktivität** auf der
Geräteseite des Log-Companions, z. B. „Kai hat geöffnet“. Im Dashboard ist dafür die Standardkarte
**Aktivität** verfügbar: [activity.yaml](activity.yaml). Recorder und Logbuch
müssen aktiviert sein und die Entität sowie diese Ereignisse aufzeichnen.
Historische Zutritte werden mit ihrer ursprünglichen Uhrzeit einmalig übernommen.
Der Importstand bleibt über Neustarts erhalten; nachgeholte Zutritte werden ergänzt.
Importe lösen den Live-Automationstrigger nicht aus. Aktivität unterliegt der
Recorder-Aufbewahrungsdauer; das separate Archiv bleibt erhalten. Sensoränderungen
können dort zusätzlich erscheinen.

Für Automationen auf **jedem** Zutritt den Ereignis-Trigger
`switchbot_lock_logs_access` verwenden, auch wenn derselbe Benutzer zweimal
öffnet. Die Ereignisdaten enthalten `user_name`, `user_id`, `timestamp` und
`entity_id`; bei mehreren Schlössern nach `entity_id` filtern. Alternativ kann
eine Zustandsautomation auf das Attribut `last_access_time` des Sensors reagieren.
Ein Trigger nur auf den Namenszustand erkennt zwei Zutritte derselben Person
nicht als Namenswechsel.

Die Ansicht enthält Fingerabdruck-Entriegelungen mit Benutzer-ID. Verriegelungen
und andere Methoden bleiben ausgeblendet. Zusammengehörige Entriegelung und
Riegelöffnung werden einmal angezeigt. Das belegt die Entriegelung, keinen
physischen Eintritt. Namen stammen aus deinen ID-Zuordnungen; unbenannte IDs
erscheinen als „User 10“ usw.

Der schnelle Bluetooth-Abruf startet bei einer Entriegelung des offiziellen
SwitchBot-Lock-Sensors. Verzögerte Datensätze werden begrenzt erneut abgefragt.
Nachts um 03:00 Uhr werden bis zu 100 Datensätze abgeglichen. Das vollständige
Archiv wird dauerhaft ergänzt; es wird nicht durch die 100 Einträge im
Entitätsattribut begrenzt. `switchbot_lock_logs.get_stored_lock_logs` gibt das
vollständige Roharchiv zurück. Große History-Attribute werden nicht bei jedem
Sensorwechsel in Recorder kopiert. Matter allein liefert diese BLE-Logs nicht.

Das Logo ist unter `custom_components/switchbot_lock_logs/brand/` enthalten.
HA nutzt diese lokalen Markenbilder. Einige HACS-Versionen zeigen trotz lokaler
Markenbilder ein Platzhalterbild; siehe HACS issue #5402.

## English

The integration uses native **Entities**, **Markdown** and **Activity** cards only.
Replace existing custom access cards, restart HA and reload the browser.
Use [last-access.yaml](last-access.yaml) for the latest name and time,
[dashboard.yaml](dashboard.yaml) for imported history (HA instance language), or
[activity.yaml](activity.yaml) for future live events. Replace the placeholder
entity ID with your **Last access** sensor.

The sensor state is the latest confirmed fingerprint user's name. Its attributes
include the ISO timestamp (`last_access_time`), local time (`last_access_local`),
user ID, newest 100 accesses (`history`), total access count and synchronization
status. A pending fetch keeps the last confirmed name visible.

The full raw archive is preserved separately. History attributes are excluded
from Recorder snapshots to avoid duplicating the archive on every update.
Live events are recorded through Logbook when enabled and not filtered out.
Archived accesses are also imported into Activity using their original timestamps.
Delivered accesses are remembered across restarts, and recovered gaps are appended.
Historical imports never replay the live automation trigger. Activity follows
Recorder retention; the separate archive remains intact.

Automations should trigger on `switchbot_lock_logs_access`, or on the sensor's
`last_access_time` attribute, to detect repeated accesses by the same person.
The event includes user name, user ID, timestamp and entity ID. Filter by entity
ID for multiple locks. Unlock/unlatch pairs appear once; locking is hidden.
