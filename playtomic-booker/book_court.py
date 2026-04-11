"""
Monitor Playtomic court availability and notify via Telegram.

Reads desired slots from bookings.json. Run via cron every minute.

When a slot's opening time is within 3 minutes (courts open exactly
14 days before), the script starts polling the Playtomic API every
5 seconds for up to 6 minutes. On success it creates a payment intent
(locks the court) and sends a Telegram message with a direct payment
link. On failure after 6 minutes it notifies about the miss.

Usage:
    python book_court.py

Crontab (every minute):
    * * * * * cd /path/to/gmail-telegram-digest && .venv/bin/python book_court.py >> /tmp/book-court.log 2>&1

Requires PLAYTOMIC_EMAIL, PLAYTOMIC_PASSWORD, TELEGRAM_BOT_TOKEN,
and TELEGRAM_CHAT_ID in .env.
"""

import fcntl
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

import telegram_sender

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

TENANT_ID = "7856db74-8044-45c4-ae4d-5cbec9305614"
SPORT_ID = "PADEL"
TIMEZONE = "Europe/Amsterdam"
API_BASE = "https://api.playtomic.io"
PAYMENT_URL = "https://app.playtomic.com/payments"
BOOKINGS_FILE = Path(__file__).parent / "bookings.json"
NOTIFIED_FILE = Path(__file__).parent / "notified.json"
LOCK_FILE = Path(__file__).parent / ".book_court.lock"

BOOKING_WINDOW = timedelta(days=14)
EARLY_START = timedelta(minutes=3)   # start polling 3 min before opening
POLL_DURATION = timedelta(minutes=6)  # poll for up to 6 min
POLL_INTERVAL = 5                     # seconds between polls

