"""Shared data model for usage limits."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class Window:
    """One rate-limit window, e.g. the 5-hour session or a weekly cap."""
    label: str                     # "5h", "7d", "7d opus", "Gemini 3 Flash"…
    used: float                    # percent used, 0–100
    resets_at: float | None = None  # epoch seconds
    used_usd: float | None = None   # dollar-based allowances (credits) only
    limit_usd: float | None = None

    @property
    def is_credit(self) -> bool:
        return self.limit_usd is not None

    def current_used(self, now: float) -> float:
        """Usage taking into account that the window may have reset already."""
        if self.resets_at and self.resets_at <= now:
            return 0.0
        return max(0.0, min(100.0, self.used))


@dataclass
class Account:
    provider: str
    name: str
    path: str = ""
    email: str = ""
    plan: str = ""
    auth: str = "ok"              # ok | nologin | expired | error
    windows: list[Window] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)   # already-translated hints
    source: str = "none"          # live | cache | local | none
    fetched_at: float | None = None
    error: str = ""
    rate_limited: bool = False
    key: str = ""
    command: str = ""             # what to type to use this account ("" if there is none)
    balances: list = field(default_factory=list)  # [(label, "¥12.30 CNY")] for API-key accounts

    def is_limited(self, now: float) -> bool:
        """A plan window is full (a spent credit is not a rate limit)."""
        return any(w.current_used(now) >= 100 for w in self.windows if not w.is_credit)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Account:
        data = dict(data)
        data["windows"] = [Window(**w) for w in data.get("windows", [])]
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in known})


def window_label(seconds: float | None) -> str:
    """18000 → '5h', 604800 → '7d', 2592000 → '30d'."""
    if not seconds:
        return "?"
    seconds = int(seconds)
    if seconds % 86400 == 0:
        return f"{seconds // 86400}d"
    if seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    return f"{max(1, seconds // 60)}m"
