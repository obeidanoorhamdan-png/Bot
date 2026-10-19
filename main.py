# -*- coding: utf-8 -*-
"""
Gates Searcher - Stable Edition
No AI. No WORM. Static gateway dorks. Bing search. Playwright scan.
"""
import os
import re
import json
import time
import asyncio
import logging
import urllib.parse
import traceback
import random
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

load_dotenv()

# ============================================================
# CONFIG
# ============================================================
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "8859103411:AAHHeDZ5_7qyp6DCWoUey9U5uBhkQMjAPUM").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "6207431030").strip()

SEARCH_ENGINE   = os.getenv("SEARCH_ENGINE", "bing").lower()
SCAN_TIMEOUT_MS = int(os.getenv("SCAN_TIMEOUT_MS", "45000"))
STATS_EVERY_N   = int(os.getenv("STATS_EVERY_N", "3"))
ROUND_PAUSE_SEC = int(os.getenv("ROUND_PAUSE_SEC", "3"))
DORKS_PER_ROUND = int(os.getenv("DORKS_PER_ROUND", "2"))

DATA_DIR = Path(os.getenv("DATA_DIR", "."))
DATA_DIR.mkdir(parents=True, exist_ok=True)
RESULTS_FILE = DATA_DIR / "gates_results.json"
DORKS_FILE   = DATA_DIR / "gates_dorks.json"

DEFAULT_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("gates_bot")


# ============================================================
# STATIC DORKS - targeted at gateway signatures
# ============================================================
STATIC_DORKS = [
    'inurl:checkout "stripe" -site:stripe.com',
    'inurl:cart "js.stripe.com" -site:stripe.com',
    'inurl:shop "paypal.com/sdk" -site:paypal.com',
    'inurl:checkout "stripe.com/v3" -site:stripe.com',
    '"add to cart" "paypalobjects.com" inurl:product',
    'inurl:payment "braintreegateway" -site:braintreegateway.com',
    'inurl:checkout "js.klarna.com" -site:klarna.com',
    '"stripe.elements" inurl:checkout -site:stripe.com',
    'inurl:cart "squarecdn.com" -site:squareup.com',
    'inurl:checkout "checkout.razorpay.com" -site:razorpay.com',
    'inurl:payment "adyen-checkout" -site:adyen.com',
    'inurl:cart "afterpay" "buy now" -site:afterpay.com',
    '"paypal.buttons(" inurl:cart -site:paypal.com',
    'inurl:checkout "authorize.net" -site:authorize.net',
    'inurl:shop "checkout.flutterwave.com" -site:flutterwave.com',
    'inurl:payment "js.mollie.com" -site:mollie.com',
    '"js.stripe.com" "add to cart" site:*.co.uk',
    '"paypal.com/sdk" "buy now" site:*.de',
    '"stripe.com/v3" "shipping" site:*.fr',
    '"braintree-web" inurl:checkout site:*.com',
    'inurl:cart "google-pay-button" -site:google.com',
    'inurl:checkout "apple-pay-button" -site:apple.com',
    '"stripe" inurl:checkout site:*.co.uk',
    '"paypal" inurl:cart site:*.de',
    '"klarna" inurl:checkout site:*.se',
    '"razorpay" inurl:checkout site:*.in',
    '"mercadopago" inurl:checkout site:*.com.br',
    '"paystack" inurl:checkout site:*.ng',
    '"flutterwave" inurl:checkout site:*.ke',
    '"adyen" inurl:checkout site:*.nl',
]


# ============================================================
# JSON store
# ============================================================
def _load(path, default):
    try:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return default

def _save(path, data):
    try:
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        tmp.replace(path)
    except Exception as e:
        log.warning("save %s failed: %s", path.name, e)

def load_results():     return _load(RESULTS_FILE, [])
def save_results(r):    _save(RESULTS_FILE, r)
def load_used_dorks():  return _load(DORKS_FILE, [])
def save_used_dorks(d): _save(DORKS_FILE, d)


