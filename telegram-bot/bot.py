#!/usr/bin/env python3
"""
sanek325 top-level Telegram bot.

Long-running polling bot that dispatches commands to per-project handler
modules. Each subproject under ../ can contribute its own menu by providing
a `bot_handlers.py` that exports:

    MENU_BUTTON: str                          # label for the main-menu button
    register(app: Application, chat_filter: int) -> None

The dispatcher loads the handler module, appends MENU_BUTTON to the main
reply-keyboard, and delegates registration.

Access control: all updates are gated by TELEGRAM_CHAT_ID from the env.

Run via systemd: sanek325-telegram-bot.service
"""

import importlib.util
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from telegram import KeyboardButton, ReplyKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

SANEK_ROOT = Path(__file__).resolve().parent.parent

# Shared Telegram credentials: playtomic-booker/.env is the canonical source
# (same bot token is used across all sanek325 scripts).
load_dotenv(SANEK_ROOT / "playtomic-booker" / ".env")

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
# python-telegram-bot is chatty at INFO level; keep its noise down.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram.ext.Application").setLevel(logging.WARNING)
log = logging.getLogger("sanek325-bot")


# --------------------------------------------------------------------------- #
# Plugin loading
# --------------------------------------------------------------------------- #

# (subproject dir name, handler module file)
REGISTERED_MODULES = [
    ("playtomic-booker", "bot_handlers.py"),
]


def _load_handler_module(project: str, filename: str):
    """Import a handler file from a sibling project directory."""
    path = SANEK_ROOT / project / filename
    if not path.exists():
        raise FileNotFoundError(f"handler module not found: {path}")
    # Make the project dir importable so the handler can `import` siblings
    # from its own directory if it wants to.
    project_dir = str(path.parent)
    if project_dir not in sys.path:
        sys.path.insert(0, project_dir)
    module_name = f"sanek325_handlers_{project.replace('-', '_')}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------- #
# Bot setup
# --------------------------------------------------------------------------- #

def _env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        print(f"error: {name} must be set in the environment", file=sys.stderr)
        sys.exit(1)
    return val


def build_app() -> Application:
    token = _env("TELEGRAM_BOT_TOKEN")
    authorized_chat_id = int(_env("TELEGRAM_CHAT_ID"))

    app = Application.builder().token(token).build()

    # Load handler modules and build the main menu from their MENU_BUTTON labels.
    menu_rows: list[list[KeyboardButton]] = []
    menu_labels: list[str] = []
    for project, filename in REGISTERED_MODULES:
        try:
            module = _load_handler_module(project, filename)
        except Exception as e:
            log.exception("failed to load handler for %s: %s", project, e)
            continue
        if not hasattr(module, "MENU_BUTTON") or not hasattr(module, "register"):
            log.error("%s is missing MENU_BUTTON or register()", project)
            continue
        module.register(app, chat_filter=authorized_chat_id)
        menu_labels.append(module.MENU_BUTTON)
        menu_rows.append([KeyboardButton(module.MENU_BUTTON)])
        log.info("registered handlers for %s", project)

    main_menu = ReplyKeyboardMarkup(menu_rows, resize_keyboard=True)

    chat_filter = filters.Chat(chat_id=authorized_chat_id)

    async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "Hi! Pick a section.", reply_markup=main_menu
        )

    async def on_unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.message is None:
            return
        await update.message.reply_text(
            "Use /menu to see the buttons.", reply_markup=main_menu
        )

    async def reject_unauthorized(update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat = update.effective_chat
        user = update.effective_user
        log.warning(
            "rejected update from chat_id=%s user_id=%s",
            chat.id if chat else "?",
            user.id if user else "?",
        )
        if update.message:
            await update.message.reply_text(
                "This bot is private."
            )

    app.add_handler(CommandHandler("start", cmd_start, filters=chat_filter))
    app.add_handler(CommandHandler("menu", cmd_start, filters=chat_filter))

    # Fallback for any unauthorized chat — runs in a lower-priority group so
    # it only fires when no other handler has already taken the update.
    app.add_handler(
        MessageHandler(~chat_filter, reject_unauthorized),
        group=10,
    )

    # Unknown text from the authorized chat that no handler matched.
    app.add_handler(
        MessageHandler(
            chat_filter & filters.TEXT & ~filters.COMMAND,
            on_unknown,
        ),
        group=20,
    )

    log.info("main menu: %s", menu_labels)
    return app


def main():
    app = build_app()
    log.info("sanek325-bot starting (polling)...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
