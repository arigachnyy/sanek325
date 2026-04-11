# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Playtomic Court Booker** (`book_court.py`) — Monitors Playtomic court availability at Padel Mate Club NTC and notifies via Telegram with a payment link the moment a slot becomes bookable. Reads desired slots from `bookings.json` and tracks state in `notified.json`. Designed to run via cron every minute.

## Running

```bash
source .venv/bin/activate
python book_court.py
```

Crontab (every minute):

```cron
* * * * * cd /path/to/playtomic-booker && .venv/bin/python book_court.py >> /tmp/book-court.log 2>&1
```

## Dependencies

```bash
pip install -r requirements.txt
```

## Architecture

Uses the Playtomic consumer API (`api.playtomic.io`). No Google OAuth — authenticates with Playtomic email/password.

**Booking window**: Courts open exactly 14 days before. The script categorises future slots into:
- **actionable** — booking window is opening within 3 minutes; polls Playtomic every 5s for up to 6 minutes, creates a payment intent on success, and sends a Telegram message with a direct payment link.
- **early_check** — past the booking window opening; tries a single check in case a court is already available (e.g. cancellation).

**Config**: `bookings.json` lists desired slots (date, time in local HH:MM:SS, duration in minutes).

**Deduplication**: `notified.json` tracks completed slots as `{key: "ok" | "fail"}`. Past dates and already-processed slots are skipped automatically.

**Concurrency**: A flock-based lock (`.book_court.lock`) prevents overlapping cron invocations.

**Tenant**: Padel Mate Club NTC (De Kegel), Amstelveen. Tenant ID hardcoded. Has 5 double + 2 single courts; only double courts are considered, in numeric order (1 preferred).

**Local module**: `telegram_sender.py` provides `send` (Markdown) and `send_html` (HTML) helpers.

## Required Environment Variables

Defined in `.env` (loaded via `python-dotenv`):
- `PLAYTOMIC_EMAIL` — Playtomic account email
- `PLAYTOMIC_PASSWORD` — Playtomic account password
- `TELEGRAM_BOT_TOKEN` — Telegram bot token from BotFather
- `TELEGRAM_CHAT_ID` — Target Telegram chat ID
