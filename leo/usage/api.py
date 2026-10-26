"""Adaptadores de saldo/cuota de AI Usage Widget, MIT; sin búsqueda de secretos."""
from __future__ import annotations

import os
import re
import math
from dataclasses import dataclass, field
from pathlib import Path

from .net import HttpError, http_json, now
from .models import Window

CURRENCY = {"CNY": "¥", "USD": "$", "EUR": "€"}


@dataclass(frozen=True)
class ApiProvider:
    id: str
    title: str
    env: tuple[str, ...]                 # variables we import keys from
    regions: tuple[tuple[str, str], ...]  # (region id, base URL); the first is the default
    console: str                          # where the user manages the account


@dataclass
class ApiKey:
    provider: str
    key: str
    region: str = ""        # "" = unknown: try each region
    source: str = ""        # keyring | env | file path
    id: str = ""

    @property
    def masked(self) -> str:
        return "…" + self.key[-4:] if len(self.key) > 8 else "…"

    @property
    def account_key(self) -> str:
        return f"api:{self.provider}:{self.id}"


@dataclass
class Result:
    windows: list[Window] = field(default_factory=list)
    balances: list[tuple[str, str]] = field(default_factory=list)
    plan: str = ""
    notes: list[str] = field(default_factory=list)
    region: str = ""


PROVIDERS = {
    "deepseek": ApiProvider("deepseek", "DeepSeek", ("DEEPSEEK_API_KEY",),
                            (("global", "https://api.deepseek.com"),), "https://platform.deepseek.com"),
    "kimi": ApiProvider("kimi", "Kimi (Moonshot)", ("MOONSHOT_API_KEY", "KIMI_API_KEY"),
                        (("global", "https://api.moonshot.ai"), ("cn", "https://api.moonshot.cn")),
                        "https://platform.moonshot.ai"),
    "zai": ApiProvider("zai", "Z.ai · GLM", ("ZAI_API_KEY", "ZHIPUAI_API_KEY", "ZHIPU_API_KEY", "GLM_API_KEY",
                                             "BIGMODEL_API_KEY"),
                       (("global", "https://api.z.ai"), ("cn", "https://open.bigmodel.cn")), "https://z.ai"),
    "minimax": ApiProvider("minimax", "MiniMax", ("MINIMAX_API_KEY",),
                           (("global", "https://api.minimax.io"), ("cn", "https://api.minimaxi.com")),
                           "https://platform.minimax.io"),
    "openrouter": ApiProvider("openrouter", "OpenRouter", ("OPENROUTER_API_KEY",),
                              (("global", "https://openrouter.ai"),), "https://openrouter.ai/settings/credits"),
    "siliconflow": ApiProvider("siliconflow", "SiliconFlow", ("SILICONFLOW_API_KEY",),
                               (("cn", "https://api.siliconflow.cn"), ("global", "https://api.siliconflow.com")),
                               "https://cloud.siliconflow.cn"),
}
CN_VARS = {"ZHIPUAI_API_KEY", "ZHIPU_API_KEY", "BIGMODEL_API_KEY"}  # these keys are for the China site


def money(amount, currency: str) -> str:
    try:
        value = float(amount)
    except (TypeError, ValueError):
        raise HttpError(None, "El proveedor no informó un saldo válido") from None
    if not math.isfinite(value):
        raise HttpError(None, "El proveedor no informó un saldo válido")
    symbol = CURRENCY.get(currency.upper(), "")
    return f"{symbol}{value:,.2f} {currency.upper()}".strip()


# ── fetching ──────────────────────────────────────────────────────────────────

