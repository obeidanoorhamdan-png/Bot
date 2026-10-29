"""services.py — API، لوحات المفاتيح، الحماية، المعالج التفاعلي (Wizard)، النشر، التنبيهات"""
import asyncio
import inspect
import time

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden, RetryAfter

from config import *  # noqa
from database import DBError, db  # noqa


# ═══════════════════════════════════════════════════════════════════
# 🌐 عميل API
# ═══════════════════════════════════════════════════════════════════
def extract_code(resp):
    if isinstance(resp, dict):
        data = resp.get("data")
        if isinstance(data, dict) and data.get("code"):
            return str(data["code"])
    return None


class ApiClient:
    def __init__(self):
        self.client = None

    async def start(self):
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0),
                                        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})

    async def close(self):
        if self.client:
            await self.client.aclose()

    async def _call(self, method, **kwargs):
        active = await db.get_active_key()
        if not active:
            return {"ok": False, "error": "no_key", "message": "❌ لا يوجد مفتاح نشط"}
        headers = {"Authorization": f"Bearer {active['key_value'].strip()}"}
        last_err = None
        for attempt in range(3):
            t0 = time.monotonic()
            try:
                r = await self.client.request(method, API_URL, headers=headers, **kwargs)
            except httpx.ConnectError as e:
                last_err = str(e)
                await db.log_api(False, 0, int((time.monotonic() - t0) * 1000))
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
            except httpx.TimeoutException:
                await db.log_api(False, 0, int((time.monotonic() - t0) * 1000))
                if method == "GET":
                    last_err = "timeout"
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue
                await db.record_key_result(active["id"], False)
                return {"ok": False, "error": "timeout", "message": "⏱️ انتهت المهلة"}
            except Exception as e:
                await db.record_key_result(active["id"], False)
                return {"ok": False, "error": "unknown", "message": f"⚠️ {e}"}
            ms = int((time.monotonic() - t0) * 1000)
            await db.log_api(r.status_code < 400, r.status_code, ms)
            if r.status_code >= 500 and method == "GET":
                last_err = f"http_{r.status_code}"
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
            if r.status_code < 400:
                await db.record_key_result(active["id"], True)
                if r.status_code == 204 or not r.content:
                    return {"ok": True, "data": {"success": True}}
                try:
                    return {"ok": True, "data": r.json()}
                except ValueError:
                    return {"ok": False, "error": "bad_json", "message": "⚠️ استجابة غير مفهومة"}
            await db.record_key_result(active["id"], False)
            msgs = {401: "🔴 المفتاح غير صالح (401)", 403: "🔴 المفتاح لا يملك الصلاحية (403)",
                    404: "❌ الكود غير موجود (404)", 429: "⏳ الخدمة مزدحمة، حاول بعد قليل (429)"}
            msg = msgs.get(r.status_code) or (f"❌ طلب خاطئ (400): {r.text[:200]}" if r.status_code == 400
                                              else f"❌ خطأ HTTP {r.status_code}")
            return {"ok": False, "error": f"http_{r.status_code}", "message": msg}
        await db.record_key_result(active["id"], False)
        return {"ok": False, "error": last_err or "unknown", "message": "⚠️ تعذر الوصول للخدمة، حاول لاحقاً"}

    async def create(self, plan, days, max_uses=1):
        return await self._call("POST", json={"plan": plan, "days": days, "max_uses": max_uses})

    async def check_code(self, code):
        return await self._call("GET", params={"code": code})

    async def code_state(self, code):
        """يعيد (valid: bool|None, info: dict|None, message). valid=None عند تعذر الفحص"""
        res = await self.check_code(code)
        if not res["ok"]:
            return None, None, res.get("message", "")
        data = res["data"]
        info = data.get("data") if isinstance(data, dict) and isinstance(data.get("data"), dict) else data
        if not isinstance(info, dict):
            return None, None, "استجابة غير مفهومة"
        uses, mx, status = info.get("uses", 0), info.get("max_uses"), str(info.get("status", "")).lower()
        if status in ("used", "expired", "revoked", "disabled"):
            return False, info, status
        if isinstance(uses, int) and isinstance(mx, int) and uses >= mx:
            return False, info, "used"
        return True, info, status

    async def test_key(self, key_value):
        try:
            r = await self.client.get(API_URL, headers={"Authorization": f"Bearer {key_value.strip()}"}, timeout=15)
            if r.status_code in (401, 403):
                return False, f"🔴 غير صالح ({r.status_code})"
            if r.status_code >= 500:
                return False, f"🟠 الخدمة تعطي {r.status_code}"
            return True, "🟢 صالح"
        except Exception as e:
            return False, f"⚠️ {e}"


