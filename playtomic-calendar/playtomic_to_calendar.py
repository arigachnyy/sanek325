"""
Fetch unread Playtomic and Padel Mate Academy emails, create Google Calendar
events, and mark emails as read.

Playtomic emails: prefers .ics attachment, falls back to body parsing.
Padel Mate Academy: parses lesson dates from the PDF invoice attachment
(requires pdftotext from poppler-utils).

Usage:
    python3 playtomic_to_calendar.py

First run will open a browser for OAuth authorization (Gmail modify + Calendar access).
Requires credentials.json in this directory (same as for gmail digest).
"""

import base64
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar",
]
CREDENTIALS_PATH = os.path.join(os.path.dirname(__file__), "credentials.json")
TOKEN_PATH = os.path.join(os.path.dirname(__file__), "token_playtomic.json")
TIMEZONE = "Europe/Amsterdam"
CALENDAR_ID = "89e4271009dd958bd23763220f96d69597dca104cb965556a3cd0cfd391aed9a@group.calendar.google.com"


def get_credentials():
    """Load or create OAuth2 credentials for Gmail + Calendar access."""
    creds = None
    if os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CREDENTIALS_PATH):
                print(f"Error: {CREDENTIALS_PATH} not found.")
                print("Download OAuth2 Desktop credentials from Google Cloud Console.")
                sys.exit(1)
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_PATH, "w") as f:
            f.write(creds.to_json())
    return creds


