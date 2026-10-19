# -*- coding: utf-8 -*-
"""
╔═══════════════════════════════════════════════════════════╗
║  ⚡ Gates Searcher — Telegram Bot · Continuous Mode     ║
║  Single-file · Gemini Flash · Playwright scanner         ║
║  Runs until stopped · Live results · Live stats          ║
╚═══════════════════════════════════════════════════════════╝
"""
import os
import re
import json
import ast
import time
import asyncio
import logging
import urllib.parse
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

load_dotenv()

# ============================================================
#                    ⚙️ الإعدادات
# ============================================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "8859103411:AAHHeDZ5_7qyp6DCWoUey9U5uBhkQMjAPUM")

WORM_API_URL = os.getenv("WORM_API_URL", "https://apiobeida.lovable.app/api/public/v1")
WORM_API_KEY = os.getenv("WORM_API_KEY", "on-sk-af8b6aabc4d9695e39b1922fbc920707744e221189f45a34")
WORM_MODEL   = os.getenv("WORM_MODEL", "worm-gpt-1.5-gemini-flash-latest")
WORM_PATH    = os.getenv("WORM_PATH", "/chat/completions")
WEB_SEARCH_ENABLED = os.getenv("WEB_SEARCH_ENABLED", "1") == "1"

BRIGHTDATA_PROXY_HOST = os.getenv("BRIGHTDATA_PROXY_HOST", "brd.superproxy.io:33335")
BRIGHTDATA_PROXY_USER = os.getenv("BRIGHTDATA_PROXY_USER", "")
BRIGHTDATA_PROXY_PASS = os.getenv("BRIGHTDATA_PROXY_PASS", "")

STATE_FILE   = Path("gates_bot_state.json")
RESULTS_FILE = Path("gates_bot_results.json")
DORKS_FILE   = Path("gates_bot_dorks.json")

DORKS_PER_ROUND  = 3
SCAN_TIMEOUT_MS  = 25000
STATS_EVERY_N    = 5      # نبعت ملخّص كل N نتيجة (نتيجة فردية دائمًا)
DEFAULT_AGENT    = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("gates_bot")


# ============================================================
#                    🗄️ تخزين JSON
# ============================================================
def _load(path: Path, default):
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return default
    return default

