"""
Wells Fargo statement parser (v2).

Real WF checking statements use ONE transaction table with separate columns:

    Date | Number | Description | Deposits/Additions | Withdrawals/Subtractions | Ending daily balance

Plain-text extraction loses the columns, so each transaction is classified by
the *horizontal position* of its amount (pdfplumber word coordinates) relative
to the Additions/Subtractions column headers. The trailing "ending daily
balance" — printed on the last transaction of each day — is dropped, because
the naive "last number on the line" approach mistakes it for the amount.

Only Wells Fargo is supported in v1 — same scope as MyMoneyCycle today.
"""
import re
from datetime import datetime

AMT_WORD = re.compile(r"^\(?\$?[\d,]+\.\d{2}\)?$")
DATE_WORD = re.compile(r"^(\d{1,2})/(\d{1,2})(?:/\d{2,4})?$")
STMT_DATE_RE = re.compile(r"([A-Za-z]+ \d{1,2}, \d{4})\s+Page \d+ of \d+")
BALANCE_RE = re.compile(r"ending balance(?: on \d{1,2}/\d{1,2})?\s+\$?([\d,]+\.\d{2})", re.I)
BEGIN_RE = re.compile(r"beginning balance on (\d{1,2})/(\d{1,2})", re.I)
END_RE = re.compile(r"ending balance on (\d{1,2})/(\d{1,2})", re.I)
PERIOD_RE = re.compile(
    r"statement period:?\s*(\d{1,2}/\d{1,2}/\d{2,4})\s*(?:to|-|through)\s*(\d{1,2}/\d{1,2}/\d{2,4})",
    re.I,
)

CREDIT_HINTS = ("direct deposit", "zelle from", "transfer from")
DEBIT_HINTS = ("zelle to", "transfer to")


def _positioned_lines(pdf_path: str) -> list[list[dict]]:
    """PDF -> pages -> lines -> pdfplumber word dicts (with x0/x1/top)."""
    import pdfplumber

    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            words = page.extract_words(x_tolerance=2, y_tolerance=3)
            lines, cur, cur_top = [], [], None
            for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
                if cur_top is None or abs(w["top"] - cur_top) <= 3:
                    cur.append(w)
                    if cur_top is None:
                        cur_top = w["top"]
                else:
                    lines.append(cur)
                    cur, cur_top = [w], w["top"]
            if cur:
                lines.append(cur)
            pages.append(lines)
    return pages


def _line_text(words: list[dict]) -> str:
    return " ".join(w["text"] for w in words)


def _parse_amount_word(tok: str) -> float | None:
    s = tok.replace("$", "").replace(",", "")
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    try:
        v = float(s)
    except ValueError:
        return None
    return -v if neg else v


def _classify(amt_word: dict, cx_add, cx_sub, desc: str) -> str:
    """Column position first; keyword hints only if headers weren't found."""
    if cx_add is not None and cx_sub is not None:
        cx = (amt_word["x0"] + amt_word["x1"]) / 2
        return "credit" if abs(cx - cx_add) < abs(cx - cx_sub) else "debit"
    d = desc.lower()
    if any(h in d for h in CREDIT_HINTS):
        return "credit"
    if any(h in d for h in DEBIT_HINTS):
        return "debit"
    return "debit"


def parse_pdf(pdf_path: str) -> dict:
    """Returns {transactions, ending_balance, period_start, period_end}."""
    pages = _positioned_lines(pdf_path)
    full_text = "\n".join(_line_text(w) for lines in pages for w in lines)

    # Statement year from "July 7, 2026 Page 1 of 7".
    year = datetime.now().year
    m = STMT_DATE_RE.search(full_text)
    if m:
        try:
            year = datetime.strptime(m.group(1), "%B %d, %Y").year
        except ValueError:
            pass

    # Column anchors from the "Date Number Description Additions Subtractions balance" header.
    cx_add = cx_sub = None
    for lines in pages:
        for words in lines:
            wl = [w["text"].lower() for w in words]
            if "additions" in wl and "subtractions" in wl:
                wa = next(w for w in words if w["text"].lower() == "additions")
                ws = next(w for w in words if w["text"].lower() == "subtractions")
                cx_add = (wa["x0"] + wa["x1"]) / 2
                cx_sub = (ws["x0"] + ws["x1"]) / 2
                break
        if cx_add is not None:
            break

    transactions = []
    for lines in pages:
        for words in lines:
            if not words:
                continue
            dm = DATE_WORD.match(words[0]["text"])
            if not dm:
                continue
            month, day = int(dm.group(1)), int(dm.group(2))
            # Trailing run of amount-like words: last = daily balance (drop),
            # second-to-last = the transaction amount.
            run = []
            idx = len(words) - 1
            while idx > 0 and AMT_WORD.match(words[idx]["text"]):
                run.append(idx)
                idx -= 1
            if not run:
                continue
            amt_idx = run[1] if len(run) >= 2 else run[0]
            amount = _parse_amount_word(words[amt_idx]["text"])
            if amount is None:
                continue
            desc = re.sub(r"\s+", " ", _line_text(words[1:amt_idx])).strip()
            if len(desc) < 3:
                continue
            y = year
            if len(dm.group(0).split("/")) == 3:  # date carried its own year
                try:
                    y = int(dm.group(0).split("/")[2])
                    if y < 100:
                        y += 2000
                except ValueError:
                    pass
            try:
                iso = datetime(y, month, day).strftime("%Y-%m-%d")
            except ValueError:
                continue
            kind = _classify(words[amt_idx], cx_add, cx_sub, desc)
            transactions.append(
                {"date": iso, "description": desc, "amount": abs(amount), "kind": kind}
            )

    # Dedupe exact repeats.
    seen, uniq = set(), []
    for t in transactions:
        key = (t["date"], t["description"], t["amount"], t["kind"])
        if key not in seen:
            seen.add(key)
            uniq.append(t)

    ending_balance = None
    m = BALANCE_RE.search(full_text)
    if m:
        ending_balance = _parse_amount_word(m.group(1))

    period_start = period_end = None
    mb, me = BEGIN_RE.search(full_text), END_RE.search(full_text)
    if mb and me:
        try:
            y2 = year + 1 if int(me.group(1)) < int(mb.group(1)) else year
            period_start = datetime(year, int(mb.group(1)), int(mb.group(2))).strftime("%Y-%m-%d")
            period_end = datetime(y2, int(me.group(1)), int(me.group(2))).strftime("%Y-%m-%d")
        except ValueError:
            pass
    else:
        m = PERIOD_RE.search(full_text)
        if m:
            try:
                period_start = datetime.strptime(m.group(1), "%m/%d/%Y").strftime("%Y-%m-%d")
                period_end = datetime.strptime(m.group(2), "%m/%d/%Y").strftime("%Y-%m-%d")
            except ValueError:
                pass

    return {
        "transactions": sorted(uniq, key=lambda t: t["date"]),
        "ending_balance": ending_balance,
        "period_start": period_start,
        "period_end": period_end,
    }