def fetch_unread_playtomic_emails(gmail):
    """Fetch all unread emails from Playtomic."""
    messages = []
    page_token = None
    while True:
        resp = (
            gmail.users()
            .messages()
            .list(userId="me", q="from:playtomic is:unread", pageToken=page_token)
            .execute()
        )
        messages.extend(resp.get("messages", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    emails = []
    for m in messages:
        full = (
            gmail.users()
            .messages()
            .get(userId="me", id=m["id"], format="full")
            .execute()
        )
        emails.append(full)
    return emails



def get_headers(msg):
    return {h["name"].lower(): h["value"] for h in msg["payload"].get("headers", [])}


def mark_as_read(gmail, message_id):
    """Remove UNREAD label from a message."""
    gmail.users().messages().modify(
        userId="me",
        id=message_id,
        body={"removeLabelIds": ["UNREAD"]},
    ).execute()


# ---------------------------------------------------------------------------
# ICS attachment extraction (preferred — matches Gmail "Add to calendar")
# ---------------------------------------------------------------------------

def find_ics_attachment(gmail, msg):
    """Find and return the calendar.ics attachment content, or None."""

    def _search(payload):
        filename = payload.get("filename", "")
        if filename.endswith(".ics"):
            body = payload.get("body", {})
            att_id = body.get("attachmentId")
            if att_id:
                att = (
                    gmail.users()
                    .messages()
                    .attachments()
                    .get(userId="me", messageId=msg["id"], id=att_id)
                    .execute()
                )
                return base64.urlsafe_b64decode(att["data"]).decode("utf-8")
            data = body.get("data")
            if data:
                return base64.urlsafe_b64decode(data).decode("utf-8")
        for part in payload.get("parts", []):
            result = _search(part)
            if result:
                return result
        return None

    return _search(msg["payload"])


def parse_ics(ics_text):
    """Parse a VCALENDAR ICS string into a Google Calendar event body.

    Imports the event using the same fields Playtomic provides:
    SUMMARY, DTSTART, DTEND, LOCATION, GEO, URL, UID.
    """
    def get_field(name):
        m = re.search(rf"^{name}[;:](.+)$", ics_text, re.MULTILINE)
        return m.group(1).strip() if m else None

    dtstart = get_field("DTSTART")
    dtend = get_field("DTEND")
    if not dtstart or not dtend:
        return None

    summary = get_field("SUMMARY") or "Playtomic Match"
    location = get_field("LOCATION") or ""
    url = get_field("URL") or ""
    geo = get_field("GEO")
    uid = get_field("UID") or ""

    description = url

    event = {
        "summary": summary,
        "location": location,
        "description": description,
        "start": {"dateTime": _ics_dt_to_rfc3339(dtstart), "timeZone": "UTC"},
        "end": {"dateTime": _ics_dt_to_rfc3339(dtend), "timeZone": "UTC"},
    }

    # Preserve Playtomic UID so re-runs can detect duplicates via iCalUID
    if uid:
        event["iCalUID"] = uid

    return event


def _ics_dt_to_rfc3339(dt_str):
    """Convert ICS datetime like '20260331T070000Z' to '2026-03-31T07:00:00Z'."""
    dt_str = dt_str.rstrip("Z")
    dt = datetime.strptime(dt_str, "%Y%m%dT%H%M%S")
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")



# ---------------------------------------------------------------------------
# Fallback body parsers for email types without ICS attachments
# ---------------------------------------------------------------------------

def get_body(payload):
    """Extract plain text body from message payload (recursive for multipart)."""
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode(
            "utf-8", errors="replace"
        )
    for part in payload.get("parts", []):
        text = get_body(part)
        if text:
            return text
    return ""


def parse_registration_confirmation(body):
    """Parse a 'Registration confirmation' email body into a calendar event."""
    date_m = re.search(r"Date\s+(\d{2}/\d{2}/\d{4})", body)
    hour_m = re.search(r"Hour\s+(\d{2}:\d{2})\s*-\s*(\d{2}:\d{2})", body)
    if not date_m or not hour_m:
        return None

    club_m = re.search(r"Club\s+(.+)", body)
    location = club_m.group(1).strip() if club_m else ""
    # Strip trailing court info after comma
    location = re.sub(r"\s*,\s*\d+\..*$", "", location)

    # Class name sits between "Name" and "Sport" keywords
    name_m = re.search(r"Name\s+(.+?)\s*Sport\b", body, re.DOTALL)
    class_name = name_m.group(1).strip() if name_m else ""

    try:
        dt_start = datetime.strptime(
            f"{date_m.group(1)} {hour_m.group(1)}", "%d/%m/%Y %H:%M"
        )
        dt_end = datetime.strptime(
            f"{date_m.group(1)} {hour_m.group(2)}", "%d/%m/%Y %H:%M"
        )
    except ValueError:
        return None

    # Convert local time to UTC for consistency with ICS events
    tz = ZoneInfo(TIMEZONE)
    dt_start_utc = dt_start.replace(tzinfo=tz).astimezone(ZoneInfo("UTC"))
    dt_end_utc = dt_end.replace(tzinfo=tz).astimezone(ZoneInfo("UTC"))

    summary = class_name if class_name else "Playtomic Class"

    return {
        "summary": summary,
        "location": location,
        "start": {
            "dateTime": dt_start_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "timeZone": "UTC",
        },
        "end": {
            "dateTime": dt_end_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "timeZone": "UTC",
        },
    }


def parse_match_invitation(body):
    """Parse a 'Match invitation' email body into a calendar event.

    Format: Date and time DD/MM/YYYY, HH:MM / Location ... / Duration N minutes
    """
    m = re.search(r"Date and time\s+(\d{2}/\d{2}/\d{4}),\s*(\d{2}:\d{2})", body)
    if not m:
        return None

    dur_m = re.search(r"Duration\s+(\d+)\s+minutes", body)
    duration = int(dur_m.group(1)) if dur_m else 60

    loc_m = re.search(r"Location\s+(.+)", body)
    location = loc_m.group(1).strip() if loc_m else ""

    try:
        dt_start = datetime.strptime(
            f"{m.group(1)} {m.group(2)}", "%d/%m/%Y %H:%M"
        )
    except ValueError:
        return None

    dt_end = dt_start + timedelta(minutes=duration)

    tz = ZoneInfo(TIMEZONE)
    dt_start_utc = dt_start.replace(tzinfo=tz).astimezone(ZoneInfo("UTC"))
    dt_end_utc = dt_end.replace(tzinfo=tz).astimezone(ZoneInfo("UTC"))

    return {
        "summary": "Playtomic Match",
        "location": location,
        "start": {
            "dateTime": dt_start_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "timeZone": "UTC",
        },
        "end": {
            "dateTime": dt_end_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "timeZone": "UTC",
        },
    }


def parse_booking_confirmation(body):
    """Parse a 'Playtomic - Booking Confirmation / Receipt' email body.

    Handles two date formats:
    - Newer (ts.playtomic.com): Date YYYY-MM-DD
    - Older (playtomic.io):     Date DD/MM/YYYY
    """
    # Try YYYY-MM-DD first, then DD/MM/YYYY
    date_m = re.search(r"Date\s+(\d{4}-\d{2}-\d{2})", body)
    date_fmt = "%Y-%m-%d"
    if not date_m:
        date_m = re.search(r"Date\s+(\d{2}/\d{2}/\d{4})", body)
        date_fmt = "%d/%m/%Y"
    time_m = re.search(r"Time\s+(\d{2}:\d{2})\s*-\s*(\d{2}:\d{2})", body)
    if not date_m or not time_m:
        return None

    club_m = re.search(r"Club\s+(.+)", body)
    location = club_m.group(1).strip() if club_m else ""
    location = re.sub(r"\s*,\s*\d+\..*$", "", location)

    try:
        dt_start = datetime.strptime(
            f"{date_m.group(1)} {time_m.group(1)}", f"{date_fmt} %H:%M"
        )
        dt_end = datetime.strptime(
            f"{date_m.group(1)} {time_m.group(2)}", f"{date_fmt} %H:%M"
        )
    except ValueError:
        return None

    tz = ZoneInfo(TIMEZONE)
    dt_start_utc = dt_start.replace(tzinfo=tz).astimezone(ZoneInfo("UTC"))
    dt_end_utc = dt_end.replace(tzinfo=tz).astimezone(ZoneInfo("UTC"))

    return {
        "summary": "Playtomic Match",
        "location": location,
        "start": {
            "dateTime": dt_start_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "timeZone": "UTC",
        },
        "end": {
            "dateTime": dt_end_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "timeZone": "UTC",
        },
    }


# ---------------------------------------------------------------------------
# Padel Mate Academy invoice (PDF attachment with lesson dates)
# ---------------------------------------------------------------------------

PADEL_MATE_LESSON_DURATION = timedelta(hours=1)
PADEL_MATE_LOCATION = "Padel Mate Club NTC, Amstelveen"


def fetch_unread_padel_mate_academy_emails(gmail):
    """Fetch all unread emails from Padel Mate Academy with invoice subject."""
    messages = []
    page_token = None
    while True:
        resp = (
            gmail.users()
            .messages()
            .list(
                userId="me",
                q='from:info@padelmateacademy.com subject:"Invoice Lessons Padel Mate Academy" is:unread',
                pageToken=page_token,
            )
            .execute()
        )
        messages.extend(resp.get("messages", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    emails = []
    for m in messages:
        full = (
            gmail.users()
            .messages()
            .get(userId="me", id=m["id"], format="full")
            .execute()
        )
        emails.append(full)
    return emails


def find_pdf_attachment(gmail, msg):
    """Find and return the PDF attachment content as bytes, or None."""

    def _search(payload):
        filename = payload.get("filename", "")
        if filename.lower().endswith(".pdf"):
            body = payload.get("body", {})
            att_id = body.get("attachmentId")
            if att_id:
                att = (
                    gmail.users()
                    .messages()
                    .attachments()
                    .get(userId="me", messageId=msg["id"], id=att_id)
                    .execute()
                )
                return base64.urlsafe_b64decode(att["data"])
            data = body.get("data")
            if data:
                return base64.urlsafe_b64decode(data)
        for part in payload.get("parts", []):
            result = _search(part)
            if result:
                return result
        return None

    return _search(msg["payload"])


def extract_pdf_text(pdf_bytes):
    """Extract text from a PDF using pdftotext (poppler-utils)."""
    with tempfile.NamedTemporaryFile(suffix=".pdf") as tmp:
        tmp.write(pdf_bytes)
        tmp.flush()
        result = subprocess.run(
            ["pdftotext", "-layout", tmp.name, "-"],
            capture_output=True,
            text=True,
        )
        return result.stdout


def parse_padel_mate_invoice(pdf_text):
    """Parse Padel Mate Academy invoice text into a list of calendar events.

    The PDF contains a lesson name (e.g. "LESSENREEKS 8x - NTC") followed by
    dates in DD-MM-YYYY - HH:MM format, and a "Lesson package id: NNNNN" line.
    Returns one event per lesson date.
    """
    # Extract lesson name: first non-blank line after the "Omschrijving" header
    # that is not a date and not a price/table header
    lesson_name = None
    lines = pdf_text.splitlines()
    after_header = False
    for line in lines:
        stripped = line.strip()
        if "Omschrijving" in stripped:
            after_header = True
            continue
        if after_header and stripped and not re.match(r"^\d{2}-\d{2}-\d{4}", stripped):
            if stripped not in ("Per stuk", "Aantal", "Bedrag"):
                lesson_name = stripped
                break

    if not lesson_name:
        lesson_name = "Padel Mate Academy Lesson"

    # Extract lesson package id for deduplication
    pkg_m = re.search(r"Lesson package id:\s*(\d+)", pdf_text)
    package_id = pkg_m.group(1) if pkg_m else ""

    # Extract all date-time entries
    dates = re.findall(r"(\d{2}-\d{2}-\d{4})\s*-\s*(\d{2}:\d{2})", pdf_text)
    if not dates:
        return []

    tz = ZoneInfo(TIMEZONE)
    events = []
    for date_str, time_str in dates:
        try:
            dt_start = datetime.strptime(f"{date_str} {time_str}", "%d-%m-%Y %H:%M")
        except ValueError:
            continue
        dt_end = dt_start + PADEL_MATE_LESSON_DURATION

        dt_start_utc = dt_start.replace(tzinfo=tz).astimezone(ZoneInfo("UTC"))
        dt_end_utc = dt_end.replace(tzinfo=tz).astimezone(ZoneInfo("UTC"))

        # Build a stable iCalUID from package id + date for dedup
        uid = f"padel-mate-{package_id}-{date_str}" if package_id else ""

        event = {
            "summary": lesson_name,
            "location": PADEL_MATE_LOCATION,
            "start": {
                "dateTime": dt_start_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "timeZone": "UTC",
            },
            "end": {
                "dateTime": dt_end_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "timeZone": "UTC",
            },
        }
        if uid:
            event["iCalUID"] = uid

        events.append(event)

    return events


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def event_exists(calendar, event):
    """Check if a matching event already exists in Google Calendar."""
    # For ICS events with a Playtomic UID, check by iCalUID
    ical_uid = event.get("iCalUID")
    if ical_uid:
        resp = (
            calendar.events()
            .list(calendarId=CALENDAR_ID, iCalUID=ical_uid)
            .execute()
        )
        if resp.get("items"):
            return True

    # Query the whole day in UTC and compare start times
    start_dt = event["start"]["dateTime"]
    if start_dt.endswith("Z"):
        dt = datetime.strptime(start_dt, "%Y-%m-%dT%H:%M:%SZ")
    else:
        dt = datetime.strptime(start_dt, "%Y-%m-%dT%H:%M:%S")

    day_start = dt.replace(hour=0, minute=0, second=0)
    day_end = day_start + timedelta(days=1)

    resp = (
        calendar.events()
        .list(
            calendarId=CALENDAR_ID,
            timeMin=day_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            timeMax=day_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            timeZone="UTC",
            singleEvents=True,
        )
        .execute()
    )
    for existing in resp.get("items", []):
        existing_start = existing.get("start", {}).get("dateTime", "")
        # Compare date and time portion (first 16 chars: YYYY-MM-DDTHH:MM)
        if existing_start and existing_start[:16] == start_dt[:16]:
            return True

    return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def create_event_if_new(calendar, gmail, event, msg_id, subject, counters):
    """Insert event into calendar if it doesn't already exist.

    Updates counters dict in place (keys: created, duplicates, errors).
    """
    if event_exists(calendar, event):
        print(
            f"  SKIP (duplicate): {event['summary']} | "
            f"{event['start']['dateTime']}"
        )
        mark_as_read(gmail, msg_id)
        counters["duplicates"] += 1
    else:
        try:
            calendar.events().insert(
                calendarId=CALENDAR_ID, body=event
            ).execute()
            print(
                f"  CREATED: {event['summary']} | "
                f"{event['start']['dateTime']} | "
                f"{event.get('location', '')}"
            )
            mark_as_read(gmail, msg_id)
            counters["created"] += 1
        except Exception as e:
            if "already exists" in str(e):
                try:
                    calendar.events().import_(
                        calendarId=CALENDAR_ID, body=event
                    ).execute()
                    print(
                        f"  CREATED (reimport): {event['summary']} | "
                        f"{event['start']['dateTime']} | "
                        f"{event.get('location', '')}"
                    )
                    mark_as_read(gmail, msg_id)
                    counters["created"] += 1
                except Exception:
                    print(
                        f"  SKIP (already exists): {event['summary']} | "
                        f"{event['start']['dateTime']}"
                    )
                    mark_as_read(gmail, msg_id)
                    counters["duplicates"] += 1
            else:
                print(f"  ERROR ({subject}): {e}")
                counters["errors"] += 1


def main():
    load_dotenv()

    creds = get_credentials()
    gmail = build("gmail", "v1", credentials=creds)
    calendar = build("calendar", "v3", credentials=creds)

    counters = {"created": 0, "skipped": 0, "duplicates": 0, "errors": 0}

    # --- Playtomic emails ---
    print("Fetching unread Playtomic emails...")
    emails = fetch_unread_playtomic_emails(gmail)
    print(f"Found {len(emails)} unread Playtomic email(s).\n")

    for msg in emails:
        headers = get_headers(msg)
        subject = headers.get("subject", "")
        msg_id = msg["id"]
        date = headers.get("date", "")

        # Prefer ICS attachment; fall back to body parsing for class registrations
        ics_text = find_ics_attachment(gmail, msg)
        if ics_text:
            event = parse_ics(ics_text)
        elif subject in ("Registration confirmation", "Match invitation",
                         "Playtomic - Booking Confirmation / Receipt"):
            body = get_body(msg["payload"])
            if subject == "Registration confirmation":
                event = parse_registration_confirmation(body)
            elif subject == "Match invitation":
                event = parse_match_invitation(body)
            else:
                event = parse_booking_confirmation(body)
        else:
            print(f"  SKIP (no ics): {subject} [{date}]")
            counters["skipped"] += 1
            continue

        if not event:
            print(f"  SKIP (parse failed): {subject} [{date}]")
            counters["skipped"] += 1
            continue

        create_event_if_new(calendar, gmail, event, msg_id, subject, counters)

    # --- Padel Mate Academy invoice emails ---
    print("\nFetching unread Padel Mate Academy invoice emails...")
    pma_emails = fetch_unread_padel_mate_academy_emails(gmail)
    print(f"Found {len(pma_emails)} unread Padel Mate Academy email(s).\n")

    for msg in pma_emails:
        headers = get_headers(msg)
        subject = headers.get("subject", "")
        msg_id = msg["id"]
        date = headers.get("date", "")

        pdf_bytes = find_pdf_attachment(gmail, msg)
        if not pdf_bytes:
            print(f"  SKIP (no PDF): {subject} [{date}]")
            mark_as_read(gmail, msg_id)
            counters["skipped"] += 1
            continue

        pdf_text = extract_pdf_text(pdf_bytes)
        events = parse_padel_mate_invoice(pdf_text)
        if not events:
            print(f"  SKIP (no dates in PDF): {subject} [{date}]")
            mark_as_read(gmail, msg_id)
            counters["skipped"] += 1
            continue

        for event in events:
            create_event_if_new(calendar, gmail, event, msg_id, subject, counters)

    print(
        f"\nDone. Created: {counters['created']}, "
        f"Duplicates: {counters['duplicates']}, "
        f"Skipped: {counters['skipped']}, "
        f"Errors: {counters['errors']}"
    )


if __name__ == "__main__":
    main()
