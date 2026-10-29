"""bot.py — نقطة التشغيل + الراوتر المركزي
تشغيل:  pip install -r requirements.txt  ثم  python bot.py
"""
import asyncio
import os
import re
import sys
import threading
from datetime import time as dtime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from telegram import Update
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters)

import services
import f_admin as A
import f_drops as D
import f_market as M
import f_user as U
from config import *  # noqa
from database import db
from services import *  # noqa


# ═══════════════════════════════════════════════════════════════════
# 🌐 Health server
# ═══════════════════════════════════════════════════════════════════
class HealthHandler(BaseHTTPRequestHandler):
    def _ok(self, body=True):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        if body:
            self.wfile.write(b"Worm GPT is alive!")

    def do_GET(self):
        if self.path in ("/", "/health", "/ping"):
            self._ok()
        else:
            self.send_response(404); self.end_headers()

    def do_HEAD(self):
        self._ok(body=False)

    def log_message(self, *a):
        pass


def start_http_server():
    port = int(os.environ.get("PORT", "8080"))
    try:
        server = ThreadingHTTPServer(("0.0.0.0", port), HealthHandler)
    except OSError as e:
        logger.warning(f"تعذر تشغيل HTTP server: {e}"); return
    threading.Thread(target=server.serve_forever, daemon=True).start()
    logger.info(f"🌐 HTTP server على المنفذ {port}")


# ═══════════════════════════════════════════════════════════════════
# 🧭 جداول التوجيه
# ═══════════════════════════════════════════════════════════════════
async def show_home(update, context):
    await U.show_home(update, context)


services.go_home_hook = show_home      # يستعمله المعالج التفاعلي

NAV = {   # شاشات عامة: تلغي أي عملية جارية
    BTN_HOME: U.show_home, BTN_SHOP: U.show_shop, BTN_SUBS: U.show_subs, BTN_ACTIVE_SUBS: U.show_subs,
    BTN_ARCHIVE: U.show_archive, BTN_WALLET: U.show_wallet, BTN_EARN: U.show_earn, BTN_TOP: U.show_top,
    BTN_GUIDE: U.show_guide, BTN_CHECK: U.check_code_begin, BTN_ABOUT: U.show_about, BTN_STATUS: U.show_status,
    BTN_LANG: U.show_lang, BTN_THEME: U.show_theme, BTN_NOTIF: U.show_notif, BTN_BAZAAR: M.show_bazaar,
    BTN_CALC: U.calc_begin, BTN_UDROPS: D.user_drops_view, BTN_REDEEM: D.redeem_begin,
    BTN_MK_BROWSE: M.show_browse, BTN_MK_SELL: M.start_sell, BTN_MK_MINE: M.show_mine,
    BTN_MK_COMPLAIN: M.start_complaint, BTN_MK_LEDGER: M.show_ledger,
}
ACTIONS = {BTN_DAILY: U.do_daily, BTN_INVITE: U.show_invite, BTN_MY_ID: U.show_my_id, BTN_TRANSFER: U.transfer_begin}

ADMIN_NAV = {
    BTN_ADMIN: A.show_admin, BTN_DASH: A.show_dashboard, BTN_KEYS: A.show_keys, BTN_CHANNELS: A.show_channels,
    BTN_DROPS: D.show_drops_menu, BTN_MARKET: M.show_market_admin, BTN_ANALYTICS: A.show_analytics_menu,
    BTN_MONITOR: A.show_monitor_menu, BTN_LOGS: A.show_logs, BTN_BROADCAST: A.show_broadcast_menu,
    BTN_D_NORMAL: lambda u, c: D.start_drop_wizard(u, c, "normal"),
    BTN_D_EGG: lambda u, c: D.start_drop_wizard(u, c, "egg"),
    BTN_D_COND: lambda u, c: D.start_drop_wizard(u, c, "cond"),
    BTN_D_CHAIN: lambda u, c: D.start_drop_wizard(u, c, "chain"),
    BTN_D_POINTS: lambda u, c: D.start_drop_wizard(u, c, "points"),
    BTN_D_VOTE: lambda u, c: wiz_begin(u, c, "vote"),
    BTN_D_AUCTION: lambda u, c: wiz_begin(u, c, "auction"),
    BTN_D_ALL: D.show_drops_all, BTN_D_SCHED: D.show_drops_scheduled, BTN_D_STATS: D.show_drop_stats,
    BTN_M_PRODUCTS: M.show_products, BTN_M_COMM: M.show_commissions, BTN_M_COMPLAINTS: M.show_complaints,
    BTN_A_USERS: A.analytics_users, BTN_A_REV: A.analytics_revenue, BTN_A_DROPS: A.analytics_drops,
    BTN_A_EXPORT: A.show_export_menu,
    BTN_MON_SYS: A.show_system, BTN_MON_ALERTS: A.show_alerts, BTN_MON_BACKUP: A.show_backup,
    BTN_B_USERS: A.bc_users_begin, BTN_B_CHANNEL: A.bc_channel_begin,
    BTN_CH_ADD: A.channel_add_begin, BTN_ADD_KEY: A.key_add_begin,
}

