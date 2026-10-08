# Changelog

## 2.0.5 — 2026-10-08

- Remove periodic polling and its options; reconcile up to 100 raw records nightly at 02:00 HA local time. Keep immediate unlock-triggered reads and retries.
- Release the shared Bluetooth connection and reset encryption state after history retrieval, including read errors and cancellation.
- Add native diagnostic downloads with the full raw archive and Activity delivery records; omit assigned names and account/encryption secrets.
- Explain the distinction between native sensor state changes and imported access events in Activity.

## 2.0.4 — 2026-10-08

- Show only fingerprint openings in sensors and fresh log responses; retain all raw records in the separate archive.

- Short Activity messages without repeating the native date and time.
- Keep the confirmed Last access visible during Bluetooth outages or failed reads.
- Expose lock availability and synchronization status separately on Last access.

## 2.0.2 — 2026-10-08

- Import archived fingerprint accesses into native Activity with original timestamps.
- Persist delivered accesses across restarts and append accesses found by scheduled synchronization.
- Keep historical imports separate from live automation triggers; resolve names when Activity is displayed.

## 2.0.1 — 2026-10-08

- Clear name entry labels in the native user mapping form, with saved names preserved.
- Step-by-step timestamp comparison instructions for identifying users in the SwitchBot app.

## 2.0.0 — 2026-10-08

- Guided setup with history download and user name mapping.
- Automatic synchronization on unlock and nightly history reconciliation.
- Persistent event archive and a Last access entity with names and timestamps.
- Native Home Assistant dashboard cards and live Activity entries.
- German and English interface, translated action states and included brand images.
