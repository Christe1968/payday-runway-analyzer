# Payday Runway — statement analyzer API

Turns uploaded bank statements into the **forecast profile** the Payday Runway
app needs: paycheck pattern, recurring bills, balance, average daily spend.

Wells Fargo statements only in v1 (same scope as MyMoneyCycle today).

## Endpoints

- `GET /health` → `{"ok": true, ...}`
- `POST /api/analyze-statements` — multipart form, PDFs under field
  `statements`. Returns:

```json
{
  "paycheck": { "amount": 1840.0, "cycle": "biweekly",
                "next_date": "2026-10-10", "confidence": 0.85,
                "source": "Direct Deposit - Acme Corp" },
  "bills": [ { "name": "Electric", "amount": 92.0, "cycle": "monthly",
               "next_due": "2026-10-08", "confidence": 0.9 } ],
  "balance": 2310.40,
  "avg_daily_spend": 48.0,
  "warnings": [],
  "transactions_read": 214
}
```

## Privacy

Uploads land in a temp dir, are parsed, and are **deleted immediately** in a
`finally` block. Nothing is stored or logged.

## Deploy (Render)

1. Push this folder to a Git repo.
2. Render → New → Web Service → point at the repo (it picks up `render.yaml`).
3. Note the URL, e.g. `https://payday-runway-analyzer.onrender.com`.
4. In the Payday Runway prototype, set `API_BASE` to that URL — the upload
   flow switches from mock data to the real engine automatically.

## Local dev

```
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python app.py
```
