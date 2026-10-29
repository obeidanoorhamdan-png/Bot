"""f_admin.py — لوحة المشرف: لوحة التحكم، المفاتيح، القنوات، البث، التحليلات، المراقبة، النسخ الاحتياطي"""
import asyncio
import csv
import glob
import gzip
import io
import os
import platform
import re
import shutil
import sys

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram import __version__ as PTB_VERSION
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden, RetryAfter

from config import *  # noqa
from database import DBError, db
from services import *  # noqa
import f_drops

MAX_TG_FILE = 45 * 1024 * 1024


# ═══════════════════════════════════════════════════════════════════
# 🛡️ القائمة الرئيسية + لوحة التحكم
# ═══════════════════════════════════════════════════════════════════
async def show_admin(update, context):
    if not is_admin(update.effective_user.id):
        await say(update, "🚫 للمشرفين فقط", kb_main(False)); return
    reset_flow(context, "admin")
    unseen = await db.unseen_alerts()
    await say(update, "🛡️ <b>لوحة المشرف</b>" + (f"\n\n⚠️ لديك <b>{unseen}</b> تنبيه غير مقروء (🔍 المراقبة)" if unseen else ""), kb_admin())


async def dashboard_text():
    s = await db.stats()
    ms = await db.market_stats()
    h = await db.api_health(60)
    key = await db.get_active_key()
    ok_rate = f"{(h['n'] - h['fails']) * 100 // h['n']}%" if h["n"] else "—"
    api_icon = "🟢" if key and (not h["n"] or h["fails"] / max(h["n"], 1) < 0.3) else "🔴"
    last_bk = sorted(glob.glob(os.path.join(BACKUP_DIR, "*.gz")))
    bk = fmt_dt(datetime.fromtimestamp(os.path.getmtime(last_bk[-1])).isoformat()) if last_bk else "لا يوجد"
    return (f"📊 <b>لوحة التحكم</b> — {datetime.now().strftime('%H:%M:%S')}\n\n"
            f"👥 <b>المستخدمون:</b> {s['users']} (+{s['users_today']} اليوم) • نشط اليوم {s['dau']} • أسبوعياً {s['wau']}\n"
            f"📦 <b>الاشتراكات:</b> {s['subs']} (+{s['subs_today']}) • نقاط مصروفة اليوم {s['spent_today']}\n"
            f"💰 <b>نقاط متداولة:</b> {s['points_total']}\n\n"
            f"🎁 دروبات نشطة {s['drops_active']} • ⏰ مجدولة {s['drops_sched']}\n"
            f"🔨 مزادات مفتوحة {s['auctions_open']} • 🗳️ تصويتات {s['polls_open']}\n"
            f"🏪 عروض بازار {ms['active']} • ⚠️ شكاوى مفتوحة {ms['open_complaints']}\n\n"
            f"{api_icon} <b>API:</b> نجاح {ok_rate} ({h['n']} طلب/ساعة) • متوسط {h['avg_ms']}ms\n"
            f"🔑 المفتاح: {esc(key['label']) if key else '❌ لا يوجد'}\n"
            f"⚠️ تنبيهات غير مقروءة: {await db.unseen_alerts()}\n💾 آخر نسخة: {bk}")


DASH_KB = InlineKeyboardMarkup([[InlineKeyboardButton("🔄 تحديث", callback_data="adm_dash")]])


async def show_dashboard(update, context):
    await say(update, await dashboard_text(), kb_admin())
    await update.effective_chat.send_message("⬇️", reply_markup=DASH_KB)


# ═══════════════════════════════════════════════════════════════════
# 🔑 المفاتيح
# ═══════════════════════════════════════════════════════════════════
async def keys_view():
    keys = await db.list_api_keys()
    if not keys:
        return "🔑 <b>المفاتيح</b>\n\nلا توجد مفاتيح. أضف واحداً بزر ➕", None
    lines, rows = ["🔑 <b>المفاتيح</b>\n"], []
    for k in keys[:10]:
        lines.append(f"{'🟢' if k['is_active'] else '⚪'} <b>#{k['id']}</b> {esc(k.get('label') or '—')}\n"
                     f"    <code>{esc(mask_key(k['key_value']))}</code> — ✅{k['success_count']} ❌{k['fail_count']}")
        btns = []
        if not k["is_active"]:
            btns.append(InlineKeyboardButton(f"🟢 تفعيل #{k['id']}", callback_data=f"adm_key_act:{k['id']}"))
        btns += [InlineKeyboardButton(f"🧪 #{k['id']}", callback_data=f"adm_key_test:{k['id']}"),
                 InlineKeyboardButton(f"🗑 #{k['id']}", callback_data=f"adm_key_del:{k['id']}")]
        rows.append(btns)
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def show_keys(update, context):
    reset_flow(context, "keys")
    await say(update, "🔑 <b>إدارة المفاتيح</b>", kb_keys())
    text, markup = await keys_view()
    await say(update, text, markup)


