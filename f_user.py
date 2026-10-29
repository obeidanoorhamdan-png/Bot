"""f_user.py — ميزات المستخدم: البداية، المتجر، الاشتراكات، المحفظة، الحاسبة، الحالة، الإعدادات ..."""
import asyncio
import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode

from config import *  # noqa
from database import DBError, db
from services import *  # noqa
import f_drops
import f_market


def kb_for_state(context, admin):
    return {"wallet": kb_wallet(), "earn": kb_earn(), "subs": kb_subs(), "bazaar": kb_bazaar()}.get(
        context.user_data.get("state"), kb_main(admin))


# ═══════════════════════════════════════════════════════════════════
# 🚀 /start
# ═══════════════════════════════════════════════════════════════════
@user_locked
async def start(update, context):
    if context.args:
        context.user_data["pending_start"] = context.args[0][:64]
    if not await guard(update, context, register=False):
        return
    await process_start(update, context, context.user_data.pop("pending_start", None))


async def process_start(update, context, arg):
    tg = update.effective_user
    admin = is_admin(tg.id)
    reset_flow(context)
    if arg and arg.startswith("drop_"):
        await f_drops.handle_drop_entry(update, context, arg.removeprefix("drop_")); return
    if arg and arg.startswith("pdrop_"):
        await f_drops.handle_points_drop_entry(update, context, arg.removeprefix("pdrop_")); return
    if arg and arg.startswith("mk_"):
        u = await db.get_user(tg.id) or (await db.register_user(tg.id, tg.username, tg.first_name))[0]
        apply_prefs(u)
        await f_market.handle_market_entry(update, context, arg.removeprefix("mk_")); return
    invited_by = None
    if arg and arg.startswith("ref_"):
        try:
            invited_by = int(arg.removeprefix("ref_"))
        except ValueError:
            pass
    user, is_new, inviter = await db.register_user(tg.id, tg.username, tg.first_name, invited_by)
    apply_prefs(user)
    txt = f"👋 أهلاً <b>{esc(tg.first_name)}</b>!\n\n🤖 <b>Worm GPT</b> — بوت إدارة الاشتراكات\n\n"
    if is_new:
        txt += "🎉 تم تسجيلك بنجاح!\n"
        if inviter:
            txt += "🎁 حصل داعيك على نقطة!\n"
            spawn(context, notify_users(context.bot, [inviter], f"🎉 انضم <b>{esc(tg.first_name)}</b> عبر رابطك!\n💎 +{POINTS_INVITE} نقطة"))
        txt += "\n"
    await say(update, txt + "استخدم الأزرار بالأسفل:", kb_main(admin))


async def verify_join_callback(update, context):
    q = update.callback_query
    uid = q.from_user.id
    u = await db.get_user(uid)
    apply_prefs(u)
    if u and u["is_banned"]:
        await q.answer("🚫", show_alert=True); return
    await db.delete_force_join_cache(uid)
    ok, _ = await check_force_join(context, uid, use_cache=False)
    if not ok:
        await q.answer("❌ لم نتمكن من التحقق.\nتأكد من انضمامك لكل القنوات ثم أعد المحاولة.", show_alert=True); return
    await q.answer("✅")
    try:
        await q.message.delete()
    except Exception:
        pass
    await process_start(update, context, context.user_data.pop("pending_start", None))


# ═══════════════════════════════════════════════════════════════════
# 🏠 القوائم
# ═══════════════════════════════════════════════════════════════════
async def show_home(update, context):
    reset_flow(context)
    await say(update, "🏠 <b>القائمة الرئيسية</b>", kb_main(is_admin(update.effective_user.id)))


async def show_shop(update, context):
    user = await db.get_user(update.effective_user.id)
    reset_flow(context, "shop")
    lines = "\n".join(f"{i['emoji']} {esc(i['name'].split(' ', 1)[-1])} — من {min(i['prices'].values())} نقاط" for i in SUBSCRIPTION_TYPES.values())
    await say(update, f"🛒 <b>المتجر</b>\n\n💎 رصيدك: <code>{user['points']}</code>\n\n📦 <b>الخطط:</b>\n{lines}\n\n"
                      f"🎁 خصم تلقائي عند شراء كميات\n💡 جرّب 🧮 الحاسبة من محفظتي\n\nاختر نوع الاشتراك:", kb_shop())


