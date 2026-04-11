"""
Telegram bot handlers for playtomic-booker.

The top-level sanek325 Telegram bot (see ../telegram-bot/bot.py) imports this
module and calls `register(app, chat_filter)` to attach the "Playtomic"
submenu: list / add / remove bookings in bookings.json.

Design notes:
- bookings.json is read/written atomically (tmpfile + os.replace) so that
  the cron-driven book_court.py can read it concurrently without races.
- All handlers are gated by chat_filter, so only the authorized chat can
  see/trigger them.
"""

import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

# Shown as a top-level reply-keyboard button in the main bot menu.
MENU_BUTTON = "🎾 Playtomic"

BOOKINGS_FILE = Path(__file__).parent / "bookings.json"
NOTIFIED_FILE = Path(__file__).parent / "notified.json"

# Conversation states for the "Add" flow
ADD_DATE, ADD_TIME, ADD_DURATION = range(3)

# Callback-data tokens
CB_LIST = "pb_list"
CB_ADD = "pb_add"
CB_REMOVE = "pb_remove"
CB_BACK = "pb_back"
CB_DEL_PREFIX = "pb_del_"  # followed by index in the sorted list


# --------------------------------------------------------------------------- #
# bookings.json helpers
# --------------------------------------------------------------------------- #

def _load() -> list[dict]:
    if not BOOKINGS_FILE.exists():
        return []
    return json.loads(BOOKINGS_FILE.read_text())


def _load_notified() -> dict:
    """Mirror of book_court.py:load_notified — {slot_key: "ok"|"fail"}."""
    if not NOTIFIED_FILE.exists():
        return {}
    return json.loads(NOTIFIED_FILE.read_text())


def _slot_key(b: dict) -> str:
    """Must match book_court.py:slot_key."""
    return f"{b['date']}_{b['time']}_{b['duration']}"


def _pending_bookings() -> list[dict]:
    """Bookings that haven't been notified yet, sorted chronologically."""
    notified = _load_notified()
    pending = [b for b in _load() if _slot_key(b) not in notified]
    pending.sort(key=lambda b: (b["date"], b["time"]))
    return pending


def _atomic_write_json(path: Path, data) -> None:
    fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass
        raise


def _save(bookings: list[dict]) -> None:
    bookings.sort(key=lambda b: (b["date"], b["time"]))
    _atomic_write_json(BOOKINGS_FILE, bookings)


def _save_notified(notified: dict) -> None:
    _atomic_write_json(NOTIFIED_FILE, notified)


def _fmt(b: dict) -> str:
    return f"{b['date']} {b['time'][:5]} · {b['duration']} min"


def _submenu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📋 List", callback_data=CB_LIST)],
            [InlineKeyboardButton("➕ Add", callback_data=CB_ADD)],
            [InlineKeyboardButton("➖ Remove", callback_data=CB_REMOVE)],
        ]
    )


# --------------------------------------------------------------------------- #
# Simple (non-conversation) handlers
# --------------------------------------------------------------------------- #

