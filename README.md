# sanek325

Personal automation scripts running via cron on a Hetzner VM.

## Projects

### [gmail-digest](gmail-digest/)
Fetches new emails via the Gmail API, summarizes them with Claude, and sends a digest to Telegram. Intended for periodic cron runs.

- Entry point: `main.py`
- OAuth setup: `python setup_oauth.py` (one-time)
- Requires `.env` with `ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.

### [playtomic-booker](playtomic-booker/)
Monitors Playtomic court availability at Padel Mate Club NTC and sends a Telegram message with a payment link the moment a desired slot becomes bookable. Courts open exactly 14 days in advance; the script polls the booking window opening and creates a payment intent on success.

- Entry point: `book_court.py`
- Desired slots: `bookings.json`
- State: `notified.json` (deduplication), `.book_court.lock` (flock)
- Requires `.env` with `PLAYTOMIC_EMAIL`, `PLAYTOMIC_PASSWORD`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.

### [playtomic-calendar](playtomic-calendar/)
Scans unread Playtomic and Padel Mate Academy emails, extracts match/class details (ICS attachments, email bodies, or PDF invoices via `pdftotext`), creates Google Calendar events, and marks the emails as read.

- Entry point: `playtomic_to_calendar.py`
- Requires `poppler-utils` (`pdftotext`) on the host.

### [telegram-bot](telegram-bot/)
Long-running interactive Telegram bot. Runs as a systemd service and dispatches commands to per-project handler modules — each subproject under this repo can contribute its own submenu by exporting `MENU_BUTTON` and `register(app, chat_filter)` from a `bot_handlers.py`.

- Entry point: `bot.py` (systemd unit: `sanek325-telegram-bot.service`)
- Access-gated to `TELEGRAM_CHAT_ID` from `playtomic-booker/.env`
- Currently registered modules:
  - `playtomic-booker` — "🎾 Playtomic" submenu: list / add / remove entries in `bookings.json`

To add a new section, drop a `bot_handlers.py` into the target project and append its directory name to `REGISTERED_MODULES` in `telegram-bot/bot.py`.

Service management:

```bash
systemctl status sanek325-telegram-bot
systemctl restart sanek325-telegram-bot
journalctl -u sanek325-telegram-bot -f
```

## Crontab

Jobs run through [`bin/cron_notify.sh`](bin/cron_notify.sh), a wrapper that sends a Telegram alert on non-zero exit (creds are read from `playtomic-booker/.env`):

```cron
*/10 * * * * /root/workspace/sanek325/bin/cron_notify.sh playtomic-calendar .venv/bin/python playtomic_to_calendar.py >> /tmp/playtomic-calendar.log 2>&1
*    * * * * /root/workspace/sanek325/bin/cron_notify.sh playtomic-booker   .venv/bin/python book_court.py            >> /tmp/book-court.log        2>&1
```

## Re-authorizing Google OAuth (headless VM)

Google expires refresh tokens every 7 days while the OAuth consent screen is in **Testing** mode. Publish the consent screen to **In production** in Google Cloud Console to stop this. If a token still gets revoked, each Google-using project ships a `reauth.sh` that runs the OAuth flow over an SSH port-forward:

```bash
# From your laptop:
ssh -L 8080:localhost:8080 root@<hetzner-ip>

# Then on the VM:
cd /root/workspace/sanek325/playtomic-calendar && ./reauth.sh   # or gmail-digest/reauth.sh
```

The script prints an authorization URL — open it in your laptop browser, approve, and Google redirects to `localhost:8080` which tunnels back to the VM's OAuth flow server.

## Per-project setup

Each project has its own `.venv` and `requirements.txt`:

```bash
cd <project>
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Secrets (`.env`, `credentials.json`, `token*.json`) and runtime state are gitignored — see `.gitignore`.
