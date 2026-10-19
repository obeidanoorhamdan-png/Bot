# -*- coding: utf-8 -*-
"""
Gates Searcher - Final Edition
Google Custom Search (10 pages) + Playwright deep scan.
Auto-start Telegram bot.
"""
import os
import re
import json
import time
import asyncio
import logging
import urllib.parse
import traceback
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
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

# Google CSE - direct keys
GOOGLE_CSE_KEY   = os.getenv("GOOGLE_CSE_KEY", "AIzaSyCxBFcawOclW9DRGt-lDNavzvzYFFsKykU").strip()
GOOGLE_CSE_CX    = os.getenv("GOOGLE_CSE_CX", "464c96d9e9f4d4eae").strip()
CSE_NUM_PAGES    = int(os.getenv("CSE_NUM_PAGES", "10"))
CSE_PER_PAGE     = 10

SCAN_TIMEOUT_MS  = int(os.getenv("SCAN_TIMEOUT_MS", "45000"))
STATS_EVERY_N    = int(os.getenv("STATS_EVERY_N", "3"))
ROUND_PAUSE_SEC  = int(os.getenv("ROUND_PAUSE_SEC", "30"))
DORKS_PER_ROUND  = int(os.getenv("DORKS_PER_ROUND", "1"))

DATA_DIR = Path(os.getenv("DATA_DIR", "."))
DATA_DIR.mkdir(parents=True, exist_ok=True)
RESULTS_FILE = DATA_DIR / "gates_results.json"
DORKS_FILE   = DATA_DIR / "gates_dorks.json"

DEFAULT_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("gates_bot")


# ============================================================
# STATIC DORK POOL
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
    '"stripe" "add to cart" site:*.ca',
    '"paypal" "checkout" site:*.au',
    '"braintree" inurl:cart site:*.it',
    '"square" inurl:checkout site:*.es',
    '"klarna" inurl:cart site:*.no',
    '"checkout.com" inurl:checkout site:*.dk',
    '"adyen" inurl:checkout site:*.at',
    '"stripe" "buy now" site:*.ch',
    '"paypal" "order" site:*.ie',
    '"stripe" "cart" site:*.nz',
    '"tabby" inurl:checkout site:*.ae',
    '"tamara" inurl:checkout site:*.sa',
    '"paymob" inurl:checkout site:*.eg',
    '"fawry" inurl:checkout site:*.eg',
    '"razorpay" inurl:cart site:*.in',
    '"paytm" inurl:checkout site:*.in',
    '"mercadopago" inurl:cart site:*.ar',
    '"mercadopago" inurl:checkout site:*.mx',
    '"webpay" inurl:checkout site:*.cl',
    '"flow" inurl:checkout site:*.cl',
]


# ============================================================
# JSON store
# ============================================================
def _load(path: Path, default):
    try:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        log.warning("load %s failed: %s", path.name, e)
    return default


def _save(path: Path, data):
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
# GOOGLE CSE
# ============================================================
_cse_quota_exhausted = False


def search_dork(dork: str, num_pages: int = None) -> list:
    global _cse_quota_exhausted

    if not (GOOGLE_CSE_KEY and GOOGLE_CSE_CX):
        log.warning("GOOGLE_CSE_KEY or GOOGLE_CSE_CX missing")
        return []

    if _cse_quota_exhausted:
        return []

    pages = num_pages or CSE_NUM_PAGES
    all_links = []
    pages_done = 0

    for page in range(1, pages + 1):
        start_index = (page - 1) * CSE_PER_PAGE + 1
        try:
            r = requests.get(
                "https://www.googleapis.com/customsearch/v1",
                params={
                    "q": dork,
                    "cx": GOOGLE_CSE_CX,
                    "key": GOOGLE_CSE_KEY,
                    "start": start_index,
                    "num": CSE_PER_PAGE,
                },
                timeout=25,
            )

            if r.status_code == 429:
                _cse_quota_exhausted = True
                log.warning("CSE quota exhausted at page %d", page)
                break

            r.raise_for_status()
            data = r.json()

            if "error" in data:
                msg = data["error"].get("message", "")[:150]
                log.warning("CSE error page %d: %s", page, msg)
                if data["error"].get("code") == 429:
                    _cse_quota_exhausted = True
                break

            items = data.get("items", [])
            if not items:
                log.info("CSE: empty page %d for %s", page, dork[:50])
                break

            for item in items:
                link = item.get("link")
                if link:
                    all_links.append(link)

            pages_done = page

        except Exception as e:
            log.warning("CSE page %d failed: %s", page, e)
            break

    unique = list(dict.fromkeys(all_links))
    log.info("CSE: %d unique URLs (%d pages) for %s",
             len(unique), pages_done, dork[:60])
    return unique