async def show_submenu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Entry: user tapped the 'Playtomic' reply-keyboard button."""
    await update.message.reply_text(
        "Playtomic — what do you want to do?", reply_markup=_submenu()
    )


async def cb_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    pending = _pending_bookings()
    if not pending:
        text = "📋 *Pending bookings*\n\n_Nothing to book._"
    else:
        lines = ["📋 *Pending bookings*", ""]
        lines.extend(f"• {_fmt(b)}" for b in pending)
        text = "\n".join(lines)
    await q.edit_message_text(text, parse_mode="Markdown", reply_markup=_submenu())


async def cb_remove(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    bookings = sorted(_load(), key=lambda b: (b["date"], b["time"]))
    if not bookings:
        await q.edit_message_text(
            "No bookings to remove.", reply_markup=_submenu()
        )
        return
    buttons = [
        [InlineKeyboardButton(f"🗑 {_fmt(b)}", callback_data=f"{CB_DEL_PREFIX}{i}")]
        for i, b in enumerate(bookings)
    ]
    buttons.append([InlineKeyboardButton("⬅️ Back", callback_data=CB_BACK)])
    await q.edit_message_text(
        "Tap a slot to remove it:", reply_markup=InlineKeyboardMarkup(buttons)
    )


async def cb_delete_one(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    try:
        idx = int(q.data[len(CB_DEL_PREFIX):])
    except ValueError:
        await q.edit_message_text("Invalid selection.", reply_markup=_submenu())
        return
    bookings = sorted(_load(), key=lambda b: (b["date"], b["time"]))
    if idx < 0 or idx >= len(bookings):
        await q.edit_message_text(
            "That slot is no longer in the list.", reply_markup=_submenu()
        )
        return
    removed = bookings.pop(idx)
    _save(bookings)

    # Also drop the slot from notified.json if it was there, so re-adding the
    # same slot later won't be auto-skipped by book_court.py.
    notified = _load_notified()
    cleared_notified = notified.pop(_slot_key(removed), None) is not None
    if cleared_notified:
        _save_notified(notified)

    msg = f"✅ Removed: {_fmt(removed)}"
    if cleared_notified:
        msg += "\n🧹 Also cleared from notified.json"
    await q.edit_message_text(msg, reply_markup=_submenu())


async def cb_back(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(
        "Playtomic — what do you want to do?", reply_markup=_submenu()
    )


# --------------------------------------------------------------------------- #
# Add conversation
# --------------------------------------------------------------------------- #

async def cb_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(
        "📅 Send the *date* for the new booking (format: `YYYY-MM-DD`).\n\n"
        "Send /cancel or tap 🎾 Playtomic to abort.",
        parse_mode="Markdown",
    )
    return ADD_DATE


async def add_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    try:
        d = datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        await update.message.reply_text(
            "Invalid date. Use `YYYY-MM-DD`, e.g. `2026-05-06`.",
            parse_mode="Markdown",
        )
        return ADD_DATE
    if d < datetime.now().date():
        await update.message.reply_text("That date is in the past. Try again.")
        return ADD_DATE
    context.user_data["pb_date"] = text
    await update.message.reply_text(
        "🕒 Now send the *time* in 24h format (`HH:MM`).", parse_mode="Markdown"
    )
    return ADD_TIME


async def add_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    try:
        datetime.strptime(text, "%H:%M")
    except ValueError:
        await update.message.reply_text(
            "Invalid time. Use `HH:MM`, e.g. `19:30`.", parse_mode="Markdown"
        )
        return ADD_TIME
    context.user_data["pb_time"] = text + ":00"
    await update.message.reply_text(
        "⏱ Finally, send the *duration* in minutes (e.g. `60` or `90`).",
        parse_mode="Markdown",
    )
    return ADD_DURATION


async def add_duration(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    try:
        duration = int(text)
    except ValueError:
        await update.message.reply_text("Invalid duration. Send an integer (minutes).")
        return ADD_DURATION
    if duration <= 0 or duration > 240:
        await update.message.reply_text("Duration must be between 1 and 240 minutes.")
        return ADD_DURATION

    new_booking = {
        "date": context.user_data["pb_date"],
        "time": context.user_data["pb_time"],
        "duration": duration,
    }
    bookings = _load()
    bookings.append(new_booking)
    _save(bookings)

    await update.message.reply_text(
        f"✅ Added: {_fmt(new_booking)}", reply_markup=_submenu()
    )
    context.user_data.pop("pb_date", None)
    context.user_data.pop("pb_time", None)
    return ConversationHandler.END


async def add_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("pb_date", None)
    context.user_data.pop("pb_time", None)
    await update.message.reply_text(
        "Cancelled.", reply_markup=_submenu()
    )
    return ConversationHandler.END


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #

def register(app: Application, chat_filter: int) -> None:
    """Attach the Playtomic submenu handlers to the bot Application."""
    chat = filters.Chat(chat_id=chat_filter)

    # While inside the Add conversation, exclude the menu button so the user's
    # tap cleanly cancels instead of being interpreted as date/time/duration.
    add_input = (
        chat
        & filters.TEXT
        & ~filters.COMMAND
        & ~filters.Text([MENU_BUTTON])
    )

    add_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_add_start, pattern=f"^{CB_ADD}$")],
        states={
            ADD_DATE: [MessageHandler(add_input, add_date)],
            ADD_TIME: [MessageHandler(add_input, add_time)],
            ADD_DURATION: [MessageHandler(add_input, add_duration)],
        },
        fallbacks=[
            MessageHandler(chat & filters.Regex(r"^/cancel$"), add_cancel),
            MessageHandler(chat & filters.Text([MENU_BUTTON]), add_cancel),
        ],
        per_user=True,
        per_chat=True,
        allow_reentry=True,
    )
    # Conversation must be registered before the plain menu handler so that
    # the "Playtomic" tap during an active conversation hits the cancel
    # fallback instead of the plain submenu handler.
    app.add_handler(add_conv)

    app.add_handler(
        MessageHandler(chat & filters.Text([MENU_BUTTON]), show_submenu)
    )

    app.add_handler(CallbackQueryHandler(cb_list, pattern=f"^{re.escape(CB_LIST)}$"))
    app.add_handler(CallbackQueryHandler(cb_remove, pattern=f"^{re.escape(CB_REMOVE)}$"))
    app.add_handler(CallbackQueryHandler(cb_back, pattern=f"^{re.escape(CB_BACK)}$"))
    app.add_handler(
        CallbackQueryHandler(cb_delete_one, pattern=rf"^{re.escape(CB_DEL_PREFIX)}\d+$")
    )
