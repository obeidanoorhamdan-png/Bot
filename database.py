"""database.py — طبقة قاعدة البيانات (SQLite + aiosqlite، اتصال واحد، معاملات ذرّية)"""
import asyncio
import json
import os
import secrets
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

import aiosqlite

from config import *  # noqa


class DBError(Exception):
    pass


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT,
    points INTEGER DEFAULT 0, total_points_earned INTEGER DEFAULT 0,
    invited_by INTEGER, invite_code TEXT UNIQUE,
    last_daily_bonus TEXT, is_banned INTEGER DEFAULT 0, joined_at TEXT
);
CREATE TABLE IF NOT EXISTS api_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT, key_value TEXT NOT NULL,
    label TEXT, is_active INTEGER DEFAULT 0, created_at TEXT,
    created_by INTEGER, last_used TEXT,
    success_count INTEGER DEFAULT 0, fail_count INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
    code TEXT, plan TEXT, actual_plan TEXT, days INTEGER, max_uses INTEGER,
    uses INTEGER DEFAULT 0, status TEXT DEFAULT 'active',
    created_at TEXT, expires_at TEXT,
    points_spent INTEGER DEFAULT 0, api_response TEXT
);
CREATE TABLE IF NOT EXISTS drops (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token TEXT UNIQUE NOT NULL, plan TEXT NOT NULL, days INTEGER NOT NULL,
    created_by INTEGER, created_at TEXT,
    claimed_by INTEGER, claimed_at TEXT, claim_code TEXT,
    chat_id INTEGER, message_id INTEGER, last_refreshed TEXT,
    retry_count INTEGER DEFAULT 0, is_active INTEGER DEFAULT 1, is_deleted INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS points_drops (
    id INTEGER PRIMARY KEY AUTOINCREMENT, token TEXT UNIQUE NOT NULL, points INTEGER NOT NULL,
    created_by INTEGER, created_at TEXT, claimed_by INTEGER, claimed_at TEXT, is_active INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS force_join_cache (user_id INTEGER PRIMARY KEY, checked_at TEXT, is_member INTEGER);
CREATE TABLE IF NOT EXISTS admin_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, admin_id INTEGER, action TEXT, details TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS points_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, amount INTEGER, reason TEXT, ref_id INTEGER, created_at TEXT
);
CREATE TABLE IF NOT EXISTS drop_claims (
    id INTEGER PRIMARY KEY AUTOINCREMENT, drop_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
    prize_type TEXT, prize_value TEXT, code TEXT, created_at TEXT,
    UNIQUE(drop_id, user_id)
);
CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, entity TEXT NOT NULL, entity_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL, message_id INTEGER NOT NULL, retry_count INTEGER DEFAULT 0,
    UNIQUE(entity, entity_id, chat_id)
);
CREATE TABLE IF NOT EXISTS channels (
    id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, chat_id INTEGER, title TEXT,
    purpose TEXT DEFAULT 'both', is_active INTEGER DEFAULT 1, created_at TEXT
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS polls (
    id INTEGER PRIMARY KEY AUTOINCREMENT, token TEXT UNIQUE, options TEXT NOT NULL,
    duration_min INTEGER DEFAULT 60, winners INTEGER DEFAULT 1, status TEXT DEFAULT 'pending',
    ends_at TEXT, winner_idx INTEGER, result_drop_id INTEGER, publish_at TEXT, published INTEGER DEFAULT 0,
    is_deleted INTEGER DEFAULT 0, created_by INTEGER, created_at TEXT
);
CREATE TABLE IF NOT EXISTS poll_votes (
    poll_id INTEGER, user_id INTEGER, option_idx INTEGER, created_at TEXT, PRIMARY KEY(poll_id, user_id)
);
CREATE TABLE IF NOT EXISTS auctions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, token TEXT UNIQUE, plan TEXT, days INTEGER,
    start_bid INTEGER, step INTEGER DEFAULT 1, high_bid INTEGER DEFAULT 0, high_bidder INTEGER,
    bids_count INTEGER DEFAULT 0, duration_min INTEGER DEFAULT 60, ends_at TEXT, status TEXT DEFAULT 'pending',
    winner_id INTEGER, final_price INTEGER, winner_code TEXT, publish_at TEXT, published INTEGER DEFAULT 0,
    is_deleted INTEGER DEFAULT 0, created_by INTEGER, created_at TEXT
);
CREATE TABLE IF NOT EXISTS auction_bids (
    id INTEGER PRIMARY KEY AUTOINCREMENT, auction_id INTEGER, user_id INTEGER, amount INTEGER, created_at TEXT
);
CREATE TABLE IF NOT EXISTS market_listings (
    id INTEGER PRIMARY KEY AUTOINCREMENT, seller_id INTEGER NOT NULL, sub_id INTEGER, code TEXT, plan TEXT, days INTEGER,
    expires_at TEXT, price INTEGER NOT NULL, status TEXT DEFAULT 'active', buyer_id INTEGER, commission INTEGER DEFAULT 0,
    created_at TEXT, sold_at TEXT
);
CREATE TABLE IF NOT EXISTS market_complaints (
    id INTEGER PRIMARY KEY AUTOINCREMENT, listing_id INTEGER, buyer_id INTEGER, seller_id INTEGER, reason TEXT,
    api_status TEXT, status TEXT DEFAULT 'open', admin_note TEXT, created_at TEXT, resolved_at TEXT
);
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, level TEXT, kind TEXT, message TEXT, created_at TEXT, seen INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS api_log (id INTEGER PRIMARY KEY AUTOINCREMENT, ok INTEGER, status INTEGER, ms INTEGER, created_at TEXT);
CREATE INDEX IF NOT EXISTS idx_subs_user ON subscriptions(user_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_users_points ON users(points DESC);
CREATE INDEX IF NOT EXISTS idx_plog_user ON points_log(user_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_claims_drop ON drop_claims(drop_id);
CREATE INDEX IF NOT EXISTS idx_listing_status ON market_listings(status, id DESC);
CREATE INDEX IF NOT EXISTS idx_apilog_time ON api_log(created_at);
"""

MIGRATIONS = {
    "users": [("lang", "TEXT DEFAULT 'ar'"), ("theme", "TEXT DEFAULT 'dark'"), ("notif_drops", "INTEGER DEFAULT 1"),
              ("notif_expiry", "INTEGER DEFAULT 1"), ("notif_news", "INTEGER DEFAULT 1"),
              ("streak", "INTEGER DEFAULT 0"), ("last_active", "TEXT")],
    "subscriptions": [("source", "TEXT DEFAULT 'purchase'"), ("listed", "INTEGER DEFAULT 0"),
                      ("reminded", "INTEGER DEFAULT 0")],
    "drops": [("kind", "TEXT DEFAULT 'normal'"), ("prize_points", "INTEGER DEFAULT 0"),
              ("max_winners", "INTEGER DEFAULT 1"), ("claims_count", "INTEGER DEFAULT 0"),
              ("cond_type", "TEXT"), ("cond_value", "TEXT"), ("extra", "TEXT"), ("publish_at", "TEXT"),
              ("published", "INTEGER DEFAULT 1"), ("parent_id", "INTEGER"), ("stage", "INTEGER DEFAULT 0")],
}
PREF_FIELDS = {"lang", "theme", "notif_drops", "notif_expiry", "notif_news"}
EXPORT_TABLES = {"users", "subscriptions", "points_log", "drops", "drop_claims", "market_listings",
                 "market_complaints", "auctions", "auction_bids", "polls", "admin_logs"}


class Database:
    def __init__(self, path):
        self.path = path
        self.conn = None
        self._lock = asyncio.Lock()

    async def init(self):
        d = os.path.dirname(self.path)
        if d:
            os.makedirs(d, exist_ok=True)
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.execute("PRAGMA journal_mode=WAL")
        await self.conn.execute("PRAGMA synchronous=NORMAL")
        await self.conn.execute("PRAGMA busy_timeout=5000")
        await self.conn.executescript(SCHEMA)
        for table, cols in MIGRATIONS.items():
            have = {r["name"] for r in await self.q_all(f"PRAGMA table_info({table})")}
            for col, decl in cols:
                if col not in have:
                    await self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
        await self.conn.execute("CREATE INDEX IF NOT EXISTS idx_drops_pub ON drops(published, publish_at)")
        # ترحيل بطاقات الدروب القديمة إلى جدول posts
        await self.conn.execute(
            """INSERT OR IGNORE INTO posts (entity, entity_id, chat_id, message_id)
               SELECT 'drop', id, chat_id, message_id FROM drops WHERE chat_id IS NOT NULL AND message_id IS NOT NULL""")
        await self.conn.commit()

    async def close(self):
        if self.conn:
            await self.conn.close()

    @asynccontextmanager
    async def _tx(self):
        async with self._lock:
            try:
                yield self.conn
                await self.conn.commit()
            except BaseException:
                await self.conn.rollback()
                raise

    # ── استعلامات عامة ──
    async def q_one(self, sql, params=()):
        async with self.conn.execute(sql, params) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None

    async def q_all(self, sql, params=()):
        async with self.conn.execute(sql, params) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def q_scalar(self, sql, params=()):
        async with self.conn.execute(sql, params) as cur:
            row = await cur.fetchone()
        return (row[0] if row and row[0] is not None else 0)

    async def execute(self, sql, params=()):
        async with self._tx() as c:
            cur = await c.execute(sql, params)
            return cur.rowcount

    # ═════════ المستخدمون ═════════
    async def get_user(self, user_id):
        return await self.q_one("SELECT * FROM users WHERE user_id=?", (user_id,))

    async def register_user(self, user_id, username, first_name, invited_by=None):
        existing = await self.get_user(user_id)
        if existing:
            return existing, False, None
        if invited_by:
            inv = await self.get_user(invited_by)
            if not inv or inv["is_banned"] or invited_by == user_id:
                invited_by = None
        created = False
        for _ in range(5):
            code = secrets.token_hex(4).upper()
            async with self._tx() as c:
                cur = await c.execute(
                    """INSERT OR IGNORE INTO users (user_id, username, first_name, invited_by, invite_code, joined_at, last_active)
                       VALUES (?,?,?,?,?,?,?)""",
                    (user_id, username, first_name, invited_by, code, now_iso(), now_iso()))
                created = cur.rowcount > 0
            if await self.get_user(user_id):
                break
        user = await self.get_user(user_id)
        if created and invited_by:
            await self.add_points(invited_by, POINTS_INVITE, "invite_reward", user_id)
        return user, created, (invited_by if created else None)

    async def refresh_profile(self, user_id, username, first_name):
        await self.execute("UPDATE users SET username=?, first_name=? WHERE user_id=?", (username, first_name, user_id))

    async def touch_active(self, user_id):
        await self.execute("UPDATE users SET last_active=? WHERE user_id=?", (now_iso(), user_id))

    async def set_pref(self, user_id, field, value):
        if field not in PREF_FIELDS:
            raise ValueError(field)
        await self.execute(f"UPDATE users SET {field}=? WHERE user_id=?", (value, user_id))

    async def add_points(self, user_id, amount, reason, ref_id=None, count_total=True):
        earned = amount if (count_total and amount > 0) else 0
        async with self._tx() as c:
            await c.execute("""UPDATE users SET points = MAX(0, points + ?),
                               total_points_earned = total_points_earned + ? WHERE user_id=?""",
                            (amount, earned, user_id))
            await c.execute("INSERT INTO points_log (user_id, amount, reason, ref_id, created_at) VALUES (?,?,?,?,?)",
                            (user_id, amount, reason, ref_id, now_iso()))

    async def deduct_points(self, user_id, amount, reason="purchase", ref_id=None):
        async with self._tx() as c:
            cur = await c.execute("UPDATE users SET points = points - ? WHERE user_id=? AND points >= ?",
                                  (amount, user_id, amount))
            if cur.rowcount == 0:
                return False
            await c.execute("INSERT INTO points_log (user_id, amount, reason, ref_id, created_at) VALUES (?,?,?,?,?)",
                            (user_id, -amount, reason, ref_id, now_iso()))
            return True

    async def claim_daily_bonus(self, user_id):
        """يعيد dict: ok, bonus, streak, remaining(secs)"""
        async with self._tx() as c:
            cur = await c.execute("SELECT last_daily_bonus, streak FROM users WHERE user_id=?", (user_id,))
            row = await cur.fetchone()
            if not row:
                return {"ok": False, "remaining": 0}
            last = parse_dt(row["last_daily_bonus"]) if row["last_daily_bonus"] else None
            now = datetime.now()
            if last and (now - last) < timedelta(hours=24):
                return {"ok": False, "remaining": int((timedelta(hours=24) - (now - last)).total_seconds())}
            streak = (row["streak"] or 0) + 1 if (last and (now - last) < timedelta(hours=48)) else 1
            bonus = POINTS_DAILY * (2 if streak % 7 == 0 else 1)
            await c.execute("""UPDATE users SET last_daily_bonus=?, streak=?, points=points+?,
                               total_points_earned=total_points_earned+? WHERE user_id=?""",
                            (now_iso(), streak, bonus, bonus, user_id))
            await c.execute("INSERT INTO points_log (user_id, amount, reason, created_at) VALUES (?,?,?,?)",
                            (user_id, bonus, "daily_bonus", now_iso()))
            return {"ok": True, "bonus": bonus, "streak": streak}

    async def transfer_points(self, from_user, to_user, amount):
        if from_user == to_user:
            return False, "لا يمكن التحويل لنفسك"
        try:
            async with self._tx() as c:
                cur = await c.execute("SELECT is_banned FROM users WHERE user_id=?", (to_user,))
                row = await cur.fetchone()
                if not row or row[0]:
                    raise DBError("المستخدم المستلم غير متاح")
                cur = await c.execute("UPDATE users SET points = points - ? WHERE user_id=? AND points >= ?",
                                      (amount, from_user, amount))
                if cur.rowcount == 0:
                    raise DBError("نقاطك غير كافية")
                await c.execute("UPDATE users SET points = points + ? WHERE user_id=?", (amount, to_user))
                t = now_iso()
                await c.execute("INSERT INTO points_log (user_id, amount, reason, ref_id, created_at) VALUES (?,?,?,?,?)",
                                (from_user, -amount, "transfer_out", to_user, t))
                await c.execute("INSERT INTO points_log (user_id, amount, reason, ref_id, created_at) VALUES (?,?,?,?,?)",
                                (to_user, amount, "transfer_in", from_user, t))
            return True, "تم التحويل"
        except DBError as e:
            return False, str(e)

    async def set_ban(self, user_id, banned):
        return await self.execute("UPDATE users SET is_banned=? WHERE user_id=?", (1 if banned else 0, user_id))

    async def count_users(self):
        return await self.q_scalar("SELECT COUNT(*) FROM users")

    async def user_ids(self, audience="all"):
        sql = "SELECT user_id FROM users WHERE is_banned=0"
        if audience == "news":
            sql += " AND notif_news=1"
        elif audience == "active7":
            sql += f" AND last_active >= '{(datetime.now() - timedelta(days=7)).isoformat(timespec='seconds')}'"
        return [r["user_id"] for r in await self.q_all(sql)]

    async def users_for_notify(self, field, limit=NOTIFY_MAX):
        if field not in PREF_FIELDS:
            return []
        rows = await self.q_all(f"SELECT user_id FROM users WHERE is_banned=0 AND {field}=1 ORDER BY last_active DESC LIMIT ?", (limit,))
        return [r["user_id"] for r in rows]

    async def top_users(self, limit=10):
        return await self.q_all("SELECT user_id, username, first_name, points FROM users ORDER BY points DESC, user_id ASC LIMIT ?", (limit,))

    async def get_user_rank(self, user_id):
        u = await self.get_user(user_id)
        if not u:
            return 0, 0
        rank = await self.q_scalar("SELECT COUNT(*) FROM users WHERE points > ?", (u["points"],)) + 1
        return rank, await self.count_users()

    async def count_invited(self, user_id):
        return await self.q_scalar("SELECT COUNT(*) FROM users WHERE invited_by=?", (user_id,))

    # ═════════ مفاتيح API ═════════
    async def add_api_key(self, value, label, admin_id):
        async with self._tx() as c:
            cur = await c.execute("INSERT INTO api_keys (key_value, label, is_active, created_at, created_by) VALUES (?,?,0,?,?)",
                                  (value, label, now_iso(), admin_id))
            return cur.lastrowid

    async def get_active_key(self):
        return await self.q_one("SELECT * FROM api_keys WHERE is_active=1 LIMIT 1")

    async def list_api_keys(self):
        return await self.q_all("SELECT * FROM api_keys ORDER BY id DESC")

    async def get_key(self, key_id):
        return await self.q_one("SELECT * FROM api_keys WHERE id=?", (key_id,))

    async def find_key_by_value(self, value):
        return await self.q_one("SELECT * FROM api_keys WHERE key_value=?", (value,))

    async def set_active_key(self, key_id):
        async with self._tx() as c:
            await c.execute("UPDATE api_keys SET is_active=0")
            await c.execute("UPDATE api_keys SET is_active=1 WHERE id=?", (key_id,))

    async def delete_api_key(self, key_id):
        return await self.execute("DELETE FROM api_keys WHERE id=?", (key_id,))

    async def record_key_result(self, key_id, success):
        if success:
            await self.execute("UPDATE api_keys SET success_count=success_count+1, last_used=? WHERE id=?", (now_iso(), key_id))
        else:
            await self.execute("UPDATE api_keys SET fail_count=fail_count+1 WHERE id=?", (key_id,))

    async def log_api(self, ok, status, ms):
        await self.execute("INSERT INTO api_log (ok, status, ms, created_at) VALUES (?,?,?,?)", (1 if ok else 0, status, ms, now_iso()))

    async def api_health(self, minutes=60):
        since = (datetime.now() - timedelta(minutes=minutes)).isoformat(timespec="seconds")
        row = await self.q_one("SELECT COUNT(*) n, COALESCE(SUM(1-ok),0) fails, COALESCE(AVG(ms),0) avg FROM api_log WHERE created_at>=?", (since,))
        last = await self.q_all("SELECT ok, status FROM api_log ORDER BY id DESC LIMIT 10")
        return {"n": row["n"], "fails": row["fails"], "avg_ms": int(row["avg"]), "last": last}

    # ═════════ الاشتراكات ═════════
    async def save_subscription(self, user_id, data):
        expires = (datetime.now() + timedelta(days=data["days"])).isoformat(timespec="seconds")
        async with self._tx() as c:
            cur = await c.execute(
                """INSERT INTO subscriptions (user_id, code, plan, actual_plan, days, max_uses, status, created_at, expires_at,
                   points_spent, api_response, source) VALUES (?,?,?,?,?,?, 'active', ?,?,?,?,?)""",
                (user_id, data.get("code", ""), data["plan"], data.get("actual_plan", data["plan"]), data["days"],
                 data.get("max_uses", 1), now_iso(), expires, data.get("points_spent", 0),
                 json.dumps(data.get("response", {}), ensure_ascii=False), data.get("source", "purchase")))
            return cur.lastrowid

    def _sub_filter(self, mode):
        n = now_iso()
        if mode == "active":
            return " AND (expires_at IS NULL OR expires_at > ?)", [n]
        if mode == "archive":
            return " AND expires_at IS NOT NULL AND expires_at <= ?", [n]
        return "", []

    async def list_user_subs(self, user_id, mode="all", limit=5, offset=0):
        f, p = self._sub_filter(mode)
        return await self.q_all(f"SELECT * FROM subscriptions WHERE user_id=?{f} ORDER BY id DESC LIMIT ? OFFSET ?",
                                [user_id, *p, limit, offset])

    async def count_user_subs(self, user_id, mode="all"):
        f, p = self._sub_filter(mode)
        return await self.q_scalar(f"SELECT COUNT(*) FROM subscriptions WHERE user_id=?{f}", [user_id, *p])

    async def get_sub(self, sub_id):
        return await self.q_one("SELECT * FROM subscriptions WHERE id=?", (sub_id,))

    async def get_last_sub_code(self, user_id):
        return await self.q_one("SELECT code, plan, days FROM subscriptions WHERE user_id=? ORDER BY id DESC LIMIT 1", (user_id,))

    async def next_expiry(self, user_id):
        return await self.q_scalar("SELECT MIN(expires_at) FROM subscriptions WHERE user_id=? AND expires_at>?", (user_id, now_iso())) or None

    async def subs_to_remind(self, days=3):
        now = now_iso()
        until = (datetime.now() + timedelta(days=days)).isoformat(timespec="seconds")
        return await self.q_all(
            """SELECT s.id, s.user_id, s.plan, s.days, s.expires_at, s.code FROM subscriptions s
               JOIN users u ON u.user_id=s.user_id
               WHERE s.expires_at>? AND s.expires_at<=? AND s.reminded=0 AND u.notif_expiry=1 AND u.is_banned=0""",
            (now, until))

    async def mark_reminded(self, sub_id):
        await self.execute("UPDATE subscriptions SET reminded=1 WHERE id=?", (sub_id,))

    # ═════════ الدروبات (موحّدة) ═════════
    async def create_drop(self, kind, admin_id, plan="", days=0, points=0, max_winners=1, cond_type=None,
                          cond_value=None, extra=None, publish_at=None, parent_id=None, stage=0):
        token = secrets.token_hex(5)
        async with self._tx() as c:
            cur = await c.execute(
                """INSERT INTO drops (token, plan, days, created_by, created_at, is_active, kind, prize_points, max_winners,
                   cond_type, cond_value, extra, publish_at, published, parent_id, stage)
                   VALUES (?,?,?,?,?,1,?,?,?,?,?,?,?,0,?,?)""",
                (token, plan, days, admin_id, now_iso(), kind, points, max_winners, cond_type,
                 None if cond_value is None else str(cond_value), json.dumps(extra) if extra else None,
                 publish_at, parent_id, stage))
            return cur.lastrowid

    async def get_drop(self, token):
        return await self.q_one("SELECT * FROM drops WHERE token=?", (token,))

    async def get_drop_by_id(self, drop_id):
        return await self.q_one("SELECT * FROM drops WHERE id=?", (drop_id,))

    async def set_published(self, entity, entity_id):
        table = {"drop": "drops", "poll": "polls", "auction": "auctions"}[entity]
        await self.execute(f"UPDATE {table} SET published=1 WHERE id=?", (entity_id,))

    async def reserve_claim(self, drop_id, user_id):
        """حجز ذرّي لمقعد فائز. يعيد (claim_id, None) أو (None, reason)"""
        async with self._tx() as c:
            cur = await c.execute("SELECT * FROM drops WHERE id=?", (drop_id,))
            d = await cur.fetchone()
            if not d or d["is_deleted"] or not d["is_active"] or d["published"] == 0:
                return None, "inactive"
            if d["claims_count"] >= d["max_winners"]:
                return None, "full"
            try:
                cur = await c.execute("INSERT INTO drop_claims (drop_id, user_id, created_at) VALUES (?,?,?)",
                                      (drop_id, user_id, now_iso()))
            except sqlite3.IntegrityError:
                return None, "dup"
            claim_id = cur.lastrowid
            await c.execute(
                """UPDATE drops SET claims_count=claims_count+1, claimed_by=?, claimed_at=?,
                   is_active=CASE WHEN claims_count+1 >= max_winners THEN 0 ELSE 1 END WHERE id=?""",
                (user_id, now_iso(), drop_id))
            return claim_id, None

    async def finalize_claim(self, claim_id, prize_type, prize_value, code=None):
        await self.execute("UPDATE drop_claims SET prize_type=?, prize_value=?, code=? WHERE id=?",
                           (prize_type, str(prize_value), code, claim_id))

    async def release_claim(self, claim_id):
        async with self._tx() as c:
            cur = await c.execute("SELECT drop_id FROM drop_claims WHERE id=?", (claim_id,))
            row = await cur.fetchone()
            if not row:
                return
            did = row[0]
            await c.execute("DELETE FROM drop_claims WHERE id=?", (claim_id,))
            await c.execute(
                """UPDATE drops SET claims_count=MAX(claims_count-1,0), is_active=1,
                   claimed_by=(SELECT user_id FROM drop_claims WHERE drop_id=? ORDER BY id DESC LIMIT 1)
                   WHERE id=? AND is_deleted=0""", (did, did))

    async def user_has_claim(self, drop_id, user_id):
        return bool(await self.q_scalar("SELECT COUNT(*) FROM drop_claims WHERE drop_id=? AND user_id=? AND prize_type IS NOT NULL",
                                        (drop_id, user_id)))

    async def get_claims(self, drop_id, limit=20):
        return await self.q_all("SELECT * FROM drop_claims WHERE drop_id=? ORDER BY id DESC LIMIT ?", (drop_id, limit))

    def _drop_scope(self, scope):
        if scope == "scheduled":
            return "WHERE published=0 AND is_deleted=0 AND publish_at IS NOT NULL"
        if scope == "active":
            return "WHERE is_active=1 AND is_deleted=0 AND published=1"
        return ""

    async def list_drops(self, scope="all", limit=5, offset=0):
        return await self.q_all(f"SELECT * FROM drops {self._drop_scope(scope)} ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset))

    async def count_drops(self, scope="all"):
        return await self.q_scalar(f"SELECT COUNT(*) FROM drops {self._drop_scope(scope)}")

    async def cancel_drop(self, drop_id):
        return await self.execute("UPDATE drops SET is_active=0, is_deleted=1 WHERE id=? AND claims_count=0", (drop_id,))

    async def due_unpublished(self, table):
        return await self.q_all(
            f"SELECT * FROM {table} WHERE published=0 AND is_deleted=0 AND publish_at IS NOT NULL AND publish_at<=?", (now_iso(),))

    async def drops_for_refresh(self):
        return await self.q_all(
            """SELECT d.id FROM drops d WHERE d.is_deleted=0 AND d.is_active=1 AND d.published=1
               AND EXISTS (SELECT 1 FROM posts p WHERE p.entity='drop' AND p.entity_id=d.id)""")

    async def drop_stats(self):
        return await self.q_all(
            """SELECT kind, COUNT(*) n, COALESCE(SUM(claims_count),0) claims, COALESCE(SUM(max_winners),0) seats,
               COALESCE(SUM(is_deleted),0) cancelled FROM drops GROUP BY kind""")

    # ═════════ دروب النقاط القديم (توافق) ═════════
    async def get_points_drop(self, token):
        return await self.q_one("SELECT * FROM points_drops WHERE token=?", (token,))

    async def try_claim_points_drop(self, token, user_id):
        n = await self.execute("UPDATE points_drops SET claimed_by=?, claimed_at=?, is_active=0 WHERE token=? AND is_active=1 AND claimed_by IS NULL",
                               (user_id, now_iso(), token))
        return n > 0

    # ═════════ المنشورات ═════════
    async def add_post(self, entity, entity_id, chat_id, message_id):
        await self.execute("INSERT OR REPLACE INTO posts (entity, entity_id, chat_id, message_id, retry_count) VALUES (?,?,?,?,0)",
                           (entity, entity_id, chat_id, message_id))

    async def get_posts(self, entity, entity_id):
        return await self.q_all("SELECT * FROM posts WHERE entity=? AND entity_id=?", (entity, entity_id))

    async def delete_post(self, post_id):
        await self.execute("DELETE FROM posts WHERE id=?", (post_id,))

    async def post_failed(self, post_id):
        await self.execute("UPDATE posts SET retry_count=retry_count+1 WHERE id=?", (post_id,))
        await self.execute("DELETE FROM posts WHERE id=? AND retry_count>=5", (post_id,))

    # ═════════ القنوات والإعدادات ═════════
    async def add_channel(self, username, chat_id, title, purpose="both"):
        async with self._tx() as c:
            cur = await c.execute("INSERT OR IGNORE INTO channels (username, chat_id, title, purpose, is_active, created_at) VALUES (?,?,?,?,1,?)",
                                  (username, chat_id, title, purpose, now_iso()))
            return cur.rowcount > 0

    async def list_channels(self, purpose=None, active_only=False):
        sql, p = "SELECT * FROM channels WHERE 1=1", []
        if active_only:
            sql += " AND is_active=1"
        if purpose == "join":
            sql += " AND purpose IN ('join','both')"
        elif purpose == "publish":
            sql += " AND purpose IN ('publish','both')"
        return await self.q_all(sql + " ORDER BY id", p)

    async def get_channel(self, cid):
        return await self.q_one("SELECT * FROM channels WHERE id=?", (cid,))

    async def update_channel(self, cid, **fields):
        for k in fields:
            if k not in ("is_active", "purpose", "chat_id", "title"):
                raise ValueError(k)
        sets = ", ".join(f"{k}=?" for k in fields)
        await self.execute(f"UPDATE channels SET {sets} WHERE id=?", [*fields.values(), cid])
        await self.execute("DELETE FROM force_join_cache")

    async def delete_channel(self, cid):
        await self.execute("DELETE FROM channels WHERE id=?", (cid,))
        await self.execute("DELETE FROM force_join_cache")

    async def get_setting(self, key, default=None):
        row = await self.q_one("SELECT value FROM settings WHERE key=?", (key,))
        return row["value"] if row else default

    async def set_setting(self, key, value):
        await self.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, str(value)))

    # ═════════ Force join cache ═════════
    async def get_force_join_cache(self, user_id):
        return await self.q_one("SELECT * FROM force_join_cache WHERE user_id=?", (user_id,))

    async def save_force_join_cache(self, user_id, is_member):
        await self.execute("INSERT OR REPLACE INTO force_join_cache (user_id, checked_at, is_member) VALUES (?,?,?)",
                           (user_id, now_iso(), 1 if is_member else 0))

    async def delete_force_join_cache(self, user_id):
        await self.execute("DELETE FROM force_join_cache WHERE user_id=?", (user_id,))

    # ═════════ التصويت ═════════
    async def create_poll(self, options, duration_min, winners, admin_id, publish_at=None):
        token = secrets.token_hex(5)
        async with self._tx() as c:
            cur = await c.execute(
                "INSERT INTO polls (token, options, duration_min, winners, status, publish_at, published, created_by, created_at) VALUES (?,?,?,?, 'pending', ?,0,?,?)",
                (token, json.dumps(options), duration_min, winners, publish_at, admin_id, now_iso()))
            return cur.lastrowid

    async def get_poll(self, pid):
        return await self.q_one("SELECT * FROM polls WHERE id=?", (pid,))

    async def open_poll(self, pid):
        p = await self.get_poll(pid)
        ends = (datetime.now() + timedelta(minutes=p["duration_min"])).isoformat(timespec="seconds")
        await self.execute("UPDATE polls SET status='open', ends_at=?, published=1 WHERE id=?", (ends, pid))

    async def vote(self, pid, user_id, idx):
        async with self._tx() as c:
            cur = await c.execute("SELECT status, ends_at, options FROM polls WHERE id=?", (pid,))
            p = await cur.fetchone()
            if not p or p["status"] != "open" or parse_dt(p["ends_at"]) <= datetime.now():
                raise DBError("انتهى التصويت")
            if not (0 <= idx < len(json.loads(p["options"]))):
                raise DBError("خيار غير صالح")
            await c.execute("INSERT OR REPLACE INTO poll_votes (poll_id, user_id, option_idx, created_at) VALUES (?,?,?,?)",
                            (pid, user_id, idx, now_iso()))

    async def poll_counts(self, pid, n_options):
        rows = await self.q_all("SELECT option_idx, COUNT(*) n FROM poll_votes WHERE poll_id=? GROUP BY option_idx", (pid,))
        counts = [0] * n_options
        for r in rows:
            if 0 <= r["option_idx"] < n_options:
                counts[r["option_idx"]] = r["n"]
        return counts

    async def due_polls(self):
        return await self.q_all("SELECT * FROM polls WHERE status='open' AND ends_at<=?", (now_iso(),))

    async def close_poll(self, pid, winner_idx, drop_id, status="closed"):
        await self.execute("UPDATE polls SET status=?, winner_idx=?, result_drop_id=? WHERE id=?", (status, winner_idx, drop_id, pid))

    async def cancel_poll(self, pid):
        return await self.execute("UPDATE polls SET is_deleted=1, status='cancelled' WHERE id=? AND status IN ('pending','open')", (pid,))

    async def list_polls(self, limit=10):
        return await self.q_all("SELECT * FROM polls ORDER BY id DESC LIMIT ?", (limit,))

    # ═════════ المزادات ═════════
    async def create_auction(self, plan, days, start_bid, step, duration_min, admin_id, publish_at=None):
        token = secrets.token_hex(5)
        async with self._tx() as c:
            cur = await c.execute(
                """INSERT INTO auctions (token, plan, days, start_bid, step, duration_min, status, publish_at, published, created_by, created_at)
                   VALUES (?,?,?,?,?,?, 'pending', ?,0,?,?)""",
                (token, plan, days, start_bid, step, duration_min, publish_at, admin_id, now_iso()))
            return cur.lastrowid

    async def get_auction(self, aid):
        return await self.q_one("SELECT * FROM auctions WHERE id=?", (aid,))

    async def open_auction(self, aid):
        a = await self.get_auction(aid)
        ends = (datetime.now() + timedelta(minutes=a["duration_min"])).isoformat(timespec="seconds")
        await self.execute("UPDATE auctions SET status='open', ends_at=?, published=1 WHERE id=?", (ends, aid))

    async def place_bid(self, aid, user_id, amount):
        async with self._tx() as c:
            cur = await c.execute("SELECT * FROM auctions WHERE id=?", (aid,))
            a = await cur.fetchone()
            if not a or a["status"] != "open" or parse_dt(a["ends_at"]) <= datetime.now():
                raise DBError("انتهى المزاد")
            if a["high_bidder"] == user_id:
                raise DBError("أنت صاحب أعلى مزايدة حالياً")
            min_bid = a["start_bid"] if a["high_bidder"] is None else a["high_bid"] + a["step"]
            if amount < min_bid:
                raise DBError(f"تم تجاوزك! الحد الأدنى الآن {min_bid} — اضغط 🔄 للتحديث")
            cur = await c.execute("SELECT points FROM users WHERE user_id=?", (user_id,))
            u = await cur.fetchone()
            if not u or u["points"] < amount:
                raise DBError("رصيدك لا يكفي لهذه المزايدة")
            ends = parse_dt(a["ends_at"])
            if (ends - datetime.now()).total_seconds() < 120:                 # حماية من القنص
                ends = datetime.now() + timedelta(seconds=120)
            await c.execute("UPDATE auctions SET high_bid=?, high_bidder=?, bids_count=bids_count+1, ends_at=? WHERE id=?",
                            (amount, user_id, ends.isoformat(timespec="seconds"), aid))
            await c.execute("INSERT INTO auction_bids (auction_id, user_id, amount, created_at) VALUES (?,?,?,?)",
                            (aid, user_id, amount, now_iso()))
            return {"prev_bidder": a["high_bidder"], "prev_bid": a["high_bid"], "ends_at": ends.isoformat(timespec="seconds")}

    async def due_auctions(self):
        return await self.q_all("SELECT * FROM auctions WHERE status='open' AND ends_at<=?", (now_iso(),))

    async def auction_candidates(self, aid):
        return await self.q_all("SELECT user_id, MAX(amount) amt FROM auction_bids WHERE auction_id=? GROUP BY user_id ORDER BY amt DESC", (aid,))

    async def close_auction(self, aid, status, winner_id=None, price=None, code=None):
        await self.execute("UPDATE auctions SET status=?, winner_id=?, final_price=?, winner_code=? WHERE id=?",
                           (status, winner_id, price, code, aid))

    async def cancel_auction(self, aid):
        return await self.execute("UPDATE auctions SET is_deleted=1, status='cancelled' WHERE id=? AND status IN ('pending','open') AND bids_count=0", (aid,))

    async def list_auctions(self, limit=10):
        return await self.q_all("SELECT * FROM auctions ORDER BY id DESC LIMIT ?", (limit,))

    # ═════════ البازار ═════════
    async def market_commission(self):
        return int(await self.get_setting("market_commission", MARKET_DEFAULT_COMMISSION))

    async def market_create_listing(self, seller_id, sub_id, price):
        async with self._tx() as c:
            cur = await c.execute("SELECT * FROM subscriptions WHERE id=? AND user_id=?", (sub_id, seller_id))
            s = await cur.fetchone()
            if not s:
                raise DBError("الاشتراك غير موجود")
            if s["listed"]:
                raise DBError("هذا الكود معروض للبيع مسبقاً")
            if s["expires_at"] and parse_dt(s["expires_at"]) <= datetime.now():
                raise DBError("هذا الاشتراك منتهي")
            cur = await c.execute("SELECT COUNT(*) FROM market_listings WHERE seller_id=? AND status='active'", (seller_id,))
            if (await cur.fetchone())[0] >= MARKET_MAX_LISTINGS:
                raise DBError(f"الحد الأقصى {MARKET_MAX_LISTINGS} عروض نشطة")
            cur = await c.execute(
                """INSERT INTO market_listings (seller_id, sub_id, code, plan, days, expires_at, price, status, created_at)
                   VALUES (?,?,?,?,?,?,?, 'active', ?)""",
                (seller_id, sub_id, s["code"], s["plan"], s["days"], s["expires_at"], price, now_iso()))
            await c.execute("UPDATE subscriptions SET listed=1 WHERE id=?", (sub_id,))
            return cur.lastrowid

    async def get_listing(self, lid):
        return await self.q_one("SELECT * FROM market_listings WHERE id=?", (lid,))

    async def list_listings(self, status="active", seller_id=None, limit=5, offset=0):
        sql, p = "SELECT * FROM market_listings WHERE status=?", [status]
        if seller_id:
            sql += " AND seller_id=?"
            p.append(seller_id)
        return await self.q_all(sql + " ORDER BY id DESC LIMIT ? OFFSET ?", [*p, limit, offset])

    async def count_listings(self, status="active", seller_id=None):
        sql, p = "SELECT COUNT(*) FROM market_listings WHERE status=?", [status]
        if seller_id:
            sql += " AND seller_id=?"
            p.append(seller_id)
        return await self.q_scalar(sql, p)

    async def market_set_status(self, lid, status, only_if="active"):
        async with self._tx() as c:
            cur = await c.execute("UPDATE market_listings SET status=? WHERE id=? AND status=?", (status, lid, only_if))
            if cur.rowcount == 0:
                return False
            cur = await c.execute("SELECT sub_id FROM market_listings WHERE id=?", (lid,))
            row = await cur.fetchone()
            if row and row[0]:
                await c.execute("UPDATE subscriptions SET listed=0 WHERE id=?", (row[0],))
            return True

    async def market_buy(self, lid, buyer_id):
        pct = await self.market_commission()
        async with self._tx() as c:
            cur = await c.execute("SELECT * FROM market_listings WHERE id=?", (lid,))
            l = await cur.fetchone()
            if not l or l["status"] != "active":
                raise DBError("العرض لم يعد متاحاً")
            if l["seller_id"] == buyer_id:
                raise DBError("لا يمكنك شراء عرضك")
            cur = await c.execute("UPDATE users SET points=points-? WHERE user_id=? AND points>=?", (l["price"], buyer_id, l["price"]))
            if cur.rowcount == 0:
                raise DBError("رصيدك غير كافٍ")
            commission = l["price"] * pct // 100
            payout = l["price"] - commission
            t = now_iso()
            await c.execute("UPDATE market_listings SET status='sold', buyer_id=?, commission=?, sold_at=? WHERE id=?",
                            (buyer_id, commission, t, lid))
            await c.execute("UPDATE users SET points=points+? WHERE user_id=?", (payout, l["seller_id"]))
            await c.execute("UPDATE subscriptions SET user_id=?, listed=0, source='market', reminded=0 WHERE id=?", (buyer_id, l["sub_id"]))
            for uid, amt, why in ((buyer_id, -l["price"], "market_buy"), (l["seller_id"], payout, "market_sale")):
                await c.execute("INSERT INTO points_log (user_id, amount, reason, ref_id, created_at) VALUES (?,?,?,?,?)", (uid, amt, why, lid, t))
            d = dict(l)
            d.update(status="sold", buyer_id=buyer_id, commission=commission, payout=payout, sold_at=t, commission_pct=pct)
            return d

    async def market_create_complaint(self, lid, buyer_id, reason, api_status):
        async with self._tx() as c:
            cur = await c.execute("SELECT * FROM market_listings WHERE id=? AND buyer_id=? AND status='sold'", (lid, buyer_id))
            l = await cur.fetchone()
            if not l:
                raise DBError("لا يمكن تقديم شكوى على هذا العرض")
            if (datetime.now() - parse_dt(l["sold_at"])).total_seconds() > COMPLAINT_HOURS * 3600:
                raise DBError(f"انتهت مهلة الشكوى ({COMPLAINT_HOURS} ساعة)")
            cur = await c.execute("SELECT COUNT(*) FROM market_complaints WHERE listing_id=?", (lid,))
            if (await cur.fetchone())[0]:
                raise DBError("تم تقديم شكوى على هذا العرض مسبقاً")
            cur = await c.execute(
                "INSERT INTO market_complaints (listing_id, buyer_id, seller_id, reason, api_status, status, created_at) VALUES (?,?,?,?,?, 'open', ?)",
                (lid, buyer_id, l["seller_id"], reason[:500], api_status, now_iso()))
            return cur.lastrowid

    async def get_complaint(self, cid):
        return await self.q_one("SELECT * FROM market_complaints WHERE id=?", (cid,))

    async def list_complaints(self, status="open", limit=5, offset=0):
        return await self.q_all("SELECT * FROM market_complaints WHERE status=? ORDER BY id DESC LIMIT ? OFFSET ?", (status, limit, offset))

    async def count_complaints(self, status="open"):
        return await self.q_scalar("SELECT COUNT(*) FROM market_complaints WHERE status=?", (status,))

    async def resolve_complaint(self, cid, refund, note=""):
        async with self._tx() as c:
            cur = await c.execute("SELECT * FROM market_complaints WHERE id=? AND status='open'", (cid,))
            cp = await cur.fetchone()
            if not cp:
                raise DBError("الشكوى غير مفتوحة")
            cur = await c.execute("SELECT * FROM market_listings WHERE id=?", (cp["listing_id"],))
            l = await cur.fetchone()
            t = now_iso()
            if refund:
                payout = l["price"] - l["commission"]
                await c.execute("UPDATE users SET points=points+? WHERE user_id=?", (l["price"], cp["buyer_id"]))
                await c.execute("UPDATE users SET points=MAX(0, points-?) WHERE user_id=?", (payout, cp["seller_id"]))
                await c.execute("UPDATE market_listings SET status='refunded' WHERE id=?", (l["id"],))
                for uid, amt, why in ((cp["buyer_id"], l["price"], "market_refund_in"), (cp["seller_id"], -payout, "market_refund_out")):
                    await c.execute("INSERT INTO points_log (user_id, amount, reason, ref_id, created_at) VALUES (?,?,?,?,?)", (uid, amt, why, l["id"], t))
            await c.execute("UPDATE market_complaints SET status=?, admin_note=?, resolved_at=? WHERE id=?",
                            ("refunded" if refund else "rejected", note, t, cid))
            return dict(cp), dict(l)

    async def market_stats(self):
        d7 = (datetime.now() - timedelta(days=7)).isoformat(timespec="seconds")
        return {
            "active": await self.count_listings("active"),
            "sold": await self.count_listings("sold"),
            "volume": await self.q_scalar("SELECT COALESCE(SUM(price),0) FROM market_listings WHERE status IN ('sold','refunded')"),
            "commission": await self.q_scalar("SELECT COALESCE(SUM(commission),0) FROM market_listings WHERE status='sold'"),
            "commission7": await self.q_scalar("SELECT COALESCE(SUM(commission),0) FROM market_listings WHERE status='sold' AND sold_at>=?", (d7,)),
            "refunded": await self.count_listings("refunded"),
            "open_complaints": await self.count_complaints("open"),
        }

    # ═════════ التنبيهات ═════════
    async def add_alert(self, level, kind, message):
        async with self._tx() as c:
            cur = await c.execute("INSERT INTO alerts (level, kind, message, created_at) VALUES (?,?,?,?)", (level, kind, message[:500], now_iso()))
            return cur.lastrowid

    async def recent_alert(self, kind, minutes):
        since = (datetime.now() - timedelta(minutes=minutes)).isoformat(timespec="seconds")
        return bool(await self.q_scalar("SELECT COUNT(*) FROM alerts WHERE kind=? AND created_at>=?", (kind, since)))

    async def list_alerts(self, limit=15):
        return await self.q_all("SELECT * FROM alerts ORDER BY id DESC LIMIT ?", (limit,))

    async def unseen_alerts(self):
        return await self.q_scalar("SELECT COUNT(*) FROM alerts WHERE seen=0")

    async def mark_alerts_seen(self):
        await self.execute("UPDATE alerts SET seen=1 WHERE seen=0")

    # ═════════ السجل والإحصاءات والتصدير ═════════
    async def log_admin(self, admin_id, action, details=""):
        await self.execute("INSERT INTO admin_logs (admin_id, action, details, created_at) VALUES (?,?,?,?)",
                           (admin_id, action, str(details)[:300], now_iso()))

    async def recent_logs(self, limit=20):
        return await self.q_all("SELECT * FROM admin_logs ORDER BY id DESC LIMIT ?", (limit,))

    async def daily_counts(self, table, col, days=14, where=""):
        start = (datetime.now() - timedelta(days=days - 1)).strftime("%Y-%m-%d")
        rows = await self.q_all(f"SELECT substr({col},1,10) d, COUNT(*) n FROM {table} WHERE {col}>=? {where} GROUP BY d", (start,))
        m = {r["d"]: r["n"] for r in rows}
        return [(( datetime.now() - timedelta(days=days - 1 - i)).strftime("%Y-%m-%d"),
                 m.get((datetime.now() - timedelta(days=days - 1 - i)).strftime("%Y-%m-%d"), 0)) for i in range(days)]

    async def daily_sum(self, table, col, sum_col, days=14, where=""):
        start = (datetime.now() - timedelta(days=days - 1)).strftime("%Y-%m-%d")
        rows = await self.q_all(f"SELECT substr({col},1,10) d, COALESCE(SUM({sum_col}),0) n FROM {table} WHERE {col}>=? {where} GROUP BY d", (start,))
        m = {r["d"]: r["n"] for r in rows}
        out = []
        for i in range(days):
            day = (datetime.now() - timedelta(days=days - 1 - i)).strftime("%Y-%m-%d")
            out.append((day, m.get(day, 0)))
        return out

    async def stats(self):
        today = datetime.now().strftime("%Y-%m-%d")
        d7 = (datetime.now() - timedelta(days=7)).isoformat(timespec="seconds")
        return {
            "users": await self.count_users(),
            "users_today": await self.q_scalar("SELECT COUNT(*) FROM users WHERE joined_at >= ?", (today,)),
            "dau": await self.q_scalar("SELECT COUNT(*) FROM users WHERE last_active >= ?", (today,)),
            "wau": await self.q_scalar("SELECT COUNT(*) FROM users WHERE last_active >= ?", (d7,)),
            "banned": await self.q_scalar("SELECT COUNT(*) FROM users WHERE is_banned=1"),
            "subs": await self.q_scalar("SELECT COUNT(*) FROM subscriptions"),
            "subs_today": await self.q_scalar("SELECT COUNT(*) FROM subscriptions WHERE created_at >= ?", (today,)),
            "spent": await self.q_scalar("SELECT COALESCE(SUM(points_spent),0) FROM subscriptions"),
            "spent_today": await self.q_scalar("SELECT COALESCE(SUM(points_spent),0) FROM subscriptions WHERE created_at >= ?", (today,)),
            "points_total": await self.q_scalar("SELECT COALESCE(SUM(points),0) FROM users"),
            "keys": await self.q_scalar("SELECT COUNT(*) FROM api_keys"),
            "drops_active": await self.count_drops("active"),
            "drops_sched": await self.count_drops("scheduled"),
            "auctions_open": await self.q_scalar("SELECT COUNT(*) FROM auctions WHERE status='open'"),
            "polls_open": await self.q_scalar("SELECT COUNT(*) FROM polls WHERE status='open'"),
        }

    async def export_rows(self, table):
        if table not in EXPORT_TABLES:
            raise ValueError(table)
        rows = await self.q_all(f"SELECT * FROM {table}")
        cols = list(rows[0].keys()) if rows else [r["name"] for r in await self.q_all(f"PRAGMA table_info({table})")]
        return cols, rows

    async def backup_to(self, path):
        async with self._lock:
            await self.conn.commit()
            await self.conn.execute("VACUUM INTO ?", (path,))

    async def purge_old(self):
        cutoff = (datetime.now() - timedelta(days=7)).isoformat(timespec="seconds")
        await self.execute("DELETE FROM api_log WHERE created_at<?", (cutoff,))
        await self.execute("DELETE FROM force_join_cache WHERE checked_at<?", (cutoff,))


db = Database(DB_PATH)