async def key_add_begin(update, context):
    context.user_data["state"] = "key_add_value"
    await say(update, "🔐 أرسل قيمة المفتاح (سيُحذف من المحادثة تلقائياً):", kb_cancel())


async def key_add_value(update, context, text):
    if len(text) < 8 or " " in text:
        await say(update, "❌ مفتاح غير صالح، أعد الإرسال:", kb_cancel()); return
    try:
        await update.message.delete()
    except Exception:
        pass
    if await db.find_key_by_value(text):
        reset_flow(context, "keys")
        await say(update, "⚠️ هذا المفتاح مضاف مسبقاً.", kb_keys()); return
    context.user_data["new_key_value"] = text
    context.user_data["state"] = "key_add_label"
    await say(update, "✏️ اسم وصفي للمفتاح:", kb_cancel())


async def key_add_label(update, context, text):
    value = context.user_data.pop("new_key_value", None)
    if not value:
        await show_admin(update, context); return
    label = text[:50]
    kid = await db.add_api_key(value, label, update.effective_user.id)
    ok, msg = await api.test_key(value)
    await db.log_admin(update.effective_user.id, "add_key", f"#{kid} {label}")
    first = not await db.get_active_key()
    if first:
        await db.set_active_key(kid)
    reset_flow(context, "keys")
    await say(update, f"✅ حُفظ <b>{esc(label)}</b> (#{kid})\n{msg}" + ("\n🟢 فُعّل تلقائياً" if first else "\nفعّله من القائمة."), kb_keys())
    t2, m2 = await keys_view()
    await say(update, t2, m2)


async def admin_callback(update, context):
    """أزرار المفاتيح والقنوات والتحليلات والمراقبة"""
    q = update.callback_query
    uid = q.from_user.id
    if not is_admin(uid):
        await q.answer("🚫 للمشرفين فقط", show_alert=True); return
    act, _, raw = q.data.partition(":")

    async def rerender(view):
        text, markup = await view()
        try:
            await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)
        except BadRequest as e:
            if "not modified" not in str(e).lower():
                raise

    if act == "adm_dash":
        await q.answer("🔄")
        try:
            await q.edit_message_text(await dashboard_text(), parse_mode=ParseMode.HTML, reply_markup=DASH_KB)
        except BadRequest:
            pass
    elif act.startswith("adm_key_"):
        oid = int(raw)
        if act == "adm_key_act":
            if not await db.get_key(oid):
                await q.answer("غير موجود", show_alert=True); return
            await db.set_active_key(oid); await db.log_admin(uid, "activate_key", f"#{oid}")
            await q.answer("🟢 تم التفعيل"); await rerender(keys_view)
        elif act == "adm_key_test":
            key = await db.get_key(oid)
            if not key:
                await q.answer("غير موجود", show_alert=True); return
            await q.answer("⏳")
            _, msg = await api.test_key(key["key_value"])
            await q.answer(f"المفتاح #{oid}: {msg}", show_alert=True)
        elif act == "adm_key_del":
            await q.answer()
            await q.edit_message_text(f"⚠️ حذف المفتاح <b>#{oid}</b> نهائياً؟", parse_mode=ParseMode.HTML,
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ نعم احذف", callback_data=f"adm_key_delok:{oid}"),
                                                                          InlineKeyboardButton("↩️ تراجع", callback_data="adm_key_back:0")]]))
        elif act == "adm_key_delok":
            await db.delete_api_key(oid); await db.log_admin(uid, "delete_key", f"#{oid}")
            await q.answer("🗑 حُذف"); await rerender(keys_view)
        elif act == "adm_key_back":
            await q.answer(); await rerender(keys_view)
    elif act.startswith("adm_ch_"):
        await channels_callback(update, context, act, raw)
    elif act == "adm_export":
        await q.answer("⏳ جاري التصدير...")
        await send_export(context.bot, q.message.chat_id, raw)
    elif act == "adm_alerts_seen":
        await db.mark_alerts_seen(); await q.answer("✅")
        await rerender(alerts_view)
    elif act == "adm_mon":
        await q.answer("🔄")
        try:
            await q.edit_message_text(await system_text(context, ping=(raw == "ping")), parse_mode=ParseMode.HTML, reply_markup=MON_KB)
        except BadRequest:
            pass
    elif act == "adm_bk_now":
        await q.answer("⏳ جاري إنشاء النسخة...")
        await do_backup(context.bot, send=True, chat_id=q.message.chat_id)
        await rerender(backups_view)
    elif act == "adm_bk_get":
        files = sorted(glob.glob(os.path.join(BACKUP_DIR, "*.gz")), reverse=True)
        i = int(raw)
        if i >= len(files):
            await q.answer("غير موجود", show_alert=True); return
        await q.answer("⏳")
        await send_file(context.bot, q.message.chat_id, files[i])
    else:
        await q.answer()


