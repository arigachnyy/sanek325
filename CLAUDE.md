# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Layout

Personal automation scripts running via cron (and one long-running bot) on a Hetzner VM. Each subproject is independent — its own `.venv`, `requirements.txt`, and (where relevant) its own `.env` and OAuth tokens. Three of the four Python projects also have their own `CLAUDE.md` with project-specific architecture; read those when working inside a subproject.

- `gmail-digest/` — Gmail → Claude summary → Telegram. Cron.
- `playtomic-booker/` — Polls Playtomic for court availability, sends a Telegram payment link. Cron (every minute).
- `playtomic-calendar/` — Reads Playtomic / Padel Mate Academy emails → Google Calendar events. Cron.
- `telegram-bot/` — Long-running interactive Telegram bot, runs under systemd. Plugin host for per-project menus.
- `bin/cron_notify.sh` — Wrapper used by all cron entries; alerts Telegram on non-zero exit.

## Per-project setup

```bash
cd <project>
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

There is no repo-wide build/lint/test tooling — each project is run directly with its own venv's Python.

## Cross-cutting conventions

**Shared Telegram credentials.** `playtomic-booker/.env` is the canonical source of `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` for *every* script and the bot. `bin/cron_notify.sh` and `telegram-bot/bot.py` both `load_dotenv` (or `.`-source) that file directly. If you add a new script that needs to send Telegram messages, follow the same pattern instead of duplicating creds.

**Cron wrapping.** All cron jobs go through `bin/cron_notify.sh <project-dir> <cmd...>`. The wrapper `cd`s into the project, captures stderr, and posts an alert with the tail of stderr if the command exits non-zero. Don't invoke project scripts directly from crontab — use the wrapper so failures are visible.

**Per-project state files (gitignored).** Several scripts persist state next to their code: `playtomic-booker/bookings.json` (config), `playtomic-booker/notified.json` (dedup), `playtomic-booker/.book_court.lock` (flock against overlapping cron runs), `~/.gmail_digest_last_run` (gmail-digest cursor). These are intentionally not in git — see `.gitignore`.

**Headless Google OAuth.** While the OAuth consent screen is in **Testing**, Google revokes refresh tokens every 7 days. Each Google-using project ships a `reauth.sh` that runs the OAuth flow against `localhost:8080`; you re-authorize from a laptop via `ssh -L 8080:localhost:8080 root@<vm>` and then run `./reauth.sh` on the VM. Publishing the consent screen to **In production** in Google Cloud Console removes the 7-day expiry.

## Telegram bot plugin model

`telegram-bot/bot.py` is a dispatcher, not a monolith. At startup it walks `REGISTERED_MODULES` (a list of `(project_dir, handler_filename)` tuples) and dynamically imports each handler file from a sibling project directory. Every plugin must export:

- `MENU_BUTTON: str` — label appended to the bot's main reply keyboard.
- `register(app: Application, chat_filter: int) -> None` — registers handlers on the PTB `Application`. `chat_filter` is the authorized chat id (the bot is access-gated to a single chat).

To add a section: drop a `bot_handlers.py` into the target project (it can `import` siblings from its own dir — the dispatcher inserts the project dir on `sys.path` before loading), then append `("<project-dir>", "bot_handlers.py")` to `REGISTERED_MODULES` in `telegram-bot/bot.py`. Currently registered: `playtomic-booker` ("🎾 Playtomic" submenu over `bookings.json`).

Bot service management:

```bash
systemctl status sanek325-telegram-bot
systemctl restart sanek325-telegram-bot      # required after editing bot.py or any handler
journalctl -u sanek325-telegram-bot -f
```

The systemd unit lives at `telegram-bot/sanek325-telegram-bot.service`. Restart the service after touching `bot.py` or any plugin's `bot_handlers.py`.

## Crontab

```cron
*/10 * * * * /root/workspace/sanek325/bin/cron_notify.sh playtomic-calendar .venv/bin/python playtomic_to_calendar.py >> /tmp/playtomic-calendar.log 2>&1
*    * * * * /root/workspace/sanek325/bin/cron_notify.sh playtomic-booker   .venv/bin/python book_court.py            >> /tmp/book-court.log        2>&1
```
