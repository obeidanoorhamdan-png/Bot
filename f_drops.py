"""f_drops.py — منظومة الدروبات: عادي، بيضاوي، بشرط، تسلسلي، تصويت، مزاد، نقاط، مجدول + المستخدم"""
import asyncio
import json
import random
import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode

from config import *  # noqa
from database import DBError, db
from services import *  # noqa

KINDS = {
    "normal": ("🎁", "عادي"), "egg": ("🥚", "بيضاوي"), "cond": ("🔒", "بشرط"),
    "chain": ("🔗", "تسلسلي"), "points": ("💎", "نقاط"),
}
POOL_LABELS = {"light": "🥚 خفيفة", "medium": "🥚 متوسطة", "gold": "🥚 ذهبية"}
COND_LABELS = {"invites": "👥 عدد الدعوات", "age": "📅 عمر الحساب (أيام)", "points": "💎 حد أدنى للنقاط",
               "channel": "📢 عضوية قناة", "has_sub": "📦 لديه اشتراك سابق"}
PLAN_SYNONYMS = {"plus": "plus", "بلس": "plus", "pro": "pro", "برو": "pro", "premium": "premium", "بريميوم": "premium"}
tick_lock = asyncio.Lock()


def bot_link(username, token, prefix="drop"):
    return f"https://t.me/{username}?start={prefix}_{token}"


def drop_extra(d):
    try:
        return json.loads(d.get("extra") or "{}")
    except ValueError:
        return {}


def prize_text(d):
    kind = d["kind"]
    if kind == "egg":
        return f"🥚 جائزة مفاجئة — مجموعة {POOL_LABELS.get(drop_extra(d).get('pool'), '')}"
    if d.get("prize_points") and not d.get("plan"):
        return f"💎 <b>{d['prize_points']}</b> نقطة"
    i = plan_info(d["plan"])
    return f"{i['emoji']} <b>{esc(i['name'])}</b> — {d['days']} يوم"


def cond_text(d):
    t, v = d.get("cond_type"), d.get("cond_value")
    return {"invites": f"👥 دعوة {v}+ أصدقاء", "age": f"📅 حساب عمره {v}+ يوم", "points": f"💎 رصيد {v}+ نقطة",
            "channel": f"📢 عضوية @{esc(v)}", "has_sub": "📦 اشتراك سابق في البوت"}.get(t, "")


# ═══════════════════════════════════════════════════════════════════
# 🎨 البطاقات
# ═══════════════════════════════════════════════════════════════════
async def render_drop(bot, d):
    emoji, label = KINDS.get(d["kind"], ("🎁", ""))
    extra = drop_extra(d)
    lines = [f"{emoji} <b>دروب {label} #{esc(d['token'])}</b>", "", prize_text(d)]
    if d["kind"] == "chain":
        lines.append(f"🔗 المرحلة <b>{d['stage']}</b> من <b>{extra.get('stages', '?')}</b>"
                     + ("  — يتطلب الفوز بالمرحلة السابقة" if d.get("parent_id") else ""))
    if d.get("cond_type"):
        lines.append(f"🔒 الشرط: {cond_text(d)}")
    if extra.get("poll"):
        lines.append("🗳️ نتيجة تصويت الجمهور")
    mw, cc = d["max_winners"], d["claims_count"]
    line = "━━━━━━━━━━━━━━━━━"
    lines += ["", line]
    if d["is_deleted"] and cc == 0:
        status = "⚫ <b>ملغي</b>"
    elif not d["published"]:
        status = f"⏳ <b>مجدول</b> — {fmt_dt(d.get('publish_at'))}"
    elif cc >= mw:
        status = "🔴 <b>اكتمل</b>"
    elif d["is_deleted"]:
        status = "⚫ <b>أُوقف</b>"
    else:
        status = "🟢 <b>متاح — أسرع من يفوز!</b>"
    lines.append(status)
    if mw > 1 or cc:
        lines.append(f"👥 الفائزون: <b>{cc}/{mw}</b>  {bar(cc, mw, 8)}")
    if cc:
        claims = await db.get_claims(d["id"], 5)
        lines.append("🏅 " + " • ".join(f"<code>{mask_id(c['user_id'])}</code>" for c in claims))
    lines.append(f"⏰ {relative_time(d.get('created_at'))}")
    lines.append(line)
    return "\n".join(lines)


def drop_kb(bot_username, d):
    rows = []
    open_ = d["published"] and d["is_active"] and not d["is_deleted"] and d["claims_count"] < d["max_winners"]
    if open_:
        rows.append([InlineKeyboardButton("🔗 افتح الدروب الآن", url=bot_link(bot_username, d["token"]))])
    rows.append([InlineKeyboardButton("📊 الحالة", callback_data=f"drop_status_{d['id']}"),
                 InlineKeyboardButton("🔄 تحديث", callback_data=f"drop_refresh_{d['id']}")])
    return InlineKeyboardMarkup(rows)


async def render_poll(bot, p):
    opts = json.loads(p["options"])
    counts = await db.poll_counts(p["id"], len(opts))
    total = sum(counts)
    nums = ["1️⃣", "2️⃣", "3️⃣", "4️⃣"]
    lines = [f"🗳️ <b>دروب بتصويت #{esc(p['token'])}</b>", "",
             f"صوّت للجائزة التي تريدها! الخيار الفائز يتحول إلى دروب لـ <b>{p['winners']}</b> فائز.", ""]
    for i, o in enumerate(opts):
        pi = plan_info(o["plan"])
        pct = counts[i] * 100 // total if total else 0
        lines.append(f"{nums[i]} {pi['emoji']} {esc(pi['name'])} — {o['days']} يوم\n    {bar(counts[i], max(total, 1))} {counts[i]} ({pct}%)")
    lines += ["", f"👥 المصوّتون: <b>{total}</b>"]
    if p["status"] == "open":
        lines.append(f"⏰ ينتهي بعد: <b>{human_delta((parse_dt(p['ends_at']) - datetime.now()).total_seconds())}</b>")
    elif p["status"] == "closed" and p["winner_idx"] is not None:
        w = opts[p["winner_idx"]]
        lines.append(f"🏆 <b>الفائز:</b> {plan_info(w['plan'])['emoji']} {esc(plan_info(w['plan'])['name'])} — {w['days']} يوم")
    elif p["status"] == "closed":
        lines.append("⚪ انتهى بلا أصوات")
    elif p["status"] == "cancelled":
        lines.append("⚫ ملغي")
    return "\n".join(lines), opts


