# -*- coding: utf-8 -*-
"""
Gates Searcher - Autonomous Telegram Bot
Auto-start. Continuous scan. Live results.
"""
import os
import re
import json
import ast
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

from telegram import Update, Bot
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

load_dotenv()

# ============================================================
# CONFIG
# ============================================================
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "8859103411:AAHHeDZ5_7qyp6DCWoUey9U5uBhkQMjAPUM").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "6207431030").strip()

WORM_API_URL = os.getenv("WORM_API_URL", "https://apiobeida.lovable.app/api/public/v1")
WORM_API_KEY = os.getenv("WORM_API_KEY", "").strip()
WORM_MODEL   = os.getenv("WORM_MODEL", "worm-gpt-1.5-gemini-flash-latest")
WORM_PATH    = os.getenv("WORM_PATH", "/chat/completions")
WEB_SEARCH_ENABLED = os.getenv("WEB_SEARCH_ENABLED", "1") == "1"

BRIGHTDATA_PROXY_HOST = os.getenv("BRIGHTDATA_PROXY_HOST", "brd.superproxy.io:33335")
BRIGHTDATA_PROXY_USER = os.getenv("BRIGHTDATA_PROXY_USER", "").strip()
BRIGHTDATA_PROXY_PASS = os.getenv("BRIGHTDATA_PROXY_PASS", "").strip()

SEARCH_ENGINE = os.getenv("SEARCH_ENGINE", "duckduckgo").lower()
SEARCH_MODE   = os.getenv("SEARCH_MODE", "free").lower()

DATA_DIR = Path(os.getenv("DATA_DIR", "."))
DATA_DIR.mkdir(parents=True, exist_ok=True)

STATE_FILE   = DATA_DIR / "gates_bot_state.json"
RESULTS_FILE = DATA_DIR / "gates_bot_results.json"
DORKS_FILE   = DATA_DIR / "gates_bot_dorks.json"

DORKS_PER_ROUND = int(os.getenv("DORKS_PER_ROUND", "3"))
SCAN_TIMEOUT_MS = int(os.getenv("SCAN_TIMEOUT_MS", "25000"))
STATS_EVERY_N   = int(os.getenv("STATS_EVERY_N", "5"))
ROUND_PAUSE_SEC = int(os.getenv("ROUND_PAUSE_SEC", "2"))

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
logging.getLogger("telegram").setLevel(logging.INFO)
log = logging.getLogger("gates_bot")


# ============================================================
# JSON STORE
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

def load_state():       return _load(STATE_FILE, {})
def save_state(s):      _save(STATE_FILE, s)
def load_results():     return _load(RESULTS_FILE, [])
def save_results(r):    _save(RESULTS_FILE, r)
def load_used_dorks():  return _load(DORKS_FILE, [])
def save_used_dorks(d): _save(DORKS_FILE, d)