def _save(path: Path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

def load_state():      return _load(STATE_FILE, {})
def save_state(s):     _save(STATE_FILE, s)
def load_results():    return _load(RESULTS_FILE, [])
def save_results(r):   _save(RESULTS_FILE, r)
def load_used_dorks(): return _load(DORKS_FILE, [])
def save_used_dorks(d):_save(DORKS_FILE, d)


# ============================================================
#                    🧠 WORM AI CALL
# ============================================================
EXCLUSION_BLOCK = (
    " -site:myshopify.com -inurl:checkouts -site:medium.com -site:youtube.com"
    " -site:facebook.com -site:instagram.com -site:twitter.com -site:linkedin.com"
    " -site:reddit.com -site:pinterest.com -site:tiktok.com -site:stackoverflow.com"
    " -site:wordpress.org -site:github.com -inurl:blog -inurl:docs -inurl:tutorial"
    " -inurl:help -inurl:support -inurl:wiki"
)

_web_search_state = {"enabled": WEB_SEARCH_ENABLED}


def _worm_post(payload: dict) -> dict:
    url = WORM_API_URL.rstrip("/") + WORM_PATH
    headers = {
        "Authorization": f"Bearer {WORM_API_KEY}",
        "Content-Type": "application/json",
    }
    r = requests.post(url, headers=headers, json=payload, timeout=90)
    if r.status_code >= 400:
        raise RuntimeError(f"WORM {r.status_code}: {r.text[:300]}")
    return r.json()


def _extract_content(data: dict) -> str:
    if isinstance(data, dict):
        if "choices" in data and data["choices"]:
            msg = data["choices"][0].get("message") or {}
            if msg.get("content"):
                return msg["content"].strip()
        for key in ("content", "response", "text", "output", "message"):
            if isinstance(data.get(key), str):
                return data[key].strip()
    return ""


def worm_chat(prompt: str, system: str = "You output strictly valid JSON arrays. No markdown.") -> str:
    base_payload = {
        "model": WORM_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.8,
        "max_tokens": 500,
    }
    if _web_search_state["enabled"]:
        payload = dict(base_payload)
        payload["tools"] = [{"google_search": {}}]
        try:
            data = _worm_post(payload)
            content = _extract_content(data)
            if content:
                return content
        except Exception as e:
            log.warning(f"web search rejected: {e} — falling back")
            _web_search_state["enabled"] = False

    try:
        data = _worm_post(base_payload)
        return _extract_content(data)
    except Exception as e:
        log.error(f"worm_chat plain: {e}")
        return ""


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```json"): text = text[7:]
    elif text.startswith("```"):   text = text[3:]
    if text.endswith("```"):       text = text[:-3]
    return text.strip()


def _parse_json_lenient(text: str):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    if "[" in text and "]" in text:
        arr = text[text.find("["): text.rfind("]") + 1]
        try:
            return ast.literal_eval(arr)
        except Exception:
            return None
    return None


def apply_exclusions(dork: str) -> str:
    return dork + EXCLUSION_BLOCK


def generate_dorks(used_dorks: list, num: int = DORKS_PER_ROUND) -> list:
    used_block = ""
    if used_dorks:
        clean = [d.split(" -site:")[0] if " -site:" in d else d for d in used_dorks[-10:]]
        used_block = "\nDO NOT REPEAT:\n" + "\n".join(f"- {d}" for d in clean) + "\n"

    prompt = f"""You are an expert Google Dorker. {('Use Google Search to verify currently active niches/TLDs in 2025-2026 e-commerce.' if _web_search_state['enabled'] else '')}

Find REAL e-commerce shops with active checkout. Generate {num} UNIQUE dorks combining:
- SHOPPING intent: "add to cart", "buy now", "checkout", inurl:product, inurl:shop, inurl:cart, inurl:store, inurl:order, "price", "shipping", "delivery"
- A SPECIFIC NICHE (different per dork): clothing, electronics, supplements, cosmetics, furniture, courses, hosting, auto parts, jewelry, pet supplies, books, toys, sports equipment, organic food, baby products, shoes, watches, software, home decor, garden tools, fitness equipment, musical instruments, art supplies, phone accessories, gaming, health products, vintage items, handmade crafts
- COUNTRY target (different per dork): site:*.uk, site:*.de, site:*.com.br, site:*.fr, site:*.au, site:*.nl, site:*.it, site:*.es, site:*.ca, site:*.in, site:*.com, site:*.co.uk, site:*.com.au, site:*.co.za, site:*.se, site:*.no, site:*.dk, site:*.at, site:*.ch, site:*.ie, site:*.nz, site:*.ae, site:*.sa, site:*.sg, site:*.jp, site:*.kr, site:*.mx, site:*.ar, site:*.cl, site:*.co, site:*.ng, site:*.ke
{used_block}
RULES:
1. Different niche AND country per dork.
2. Under 80 chars. NO -site: operators.
3. No payment gateway names.
4. Return JSON array: [{{"dork": "...", "confidence": 8}}]
5. NO markdown. ONLY JSON.
"""
    content = worm_chat(prompt)
    if not content:
        return [{"dork": apply_exclusions('inurl:shop "add to cart"'), "confidence": 5}]

    parsed = _parse_json_lenient(_strip_fences(content))
    if isinstance(parsed, list) and parsed:
        cleaned = []
        for item in parsed:
            if isinstance(item, dict) and item.get("dork"):
                item["dork"] = apply_exclusions(item["dork"])
                cleaned.append(item)
            elif isinstance(item, str):
                cleaned.append({"dork": apply_exclusions(item), "confidence": 5})
        if cleaned:
            return cleaned
    return [{"dork": apply_exclusions('inurl:shop "add to cart"'), "confidence": 5}]


# ============================================================
#                    🔎 محرك البحث
# ============================================================
BLOCK_TERMS = (
    "myshopify.com", "/checkouts/", "medium.com", "blog",
    "tutorial", "docs", "/api", "stackoverflow.com", "developer",
    "facebook.com", "instagram.com", "twitter.com", "linkedin.com",
)


def _headers(agent=None):
    return {
        "User-Agent": agent or DEFAULT_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }


def _engine_url(engine: str, dork: str) -> str:
    q = urllib.parse.quote_plus(dork)
    return {
        "bing":       f"https://www.bing.com/search?q={q}&count=30",
        "yahoo":      f"https://search.yahoo.com/search?p={q}",
        "yandex":     f"https://yandex.com/search/?text={q}",
        "ask":        f"https://www.ask.com/web?q={q}",
        "duckduckgo": f"https://html.duckduckgo.com/html/?q={q}",
    }.get(engine, f"https://www.google.com/search?q={q}&num=30")


def _is_noise(url: str, engine: str) -> bool:
    if not url or not url.startswith("http"):
        return True
    low = url.lower()
    if engine in low:
        return True
    return any(t in low for t in BLOCK_TERMS)


def _extract_links(html: str, engine: str) -> list:
    soup = BeautifulSoup(html, "html.parser")
    urls = []
    for a in soup.find_all("a"):
        href = a.get("href", "")
        if href.startswith("/url?q="):
            clean = href.split("/url?q=")[1].split("&")[0]
            if not _is_noise(clean, engine):
                urls.append(clean)
        elif href.startswith("http") and not _is_noise(href, engine):
            urls.append(href)
    return list(set(urls))


def _brightdata_proxies():
    if not (BRIGHTDATA_PROXY_USER and BRIGHTDATA_PROXY_PASS):
        return None
    proxy = f"http://{BRIGHTDATA_PROXY_USER}:{BRIGHTDATA_PROXY_PASS}@{BRIGHTDATA_PROXY_HOST}"
    return {"http": proxy, "https": proxy}


def search_dork(dork: str, engine: str = "duckduckgo", mode: str = "free") -> list:
    url = _engine_url(engine, dork)
    try:
        if mode == "brightdata" and _brightdata_proxies():
            resp = requests.get(url, headers=_headers(), proxies=_brightdata_proxies(), verify=False, timeout=30)
        elif engine == "duckduckgo":
            resp = requests.post("https://html.duckduckgo.com/html/", data={"q": dork}, headers=_headers(), timeout=20)
        else:
            resp = requests.get(url, headers=_headers(), timeout=20)
        resp.raise_for_status()
        return _extract_links(resp.text, engine)
    except Exception as e:
        log.warning(f"search_dork ({engine}): {e}")
        return []


# ============================================================
#                    🔬 فحص البوابات
# ============================================================
GATEWAY_SIGNATURES = {
    "Stripe":       ["js.stripe.com", "stripe.js/v3", "checkout.stripe.com", "pk_live_", "pk_test_"],
    "PayPal":       ["paypalobjects.com", "paypal.com/sdk/js", "paypal.buttons(", "paypal-button-container"],
    "Square":       ["squareup.com", "squarecdn.com", "sq-payment-form", "web-payments-sdk"],
    "Braintree":    ["js.braintreegateway.com", "braintree-web", "braintree.client.create"],
    "Adyen":        ["checkoutshopper-live.adyen.com", "adyen-checkout"],
    "Authorize.Net":["js.authorize.net", "accept.authorize.net", "acceptjs"],
    "Checkout.com": ["cdn.checkout.com", "js.checkout.com", "checkout.frames"],
    "Klarna":       ["js.klarna.com", "klarna-payments", "klarna-checkout"],
    "Afterpay":     ["js.afterpay.com", "afterpay.js", "afterpay-button"],
    "Apple Pay":    ["apple-pay-button", "applepaysession"],
    "Google Pay":   ["pay.google.com/gp/p", "google-pay-button", "gpay-button"],
    "Amazon Pay":   ["static-na.payments-amazon.com", "amazon-pay-button"],
    "Razorpay":     ["checkout.razorpay.com", "razorpay.js", "rzp_live_"],
    "Mollie":       ["js.mollie.com", "mollie-components"],
    "Mercado Pago": ["sdk.mercadopago.com", "mercadopago.js"],
    "PayU":         ["secure.payu.com", "jssdk.payu.in"],
    "Flutterwave":  ["checkout.flutterwave.com", "flw-inline"],
    "Paystack":     ["js.paystack.co", "paystack.pop.setup("],
    "Tabby":        ["checkout.tabby.ai", "cdn.tabby.ai"],
    "Tamara":       ["cdn.tamara.co", "tamara-widget"],
    "PayMob":       ["accept.paymob.com"],
    "Fawry":        ["atfawry.com", "fawrypay-sdk"],
}

SHOPIFY_SIGNATURES = (
    "cdn.shopify.com", "window.shopify", "myshopify.com",
    "shopify-payment-button", "shopify-checkout-api-token",
)

COMMON_CHECKOUT_PATHS = [
    "/checkout", "/cart", "/basket", "/payment", "/pay",
    "/order", "/kasse", "/panier", "/carrello", "/warenkorb",
    "/cesta", "/module/checkout", "/index.php/checkout",
]

CHECKOUT_LINK_RE = re.compile(
    r'href=["\']([^"\']*(?:checkout|cart|basket|payment|pay(?:ment)?|order'
    r'|kasse|panier|warenkorb|cesta|carrello)[^"\']*)["\']',
    re.IGNORECASE,
)

BLOCKED_HOSTS = (
    "google.", "bing.", "yahoo.", "yandex.", "duckduckgo.",
    "facebook.", "instagram.", "twitter.", "x.com", "linkedin.",
    "youtube.", "pinterest.", "tiktok.", "reddit.", "medium.",
    "github.", "stackoverflow.", "wikipedia.",
)


def _is_blocked(url: str) -> bool:
    try:
        host = urllib.parse.urlparse(url).netloc.lower()
    except Exception:
        return True
    return any(b in host for b in BLOCKED_HOSTS)


def scan_url_with_page(page, url: str, timeout_ms: int = SCAN_TIMEOUT_MS):
    found = {}
    shopify = [False]
    handlers = []

    def check(text):
        if not text: return
        low = text.lower()
        for sig in SHOPIFY_SIGNATURES:
            if sig in low:
                shopify[0] = True
                return
        for gw, sigs in GATEWAY_SIGNATURES.items():
            for sig in sigs:
                if sig in low:
                    found[gw] = found.get(gw, 0) + 1
                    break

    def on_request(req):
        check(req.url)
        if req.post_data: check(req.post_data)
        for v in req.headers.values(): check(v)

    def on_response(resp):
        check(resp.url)
        for v in resp.headers.values(): check(v)

    page.on("request", on_request)
    page.on("response", on_response)
    handlers = [(on_request, on_response)]

    try:
        try:
            page.goto(url, wait_until="networkidle", timeout=timeout_ms)
        except Exception:
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=12000)
            except Exception:
                return [], False

        if shopify[0]: return [], True

        base = urllib.parse.urlparse(url)
        base_origin = f"{base.scheme}://{base.netloc}"
        checkout_url = None

        try:
            html = page.content()
            for match in CHECKOUT_LINK_RE.findall(html):
                full = urllib.parse.urljoin(url, match)
                if urllib.parse.urlparse(full).netloc == base.netloc:
                    checkout_url = full
                    break
        except Exception:
            pass

        if not checkout_url:
            for path in COMMON_CHECKOUT_PATHS:
                test_url = base_origin + path
                try:
                    resp = page.request.head(test_url, timeout=5000)
                    if resp.status < 400:
                        checkout_url = test_url
                        break
                except Exception:
                    continue

        if checkout_url and checkout_url != url:
            try:
                page.goto(checkout_url, wait_until="networkidle", timeout=timeout_ms)
            except Exception:
                try:
                    page.goto(checkout_url, wait_until="domcontentloaded", timeout=12000)
                except Exception:
                    pass
    except Exception as e:
        log.warning(f"scan error {url}: {e}")
    finally:
        for req_h, res_h in handlers:
            try:
                page.remove_listener("request", req_h)
                page.remove_listener("response", res_h)
            except Exception:
                pass

    if shopify[0]: return [], True
    return list(found.keys()), False