def poll_kb(p, opts, bot_username=None, result_drop=None):
    rows = []
    if p["status"] == "open":
        nums = ["1️⃣", "2️⃣", "3️⃣", "4️⃣"]
        for i, o in enumerate(opts):
            rows.append([InlineKeyboardButton(f"{nums[i]} {plan_info(o['plan'])['emoji']} {o['days']}ي",
                                              callback_data=f"poll_vote:{p['id']}:{i}")])
        rows.append([InlineKeyboardButton("🔄 تحديث", callback_data=f"poll_refresh:{p['id']}")])
    elif result_drop and bot_username:
        rows.append([InlineKeyboardButton("🎁 افتح دروب النتيجة", url=bot_link(bot_username, result_drop["token"]))])
    return InlineKeyboardMarkup(rows) if rows else None


def auction_min_bid(a):
    return a["start_bid"] if a["high_bidder"] is None else a["high_bid"] + a["step"]


async def render_auction(bot, a):
    pi = plan_info(a["plan"])
    lines = [f"🔨 <b>مزاد #{esc(a['token'])}</b>", "", f"🎁 {pi['emoji']} <b>{esc(pi['name'])}</b> — {a['days']} يوم", ""]
    if a["status"] in ("open", "pending"):
        lines.append(f"🏆 أعلى مزايدة: " + (f"<b>{a['high_bid']}</b> 💎 — <code>{mask_id(a['high_bidder'])}</code>"
                                           if a["high_bidder"] else f"لا مزايدات (يبدأ من <b>{a['start_bid']}</b> 💎)"))
        lines.append(f"📈 الحد الأدنى التالي: <b>{auction_min_bid(a)}</b> 💎")
        lines.append(f"🔢 عدد المزايدات: {a['bids_count']}")
        if a["status"] == "open":
            lines.append(f"⏰ ينتهي بعد: <b>{human_delta((parse_dt(a['ends_at']) - datetime.now()).total_seconds())}</b>")
        lines += ["", "⚠️ المزايدة التزام: يُخصم المبلغ من الفائز عند الإغلاق.", "⏱ أي مزايدة في آخر دقيقتين تمدّ المزاد دقيقتين."]
    elif a["status"] == "closed":
        lines.append(f"🔴 <b>انتهى</b> — الفائز <code>{mask_id(a['winner_id'])}</code> بمبلغ <b>{a['final_price']}</b> 💎")
    elif a["status"] == "cancelled":
        lines.append("⚫ <b>ملغي</b>")
    else:
        lines.append("⚪ <b>انتهى بلا فائز</b>")
    return "\n".join(lines)


def auction_kb(a):
    if a["status"] != "open":
        return None
    m = auction_min_bid(a)
    amounts = sorted({m, m + a["step"] * 2, m + a["step"] * 5})
    rows = [[InlineKeyboardButton(f"⬆️ {x} 💎", callback_data=f"auc_bid:{a['id']}:{x}") for x in amounts]]
    rows.append([InlineKeyboardButton("🔄 تحديث", callback_data=f"auc_refresh:{a['id']}")])
    return InlineKeyboardMarkup(rows)


async def render_entity(bot, entity, eid):
    if entity == "drop":
        d = await db.get_drop_by_id(eid)
        return (await render_drop(bot, d), drop_kb(bot.username, d)) if d else (None, None)
    if entity == "poll":
        p = await db.get_poll(eid)
        if not p:
            return None, None
        text, opts = await render_poll(bot, p)
        rd = await db.get_drop_by_id(p["result_drop_id"]) if p["result_drop_id"] else None
        return text, poll_kb(p, opts, bot.username, rd)
    a = await db.get_auction(eid)
    return (await render_auction(bot, a), auction_kb(a)) if a else (None, None)


async def refresh_entity(bot, entity, eid):
    text, kb = await render_entity(bot, entity, eid)
    if text is None:
        return False
    return await refresh_posts(bot, entity, eid, text, kb)


# ═══════════════════════════════════════════════════════════════════
# 📡 النشر والإعلان
# ═══════════════════════════════════════════════════════════════════
async def announce(app, entity, eid, headline):
    ids = await db.users_for_notify("notif_drops")
    if not ids:
        return
    bot = app.bot
    row = {"drop": db.get_drop_by_id, "poll": db.get_poll, "auction": db.get_auction}[entity]
    r = await row(eid)
    urls = await _post_urls(entity, eid)
    if entity == "drop":
        url = bot_link(bot.username, r["token"])
    else:
        url = urls[0] if urls else None
    markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔗 افتح", url=url)]]) if url else None
    spawn(app, notify_users(bot, ids, f"{headline}\n\n🔕 يمكنك إيقاف هذه الإشعارات من «🔔 الإشعارات».", markup))


async def publish_entity(app, entity, eid, notify=True):
    bot = app.bot
    if entity == "drop":
        await db.set_published("drop", eid)
    elif entity == "poll":
        await db.open_poll(eid)
    else:
        await db.open_auction(eid)
    text, kb = await render_entity(bot, entity, eid)
    ok, errs = await publish_to_channels(bot, entity, eid, text, kb)
    if notify:
        head = {"drop": "🎁 <b>دروب جديد متاح الآن!</b>", "poll": "🗳️ <b>تصويت جديد!</b> شارك الآن",
                "auction": "🔨 <b>مزاد جديد!</b> زايد الآن"}[entity]
        await announce(app, entity, eid, head)
    return ok, errs


async def _post_urls(entity, eid):
    urls = []
    chans = {c["chat_id"]: c for c in await db.list_channels()}
    for p in await db.get_posts(entity, eid):
        ch = chans.get(p["chat_id"])
        if ch and ch["username"]:
            urls.append(post_link(ch["username"], p["message_id"]))
    return urls


