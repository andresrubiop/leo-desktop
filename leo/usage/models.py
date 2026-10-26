"""Modelo de cuotas heredado de AI Usage Widget (MIT)."""
from dataclasses import dataclass


@dataclass
class Window:
    label: str
    used: float
    resets_at: float | None = None
    used_usd: float | None = None
    limit_usd: float | None = None
