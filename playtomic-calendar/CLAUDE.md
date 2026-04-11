# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Playtomic to Calendar** (`playtomic_to_calendar.py`) — Fetches unread Playtomic and Padel Mate Academy emails, extracts match/class details, creates Google Calendar events, marks emails as read. Designed to run as a cron job.

Self-contained script (no external local modules). Uses its own OAuth token (`token_playtomic.json`) with Gmail modify + Calendar scopes.

## Running

```bash
source .venv/bin/activate
python playtomic_to_calendar.py
```

First run will open a browser for OAuth authorization.

## Dependencies

```bash
pip install -r requirements.txt
```

`pdftotext` from `poppler-utils` is required for parsing Padel Mate Academy PDF invoices.

## Architecture

**Email processing strategy**:
- Playtomic: prefers `.ics` attachment parsing (matches Gmail's "Add to calendar" button data); falls back to body text parsing for "Registration confirmation" and "Match invitation" emails.
- Padel Mate Academy: parses lesson dates from the PDF invoice attachment via `pdftotext`.
- Other emails are skipped.

**Deduplication**: Checks by `iCalUID` for ICS events, then by matching start time on the same day. All processed emails are marked as read regardless of outcome.

**Timezone handling**: Local times (Europe/Amsterdam) are converted to UTC for calendar storage.

## OAuth Tokens

- `token_playtomic.json` — Gmail modify + Calendar. Auto-generated on first run.
- Requires `credentials.json` (Google OAuth2 Desktop client, not committed).