# ═══════════════════════════════════════════════════════════════════
# 📢 إدارة القنوات
# ═══════════════════════════════════════════════════════════════════
PURPOSE_LABELS = {"join": "🔒 اشتراك إجباري", "publish": "📡 نشر", "both": "🔒+📡 كلاهما"}


async def channels_view():
    chans = await db.list_channels()
    if not chans:
        return "📢 <b>القنوات</b>\n\nلا توجد قنوات. أضف واحدة بزر ➕", None
    lines, rows = ["📢 <b>القنوات</b>\n"], []
    for c in chans:
        lines.append(f"{'🟢' if c['is_active'] else '⚪'} <b>#{c['id']}</b> @{esc(c['username'])} — {esc(c.get('title') or '')}\n    {PURPOSE_LABELS.get(c['purpose'])}")
        rows.append([InlineKeyboardButton("⛔ تعطيل" if c["is_active"] else "🟢 تفعيل", callback_data=f"adm_ch_tgl:{c['id']}"),
                     InlineKeyboardButton("🔁 الغرض", callback_data=f"adm_ch_pur:{c['id']}"),
                     InlineKeyboardButton("🧪", callback_data=f"adm_ch_test:{c['id']}"),
                     InlineKeyboardButton("🗑", callback_data=f"adm_ch_del:{c['id']}")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def show_channels(update, context):
    reset_flow(context, "channels")
    await say(update, "📢 <b>إدارة القنوات</b>\n\n• 🔒 قنوات الاشتراك الإجباري\n• 📡 قنوات النشر (الدروبات، البازار، الإعلانات)\n"
                      "⚠️ يجب أن يكون البوت <b>مشرفاً</b> في كل قناة.", kb_channels())
    text, markup = await channels_view()
    await say(update, text, markup)


async def _parse_channel(text, data):
    u = text.strip().lstrip("@").replace("https://t.me/", "")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{3,31}", u):
        raise ValueError("اسم قناة غير صالح (بدون @)")
    bot = RUNTIME["app"].bot
    try:
        chat = await bot.get_chat(f"@{u}")
    except Exception:
        raise ValueError("لا أستطيع الوصول لهذه القناة. تأكد من الاسم وأنها عامة.")
    try:
        me = await bot.get_chat_member(chat.id, bot.id)
        data["bot_admin"] = me.status in ("administrator", "creator")
    except Exception:
        data["bot_admin"] = False
    data["chat_id"], data["title"] = chat.id, chat.title or ""
    return u


def _parse_purpose(text, data):
    for k, v in PURPOSE_LABELS.items():
        if text == v:
            return k
    raise ValueError("اختر من الأزرار")


def _channel_summary(d):
    return (f"📋 <b>مراجعة القناة</b>\n\n📢 @{esc(d['username'])} — {esc(d['title'])}\nالغرض: {PURPOSE_LABELS[d['purpose']]}\n"
            + ("✅ البوت مشرف فيها" if d.get("bot_admin") else "⚠️ البوت <b>ليس مشرفاً</b> فيها — لن يعمل الفحص/النشر حتى ترفعه"))


async def channel_done(update, context, d):
    ok = await db.add_channel(d["username"], d["chat_id"], d["title"], d["purpose"])
    await db.log_admin(update.effective_user.id, "add_channel", d["username"])
    await db.execute("DELETE FROM force_join_cache")
    await say(update, "✅ أُضيفت القناة" if ok else "⚠️ القناة مضافة مسبقاً", kb_channels())
    context.user_data["state"] = "channels"
    t, m = await channels_view()
    await say(update, t, m)


register_wizard("channel", [
    Step("username", "📢 أرسل يوزر القناة (مثل <code>mychannel</code>):", _parse_channel),
    Step("purpose", "🎯 غرض القناة:", _parse_purpose, kb=lambda d: choice_kb([[PURPOSE_LABELS["join"], PURPOSE_LABELS["publish"]], [PURPOSE_LABELS["both"]]]))],
    channel_done, show_channels, summary=_channel_summary)


async def channel_add_begin(update, context):
    await wiz_begin(update, context, "channel")


async def channels_callback(update, context, act, raw):
    q = update.callback_query
    cid = int(raw)
    ch = await db.get_channel(cid)
    if not ch:
        await q.answer("غير موجودة", show_alert=True); return

    async def rerender():
        t, m = await channels_view()
        try:
            await q.edit_message_text(t, parse_mode=ParseMode.HTML, reply_markup=m)
        except BadRequest:
            pass
    if act == "adm_ch_tgl":
        await db.update_channel(cid, is_active=0 if ch["is_active"] else 1)
        await db.log_admin(q.from_user.id, "toggle_channel", ch["username"]); await q.answer("✅"); await rerender()
    elif act == "adm_ch_pur":
        order = ["both", "join", "publish"]
        await db.update_channel(cid, purpose=order[(order.index(ch["purpose"]) + 1) % 3])
        await q.answer("✅"); await rerender()
    elif act == "adm_ch_test":
        try:
            me = await context.bot.get_chat_member(chat_id=chan_target(ch), user_id=context.bot.id)
            await q.answer(f"البوت: {me.status}\n" + ("✅ جاهز" if me.status in ("administrator", "creator") else "⚠️ ارفعه مشرفاً"), show_alert=True)
        except Exception as e:
            await q.answer(f"❌ {str(e)[:150]}", show_alert=True)
    elif act == "adm_ch_del":
        await q.answer()
        await q.edit_message_text(f"⚠️ حذف القناة @{esc(ch['username'])}؟", parse_mode=ParseMode.HTML,
                                  reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ نعم", callback_data=f"adm_ch_delok:{cid}"),
                                                                      InlineKeyboardButton("↩️ تراجع", callback_data=f"adm_ch_back:{cid}")]]))
    elif act == "adm_ch_delok":
        await db.delete_channel(cid); await db.log_admin(q.from_user.id, "delete_channel", ch["username"]); await q.answer("🗑"); await rerender()
    elif act == "adm_ch_back":
        await q.answer(); await rerender()


