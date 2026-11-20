"""PF, ESI, Professional tax and TDS on salary (India) — the working for every salary payment.

All rates live in ``DEFAULTS`` and can be changed per firm (``hop_salary_settings``) — the CA can adjust them.
Owner 20-11: "pf, esi ki working, tax ki working".

* **PF (EPF)**: employee 12% and employer 12% of basic. By default only basic up to the wage ceiling (₹15,000)
  counts; ``pf_full_basic`` = on uses the whole basic. Employer also pays EDLI 0.5% + admin 0.5% (on the same wage).
  Basic = ``basic_pct`` % of the month's earned pay (per staff member, default 100%).
* **ESI**: employee 0.75%, employer 3.25% of gross, while the staff member's monthly gross is up to ₹21,000.
* **Professional tax**: a fixed amount a month (firm default, per staff override; 0 where the state has none).
* **TDS on salary (Sec 192), new regime**: yearly salary − standard deduction ₹75,000 → slabs; nil tax up to ₹12 lakh
  taxable (rebate); + 4% cess; spread over 12 months; ``tds_extra`` adds more per month.
Employee shares (PF, ESI, PT, TDS) are cut from the salary; employer shares (PF, ESI) are the firm's own cost.
Both are paid to the government later — "Pay dues" saves them as expenses, so P&L = gross salary + employer share.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

# One place for the government rates: change them here (server) and every firm gets them at once — no app update.
RATES_AS_OF = "2026-04-01"   # FY 2026-27: new-regime slabs, ₹75,000 standard deduction, ₹12 lakh rebate; EPF 12%; ESI 0.75 / 3.25%
DEFAULTS: dict[str, Any] = {
    "pf_enabled": False,          # firm registered with EPFO
    "esi_enabled": False,         # firm registered with ESIC
    "pf_emp_pct": 12.0, "pf_er_pct": 12.0, "pf_er_extra_pct": 1.0,   # EDLI 0.5 + admin 0.5
    "pf_wage_ceiling": 15000.0, "pf_full_basic": False,
    "esi_emp_pct": 0.75, "esi_er_pct": 3.25, "esi_gross_limit": 21000.0,
    "pt_default": 0.0,            # professional tax per month (state rule; 0 = none)
    "tds_enabled": True,
    "std_deduction": 75000.0, "rebate_upto": 1200000.0, "cess_pct": 4.0,
    # new regime slabs: (upper limit of the slab, rate %)
    "slabs": [[400000, 0], [800000, 5], [1200000, 10], [1600000, 15], [2000000, 20], [2400000, 25], [None, 30]],
}


def _f(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _r(v: float) -> float:
    return float(round(v))   # statutory amounts are rounded to the rupee


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS hop_salary_settings (workspace_id TEXT PRIMARY KEY, settings_json TEXT NOT NULL)")


def settings(conn: sqlite3.Connection, ws: str) -> dict[str, Any]:
    """The firm's switches (PF / ESI registered, PT amount, …) on top of the server's current government rates."""
    ensure_schema(conn)
    r = conn.execute("SELECT settings_json FROM hop_salary_settings WHERE workspace_id=?", (ws,)).fetchone()
    saved = json.loads(r[0]) if r else {}
    return {**DEFAULTS, **{k: v for k, v in saved.items() if k in FIRM_KEYS}, "rates_as_of": RATES_AS_OF}


# What a firm chooses. Government rates (PF %, ESI %, slabs, limits) are NOT saved per firm: they always come from
# the server, so a rate change reaches every firm at once.
FIRM_KEYS = ("pf_enabled", "esi_enabled", "pt_default", "pf_full_basic", "tds_enabled")


def save_settings(conn: sqlite3.Connection, ws: str, p: dict) -> dict[str, Any]:
    ensure_schema(conn)
    r = conn.execute("SELECT settings_json FROM hop_salary_settings WHERE workspace_id=?", (ws,)).fetchone()
    cur = {k: v for k, v in (json.loads(r[0]) if r else {}).items() if k in FIRM_KEYS}
    for k, v in (p or {}).items():
        if k not in FIRM_KEYS:
            continue
        if isinstance(DEFAULTS[k], bool):
            cur[k] = v in (True, 1, "1", "true", "on")
        elif k == "slabs":
            if isinstance(v, list) and all(isinstance(x, list) and len(x) == 2 for x in v):
                cur[k] = v
        else:
            cur[k] = max(0.0, _f(v))
    conn.execute("INSERT OR REPLACE INTO hop_salary_settings (workspace_id, settings_json) VALUES (?, ?)", (ws, json.dumps(cur)))
    conn.commit()
    return settings(conn, ws)


def yearly_tax(taxable: float, cfg: dict[str, Any]) -> float:
    """New-regime income tax on a year's taxable salary (after the standard deduction), with rebate and cess."""
    if taxable <= _f(cfg["rebate_upto"]):
        return 0.0
    tax, low = 0.0, 0.0
    for upper, rate in cfg["slabs"]:
        top = taxable if upper is None else min(taxable, float(upper))
        if top > low:
            tax += (top - low) * float(rate) / 100
        if upper is None or taxable <= float(upper):
            break
        low = float(upper)
    # marginal relief just above the rebate limit: tax can't be more than the income above the limit
    tax = min(tax, taxable - _f(cfg["rebate_upto"]))
    return tax * (1 + _f(cfg["cess_pct"]) / 100)