# ═══════════════════════════════════════════════════════════════════
# 🎯 الاستلام (Claim)
# ═══════════════════════════════════════════════════════════════════
async def check_condition(bot, user, d):
    t, v = d.get("cond_type"), d.get("cond_value")
    uid = user["user_id"]
    if not t:
        return True, ""
    if t == "invites":
        n = await db.count_invited(uid)
        return (n >= int(v)), f"🔒 تحتاج دعوة {v} أصدقاء (لديك {n})"
    if t == "age":
        j = parse_dt(user["joined_at"])
        days = (datetime.now() - j).days if j else 0
        return (days >= int(v)), f"🔒 يجب أن يكون حسابك أقدم من {v} يوم (عمره {days})"
    if t == "points":
        return (user["points"] >= int(v)), f"🔒 تحتاج رصيد {v} نقطة على الأقل (لديك {user['points']})"
    if t == "has_sub":
        n = await db.count_user_subs(uid)
        return (n > 0), "🔒 هذا الدروب لمن لديه اشتراك سابق في البوت"
    if t == "channel":
        try:
            m = await bot.get_chat_member(chat_id=f"@{v}", user_id=uid)
            ok = m.status in ("member", "administrator", "creator")
        except Exception:
            ok = True      # لا نعاقب المستخدم إن تعذر الفحص
        return ok, f"🔒 انضم إلى @{v} أولاً ثم أعد المحاولة"
    return True, ""


def roll_egg(pool_name):
    pool = EGG_POOLS.get(pool_name, EGG_POOLS["light"])
    kind, value, _ = random.choices(pool, weights=[p[2] for p in pool], k=1)[0]
    return kind, value


async def create_sub_code(plan, days):
    info = SUBSCRIPTION_TYPES[plan]
    res = await api.create(info["api_plan"], days, 1)
    code = extract_code(res["data"]) if res.get("ok") else None
    return code, res


async def handle_drop_entry(update, context, token):
    tg = update.effective_user
    admin = is_admin(tg.id)
    user = await db.get_user(tg.id)
    if not user:
        user, _, _ = await db.register_user(tg.id, tg.username, tg.first_name)
    apply_prefs(user)
    d = await db.get_drop(token)
    if not d or d["is_deleted"] or not d["published"]:
        await say(update, "😔 <b>الدروب غير موجود أو ملغي.</b>", kb_main(admin)); return
    if d["claims_count"] >= d["max_winners"] or not d["is_active"]:
        await say(update, "😔 <b>حظاً أوفر!</b>\n\nاكتمل عدد الفائزين.", kb_main(admin)); return

    ok, msg = await check_condition(context.bot, user, d)
    if not ok:
        await say(update, msg, kb_main(admin)); return
    if d.get("parent_id"):
        if not await db.user_has_claim(d["parent_id"], tg.id):
            await say(update, "🔗 هذه مرحلة متقدمة: يجب أن تفوز بالمرحلة السابقة أولاً.", kb_main(admin)); return

    claim_id, reason = await db.reserve_claim(d["id"], tg.id)
    if not claim_id:
        txt = {"dup": "ℹ️ لقد شاركت في هذا الدروب مسبقاً.", "full": "😔 <b>حظاً أوفر!</b> اكتمل العدد.",
               "inactive": "😔 الدروب غير متاح."}[reason]
        await say(update, txt, kb_main(admin)); return

    await say(update, "🎁 جاري تجهيز جائزتك...", kb_main(admin))
    extra = drop_extra(d)
    prize_type, prize_val, code, note = None, None, None, ""

    try:
        if d["kind"] == "egg":
            kind, val = roll_egg(extra.get("pool", "light"))
            if kind == "sub":
                plan, days = val
                code, _ = await create_sub_code(plan, days)
                if code:
                    prize_type, prize_val = "sub", f"{plan}:{days}"
                else:                      # تعويض بنقاط بدل فشل الـ API
                    pts = SUBSCRIPTION_TYPES[plan]["prices"][days]
                    prize_type, prize_val, note = "points", pts, "\n(تعذر إنشاء الاشتراك فحصلت على قيمته نقاطاً)"
            else:
                prize_type, prize_val = "points", val
        elif d.get("prize_points") and not d.get("plan"):
            prize_type, prize_val = "points", d["prize_points"]
        else:
            code, res = await create_sub_code(d["plan"], d["days"])
            if not code:
                await db.release_claim(claim_id)
                await say(update, f"❌ تعذر إنشاء الكود الآن، مقعدك محفوظ — حاول مجدداً بعد قليل.\n<code>{esc(res.get('message', ''))[:150]}</code>", kb_main(admin))
                return
            prize_type, prize_val = "sub", f"{d['plan']}:{d['days']}"
    except Exception:
        logger.exception("[DROP] فشل تجهيز الجائزة")
        await db.release_claim(claim_id)
        await say(update, "❌ حدث خطأ، حاول مجدداً.", kb_main(admin)); return

    if prize_type == "points":
        await db.add_points(tg.id, int(prize_val), "drop_prize", d["id"])
        await db.finalize_claim(claim_id, "points", prize_val)
        await say(update, f"🎉 <b>مبروك!</b> فزت بـ <b>{prize_val}</b> نقطة 💎{note}", kb_main(admin))
    else:
        plan, days = prize_val.split(":")
        await db.finalize_claim(claim_id, "sub", prize_val, code)
        await db.save_subscription(tg.id, {"code": code, "plan": plan, "days": int(days), "max_uses": 1,
                                           "points_spent": 0, "source": "drop", "response": {}})
        pi = plan_info(plan)
        await say(update, f"🎉 <b>مبروك! فزت بالدروب!</b>\n\n{pi['emoji']} {esc(pi['name'])} — {days} يوم\n"
                          f"🔑 <b>كودك:</b> <code>{esc(code)}</code>\n\n📖 فعّله في: {esc(SITE_URL)}{note}", kb_main(admin))
        await notify_admins(context.bot, f"🔔 <b>دروب استُخدم</b> #{esc(d['token'])}\n👤 <code>{tg.id}</code> {('@' + esc(tg.username)) if tg.username else ''}\n"
                                         f"🎁 {pi['emoji']} {esc(pi['name'])} {days}ي\n🔑 <code>{esc(code)}</code>")
    await refresh_entity(context.bot, "drop", d["id"])


async def handle_points_drop_entry(update, context, token):
    """دروب النقاط القديم (توافق مع الروابط السابقة)"""
    tg = update.effective_user
    admin = is_admin(tg.id)
    await db.register_user(tg.id, tg.username, tg.first_name)
    pd = await db.get_points_drop(token)
    if not pd or not pd["is_active"] or pd["claimed_by"] or not await db.try_claim_points_drop(token, tg.id):
        await say(update, "😔 <b>حظاً أوفر!</b>", kb_main(admin)); return
    await db.add_points(tg.id, pd["points"], "points_drop_claim", pd["id"])
    await say(update, f"🎉 <b>مبروك!</b> حصلت على <b>{pd['points']}</b> نقطة!", kb_main(admin))


