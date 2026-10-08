"""Stable log model, independent of Bluetooth and Home Assistant."""

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class LogEntry:
    """One decoded reference-protocol record, with original bytes preserved."""

    timestamp: int
    index: int
    source: int
    action: int
    value: int
    payload: str
    raw: str
    device_id: str
    user_id: int | None
    model: str = ""

    def as_dict(self, users: dict[str, str]) -> dict[str, Any]:
        """Serialize; numeric codes remain authoritative."""
        result = asdict(self)
        result["user_name"] = (
            users.get(str(self.user_id), f"User {self.user_id}")
            if self.user_id is not None
            else None
        )
        if self.model == "lock_pro":
            return self._lock_pro_labels(result)
        # Labels are from the reference fork, not independent protocol validation.
        result["action_name"] = ACTION_NAMES.get(self.action, f"unknown_{self.action}")
        result["source_name"] = SOURCE_NAMES.get(self.source, f"unknown_{self.source}")
        result["result"] = {
            0: "success",
            1: "success",
            2: "jammed",
            3: "failed",
            4: "failed",
        }.get(self.action)
        result["interpretation"] = "reference_fork"
        return result

    def _lock_pro_labels(self, result: dict[str, Any]) -> dict[str, Any]:
        """Interpret only combinations correlated with the supplied app history.

        Legacy source/action field names remain for response compatibility;
        they are raw bytes, not independent enums on this model.
        """
        fingerprint = self.user_id is not None and self.action == 15
        key = (self.source, self.action, self.value)
        labels = {
            (1, 1, 0): ("lock", "app"),
            (2, 15, 0): ("unlock", "fingerprint"),
            (1, 15, 64): ("unlatch", "fingerprint"),
        }
        known = key in labels and (self.action != 15 or fingerprint)
        action, source = (
            labels[key]
            if known
            else (
                f"unknown_event_{self.source}_{self.action}_{self.value}",
                f"unknown_{self.action}",
            )
        )
        result.update(
            action_name=action,
            source_name=source,
            result="success" if known else None,
            interpretation="lock_pro_app_correlated"
            if known
            else "lock_pro_unverified",
            user_id_interpretation="observed_keypad_credential"
            if fingerprint
            else None,
        )
        return result


ACTION_NAMES = {
    0: "lock",
    1: "unlock",
    2: "jammed",
    3: "unlock_failed",
    4: "lock_failed",
}
SOURCE_NAMES = {
    0: "app",
    1: "keypad",
    2: "manual",
    3: "auto_lock",
    4: "nfc",
    5: "remote",
    6: "fingerprint",
}