# ============================================================
# SEARCH
# ============================================================
def _headers():
    return {
        "User-Agent": DEFAULT_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }


def _engine_url(engine: str, dork: str) -> str:
    q = urllib.parse.quote_plus(dork)
    return {
        "bing":   f"https://www.bing.com/search?q={q}&count=30",
        "yahoo":  f"https://search.yahoo.com/search?p={q}",
        "google": f"https://www.google.com/search?q={q}&num=30",
    }.get(engine, f"https://www.bing.com/search?q={q}&count=30")


def _extract_links(html: str) -> list:
    soup = BeautifulSoup(html, "html.parser")
    urls = []
    for a in soup.find_all("a"):
        href = a.get("href", "")
        if href.startswith("/url?q="):
            clean = href.split("/url?q=")[1].split("&")[0]
            if clean.startswith("http") and "bing.com" not in clean and "microsoft.com" not in clean:
                urls.append(clean)
        elif href.startswith("http"):
            low = href.lower()
            if any(t in low for t in (
                "bing.com", "microsoft.com", "go.microsoft",
                "facebook.com", "instagram.com", "twitter.com",
                "linkedin.com", "youtube.com", "pinterest.com",
                "tiktok.com", "reddit.com",
            )):
                continue
            urls.append(href)
    return list(set(urls))


def search_dork(dork: str) -> list:
    url = _engine_url(SEARCH_ENGINE, dork)
    try:
        r = requests.get(url, headers=_headers(), timeout=25)
        r.raise_for_status()
        links = _extract_links(r.text)
        log.info("dork ok: %d links from %s", len(links), dork[:50])
        return links
    except Exception as e:
        log.warning("search failed (%s): %s", SEARCH_ENGINE, e)
        return []


# ============================================================
# GATEWAY SCANNER
# ============================================================
GATEWAY_SIGNATURES = {
    "Stripe":       ["js.stripe.com", "stripe.js/v3", "checkout.stripe.com", "pk_live_", "pk_test_"],
    "PayPal":       ["paypalobjects.com", "paypal.com/sdk/js", "paypal.buttons("],
    "Square":       ["squareup.com", "squarecdn.com", "sq-payment-form"],
    "Braintree":    ["js.braintreegateway.com", "braintree-web"],
    "Adyen":        ["checkoutshopper-live.adyen.com", "adyen-checkout"],
    "Authorize.Net":["js.authorize.net", "accept.authorize.net"],
    "Checkout.com": ["cdn.checkout.com", "js.checkout.com"],
    "Klarna":       ["js.klarna.com", "klarna-payments"],
    "Afterpay":     ["js.afterpay.com", "afterpay.js"],
    "Apple Pay":    ["apple-pay-button", "applepaysession"],
    "Google Pay":   ["pay.google.com/gp/p", "google-pay-button"],
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
]

CHECKOUT_LINK_RE = re.compile(
    r'href=["\']([^"\']*(?:checkout|cart|basket|payment|order)[^"\']*)["\']',
    re.IGNORECASE,
)


def scan_url_with_page(page, url: str, timeout_ms: int = SCAN_TIMEOUT_MS):
    found = {}
    shopify = [False]

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

    try:
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        except Exception:
            return [], False

        if shopify[0]:
            return [], True

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
                page.goto(checkout_url, wait_until="domcontentloaded", timeout=timeout_ms)
            except Exception:
                pass

    except Exception as e:
        log.warning("scan error %s: %s", url, e)
    finally:
        try:
            page.remove_listener("request", on_request)
            page.remove_listener("response", on_response)
        except Exception:
            pass

    return list(found.keys()), shopify[0]


# ============================================================
# STATE
# ============================================================
_scan = {
    "running": False,
    "stop_requested": False,
    "started_at": None,
    "chat_id": None,
    "app": None,
    "loop": None,
    "stats": {
        "hits": 0, "scraped": 0, "dorks": 0, "rounds": 0,
        "skipped_shopify": 0, "empty": 0, "errors": 0,
        "gateway_counts": {},
    },
}