# ═══════════════════════════════════════════════════════════════════
# 📢 بث ونشر
# ═══════════════════════════════════════════════════════════════════
async def show_broadcast_menu(update, context):
    reset_flow(context, "broadcast")
    await say(update, "📢 <b>بث ونشر</b>\n\n• <b>بث للمستخدمين</b>: رسالة خاصة لكل مستخدم\n• <b>نشر في القنوات</b>: منشور مباشر في قنوات النشر\n\n"
                      "يدعم HTML: <code>&lt;b&gt;عريض&lt;/b&gt;</code> <code>&lt;i&gt;مائل&lt;/i&gt;</code> <code>&lt;a href=\"...\"&gt;رابط&lt;/a&gt;</code>", kb_broadcast())


AUDIENCES = {"👥 كل المستخدمين": "all", "🟢 النشطون (7 أيام)": "active7", "🔔 المشتركون في الأخبار": "news"}


def _parse_audience(text, data):
    if text not in AUDIENCES:
        raise ValueError("اختر من الأزرار")
    return AUDIENCES[text]


def _parse_text(text, data):
    t = text.strip()
    if not t or len(t) > 3900:
        raise ValueError("النص فارغ أو أطول من 3900 حرف")
    return t


async def _bc_summary(d):
    n = len(await db.user_ids(d["audience"]))
    return f"📣 <b>مراجعة البث</b>\n\nسيُرسل إلى <b>{n}</b> مستخدم:\n\n{'─' * 10}\n{d['text'][:700]}"


