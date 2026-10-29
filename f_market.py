"""f_market.py — البازار: بيع/شراء الأكواد بين المستخدمين + عمولات + شكاوى + نشر شفاف في القناة"""
import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode

from config import *  # noqa
from database import DBError, db
from services import *  # noqa


# ═══════════════════════════════════════════════════════════════════
# 🎨 البطاقات
# ═══════════════════════════════════════════════════════════════════
def remaining_text(expires_at):
    dt = parse_dt(expires_at) if expires_at else None
    if not dt:
        return "غير محدد"
    s = (dt - datetime.now()).total_seconds()
    return "منتهي" if s <= 0 else human_delta(s)


async def render_listing(bot, l):
    pi = plan_info(l["plan"])
    pct = await db.market_commission()
    payout = l["price"] - (l["commission"] if l["status"] == "sold" else l["price"] * pct // 100)
    lines = [f"🏪 <b>عرض بازار #{l['id']}</b>", "", f"{pi['emoji']} <b>{esc(pi['name'])}</b> — {l['days']} يوم",
             f"⏳ المتبقي من الصلاحية: <b>{remaining_text(l['expires_at'])}</b>", f"💰 السعر: <b>{l['price']}</b> 💎",
             f"📊 العمولة: {pct if l['status'] != 'sold' else (l['commission'] * 100 // l['price'] if l['price'] else 0)}% — يستلم البائع <b>{payout}</b> 💎",
             f"👤 البائع: <code>{mask_id(l['seller_id'])}</code>", "", "━━━━━━━━━━━━━━━━━"]
    st = l["status"]
    if st == "active":
        lines.append("🟢 <b>متاح للشراء</b>")
    elif st == "sold":
        lines += ["✅ <b>تم البيع</b>", f"🛒 المشتري: <code>{mask_id(l['buyer_id'])}</code>", f"⏰ {relative_time(l['sold_at'])}",
                  f"🏦 عمولة المنصة: {l['commission']} 💎"]
    elif st == "refunded":
        lines.append("↩️ <b>أُعيد المبلغ للمشتري بعد شكوى</b>")
    elif st == "cancelled":
        lines.append("⚫ <b>سحبه البائع</b>")
    elif st == "invalid":
        lines.append("🔴 <b>أُزيل (الكود لم يعد صالحاً)</b>")
    else:
        lines.append("⚫ <b>أزاله المشرف</b>")
    lines.append("━━━━━━━━━━━━━━━━━\n🔍 كل العمليات شفافة وتُنشر هنا.")
    return "\n".join(lines)


def listing_kb(bot_username, l):
    if l["status"] != "active":
        return None
    return InlineKeyboardMarkup([[InlineKeyboardButton("🛒 اشترِ عبر البوت", url=f"https://t.me/{bot_username}?start=mk_{l['id']}")]])


async def refresh_listing(bot, lid):
    l = await db.get_listing(lid)
    if l:
        await refresh_posts(bot, "listing", lid, await render_listing(bot, l), listing_kb(bot.username, l))


# ═══════════════════════════════════════════════════════════════════
# 🏪 القوائم
# ═══════════════════════════════════════════════════════════════════
async def show_bazaar(update, context):
    reset_flow(context, "bazaar")
    pct = await db.market_commission()
    n = await db.count_listings("active")
    await say(update, f"🏪 <b>البازار</b>\n\nسوق شفاف لتبادل الأكواد بين المستخدمين بالنقاط.\n\n"
                      f"🛍 عروض نشطة: <b>{n}</b>\n📊 عمولة المنصة: <b>{pct}%</b>\n"
                      f"🛡️ نتحقق من صلاحية الكود قبل العرض وقبل الشراء، ويمكنك تقديم شكوى خلال {COMPLAINT_HOURS} ساعة.",
              kb_bazaar())


async def browse_view(page=0):
    total = await db.count_listings("active")
    items = await db.list_listings("active", None, PAGE_SIZE, page * PAGE_SIZE)
    if not items:
        return "🛍 لا توجد عروض حالياً.", None
    pct = await db.market_commission()
    lines = [f"🛍 <b>العروض المتاحة</b> ({total}) — عمولة {pct}%\n"]
    rows = []
    for l in items:
        pi = plan_info(l["plan"])
        lines.append(f"<b>#{l['id']}</b> {pi['emoji']} {esc(pi['name'])} — {l['days']}ي — متبقي {remaining_text(l['expires_at'])}\n"
                     f"    💰 {l['price']} 💎 — بائع <code>{mask_id(l['seller_id'])}</code>")
        rows.append([InlineKeyboardButton(f"🛒 شراء #{l['id']} — {l['price']} 💎", callback_data=f"mk_buy:{l['id']}")])
    rows.append(_pager("mk_pg", page, total))
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def _pager(prefix, page, total, per=PAGE_SIZE):
    pages = max(1, -(-total // per))
    row = []
    if page > 0:
        row.append(InlineKeyboardButton("◀️", callback_data=f"{prefix}:{page - 1}"))
    row.append(InlineKeyboardButton(f"{page + 1}/{pages}", callback_data="noop"))
    if page + 1 < pages:
        row.append(InlineKeyboardButton("▶️", callback_data=f"{prefix}:{page + 1}"))
    return row


async def show_browse(update, context):
    text, kb = await browse_view(0)
    await say(update, text, kb)


async def show_mine(update, context):
    uid = update.effective_user.id
    active = await db.list_listings("active", uid, 20, 0)
    sold = await db.count_listings("sold", uid)
    lines = [f"📋 <b>عروضي</b>\n\n✅ مبيعاتي: {sold}\n"]
    rows = []
    for l in active:
        pi = plan_info(l["plan"])
        lines.append(f"<b>#{l['id']}</b> {pi['emoji']} {l['days']}ي — {l['price']} 💎")
        rows.append([InlineKeyboardButton(f"🗑 سحب #{l['id']}", callback_data=f"mk_cancel:{l['id']}")])
    if not active:
        lines.append("لا عروض نشطة. استخدم ➕ بيع كود.")
    await say(update, "\n".join(lines), InlineKeyboardMarkup(rows) if rows else None)


async def show_ledger(update, context):
    rows = await db.q_all("SELECT * FROM market_listings WHERE status IN ('sold','refunded') ORDER BY id DESC LIMIT 10")
    st = await db.market_stats()
    lines = ["📜 <b>سجل البازار العلني</b>\n",
             f"إجمالي التداول: <b>{st['volume']}</b> 💎 — العمولات: <b>{st['commission']}</b> 💎 — المستردّ: {st['refunded']}\n"]
    for l in rows:
        pi = plan_info(l["plan"])
        lines.append(f"#{l['id']} {pi['emoji']} {l['days']}ي — {l['price']} 💎 — <code>{mask_id(l['seller_id'])}</code> ⟶ "
                     f"<code>{mask_id(l['buyer_id'])}</code> — عمولة {l['commission']} — {'↩️' if l['status'] == 'refunded' else '✅'} {relative_time(l['sold_at'])}")
    if not rows:
        lines.append("لا عمليات بعد.")
    await say(update, "\n".join(lines), kb_bazaar())


# ═══════════════════════════════════════════════════════════════════
# 🛒 الشراء
# ═══════════════════════════════════════════════════════════════════
async def buy_card(lid):
    l = await db.get_listing(lid)
    if not l or l["status"] != "active":
        return "😔 العرض لم يعد متاحاً.", None
    pi = plan_info(l["plan"])
    pct = await db.market_commission()
    text = (f"🛒 <b>تأكيد الشراء #{lid}</b>\n\n{pi['emoji']} {esc(pi['name'])} — {l['days']} يوم\n"
            f"⏳ متبقي: {remaining_text(l['expires_at'])}\n💰 السعر: <b>{l['price']}</b> 💎\n"
            f"📊 عمولة المنصة {pct}% تُخصم من البائع.\n\n🔍 سنتحقق من صلاحية الكود قبل إتمام الصفقة.")
    return text, InlineKeyboardMarkup([[InlineKeyboardButton("✅ تأكيد الشراء", callback_data=f"mk_buyok:{lid}"),
                                        InlineKeyboardButton("❌ إلغاء", callback_data="mk_close")]])


async def handle_market_entry(update, context, arg):
    if not arg.isdigit():
        await say(update, "❌ رابط غير صالح.", kb_main(is_admin(update.effective_user.id))); return
    text, kb = await buy_card(int(arg))
    await say(update, text, kb or kb_main(is_admin(update.effective_user.id)))


async def market_user_callback(update, context):
    q = update.callback_query
    user = await cb_guard(update)
    if not user:
        return
    parts = q.data.split(":")
    act = parts[0]
    uid = q.from_user.id

    if act == "mk_close":
        await q.answer()
        try:
            await q.message.delete()
        except Exception:
            pass
        return
    if act == "mk_pg":
        text, kb = await browse_view(int(parts[1]))
        await q.answer()
        try:
            await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
        except Exception:
            pass
        return
    if act == "mk_buy":
        text, kb = await buy_card(int(parts[1]))
        await q.answer()
        await q.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
        return
    if act == "mk_cancel":
        lid = int(parts[1])
        l = await db.get_listing(lid)
        if not l or l["seller_id"] != uid:
            await q.answer("غير مسموح", show_alert=True); return
        ok = await db.market_set_status(lid, "cancelled")
        await q.answer("🗑 تم السحب" if ok else "لا يمكن السحب", show_alert=not ok)
        if ok:
            await refresh_listing(context.bot, lid)
            try:
                await q.edit_message_text("🗑 تم سحب العرض.")
            except Exception:
                pass
        return
    if act == "mk_sellsub":
        await q.answer()
        await start_sell(update, context, preset_sub=int(parts[1]))
        return
    if act == "mk_buyok":
        lid = int(parts[1])
        l = await db.get_listing(lid)
        if not l or l["status"] != "active":
            await q.answer("😔 العرض لم يعد متاحاً", show_alert=True); return
        if l["seller_id"] == uid:
            await q.answer("لا يمكنك شراء عرضك", show_alert=True); return
        await q.answer("🔍 جاري التحقق من الكود...")
        valid, _, msg = await api.code_state(l["code"])
        if valid is None:
            await q.edit_message_text(f"⚠️ تعذر التحقق من الكود الآن ({esc(msg)[:80]})، حاول لاحقاً.", parse_mode=ParseMode.HTML); return
        if valid is False:
            await db.market_set_status(lid, "invalid")
            await refresh_listing(context.bot, lid)
            await q.edit_message_text("🔴 هذا الكود لم يعد صالحاً (مستخدم/منتهي). أُزيل العرض.")
            spawn(context, notify_users(context.bot, [l["seller_id"]], f"⚠️ أُزيل عرضك #{lid} لأن الكود لم يعد صالحاً."))
            return
        try:
            r = await db.market_buy(lid, uid)
        except DBError as e:
            await q.edit_message_text(f"❌ {esc(e)}"); return
        pi = plan_info(r["plan"])
        await q.edit_message_text(
            f"✅ <b>تمت الصفقة!</b>\n\n{pi['emoji']} {esc(pi['name'])} — {r['days']} يوم\n🔑 <code>{esc(r['code'])}</code>\n\n"
            f"💎 خُصم: {r['price']}\n📖 فعّله في: {esc(SITE_URL)}\n\n⚠️ إن كان الكود غير صالح قدّم شكوى خلال {COMPLAINT_HOURS} ساعة من «⚠️ تقديم شكوى».",
            parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        await refresh_listing(context.bot, lid)
        await db.log_admin(0, "market_sale", f"#{lid} {r['price']}p fee {r['commission']}")
        spawn(context, notify_users(context.bot, [r["seller_id"]],
                                    f"💰 <b>تم بيع عرضك #{lid}</b>\nالسعر {r['price']} — العمولة {r['commission']} — أُضيف لرصيدك <b>{r['payout']}</b> 💎"))


# ═══════════════════════════════════════════════════════════════════
# ➕ البيع (Wizard)
# ═══════════════════════════════════════════════════════════════════
def _sell_prompt(d):
    lines = ["➕ <b>بيع كود</b>\n\nاختر رقم الاشتراك الذي تريد عرضه (فقط الأكواد التي حصلت عليها من البوت وما زالت صالحة):\n"]
    for i, s in enumerate(d["subs"], 1):
        pi = plan_info(s["plan"])
        lines.append(f"<b>{i}.</b> {pi['emoji']} {esc(pi['name'])} — {s['days']}ي — متبقي {remaining_text(s['expires_at'])}")
    return "\n".join(lines)


def _parse_sell_sub(text, data):
    if not text.strip().isdigit() or not (1 <= int(text) <= len(data["subs"])):
        raise ValueError("أرسل رقماً من القائمة")
    return data["subs"][int(text) - 1]["id"]


async def _sell_summary(d):
    pct = await db.market_commission()
    price = d["price"]
    s = await db.get_sub(d["sub_id"])
    pi = plan_info(s["plan"])
    fee = price * pct // 100
    return (f"📋 <b>مراجعة العرض</b>\n\n{pi['emoji']} {esc(pi['name'])} — {s['days']}ي\n💰 السعر: <b>{price}</b> 💎\n"
            f"📊 العمولة {pct}%: {fee} 💎\n✅ ستستلم: <b>{price - fee}</b> 💎\n\n"
            "ℹ️ سيُنشر العرض علناً في القناة. لا تستخدم الكود بنفسك وهو معروض!")


SELL_STEPS = [
    Step("sub_id", _sell_prompt, _parse_sell_sub, when=lambda d: "sub_id" not in d),
    Step("price", f"💰 السعر بالنقاط ({MARKET_MIN_PRICE} – {MARKET_MAX_PRICE}):", parse_int(MARKET_MIN_PRICE, MARKET_MAX_PRICE, "السعر")),
]


async def sell_done(update, context, d):
    uid = update.effective_user.id
    s = await db.get_sub(d["sub_id"])
    if not s or s["user_id"] != uid:
        await say(update, "❌ اشتراك غير صالح.", kb_bazaar()); return
    valid, _, msg = await api.code_state(s["code"])
    if valid is None:
        await say(update, f"⚠️ تعذر التحقق من الكود الآن ({esc(msg)[:80]})، حاول لاحقاً.", kb_bazaar()); return
    if valid is False:
        await say(update, "🔴 هذا الكود مستخدم أو منتهي ولا يمكن بيعه.", kb_bazaar()); return
    try:
        lid = await db.market_create_listing(uid, d["sub_id"], d["price"])
    except DBError as e:
        await say(update, f"❌ {esc(e)}", kb_bazaar()); return
    l = await db.get_listing(lid)
    ok, _ = await publish_to_channels(context.bot, "listing", lid, await render_listing(context.bot, l), listing_kb(context.bot.username, l))
    await say(update, f"✅ <b>تم عرض الكود</b> (#{lid})\n" + ("📡 نُشر في القناة." if ok else "ℹ️ لا توجد قناة نشر نشطة، العرض ظاهر داخل البوت."),
              kb_bazaar())
    context.user_data["state"] = "bazaar"


async def sell_exit(update, context):
    await show_bazaar(update, context)


register_wizard("sell", SELL_STEPS, sell_done, sell_exit, summary=_sell_summary)


async def start_sell(update, context, preset_sub=None):
    uid = update.effective_user.id
    data = {}
    if preset_sub:
        s = await db.get_sub(preset_sub)
        if not s or s["user_id"] != uid:
            await say(update, "❌ اشتراك غير صالح."); return
        data["sub_id"] = preset_sub
    else:
        subs = [s for s in await db.list_user_subs(uid, "active", 20, 0) if not s["listed"]]
        if not subs:
            await say(update, "📭 لا تملك اشتراكات نشطة قابلة للبيع.", kb_bazaar()); return
        data["subs"] = subs
    await wiz_begin(update, context, "sell", data)


# ═══════════════════════════════════════════════════════════════════
# ⚠️ الشكاوى (Wizard)
# ═══════════════════════════════════════════════════════════════════
def _cmp_prompt(d):
    lines = ["⚠️ <b>تقديم شكوى</b>\n\nاختر رقم عملية الشراء:\n"]
    for i, l in enumerate(d["purchases"], 1):
        pi = plan_info(l["plan"])
        lines.append(f"<b>{i}.</b> #{l['id']} {pi['emoji']} {l['days']}ي — {l['price']} 💎 — {relative_time(l['sold_at'])}")
    return "\n".join(lines)


def _parse_cmp_listing(text, data):
    if not text.strip().isdigit() or not (1 <= int(text) <= len(data["purchases"])):
        raise ValueError("أرسل رقماً من القائمة")
    return data["purchases"][int(text) - 1]["id"]


def _parse_reason(text, data):
    t = text.strip()
    if len(t) < 5 or len(t) > 500:
        raise ValueError("اكتب سبباً واضحاً (5–500 حرف)")
    return t


CMP_STEPS = [Step("listing_id", _cmp_prompt, _parse_cmp_listing),
             Step("reason", "✍️ اشرح المشكلة باختصار:", _parse_reason)]


async def cmp_done(update, context, d):
    uid = update.effective_user.id
    l = await db.get_listing(d["listing_id"])
    valid, info, msg = await api.code_state(l["code"])
    api_status = {True: "valid", False: f"invalid:{msg}", None: f"unknown:{msg}"}[valid]
    try:
        cid = await db.market_create_complaint(l["id"], uid, d["reason"], api_status)
    except DBError as e:
        await say(update, f"❌ {esc(e)}", kb_bazaar()); return
    await say(update, f"✅ <b>تم استلام شكواك</b> #{cid}\nسيراجعها المشرف قريباً.", kb_bazaar())
    context.user_data["state"] = "bazaar"
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("↩️ استرداد للمشتري", callback_data=f"adm_cmp_refund:{cid}:0"),
                                InlineKeyboardButton("❌ رفض", callback_data=f"adm_cmp_reject:{cid}:0")]])
    for aid in ADMIN_IDS:
        try:
            await context.bot.send_message(aid, await complaint_text(cid), parse_mode=ParseMode.HTML, reply_markup=kb)
        except Exception:
            pass


async def cmp_exit(update, context):
    await show_bazaar(update, context)


register_wizard("complaint", CMP_STEPS, cmp_done, cmp_exit)


async def start_complaint(update, context):
    uid = update.effective_user.id
    since = (datetime.now() - timedelta(hours=COMPLAINT_HOURS)).isoformat(timespec="seconds")
    rows = await db.q_all(
        """SELECT * FROM market_listings WHERE buyer_id=? AND status='sold' AND sold_at>=?
           AND id NOT IN (SELECT listing_id FROM market_complaints) ORDER BY id DESC LIMIT 10""", (uid, since))
    if not rows:
        await say(update, f"📭 لا توجد مشتريات قابلة للشكوى (خلال آخر {COMPLAINT_HOURS} ساعة).", kb_bazaar()); return
    await wiz_begin(update, context, "complaint", {"purchases": rows})


# ═══════════════════════════════════════════════════════════════════
# 🛡️ إدارة السوق (المشرف)
# ═══════════════════════════════════════════════════════════════════
async def show_market_admin(update, context):
    reset_flow(context, "market")
    st = await db.market_stats()
    await say(update, f"🛒 <b>إدارة السوق</b>\n\n🛍 نشطة: {st['active']} — ✅ مباعة: {st['sold']}\n"
                      f"💰 التداول: {st['volume']} 💎\n⚠️ شكاوى مفتوحة: <b>{st['open_complaints']}</b>", kb_market_admin())


async def admin_products_view(page=0):
    total = await db.count_listings("active")
    items = await db.list_listings("active", None, PAGE_SIZE, page * PAGE_SIZE)
    lines, rows = [f"📋 <b>المنتجات النشطة</b> ({total})\n"], []
    for l in items:
        pi = plan_info(l["plan"])
        lines.append(f"<b>#{l['id']}</b> {pi['emoji']} {l['days']}ي — {l['price']} 💎 — بائع <code>{l['seller_id']}</code>\n    <code>{esc(mask_key(l['code']))}</code>")
        rows.append([InlineKeyboardButton(f"🗑 إزالة #{l['id']}", callback_data=f"adm_mk_rm:{l['id']}:{page}")])
    if not items:
        lines.append("لا توجد منتجات.")
    rows.append(_pager("adm_mk_pg", page, total))
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def show_products(update, context):
    text, kb = await admin_products_view(0)
    await say(update, text, kb)


async def show_commissions(update, context):
    st = await db.market_stats()
    pct = await db.market_commission()
    await say(update, f"💰 <b>العمولات</b>\n\nالنسبة الحالية: <b>{pct}%</b>\nإجمالي العمولات: <b>{st['commission']}</b> 💎\n"
                      f"آخر 7 أيام: <b>{st['commission7']}</b> 💎\nحجم التداول: {st['volume']} 💎",
              kb_market_admin())
    await update.effective_chat.send_message("تعديل النسبة:", reply_markup=InlineKeyboardMarkup(
        [[InlineKeyboardButton("✏️ تغيير النسبة", callback_data="adm_mk_setcomm")]]))


async def comm_done(update, context, d):
    await db.set_setting("market_commission", d["pct"])
    await db.log_admin(update.effective_user.id, "set_commission", d["pct"])
    await say(update, f"✅ أصبحت العمولة <b>{d['pct']}%</b>", kb_market_admin())
    context.user_data["state"] = "market"


register_wizard("commission", [Step("pct", "✏️ أرسل نسبة العمولة الجديدة (0 – 50):", parse_int(0, 50, "النسبة"))],
                comm_done, show_market_admin)


async def complaint_text(cid):
    c = await db.get_complaint(cid)
    l = await db.get_listing(c["listing_id"])
    pi = plan_info(l["plan"])
    return (f"⚠️ <b>شكوى #{cid}</b> — {esc(c['status'])}\n\nعرض #{l['id']} {pi['emoji']} {l['days']}ي — {l['price']} 💎\n"
            f"🛒 المشتري: <code>{c['buyer_id']}</code>\n👤 البائع: <code>{c['seller_id']}</code>\n"
            f"🔎 حالة الكود عبر API: <code>{esc(c['api_status'])}</code>\n📝 السبب: {esc(c['reason'])}\n⏰ {relative_time(c['created_at'])}")


async def complaints_view(page=0):
    total = await db.count_complaints("open")
    items = await db.list_complaints("open", 3, page * 3)
    if not items:
        return "⚠️ <b>الشكاوى</b>\n\nلا توجد شكاوى مفتوحة ✅", None
    lines, rows = [f"⚠️ <b>الشكاوى المفتوحة</b> ({total})\n"], []
    for c in items:
        lines.append(await complaint_text(c["id"]))
        lines.append("—————")
        rows.append([InlineKeyboardButton(f"↩️ استرداد #{c['id']}", callback_data=f"adm_cmp_refund:{c['id']}:{page}"),
                     InlineKeyboardButton(f"❌ رفض #{c['id']}", callback_data=f"adm_cmp_reject:{c['id']}:{page}")])
    rows.append(_pager("adm_cmp_pg", page, total, 3))
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def show_complaints(update, context):
    text, kb = await complaints_view(0)
    await say(update, text, kb)


async def market_admin_callback(update, context):
    q = update.callback_query
    if not is_admin(q.from_user.id):
        await q.answer("🚫", show_alert=True); return
    parts = q.data.split(":")
    act = parts[0]

    async def edit(view, page):
        text, kb = await view(int(page))
        try:
            await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
        except Exception as e:
            if "not modified" not in str(e).lower():
                logger.warning(e)

    if act == "adm_mk_pg":
        await q.answer(); await edit(admin_products_view, parts[1])
    elif act == "adm_cmp_pg":
        await q.answer(); await edit(complaints_view, parts[1])
    elif act == "adm_mk_rm":
        lid = int(parts[1])
        ok = await db.market_set_status(lid, "removed")
        if ok:
            await db.log_admin(q.from_user.id, "market_remove", f"#{lid}")
            await refresh_listing(context.bot, lid)
        await q.answer("🗑 أُزيل" if ok else "لا يمكن", show_alert=not ok)
        await edit(admin_products_view, parts[2])
    elif act == "adm_mk_setcomm":
        await q.answer()
        await wiz_begin(update, context, "commission")
    elif act in ("adm_cmp_refund", "adm_cmp_reject"):
        cid, refund = int(parts[1]), act == "adm_cmp_refund"
        try:
            c, l = await db.resolve_complaint(cid, refund, "refund" if refund else "reject")
        except DBError as e:
            await q.answer(str(e), show_alert=True)
            return
        await db.log_admin(q.from_user.id, "complaint_refund" if refund else "complaint_reject", f"#{cid}")
        await q.answer("↩️ تم الاسترداد" if refund else "❌ رُفضت")
        if refund:
            await refresh_listing(context.bot, l["id"])
        spawn(context, notify_users(context.bot, [c["buyer_id"]],
                                    f"↩️ <b>قُبلت شكواك #{cid}</b>\nأُعيد لك {l['price']} 💎" if refund else f"❌ رُفضت شكواك #{cid}."))
        if refund:
            spawn(context, notify_users(context.bot, [c["seller_id"]], f"⚠️ بعد شكوى المشتري، خُصم {l['price'] - l['commission']} 💎 من رصيدك للعرض #{l['id']}."))
        try:
            await q.edit_message_text(await complaint_text(cid), parse_mode=ParseMode.HTML)
        except Exception:
            pass