api = ApiClient()


# ═══════════════════════════════════════════════════════════════════
# ⌨️ لوحات المفاتيح
# ═══════════════════════════════════════════════════════════════════
def mk_kb(rows, persistent=True):
    return ReplyKeyboardMarkup([[KeyboardButton(tb(b)) for b in r] for r in rows],
                               resize_keyboard=True, is_persistent=persistent)


def kb_main(admin=False):
    rows = [[BTN_SHOP, BTN_SUBS], [BTN_WALLET, BTN_EARN], [BTN_BAZAAR, BTN_TOP], [BTN_CHECK, BTN_STATUS],
            [BTN_GUIDE, BTN_ABOUT], [BTN_LANG, BTN_THEME, BTN_NOTIF]]
    if admin:
        rows.append([BTN_ADMIN])
    return mk_kb(rows)


def days_rows(sub_type):
    days = list(SUBSCRIPTION_TYPES[sub_type]["prices"].keys())
    return [[f"{d} يوم" for d in days[i:i + 2]] for i in range(0, len(days), 2)]


def nav_row():
    return [BTN_BACK, BTN_HOME]


def kb_shop(): return mk_kb([[BTN_PLUS, BTN_PRO, BTN_PREMIUM], nav_row()])
def kb_days(t): return mk_kb(days_rows(t) + [nav_row()])
def kb_quantity(): return mk_kb([[BTN_QTY_1, BTN_QTY_2], [BTN_QTY_3, BTN_QTY_5], [BTN_QTY_10, BTN_QTY_CUSTOM], nav_row()])
def kb_confirm(edit=True): return mk_kb([[BTN_CONFIRM]] + ([[BTN_EDIT_QTY]] if edit else []) + [[BTN_CANCEL, BTN_HOME]])
def kb_wallet(): return mk_kb([[BTN_TRANSFER, BTN_DAILY], [BTN_CALC, BTN_INVITE], [BTN_MY_ID], nav_row()])
def kb_earn(): return mk_kb([[BTN_DAILY, BTN_INVITE], [BTN_UDROPS, BTN_REDEEM], [BTN_MY_ID], nav_row()])
def kb_subs(): return mk_kb([[BTN_ACTIVE_SUBS, BTN_ARCHIVE], [BTN_BAZAAR], nav_row()])
def kb_bazaar(): return mk_kb([[BTN_MK_BROWSE, BTN_MK_SELL], [BTN_MK_MINE, BTN_MK_COMPLAIN], [BTN_MK_LEDGER], nav_row()])
def kb_cancel(): return mk_kb([[BTN_CANCEL]], persistent=False)
def kb_confirm_cancel(): return mk_kb([[BTN_CONFIRM], [BTN_CANCEL]], persistent=False)


def kb_admin():
    return mk_kb([[BTN_DASH, BTN_KEYS], [BTN_CHANNELS, BTN_DROPS], [BTN_MARKET, BTN_ANALYTICS],
                  [BTN_MONITOR, BTN_LOGS], [BTN_BROADCAST], nav_row()])


def kb_drops_menu():
    return mk_kb([[BTN_D_NORMAL, BTN_D_EGG], [BTN_D_COND, BTN_D_CHAIN], [BTN_D_VOTE, BTN_D_AUCTION], [BTN_D_POINTS],
                  [BTN_D_ALL, BTN_D_SCHED], [BTN_D_STATS], nav_row()])