# ─── أزرار البطاقات (قناة) ───
async def drop_status_callback(update, context):
    q = update.callback_query
    try:
        did = int(q.data.rsplit("_", 1)[1])
    except (ValueError, IndexError):
        await q.answer(); return
    d = await db.get_drop_by_id(did)
    if not d:
        await q.answer("❌ غير موجود", show_alert=True); return
    if d["is_deleted"] and d["claims_count"] == 0:
        msg = "⚫ ملغي"
    elif d["claims_count"] >= d["max_winners"]:
        msg = f"🔴 اكتمل ({d['claims_count']}/{d['max_winners']})"
    else:
        msg = f"🟢 متاح\n👥 {d['claims_count']}/{d['max_winners']}\n⏰ {relative_time(d.get('created_at'))}"
    await q.answer(msg, show_alert=True)
    await refresh_entity(context.bot, "drop", did)


async def drop_refresh_callback(update, context):
    q = update.callback_query
    try:
        did = int(q.data.rsplit("_", 1)[1])
    except (ValueError, IndexError):
        await q.answer(); return
    ok = await refresh_entity(context.bot, "drop", did)
    await q.answer("✅ تم التحديث" if ok else "ℹ️ لا تغيير")


async def poll_callback(update, context):
    q = update.callback_query
    parts = q.data.split(":")
    user = await cb_guard(update)
    if not user:
        return
    pid = int(parts[1])
    if parts[0] == "poll_refresh":
        await q.answer("🔄")
        await refresh_entity(context.bot, "poll", pid); return
    try:
        await db.vote(pid, q.from_user.id, int(parts[2]))
    except DBError as e:
        await q.answer(str(e), show_alert=True); return
    await q.answer("✅ تم تسجيل صوتك (يمكنك تغييره)")
    await refresh_entity(context.bot, "poll", pid)


async def auction_callback(update, context):
    q = update.callback_query
    user = await cb_guard(update)
    if not user:
        return
    parts = q.data.split(":")
    aid = int(parts[1])
    if parts[0] == "auc_refresh":
        await q.answer("🔄")
        await refresh_entity(context.bot, "auction", aid); return
    try:
        r = await db.place_bid(aid, q.from_user.id, int(parts[2]))
    except DBError as e:
        await q.answer(str(e), show_alert=True)
        await refresh_entity(context.bot, "auction", aid); return
    await q.answer(f"✅ مزايدتك {parts[2]} 💎 مسجلة", show_alert=False)
    await refresh_entity(context.bot, "auction", aid)
    if r["prev_bidder"] and r["prev_bidder"] != q.from_user.id:
        a = await db.get_auction(aid)
        spawn(context, notify_users(context.bot, [r["prev_bidder"]],
                                    f"⚠️ <b>تم تجاوز مزايدتك</b> في المزاد #{esc(a['token'])}\nالمزايدة الحالية: <b>{a['high_bid']}</b> 💎"))


# ═══════════════════════════════════════════════════════════════════
# ⏱️ المجدول (نشر مجدول + إغلاق التصويت والمزاد)
# ═══════════════════════════════════════════════════════════════════
async def close_poll_job(app, p):
    opts = json.loads(p["options"])
    counts = await db.poll_counts(p["id"], len(opts))
    if sum(counts) == 0:
        await db.close_poll(p["id"], None, None)
    else:
        best = max(counts)
        widx = random.choice([i for i, c in enumerate(counts) if c == best])
        o = opts[widx]
        did = await db.create_drop("normal", p["created_by"], plan=o["plan"], days=o["days"], max_winners=p["winners"],
                                   extra={"poll": p["id"]})
        await db.close_poll(p["id"], widx, did)
        await publish_entity(app, "drop", did)
    await refresh_entity(app.bot, "poll", p["id"])


async def close_auction_job(app, a):
    bot = app.bot
    winner, price, code = None, None, None
    for cand in await db.auction_candidates(a["id"]):
        if await db.deduct_points(cand["user_id"], cand["amt"], "auction_win", a["id"]):
            winner, price = cand["user_id"], cand["amt"]
            break
    if winner is None:
        await db.close_auction(a["id"], "no_winner")
        await refresh_entity(bot, "auction", a["id"]); return
    code, res = await create_sub_code(a["plan"], a["days"])
    if not code:
        await db.add_points(winner, price, "auction_refund", a["id"], count_total=False)
        await db.close_auction(a["id"], "no_winner")
        await refresh_entity(bot, "auction", a["id"])
        await raise_alert(bot, "critical", "auction_fail", f"فشل إنشاء كود مزاد #{a['token']}: {res.get('message')} — أُعيدت نقاط الفائز.")
        await notify_users(bot, [winner], "⚠️ تعذر إنشاء جائزة المزاد تقنياً وأُعيدت نقاطك. نعتذر!")
        return
    await db.close_auction(a["id"], "closed", winner, price, code)
    await db.save_subscription(winner, {"code": code, "plan": a["plan"], "days": a["days"], "max_uses": 1,
                                        "points_spent": price, "source": "auction", "response": {}})
    pi = plan_info(a["plan"])
    await refresh_entity(bot, "auction", a["id"])
    await notify_users(bot, [winner], f"🏆 <b>فزت بالمزاد!</b>\n\n{pi['emoji']} {esc(pi['name'])} — {a['days']} يوم\n"
                                      f"💎 خُصم: {price}\n🔑 <code>{esc(code)}</code>\n\n📖 {esc(SITE_URL)}")
    await notify_admins(bot, f"🔨 انتهى المزاد #{esc(a['token'])}\nالفائز <code>{winner}</code> — {price} 💎")


async def scheduler_tick(context):
    if tick_lock.locked():
        return
    async with tick_lock:
        app = context.application
        try:
            for d in await db.due_unpublished("drops"):
                await publish_entity(app, "drop", d["id"])
            for p in await db.due_unpublished("polls"):
                await publish_entity(app, "poll", p["id"])
            for a in await db.due_unpublished("auctions"):
                await publish_entity(app, "auction", a["id"])
            for p in await db.due_polls():
                await close_poll_job(app, p)
            for a in await db.due_auctions():
                await close_auction_job(app, a)
        except Exception:
            logger.exception("[TICK] خطأ")