async def send_rich(bot, uid, text):
    try:
        await bot.send_message(uid, text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    except BadRequest as e:
        if "parse" in str(e).lower():
            await bot.send_message(uid, text, disable_web_page_preview=True)
        else:
            raise


async def run_broadcast(app, admin_id, audience, text):
    ok = fail = 0
    for uid in await db.user_ids(audience):
        for _ in range(2):
            try:
                await send_rich(app.bot, uid, text); ok += 1; break
            except RetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
            except Exception:
                fail += 1; break
        await asyncio.sleep(0.06)
    await db.log_admin(admin_id, "broadcast", f"ok={ok} fail={fail}")
    await notify_admins(app.bot, f"📣 انتهى البث\n✅ {ok}   ❌ {fail}")


async def bc_done(update, context, d):
    spawn(context, run_broadcast(context.application, update.effective_user.id, d["audience"], d["text"]))
    await say(update, "🚀 بدأ البث في الخلفية وستصلك رسالة عند الانتهاء.", kb_broadcast())
    context.user_data["state"] = "broadcast"


register_wizard("bc_users", [
    Step("audience", "👥 اختر الجمهور:", _parse_audience, kb=lambda d: choice_kb([[k] for k in AUDIENCES])),
    Step("text", "✍️ أرسل نص الرسالة:", _parse_text)], bc_done, show_broadcast_menu, summary=_bc_summary)


async def channel_post_done(update, context, d):
    ok, errs = await publish_to_channels(context.bot, None, 0, d["text"])
    await db.log_admin(update.effective_user.id, "channel_post", f"ok={ok}")
    msg = f"✅ نُشر في {ok} قناة" + (f"\n⚠️ أخطاء:\n" + "\n".join(esc(e) for e in errs) if errs else "")
    if not ok and not errs:
        msg = "⚠️ لا توجد قنوات نشر نشطة."
    await say(update, msg, kb_broadcast())
    context.user_data["state"] = "broadcast"


register_wizard("bc_channel", [Step("text", "✍️ أرسل نص المنشور:", _parse_text)], channel_post_done, show_broadcast_menu,
                summary=lambda d: f"📡 <b>مراجعة المنشور</b>\n\n{d['text'][:700]}")


async def bc_users_begin(update, context):
    await wiz_begin(update, context, "bc_users")


async def bc_channel_begin(update, context):
    await wiz_begin(update, context, "bc_channel")


async def show_logs(update, context):
    logs = await db.recent_logs(20)
    if not logs:
        await say(update, "📜 لا توجد سجلات بعد.", kb_admin()); return
    lines = ["📜 <b>السجل:</b>\n"] + [f"<code>{esc((l['created_at'] or '')[5:16])}</code> {esc(l['action'])} — {esc(l['details'])}" for l in logs]
    await say(update, "\n".join(lines), kb_admin())


# ═══════════════════════════════════════════════════════════════════
# 📈 التحليلات المتقدمة
# ═══════════════════════════════════════════════════════════════════
async def show_analytics_menu(update, context):
    reset_flow(context, "analytics")
    await say(update, "📊 <b>التحليلات المتقدمة</b>", kb_analytics())


async def analytics_users(update, context):
    s = await db.stats()
    days = await db.daily_counts("users", "joined_at", 14)
    top_inv = await db.q_all("SELECT invited_by, COUNT(*) n FROM users WHERE invited_by IS NOT NULL GROUP BY invited_by ORDER BY n DESC LIMIT 5")
    langs = await db.q_all("SELECT COALESCE(lang,'ar') l, COUNT(*) n FROM users GROUP BY l")
    d7 = (datetime.now() - timedelta(days=7)).isoformat(timespec="seconds")
    new7 = await db.q_scalar("SELECT COUNT(*) FROM users WHERE joined_at>=?", (d7,))
    ret = await db.q_scalar("SELECT COUNT(*) FROM users WHERE joined_at<? AND last_active>=?", (d7, d7))
    old = max(await db.q_scalar("SELECT COUNT(*) FROM users WHERE joined_at<?", (d7,)), 1)
    withsub = await db.q_scalar("SELECT COUNT(DISTINCT user_id) FROM subscriptions")
    lines = ["📈 <b>تحليل المستخدمين</b>\n", f"👥 الإجمالي: <b>{s['users']}</b> • جدد 7 أيام: <b>{new7}</b>",
             f"🟢 نشط اليوم: {s['dau']} • أسبوعياً: {s['wau']} • محظور: {s['banned']}",
             f"🔁 احتفاظ (قدامى نشطون هذا الأسبوع): <b>{ret * 100 // old}%</b>",
             f"💳 تحويل لمشترين: <b>{withsub * 100 // max(s['users'], 1)}%</b>", "",
             f"📅 التسجيلات (14 يوماً):\n<code>{spark([n for _, n in days])}</code>  (مجموع {sum(n for _, n in days)})", ""]
    if top_inv:
        lines.append("🏅 أفضل الداعين:")
        lines += [f"  {i}. <code>{t['invited_by']}</code> — {t['n']}" for i, t in enumerate(top_inv, 1)]
    lines.append("\n🌍 اللغات: " + " • ".join(f"{x['l']}: {x['n']}" for x in langs))
    await say(update, "\n".join(lines), kb_analytics())


async def analytics_revenue(update, context):
    s = await db.stats()
    ms = await db.market_stats()
    daily = await db.daily_sum("subscriptions", "created_at", "points_spent", 14, "AND source IN ('purchase','auction')")
    by_plan = await db.q_all("SELECT plan, COUNT(*) n, COALESCE(SUM(points_spent),0) pts FROM subscriptions GROUP BY plan")
    by_src = await db.q_all("SELECT COALESCE(source,'purchase') s, COUNT(*) n FROM subscriptions GROUP BY s")
    auc = await db.q_scalar("SELECT COALESCE(SUM(final_price),0) FROM auctions WHERE status='closed'")
    refunds = await db.q_scalar("SELECT COALESCE(SUM(amount),0) FROM points_log WHERE reason='refund'")
    given = await db.q_scalar("SELECT COALESCE(SUM(amount),0) FROM points_log WHERE amount>0 AND reason IN ('daily_bonus','invite_reward','drop_prize','points_drop_claim')")
    lines = ["💰 <b>تحليل الإيرادات</b> (بالنقاط)\n", f"💸 إجمالي المصروف: <b>{s['spent']}</b> • اليوم {s['spent_today']}",
             f"📈 آخر 14 يوماً:\n<code>{spark([n for _, n in daily])}</code>  ({sum(n for _, n in daily)})", ""]
    for p in by_plan:
        pi = plan_info(p["plan"])
        lines.append(f"{pi['emoji']} {esc(pi['name'])}: {p['n']} كود — {p['pts']} 💎")
    lines += ["", "📦 حسب المصدر: " + " • ".join(f"{SRC.get(x['s'], x['s'])} {x['n']}" for x in by_src),
              f"🔨 إيراد المزادات: {auc} 💎", f"🏪 البازار: تداول {ms['volume']} • عمولات <b>{ms['commission']}</b> (7 أيام: {ms['commission7']})",
              f"🎁 نقاط مُنحت مجاناً: {given} • مستردّ: {refunds}",
              f"⚖️ نقاط متداولة حالياً: {s['points_total']}"]
    await say(update, "\n".join(lines), kb_analytics())


SRC = {"purchase": "🛒 شراء", "drop": "🎁 دروب", "auction": "🔨 مزاد", "market": "🏪 بازار"}


async def analytics_drops(update, context):
    await f_drops.analytics_drops(update, context)


async def show_export_menu(update, context):
    names = [("users", "👥 المستخدمون"), ("subscriptions", "📦 الاشتراكات"), ("points_log", "💎 سجل النقاط"), ("drops", "🎁 الدروبات"),
             ("drop_claims", "🏅 الاستلامات"), ("market_listings", "🏪 البازار"), ("auctions", "🔨 المزادات"), ("admin_logs", "📜 سجل المشرف")]
    rows = [[InlineKeyboardButton(label, callback_data=f"adm_export:{key}")] for key, label in names]
    rows.append([InlineKeyboardButton("💾 قاعدة البيانات كاملة", callback_data="adm_export:backup")])
    await say(update, "📤 <b>التصدير</b>\n\nاختر ما تريد تصديره (CSV متوافق مع Excel):", kb_analytics())
    await update.effective_chat.send_message("👇", reply_markup=InlineKeyboardMarkup(rows))


async def send_export(bot, chat_id, what):
    if what == "backup":
        await do_backup(bot, send=True, chat_id=chat_id); return
    cols, rows = await db.export_rows(what)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    for r in rows:
        w.writerow([r.get(c) for c in cols])
    data = io.BytesIO(("\ufeff" + buf.getvalue()).encode("utf-8"))
    data.name = f"{what}_{datetime.now():%Y%m%d_%H%M}.csv"
    await bot.send_document(chat_id, data, caption=f"📤 {what} — {len(rows)} صف")


# ═══════════════════════════════════════════════════════════════════
# 🔍 المراقبة
# ═══════════════════════════════════════════════════════════════════
MON_KB = InlineKeyboardMarkup([[InlineKeyboardButton("🔄 تحديث", callback_data="adm_mon:r"),
                                InlineKeyboardButton("🧪 فحص API الآن", callback_data="adm_mon:ping")]])


async def show_monitor_menu(update, context):
    reset_flow(context, "monitor")
    await say(update, "🔍 <b>المراقبة</b>", kb_monitor())


async def system_text(context, ping=False):
    import resource
    up = human_delta(time.time() - START_TIME)
    size = os.path.getsize(DB_PATH) / 1048576 if os.path.exists(DB_PATH) else 0
    mem = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    h1, h24 = await db.api_health(60), await db.api_health(1440)
    key = await db.get_active_key()
    lines = [f"⚙️ <b>حالة النظام</b>\n", f"⏱ التشغيل: {up}", f"🐍 Python {platform.python_version()} • PTB {PTB_VERSION}",
             f"💾 قاعدة البيانات: {size:.2f}MB • 🧠 ذاكرة (ذروة): {mem:.0f}MB",
             f"🧵 مهام asyncio: {len(asyncio.all_tasks())}", ""]
    for label, h in (("آخر ساعة", h1), ("آخر 24 ساعة", h24)):
        rate = f"{(h['n'] - h['fails']) * 100 // h['n']}%" if h["n"] else "—"
        lines.append(f"🌐 API {label}: {h['n']} طلب • نجاح {rate} • {h['avg_ms']}ms")
    if ping and key:
        t0 = time.monotonic()
        ok, msg = await api.test_key(key["key_value"])
        lines.append(f"🧪 فحص مباشر: {msg} ({int((time.monotonic() - t0) * 1000)}ms)")
    lines.append(f"🔑 المفتاح: {esc(key['label']) if key else '❌ لا يوجد'}")
    lines.append("")
    for ch in await db.list_channels(active_only=True):
        try:
            me = await context.bot.get_chat_member(chan_target(ch), context.bot.id)
            lines.append(f"{'✅' if me.status in ('administrator', 'creator') else '⚠️'} @{esc(ch['username'])} — {me.status}")
        except Exception as e:
            lines.append(f"❌ @{esc(ch['username'])} — {esc(str(e))[:60]}")
    s = await db.stats()
    lines += ["", f"⏰ مجدول بانتظار النشر: {s['drops_sched']} • مزادات مفتوحة {s['auctions_open']} • تصويتات {s['polls_open']}",
              f"👥 مستخدمون: {s['users']} • ⚠️ تنبيهات غير مقروءة: {await db.unseen_alerts()}"]
    return "\n".join(lines)


async def show_system(update, context):
    await say(update, await system_text(context), kb_monitor())
    await update.effective_chat.send_message("⬇️", reply_markup=MON_KB)


async def alerts_view():
    alerts = await db.list_alerts(12)
    if not alerts:
        return "⚠️ <b>التنبيهات</b>\n\nلا توجد تنبيهات ✅", None
    icon = {"critical": "🚨", "warning": "⚠️", "info": "ℹ️"}
    lines = ["⚠️ <b>التنبيهات</b>\n"] + [f"{icon.get(a['level'], '⚠️')}{'' if a['seen'] else '🆕'} <code>{esc(a['created_at'][5:16])}</code> {esc(a['message'])[:160]}" for a in alerts]
    return "\n".join(lines), InlineKeyboardMarkup([[InlineKeyboardButton("✅ تعليم الكل كمقروء", callback_data="adm_alerts_seen:0")]])


async def show_alerts(update, context):
    text, kb = await alerts_view()
    await say(update, text, kb)


async def monitor_job(context):
    """فحوص دورية كل 5 دقائق"""
    bot = context.bot
    try:
        n = context.application.bot_data["mon_n"] = context.application.bot_data.get("mon_n", 0) + 1
        if not await db.get_active_key():
            await raise_alert(bot, "critical", "no_key", "لا يوجد مفتاح API نشط — الشراء والدروبات متوقفة.", 120)
        h = await db.api_health(30)
        if h["n"] >= 5 and h["fails"] / h["n"] >= 0.5:
            await raise_alert(bot, "warning", "api_failures", f"معدل فشل API مرتفع: {h['fails']}/{h['n']} خلال 30 دقيقة.", 30)
        last = h["last"]
        if len(last) >= 5 and all(x["status"] == 401 for x in last[:5]):
            await raise_alert(bot, "critical", "key_invalid", "المفتاح النشط مرفوض (401) في آخر 5 طلبات — بدّله من إدارة المفاتيح.", 60)
        if os.path.exists(DB_PATH) and os.path.getsize(DB_PATH) > 500 * 1048576:
            await raise_alert(bot, "warning", "db_big", "حجم قاعدة البيانات تجاوز 500MB.", 720)
        stuck = await db.q_scalar("SELECT COUNT(*) FROM auctions WHERE status='open' AND ends_at<?",
                                  ((datetime.now() - timedelta(minutes=10)).isoformat(timespec="seconds"),))
        if stuck:
            await raise_alert(bot, "warning", "stuck_auction", f"{stuck} مزاد تجاوز وقته ولم يُغلق.", 30)
        if n % 12 == 1:                                   # مرة كل ساعة
            for ch in await db.list_channels(active_only=True):
                try:
                    me = await bot.get_chat_member(chan_target(ch), bot.id)
                    if me.status not in ("administrator", "creator"):
                        await raise_alert(bot, "warning", f"chan_{ch['id']}", f"البوت ليس مشرفاً في @{ch['username']}.", 360)
                except Exception as e:
                    await raise_alert(bot, "warning", f"chan_{ch['id']}", f"تعذر الوصول إلى @{ch['username']}: {str(e)[:80]}", 360)
    except Exception:
        logger.exception("[MONITOR] خطأ")


# ─── النسخ الاحتياطي ───
async def do_backup(bot, send=False, chat_id=None):
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    raw_path = os.path.join(BACKUP_DIR, f"bot_{stamp}.db")
    gz_path = raw_path + ".gz"
    await db.backup_to(raw_path)

    def compress():
        with open(raw_path, "rb") as f_in, gzip.open(gz_path, "wb", compresslevel=6) as f_out:
            shutil.copyfileobj(f_in, f_out)
        os.remove(raw_path)
    await asyncio.to_thread(compress)
    for old in sorted(glob.glob(os.path.join(BACKUP_DIR, "*.gz")))[:-BACKUP_KEEP]:
        try:
            os.remove(old)
        except OSError:
            pass
    await db.log_admin(0, "backup", os.path.basename(gz_path))
    if send:
        await send_file(bot, chat_id or SUPER_ADMIN_ID, gz_path)
    return gz_path


async def send_file(bot, chat_id, path):
    size = os.path.getsize(path)
    if size > MAX_TG_FILE:
        await bot.send_message(chat_id, f"⚠️ الملف كبير ({size / 1048576:.1f}MB) — حمّله من المسار:\n<code>{esc(path)}</code>", parse_mode=ParseMode.HTML)
        return
    with open(path, "rb") as f:
        await bot.send_document(chat_id, f, filename=os.path.basename(path),
                                caption=f"💾 {os.path.basename(path)} — {size / 1024:.0f}KB\n⚠️ يحتوي على مفاتيح API — احتفظ به بسرية.")


async def backups_view():
    files = sorted(glob.glob(os.path.join(BACKUP_DIR, "*.gz")), reverse=True)
    lines = ["💾 <b>النسخ الاحتياطي</b>\n", f"🕒 تلقائي يومياً 03:00 (يُحتفظ بآخر {BACKUP_KEEP}) ويُرسل للمشرف الرئيسي.\n"]
    rows = []
    for i, f in enumerate(files[:8]):
        lines.append(f"• {os.path.basename(f)} — {os.path.getsize(f) / 1024:.0f}KB")
        rows.append([InlineKeyboardButton(f"⬇️ {os.path.basename(f)[4:19]}", callback_data=f"adm_bk_get:{i}")])
    if not files:
        lines.append("لا توجد نسخ بعد.")
    lines += ["", "♻️ <b>الاستعادة:</b> أوقف البوت، فكّ الضغط <code>gunzip file.gz</code>، وانسخ الملف مكان <code>bot_data.db</code> ثم شغّله."]
    rows.append([InlineKeyboardButton("💾 نسخة الآن", callback_data="adm_bk_now:0")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def show_backup(update, context):
    text, kb = await backups_view()
    await say(update, text, kb_monitor())
    await update.effective_chat.send_message("👇", reply_markup=kb)


async def backup_job(context):
    try:
        await do_backup(context.bot, send=True, chat_id=SUPER_ADMIN_ID)
        await db.purge_old()
    except Exception as e:
        logger.exception("[BACKUP] فشل")
        await raise_alert(context.bot, "critical", "backup_fail", f"فشل النسخ الاحتياطي: {e}", 360)


# ═══════════════════════════════════════════════════════════════════
# 🛡️ أوامر المشرف
# ═══════════════════════════════════════════════════════════════════
def admin_only(fn):
    import functools

    @functools.wraps(fn)
    async def wrapper(update, context):
        if not update.effective_user or not is_admin(update.effective_user.id):
            return
        return await fn(update, context)
    return wrapper


@admin_only
async def admin_cmd(update, context):
    await show_admin(update, context)


@admin_only
async def ban_cmd(update, context):
    if not context.args or not context.args[0].isdigit():
        await say(update, "<code>/ban &lt;id&gt;</code>"); return
    t = int(context.args[0])
    if is_admin(t):
        await say(update, "❌ لا يمكن حظر مشرف"); return
    n = await db.set_ban(t, True)
    await db.log_admin(update.effective_user.id, "ban", t)
    await say(update, f"✅ حُظر <code>{t}</code>" if n else "❌ غير موجود")


@admin_only
async def unban_cmd(update, context):
    if not context.args or not context.args[0].isdigit():
        await say(update, "<code>/unban &lt;id&gt;</code>"); return
    t = int(context.args[0])
    n = await db.set_ban(t, False)
    await db.log_admin(update.effective_user.id, "unban", t)
    await say(update, f"✅ رُفع الحظر عن <code>{t}</code>" if n else "❌ غير موجود")


@admin_only
async def addpoints_cmd(update, context):
    try:
        t, amt = int(context.args[0]), int(context.args[1])
    except (IndexError, ValueError):
        await say(update, "<code>/addpoints &lt;id&gt; &lt;±amount&gt;</code>"); return
    if not await db.get_user(t):
        await say(update, "❌ غير موجود"); return
    await db.add_points(t, amt, "admin_adjust", update.effective_user.id, count_total=False)
    await db.log_admin(update.effective_user.id, "addpoints", f"{t} {amt:+d}")
    await say(update, f"✅ رصيد <code>{t}</code>: <b>{(await db.get_user(t))['points']}</b>")


@admin_only
async def userinfo_cmd(update, context):
    if not context.args or not context.args[0].isdigit():
        await say(update, "<code>/userinfo &lt;id&gt;</code>"); return
    u = await db.get_user(int(context.args[0]))
    if not u:
        await say(update, "❌ غير موجود"); return
    await say(update, f"👤 <b>{esc(user_label(u))}</b> {('@' + esc(u['username'])) if u['username'] else ''}\n🆔 <code>{u['user_id']}</code>\n"
                      f"💎 {u['points']} (إجمالي {u['total_points_earned']})\n📦 اشتراكات: {await db.count_user_subs(u['user_id'])}\n"
                      f"👥 مدعوون: {await db.count_invited(u['user_id'])}\n🚫 محظور: {'نعم' if u['is_banned'] else 'لا'}\n"
                      f"🌍 {u['lang']} • 🎨 {u['theme']}\n📅 انضم: {fmt_dt(u['joined_at'])} • آخر نشاط: {fmt_dt(u['last_active'])}")