def _stats_block() -> str:
    s = _scan["stats"]
    elapsed = "00:00:00"
    if _scan["started_at"]:
        secs = int((datetime.now() - _scan["started_at"]).total_seconds())
        h, rem = divmod(secs, 3600)
        m, sec = divmod(rem, 60)
        elapsed = f"{h:02d}:{m:02d}:{sec:02d}"

    top_gw = sorted(s["gateway_counts"].items(), key=lambda kv: -kv[1])[:5]
    gw_line = "\nTop: " + ", ".join(f"{g}({c})" for g, c in top_gw) if top_gw else ""

    return (
        f"Stats\n"
        f"Time: {elapsed}\n"
        f"------------------\n"
        f"Hits: {s['hits']}\n"
        f"Scraped: {s['scraped']}\n"
        f"Dorks: {s['dorks']} | Rounds: {s['rounds']}\n"
        f"Shopify skipped: {s['skipped_shopify']}\n"
        f"No gateway: {s['empty']}\n"
        f"Errors: {s['errors']}"
        + gw_line
    )


def _send_sync(text: str):
    if not (_scan["loop"] and _scan["chat_id"] and _scan["app"]):
        return
    try:
        fut = asyncio.run_coroutine_threadsafe(
            _scan["app"].bot.send_message(
                _scan["chat_id"], text, disable_web_page_preview=True
            ),
            _scan["loop"],
        )
        fut.result(timeout=15)
    except Exception as e:
        log.warning("send failed: %s", e)