async def periodic_refresh(context):
    """تحديث الأوقات النسبية للبطاقات المفتوحة"""
    try:
        for r in await db.drops_for_refresh():
            await refresh_entity(context.bot, "drop", r["id"])
            await asyncio.sleep(0.6)
        for a in await db.q_all("SELECT id FROM auctions WHERE status='open'"):
            await refresh_entity(context.bot, "auction", a["id"])
            await asyncio.sleep(0.6)
        for p in await db.q_all("SELECT id FROM polls WHERE status='open'"):
            await refresh_entity(context.bot, "poll", p["id"])
            await asyncio.sleep(0.6)
    except Exception:
        logger.exception("[REFRESH] خطأ")


# ═══════════════════════════════════════════════════════════════════
# 🧙 معالجات الإنشاء (المشرف)
# ═══════════════════════════════════════════════════════════════════
async def show_drops_menu(update, context):
    reset_flow(context, "drops")
    await say(update, "🎁 <b>إدارة الدروبات</b>\n\nاختر النوع الذي تريد إنشاءه:", kb_drops_menu())


def _sub_kb(d): return choice_kb([[BTN_PLUS, BTN_PRO, BTN_PREMIUM]])
def _days_kb(d): return choice_kb(days_rows(d["plan"]))


def parse_prize_type(text, data):
    m = {"🎟 اشتراك": "sub", "💎 نقاط": "points"}
    if text not in m:
        raise ValueError("اختر من الأزرار")
    return m[text]


def parse_pool(text, data):
    for k, v in POOL_LABELS.items():
        if text == v:
            return k
    raise ValueError("اختر من الأزرار")


def parse_cond_type(text, data):
    for k, v in COND_LABELS.items():
        if text == v:
            return k
    raise ValueError("اختر من الأزرار")


async def parse_cond_value(text, data):
    t, v = data["cond_type"], text.strip().lstrip("@")
    if t == "channel":
        if not re.fullmatch(r"[A-Za-z0-9_]{4,32}", v):
            raise ValueError("اسم قناة غير صالح")
        try:
            await RUNTIME["app"].bot.get_chat(f"@{v}")
        except Exception:
            raise ValueError("لا أستطيع الوصول لهذه القناة (تأكد من الاسم وأن البوت مشرف فيها)")
        return v
    if not v.isdigit() or not (1 <= int(v) <= 100000):
        raise ValueError("أرسل رقماً صحيحاً")
    return int(v)


def parse_winners_for(max_n):
    return parse_int(1, max_n, "عدد الفائزين")


def _stages_max():
    return max(len(s["prices"]) for s in SUBSCRIPTION_TYPES.values())


DROP_STEPS = [
    Step("prize_type", "🎯 <b>نوع الجائزة؟</b>", parse_prize_type,
         kb=lambda d: choice_kb([["🎟 اشتراك", "💎 نقاط"]]), when=lambda d: d["kind"] == "cond"),
    Step("plan", "📦 اختر الخطة:", parse_plan, kb=_sub_kb, when=lambda d: d["prize_type"] == "sub"),
    Step("days", "📅 اختر المدة:", parse_days_for("plan"), kb=_days_kb,
         when=lambda d: d["prize_type"] == "sub" and d["kind"] != "chain"),
    Step("points", "💎 أرسل عدد النقاط للفائز:", parse_int(1, 100000, "النقاط"), when=lambda d: d["prize_type"] == "points"),
    Step("pool", "🥚 اختر مجموعة الجوائز العشوائية:\n\n"
                 "• <b>خفيفة</b>: نقاط غالباً\n• <b>متوسطة</b>: نقاط + بلس/برو 7 أيام\n• <b>ذهبية</b>: تشمل برو 30 وبريميوم 7",
         parse_pool, kb=lambda d: choice_kb([list(POOL_LABELS.values())]), when=lambda d: d["prize_type"] == "egg"),
    Step("stages", f"🔗 كم مرحلة؟ (2 إلى {_stages_max()}) — مدة كل مرحلة أطول من التي قبلها",
         parse_int(2, _stages_max(), "المراحل"), when=lambda d: d["kind"] == "chain"),
    Step("winners", "👥 كم فائزاً (لكل مرحلة إن كان تسلسلياً)؟ (1–50)", parse_winners_for(50),
         when=lambda d: d["kind"] != "normal"),
    Step("cond_type", "🔒 اختر نوع الشرط:", parse_cond_type,
         kb=lambda d: choice_kb([[COND_LABELS["invites"], COND_LABELS["age"]], [COND_LABELS["points"], COND_LABELS["channel"]],
                                 [COND_LABELS["has_sub"]]]), when=lambda d: d["kind"] == "cond"),
    Step("cond_value", lambda d: {"invites": "👥 كم دعوة مطلوبة؟", "age": "📅 كم يوماً عمر الحساب؟",
                                  "points": "💎 كم نقطة كحد أدنى؟", "channel": "📢 أرسل يوزر القناة (بدون @):"}[d["cond_type"]],
         parse_cond_value, when=lambda d: d["kind"] == "cond" and d["cond_type"] != "has_sub"),
    Step("schedule", SCHEDULE_PROMPT, parse_schedule, kb=kb_schedule),
]


def drop_summary(d):
    kind = d["kind"]
    emoji, label = KINDS[kind]
    lines = [f"📋 <b>مراجعة الدروب {emoji} {label}</b>", ""]
    if d["prize_type"] == "sub":
        pi = plan_info(d["plan"])
        if kind == "chain":
            ds = sorted(SUBSCRIPTION_TYPES[d["plan"]]["prices"])[:d["stages"]]
            lines.append(f"🎁 {pi['emoji']} {esc(pi['name'])} — المراحل: " + " ← ".join(f"{x}ي" for x in ds))
        else:
            lines.append(f"🎁 {pi['emoji']} {esc(pi['name'])} — {d['days']} يوم")
    elif d["prize_type"] == "points":
        lines.append(f"🎁 💎 {d['points']} نقطة")
    else:
        lines.append(f"🎁 🥚 مفاجأة — {POOL_LABELS[d['pool']]}")
    lines.append(f"👥 الفائزون: {d.get('winners', 1)}")
    if kind == "cond":
        lines.append(f"🔒 الشرط: {COND_LABELS[d['cond_type']]}" + (f" = {d['cond_value']}" if d.get("cond_value") else ""))
    lines.append(f"📅 النشر: {sched_text(d.get('schedule'))}")
    if d["prize_type"] in ("sub", "egg"):
        lines.append("\n⚠️ كل فائز يستهلك كوداً حقيقياً من رصيد الـ API.")
    return "\n".join(lines)