def _bearer(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"}


def fetch_deepseek(base: str, key: str, tr) -> Result:
    data = http_json(f"{base}/user/balance", _bearer(key))
    res = Result()
    for info in data.get("balance_infos") or []:
        currency = info.get("currency", "")
        res.balances.append((tr("api_balance"), money(info.get("total_balance"), currency)))
        granted = float(info.get("granted_balance") or 0)
        if granted:
            res.notes.append(tr("api_granted", amount=money(granted, currency)))
    if data.get("is_available") is False:
        res.notes.append(tr("api_no_funds"))
    return res


def fetch_kimi(base: str, key: str, tr) -> Result:
    data = (http_json(f"{base}/v1/users/me/balance", _bearer(key)) or {}).get("data") or {}
    currency = "CNY" if base.endswith(".cn") else "USD"
    res = Result()
    res.balances.append((tr("api_balance"), money(data.get("available_balance"), currency)))
    voucher = float(data.get("voucher_balance") or 0)
    if voucher:
        res.notes.append(tr("api_voucher", amount=money(voucher, currency)))
    if float(data.get("available_balance") or 0) <= 0:
        res.notes.append(tr("api_no_funds"))
    return res


ZAI_UNITS = {3: "h", 4: "d", 5: "mo", 6: "w"}


def _zai_label(limit: dict) -> str:
    unit, number = limit.get("unit"), int(limit.get("number") or 1)
    if unit == 6:
        label = f"{7 * number}d"
    elif unit in ZAI_UNITS:
        label = f"{number}{ZAI_UNITS[unit]}"
    else:
        label = "?"
    return f"MCP {label}" if limit.get("type") == "TIME_LIMIT" else label


def fetch_zai(base: str, key: str, tr) -> Result:
    data = http_json(f"{base}/api/monitor/usage/quota/limit", {"Authorization": key})
    res = Result()
    if data.get("success") is False or data.get("code") not in (None, 0, 200):
        msg = str(data.get("msg") or "")
        if "coding plan" in msg.lower():
            res.notes.append(tr("api_no_plan"))  # a pay-as-you-go key: valid, but nothing to measure
            return res
        raise HttpError(None, "El proveedor no pudo consultar la cuota")
    body = data.get("data") or {}
    res.plan = str(body.get("level") or "").capitalize()
    for limit in body.get("limits") or []:
        if not isinstance(limit, dict) or limit.get("percentage") is None:
            continue
        reset = limit.get("nextResetTime")
        res.windows.append(Window(_zai_label(limit), float(limit["percentage"]),
                                  reset / 1000 if isinstance(reset, (int, float)) and reset else None))
    return res


def _ms_to_epoch(value) -> float | None:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value / 1000 if value > 1e11 else value


def fetch_minimax(base: str, key: str, tr) -> Result:
    data = http_json(f"{base}/v1/token_plan/remains", _bearer(key))
    base_resp = data.get("base_resp") or {}
    if base_resp.get("status_code") not in (None, 0):
        raise HttpError(None, "El proveedor no pudo consultar la cuota")
    body = data.get("data") if isinstance(data.get("data"), dict) else data
    models = body.get("model_remains") or []
    text_models = [m for m in models if "minimax-m" in str(m.get("model_name", "")).lower()]
    entry = (text_models or models or [{}])[0]
    res = Result()
    # MiniMax's "*_usage_count" fields hold what is LEFT, not what was used
    for label, total_k, left_k, end_k, remains_k in (
            ("5h", "current_interval_total_count", "current_interval_usage_count", "end_time", "remains_time"),
            ("7d", "current_weekly_total_count", "current_weekly_usage_count", "weekly_end_time",
             "weekly_remains_time")):
        total, left = entry.get(total_k), entry.get(left_k)
        if not total:
            continue
        if left is None:
            raise HttpError(None, "El proveedor no informó la cuota restante")
        used = max(0.0, min(100.0, 100.0 * (float(total) - float(left or 0)) / float(total)))
        reset = _ms_to_epoch(entry.get(end_k))
        if reset is None and entry.get(remains_k) is not None:
            reset = now() + float(entry[remains_k]) / 1000
        res.windows.append(Window(label, used, reset))
    return res


def fetch_openrouter(base: str, key: str, tr) -> Result:
    res = Result()
    try:
        data = (http_json(f"{base}/api/v1/credits", _bearer(key)) or {}).get("data") or {}
        if data.get("total_credits") is None or data.get("total_usage") is None:
            raise HttpError(None, "El proveedor no informó el saldo")
        left = float(data["total_credits"]) - float(data["total_usage"])
        res.balances.append((tr("api_balance"), money(left, "USD")))
        return res
    except HttpError as exc:
        if exc.status in (401, 403):
            raise
    data = (http_json(f"{base}/api/v1/key", _bearer(key)) or {}).get("data") or {}
    if data.get("limit"):
        used, limit = float(data.get("usage") or 0), float(data["limit"])
        res.windows.append(Window(tr("credit"), 100.0 * used / limit, None, used, limit))
    return res


def fetch_siliconflow(base: str, key: str, tr) -> Result:
    data = (http_json(f"{base}/v1/user/info", _bearer(key)) or {}).get("data") or {}
    currency = "CNY" if base.endswith(".cn") else "USD"
    res = Result()
    res.balances.append((tr("api_balance"), money(data.get("totalBalance", data.get("balance")), currency)))
    return res


FETCHERS = {"deepseek": fetch_deepseek, "kimi": fetch_kimi, "zai": fetch_zai, "minimax": fetch_minimax,
            "openrouter": fetch_openrouter, "siliconflow": fetch_siliconflow}


def query(provider, region, key):
    bases = dict(PROVIDERS[provider].regions)
    if region not in bases:
        raise ValueError("Selecciona una región válida")
    translations = {"api_balance": "Saldo", "credit": "Crédito", "api_no_funds": "Sin saldo disponible",
                    "api_granted": "Crédito de regalo: {amount}", "api_voucher": "Cupón: {amount}",
                    "api_no_plan": "Sin cuota de plan publicada"}
    def tr(name, **kwargs):
        return translations.get(name, name).format(**kwargs)
    return FETCHERS[provider](bases[region], key, tr)