# ============================================================
# MAIN LOOP
# ============================================================
def _run_scan_worker():
    from playwright.sync_api import sync_playwright

    st = _scan["stats"]
    used_dorks = load_used_dorks()
    all_results = load_results()
    found_urls = {r["url"] for r in all_results}

    available_dorks = [d for d in STATIC_DORKS if d not in used_dorks]
    random.shuffle(available_dorks)
    hits_since_stats = 0

    pw = browser = None
    try:
        pw = sync_playwright().start()
        browser = pw.chromium.launch(headless=True)
        bctx = browser.new_context(user_agent=DEFAULT_AGENT, ignore_https_errors=True)
        page = bctx.new_page()

        _send_sync(
            f"Scan started (static gateway dorks - no AI).\n"
            f"Engine: {SEARCH_ENGINE}\n"
            f"Gateway pool: {len(available_dorks)} dorks\n\n"
            f"/stop to stop."
        )

        while not _scan["stop_requested"]:
            if not available_dorks:
                _send_sync("All dorks used. Recycling with 50% overlap.")
                random.shuffle(STATIC_DORKS)
                available_dorks = STATIC_DORKS[-len(STATIC_DORKS) // 2:]

            st["rounds"] += 1
            batch = available_dorks[:DORKS_PER_ROUND]
            available_dorks = available_dorks[DORKS_PER_ROUND:]

            for d in batch:
                if d not in used_dorks:
                    used_dorks.append(d)
                    st["dorks"] += 1

            save_used_dorks(used_dorks)
            _send_sync(f"Round #{st['rounds']} - {len(batch)} dorks - hits: {st['hits']}")

            round_urls = []
            for d in batch:
                if _scan["stop_requested"]:
                    break
                try:
                    round_urls.extend(search_dork(d))
                except Exception as e:
                    st["errors"] += 1
                    log.warning("search_dork failed: %s", e)

            round_urls = list(dict.fromkeys(round_urls))
            round_urls = [u for u in round_urls if u not in found_urls]
            st["scraped"] += len(round_urls)

            if not round_urls:
                _send_sync(f"Round #{st['rounds']} - no URLs returned")
                time.sleep(ROUND_PAUSE_SEC)
                continue

            _send_sync(f"Scraping {len(round_urls)} URLs")

            for url in round_urls:
                if _scan["stop_requested"]:
                    break

                try:
                    gws, shop = scan_url_with_page(page, url)
                except Exception as e:
                    st["errors"] += 1
                    log.warning("scan failed: %s", e)
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
                _send_sync(
                    f"Hit #{st['hits']}\n"
                    f"Domain: {dom}\n"
                    f"Gateways: {', '.join(gws)}\n"
                    f"URL: {url}"
                )

                if hits_since_stats >= STATS_EVERY_N:
                    hits_since_stats = 0
                    _send_sync(_stats_block())

            time.sleep(ROUND_PAUSE_SEC)

    except Exception as outer:
        st["errors"] += 1
        log.error("worker crashed: %s\n%s", outer, traceback.format_exc())
        _send_sync(f"Worker crashed: {type(outer).__name__}: {str(outer)[:250]}")
    finally:
        try:
            if browser: browser.close()
        finally:
            if pw: pw.stop()
            _scan["running"] = False
            _send_sync("Scan stopped.\n\n" + _stats_block())


async def start_scan_async():
    if _scan["running"]:
        return
    _scan["running"] = True
    _scan["stop_requested"] = False
    _scan["started_at"] = datetime.now()
    _scan["stats"] = {
        "hits": 0, "scraped": 0, "dorks": 0, "rounds": 0,
        "skipped_shopify": 0, "empty": 0, "errors": 0,
        "gateway_counts": {},
    }
    await asyncio.to_thread(_run_scan_worker)


# ============================================================
# BOT
# ============================================================
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    _scan["chat_id"] = update.effective_chat.id
    _scan["app"] = ctx.application
    _scan["loop"] = asyncio.get_running_loop()

    await update.message.reply_text(
        f"Gates Searcher (static dorks, no AI).\n"
        f"Engine: {SEARCH_ENGINE}\n"
        f"Dorks: {len(STATIC_DORKS)} pre-loaded\n\n"
        f"/stop /stats /results"
    )
    if not _scan["running"]:
        asyncio.create_task(start_scan_async())


async def cmd_stop(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _scan["running"]:
        await update.message.reply_text("No scan running.")
        return
    _scan["stop_requested"] = True
    await update.message.reply_text("Stopping after current site.")


async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(_stats_block())


async def cmd_results(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    results = load_results()
    if not results:
        await update.message.reply_text("No results yet.")
        return
    lines = []
    for i, r in enumerate(reversed(results[-10:]), 1):
        dom = urllib.parse.urlparse(r["url"]).netloc
        lines.append(f"{i}. {dom} - {', '.join(r['gateways'])}")
    await update.message.reply_text("Last 10:\n\n" + "\n".join(lines))


# ============================================================
# PREFLIGHT + BOOT
# ============================================================
def _preflight():
    if not TELEGRAM_TOKEN:
        raise SystemExit("TELEGRAM_TOKEN missing")
    try:
        r = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getMe", timeout=15)
        if not r.json().get("ok"):
            raise SystemExit(f"Telegram getMe failed: {r.json()}")
        log.info("Telegram OK")
    except SystemExit:
        raise
    except Exception as e:
        raise SystemExit(f"Telegram unreachable: {e}")

    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            b.close()
        log.info("Playwright OK")
    except Exception as e:
        raise SystemExit(f"Chromium failed: {e}")

    log.info("PREFLIGHT OK")


async def _post_init(app):
    _scan["app"] = app
    _scan["loop"] = asyncio.get_running_loop()
    try:
        await app.bot.delete_webhook(drop_pending_updates=True)
    except Exception:
        pass
    if TELEGRAM_CHAT_ID:
        try:
            _scan["chat_id"] = int(TELEGRAM_CHAT_ID)
            asyncio.create_task(start_scan_async())
            log.info("Auto-starting for chat %s", TELEGRAM_CHAT_ID)
        except ValueError:
            log.warning("TELEGRAM_CHAT_ID invalid")


def main():
    log.info("PREFLIGHT START")
    _preflight()

    app = Application.builder().token(TELEGRAM_TOKEN).post_init(_post_init).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("results", cmd_results))

    log.info("Polling - engine=%s", SEARCH_ENGINE)
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