async def drop_done(update, context, d):
    admin_id = update.effective_user.id
    kind, pa = d["kind"], d.get("schedule")
    winners = d.get("winners", 1)
    ids = []
    if kind == "chain":
        parent = None
        for i, days in enumerate(sorted(SUBSCRIPTION_TYPES[d["plan"]]["prices"])[:d["stages"]], 1):
            parent = await db.create_drop("chain", admin_id, plan=d["plan"], days=days, max_winners=winners,
                                          extra={"stages": d["stages"]}, publish_at=pa, parent_id=parent, stage=i)
            ids.append(parent)
    else:
        ids.append(await db.create_drop(
            kind, admin_id, plan=d.get("plan", ""), days=d.get("days", 0), points=d.get("points", 0),
            max_winners=winners, cond_type=d.get("cond_type"), cond_value=d.get("cond_value"),
            extra={"pool": d["pool"]} if d["prize_type"] == "egg" else None, publish_at=pa))
    status = "⏰ سيُنشر في الموعد المحدد"
    if not pa:
        errs_all = []
        for i in ids:
            ok, errs = await publish_entity(context.application, "drop", i, notify=(i == ids[0]))
            errs_all += errs
        status = "✅ نُشر في القنوات" if ok else "⚠️ لم يُنشر في أي قناة (راجع إدارة القنوات)"
    await db.log_admin(admin_id, f"create_drop_{kind}", f"ids={ids} pa={pa}")
    link_lines = []
    for i in ids:
        link_lines.append(f"🔗 <code>{bot_link(context.bot.username, (await db.get_drop_by_id(i))['token'])}</code>")
    links = "\n".join(link_lines)
    await say(update, f"✅ <b>تم إنشاء الدروب</b> ({len(ids)})\n\n{links}\n\n{status}", kb_drops_menu())
    context.user_data["state"] = "drops"


register_wizard("drop", DROP_STEPS, drop_done, show_drops_menu, summary=drop_summary)


async def start_drop_wizard(update, context, kind):
    pre = {"normal": "sub", "chain": "sub", "egg": "egg", "points": "points"}.get(kind)
    data = {"kind": kind}
    if pre:
        data["prize_type"] = pre
    await wiz_begin(update, context, "drop", data)


# ─── تصويت ───
def parse_vote_options(text, data):
    opts = []
    for line in text.strip().splitlines():
        parts = line.split()
        if len(parts) != 2 or parts[0].lower() not in PLAN_SYNONYMS or not parts[1].isdigit():
            raise ValueError("كل سطر: <خطة> <أيام> مثل: pro 30")
        plan, days = PLAN_SYNONYMS[parts[0].lower()], int(parts[1])
        if days not in SUBSCRIPTION_TYPES[plan]["prices"]:
            raise ValueError(f"المدة {days} غير موجودة لخطة {plan}")
        if {"plan": plan, "days": days} in opts:
            raise ValueError("خيار مكرر")
        opts.append({"plan": plan, "days": days})
    if not (2 <= len(opts) <= 4):
        raise ValueError("أرسل من 2 إلى 4 خيارات (كل خيار في سطر)")
    return opts


VOTE_STEPS = [
    Step("options", "🗳️ <b>أرسل الخيارات</b> (2–4، كل خيار في سطر):\n\n<code>plus 7\npro 30\npremium 7</code>", parse_vote_options),
    Step("duration", "⏳ مدة التصويت بالدقائق (5 – 4320):", parse_int(5, 4320, "المدة"),
         kb=lambda d: choice_kb([["30", "60", "180"], ["720", "1440"]])),
    Step("winners", "👥 عدد فائزي الدروب الناتج (1–50):", parse_winners_for(50)),
    Step("schedule", SCHEDULE_PROMPT, parse_schedule, kb=kb_schedule),
]


def vote_summary(d):
    o = "\n".join(f"  • {plan_info(x['plan'])['emoji']} {esc(plan_info(x['plan'])['name'])} — {x['days']}ي" for x in d["options"])
    return (f"📋 <b>مراجعة التصويت</b>\n\n{o}\n\n⏳ المدة: {d['duration']} دقيقة\n👥 الفائزون: {d['winners']}\n"
            f"📅 النشر: {sched_text(d.get('schedule'))}")


async def vote_done(update, context, d):
    pid = await db.create_poll(d["options"], d["duration"], d["winners"], update.effective_user.id, d.get("schedule"))
    await db.log_admin(update.effective_user.id, "create_poll", f"#{pid}")
    status = "⏰ سيُنشر في الموعد المحدد"
    if not d.get("schedule"):
        ok, _ = await publish_entity(context.application, "poll", pid)
        status = "✅ نُشر في القنوات" if ok else "⚠️ لم يُنشر في أي قناة"
    await say(update, f"✅ <b>تم إنشاء التصويت</b> #{pid}\n{status}", kb_drops_menu())
    context.user_data["state"] = "drops"


register_wizard("vote", VOTE_STEPS, vote_done, show_drops_menu, summary=vote_summary)


# ─── مزاد ───
AUCTION_STEPS = [
    Step("plan", "📦 اختر خطة جائزة المزاد:", parse_plan, kb=_sub_kb),
    Step("days", "📅 اختر المدة:", parse_days_for("plan"), kb=_days_kb),
    Step("start_bid", "💰 السعر الابتدائي بالنقاط (1–10000):", parse_int(1, 10000, "السعر")),
    Step("step", "📈 أقل زيادة بين مزايدتين:", parse_int(1, 1000, "الزيادة"), kb=lambda d: choice_kb([["1", "2", "5"]])),
    Step("duration", "⏳ مدة المزاد بالدقائق (5–4320):", parse_int(5, 4320, "المدة"),
         kb=lambda d: choice_kb([["30", "60", "180"], ["720", "1440"]])),
    Step("schedule", SCHEDULE_PROMPT, parse_schedule, kb=kb_schedule),
]


