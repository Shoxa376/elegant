#!/usr/bin/env python3
import json, logging
from pathlib import Path
from typing import Dict

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder, CallbackQueryHandler, CommandHandler,
    ConversationHandler, MessageHandler, ContextTypes, filters
)

# ─── States ───────────────────────────────────────────────────────────────
LANG, ROOM, ISSUE = range(3)

# ─── Configuration ────────────────────────────────────────────────────────
BOT_TOKEN = "7739435317:AAHYupc2FTUELRhrK2d6h_yBVQ6MvaYyTUw"
ADMIN_CHAT_IDS = [-1002656961314]  # Make sure bot is added and admin
RECEPTION_USER_IDS = [430932662]
BOSS_USER_IDS = [1746109123]

DB_FILE = Path("issues.json")

def load_db() -> Dict[str, dict]:
    if DB_FILE.exists():
        text = DB_FILE.read_text(encoding='utf-8').strip()
        if text:
            return json.loads(text)
    return {}

def save_db(data: Dict[str, dict]):
    DB_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

tickets = load_db()

MSG = {
    "choose_lang": "Tilni tanlang / Выберите язык / Select language",
    "choose_room": {
        "uz": "Iltimos, xona raqamini tanlang",
        "ru": "Пожалуйста, выберите номер комнаты",
        "en": "Please choose your room number",
    },
    "ask_issue": {
        "uz": "Muammoingizni qisqa yozib qoldiring",
        "ru": "Опишите вашу проблему коротко",
        "en": "Please describe your issue briefly",
    },
    "ack_guest": {
        "uz": "Xabaringiz qabul qilindi! Adminlar tez orada javob beradi.",
        "ru": "Ваше сообщение получено! Администраторы скоро ответят.",
        "en": "Your message has been received! The admins will reply shortly.",
    },
    "resolved_dm": {
        "uz": "Muammoingiz hal qilindi! Rahmat.",
        "ru": "Ваша проблема решена! Спасибо.",
        "en": "Your issue has been resolved! Thank you.",
    },
    "processing_dm": {
        "uz": "Muammoingiz admin tomonidan ko‘rib chiqilmoqda.",
        "ru": "Ваша проблема рассматривается админом.",
        "en": "Your issue is being processed by an admin.",
    },
}

LANG_NAMES = {"uz": "🇺🇿 Uzb", "ru": "🇷🇺 Rus", "en": "🇬🇧 Eng"}

def t(key: str, lang: str) -> str:
    val = MSG.get(key)
    if isinstance(val, dict):
        return val.get(lang, val.get("uz", ""))
    return val

def build_lang_keyboard() -> InlineKeyboardMarkup:
    buttons = [[InlineKeyboardButton(name, callback_data=f"lang|{code}")]
               for code, name in LANG_NAMES.items()]
    return InlineKeyboardMarkup(buttons)

def build_room_keyboard(lang: str) -> InlineKeyboardMarkup:
    buttons, row = [], []
    for i in range(1, 13):
        row.append(InlineKeyboardButton(str(i), callback_data=f"room|{i}"))
        if i % 7 == 0:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    return InlineKeyboardMarkup(buttons)

# ─── Handlers ──────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(MSG["choose_lang"], reply_markup=build_lang_keyboard())
    return LANG

async def lang_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    code = query.data.split("|", 1)[1]
    context.user_data["lang"] = code
    await query.edit_message_text(t("choose_room", code), reply_markup=build_room_keyboard(code))
    return ROOM

async def room_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    room = query.data.split("|", 1)[1]
    context.user_data["room"] = room
    lang = context.user_data["lang"]
    await query.edit_message_text(t("ask_issue", lang))
    return ISSUE

async def receive_issue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    lang = context.user_data.get("lang")
    room = context.user_data.get("room")

    if not lang or not room:
        await update.message.reply_text("Iltimos, /start buyrug'ini bosib, formani to'liq to'ldiring.")
        return ConversationHandler.END

    text = update.message.text_html or "(no text)"
    ticket_id = f"{user.id}_{update.message.id}"

    tickets[ticket_id] = {
        "guest_id": user.id,
        "guest_name": user.full_name,
        "guest_username": user.username,
        "room": room,
        "lang": lang,
        "text": text,
        "resolved": False,
    }
    save_db(tickets)

    payload = (
        "<b>🚨 Yangi murojaat!</b>\n"
        f"🆔 <code>{ticket_id}</code>\n"
        f"🏠 Xona: <b>{room}</b>\n"
        f"👤 <b>{user.full_name}</b> (@{user.username or 'no_username'})\n\n"
        f"✉️ {text}"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Bajardi", callback_data=f"done|{ticket_id}")]
    ])

    first_admin_msg = None
    for chat_id in ADMIN_CHAT_IDS:
        sent = await context.bot.send_message(chat_id, payload, parse_mode="HTML", reply_markup=keyboard)
        if first_admin_msg is None:
            first_admin_msg = sent

    tickets[ticket_id]["admin_msg"] = {
        "chat_id": first_admin_msg.chat_id,
        "message_id": first_admin_msg.message_id,
    }
    save_db(tickets)

    await update.message.reply_text(t("ack_guest", lang))
    return ConversationHandler.END

async def done_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()

    if user_id not in RECEPTION_USER_IDS:
        await query.answer("Faqat reception admini bajardi tugmasini bosishi mumkin.", show_alert=True)
        return

    ticket_id = query.data.split("|", 1)[1]
    ticket = tickets.get(ticket_id)

    if not ticket or ticket["resolved"]:
        return

    # Notify guest that issue is being processed
    try:
        lang = ticket.get("lang", "uz")
        await context.bot.send_message(ticket["guest_id"], t("processing_dm", lang))
    except Exception as e:
        logging.warning("Failed to notify guest that issue is being processed: %s", e)

    # Mark as resolved
    ticket["resolved"] = True
    save_db(tickets)

    # Notify group the issue is resolved
    msg_info = ticket.get("admin_msg", {})
    if msg_info:
        await context.bot.send_message(
            msg_info["chat_id"],
            f"✅ Muammo hal qilindi. (ID: {ticket_id})",
            reply_to_message_id=msg_info["message_id"],
        )

    # DM guest that it's resolved
    try:
        await context.bot.send_message(ticket["guest_id"], t("resolved_dm", lang))
    except Exception as e:
        logging.warning("Failed to notify guest that issue is resolved: %s", e)

async def pending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in BOSS_USER_IDS:
        return
    open_tickets = [(tid, tk) for tid, tk in tickets.items() if not tk["resolved"]]
    if not open_tickets:
        await update.message.reply_text("Barcha muammolar hal qilindi ✅")
        return
    lines = ["❗️ Hal bo‘lmagan muammolar:"]
    for tid, tk in open_tickets:
        lines.append(f"• ID {tid} | Xona {tk['room']} | {tk['guest_name']}: {tk['text']}")
    await update.message.reply_text("\n".join(lines))

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Bekor qilindi.")
    return ConversationHandler.END

# ─── Webhook Conflict Fix ──────────────────────────────────────────────────

async def setup_bot(app):
    await app.bot.delete_webhook(drop_pending_updates=True)

# ─── Main ──────────────────────────────────────────────────────────────────

def main():
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
    )
    app = ApplicationBuilder().token(BOT_TOKEN).post_init(setup_bot).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            LANG: [CallbackQueryHandler(lang_selected, pattern=r"^lang\|")],
            ROOM: [CallbackQueryHandler(room_selected, pattern=r"^room\|")],
            ISSUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_issue)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(done_callback, pattern=r"^done\|"))
    app.add_handler(CommandHandler("pending", pending))
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