MENUS = {
    "main": U.show_home, "shop": U.show_shop, "wallet": U.show_wallet, "earn": U.show_earn, "subs": U.show_subs,
    "bazaar": M.show_bazaar, "admin": A.show_admin, "drops": D.show_drops_menu, "market": M.show_market_admin,
    "analytics": A.show_analytics_menu, "monitor": A.show_monitor_menu, "channels": A.show_channels,
    "keys": A.show_keys, "broadcast": A.show_broadcast_menu,
}
PARENT = {"shop": "main", "wallet": "main", "earn": "main", "subs": "main", "bazaar": "main", "admin": "main",
          "drops": "admin", "market": "admin", "analytics": "admin", "monitor": "admin", "channels": "admin",
          "keys": "admin", "broadcast": "admin", "redeem": "earn", "key_add_value": "keys", "key_add_label": "keys"}


async def handle_cancel(update, context, state):
    if state == "wiz":
        await wiz_cancel(update, context)
    elif state == "buy_custom_qty":
        context.user_data["state"] = "buy_qty"
        await say(update, "🔢 اختر عدداً:", kb_quantity())
    elif state in ("key_add_value", "key_add_label"):
        await A.show_keys(update, context)
    elif state == "redeem":
        await U.show_earn(update, context)
    else:
        await U.show_home(update, context)


async def handle_back(update, context, state):
    ctx = context.user_data.get("buy", {})
    if state == "wiz":
        await wiz_back(update, context)
    elif state == "buy_days":
        await U.show_shop(update, context)
    elif state == "buy_qty":
        await (U.buy_choose_type(update, context, ctx["type"]) if ctx.get("type") else U.show_shop(update, context))
    elif state in ("buy_confirm", "buy_custom_qty"):
        await (U.buy_choose_days(update, context, ctx["days"]) if ctx.get("type") and ctx.get("days") else U.show_shop(update, context))
    else:
        await MENUS[PARENT.get(state, "main")](update, context)


@user_locked
async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    user = await guard(update, context)
    if not user:
        return
    text = canon(update.message.text.strip())
    uid = update.effective_user.id
    admin = is_admin(uid)
    state = context.user_data.get("state", "main")
    try:
        if text == BTN_CANCEL:
            await handle_cancel(update, context, state); return
        if text == BTN_BACK:
            await handle_back(update, context, state); return
        if text in NAV:
            reset_flow(context)
            await NAV[text](update, context); return
        if text in ADMIN_NAV:
            if not admin:
                await say(update, "🚫 للمشرفين فقط", kb_main(False)); return
            reset_flow(context, state if state in ("drops", "market", "analytics", "monitor", "channels", "keys", "broadcast", "admin") else "admin")
            await ADMIN_NAV[text](update, context); return
        if text in ACTIONS:
            await ACTIONS[text](update, context); return

        if state == "wiz":
            await wiz_input(update, context, text); return
        if state == "redeem":
            await D.redeem_receive(update, context, text); return
        if admin and state == "key_add_value":
            await A.key_add_value(update, context, text); return
        if admin and state == "key_add_label":
            await A.key_add_label(update, context, text); return

        # ── المتجر ──
        if text in PLAN_BUTTONS and state in ("shop", "buy_days"):
            await U.buy_choose_type(update, context, PLAN_BUTTONS[text]); return
        if state == "buy_days":
            m = re.match(r"\s*(\d+)", text)
            if m:
                await U.buy_choose_days(update, context, int(m.group(1))); return
        if state == "buy_qty":
            if text in QTY_BUTTONS:
                await U.buy_choose_qty(update, context, QTY_BUTTONS[text]); return
            if text == BTN_QTY_CUSTOM:
                context.user_data["state"] = "buy_custom_qty"
                await say(update, f"✏️ أرسل عدد الأكواد (1-{MAX_QTY}):", kb_cancel()); return
        if state == "buy_custom_qty":
            if text.isdigit() and 1 <= int(text) <= MAX_QTY:
                await U.buy_choose_qty(update, context, int(text)); return
            await say(update, f"❌ رقم بين 1 و {MAX_QTY}:", kb_cancel()); return
        if state == "buy_confirm":
            if text == BTN_CONFIRM:
                await U.buy_confirm(update, context); return
            if text == BTN_EDIT_QTY:
                context.user_data["state"] = "buy_qty"
                await say(update, "🔢 اختر عدداً جديداً:", kb_quantity()); return

        await say(update, "🤔 استخدم الأزرار بالأسفل.", kb_main(admin))
    except Exception:
        logger.exception("[TEXT] خطأ")
        reset_flow(context)
        try:
            await say(update, "❌ حدث خطأ غير متوقع، حاول مجدداً.", kb_main(admin))
        except Exception:
            pass


async def noop_callback(update, context):
    await update.callback_query.answer()