# ============================================================
#                    🤖 حالة الفحص المشتركة
# ============================================================
_scan_control = {
    "running": False,
    "stop_requested": False,
    "started_at": None,
    "stats": {
        "hits": 0,
        "scraped": 0,
        "dorks": 0,
        "rounds": 0,
        "skipped_shopify": 0,
        "empty": 0,
        "errors": 0,
        "gateway_counts": {},
    },
}


def _stats_block() -> str:
    s = _scan_control["stats"]
    elapsed = ""
    if _scan_control["started_at"]:
        secs = int((datetime.now() - _scan_control["started_at"]).total_seconds())
        m, sec = divmod(secs, 60)
        h, m = divmod(m, 60)
        elapsed = f"{h:02d}:{m:02d}:{sec:02d}"

    top_gw = sorted(s["gateway_counts"].items(), key=lambda kv: -kv[1])[:5]
    gw_line = ""
    if top_gw:
        gw_line = "\n*أعلى البوابات:* " + ", ".join(f"{g}({c})" for g, c in top_gw)

    return (
        f"📊 *إحصائية لحظية*\n"
        f"⏱ الوقت: `{elapsed or '00:00:00'}`\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"✅ مواقع مؤكدة: `{s['hits']}`\n"
        f"🔗 روابط مفحوصة: `{s['scraped']}`\n"
        f"🧠 دوركات: `{s['dorks']}`  ·  🔄 جولات: `{s['rounds']}`\n"
        f"🚫 Shopify متجاهل: `{s['skipped_shopify']}`\n"
        f"⚪ بلا بوابة: `{s['empty']}`\n"
        f"⚠️ أخطاء: `{s['errors']}`"
        + gw_line
    )