def working(staff: dict[str, Any], earned: float, bonus: float, cfg: dict[str, Any]) -> dict[str, Any]:
    """PF / ESI / PT / TDS for one month's pay of one staff member."""
    gross = max(0.0, earned + bonus)
    basic = earned * (_f(staff.get("basic_pct")) or 100.0) / 100
    out = {"gross": round(gross, 2), "basic": round(basic, 2), "pf_wage": 0.0, "pf_emp": 0.0, "pf_er": 0.0,
           "esi_emp": 0.0, "esi_er": 0.0, "pt": 0.0, "tds": 0.0, "notes": []}
    if cfg.get("pf_enabled") and staff.get("pf_on"):
        wage = basic if cfg.get("pf_full_basic") else min(basic, _f(cfg["pf_wage_ceiling"]))
        out["pf_wage"] = round(wage, 2)
        out["pf_emp"] = _r(wage * _f(cfg["pf_emp_pct"]) / 100)
        out["pf_er"] = _r(wage * (_f(cfg["pf_er_pct"]) + _f(cfg["pf_er_extra_pct"])) / 100)
    monthly_rate = _f(staff.get("salary")) * (26 if staff.get("pay_type") == "daily" else 1)
    if cfg.get("esi_enabled") and staff.get("esi_on", 1) and monthly_rate <= _f(cfg["esi_gross_limit"]):
        out["esi_emp"] = float(-(-gross * _f(cfg["esi_emp_pct"]) // 100))   # ESI rounds up to the next rupee
        out["esi_er"] = float(-(-gross * _f(cfg["esi_er_pct"]) // 100))
    elif cfg.get("esi_enabled") and staff.get("esi_on", 1):
        out["notes"].append(f"ESI does not apply — monthly pay is above ₹{_f(cfg['esi_gross_limit']):,.0f}")
    pt = staff.get("pt_amount")
    out["pt"] = _r(_f(pt) if pt not in (None, "") else _f(cfg["pt_default"]))
    if out["pt"] > gross:
        out["pt"] = 0.0
    if cfg.get("tds_enabled") and staff.get("tds_on", 1):
        yearly = monthly_rate * 12 - (out["pf_emp"] * 0)   # new regime: PF is not deducted from taxable income
        taxable = max(0.0, yearly - _f(cfg["std_deduction"]))
        out["tds"] = _r(yearly_tax(taxable, cfg) / 12) + _r(_f(staff.get("tds_extra")))
        out["yearly_taxable"] = round(taxable, 2)
    out["employee_total"] = round(out["pf_emp"] + out["esi_emp"] + out["pt"] + out["tds"], 2)
    out["employer_total"] = round(out["pf_er"] + out["esi_er"], 2)
    return out