async def show_wallet(update, context):
    uid = update.effective_user.id
    u = await db.get_user(uid)
    rank, total = await db.get_user_rank(uid)
    reset_flow(context, "wallet")
    await say(update, f"💎 <b>محفظتي</b>\n{sep()}\n💰 الرصيد: <code>{u['points']}</code>\n🏆 الترتيب: <code>{rank}</code> من <code>{total}</code>\n"
                      f"📈 إجمالي المكسوب: <code>{u['total_points_earned']}</code>\n👥 المدعوون: <code>{await db.count_invited(uid)}</code>\n"
                      f"🔥 سلسلة المكافأة: <code>{u.get('streak') or 0}</code> يوم", kb_wallet())


async def show_earn(update, context):
    reset_flow(context, "earn")
    await say(update, "🎁 <b>كسب النقاط</b>\n\n1️⃣ المكافأة اليومية (مضاعفة كل 7 أيام متتالية 🔥)\n2️⃣ دعوة الأصدقاء\n"
                      "3️⃣ 🎫 الدروبات والمزادات والتصويتات\n4️⃣ استلام تحويلات\n5️⃣ 🏪 بيع أكوادك في البازار", kb_earn())


async def show_top(update, context):
    reset_flow(context)
    uid = update.effective_user.id
    top = await db.top_users(5)
    my_rank, total = await db.get_user_rank(uid)
    me = await db.get_user(uid)
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
    lines = ["🏆 <b>أفضل 5:</b>\n"]
    for i, u in enumerate(top):
        ref = f"@{esc(u['username'])}" if u.get("username") else f"<code>{mask_id(u['user_id'])}</code>"
        lines.append(f"{medals[i]} <b>{esc(u.get('first_name') or 'مستخدم')}</b> — {ref}\n    💎 {u['points']}" + (" ← <b>أنت</b>" if u["user_id"] == uid else ""))
    lines.append("\n" + sep())
    if my_rank <= 5:
        lines.append(f"🎯 <b>ترتيبك:</b> {my_rank}")
    elif my_rank:
        diff = (top[-1]["points"] - me["points"]) if top else 0
        lines.append(f"🎯 <b>ترتيبك:</b> <code>{my_rank}</code> من <code>{total}</code>\n💎 <code>{me['points']}</code>\n⬆️ تحتاج <code>{max(1, diff + 1)}</code> نقطة للدخول")
    await say(update, "\n".join(lines), kb_main(is_admin(uid)))


async def show_guide(update, context):
    reset_flow(context)
    uid = update.effective_user.id
    last = await db.get_last_sub_code(uid)
    guide = ("📖 <b>دليل التفعيل</b>\n\n1️⃣ افتح الموقع\n2️⃣ سجّل الدخول\n3️⃣ القائمة الجانبية (☰)\n4️⃣ قسم الخطط\n"
             "5️⃣ انزل → «كود التفعيل»\n6️⃣ الصق الكود → تفعيل ✅\n\n")
    guide += (f"{sep()}🔑 <b>آخر كود:</b>\n<code>{esc(last['code'])}</code>\n📦 {esc(last['plan'])} — {last['days']}ي" if last
              else f"{sep()}⚠️ لا تملك كوداً بعد.")
    await say(update, guide + f"\n\n🌐 {esc(SITE_URL)}", kb_main(is_admin(uid)))


async def show_about(update, context):
    reset_flow(context)
    types = "\n".join(f"{i['emoji']} <b>{esc(i['name'])}</b> — {esc(i['description'])}" for i in SUBSCRIPTION_TYPES.values())
    chans = await db.list_channels("publish", active_only=True)
    ch = "\n".join(f"📢 @{esc(c['username'])}" for c in chans)
    await say(update, f"🤖 <b>Worm GPT</b>\n\nبوت إدارة اشتراكات احترافي\n\n📦 <b>الباقات:</b>\n{types}\n\n{ch}\n🌐 {esc(SITE_URL)}",
              kb_main(is_admin(update.effective_user.id)))


# ═══════════════════════════════════════════════════════════════════
# 📦 الاشتراكات + الأرشيف
# ═══════════════════════════════════════════════════════════════════
SRC_ICON = {"purchase": "🛒", "drop": "🎁", "auction": "🔨", "market": "🏪"}