# ============================================================
#                    🤖 بوت تيليجرام
# ============================================================
SEARCH_ENGINES = ["duckduckgo", "google", "bing", "yahoo", "yandex", "ask"]
PROXY_MODES = ["free", "brightdata"]


def _kb(state: dict):
    engine = state.get("engine", "duckduckgo")
    mode   = state.get("mode", "free")
    running = _scan_control["running"]

    buttons = [
        [InlineKeyboardButton(f"🌐 محرك: {engine}", callback_data="cycle_engine")],
        [InlineKeyboardButton(f"🔌 وضع: {mode}",   callback_data="cycle_mode")],
    ]
    if running:
        buttons.append([InlineKeyboardButton("⛔ إيقاف الفحص", callback_data="stop_scan")])
    else:
        buttons.append([InlineKeyboardButton("🚀 ابدأ الفحص (مستمر)", callback_data="start_scan")])
    return InlineKeyboardMarkup(buttons)


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    state["telegram_chat_id"] = update.effective_chat.id
    save_state(state)
    await update.message.reply_text(
        "⚡ *Gates Searcher — Continuous*\n\n"
        f"الموديل: `{WORM_MODEL}`\n"
        "الفحص مستمر حتى تضغط /stop أو زر الإيقاف.\n\n"
        "*الأوامر:*\n"
        "/panel — لوحة التحكم\n"
        "/scan — بدء الفحص\n"
        "/stop — إيقاف\n"
        "/stats — إحصائيات لحظية\n"
        "/results — آخر النتائج\n"
        "/reset — تصفير الحالة\n"
        "/wipe — مسح كل النتائج\n",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_panel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎛 *لوحة التحكم*\nاضبط ثم ابدأ. زر الإيقاف يظهر أثناء الفحص.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_kb(load_state()),
    )


