"""Fingerprint access view over the complete, unchanged raw archive."""

from bisect import bisect_left

from .lock_logs.models import LogEntry


def fingerprint_accesses(records: list[LogEntry]) -> list[LogEntry]:
    """Keep unlocks, using an unpaired unlatch as a fallback access event."""
    candidates = [
        record
        for record in records
        if record.user_id is not None
        and record.as_dict({})["source_name"] == "fingerprint"
        and record.as_dict({})["action_name"] in ("unlock", "unlatch")
    ]
    times: dict[int, list[int]] = {}
    for record in candidates:
        if record.as_dict({})["action_name"] == "unlock":
            assert record.user_id is not None
            times.setdefault(record.user_id, []).append(record.timestamp)
    for values in times.values():
        values.sort()
    result = []
    for record in candidates:
        if record.as_dict({})["action_name"] == "unlock":
            result.append(record)
            continue
        assert record.user_id is not None
        values = times.get(record.user_id, [])
        index = bisect_left(values, record.timestamp - 15)
        if index == len(values) or values[index] > record.timestamp + 15:
            result.append(record)
    return result