def kb_market_admin(): return mk_kb([[BTN_M_PRODUCTS, BTN_M_COMM], [BTN_M_COMPLAINTS], nav_row()])
def kb_analytics(): return mk_kb([[BTN_A_USERS, BTN_A_REV], [BTN_A_DROPS, BTN_A_EXPORT], nav_row()])
def kb_monitor(): return mk_kb([[BTN_MON_SYS, BTN_MON_ALERTS], [BTN_MON_BACKUP], nav_row()])
def kb_broadcast(): return mk_kb([[BTN_B_USERS, BTN_B_CHANNEL], nav_row()])
def kb_channels(): return mk_kb([[BTN_CH_ADD], nav_row()])
def kb_keys(): return mk_kb([[BTN_ADD_KEY], nav_row()])


def kb_force_join(channels):
    rows = [[InlineKeyboardButton(f"📢 @{c['username']}", url=f"https://t.me/{c['username']}")] for c in channels]
    rows.append([InlineKeyboardButton("✅ " + ("Verify" if CUR_LANG.get() == "en" else "تحقق من الانضمام"),
                                      callback_data="verify_join")])
    return InlineKeyboardMarkup(rows)


# ═══════════════════════════════════════════════════════════════════
# 🧰 أدوات الإرسال والحالة
# ═══════════════════════════════════════════════════════════════════
async def say(update, text, kb=None):
    return await update.effective_chat.send_message(
        tr(text), parse_mode=ParseMode.HTML, reply_markup=kb, disable_web_page_preview=True)


FLOW_KEYS = ("buy", "transfer_to", "transfer_amount", "new_key_value", "wiz", "pending_calc")


def reset_flow(context, state="main"):
    for k in FLOW_KEYS:
        context.user_data.pop(k, None)
    context.user_data["state"] = state


def user_locked(fn):
    """يمنع تداخل رسائل نفس المستخدم (مهم مع concurrent_updates).
    🔧 الأقفال تُحفظ في app.bot_data (لا تُمسح بـ reset_flow) بدل user_data."""
    import functools

    @functools.wraps(fn)
    async def wrapper(update, context, *a, **kw):
        app = getattr(context, "application", None)
        if app is None:                       # لا يوجد application (حالة نادرة) — نفّذ بدون قفل
            return await fn(update, context, *a, **kw)
        locks = app.bot_data.setdefault("_locks", {})
        uid = update.effective_user.id if update.effective_user else 0
        lock = locks.setdefault(uid, asyncio.Lock())
        async with lock:
            return await fn(update, context, *a, **kw)
    return wrapper


# ═══════════════════════════════════════════════════════════════════
# 🔒 Force Join (قنوات متعددة) + الحماية
# ═══════════════════════════════════════════════════════════════════
async def check_force_join(context, user_id, use_cache=True):
    """يعيد (ok, missing_channels)"""
    if is_admin(user_id):
        return True, []
    chans = await db.list_channels("join", active_only=True)
    if not chans:
        return True, []
    if use_cache:
        cache = await db.get_force_join_cache(user_id)
        if cache and cache["is_member"] == 1:
            t = parse_dt(cache["checked_at"])
            if t and (datetime.now() - t).total_seconds() < JOIN_CACHE_SECONDS:
                return True, []
    missing = []
    for ch in chans:
        target = ch["chat_id"] or f"@{ch['username']}"
        try:
            m = await context.bot.get_chat_member(chat_id=target, user_id=user_id)
            ok = m.status in ("member", "administrator", "creator") or (
                m.status == "restricted" and getattr(m, "is_member", False))
            if not ok:
                missing.append(ch)
        except Exception as e:
            logger.error(f"فشل فحص العضوية في @{ch['username']} (البوت يجب أن يكون مشرفاً): {e}")
    if missing:
        await db.delete_force_join_cache(user_id)
    else:
        await db.save_force_join_cache(user_id, True)
    return (not missing), missing