async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(_stats_block(), parse_mode=ParseMode.MARKDOWN)


async def cmd_results(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    results = load_results()
    if not results:
        await update.message.reply_text("لا نتائج بعد."); return
    lines = []
    for i, r in enumerate(reversed(results[-10:]), 1):
        dom = urllib.parse.urlparse(r["url"]).netloc
        gws = ", ".join(r["gateways"])
        lines.append(f"{i}. `{dom}` — {gws}")
    await update.message.reply_text(
        "*آخر 10 نتائج:*\n\n" + "\n".join(lines),
        parse_mode=ParseMode.MARKDOWN,
        disable_web_page_preview=True,
    )


async def cmd_reset(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    s = load_state(); s.pop("scanning", None); save_state(s)
    _scan_control["running"] = False
    _scan_control["stop_requested"] = False
    await update.message.reply_text("✅ تم التصفير (النتائج محفوظة).")


async def cmd_wipe(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    save_results([]); save_used_dorks([])
    await update.message.reply_text("🧹 مسح كامل للنتائج والدوركات.")


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, ctx)


async def cmd_scan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if _scan_control["running"]:
        await update.message.reply_text("⚠️ الفحص شغّال بالفعل."); return
    await update.message.reply_text("🚀 بدء الفحص المستمر... أوقفه بـ /stop.")
    asyncio.create_task(run_scan(ctx.application, update.effective_chat.id))


async def cmd_stop(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _scan_control["running"]:
        await update.message.reply_text("لا يوجد فحص جارٍ."); return
    _scan_control["stop_requested"] = True
    await update.message.reply_text(
        "⛔ تم طلب الإيقاف — يتوقف بعد الموقع الحالي.",
        reply_markup=None,
    )


async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    s = load_state()

    if q.data == "cycle_engine":
        idx = SEARCH_ENGINES.index(s.get("engine", "duckduckgo"))
        s["engine"] = SEARCH_ENGINES[(idx + 1) % len(SEARCH_ENGINES)]
        save_state(s); await q.edit_message_reply_markup(_kb(s))
    elif q.data == "cycle_mode":
        idx = PROXY_MODES.index(s.get("mode", "free"))
        s["mode"] = PROXY_MODES[(idx + 1) % len(PROXY_MODES)]
        save_state(s); await q.edit_message_reply_markup(_kb(s))
    elif q.data == "start_scan":
        if _scan_control["running"]:
            await q.edit_message_text("⚠️ الفحص شغّال بالفعل.")
            return
        await q.edit_message_text("🚀 بدء الفحص المستمر...")
        asyncio.create_task(run_scan(ctx.application, update.effective_chat.id))
    elif q.data == "stop_scan":
        _scan_control["stop_requested"] = True
        await q.edit_message_text("⛔ تم طلب الإيقاف — جاري الإنهاء النظيف.")


# ============================================================
#                    🔄 دورة الفحص المستمرة
# ============================================================
async def run_scan(app: Application, chat_id: int):
    from playwright.sync_api import sync_playwright

    if _scan_control["running"]:
        await app.bot.send_message(chat_id, "⚠️ الفحص شغّال بالفعل.")
        return

    _scan_control["running"] = True
    _scan_control["stop_requested"] = False
    _scan_control["started_at"] = datetime.now()
    _scan_control["stats"] = {
        "hits": 0, "scraped": 0, "dorks": 0, "rounds": 0,
        "skipped_shopify": 0, "empty": 0, "errors": 0,
        "gateway_counts": {},
    }

    s = load_state()
    engine = s.get("engine", "duckduckgo")
    mode   = s.get("mode", "free")

    used_dorks  = load_used_dorks()
    all_results = load_results()
    found_urls  = {r["url"] for r in all_results}

    hits_since_stats = 0

    def emit(msg: str):
        try:
            asyncio.run_coroutine_threadsafe(
                app.bot.send_message(chat_id, msg, disable_web_page_preview=True),
                app.loop,
            )
        except Exception as e:
            log.warning(f"emit: {e}")

    def emit_md(msg: str):
        try:
            asyncio.run_coroutine_threadsafe(
                app.bot.send_message(
                    chat_id, msg,
                    parse_mode=ParseMode.MARKDOWN,
                    disable_web_page_preview=True,
                ),
                app.loop,
            )
        except Exception as e:
            log.warning(f"emit_md: {e}")

    def worker():
        nonlocal hits_since_stats
        st = _scan_control["stats"]
        pw = sync_playwright().start()
        browser = pw.chromium.launch(headless=True)
        bctx = browser.new_context(user_agent=DEFAULT_AGENT, ignore_https_errors=True)
        page = bctx.new_page()

        try:
            emit("🔥 الفحص بدأ. أنا شغّال لحد ما توقفني بـ /stop.")

            while not _scan_control["stop_requested"]:
                st["rounds"] += 1
                round_no = st["rounds"]
                emit(f"━━ الجولة #{round_no} ━━  مواقع: {st['hits']}")

                try:
                    dorks_data = generate_dorks(used_dorks, num=DORKS_PER_ROUND)
                except Exception as e:
                    st["errors"] += 1
                    log.error(f"generate_dorks: {e}")
                    time.sleep(3)
                    continue

                dorks_data.sort(
                    key=lambda x: x.get("confidence", 0)
                    if isinstance(x.get("confidence"), int) else 0,
                    reverse=True,
                )
                dorks = [d["dork"] for d in dorks_data if d.get("dork")]
                new_dorks = [d for d in dorks if d not in used_dorks]

                if not new_dorks:
                    emit("↺ دوركات مكررة — جولة جديدة.")
                    time.sleep(2)
                    continue

                st["dorks"] += len(new_dorks)
                used_dorks.extend(new_dorks)
                emit(f"🧠 {len(new_dorks)} دورك جديد")

                round_urls = []
                for d in new_dorks:
                    if _scan_control["stop_requested"]:
                        break
                    try:
                        round_urls.extend(search_dork(d, engine=engine, mode=mode))
                    except Exception as e:
                        st["errors"] += 1
                        log.warning(f"search_dork: {e}")

                round_urls = [
                    u for u in dict.fromkeys(round_urls)
                    if u not in found_urls and not _is_blocked(u)
                ]
                st["scraped"] += len(round_urls)
                emit(f"🔗 {len(round_urls)} رابط جديد — فحص عميق")

                for i, url in enumerate(round_urls, 1):
                    if _scan_control["stop_requested"]:
                        break

                    try:
                        gws, shop = scan_url_with_page(page, url)
                    except Exception as e:
                        st["errors"] += 1
                        log.warning(f"scan: {e}")
                        continue

                    if shop:
                        st["skipped_shopify"] += 1
                        continue
                    if not gws:
                        st["empty"] += 1
                        continue
                    if url in found_urls:
                        continue

                    found_urls.add(url)
                    st["hits"] += 1
                    hits_since_stats += 1
                    for g in gws:
                        st["gateway_counts"][g] = st["gateway_counts"].get(g, 0) + 1

                    all_results.append({
                        "url": url,
                        "gateways": gws,
                        "timestamp": datetime.now().isoformat(),
                    })
                    save_results(all_results)

                    dom = urllib.parse.urlparse(url).netloc
                    emit_md(
                        f"✅ *موقع #{st['hits']}*\n"
                        f"🌐 `{dom}`\n"
                        f"🔐 {', '.join(gws)}\n"
                        f"🔗 {url}"
                    )

                    # إحصائية تفصيلية كل STATS_EVERY_N نتيجة
                    if hits_since_stats >= STATS_EVERY_N:
                        hits_since_stats = 0
                        emit_md(_stats_block())

                save_used_dorks(used_dorks)

                if _scan_control["stop_requested"]:
                    break

        finally:
            try:
                browser.close()
            finally:
                pw.stop()
                _scan_control["running"] = False
                final = _stats_block()
                emit_md("🛑 *تم الإيقاف النهائي.*\n\n" + final)
                st = load_state()
                st["scanning"] = False
                save_state(st)

    await asyncio.to_thread(worker)


# ============================================================
#                    ▶️ التشغيل
# ============================================================
def main():
    if not TELEGRAM_TOKEN:
        raise SystemExit("TELEGRAM_TOKEN missing in .env")

    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("panel", cmd_panel))
    app.add_handler(CommandHandler("scan", cmd_scan))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("results", cmd_results))
    app.add_handler(CommandHandler("reset", cmd_reset))
    app.add_handler(CommandHandler("wipe", cmd_wipe))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CallbackQueryHandler(on_callback))

    log.info(f"Bot starting — model={WORM_MODEL}")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