# ============================================================
# GATEWAY SIGNATURES
# ============================================================
GATEWAY_SIGNATURES = {
    "Stripe":         ["js.stripe.com", "stripe.js/v3", "checkout.stripe.com", "pk_live_", "pk_test_"],
    "PayPal":         ["paypalobjects.com", "paypal.com/sdk/js", "paypal.buttons("],
    "Square":         ["squareup.com", "squarecdn.com", "sq-payment-form"],
    "Braintree":      ["js.braintreegateway.com", "braintree-web"],
    "Adyen":          ["checkoutshopper-live.adyen.com", "adyen-checkout"],
    "Authorize.Net":  ["js.authorize.net", "accept.authorize.net"],
    "Checkout.com":   ["cdn.checkout.com", "js.checkout.com"],
    "Klarna":         ["js.klarna.com", "klarna-payments"],
    "Afterpay":       ["js.afterpay.com", "afterpay.js"],
    "Apple Pay":      ["apple-pay-button", "applepaysession"],
    "Google Pay":     ["pay.google.com/gp/p", "google-pay-button"],
    "Amazon Pay":     ["static-na.payments-amazon.com", "amazon-pay-button"],
    "Razorpay":       ["checkout.razorpay.com", "razorpay.js", "rzp_live_"],
    "Mollie":         ["js.mollie.com", "mollie-components"],
    "Mercado Pago":   ["sdk.mercadopago.com", "mercadopago.js"],
    "PayU":           ["secure.payu.com", "jssdk.payu.in"],
    "Flutterwave":    ["checkout.flutterwave.com", "flw-inline"],
    "Paystack":       ["js.paystack.co", "paystack.pop.setup("],
    "Tabby":          ["checkout.tabby.ai", "cdn.tabby.ai"],
    "Tamara":         ["cdn.tamara.co", "tamara-widget"],
    "PayMob":         ["accept.paymob.com"],
    "Fawry":          ["atfawry.com", "fawrypay-sdk"],
    "PayJunction":    ["payjunction.com", "payjunctionlabs.com", "pjcheckout"],
    "Worldpay":       ["worldpay.com", "secure.worldpay.com"],
    "GoCardless":     ["gocardless.com", "pay.gocardless.com"],
    "2Checkout":      ["2checkout.com", "secure.2co.com"],
    "Paysafe":        ["paysafe.com", "hosted.paysafe.com"],
    "Payoneer":       ["payoneer.com", "checkout.payoneer.com"],
    "Skrill":         ["skrill.com", "pay.skrill.com"],
    "Neteller":       ["neteller.com", "pay.neteller.com"],
    "BitPay":         ["bitpay.com", "bitpay-checkout"],
    "Coinbase":       ["commerce.coinbase.com", "coinbase-commerce"],
    "Iyzico":         ["iyzico.com", "iyzipay"],
    "PayTR":          ["paytr.com", "paytr-checkout"],
    "Sberbank":       ["sberbank.ru", "sberpay"],
    "YooKassa":       ["yookassa.ru", "yoomoney"],
    "Robokassa":      ["robokassa.ru", "auth.robokassa"],
    "Perfect Money":  ["perfectmoney.com", "perfectmoney.is"],
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
    r'href=["\']([^"\']*(?:checkout|cart|basket|payment|order|kasse|panier)[^"\']*)["\']',
    re.IGNORECASE,
)


def _is_boring_host(url: str) -> bool:
    try:
        host = urllib.parse.urlparse(url).netloc.lower()
    except Exception:
        return True
    return any(b in host for b in (
        "google.", "bing.", "yahoo.", "yandex.", "duckduckgo.",
        "facebook.", "instagram.", "twitter.", "x.com", "linkedin.",
        "youtube.", "pinterest.", "tiktok.", "reddit.", "medium.",
        "github.", "stackoverflow.", "wikipedia.", "microsoft.com",
    ))