# Double courts in preferred order (lowest number first)
DOUBLE_COURTS = [
    ("f390df12-36a5-4066-aae5-ef5c864bbcf2", "1. Center Court Mate"),
    ("72603349-0750-4d05-9e9c-a8a130a22f31", "2. Acosorb Mate"),
    ("19c9e451-b546-4b4b-a99c-056c5637c4f8", "3. Mate Court"),
    ("d1d43b57-f854-4e75-8a27-5c5f93746c20", "4. Mate Court"),
    ("03b0b8c2-33e3-4fc0-91f1-ee7a0435fc82", "5. Mate Court"),
]
DOUBLE_COURT_IDS = {rid for rid, _ in DOUBLE_COURTS}
COURT_NAMES = {rid: name for rid, name in DOUBLE_COURTS}

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "X-Requested-With": "com.playtomic.web",
    "Accept": "application/json",
    "Content-Type": "application/json",
}

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def login(email: str, password: str) -> dict:
    """Login and return {access_token, user_id}."""
    resp = requests.post(
        f"{API_BASE}/v3/auth/login",
        headers=HEADERS,
        json={"email": email, "password": password},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    return {"access_token": data["access_token"], "user_id": data["user_id"]}


def auth_headers(token: str) -> dict:
    return {**HEADERS, "Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------


def get_availability(date_str: str) -> list:
    """Fetch available slots for a given date (YYYY-MM-DD, local time)."""
    resp = requests.get(
        f"{API_BASE}/v1/availability",
        headers=HEADERS,
        params={
            "sport_id": SPORT_ID,
            "tenant_id": TENANT_ID,
            "local_start_min": f"{date_str}T00:00:00",
            "local_start_max": f"{date_str}T23:59:59",
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def find_matching_court(availability: list, time_str: str, duration: int):
    """Find the best double court with a slot matching requested local time.

    Prefers the court with the smallest number (1 > 2 > ... > 5).
    Returns (resource_id, court_name, start_utc) or (None, None, None).
    """
    tz = ZoneInfo(TIMEZONE)
    if not availability:
        return None, None, None
    date_str = availability[0]["start_date"]
    local_dt = datetime.strptime(
        f"{date_str}T{time_str}", "%Y-%m-%dT%H:%M:%S"
    ).replace(tzinfo=tz)
    target_utc = local_dt.astimezone(ZoneInfo("UTC")).strftime("%H:%M:%S")

    # Index availability by resource_id for fast lookup
    by_rid = {r["resource_id"]: r for r in availability}

    # Iterate courts in preferred order (lowest number first)
    for rid, name in DOUBLE_COURTS:
        resource = by_rid.get(rid)
        if not resource:
            continue
        for slot in resource.get("slots", []):
            if slot["start_time"] == target_utc and slot["duration"] == duration:
                start_utc = f"{resource['start_date']}T{slot['start_time']}"
                return rid, name, start_utc
    return None, None, None


# ---------------------------------------------------------------------------
# Payment intent
# ---------------------------------------------------------------------------


def create_payment_intent(token: str, user_id: str, resource_id: str,
                          start_utc: str, duration: int) -> str:
    """Create a payment intent (locks the court). Returns payment_intent_id."""
    resp = requests.post(
        f"{API_BASE}/v1/payment_intents",
        headers=auth_headers(token),
        json={
            "allowed_payment_method_types": [
                "OFFER", "CASH", "MERCHANT_WALLET", "DIRECT", "SWISH",
                "IDEAL", "BANCONTACT", "PAYTRAIL", "CREDIT_CARD", "QUICK_PAY",
            ],
            "user_id": user_id,
            "cart": {
                "requested_item": {
                    "cart_item_type": "CUSTOMER_MATCH",
                    "cart_item_voucher_id": None,
                    "cart_item_data": {
                        "supports_split_payment": True,
                        "number_of_players": 4,
                        "tenant_id": TENANT_ID,
                        "resource_id": resource_id,
                        "start": start_utc,
                        "duration": duration,
                        "match_registrations": [
                            {"user_id": user_id, "pay_now": True},
                        ],
                    },
                },
            },
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["payment_intent_id"]


# ---------------------------------------------------------------------------
# Notification tracking
# ---------------------------------------------------------------------------


def load_notified() -> dict:
    """Load notified slots as {key: status}. Status is "ok" or "fail"."""
    if not NOTIFIED_FILE.exists():
        return {}
    return json.loads(NOTIFIED_FILE.read_text())


def save_notified(notified: dict):
    NOTIFIED_FILE.write_text(json.dumps(notified, indent=2))


def slot_key(b: dict) -> str:
    return f"{b['date']}_{b['time']}_{b['duration']}"


# ---------------------------------------------------------------------------
# Polling
# ---------------------------------------------------------------------------


def poll_and_book(b: dict, token: str, user_id: str) -> bool:
    """Poll for availability for up to POLL_DURATION. Returns True on success."""
    date, time_str, duration = b["date"], b["time"], b["duration"]
    time_short = time_str[:5]
    deadline = datetime.now() + POLL_DURATION
    attempt = 0

    while datetime.now() < deadline:
        attempt += 1
        try:
            availability = get_availability(date)
            resource_id, court_name, start_utc = find_matching_court(
                availability, time_str, duration
            )
        except Exception as e:
            print(f"    Poll #{attempt}: error fetching availability: {e}")
            time.sleep(POLL_INTERVAL)
            continue

        if not resource_id:
            print(f"    Poll #{attempt}: no courts yet...")
            time.sleep(POLL_INTERVAL)
            continue

        print(f"    Poll #{attempt}: found {court_name}! Creating payment intent...")
        try:
            pi_id = create_payment_intent(
                token, user_id, resource_id, start_utc, duration
            )
        except Exception as e:
            print(f"    Failed to create payment intent: {e}")
            time.sleep(POLL_INTERVAL)
            continue

        payment_link = f"{PAYMENT_URL}?payment_intent_id={pi_id}"
        msg = (
            f"🎾 Court available!\n"
            f"{date} at {time_short} ({duration}min)\n"
            f"Court: {court_name}\n\n"
            f"Pay here: {payment_link}"
        )
        telegram_sender.send_html(msg)
        print(f"    Notified! PI: {pi_id}")
        return True

    return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    load_dotenv()

    if not BOOKINGS_FILE.exists():
        print(f"Error: {BOOKINGS_FILE} not found.")
        sys.exit(1)

    email = os.environ.get("PLAYTOMIC_EMAIL")
    password = os.environ.get("PLAYTOMIC_PASSWORD")
    if not email or not password:
        print("Error: PLAYTOMIC_EMAIL and PLAYTOMIC_PASSWORD must be set in .env")
        sys.exit(1)

    bookings = json.loads(BOOKINGS_FILE.read_text())
    notified = load_notified()

    tz = ZoneInfo(TIMEZONE)
    now = datetime.now(tz)

    # Categorise future slots into two buckets:
    # 1. "actionable" — booking window is opening now, poll aggressively.
    # 2. "early_check" — before the booking window, try a single check
    #    in case a court is already available (e.g. cancellation).
    actionable = []
    early_check = []
    for b in bookings:
        key = slot_key(b)
        if key in notified:
            continue
        slot_dt = datetime.strptime(
            f"{b['date']}T{b['time']}", "%Y-%m-%dT%H:%M:%S"
        ).replace(tzinfo=tz)
        if slot_dt < now:
            continue
        opens_at = slot_dt - BOOKING_WINDOW
        poll_start = opens_at - EARLY_START
        poll_end = opens_at + POLL_DURATION
        if poll_start <= now <= poll_end:
            actionable.append(b)
        elif now > poll_end:
            early_check.append(b)

    if not actionable and not early_check:
        return

    # Login
    print(f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] Logging in...")
    auth = login(email, password)
    token, user_id = auth["access_token"], auth["user_id"]

    # --- Early check: single attempt for slots before the booking window ---
    for b in early_check:
        key = slot_key(b)
        date, time_str, duration = b["date"], b["time"], b["duration"]
        time_short = time_str[:5]
        print(f"  Early check for {date} {time_short} ({duration}min)...")
        try:
            availability = get_availability(date)
            resource_id, court_name, start_utc = find_matching_court(
                availability, time_str, duration
            )
        except Exception as e:
            print(f"    Error: {e}")
            continue
        if not resource_id:
            print(f"    Not available yet.")
            notified[key] = "fail"
            save_notified(notified)
            msg = (
                f"❌ Could not book court\n"
                f"{date} at {time_short} ({duration}min)\n"
                f"No double court available."
            )
            telegram_sender.send_html(msg)
            continue
        print(f"    Found {court_name}! Creating payment intent...")
        try:
            pi_id = create_payment_intent(
                token, user_id, resource_id, start_utc, duration
            )
        except Exception as e:
            print(f"    Failed to create payment intent: {e}")
            continue
        payment_link = f"{PAYMENT_URL}?payment_intent_id={pi_id}"
        msg = (
            f"🎾 Court available!\n"
            f"{date} at {time_short} ({duration}min)\n"
            f"Court: {court_name}\n\n"
            f"Pay here: {payment_link}"
        )
        telegram_sender.send_html(msg)
        print(f"    Notified! PI: {pi_id}")
        notified[key] = "ok"
        save_notified(notified)

    # --- Polling: booking window is opening now ---
    for b in actionable:
        key = slot_key(b)
        date, time_str, duration = b["date"], b["time"], b["duration"]
        time_short = time_str[:5]
        print(f"  Polling for {date} {time_short} ({duration}min)...")

        success = poll_and_book(b, token, user_id)

        if success:
            notified[key] = "ok"
        else:
            notified[key] = "fail"
            msg = (
                f"❌ Could not find court\n"
                f"{date} at {time_short} ({duration}min)\n"
                f"Polled for {int(POLL_DURATION.total_seconds() // 60)} minutes, "
                f"no double court became available."
            )
            telegram_sender.send_html(msg)
            print(f"    Failed after {int(POLL_DURATION.total_seconds() // 60)} min polling.")

        save_notified(notified)

    print("Done.")


if __name__ == "__main__":
    lock_fp = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock_fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        sys.exit(0)
    main()
