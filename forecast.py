"""
Forecast-profile engine.

Turns parsed statement transactions into the compact profile Payday Runway's
forecast needs: the paycheck pattern, recurring bills, current balance and
average daily spend. Pure functions — no I/O.
"""
import re
from datetime import date, datetime, timedelta
from statistics import median, pstdev


def _normalize(desc: str) -> str:
    s = desc.lower()
    # Strip per-occurrence fragments so recurring items group together:
    # "authorized on 06/05", "on 06/10", "Ref # Wfct1294B388".
    s = re.sub(r"ref #\s*\w+", " ", s)
    s = re.sub(r"authorized on \d{1,2}/\d{1,2}", " ", s)
    s = re.sub(r"\bon \d{1,2}/\d{1,2}\b", " ", s)
    s = re.sub(r"\d{4,}", " ", s)          # order / auth / trace numbers
    s = re.sub(r"\bx{2,}\b", " ", s)       # redaction noise: xxxxx, xxxxxx
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _interval_days(dates: list[date]) -> list[int]:
    return [(b - a).days for a, b in zip(sorted(dates), sorted(dates)[1:])]


def _cycle_label(med: float) -> str | None:
    if 6 <= med <= 8:
        return "weekly"
    if 13 <= med <= 16:
        return "biweekly"
    if 27 <= med <= 32:
        return "monthly"
    return None


def _confidence(n: int, ivls: list[int]) -> float:
    base = min(0.55 + 0.1 * n, 0.92)
    if len(ivls) >= 2 and pstdev(ivls) > 4:
        base -= 0.15
    return round(max(0.4, min(0.95, base)), 2)


def _recurring_groups(transactions: list[dict], kind: str) -> list[dict]:
    groups: dict[str, list[dict]] = {}
    for t in transactions:
        if t["kind"] != kind:
            continue
        groups.setdefault(_normalize(t["description"]), []).append(t)
    out = []
    for norm, txns in groups.items():
        if len(txns) < 2:
            continue
        dates = sorted(datetime.strptime(t["date"], "%Y-%m-%d").date() for t in txns)
        ivls = _interval_days(dates)
        if not ivls:
            continue
        med = median(ivls)
        label = _cycle_label(med)
        if label is None:
            continue
        amounts = [t["amount"] for t in txns]
        out.append(
            {
                # Cleaned display name (no auth/trace numbers, no "Ref #…").
                "name": norm.title(),
                "norm": norm,
                "amount": round(median(amounts), 2),
                "cycle": label,
                "interval_days": int(round(med)),
                "occurrences": len(txns),
                "last_date": max(dates),
                "confidence": _confidence(len(txns), ivls),
                "total": sum(amounts),
            }
        )
    return out


def build_profile(transactions: list[dict], ending_balance: float | None) -> dict:
    warnings: list[str] = []
    today = date.today()

    if not transactions:
        return {
            "paycheck": None,
            "bills": [],
            "balance": ending_balance,
            "avg_daily_spend": 0.0,
            "warnings": ["No transactions could be read from the uploaded statements."],
        }

    dates = [datetime.strptime(t["date"], "%Y-%m-%d").date() for t in transactions]
    span_days = max(1, (max(dates) - min(dates)).days)
    if span_days < 45:
        warnings.append(
            f"Only {span_days} days of history — patterns are less reliable. "
            "Upload 2–3 months for a steadier forecast."
        )

    # Paycheck: biggest recurring credit, preferring biweekly/monthly.
    # Internal transfers (savings hops, brokerage moves) are never paychecks.
    credits = [
        g for g in _recurring_groups(transactions, "credit") if "transfer" not in g["norm"]
    ]
    paycheck = None
    if credits:
        ranked = sorted(
            credits,
            key=lambda g: (g["cycle"] in ("biweekly", "monthly"), g["total"]),
            reverse=True,
        )
        best = ranked[0]
        nxt = best["last_date"] + timedelta(days=best["interval_days"])
        paycheck = {
            "amount": best["amount"],
            "cycle": best["cycle"],
            "next_date": nxt.strftime("%Y-%m-%d"),
            "confidence": best["confidence"],
            "source": best["name"],
        }
    else:
        warnings.append("No repeating paycheck pattern found — you can enter it manually.")

    # Recurring bills: recurring debits with a predicted next due date.
    # Internal transfers are not bills.
    bills = []
    for g in _recurring_groups(transactions, "debit"):
        if "transfer" in g["norm"]:
            continue
        nxt = g["last_date"] + timedelta(days=g["interval_days"])
        bills.append(
            {
                "name": g["name"],
                "amount": g["amount"],
                "cycle": g["cycle"],
                "next_due": nxt.strftime("%Y-%m-%d"),
                "confidence": g["confidence"],
            }
        )
    bills.sort(key=lambda b: b["next_due"])
    if not bills:
        warnings.append("No recurring bills detected — add any manually.")

    debits = [
        t["amount"]
        for t in transactions
        if t["kind"] == "debit" and "transfer" not in _normalize(t["description"])
    ]
    avg_daily_spend = round(sum(debits) / span_days, 2) if debits else 0.0

    return {
        "paycheck": paycheck,
        "bills": bills,
        "balance": ending_balance,
        "avg_daily_spend": avg_daily_spend,
        "warnings": warnings,
    }