def auction_summary(d):
    pi = plan_info(d["plan"])
    return (f"📋 <b>مراجعة المزاد</b>\n\n🎁 {pi['emoji']} {esc(pi['name'])} — {d['days']}ي\n💰 يبدأ من {d['start_bid']} 💎 "
            f"(زيادة {d['step']})\n⏳ {d['duration']} دقيقة\n📅 النشر: {sched_text(d.get('schedule'))}")


async def auction_done(update, context, d):
    aid = await db.create_auction(d["plan"], d["days"], d["start_bid"], d["step"], d["duration"],
                                  update.effective_user.id, d.get("schedule"))
    await db.log_admin(update.effective_user.id, "create_auction", f"#{aid}")
    status = "⏰ سيُنشر في الموعد المحدد"
    if not d.get("schedule"):
        ok, _ = await publish_entity(context.application, "auction", aid)
        status = "✅ نُشر في القنوات" if ok else "⚠️ لم يُنشر في أي قناة"
    await say(update, f"✅ <b>تم إنشاء المزاد</b> #{aid}\n{status}", kb_drops_menu())
    context.user_data["state"] = "drops"


register_wizard("auction", AUCTION_STEPS, auction_done, show_drops_menu, summary=auction_summary)


# ═══════════════════════════════════════════════════════════════════
# 📋 قوائم المشرف
# ═══════════════════════════════════════════════════════════════════
def drop_status_icon(d):
    if d["is_deleted"] and d["claims_count"] == 0: return "⚫"
    if not d["published"]: return "⏳"
    if d["claims_count"] >= d["max_winners"]: return "🔴"
    return "🟢"