async def show_join_prompt(update, missing):
    names = "\n".join(f"📢 <b>@{esc(c['username'])}</b>" for c in missing)
    if CUR_LANG.get() == "en":
        text = f"🔒 <b>Welcome to Worm GPT</b>\n\nPlease join our channel(s) first:\n\n{names}\n\nThen press ✅ Verify"
    else:
        text = f"🔒 <b>مرحباً بك في Worm GPT</b>\n\nللاستفادة من البوت، يجب الانضمام أولاً:\n\n{names}\n\nبعد الانضمام اضغط ✅ تحقق"
    await update.effective_chat.send_message(text, parse_mode=ParseMode.HTML, reply_markup=kb_force_join(missing))


async def guard(update, context, register=True):
    """يعيد dict المستخدم إن كان مسموحاً له، وإلا None"""
    tg = update.effective_user
    if not tg:
        return None
    user = await db.get_user(tg.id)
    apply_prefs(user)
    if user and user["is_banned"]:
        await say(update, "🚫 تم حظرك من استخدام البوت.")
        return None
    ok, missing = await check_force_join(context, tg.id)
    if not ok:
        await show_join_prompt(update, missing)
        return None
    if not user and register:
        user, _, _ = await db.register_user(tg.id, tg.username, tg.first_name)
        apply_prefs(user)
    elif user:
        if user["username"] != tg.username or user["first_name"] != tg.first_name:
            await db.refresh_profile(tg.id, tg.username, tg.first_name)
        la = parse_dt(user.get("last_active")) if user.get("last_active") else None
        if not la or (datetime.now() - la).total_seconds() > 600:
            await db.touch_active(tg.id)
    return user


async def cb_guard(update):
    """حماية للأزرار inline: يعيد المستخدم أو None (ويجيب على الاستعلام)"""
    q = update.callback_query
    user = await db.get_user(q.from_user.id)
    apply_prefs(user)
    if not user:
        await q.answer("ابدأ البوت أولاً: /start", show_alert=True)
        return None
    if user["is_banned"]:
        await q.answer("🚫 محظور", show_alert=True)
        return None
    return user


# ═══════════════════════════════════════════════════════════════════
# 📡 النشر في القنوات والإشعارات والتنبيهات
# ═══════════════════════════════════════════════════════════════════
def chan_target(ch):
    return ch["chat_id"] or f"@{ch['username']}"


async def publish_to_channels(bot, entity, entity_id, text, markup=None):
    """ينشر في كل قنوات النشر النشطة. يعيد (عدد النجاح، قائمة الأخطاء)"""
    ok, errors = 0, []
    for ch in await db.list_channels("publish", active_only=True):
        try:
            sent = await bot.send_message(chat_id=chan_target(ch), text=text, parse_mode=ParseMode.HTML,
                                          reply_markup=markup, disable_web_page_preview=True)
            if entity:
                await db.add_post(entity, entity_id, sent.chat_id, sent.message_id)
            ok += 1
        except RetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
            errors.append(f"@{ch['username']}: flood")
        except Exception as e:
            errors.append(f"@{ch['username']}: {e}")
            logger.warning(f"فشل النشر في @{ch['username']}: {e}")
    return ok, errors


async def refresh_posts(bot, entity, entity_id, text, markup=None):
    any_ok = False
    for p in await db.get_posts(entity, entity_id):
        try:
            await bot.edit_message_text(chat_id=p["chat_id"], message_id=p["message_id"], text=text,
                                        parse_mode=ParseMode.HTML, reply_markup=markup, disable_web_page_preview=True)
            any_ok = True
        except RetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
        except Exception as e:
            err = str(e).lower()
            if "not modified" in err:
                any_ok = True
            elif "not found" in err or "can't be edited" in err or "chat not found" in err:
                await db.delete_post(p["id"])
            else:
                await db.post_failed(p["id"])
                logger.warning(f"تحديث منشور {entity}#{entity_id}: {e}")
    return any_ok


