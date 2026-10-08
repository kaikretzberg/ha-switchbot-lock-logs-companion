"""Decode the format documented by the reference fork commit e1d8fa7."""

from .models import LogEntry


class ProtocolError(ValueError):
    """Malformed, rejected or incomplete device response."""


def parse_response(
    response: bytes | None, device_id: str, model: str = ""
) -> LogEntry | None:
    """Parse one decrypted response; only a full zero record ends iteration."""
    if not isinstance(response, bytes) or not response or response[0] not in (1, 6):
        raise ProtocolError("Lock rejected log command or returned no response")
    data = response[1:]
    if len(data) < 8:
        raise ProtocolError("Log record is shorter than its eight-byte header")
    if not any(data):
        return None
    timestamp = int.from_bytes(data[:4], "big")
    if timestamp == 0:
        raise ProtocolError("Non-empty log record has a zero timestamp")
    payload = data[8:]
    # The original integration recognizes only these two payload patterns.
    # Never interpret other payloads as a user identity.
    user_id = (
        payload[2]
        if len(payload) >= 6
        and payload[0] == 0x59
        and payload[1] in (1, 3)
        and payload[2] != 0
        else None
    )
    if model == "lock_pro":
        # Observed on Lock Pro with fingerprint keypad, correlated with app
        # events. Do not apply the reference-fork identity pattern to this model.
        user_id = (
            payload[2]
            if len(payload) == 6
            and payload[:2] == b"\x7b\x03"
            and payload[2] != 0
            and payload[3:] == b"\x00" * 3
            and data[5:8] in (b"\x02\x0f\x00", b"\x01\x0f\x40")
            else None
        )
    return LogEntry(
        timestamp,
        data[4],
        data[5],
        data[6],
        data[7],
        payload.hex(),
        data.hex(),
        device_id,
        user_id,
        model,
    )
