"""Per-run spend cap. A call is allowed only if spent + in-flight reservations + its worst-case
cost stay within the cap, so the cap is never exceeded even with concurrent workers."""

from __future__ import annotations

import threading


class Budget:
    def __init__(self, cap_usd: float) -> None:
        self.cap = cap_usd
        self.spent = 0.0
        self._reserved = 0.0
        self._lock = threading.Lock()
        self.exhausted = False

    def try_reserve(self, worst_case_usd: float) -> bool:
        with self._lock:
            if self.spent + self._reserved + worst_case_usd > self.cap:
                self.exhausted = True
                return False
            self._reserved += worst_case_usd
            return True

    def settle(self, reserved_usd: float, actual_usd: float) -> None:
        """Release a reservation and book the actual cost (0 when the call failed)."""
        with self._lock:
            self._reserved = max(0.0, self._reserved - reserved_usd)
            self.spent += actual_usd

    def add_spent(self, actual_usd: float) -> None:
        """Book spend that was not reserved (e.g. batch results already paid for)."""
        with self._lock:
            self.spent += actual_usd