async def subs_view(uid, mode="active", page=0):
    total = await db.count_user_subs(uid, mode)
    subs = await db.list_user_subs(uid, mode, PAGE_SIZE, page * PAGE_SIZE)
    title = "📦 <b>اشتراكاتي النشطة</b>" if mode == "active" else "📚 <b>الأرشيف</b> (المنتهية)"
    if not subs:
        return f"{title}\n\n{'📭 لا توجد اشتراكات نشطة.' if mode == 'active' else '📭 الأرشيف فارغ.'}", None
    lines, rows = [f"{title} ({total})\n"], []
    for s in subs:
        pi = plan_info(s["plan"])
        exp = parse_dt(s["expires_at"]) if s["expires_at"] else None
        if mode == "active" and exp:
            left = f"⏳ {human_delta((exp - datetime.now()).total_seconds())}"
        else:
            left = f"انتهى {fmt_dt(s['expires_at'])}"
        listed = " 🏪معروض" if s.get("listed") else ""
        lines.append(f"{SRC_ICON.get(s.get('source'), '📦')} {pi['emoji']} {esc(pi['name'])} — {s['days']}ي — {left}{listed}\n    <code>{esc(s['code'])}</code>")
        if mode == "active" and not s.get("listed"):
            rows.append([InlineKeyboardButton(f"🏪 اعرض للبيع — {pi['emoji']} {s['days']}ي", callback_data=f"mk_sellsub:{s['id']}")])
    other = "archive" if mode == "active" else "active"
    nav = []
    pages = max(1, -(-total // PAGE_SIZE))
    if page > 0: nav.append(InlineKeyboardButton("◀️", callback_data=f"subs_pg:{mode}:{page - 1}"))
    nav.append(InlineKeyboardButton(f"{page + 1}/{pages}", callback_data="noop"))
    if page + 1 < pages: nav.append(InlineKeyboardButton("▶️", callback_data=f"subs_pg:{mode}:{page + 1}"))
    rows.append(nav)
    rows.append([InlineKeyboardButton("📚 الأرشيف" if other == "archive" else "📦 النشطة", callback_data=f"subs_pg:{other}:0")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def show_subs(update, context, mode="active"):
    reset_flow(context, "subs")
    text, kb = await subs_view(update.effective_user.id, mode, 0)
    await say(update, text, kb_subs())
    if kb:
        await update.effective_chat.send_message("👇", reply_markup=kb)


async def show_archive(update, context):
    await show_subs(update, context, "archive")


async def subs_callback(update, context):
    q = update.callback_query
    if not await cb_guard(update):
        return
    _, mode, page = q.data.split(":")
    text, kb = await subs_view(q.from_user.id, mode, int(page))
    await q.answer()
    try:
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════
# 🛒 الشراء (حالات)
# ═══════════════════════════════════════════════════════════════════
async def buy_choose_type(update, context, sub_type):
    info = SUBSCRIPTION_TYPES[sub_type]
    user = await db.get_user(update.effective_user.id)
    context.user_data["buy"] = {"type": sub_type}
    context.user_data["state"] = "buy_days"
    prices = "\n".join(f"  • {d} يوم = {p} 💎" for d, p in info["prices"].items())
    await say(update, f"{info['emoji']} <b>{esc(info['name'])}</b>\n\n{esc(info['description'])}\n\n💰 <b>الأسعار:</b>\n{prices}\n\n"
                      f"💎 رصيدك: <code>{user['points']}</code>\n\n📅 اختر المدة:", kb_days(sub_type))


async def buy_choose_days(update, context, days):
    ctx = context.user_data.get("buy", {})
    sub_type = ctx.get("type")
    if not sub_type:
        await show_shop(update, context); return
    info = SUBSCRIPTION_TYPES[sub_type]
    if days not in info["prices"]:
        await say(update, "❌ مدة غير صحيحة", kb_days(sub_type)); return
    ctx["days"] = days
    user = await db.get_user(update.effective_user.id)
    context.user_data["state"] = "buy_qty"
    disc = " • ".join(f"{q}+ ⇒ {p}%" for q, p in QUANTITY_DISCOUNTS.items() if p)
    await say(update, f"{info['emoji']} <b>{esc(info['name'])}</b> — {days} يوم\n\n💰 سعر الوحدة: <code>{info['prices'][days]}</code>\n"
                      f"💎 رصيدك: <code>{user['points']}</code>\n\n🎁 الخصومات: {disc}\n\n🔢 اختر عدد الأكواد:", kb_quantity())


async def buy_choose_qty(update, context, qty):
    ctx = context.user_data.get("buy", {})
    sub_type, days = ctx.get("type"), ctx.get("days")
    if not sub_type or not days:
        await show_shop(update, context); return
    if not (1 <= qty <= MAX_QTY):
        await say(update, f"❌ العدد بين 1 و {MAX_QTY}", kb_quantity()); return
    info = SUBSCRIPTION_TYPES[sub_type]
    unit = info["prices"][days]
    total, subtotal, discount = calculate_price(unit, qty)
    user = await db.get_user(update.effective_user.id)
    if user["points"] < total:
        context.user_data["state"] = "buy_qty"
        await say(update, f"🔒 <b>نقاطك غير كافية</b>\n\nالمطلوب: <code>{total}</code> 💎\nلديك: <code>{user['points']}</code> 💎", kb_quantity()); return
    ctx.update(quantity=qty, total=total)
    context.user_data["state"] = "buy_confirm"
    dline = f"\n🎁 خصم: <code>{discount}%</code> (<code>-{subtotal - total}</code> 💎)" if discount else ""
    await say(update, f"📋 <b>ملخص الطلب</b>\n\n{info['emoji']} <b>{esc(info['name'])}</b>\n📅 {days} يوم\n🔢 {qty} كود\n\n{sep()}"
                      f"سعر الوحدة: <code>{unit}</code> 💎\nالمجموع: <code>{subtotal}</code> 💎{dline}\n{sep()}"
                      f"✅ <b>الإجمالي: <code>{total}</code> 💎</b>\n💎 رصيدك: <code>{user['points']}</code>\n💵 المتبقي: <code>{user['points'] - total}</code>", kb_confirm())


async def buy_confirm(update, context):
    uid = update.effective_user.id
    admin = is_admin(uid)
    ctx = context.user_data.get("buy", {})
    sub_type, days, qty = ctx.get("type"), ctx.get("days"), ctx.get("quantity")
    if not all([sub_type, days, qty]):
        await say(update, "❌ بيانات ناقصة.", kb_shop()); context.user_data["state"] = "shop"; return
    info = SUBSCRIPTION_TYPES[sub_type]
    unit = info["prices"][days]
    total, _, _ = calculate_price(unit, qty)
    context.user_data.pop("buy", None)
    context.user_data["state"] = "main"
    if not await db.deduct_points(uid, total, "purchase"):
        await say(update, "🔒 نقاطك غير كافية.", kb_main(admin)); return
    await say(update, f"⏳ <b>جاري إنشاء {qty} كود...</b>\n\n📦 {info['emoji']} {esc(info['name'])} — {days} يوم", kb_main(admin))
    codes, errors = [], []
    sem = asyncio.Semaphore(API_CONCURRENCY)
    per_code = max(total // qty, 0)

    async def one():
        async with sem:
            try:
                res = await api.create(info["api_plan"], days, 1)
            except Exception as e:
                errors.append(str(e)); return
            code = extract_code(res["data"]) if res.get("ok") else None
            if not code:
                errors.append(res.get("message") or "لا كود في الاستجابة"); return
            codes.append(code)
            try:
                await db.save_subscription(uid, {"code": code, "plan": sub_type, "actual_plan": info["api_plan"], "days": days,
                                                 "max_uses": 1, "points_spent": per_code, "source": "purchase", "response": res["data"]})
            except Exception:
                logger.exception("فشل حفظ الاشتراك")

    try:
        await asyncio.gather(*[one() for _ in range(qty)])
    except Exception:
        logger.exception("[BUY] خطأ غير متوقع")
    finally:
        actual = calculate_price(unit, len(codes))[0] if codes else 0
        if total - actual > 0:
            await db.add_points(uid, total - actual, "refund", count_total=False)
    if not codes:
        reason = esc(errors[0])[:200] if errors else ""
        extra = "\n\n💡 جرّب بلس أو برو." if sub_type == "premium" else ""
        await say(update, f"❌ <b>فشل الإنشاء</b>\n\n<code>{reason}</code>{extra}\n\n💎 أُعيدت نقاطك كاملة.", kb_main(admin))
        await raise_alert(context.bot, "warning", "buy_fail", f"فشل إنشاء كود شراء ({sub_type}): {errors[0] if errors else '?'}", 30)
        return
    failed = qty - len(codes)
    head = f"✅ <b>تم إنشاء {len(codes)} كود!</b>\n\n{info['emoji']} {esc(info['name'])} — {days} يوم\n💎 خُصم: <code>{actual}</code> نقطة\n"
    if failed:
        head += f"\n⚠️ فشل {failed} كود وأُعيدت نقاطها تلقائياً.\n"
    for i in range(0, len(codes), 40):
        chunk = "\n".join(f"<code>{esc(c)}</code>" for c in codes[i:i + 40])
        tail = f"\n\n📖 فعّلها في: {esc(SITE_URL)}" if i + 40 >= len(codes) else ""
        await say(update, (head + "\n🔑 <b>أكوادك:</b>\n" if i == 0 else "🔑 <b>تكملة:</b>\n") + chunk + tail, kb_main(admin))


# ═══════════════════════════════════════════════════════════════════
# 🧮 الحاسبة (Wizard)
# ═══════════════════════════════════════════════════════════════════
CALC_STEPS = [
    Step("plan", "🧮 <b>الحاسبة</b>\n\nاختر الخطة:", parse_plan, kb=lambda d: choice_kb([[BTN_PLUS, BTN_PRO, BTN_PREMIUM]])),
    Step("days", "📅 اختر المدة:", parse_days_for("plan"), kb=lambda d: choice_kb(days_rows(d["plan"]))),
    Step("qty", f"🔢 كم كوداً؟ (1–{MAX_QTY})", parse_int(1, MAX_QTY, "العدد"), kb=lambda d: choice_kb([["1", "2", "3"], ["5", "10", "20"]])),
]


async def calc_done(update, context, d):
    uid = update.effective_user.id
    u = await db.get_user(uid)
    info = SUBSCRIPTION_TYPES[d["plan"]]
    unit = info["prices"][d["days"]]
    total, subtotal, pct = calculate_price(unit, d["qty"])
    lines = [f"🧮 <b>نتيجة الحساب</b>\n{sep()}", f"{info['emoji']} {esc(info['name'])} — {d['days']}ي × {d['qty']}",
             f"💰 سعر الوحدة: {unit}", f"📊 المجموع: {subtotal}" + (f" — خصم {pct}% ⇒ <b>{total}</b> 💎" if pct else f" ⇒ <b>{total}</b> 💎"),
             f"💎 رصيدك: {u['points']}"]
    missing = total - u["points"]
    if missing <= 0:
        lines.append("✅ <b>رصيدك يكفي — اشترِ الآن!</b>")
    else:
        lines += [f"⛔ ينقصك: <b>{missing}</b> 💎", f"🎁 ≈ {-(-missing // max(POINTS_DAILY, 1))} يوم من المكافأة اليومية",
                  f"👥 أو ≈ {-(-missing // max(POINTS_INVITE, 1))} دعوة"]
        best = max((q for q in QUANTITY_DISCOUNTS if q <= d["qty"]), default=1)
        nxt = next((q for q in sorted(QUANTITY_DISCOUNTS) if q > d["qty"]), None)
        if nxt:
            t2, _, p2 = calculate_price(unit, nxt)
            lines.append(f"💡 عند {nxt} أكواد خصم {p2}% (سعر الكود {t2 / nxt:.1f})")
    await say(update, "\n".join(lines), kb_wallet())
    context.user_data["state"] = "wallet"


async def calc_exit(update, context):
    await show_wallet(update, context)


register_wizard("calc", CALC_STEPS, calc_done, calc_exit)


async def calc_begin(update, context):
    await wiz_begin(update, context, "calc")


# ═══════════════════════════════════════════════════════════════════
# 💰 مكافأة + دعوة + معرّف + تحويل (Wizard)
# ═══════════════════════════════════════════════════════════════════
async def do_daily(update, context):
    uid = update.effective_user.id
    r = await db.claim_daily_bonus(uid)
    if r["ok"]:
        extra = "\n🔥 <b>مكافأة مضاعفة لسلسلة 7 أيام!</b>" if r["streak"] % 7 == 0 else ""
        msg = f"✅ حصلت على <b>{r['bonus']}</b> نقطة!\n🔥 سلسلتك: <b>{r['streak']}</b> يوم{extra}"
    else:
        msg = f"⏳ المكافأة التالية بعد: <b>{human_delta(r['remaining'])}</b>"
    await say(update, msg, kb_for_state(context, is_admin(uid)))


async def show_invite(update, context):
    uid = update.effective_user.id
    link = f"https://t.me/{context.bot.username}?start=ref_{uid}"
    await say(update, f"🔗 <b>رابط دعوتك:</b>\n<code>{esc(link)}</code>\n\n💰 {POINTS_INVITE} نقطة لكل صديق ينضم!\n"
                      f"👥 دعوت حتى الآن: <b>{await db.count_invited(uid)}</b>", kb_for_state(context, is_admin(uid)))


async def show_my_id(update, context):
    uid = update.effective_user.id
    await say(update, f"🆔 <b>معرّفك:</b>\n<code>{uid}</code>\n\nأعطه لمن يريد تحويل نقاط لك.", kb_for_state(context, is_admin(uid)))


async def _parse_target(text, data):
    t = text.strip()
    if not t.isdigit():
        raise ValueError("معرّف غير صالح (أرقام فقط)")
    tid = int(t)
    if data["me"] == tid:
        raise ValueError("لا يمكن التحويل لنفسك")
    target = await db.get_user(tid)
    if not target or target["is_banned"]:
        raise ValueError("المستخدم غير مسجل في البوت")
    data["target_name"] = user_label(target)
    return tid


def _parse_amount(text, data):
    t = text.strip()
    if not t.isdigit() or int(t) < MIN_TRANSFER:
        raise ValueError(f"أرسل رقماً صحيحاً (الحد الأدنى {MIN_TRANSFER})")
    return int(t)


async def _transfer_summary(d):
    return f"📋 <b>تأكيد التحويل:</b>\n\n👤 <b>{esc(d['target_name'])}</b>\n💎 <code>{d['amount']}</code> نقطة"


async def transfer_done(update, context, d):
    uid = update.effective_user.id
    ok, msg = await db.transfer_points(uid, d["target"], d["amount"])
    admin = is_admin(uid)
    if ok:
        await say(update, f"✅ <b>تم التحويل!</b>\n\n👤 <b>{esc(d['target_name'])}</b>\n💎 <code>{d['amount']}</code>", kb_main(admin))
        spawn(context, notify_users(context.bot, [d["target"]], f"🎁 <b>وصلك تحويل!</b>\n\n💎 <code>{d['amount']}</code> نقطة من <b>{esc(update.effective_user.first_name)}</b>"))
    else:
        await say(update, f"❌ {esc(msg)}", kb_main(admin))


async def transfer_exit(update, context):
    await show_wallet(update, context)


register_wizard("transfer", [
    Step("target", "💸 <b>تحويل نقاط</b>\n\nأرسل <b>معرّف (ID)</b> المستخدم:\n<i>(يراه في زر 🆔 معرفي)</i>", _parse_target),
    Step("amount", lambda d: f"👤 <b>{esc(d['target_name'])}</b>\n\n💎 أرسل عدد النقاط:", _parse_amount)],
    transfer_done, transfer_exit, summary=_transfer_summary)


async def transfer_begin(update, context):
    u = await db.get_user(update.effective_user.id)
    await say(update, f"💎 رصيدك: <code>{u['points']}</code>")
    await wiz_begin(update, context, "transfer", {"me": update.effective_user.id})


# ═══════════════════════════════════════════════════════════════════
# 🔍 فحص كود (Wizard)
# ═══════════════════════════════════════════════════════════════════
def _parse_code(text, data):
    t = text.strip()
    if len(t) < 3 or len(t) > 100 or " " in t:
        raise ValueError("كود غير صالح")
    return t


async def check_done(update, context, d):
    code = d["code"]
    admin = is_admin(update.effective_user.id)
    wait = await say(update, "🔍 جاري الفحص...")
    result = await api.check_code(code)

    async def finish(msg):
        try:
            await wait.edit_text(msg, parse_mode=ParseMode.HTML)
        except Exception:
            await say(update, msg)
        await say(update, "💡 افحص كوداً آخر أو استخدم الأزرار:", kb_main(admin))

    if not result["ok"]:
        await finish(f"❌ <b>فشل الفحص</b>\n\n🔍 <b>السبب:</b> <code>{esc(result.get('message', 'غير معروف'))[:200]}</code>"); return
    data = result["data"]
    info = data.get("data") if isinstance(data, dict) and isinstance(data.get("data"), dict) else data
    if not isinstance(info, dict):
        await finish(f"📋 <b>النتيجة:</b>\n<pre>{esc(str(data)[:500])}</pre>"); return
    pinfo = plan_info(info.get("plan", "?"))
    uses, mx, status = info.get("uses", 0), info.get("max_uses", "?"), info.get("status", "?")
    smap = {"active": "🟢 نشط", "used": "🔴 مستخدم بالكامل", "expired": "⏰ منتهي", "valid": "🟢 صالح"}
    ut = f"<code>{esc(uses)}/{esc(mx)}</code>" + (" ⚠️ مستنفد" if isinstance(uses, int) and isinstance(mx, int) and uses >= mx else "")
    await finish(f"✅ <b>نتيجة الفحص</b>\n\n🔑 <code>{esc(info.get('code', code))}</code>\n\n{sep()}{pinfo['emoji']} <b>النوع:</b> {esc(pinfo['name'])}\n"
                 f"📅 <b>المدة:</b> <code>{esc(info.get('days', '?'))}</code> يوم\n🔢 <b>الاستخدامات:</b> {ut}\n"
                 f"📊 <b>الحالة:</b> {smap.get(status, 'ℹ️ ' + esc(status))}\n{sep()}📆 <b>أُنشئ:</b> {fmt_dt(info.get('created_at'))}\n⏰ <b>ينتهي:</b> {fmt_dt(info.get('expires_at'))}")


async def check_exit(update, context):
    await show_home(update, context)


register_wizard("check", [Step("code", "🔍 <b>فحص كود اشتراك</b>\n\nأرسل الكود الذي تريد فحصه.\n\n❌ للإلغاء", _parse_code)],
                check_done, check_exit)


async def check_code_begin(update, context):
    await wiz_begin(update, context, "check")


# ═══════════════════════════════════════════════════════════════════
# 📊 حالتي (ودجت)
# ═══════════════════════════════════════════════════════════════════
async def status_widget(uid):
    u = await db.get_user(uid)
    rank, total = await db.get_user_rank(uid)
    active = await db.count_user_subs(uid, "active")
    nxt_exp = await db.next_expiry(uid)
    (th, lvl), nxt = level_of(u["total_points_earned"])
    if nxt:
        prog = (u["total_points_earned"] - th) / max(nxt[0] - th, 1)
        lvl_line = f"{lvl}\n{bar(int(prog * 100), 100)} {int(prog * 100)}% → {nxt[1]}"
    else:
        lvl_line = f"{lvl} — المستوى الأعلى!"
    theme_name = THEMES.get(CUR_THEME.get(), THEMES["dark"])[0]
    icon = {"light": "☀️", "dark": "🌙", "minimal": "✨"}.get(CUR_THEME.get(), "🌙")
    lines = [f"{icon} <b>حالتي</b> — {esc(user_label(u))}", sep(), f"🎖 المستوى: {lvl_line}", "",
             f"💎 الرصيد: <b>{u['points']}</b>", f"🏆 الترتيب: <b>{rank}</b>/{total}", f"🔥 السلسلة: <b>{u.get('streak') or 0}</b> يوم",
             f"📦 اشتراكات نشطة: <b>{active}</b>" + (f" — أقربها ينتهي بعد {human_delta((parse_dt(nxt_exp) - datetime.now()).total_seconds())}" if nxt_exp else ""),
             f"👥 دعوات: <b>{await db.count_invited(uid)}</b>", sep() + f"🌍 {u.get('lang', 'ar').upper()} • {theme_name} • "
             f"🔔 {'✅' if u.get('notif_drops') else '❌'}"]
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔄 تحديث", callback_data="widget:refresh"),
                                InlineKeyboardButton("🎁 المكافأة", callback_data="widget:daily")]])
    return "\n".join(lines), kb


async def show_status(update, context):
    reset_flow(context)
    text, kb = await status_widget(update.effective_user.id)
    await say(update, text, kb_main(is_admin(update.effective_user.id)))
    await update.effective_chat.send_message(text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def widget_callback(update, context):
    q = update.callback_query
    if not await cb_guard(update):
        return
    if q.data == "widget:daily":
        r = await db.claim_daily_bonus(q.from_user.id)
        await q.answer(f"✅ +{r['bonus']} 💎 (سلسلة {r['streak']})" if r["ok"] else f"⏳ بعد {human_delta(r['remaining'])}", show_alert=True)
    else:
        await q.answer("✅ تم التحديث")
    text, kb = await status_widget(q.from_user.id)
    try:
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════
# 🌍🌙🔔 الإعدادات
# ═══════════════════════════════════════════════════════════════════
def lang_kb(cur):
    return InlineKeyboardMarkup([[InlineKeyboardButton(("✅ " if cur == "ar" else "") + "🇸🇦 العربية", callback_data="set_lang:ar"),
                                  InlineKeyboardButton(("✅ " if cur == "en" else "") + "🇬🇧 English", callback_data="set_lang:en")]])


def theme_kb(cur):
    return InlineKeyboardMarkup([[InlineKeyboardButton(("✅ " if cur == k else "") + v[0], callback_data=f"set_theme:{k}")] for k, v in THEMES.items()])


def notif_kb(u):
    def b(label, field):
        return [InlineKeyboardButton(f"{'✅' if u.get(field) else '❌'} {label}", callback_data=f"set_notif:{field}")]
    return InlineKeyboardMarkup([b("🎁 تنبيهات الدروبات والمزادات", "notif_drops"), b("⏰ تذكير انتهاء الاشتراك", "notif_expiry"),
                                 b("📣 الأخبار والإعلانات", "notif_news")])


async def show_lang(update, context):
    reset_flow(context)
    u = await db.get_user(update.effective_user.id)
    await say(update, "🌍 <b>اللغة</b>\n\nاختر لغة الواجهة:", lang_kb(u["lang"]))


async def show_theme(update, context):
    reset_flow(context)
    u = await db.get_user(update.effective_user.id)
    await say(update, "🌙 <b>المظهر</b>\n\nاختر شكل البطاقات والفواصل:", theme_kb(u["theme"]))


async def show_notif(update, context):
    reset_flow(context)
    u = await db.get_user(update.effective_user.id)
    await say(update, "🔔 <b>الإشعارات</b>\n\nاضغط لتفعيل/تعطيل:", notif_kb(u))


async def settings_callback(update, context):
    q = update.callback_query
    user = await cb_guard(update)
    if not user:
        return
    act, val = q.data.split(":")
    uid = q.from_user.id
    if act == "set_lang" and val in ("ar", "en"):
        await db.set_pref(uid, "lang", val)
        u = await db.get_user(uid); apply_prefs(u)
        await q.answer("✅")
        try:
            await q.edit_message_text(tr("🌍 <b>اللغة</b>\n\nاختر لغة الواجهة:"), parse_mode=ParseMode.HTML, reply_markup=lang_kb(val))
        except Exception:
            pass
        await q.message.chat.send_message("✅", reply_markup=kb_main(is_admin(uid)))     # تحديث لوحة الأزرار بلغة جديدة
    elif act == "set_theme" and val in THEMES:
        await db.set_pref(uid, "theme", val)
        u = await db.get_user(uid); apply_prefs(u)
        await q.answer("✅")
        try:
            await q.edit_message_text(tr("🌙 <b>المظهر</b>\n\nاختر شكل البطاقات والفواصل:") + f"\n\n{sep()}معاينة", parse_mode=ParseMode.HTML, reply_markup=theme_kb(val))
        except Exception:
            pass
    elif act == "set_notif" and val in ("notif_drops", "notif_expiry", "notif_news"):
        await db.set_pref(uid, val, 0 if user[val] else 1)
        u = await db.get_user(uid)
        await q.answer("✅")
        try:
            await q.edit_message_reply_markup(reply_markup=notif_kb(u))
        except Exception:
            pass
    else:
        await q.answer()


async def expiry_reminder_job(context):
    """تذكير يومي بالاشتراكات التي تنتهي خلال 3 أيام"""
    try:
        for s in await db.subs_to_remind(3):
            pi = plan_info(s["plan"])
            left = human_delta((parse_dt(s["expires_at"]) - datetime.now()).total_seconds())
            await notify_users(context.bot, [s["user_id"]],
                               f"⏰ <b>اشتراكك سينتهي قريباً</b>\n\n{pi['emoji']} {esc(pi['name'])} — ينتهي بعد <b>{left}</b>\n🛒 جدّد من المتجر!")
            await db.mark_reminded(s["id"])
    except Exception:
        logger.exception("[REMINDER] خطأ")


# ═══════════════════════════════════════════════════════════════════
# ⚙️ أوامر المستخدم الأساسية
# ═══════════════════════════════════════════════════════════════════
@user_locked
async def cancel_cmd(update, context):
    await show_home(update, context)