def scan_url_with_page(page, url: str, timeout_ms: int = SCAN_TIMEOUT_MS):
    found = {}
    shopify = [False]

    def check(text):
        if not text:
            return
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
        if req.post_data:
            check(req.post_data)
        for v in req.headers.values():
            check(v)

    def on_response(resp):
        check(resp.url)
        for v in resp.headers.values():
            check(v)

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

    top_gw = sorted(s["gateway_counts"].items(), key=lambda kv: -kv[1])[:6]
    gw_line = ("\nTop gateways:\n" + "\n".join(f"  {g}: {c}" for g, c in top_gw)) if top_gw else ""
    quota_note = "\n[CSE quota exhausted - searching stopped]" if _cse_quota_exhausted else ""

    return (
        f"Live stats\n"
        f"Time: {elapsed}\n"
        f"------------------\n"
        f"Hits: {s['hits']}\n"
        f"Scraped: {s['scraped']}\n"
        f"Dorks: {s['dorks']} | Rounds: {s['rounds']}\n"
        f"Shopify skipped: {s['skipped_shopify']}\n"
        f"No gateway: {s['empty']}\n"
        f"Errors: {s['errors']}"
        f"{gw_line}"
        f"{quota_note}"
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
        fut.result(timeout=20)
    except Exception as e:
        log.warning("send failed: %s", e)


# ============================================================
# SCAN LOOP
# ============================================================
def _run_scan_worker():
    from playwright.sync_api import sync_playwright

    st = _scan["stats"]
    used_dorks = load_used_dorks()
    all_results = load_results()
    found_urls = {r["url"] for r in all_results}

    pool = [d for d in STATIC_DORKS if d not in used_dorks]
    hits_since_stats = 0

    pw = browser = None
    try:
        pw = sync_playwright().start()
        browser = pw.chromium.launch(headless=True)
        bctx = browser.new_context(user_agent=DEFAULT_AGENT, ignore_https_errors=True)
        page = bctx.new_page()

        _send_sync(
            f"Gates Searcher started.\n"
            f"CSE pages/dork: {CSE_NUM_PAGES}\n"
            f"Dorks in pool: {len(pool)}\n"
            f"Dorks/round: {DORKS_PER_ROUND}\n\n"
            f"/stop /stats"
        )

        while not _scan["stop_requested"]:
            if not pool:
                _send_sync("Dork pool finished. Recycling oldest 30.")
                used_dorks = used_dorks[-30:] if len(used_dorks) > 30 else []
                save_used_dorks(used_dorks)
                pool = [d for d in STATIC_DORKS if d not in used_dorks]

            if _cse_quota_exhausted:
                _send_sync("Google CSE daily quota exhausted.\n"
                           "Search paused until restart.")
                time.sleep(600)
                continue

            st["rounds"] += 1
            batch = pool[:DORKS_PER_ROUND]
            pool = pool[DORKS_PER_ROUND:]

            for d in batch:
                if d not in used_dorks:
                    used_dorks.append(d)
                    st["dorks"] += 1
            save_used_dorks(used_dorks)

            _send_sync(f"Round #{st['rounds']} - {len(batch)} dork - hits: {st['hits']}")

            round_urls = []
            for d in batch:
                if _scan["stop_requested"] or _cse_quota_exhausted:
                    break
                try:
                    round_urls.extend(search_dork(d))
                except Exception as e:
                    st["errors"] += 1
                    log.warning("search_dork failed: %s", e)

            round_urls = list(dict.fromkeys(round_urls))
            round_urls = [u for u in round_urls
                          if u not in found_urls and not _is_boring_host(u)]
            st["scraped"] += len(round_urls)

            if not round_urls:
                _send_sync(f"Round #{st['rounds']} - no new URLs")
                time.sleep(ROUND_PAUSE_SEC)
                continue

            _send_sync(f"Scanning {len(round_urls)} URLs")

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
            if browser:
                browser.close()
        finally:
            if pw:
                pw.stop()
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
        f"Gates Searcher\n"
        f"CSE pages/dork: {CSE_NUM_PAGES}\n"
        f"Dork pool: {len(STATIC_DORKS)}\n"
        f"Gateways tracked: {len(GATEWAY_SIGNATURES)}\n\n"
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
    await update.message.reply_text("Last 10 hits:\n\n" + "\n".join(lines))


# ============================================================
# PREFLIGHT + BOOT
# ============================================================
def _preflight():
    if not TELEGRAM_TOKEN:
        raise SystemExit("TELEGRAM_TOKEN missing")
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getMe", timeout=15
        )
        if not r.json().get("ok"):
            raise SystemExit(f"Telegram getMe failed: {r.json()}")
        log.info("Telegram OK")
    except SystemExit:
        raise
    except Exception as e:
        raise SystemExit(f"Telegram unreachable: {e}")

    if not (GOOGLE_CSE_KEY and GOOGLE_CSE_CX):
        log.warning("CSE keys missing - search will fail")

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

    log.info("Polling started")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
