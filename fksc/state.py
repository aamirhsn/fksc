"""Persistent daily counters so the spend caps survive restarts."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path


class DailyState:
    def __init__(self, path: Path):
        self.path = path
        self.data = {"date": "", "orders": 0, "spend": 0.0, "seen": []}
        if path.exists():
            try:
                self.data.update(json.loads(path.read_text()))
            except Exception:
                pass
        self._roll()

    def _roll(self):
        today = dt.date.today().isoformat()
        if self.data.get("date") != today:
            self.data.update(date=today, orders=0, spend=0.0)

    def save(self):
        self.data["seen"] = self.data["seen"][-2000:]
        self.path.write_text(json.dumps(self.data, indent=2))

    def seen(self, key: str) -> bool:
        return key in self.data["seen"]

    def mark_seen(self, key: str):
        self.data["seen"].append(key)
        self.save()

    def can_spend(self, amount: float, max_orders: int, max_spend: float) -> tuple[bool, str]:
        self._roll()
        if max_orders and self.data["orders"] >= max_orders:
            return False, f"daily order cap reached ({max_orders})"
        if max_spend and self.data["spend"] + amount > max_spend:
            return False, f"daily spend cap would be exceeded ({self.data['spend']}+{amount} > {max_spend})"
        return True, "ok"

    def record_order(self, amount: float):
        self._roll()
        self.data["orders"] += 1
        self.data["spend"] += amount
        self.save()