# ============================================================
# WORM AI CALL
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
        raise RuntimeError(f"WORM HTTP {r.status_code}: {r.text[:200]}")
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
            log.warning("web search rejected (%s), falling back", e)
            _web_search_state["enabled"] = False

    try:
        data = _worm_post(base_payload)
        return _extract_content(data)
    except Exception as e:
        log.error("worm_chat plain failed: %s", e)
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

    prompt = f"""You are an expert Google Dorker. {('Use Google Search to verify currently active niches/TLDs in 2025-2026.' if _web_search_state['enabled'] else '')}

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
# SEARCH
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
            resp = requests.get(url, headers=_headers(), proxies=_brightdata_proxies(),
                                verify=False, timeout=30)
        elif engine == "duckduckgo":
            resp = requests.post("https://html.duckduckgo.com/html/",
                                 data={"q": dork}, headers=_headers(), timeout=20)
        else:
            resp = requests.get(url, headers=_headers(), timeout=20)
        resp.raise_for_status()
        return _extract_links(resp.text, engine)
    except Exception as e:
        log.warning("search_dork (%s) failed: %s", engine, e)
        return []


# ============================================================
# GATEWAY SCANNER
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
        log.warning("scan failed for %s: %s", url, e)
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
# SCAN STATE
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
    gw_line = ""
    if top_gw:
        gw_line = "\nTop gateways: " + ", ".join(f"{g}({c})" for g, c in top_gw)

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
        + gw_line
    )


def _send_sync(text: str):
    """Send from worker thread - safe."""
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
# SCAN LOOP
# ============================================================
def _run_scan_worker():
    from playwright.sync_api import sync_playwright

    st = _scan["stats"]
    engine = SEARCH_ENGINE
    mode   = SEARCH_MODE

    used_dorks  = load_used_dorks()
    all_results = load_results()
    found_urls  = {r["url"] for r in all_results}

    hits_since_stats = 0

    pw = browser = None
    try:
        pw = sync_playwright().start()
        browser = pw.chromium.launch(headless=True)
        bctx = browser.new_context(user_agent=DEFAULT_AGENT, ignore_https_errors=True)
        page = bctx.new_page()

        _send_sync(
            f"Scan started.\n"
            f"Engine: {engine} | Mode: {mode}\n"
            f"Model: {WORM_MODEL}\n\n"
            f"One result per site. Full stats every {STATS_EVERY_N} hits.\n"
            f"/stop to stop."
        )

        while not _scan["stop_requested"]:
            st["rounds"] += 1
            _send_sync(f"Round #{st['rounds']} - hits: {st['hits']}")

            try:
                dorks_data = generate_dorks(used_dorks, num=DORKS_PER_ROUND)
            except Exception as e:
                st["errors"] += 1
                log.error("generate_dorks failed: %s", e)
                _send_sync(f"Dork generation failed: {type(e).__name__}")
                time.sleep(5)
                continue

            dorks_data.sort(
                key=lambda x: x.get("confidence", 0)
                if isinstance(x.get("confidence"), int) else 0,
                reverse=True,
            )
            dorks = [d["dork"] for d in dorks_data if d.get("dork")]
            new_dorks = [d for d in dorks if d not in used_dorks]

            if not new_dorks:
                time.sleep(ROUND_PAUSE_SEC)
                continue

            st["dorks"] += len(new_dorks)
            used_dorks.extend(new_dorks)
            save_used_dorks(used_dorks)

            round_urls = []
            for d in new_dorks:
                if _scan["stop_requested"]:
                    break
                try:
                    round_urls.extend(search_dork(d, engine=engine, mode=mode))
                except Exception as e:
                    st["errors"] += 1
                    log.warning("search_dork failed: %s", e)

            round_urls = [
                u for u in dict.fromkeys(round_urls)
                if u not in found_urls and not _is_blocked(u)
            ]
            st["scraped"] += len(round_urls)

            if not round_urls:
                _send_sync(f"Round #{st['rounds']} - no new URLs")
                time.sleep(ROUND_PAUSE_SEC)
                continue

            _send_sync(f"{len(round_urls)} new URLs - deep scanning")

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

                entry = {
                    "url": url,
                    "gateways": gws,
                    "timestamp": datetime.now().isoformat(),
                }
                all_results.append(entry)
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

            if _scan["stop_requested"]:
                break

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
# BOT HANDLERS
# ============================================================
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    _scan["chat_id"] = update.effective_chat.id
    _scan["app"] = ctx.application
    _scan["loop"] = asyncio.get_running_loop()

    await update.message.reply_text(
        f"Gates Searcher active.\n"
        f"Model: {WORM_MODEL}\n"
        f"Engine: {SEARCH_ENGINE} | Mode: {SEARCH_MODE}\n\n"
        f"Commands:\n"
        f"/stop - stop scan\n"
        f"/stats - live stats\n"
        f"/results - last hits"
    )

    if not _scan["running"]:
        asyncio.create_task(start_scan_async())


async def cmd_stop(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _scan["running"]:
        await update.message.reply_text("No scan running.")
        return
    _scan["stop_requested"] = True
    await update.message.reply_text("Stop requested - finishing current site.")


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
def _preflight_telegram():
    if not TELEGRAM_TOKEN:
        raise SystemExit("TELEGRAM_TOKEN missing")
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getMe", timeout=15
        )
        data = r.json()
        if not data.get("ok"):
            raise SystemExit(f"Telegram getMe failed: {data}")
        log.info("Telegram OK - bot @%s (id %s)",
                 data["result"].get("username"), data["result"].get("id"))
    except SystemExit:
        raise
    except Exception as e:
        raise SystemExit(f"Telegram unreachable: {e}")


def _preflight_chat():
    if not TELEGRAM_CHAT_ID:
        log.info("TELEGRAM_CHAT_ID not set - will set on /start")
        return
    try:
        chat_id = int(TELEGRAM_CHAT_ID)
    except ValueError:
        raise SystemExit(f"TELEGRAM_CHAT_ID not numeric: {TELEGRAM_CHAT_ID}")
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": "bot preflight OK"},
            timeout=15,
        )
        data = r.json()
        if not data.get("ok"):
            raise SystemExit(
                f"Telegram sendMessage failed: {data.get('description')}\n"
                f"Tip: open Telegram, find your bot, press START."
            )
        log.info("Chat %s reachable", chat_id)
    except SystemExit:
        raise
    except Exception as e:
        raise SystemExit(f"Chat check failed: {e}")


def _preflight_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SystemExit("playwright not installed -> pip install playwright")
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            b.close()
        log.info("Playwright OK")
    except Exception as e:
        raise SystemExit(
            f"Chromium launch failed: {e}\n"
            f"Fix: playwright install --with-deps chromium"
        )


def _preflight_worm():
    if not WORM_API_KEY:
        log.warning("WORM_API_KEY missing - dorks will fall back to defaults")
        return
    try:
        r = requests.post(
            WORM_API_URL.rstrip("/") + WORM_PATH,
            headers={
                "Authorization": f"Bearer {WORM_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": WORM_MODEL,
                "messages": [{"role": "user", "content": "Reply with: OK"}],
                "max_tokens": 10,
                "temperature": 0.1,
            },
            timeout=30,
        )
        if r.status_code >= 400:
            log.warning("WORM preflight HTTP %s: %s", r.status_code, r.text[:150])
        else:
            log.info("WORM API OK")
    except Exception as e:
        log.warning("WORM preflight failed: %s", e)


async def _post_init(app: Application):
    """Called after bot is initialized but before polling starts."""
    _scan["app"] = app
    _scan["loop"] = asyncio.get_running_loop()

    # Delete any webhook left behind - avoids getUpdates conflict
    try:
        await app.bot.delete_webhook(drop_pending_updates=True)
        log.info("Webhook cleared")
    except Exception as e:
        log.warning("delete_webhook failed: %s", e)

    if TELEGRAM_CHAT_ID:
        try:
            _scan["chat_id"] = int(TELEGRAM_CHAT_ID)
        except ValueError:
            log.warning("TELEGRAM_CHAT_ID invalid: %s", TELEGRAM_CHAT_ID)
            return
        log.info("Auto-starting scan for chat %s", TELEGRAM_CHAT_ID)
        asyncio.create_task(start_scan_async())
    else:
        log.info("No TELEGRAM_CHAT_ID - waiting for /start")


def main():
    log.info("=== PREFLIGHT START ===")
    _preflight_telegram()
    _preflight_playwright()
    _preflight_worm()
    _preflight_chat()
    log.info("=== PREFLIGHT OK ===")

    app = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .post_init(_post_init)
        .build()
    )
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("results", cmd_results))

    log.info("Polling started - model=%s engine=%s mode=%s",
             WORM_MODEL, SEARCH_ENGINE, SEARCH_MODE)
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
