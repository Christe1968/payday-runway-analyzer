"""
Wells Fargo statement parser (v1).

Extracts transactions and the ending balance from Wells Fargo checking/savings
statement PDFs. Written defensively: statement layouts drift, so several line
patterns are tried and anything unrecognized is skipped rather than guessed.

Only Wells Fargo is supported in v1 — same scope as MyMoneyCycle today.
"""
import re
from datetime import datetime

DATE_RE = r"(\d{1,2}/\d{1,2}(?:/\d{2,4})?)"
AMT_RE = r"(-?\$?\(?[\d,]+\.\d{2}\)?)"

# A transaction line looks like:  10/01/26  SOME DESCRIPTION  1,840.00
LINE_PATTERNS = [
    re.compile(rf"^{DATE_RE}\s+(.+?)\s+{AMT_RE}\s*$"),
    # Description-first variant seen in some exports
    re.compile(rf"^(.+?)\s+{DATE_RE}\s+{AMT_RE}\s*$"),
]

DEPOSIT_HDR = re.compile(r"deposits?\s*(/|and)?\s*additions?", re.I)
WITHDRAW_HDR = re.compile(r"withdrawals?\s*(/|and)?\s*subtractions?|deductions", re.I)
BALANCE_RE = re.compile(r"ending balance\s+\$?([\d,]+\.\d{2})", re.I)
PERIOD_RE = re.compile(
    r"statement period:?\s*(\d{1,2}/\d{1,2}/\d{2,4})\s*(?:to|-|through)\s*(\d{1,2}/\d{1,2}/\d{2,4})",
    re.I,
)
# New-format section headers inside transaction history
TX_HISTORY_HDR = re.compile(r"transaction history", re.I)


def _parse_amount(raw: str) -> float | None:
    s = raw.strip().replace("$", "").replace(",", "")
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    if s.startswith("-"):
        neg = True
        s = s[1:]
    try:
        v = float(s)
    except ValueError:
        return None
    return -v if neg else v


def _parse_date(raw: str, hint_year: int | None) -> str | None:
    parts = raw.split("/")
    try:
        m, d = int(parts[0]), int(parts[1])
        y = int(parts[2]) if len(parts) > 2 else hint_year
        if y is None:
            return None
        if y < 100:
            y += 2000
        return datetime(y, m, d).strftime("%Y-%m-%d")
    except (ValueError, IndexError):
        return None


def extract_text(pdf_path: str) -> str:
    import pdfplumber

    chunks = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            chunks.append(page.extract_text() or "")
    return "\n".join(chunks)


def parse_statement(text: str) -> dict:
    """Returns {transactions, ending_balance, period_start, period_end}."""
    # Year hint from statement period, else current year.
    hint_year = datetime.now().year
    period_start = period_end = None
    m = PERIOD_RE.search(text)
    if m:
        try:
            period_start = datetime.strptime(m.group(1), "%m/%d/%Y").strftime("%Y-%m-%d")
            period_end = datetime.strptime(m.group(2), "%m/%d/%Y").strftime("%Y-%m-%d")
            hint_year = int(period_end[:4])
        except ValueError:
            pass

    ending_balance = None
    m = BALANCE_RE.search(text)
    if m:
        ending_balance = _parse_amount(m.group(1))

    transactions = []
    section = None  # "credit" | "debit" | None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if DEPOSIT_HDR.search(line):
            section = "credit"
            continue
        if WITHDRAW_HDR.search(line):
            section = "debit"
            continue
        if TX_HISTORY_HDR.search(line):
            continue
        for pat in LINE_PATTERNS:
            mt = pat.match(line)
            if not mt:
                continue
            # Pattern 1: (date, desc, amt); pattern 2: (desc, date, amt)
            if pat is LINE_PATTERNS[0]:
                date_raw, desc, amt_raw = mt.group(1), mt.group(2), mt.group(3)
            else:
                desc, date_raw, amt_raw = mt.group(1), mt.group(2), mt.group(3)
            date = _parse_date(date_raw, hint_year)
            amount = _parse_amount(amt_raw)
            if date is None or amount is None:
                break
            desc = re.sub(r"\s+", " ", desc).strip()
            if len(desc) < 3:
                break
            kind = section or ("credit" if amount > 0 else "debit")
            transactions.append(
                {"date": date, "description": desc, "amount": abs(amount), "kind": kind}
            )
            break

    # Dedupe exact repeats (multi-page headers can re-print lines).
    seen, uniq = set(), []
    for t in transactions:
        key = (t["date"], t["description"], t["amount"], t["kind"])
        if key not in seen:
            seen.add(key)
            uniq.append(t)

    return {
        "transactions": sorted(uniq, key=lambda t: t["date"]),
        "ending_balance": ending_balance,
        "period_start": period_start,
        "period_end": period_end,
    }
