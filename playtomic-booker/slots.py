"""
Shared booking-slot helpers for playtomic-booker.

Used by both book_court.py (the cron-driven booker) and bot_handlers.py
(the Telegram bot menu). Kept dependency-free so the bot can import it
without pulling in book_court's heavier deps (requests, dotenv).
"""

from datetime import datetime, timedelta

# Playtomic offers 60/90/120-minute slots at this venue. If the requested
# duration isn't available, fall back to longer ones in this order.
DURATION_FALLBACKS = [60, 90, 120]


def fallback_durations(requested: int) -> list[int]:
    """Durations to try, in preference order: requested first, then longer."""
    return [requested] + [d for d in DURATION_FALLBACKS if d > requested]


def slot_key(b: dict) -> str:
    """Dedup key for notified.json — must match across all callers."""
    return f"{b['date']}_{b['time']}_{b['duration']}"


def expand_booking(b: dict) -> list[dict]:
    """Expand one desired slot into earlier-start candidates that still
    cover it.

    Example: 20:00 for 60 min → [
        19:00 / 120 min,   # earliest start, 1h before
        19:30 / 90 min,    # 30 min before
        20:00 / 60 min,    # as requested
    ]

    Each candidate has the same shape as a booking entry; the booker
    treats them as independent slots (own poll window, own dedup key,
    own success/fail Telegram message). The user expects this — they'll
    cancel any extras manually.
    """
    requested_dt = datetime.strptime(
        f"{b['date']}T{b['time']}", "%Y-%m-%dT%H:%M:%S"
    )
    requested_duration = b["duration"]
    candidates = []
    for offset in (60, 30, 0):  # earliest first
        cand_dt = requested_dt - timedelta(minutes=offset)
        cand_min_duration = requested_duration + offset
        valid = [d for d in DURATION_FALLBACKS if d >= cand_min_duration]
        if not valid:
            continue
        candidates.append({
            "date": cand_dt.strftime("%Y-%m-%d"),
            "time": cand_dt.strftime("%H:%M:%S"),
            "duration": valid[0],
        })
    return candidates