async def error_handler(update, context):
    logger.error("خطأ غير معالج", exc_info=context.error)
    try:
        await raise_alert(context.bot, "warning", "exception", f"استثناء: {type(context.error).__name__}: {str(context.error)[:200]}", 15)
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════
# 🎬 التشغيل
# ═══════════════════════════════════════════════════════════════════
async def post_init(app):
    RUNTIME["app"] = app
    logger.info(f"💾 DB_PATH = {DB_PATH}")
    await db.init()
    await api.start()

    if not await db.list_channels() and CHANNEL_USERNAME:
        await db.add_channel(CHANNEL_USERNAME, None, "", "both")
        logger.info(f"📢 زُرعت القناة الافتراضية @{CHANNEL_USERNAME}")
    for ch in await db.list_channels():              # استكمال chat_id/العنوان تلقائياً
        if not ch["chat_id"]:
            try:
                chat = await app.bot.get_chat(f"@{ch['username']}")
                await db.update_channel(ch["id"], chat_id=chat.id, title=chat.title or "")
            except Exception as e:
                logger.warning(f"تعذر تحديد @{ch['username']}: {e}")

    if INITIAL_API_KEY and not await db.find_key_by_value(INITIAL_API_KEY):
        kid = await db.add_api_key(INITIAL_API_KEY, INITIAL_API_KEY_LABEL, SUPER_ADMIN_ID)
        if not await db.get_active_key():
            await db.set_active_key(kid)
        logger.info("✅ أُضيف المفتاح الأولي")
    active = await db.get_active_key()
    if active:
        _, msg = await api.test_key(active["key_value"])
        logger.info(f"🔑 المفتاح النشط: {msg}")
    else:
        logger.warning("⚠️ لا يوجد مفتاح نشط — أضفه من لوحة المشرف")

    jq = app.job_queue
    if jq is None:
        logger.warning("⚠️ JobQueue غير متوفر: pip install 'python-telegram-bot[job-queue]' — الجدولة والتنبيهات معطّلة")
    else:
        jq.run_repeating(D.scheduler_tick, interval=20, first=10)
        jq.run_repeating(D.periodic_refresh, interval=DROPS_REFRESH_INTERVAL, first=90)
        jq.run_repeating(A.monitor_job, interval=300, first=60)
        jq.run_daily(U.expiry_reminder_job, time=dtime(10, 0))
        jq.run_daily(A.backup_job, time=dtime(3, 0))
        logger.info("⏱️ المهام المجدولة: نشر/إغلاق، تحديث، مراقبة، تذكير، نسخ احتياطي")
    logger.info("✅ Worm GPT جاهز!")


async def post_shutdown(app):
    await api.close()
    await db.close()


def main():
    if not BOT_TOKEN:
        print("❌ ضع BOT_TOKEN في متغيرات البيئة"); sys.exit(1)
    if not ADMIN_IDS:
        print("❌ ضع SUPER_ADMIN_ID في متغيرات البيئة"); sys.exit(1)
    start_http_server()
    app = (Application.builder().token(BOT_TOKEN).concurrent_updates(True)
           .post_init(post_init).post_shutdown(post_shutdown).build())
    private = filters.ChatType.PRIVATE
    app.add_handler(CommandHandler("start", U.start, filters=private))
    app.add_handler(CommandHandler("cancel", U.cancel_cmd, filters=private))
    app.add_handler(CommandHandler("admin", A.admin_cmd, filters=private))
    app.add_handler(CommandHandler("ban", A.ban_cmd, filters=private))
    app.add_handler(CommandHandler("unban", A.unban_cmd, filters=private))
    app.add_handler(CommandHandler("addpoints", A.addpoints_cmd, filters=private))
    app.add_handler(CommandHandler("userinfo", A.userinfo_cmd, filters=private))

    cb = app.add_handler
    cb(CallbackQueryHandler(U.verify_join_callback, pattern=r"^verify_join$"))
    cb(CallbackQueryHandler(D.drop_status_callback, pattern=r"^drop_status_\d+$"))
    cb(CallbackQueryHandler(D.drop_refresh_callback, pattern=r"^drop_refresh_\d+$"))
    cb(CallbackQueryHandler(D.poll_callback, pattern=r"^poll_(vote|refresh):"))
    cb(CallbackQueryHandler(D.auction_callback, pattern=r"^auc_(bid|refresh):"))
    cb(CallbackQueryHandler(U.settings_callback, pattern=r"^set_(lang|theme|notif):"))
    cb(CallbackQueryHandler(U.widget_callback, pattern=r"^widget:"))
    cb(CallbackQueryHandler(U.subs_callback, pattern=r"^subs_pg:"))
    cb(CallbackQueryHandler(M.market_user_callback, pattern=r"^mk_"))
    cb(CallbackQueryHandler(D.admin_drop_callback, pattern=r"^adm_(dpg|drop_cancel|auc_cancel|poll_cancel)"))
    cb(CallbackQueryHandler(M.market_admin_callback, pattern=r"^adm_(mk_|cmp_)"))
    cb(CallbackQueryHandler(A.admin_callback, pattern=r"^adm_(dash|key_|ch_|export|alerts_seen|mon|bk_)"))
    cb(CallbackQueryHandler(noop_callback, pattern=r"^noop$"))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & private, text_router))
    app.add_error_handler(error_handler)
    logger.info("🚀 بدء Worm GPT...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
