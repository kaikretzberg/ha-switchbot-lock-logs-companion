# Protocol and dependencies

The integration uses the official SwitchBot device instance and encryption.
PySwitchbot supplies Bluetooth connection, command retries and AES handling.
The encrypted send transaction and disconnect/reset methods were compared in
2.4.1, 2.9.0 and 3.0.0 and have identical syntax trees. Their AST hashes are checked
once per runtime class in the Home Assistant executor, alongside required runtime
capabilities. Future versions with the same transaction contract are accepted
without a version allowlist. Changes to that contract stop history requests before
any command is sent and identify the incompatible method. This does not guarantee
compatibility with arbitrary future library or Home Assistant changes.
An integration-level lock serializes history downloads; the device operation
lock protects the cursor transaction. A complete request has a 180-second timeout.

The history transaction sets a uint32 base-time cursor using `57001401`, then
reads records individually using `57001405`. Empty records terminate a request;
a request makes at most 100 reads. Records are deduplicated by raw payload and
merged into the Home Assistant Store archive before publishing entity updates.

Lock Pro fingerprint records use source/action/value combinations `(2, 15, 0)`
and `(1, 15, 64)`, with credential IDs in the payload. Unknown combinations remain
raw events and do not become fingerprint accesses. The access view pairs
unlock/unlatch records for the same user within 15 seconds; separate unlocks remain.

Protocol provenance: [original integration](https://github.com/hacker-home-chile/ha-switchbot-lock-logs)
and [PySwitchbot reference](https://github.com/hacker-home-chile/pySwitchbot).
License notices remain in LICENSE and PYSWITCHBOT_LICENSE.
