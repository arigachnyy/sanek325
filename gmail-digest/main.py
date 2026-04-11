"""
Daily Gmail digest: fetch new emails, summarize with Claude, send via Telegram.

Usage:
    python main.py
"""

import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

from dotenv import load_dotenv

import gmail_client
import summarizer
import telegram_sender

LAST_RUN_FILE = Path.home() / ".gmail_digest_last_run"


def read_last_run() -> datetime:
    """Read last run timestamp, defaulting to 24h ago if missing."""
    try:
        text = LAST_RUN_FILE.read_text().strip()
        return datetime.fromisoformat(text)
    except (FileNotFoundError, ValueError):
        return datetime.now(timezone.utc) - timedelta(hours=24)


def write_last_run(ts: datetime) -> None:
    LAST_RUN_FILE.write_text(ts.isoformat())


def main():
    load_dotenv()

    since = read_last_run()
    run_start = datetime.now(timezone.utc)

    print(f"Fetching emails since {since.isoformat()}...")
    emails = gmail_client.fetch_emails_since(since)

    if not emails:
        print("No new emails.")
        telegram_sender.send("No new emails since last digest.")
        write_last_run(run_start)
        return

    print(f"Found {len(emails)} new email(s). Summarizing...")
    digest = summarizer.summarize(emails)

    print("Sending digest via Telegram...")
    telegram_sender.send(digest)

    write_last_run(run_start)
    print("Done.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