def post_link(chat_username, message_id):
    return f"https://t.me/{chat_username}/{message_id}" if chat_username else None


async def notify_users(bot, ids, text, markup=None, parse_mode=ParseMode.HTML):
    ok = 0
    for uid in ids:
        for _ in range(2):
            try:
                await bot.send_message(uid, text, parse_mode=parse_mode, reply_markup=markup, disable_web_page_preview=True)
                ok += 1
                break
            except RetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
            except (Forbidden, BadRequest):
                break
            except Exception:
                break
        await asyncio.sleep(0.05)
    return ok


async def notify_admins(bot, text):
    for aid in ADMIN_IDS:
        try:
            await bot.send_message(aid, text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        except Exception:
            pass


async def raise_alert(bot, level, kind, message, dedupe_minutes=60):
    """ينشئ تنبيهاً ويرسله للمشرفين (مع منع التكرار)"""
    if await db.recent_alert(kind, dedupe_minutes):
        return False
    await db.add_alert(level, kind, message)
    icon = {"critical": "🚨", "warning": "⚠️", "info": "ℹ️"}.get(level, "⚠️")
    await notify_admins(bot, f"{icon} <b>تنبيه نظام</b>\n\n{esc(message)}")
    return True


def spawn(context_or_app, coro):
    app = getattr(context_or_app, "application", context_or_app)
    return app.create_task(coro)


# ═══════════════════════════════════════════════════════════════════
# 🧙 المعالج التفاعلي (Wizard)
# ═══════════════════════════════════════════════════════════════════
class Step:
    def __init__(self, key, prompt, parse, kb=None, when=None):
        self.key, self.prompt, self.parse, self.kb, self.when = key, prompt, parse, kb, when


WIZARDS = {}


def register_wizard(name, steps, on_done, exit_to, summary=None):
    WIZARDS[name] = {"steps": steps, "on_done": on_done, "exit_to": exit_to, "summary": summary}


def choice_kb(rows):
    return mk_kb(list(rows) + [[BTN_BACK, BTN_CANCEL]], persistent=False)


def _applicable(spec, data, i):
    st = spec["steps"][i]
    return st.when is None or st.when(data)


async def wiz_begin(update, context, name, data=None):
    context.user_data["state"] = "wiz"
    context.user_data["wiz"] = {"name": name, "idx": -1, "data": dict(data or {}), "confirm": False}
    await wiz_advance(update, context)


async def wiz_advance(update, context):
    w = context.user_data["wiz"]
    spec = WIZARDS[w["name"]]
    i = w["idx"] + 1
    while i < len(spec["steps"]) and not _applicable(spec, w["data"], i):
        i += 1
    if i >= len(spec["steps"]):
        if spec["summary"] is None:
            await wiz_finish(update, context)
            return
        w["confirm"] = True
        w["idx"] = len(spec["steps"])
        await say(update, await _maybe(spec["summary"](w["data"])), kb_confirm_cancel())
        return
    w["idx"] = i
    st = spec["steps"][i]
    prompt = st.prompt(w["data"]) if callable(st.prompt) else st.prompt
    await say(update, prompt, st.kb(w["data"]) if st.kb else mk_kb([[BTN_BACK, BTN_CANCEL]], persistent=False))


async def _maybe(v):
    return await v if inspect.isawaitable(v) else v


async def wiz_input(update, context, text):
    w = context.user_data.get("wiz")
    if not w:
        reset_flow(context)
        return
    spec = WIZARDS[w["name"]]
    if w["confirm"]:
        if text == BTN_CONFIRM:
            await wiz_finish(update, context)
        else:
            await say(update, "اضغط ✅ تأكيد أو ❌ إلغاء")
        return
    st = spec["steps"][w["idx"]]
    try:
        val = await _maybe(st.parse(text, w["data"]))
    except ValueError as e:
        await say(update, f"❌ {esc(e)}")
        return
    w["data"][st.key] = val
    await wiz_advance(update, context)


async def wiz_back(update, context):
    w = context.user_data.get("wiz")
    if not w:
        return await go_home_hook(update, context)
    spec = WIZARDS[w["name"]]
    i = min(w["idx"], len(spec["steps"])) - 1
    while i >= 0 and not _applicable(spec, w["data"], i):
        i -= 1
    if i < 0:
        return await wiz_cancel(update, context)
    w["confirm"] = False
    w["idx"] = i - 1
    await wiz_advance(update, context)


async def wiz_cancel(update, context):
    w = context.user_data.get("wiz")
    exit_to = WIZARDS[w["name"]]["exit_to"] if w else None
    reset_flow(context)
    if exit_to:
        await exit_to(update, context)
    else:
        await go_home_hook(update, context)


async def wiz_finish(update, context):
    w = context.user_data.pop("wiz")
    spec = WIZARDS[w["name"]]
    context.user_data["state"] = "main"
    await spec["on_done"](update, context, w["data"])


async def go_home_hook(update, context):       # يُستبدل من bot.py (لتجنب الاستيراد الدائري)
    reset_flow(context)
    await say(update, "🏠 <b>القائمة الرئيسية</b>", kb_main(is_admin(update.effective_user.id)))


# ─── محللات شائعة للمعالج ───
def parse_int(lo, hi, label="الرقم"):
    def p(text, data):
        t = text.strip()
        if not t.isdigit() or not (lo <= int(t) <= hi):
            raise ValueError(f"{label} يجب أن يكون بين {lo} و {hi}")
        return int(t)
    return p


def parse_plan(text, data):
    plan = PLAN_BUTTONS.get(text)
    if not plan:
        raise ValueError("اختر الخطة من الأزرار")
    return plan


def parse_days_for(plan_key):
    def p(text, data):
        import re
        m = re.match(r"\s*(\d+)", text)
        plan = data.get(plan_key)
        if not m or int(m.group(1)) not in SUBSCRIPTION_TYPES[plan]["prices"]:
            raise ValueError("اختر المدة من الأزرار")
        return int(m.group(1))
    return p


def parse_schedule(text, data):
    """يعيد None (الآن) أو ISO للوقت المجدول"""
    t = text.strip()
    if t == BTN_NOW or t.lower() in ("now", "الآن"):
        return None
    presets = {"⏰ +10 د": 10, "⏰ +1 س": 60, "⏰ +6 س": 360, "⏰ +24 س": 1440}
    if t in presets:
        return (datetime.now() + timedelta(minutes=presets[t])).isoformat(timespec="seconds")
    if t.startswith("+") and t[1:].isdigit() and 1 <= int(t[1:]) <= 60 * 24 * 30:
        return (datetime.now() + timedelta(minutes=int(t[1:]))).isoformat(timespec="seconds")
    try:
        dt = datetime.strptime(t, "%Y-%m-%d %H:%M")
    except ValueError:
        raise ValueError("صيغة غير صحيحة. استخدم الأزرار أو +30 (دقائق) أو YYYY-MM-DD HH:MM")
    if dt <= datetime.now():
        raise ValueError("الوقت يجب أن يكون في المستقبل")
    return dt.isoformat(timespec="seconds")


SCHEDULE_PROMPT = ("⏰ <b>موعد النشر</b>\n\nاختر زراً، أو اكتب <code>+30</code> (بعد 30 دقيقة)، "
                   "أو <code>2026-12-31 18:30</code>")


def kb_schedule(data=None):
    return choice_kb([[BTN_NOW], ["⏰ +10 د", "⏰ +1 س"], ["⏰ +6 س", "⏰ +24 س"]])


def sched_text(publish_at):
    return "🚀 فوراً" if not publish_at else f"⏰ {fmt_dt(publish_at)}"


RUNTIME = {}      # يُملأ عند التشغيل: RUNTIME["app"] = application