def _pager(prefix, page, total, per=PAGE_SIZE):
    pages = max(1, -(-total // per))
    row = []
    if page > 0:
        row.append(InlineKeyboardButton("◀️", callback_data=f"{prefix}:{page - 1}"))
    row.append(InlineKeyboardButton(f"{page + 1}/{pages}", callback_data="noop"))
    if page + 1 < pages:
        row.append(InlineKeyboardButton("▶️", callback_data=f"{prefix}:{page + 1}"))
    return row


async def drops_list_view(scope="all", page=0):
    total = await db.count_drops(scope)
    drops = await db.list_drops(scope, PAGE_SIZE, page * PAGE_SIZE)
    title = "⏰ <b>المجدولة</b>" if scope == "scheduled" else "📋 <b>كل الدروبات</b>"
    lines, rows = [f"{title} ({total})\n"], []
    for d in drops:
        emoji, label = KINDS.get(d["kind"], ("🎁", ""))
        lines.append(f"{drop_status_icon(d)} #{esc(d['token'])} {emoji}{label} — {prize_text(d)} — 👥{d['claims_count']}/{d['max_winners']}"
                     + (f"\n    ⏰ {fmt_dt(d['publish_at'])}" if not d["published"] else ""))
        if not d["is_deleted"] and d["claims_count"] == 0:
            rows.append([InlineKeyboardButton(f"🗑 إلغاء #{d['token']}", callback_data=f"adm_drop_cancel:{d['id']}:{scope}:{page}")])
    if not drops:
        lines.append("لا شيء هنا.")
    # مزادات وتصويتات
    extra_items = []
    if scope == "scheduled":
        pa = await db.q_all("SELECT * FROM auctions WHERE published=0 AND is_deleted=0 AND publish_at IS NOT NULL ORDER BY id DESC LIMIT 5")
        pp = await db.q_all("SELECT * FROM polls WHERE published=0 AND is_deleted=0 AND publish_at IS NOT NULL ORDER BY id DESC LIMIT 5")
    else:
        pa, pp = await db.list_auctions(5), await db.list_polls(5)
    for a in pa:
        extra_items.append(f"🔨 مزاد #{esc(a['token'])} — {plan_info(a['plan'])['emoji']} {a['days']}ي — {a['status']}"
                           + (f" ⏰{fmt_dt(a['publish_at'])}" if not a["published"] else f" — 🏆{a['high_bid']}"))
        if a["status"] in ("pending", "open") and a["bids_count"] == 0:
            rows.append([InlineKeyboardButton(f"🗑 إلغاء مزاد #{a['token']}", callback_data=f"adm_auc_cancel:{a['id']}:{scope}:{page}")])
    for p in pp:
        extra_items.append(f"🗳️ تصويت #{esc(p['token'])} — {p['status']}" + (f" ⏰{fmt_dt(p['publish_at'])}" if not p["published"] else ""))
        if p["status"] in ("pending", "open"):
            rows.append([InlineKeyboardButton(f"🗑 إلغاء تصويت #{p['token']}", callback_data=f"adm_poll_cancel:{p['id']}:{scope}:{page}")])
    if extra_items:
        lines += ["", "—————", *extra_items]
    rows.append(_pager(f"adm_dpg:{scope}", page, total))
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def show_drops_list(update, context, scope="all"):
    text, kb = await drops_list_view(scope, 0)
    await say(update, text, kb)


async def show_drops_scheduled(update, context):
    await show_drops_list(update, context, "scheduled")


async def show_drops_all(update, context):
    await show_drops_list(update, context, "all")


async def show_drop_stats(update, context):
    rows = await db.drop_stats()
    lines = ["📊 <b>إحصائيات الدروبات</b>\n"]
    for r in rows:
        emoji, label = KINDS.get(r["kind"], ("🎁", r["kind"]))
        fill = r["claims"] * 100 // r["seats"] if r["seats"] else 0
        lines.append(f"{emoji} <b>{label}</b>: {r['n']} دروب — مستلم {r['claims']}/{r['seats']} ({fill}%) — ملغي {r['cancelled']}")
    if not rows:
        lines.append("لا دروبات بعد.")
    pc = await db.q_one("SELECT COUNT(*) n, COALESCE(SUM(winners),0) w FROM polls")
    votes = await db.q_scalar("SELECT COUNT(*) FROM poll_votes")
    ac = await db.q_one("SELECT COUNT(*) n, COALESCE(SUM(final_price),0) rev, COALESCE(SUM(bids_count),0) bids FROM auctions")
    lines += ["", f"🗳️ التصويتات: {pc['n']} — أصوات: {votes}",
              f"🔨 المزادات: {ac['n']} — مزايدات: {ac['bids']} — إيراد: {ac['rev']} 💎"]
    await say(update, "\n".join(lines), kb_drops_menu())


async def admin_drop_callback(update, context):
    q = update.callback_query
    if not is_admin(q.from_user.id):
        await q.answer("🚫", show_alert=True); return
    parts = q.data.split(":")
    act = parts[0]

    async def rerender(scope, page):
        text, kb = await drops_list_view(scope, int(page))
        try:
            await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
        except Exception as e:
            if "not modified" not in str(e).lower():
                raise

    if act == "adm_dpg":
        await q.answer()
        await rerender(parts[1], parts[2]); return
    oid, scope, page = int(parts[1]), parts[2], parts[3]
    if act == "adm_drop_cancel":
        ok = await db.cancel_drop(oid)
        if ok:
            await db.log_admin(q.from_user.id, "cancel_drop", f"#{oid}")
            await refresh_entity(context.bot, "drop", oid)
        await q.answer("🗑 أُلغي" if ok else "لا يمكن الإلغاء (يوجد فائزون)", show_alert=not ok)
    elif act == "adm_auc_cancel":
        ok = await db.cancel_auction(oid)
        if ok:
            await db.log_admin(q.from_user.id, "cancel_auction", f"#{oid}")
            await refresh_entity(context.bot, "auction", oid)
        await q.answer("🗑 أُلغي" if ok else "لا يمكن (توجد مزايدات)", show_alert=not ok)
    elif act == "adm_poll_cancel":
        ok = await db.cancel_poll(oid)
        if ok:
            await db.log_admin(q.from_user.id, "cancel_poll", f"#{oid}")
            await refresh_entity(context.bot, "poll", oid)
        await q.answer("🗑 أُلغي" if ok else "لا يمكن الإلغاء", show_alert=not ok)
    await rerender(scope, page)


async def analytics_drops(update, context):
    s = await db.drop_stats()
    claims_total = sum(r["claims"] for r in s)
    days = await db.daily_counts("drop_claims", "created_at", 14)
    lines = ["🎁 <b>تحليل الدروبات</b>\n", f"إجمالي الاستلامات: <b>{claims_total}</b>",
             f"استلامات آخر 14 يوماً: <code>{spark([n for _, n in days])}</code> ({sum(n for _, n in days)})", ""]
    prizes = await db.q_all("SELECT prize_type, COUNT(*) n FROM drop_claims WHERE prize_type IS NOT NULL GROUP BY prize_type")
    for p in prizes:
        lines.append(f"• {'🎟 اشتراكات' if p['prize_type'] == 'sub' else '💎 نقاط'}: {p['n']}")
    pts = await db.q_scalar("SELECT COALESCE(SUM(CAST(prize_value AS INTEGER)),0) FROM drop_claims WHERE prize_type='points'")
    lines.append(f"💎 نقاط وُزّعت عبر الدروبات: <b>{pts}</b>")
    top = await db.q_all("SELECT user_id, COUNT(*) n FROM drop_claims GROUP BY user_id ORDER BY n DESC LIMIT 5")
    if top:
        lines += ["", "🏅 أكثر الفائزين:"] + [f"  {i}. <code>{mask_id(t['user_id'])}</code> — {t['n']}" for i, t in enumerate(top, 1)]
    await say(update, "\n".join(lines), kb_analytics())


# ═══════════════════════════════════════════════════════════════════
# 👤 جهة المستخدم: 🎫 دروبات + إدخال كود
# ═══════════════════════════════════════════════════════════════════
async def user_drops_view(update, context):
    uid = update.effective_user.id
    user = await db.get_user(uid)
    drops = await db.list_drops("active", 8, 0)
    bot = context.bot
    lines = ["🎫 <b>الدروبات المتاحة الآن</b>\n"]
    rows = []
    for d in drops:
        if d["claims_count"] >= d["max_winners"]:
            continue
        emoji, label = KINDS.get(d["kind"], ("🎁", ""))
        ok, _ = await check_condition(bot, user, d) if d.get("cond_type") != "channel" else (True, "")
        if ok and d.get("parent_id"):
            ok = await db.user_has_claim(d["parent_id"], uid)
        done = await db.user_has_claim(d["id"], uid)
        mark = "✅ استلمته" if done else ("🟢 مؤهل" if ok else "🔒 شرط")
        lines.append(f"{emoji} {label} — {prize_text(d)} — 👥{d['claims_count']}/{d['max_winners']} — {mark}")
        if not done:
            rows.append([InlineKeyboardButton(f"{emoji} {label} #{d['token'][:6]}", url=bot_link(bot.username, d["token"]))])
    if len(lines) == 1:
        lines.append("لا توجد دروبات متاحة حالياً.")
    live = []
    for a in await db.q_all("SELECT id, plan, days, high_bid FROM auctions WHERE status='open' LIMIT 3"):
        urls = await _post_urls("auction", a["id"])
        live.append(f"🔨 مزاد {plan_info(a['plan'])['emoji']} {a['days']}ي — الأعلى {a['high_bid']} 💎")
        if urls:
            rows.append([InlineKeyboardButton("🔨 اذهب للمزاد", url=urls[0])])
    for p in await db.q_all("SELECT id FROM polls WHERE status='open' LIMIT 3"):
        urls = await _post_urls("poll", p["id"])
        live.append("🗳️ تصويت مفتوح الآن")
        if urls:
            rows.append([InlineKeyboardButton("🗳️ اذهب للتصويت", url=urls[0])])
    if live:
        lines += ["", *live]
    await say(update, "\n".join(lines), kb_earn())
    if rows:
        await update.effective_chat.send_message("👇", reply_markup=InlineKeyboardMarkup(rows[:10]))


async def redeem_begin(update, context):
    reset_flow(context, "redeem")
    await say(update, "🎟 أرسل <b>كود الدروب</b> أو رابطه:\n\n❌ للإلغاء", kb_cancel())


async def redeem_receive(update, context, text):
    t = text.strip()
    m = re.search(r"start=(?:drop|pdrop)_([A-Za-z0-9]+)", t)
    token = m.group(1) if m else re.sub(r"^(drop|pdrop)_", "", t)
    if not re.fullmatch(r"[A-Za-z0-9]{6,32}", token):
        await say(update, "❌ كود غير صالح، أعد المحاولة:", kb_cancel()); return
    reset_flow(context, "earn")
    if t.startswith("pdrop_") or "pdrop_" in t:
        await handle_points_drop_entry(update, context, token)
    else:
        await handle_drop_entry(update, context, token)
