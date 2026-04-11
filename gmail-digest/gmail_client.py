import base64
import email.utils
import os
from datetime import datetime

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
TOKEN_PATH = os.path.join(os.path.dirname(__file__), "token.json")


def _get_credentials():
    creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(TOKEN_PATH, "w") as f:
            f.write(creds.to_json())
    return creds


def _get_body(payload):
    """Extract plain text body from message payload."""
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")

    for part in payload.get("parts", []):
        text = _get_body(part)
        if text:
            return text
    return ""


def _parse_message(msg):
    headers = {h["name"].lower(): h["value"] for h in msg["payload"].get("headers", [])}
    body = _get_body(msg["payload"])
    # Truncate long bodies to avoid blowing up the Claude context
    if len(body) > 2000:
        body = body[:2000] + "\n...[truncated]"
    return {
        "subject": headers.get("subject", "(no subject)"),
        "sender": headers.get("from", "unknown"),
        "date": headers.get("date", ""),
        "snippet": msg.get("snippet", ""),
        "body": body,
    }


def fetch_emails_since(since: datetime) -> list[dict]:
    """Fetch all emails received after `since` (UTC datetime)."""
    creds = _get_credentials()
    service = build("gmail", "v1", credentials=creds)

    epoch = int(since.timestamp())
    query = f"after:{epoch}"

    messages = []
    page_token = None
    while True:
        resp = (
            service.users()
            .messages()
            .list(userId="me", q=query, pageToken=page_token)
            .execute()
        )
        messages.extend(resp.get("messages", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    emails = []
    for m in messages:
        full = service.users().messages().get(userId="me", id=m["id"], format="full").execute()
        emails.append(_parse_message(full))

    return emails
