# currency-bot

A Telegram bot that converts Ukrainian hryvnia (UAH) into six popular
currencies using live rates from the public Monobank API.

Interface language: Ukrainian. Code and documentation: English.

## Quick start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Create a bot and get its token

1. Open Telegram and find [@BotFather](https://t.me/BotFather)
2. Send `/newbot` and follow the prompts
3. Copy the token it gives you

### 3. Pass the token as an environment variable

Linux / macOS:

```bash
export BOT_TOKEN='your_token_here'
```

Windows (PowerShell):

```powershell
$env:BOT_TOKEN='your_token_here'
```

The token is never stored in the source code. If the variable is missing,
the bot refuses to start and says so explicitly.

### 4. Run

```bash
python bot.py
```

## Features

| Control | Action |
|---|---|
| `/start`, `/help` | Greeting and usage instructions |
| 💱 Конвертувати | Start a conversion |
| 📋 Історія | Last 10 conversions in this chat |
| ℹ️ Допомога | Usage instructions |

**Supported targets:** USD, EUR, GBP, CHF, PLN, CZK
**Source currency:** always UAH

## How it works

1. `fetch_rates()` downloads the Monobank rate table. Monobank rejects
   requests made more often than once per minute, so the table is cached for
   60 seconds and reused; if a request fails, the last known table is served
   instead of an error.
2. `get_rate()` finds the pair in the table. Monobank publishes `rateBuy`
   and `rateSell` only for the most traded currencies — the rest carry
   `rateCross` alone, so every branch falls back to the cross rate.
3. `convert_amount()` applies the rate, routing through UAH when no direct
   pair exists.
4. The result is saved to `history.json` under the chat ID and echoed back
   to the user.

## Files

```
bot.py            — the bot
requirements.txt  — pinned dependencies
history.json      — created on first conversion (git-ignored)
```

`history.json` is keyed by chat ID, so each user only ever sees their own
conversions:

```json
{
  "123456789": [
    {
      "amount": 1000,
      "from": "UAH",
      "to": "USD",
      "result": 24.1543,
      "date": "05.06.2026 14:30"
    }
  ]
}
```

## Tech

- Python 3.9+
- [pyTelegramBotAPI](https://github.com/eternnoir/pyTelegramBotAPI) 4.21 — Telegram Bot API
- [requests](https://requests.readthedocs.io) 2.32 — HTTP client
- [Monobank API](https://api.monobank.ua/docs/) — `GET /bank/currency`

## Known limitations

- Conversation state is kept in memory, so an in-progress conversion is lost
  if the process restarts. Completed conversions survive in `history.json`.
- Rates come from a single source and reflect Monobank's card rates, not the
  interbank market.
