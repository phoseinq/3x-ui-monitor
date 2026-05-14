#!/usr/bin/env python3
"""3x-ui Monitor Dashboard"""

import csv
import hashlib
import json
import os
import sqlite3
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path

import requests as _req
from flask import (Flask, jsonify, redirect, render_template_string,
                   request, send_from_directory, session, url_for)

try:
    from zoneinfo import ZoneInfo
    _HAS_ZONEINFO = True
except ImportError:
    _HAS_ZONEINFO = False

APP_DB      = "/opt/xui-monitor/app.db"
TRAFFIC_DB  = "/opt/xui-monitor/traffic.db"
STATIC_DIR  = "/opt/xui-monitor/static"
COOKIE_FILE = "/opt/xui-monitor/session.json"
BACKUP_DIR  = "/opt/xui-monitor/deleted_backup"
app = Flask(__name__, static_folder=STATIC_DIR)
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"]   = False  # set True when TLS is enabled

def app_db():
    c = sqlite3.connect(APP_DB)
    c.row_factory = sqlite3.Row
    return c

def traffic_db():
    c = sqlite3.connect(TRAFFIC_DB)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c

def init_app_db():
    with app_db() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS admin_users (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                username   TEXT    NOT NULL UNIQUE,
                password   TEXT    NOT NULL,
                role       TEXT    NOT NULL DEFAULT 'admin',
                created_at INTEGER NOT NULL DEFAULT (unixepoch())
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS cleanup_log (
                id     INTEGER PRIMARY KEY AUTOINCREMENT,
                ts     INTEGER NOT NULL DEFAULT (unixepoch()),
                email  TEXT    NOT NULL,
                reason TEXT    NOT NULL
            )
        """)
        defaults = {
            "panel_url":         "",
            "panel_user":        "",
            "panel_pass":        "",
            "grace_mb":          "100",
            "reset_ratio":       "0.5",
            "check_interval":    "30",
            "auto_restart_xray": "1",
            "cleanup_days":      "7",
            "cleanup_enabled":   "0",
            "cleanup_time":      "03:00",
            "timezone":          "Asia/Tehran",
            "dashboard_refresh": "30",
            "page_size":         "20",
            "history_days":      "7",
            "max_db_mb":         "0",
            "tls_enabled":           "0",
            "tls_cert":              "",
            "tls_key":               "",
            "tls_domain":            "",
            "panel_cleanup_enabled": "0",
            "panel_cleanup_time":    "00:00",
            "panel_cleanup_days":    "7",
        }
        c.executemany(
            "INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)",
            list(defaults.items()),
        )
        row = c.execute("SELECT value FROM settings WHERE key='secret_key'").fetchone()
        if not row:
            import secrets as _sec
            c.execute("INSERT INTO settings(key,value) VALUES('secret_key',?)",
                      (_sec.token_hex(32),))

def get_setting(key, default=None):
    with app_db() as c:
        row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default

def set_setting(key, value):
    with app_db() as c:
        c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (key, value))

def get_all_settings():
    with app_db() as c:
        rows = c.execute("SELECT key,value FROM settings").fetchall()
    return {r["key"]: r["value"] for r in rows}

def hash_password(pw: str) -> str:
    salt = os.urandom(16).hex()
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 260_000)
    return f"pbkdf2${salt}${h.hex()}"

def _verify_password(pw: str, stored: str) -> bool:
    if stored.startswith("pbkdf2$"):
        _, salt, expected = stored.split("$", 2)
        h = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 260_000)
        return h.hex() == expected
    return hashlib.sha256(pw.encode()).hexdigest() == stored

def count_admins():
    with app_db() as c:
        return c.execute("SELECT COUNT(*) FROM admin_users").fetchone()[0]

def check_credentials(username: str, password: str) -> bool:
    with app_db() as c:
        row = c.execute(
            "SELECT password FROM admin_users WHERE username=?", (username,)
        ).fetchone()
    if not row:
        return False
    if not _verify_password(password, row["password"]):
        return False
    if not row["password"].startswith("pbkdf2$"):
        with app_db() as c:
            c.execute("UPDATE admin_users SET password=? WHERE username=?",
                      (hash_password(password), username))
    return True

def create_admin(username, password, role="admin"):
    with app_db() as c:
        c.execute(
            "INSERT INTO admin_users(username,password,role) VALUES(?,?,?)",
            (username, hash_password(password), role),
        )

import secrets as _sec

# ── Rate limiting ─────────────────────────────────────────────────────────────
_login_attempts: dict = {}
_login_lock = threading.Lock()
_RATE_LIMIT  = 5
_RATE_WINDOW = 300

def _is_rate_limited(ip: str) -> bool:
    now = time.time()
    with _login_lock:
        ts = [t for t in _login_attempts.get(ip, []) if now - t < _RATE_WINDOW]
        _login_attempts[ip] = ts
        return len(ts) >= _RATE_LIMIT

def _record_fail(ip: str):
    now = time.time()
    with _login_lock:
        _login_attempts.setdefault(ip, []).append(now)

def _clear_fail(ip: str):
    with _login_lock:
        _login_attempts.pop(ip, None)

# ── CSRF ──────────────────────────────────────────────────────────────────────
def _get_csrf_token() -> str:
    if "csrf_token" not in session:
        session["csrf_token"] = _sec.token_hex(16)
    return session["csrf_token"]

def _csrf_ok() -> bool:
    token = session.get("csrf_token", "")
    if not token:
        return False
    form_tok   = request.form.get("csrf_token", "")
    header_tok = request.headers.get("X-CSRF-Token", "")
    return form_tok == token or header_tok == token

def require_login(f):
    @wraps(f)
    def wrapped(*a, **kw):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        if request.method == "POST" and not _csrf_ok():
            return jsonify({"ok": False, "error": "Invalid CSRF token"}), 403
        return f(*a, **kw)
    return wrapped

_tz_cache = {"name": "Asia/Tehran", "ts": 0.0}

def _get_tz_name():
    now = time.time()
    if now - _tz_cache["ts"] > 60:
        _tz_cache["name"] = get_setting("timezone", "Asia/Tehran")
        _tz_cache["ts"] = now
    return _tz_cache["name"]

def iran_fmt(ts):
    tz_name = _get_tz_name()
    if _HAS_ZONEINFO:
        try:
            return datetime.fromtimestamp(ts, tz=ZoneInfo(tz_name)).strftime("%Y-%m-%d %H:%M")
        except Exception:
            pass
    return datetime.utcfromtimestamp(ts + 3.5 * 3600).strftime("%Y-%m-%d %H:%M")

def latest_snapshot():
    with traffic_db() as c:
        ts = c.execute("SELECT MAX(ts) FROM snapshots").fetchone()[0]
        if not ts:
            return []
        return [dict(r) for r in c.execute(
            "SELECT email,up,down,total,quota,expired,enable "
            "FROM snapshots WHERE ts=? ORDER BY id ASC", (ts,)
        ).fetchall()]

def snapshot_summary():
    with traffic_db() as c:
        ts = c.execute("SELECT MAX(ts) FROM snapshots").fetchone()[0]
        if not ts:
            return {"total":0,"active":0,"over":0,"expired":0,"blocked":0,"total_bytes":0,"total_quota":0}
        rows = c.execute(
            "SELECT email,total,quota,expired,enable FROM snapshots WHERE ts=?", (ts,)
        ).fetchall()
        handled_emails = {r[0] for r in c.execute("SELECT email FROM handled")}
    total      = len(rows)
    total_bytes= sum(r["total"] for r in rows)
    total_quota= sum(r["quota"] for r in rows if r["quota"] > 0)
    expired    = sum(1 for r in rows if r["expired"])
    over       = sum(1 for r in rows if r["quota"] > 0 and r["total"] > r["quota"])
    active     = sum(1 for r in rows if r["enable"] and not r["expired"]
                     and not (r["quota"] > 0 and r["total"] > r["quota"])
                     and r["email"] not in handled_emails)
    return {"total":total,"active":active,"over":over,"expired":expired,
            "blocked":len(handled_emails),"total_bytes":total_bytes,"total_quota":total_quota}

def paginated_users(page=1, per_page=10, filter_="all", search="", sort="total", order="desc"):
    with traffic_db() as c:
        ts = c.execute("SELECT MAX(ts) FROM snapshots").fetchone()[0]
        if not ts:
            return {"users":[],"total":0,"pages":0,"page":1}
        rows = [dict(r) for r in c.execute(
            "SELECT email,up,down,total,quota,expired,enable FROM snapshots WHERE ts=?", (ts,)
        ).fetchall()]
        handled_emails = {r[0] for r in c.execute("SELECT email FROM handled")}
    online_set = set(e.lower() for e in (_online_cache.get("emails") or []))
    for r in rows:
        r["handled"] = r["email"] in handled_emails
        r["online"]  = r["email"].lower() in online_set
    if filter_ == "online":   rows = [r for r in rows if r["online"]]
    elif filter_ == "over":   rows = [r for r in rows if r["quota"] > 0 and r["total"] > r["quota"]]
    elif filter_ == "expired":rows = [r for r in rows if r["expired"]]
    elif filter_ == "handled":rows = [r for r in rows if r["handled"]]
    elif filter_ == "ok":     rows = [r for r in rows if r["enable"] and not r["expired"]
                                      and not (r["quota"] > 0 and r["total"] > r["quota"]) and not r["handled"]]
    if search:
        q = search.lower()
        rows = [r for r in rows if q in r["email"].lower()]
    reverse = (order == "desc")
    if sort == "email":   rows.sort(key=lambda r: r["email"].lower(), reverse=not reverse)
    elif sort == "quota": rows.sort(key=lambda r: r["quota"], reverse=reverse)
    elif sort == "pct":   rows.sort(key=lambda r: r["total"]/r["quota"] if r["quota"] > 0 else 0, reverse=reverse)
    elif sort == "total": rows.sort(key=lambda r: r["total"], reverse=reverse)
    total_count = len(rows)
    pages = max(1, (total_count + per_page - 1) // per_page)
    page  = max(1, min(page, pages))
    start = (page - 1) * per_page
    return {"users":rows[start:start+per_page],"total":total_count,"pages":pages,"page":page}

def user_snapshot(email):
    with traffic_db() as c:
        ts = c.execute("SELECT MAX(ts) FROM snapshots").fetchone()[0]
        if not ts:
            return None
        row = c.execute(
            "SELECT email,up,down,total,quota,expired,enable "
            "FROM snapshots WHERE ts=? AND email=?", (ts, email)
        ).fetchone()
    return dict(row) if row else None

def total_hourly(hours=24, bucket_min=60):
    since = int(time.time()) - hours * 3600
    bkt   = bucket_min * 60
    with traffic_db() as c:
        rows = c.execute("""
            SELECT bucket, SUM(CASE WHEN delta > 0 THEN delta ELSE 0 END) AS total
            FROM (
                SELECT (ts/:bkt)*:bkt AS bucket,
                       email,
                       MAX(total) - MIN(total) AS delta
                FROM snapshots WHERE ts >= :since
                GROUP BY bucket, email
            )
            GROUP BY bucket ORDER BY bucket
        """, {"bkt": bkt, "since": since}).fetchall()
    return [{"hour": iran_fmt(r["bucket"]), "bytes": r["total"] or 0,
             "gb": round((r["total"] or 0) / 1024**3, 4)} for r in rows]

def user_hourly(email, hours=24, bucket_min=30):
    since = int(time.time()) - hours * 3600
    bkt   = bucket_min * 60
    with traffic_db() as c:
        rows = c.execute("""
            SELECT (ts/:bkt)*:bkt AS bucket, MAX(total)-MIN(total) AS delta
            FROM snapshots WHERE ts>=:since AND email=:email
            GROUP BY bucket ORDER BY bucket
        """, {"bkt": bkt, "since": since, "email": email}).fetchall()
    return [{"hour": iran_fmt(r["bucket"]), "bytes": max(r["delta"], 0),
             "gb": round(max(r["delta"], 0) / 1024**3, 4)} for r in rows]

def traffic_top_users(hours=24, limit=15):
    since = int(time.time()) - hours * 3600
    bkt   = 3600
    with traffic_db() as c:
        rows = c.execute("""
            SELECT email, SUM(CASE WHEN delta>0 THEN delta ELSE 0 END) AS total
            FROM (
                SELECT email, (ts/:bkt)*:bkt AS bucket,
                       MAX(total)-MIN(total) AS delta
                FROM snapshots WHERE ts>=:since
                GROUP BY email, bucket
            )
            GROUP BY email HAVING total>0
            ORDER BY total DESC LIMIT :lim
        """, {"bkt": bkt, "since": since, "lim": limit}).fetchall()
    return [{"email": r["email"], "bytes": int(r["total"] or 0),
             "gb": round((r["total"] or 0) / 1024**3, 4)} for r in rows]

def recent_restarts(limit=20):
    with traffic_db() as c:
        rows = c.execute(
            "SELECT ts,reason FROM restarts ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall()
    return [{"time": iran_fmt(r["ts"]), "reason": r["reason"]} for r in rows]

def handled_list():
    try:
        with traffic_db() as c:
            rows = c.execute(
                "SELECT email,triggered_at,total_at_trigger FROM handled"
            ).fetchall()
        return [{"email": r["email"], "at": iran_fmt(r["triggered_at"]),
                 "total": r["total_at_trigger"]} for r in rows]
    except Exception:
        return []

_online_cache = {"ts": 0.0, "count": 0, "emails": []}
_user_online_since: dict = {}   # email -> unix timestamp when they first came online

_ttl_cache: dict = {}

def _cached(key: str, ttl: float, fn):
    now = time.time()
    entry = _ttl_cache.get(key)
    if entry and now - entry[0] < ttl:
        return entry[1]
    val = fn()
    _ttl_cache[key] = (now, val)
    return val

def _cache_clear(*prefixes):
    for k in list(_ttl_cache):
        if not prefixes or any(k.startswith(p) for p in prefixes):
            _ttl_cache.pop(k, None)

def _ensure_online_log():
    with traffic_db() as c:
        c.execute("CREATE TABLE IF NOT EXISTS online_log(ts INTEGER NOT NULL, count INTEGER NOT NULL DEFAULT 0)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_online_log_ts ON online_log(ts)")

def fetch_online():
    """Compute online users from traffic delta between last two snapshots."""
    now = time.time()
    if now - _online_cache["ts"] < 30:
        return _online_cache["count"], _online_cache["emails"]
    try:
        with traffic_db() as c:
            ts_rows = c.execute(
                "SELECT DISTINCT ts FROM snapshots ORDER BY ts DESC LIMIT 2"
            ).fetchall()
            if len(ts_rows) < 2:
                _online_cache.update({"ts": now, "count": 0, "emails": []})
                return 0, []
            ts_new, ts_old = ts_rows[0]["ts"], ts_rows[1]["ts"]
            if now - ts_new > 180:
                _online_cache.update({"ts": now, "count": 0, "emails": []})
                return 0, []
            rows = c.execute("""
                SELECT n.email
                FROM snapshots n
                JOIN snapshots o ON n.email = o.email
                WHERE n.ts = ? AND o.ts = ?
                  AND (n.total - o.total) > 10240
                  AND n.enable = 1
            """, (ts_new, ts_old)).fetchall()
        emails = [r["email"] for r in rows]
        count  = len(emails)
        _online_cache.update({"ts": now, "count": count, "emails": emails})
        now_set  = set(emails)
        prev_set = set(_user_online_since)
        for e in prev_set - now_set:
            del _user_online_since[e]
        for e in now_set - prev_set:
            _user_online_since[e] = now
        try:
            with traffic_db() as c:
                if get_setting("online_log_enabled", "0") == "1":
                    c.execute("INSERT INTO online_log(ts, count) VALUES(?,?)", (int(now), count))
                    c.execute("DELETE FROM online_log WHERE ts < ?", (int(now) - 90000,))
        except Exception:
            pass
        return count, emails
    except Exception:
        _online_cache["ts"] = now
        return _online_cache["count"], _online_cache["emails"]

def _ensure_server_table():
    with traffic_db() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS server_snapshots (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                ts         INTEGER NOT NULL,
                cpu        REAL    NOT NULL DEFAULT 0,
                mem_used   INTEGER NOT NULL DEFAULT 0,
                mem_total  INTEGER NOT NULL DEFAULT 0,
                disk_used  INTEGER NOT NULL DEFAULT 0,
                disk_total INTEGER NOT NULL DEFAULT 0,
                net_sent   INTEGER NOT NULL DEFAULT 0,
                net_recv   INTEGER NOT NULL DEFAULT 0,
                uptime     INTEGER NOT NULL DEFAULT 0
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_srv_ts ON server_snapshots(ts)")

_ensure_server_table()

def _save_server_snapshot(obj: dict):
    """Save snapshot using OS sources so history matches live display."""
    try:
        mu, mt = _read_mem()
        du, dt = _read_disk()
        sent, recv = _read_net_bytes()
        with traffic_db() as c:
            c.execute(
                "INSERT INTO server_snapshots"
                "(ts,cpu,mem_used,mem_total,disk_used,disk_total,net_sent,net_recv,uptime)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    int(time.time()),
                    _get_cpu_snapshot(),
                    mu, mt, du, dt,
                    sent, recv,
                    obj.get("uptime", 0),
                )
            )
    except Exception:
        pass

def server_history(metric="cpu", hours=24, bucket_min=60):
    since = int(time.time()) - hours * 3600
    bkt   = bucket_min * 60
    with traffic_db() as c:
        if metric == "net":
            rows = c.execute("""
                SELECT bucket,
                       SUM(CASE WHEN dsent>0 THEN dsent ELSE 0 END) AS up,
                       SUM(CASE WHEN drecv>0 THEN drecv ELSE 0 END) AS down
                FROM (
                    SELECT (ts/:bkt)*:bkt AS bucket,
                           MAX(net_sent)-MIN(net_sent) AS dsent,
                           MAX(net_recv)-MIN(net_recv) AS drecv
                    FROM server_snapshots WHERE ts>=:since
                    GROUP BY bucket
                )
                GROUP BY bucket ORDER BY bucket
            """, {"bkt": bkt, "since": since}).fetchall()
            return [{"hour":       iran_fmt(r["bucket"]),
                     "up":        round((r["up"]   or 0) / bkt / 1024**2, 4),
                     "down":      round((r["down"] or 0) / bkt / 1024**2, 4),
                     "total_up":  round((r["up"]   or 0) / 1024**2, 2),
                     "total_down":round((r["down"] or 0) / 1024**2, 2),
                     "unit": "MB/s"} for r in rows]
        elif metric == "ram":
            rows = c.execute("""
                SELECT (ts/:bkt)*:bkt AS bucket,
                       AVG(CAST(mem_used AS REAL)/mem_total*100) AS val
                FROM server_snapshots WHERE ts>=:since AND mem_total>0
                GROUP BY bucket ORDER BY bucket
            """, {"bkt": bkt, "since": since}).fetchall()
        elif metric == "disk":
            rows = c.execute("""
                SELECT (ts/:bkt)*:bkt AS bucket,
                       AVG(CAST(disk_used AS REAL)/disk_total*100) AS val
                FROM server_snapshots WHERE ts>=:since AND disk_total>0
                GROUP BY bucket ORDER BY bucket
            """, {"bkt": bkt, "since": since}).fetchall()
        else:  # cpu
            rows = c.execute("""
                SELECT (ts/:bkt)*:bkt AS bucket, AVG(cpu) AS val
                FROM server_snapshots WHERE ts>=:since
                GROUP BY bucket ORDER BY bucket
            """, {"bkt": bkt, "since": since}).fetchall()
    return [{"hour": iran_fmt(r["bucket"]),
             "value": round(r["val"] or 0, 2),
             "unit": "%"} for r in rows]

_bw_prev:  dict = {"ts": 0.0, "sent": 0, "recv": 0}
_bw_cache: dict = {"ts": 0.0, "up": 0.0, "down": 0.0}

def _read_net_bytes() -> tuple[int, int]:
    sent = recv = 0
    try:
        with open("/proc/net/dev") as f:
            for line in f.readlines()[2:]:
                parts = line.split()
                if len(parts) < 10:
                    continue
                iface = parts[0].rstrip(":")
                if iface == "lo":
                    continue
                recv += int(parts[1])
                sent += int(parts[9])
    except Exception:
        pass
    return sent, recv

import collections as _col
_cpu_buf  = _col.deque(maxlen=60)   # up to 60 s of readings
_cpu_prev = {"idle": 0, "total": 0}

def _cpu_reader():
    while True:
        try:
            with open("/proc/stat") as f:
                parts = f.readline().split()
            nums  = [int(x) for x in parts[1:9]]
            total = sum(nums)
            idle  = nums[3] + nums[4]
            p = _cpu_prev
            if p["total"] and total > p["total"]:
                d_tot  = total - p["total"]
                d_idle = idle  - p["idle"]
                _cpu_buf.append(max(0.0, min(100.0, (1 - d_idle / d_tot) * 100)))
            p["idle"]  = idle
            p["total"] = total
        except Exception:
            pass
        time.sleep(1)

threading.Thread(target=_cpu_reader, daemon=True, name="cpu-reader").start()

def _read_cpu_pct() -> float:
    """5-second rolling average — used by /api/live/stats."""
    recent = list(_cpu_buf)[-5:]
    return round(sum(recent) / len(recent), 1) if recent else 0.0

def _get_cpu_snapshot() -> float:
    """Average of all buffered readings — used by server snapshot writer."""
    buf = list(_cpu_buf)
    return round(sum(buf) / len(buf), 1) if buf else 0.0

def _read_mem() -> tuple[int, int]:
    t = a = 0
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    t = int(line.split()[1]) * 1024
                elif line.startswith("MemAvailable:"):
                    a = int(line.split()[1]) * 1024
    except Exception:
        pass
    return t - a, t

def _read_disk() -> tuple[int, int]:
    try:
        st = os.statvfs("/")
        total = st.f_blocks * st.f_frsize
        used  = (st.f_blocks - st.f_bfree) * st.f_frsize
        return used, total
    except Exception:
        return 0, 0

def get_live_bandwidth() -> dict:
    now = time.time()
    if now - _bw_cache["ts"] < 1:
        return _bw_cache
    sent, recv = _read_net_bytes()
    if _bw_prev["ts"] > 0:
        dt = max(now - _bw_prev["ts"], 0.001)
        up = max((sent - _bw_prev["sent"]) / dt, 0.0)
        dn = max((recv - _bw_prev["recv"]) / dt, 0.0)
    else:
        up = dn = 0.0
    _bw_prev.update({"ts": now, "sent": sent, "recv": recv})
    _bw_cache.update({"ts": now, "up": up, "down": dn})
    return _bw_cache

_server_cache: dict = {"ts": 0.0, "save_ts": 0.0, "data": {}}

def fetch_server_stats() -> dict:
    now = time.time()
    if now - _server_cache["ts"] < 5:
        return _server_cache["data"]
    try:
        panel_url = get_setting("panel_url", "").rstrip("/")
        s = _req.Session()
        try:
            s.cookies.update(json.loads(Path(COOKIE_FILE).read_text()))
        except Exception:
            pass
        r    = s.get(f"{panel_url}/panel/api/server/status", timeout=8)
        data = r.json()
        if r.status_code == 401 or not data.get("success"):
            pu = get_setting("panel_user", "")
            pp = get_setting("panel_pass", "")
            s.post(f"{panel_url}/login", json={"username": pu, "password": pp}, timeout=10)
            r    = s.get(f"{panel_url}/panel/api/server/status", timeout=8)
            data = r.json()
        obj = data.get("obj") or {}
        _server_cache.update({"ts": now, "data": obj})
        if now - _server_cache["save_ts"] >= 60:
            _save_server_snapshot(obj)
            _server_cache["save_ts"] = now
        return obj
    except Exception:
        _server_cache["ts"] = now
        return _server_cache["data"]

def _prune_by_size():
    max_mb = int(get_setting("max_db_mb", "0"))
    if max_mb <= 0:
        return
    size_mb = os.path.getsize(TRAFFIC_DB) / 1024**2 if os.path.exists(TRAFFIC_DB) else 0
    if size_mb <= max_mb:
        return
    with traffic_db() as c:
        total = c.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
        chunk = max(total // 10, 1000)
        c.execute("""DELETE FROM snapshots WHERE id IN
                     (SELECT id FROM snapshots ORDER BY id ASC LIMIT ?)""", (chunk,))
        c.execute("VACUUM")

def run_cleanup(days: int) -> int:
    """Delete snapshots older than `days` days. Returns rows deleted."""
    cutoff = int(time.time()) - days * 86400
    with traffic_db() as c:
        deleted = c.execute("DELETE FROM snapshots WHERE ts < ?", (cutoff,)).rowcount
    if deleted:
        with traffic_db() as c:
            c.execute("VACUUM")
    return deleted

def _prune_loop():
    _last_day = [None]
    while True:
        time.sleep(1800)   # check every 30 min
        try:
            _prune_by_size()
            if get_setting("cleanup_enabled", "0") != "1":
                continue
            days = max(1, int(get_setting("cleanup_days", "7") or 7))
            ct   = get_setting("cleanup_time", "03:00") or "03:00"
            tz_name = get_setting("timezone", "Asia/Tehran") or "Asia/Tehran"
            try:
                ch, cm = map(int, ct.split(":"))
            except Exception:
                ch, cm = 3, 0
            try:
                now_local = datetime.now(ZoneInfo(tz_name))
            except Exception:
                now_local = datetime.now()
            today = now_local.date()
            if now_local.hour == ch and _last_day[0] != today:
                deleted = run_cleanup(days)
                _last_day[0] = today
        except Exception:
            pass

threading.Thread(target=_prune_loop, daemon=True).start()

def _panel_cleanup_loop():
    _last_day = [None]
    while True:
        time.sleep(1800)
        try:
            if get_setting("panel_cleanup_enabled", "0") != "1":
                continue
            ct      = get_setting("panel_cleanup_time", "00:00") or "00:00"
            tz_name = get_setting("timezone", "Asia/Tehran") or "Asia/Tehran"
            try:
                ch, cm = map(int, ct.split(":"))
            except Exception:
                ch, cm = 0, 0
            try:
                now_local = datetime.now(ZoneInfo(tz_name))
            except Exception:
                now_local = datetime.now()
            today = now_local.date()
            if now_local.hour == ch and _last_day[0] != today:
                _last_day[0] = today
                old_days = max(1, int(get_setting("panel_cleanup_days", "7") or 7))
                candidates, err = fetch_panel_candidates(old_days)
                if err or not candidates:
                    continue
                aged = [x for x in candidates if x.get("aged")]
                if aged:
                    bp = _backup_deleted_users(aged)
                    if bp:
                        import logging as _log
                        _log.getLogger(__name__).info("Panel cleanup backup saved: %s (%d users)", bp, len(aged))
                    for c in aged:
                        delete_panel_client(int(c["inbound_id"]), str(c["client_id"]))
        except Exception:
            pass

threading.Thread(target=_panel_cleanup_loop, daemon=True, name="panel-cleanup").start()

_panel_sess_obj = _req.Session()

def _panel_api(method: str, path: str, **kwargs):
    """Call panel API with auto-reauth on 401. Returns parsed JSON or None."""
    base = get_setting("panel_url", "").rstrip("/")
    if not base:
        return None
    try:
        _panel_sess_obj.cookies.update(json.loads(Path(COOKIE_FILE).read_text()))
    except Exception:
        pass
    for attempt in range(2):
        try:
            r    = _panel_sess_obj.request(method, f"{base}{path}", timeout=12, **kwargs)
            data = r.json()
            if r.status_code == 401 or not data.get("success"):
                if attempt == 0:
                    pu = get_setting("panel_user", "")
                    pp = get_setting("panel_pass", "")
                    lr = _panel_sess_obj.post(
                        f"{base}/login", json={"username": pu, "password": pp}, timeout=10
                    )
                    if lr.json().get("success"):
                        try:
                            Path(COOKIE_FILE).write_text(json.dumps(dict(_panel_sess_obj.cookies)))
                        except Exception:
                            pass
                        continue
                return None
            return data
        except Exception:
            return None
    return None

def fetch_panel_candidates(old_days: int = 30):
    """
    Return (list_of_candidates, error_str).
    Three categories — a user can match more than one:
      expired  : expiryTime has passed, within the last 90 days
      over_quota: totalGB limit exceeded (no time cap)
      aged     : expired more than old_days ago (subset of expired)
    """
    data = _panel_api("GET", "/panel/api/inbounds/list")
    if not data or not data.get("success"):
        return None, "Could not fetch panel data"
    now_ms    = int(time.time() * 1000)
    cap_ms    = now_ms - 90 * 86_400_000        # hard 90-day cap for expired
    old_ms    = now_ms - old_days * 86_400_000   # threshold for "aged"
    seen      = set()
    candidates = []
    for ib in data.get("obj", []):
        inbound_id = ib.get("id")
        stats      = {s["email"]: s for s in (ib.get("clientStats") or [])}
        try:
            settings = json.loads(ib.get("settings", "{}"))
        except Exception:
            settings = {}
        for c in settings.get("clients", []):
            email  = c.get("email", "")
            quota  = float(c.get("totalGB", 0))
            exp_ms = c.get("expiryTime", 0)
            st     = stats.get(email, {})
            up     = float(st.get("up", 0))
            down   = float(st.get("down", 0))
            total  = up + down

            expired    = bool(exp_ms and exp_ms > cap_ms and exp_ms < now_ms)
            over_quota = quota > 0 and total > quota
            aged       = expired and exp_ms < old_ms   # expired > old_days ago

            if not expired and not over_quota:
                continue

            cid = c.get("id", "")
            if cid in seen:
                continue
            seen.add(cid)

            exp_days    = round((now_ms - exp_ms) / 86_400_000) if (exp_ms and exp_ms < now_ms) else 0
            expiry_date = datetime.fromtimestamp(exp_ms / 1000).strftime("%Y-%m-%d") if exp_ms else ""
            candidates.append({
                "inbound_id":   inbound_id,
                "client_id":    cid,
                "email":        email,
                "subscription": c.get("subId", ""),
                "tg_id":        c.get("tgId", ""),
                "comment":      c.get("comment", ""),
                "quota_gb":     round(quota / 1024**3, 2) if quota else 0,
                "up_gb":        round(up   / 1024**3, 2),
                "down_gb":      round(down / 1024**3, 2),
                "total_gb":     round(total / 1024**3, 2),
                "pct":          round(total / quota * 100) if quota > 0 else 0,
                "expired":      expired,
                "over_quota":   over_quota,
                "aged":         aged,
                "expired_days": exp_days,
                "expiry_date":  expiry_date,
            })
    return candidates, None

def delete_panel_client(inbound_id: int, client_id: str) -> tuple:
    """Delete one client from panel. Returns (success, message)."""
    data = _panel_api("POST", f"/panel/api/inbounds/{inbound_id}/delClient/{client_id}")
    if data and data.get("success"):
        return True, "ok"
    return False, (data or {}).get("msg", "unknown error")


def _backup_deleted_users(users: list[dict]) -> str:
    """Write CSV of users about to be deleted. Returns the saved file path."""
    if not users:
        return ""
    Path(BACKUP_DIR).mkdir(parents=True, exist_ok=True)
    ts   = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    path = f"{BACKUP_DIR}/{ts}.csv"
    fields = [
        "email", "client_id", "inbound_id",
        "subscription", "tg_id", "comment",
        "quota_gb", "up_gb", "down_gb", "total_gb", "pct",
        "expiry_date", "expired_days", "expired", "over_quota",
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(users)
    return path

BASE_STYLE = r"""
<style>
:root{
  --bg:#070b13;--surface:#0b1220;--card:#0f1829;
  --border:#1a2840;--text:#dde4f0;--muted:#4a637a;
  --blue:#4f8ef7;--green:#22c55e;--amber:#f59e0b;
  --red:#f04a4a;--purple:#a78bfa;
}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:'Segoe UI',system-ui,sans-serif;font-size:14px;min-height:100vh}
a{color:inherit;text-decoration:none}

.sb-backdrop{position:fixed;inset:0;z-index:299;
  background:rgba(0,0,0,0);backdrop-filter:blur(0px);
  pointer-events:none;transition:background .3s ease,backdrop-filter .3s ease}
.sb-backdrop.open{background:rgba(0,0,0,.5);backdrop-filter:blur(3px);pointer-events:auto}
.sidebar{position:fixed;top:0;left:-260px;bottom:0;width:240px;
  background:var(--surface);border-right:1px solid var(--border);
  z-index:300;transition:left .3s cubic-bezier(.4,0,.2,1),box-shadow .3s ease;
  display:flex;flex-direction:column;overflow-y:auto}
.sidebar.open{left:0;box-shadow:8px 0 40px rgba(0,0,0,.6)}
.sb-hd{padding:14px 18px;border-bottom:1px solid var(--border);
  display:flex;align-items:center;justify-content:space-between;min-height:52px}
.sb-logo{display:flex;align-items:center;gap:8px;font-weight:700;
  font-size:.9rem;color:var(--blue)}
.sb-x{background:none;border:none;cursor:pointer;color:var(--muted);
  padding:5px;border-radius:6px;display:flex;transition:color .12s}
.sb-x:hover{color:var(--text)}
.sb-section{font-size:.6rem;text-transform:uppercase;letter-spacing:.9px;
  color:var(--muted);padding:14px 18px 5px;opacity:.55}
.sb-link{display:flex;align-items:center;gap:10px;padding:10px 18px;
  color:var(--muted);font-size:.82rem;border-left:3px solid transparent;
  transition:all .12s}
.sb-link:hover{color:var(--text);background:rgba(79,142,247,.07);border-left-color:var(--border)}
.sb-link.on{color:var(--blue);background:rgba(79,142,247,.12);border-left-color:var(--blue)}
button.sb-link{width:100%;background:none;border:none;cursor:pointer;text-align:left;font-family:inherit}
.sb-footer{margin-top:auto;padding:12px 18px;border-top:1px solid var(--border);
  font-size:.7rem;color:var(--muted);display:flex;flex-direction:column;gap:8px}
.sb-user{display:flex;align-items:center;gap:7px;color:var(--muted);font-size:.75rem}
.sb-username{font-weight:600;color:var(--text);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.sb-logout{display:flex;align-items:center;gap:8px;color:var(--muted);font-size:.78rem;
  padding:7px 0;border-top:1px solid var(--border);transition:color .12s}
.sb-logout:hover{color:#f04a4a}

.hamburger{background:none;border:1px solid transparent;cursor:pointer;
  color:var(--muted);padding:6px 7px;border-radius:7px;
  display:flex;align-items:center;transition:all .15s;flex-shrink:0}
.hamburger:hover{color:var(--text);border-color:var(--border)}
.topbar{position:sticky;top:0;z-index:100;
  background:rgba(7,11,19,.93);backdrop-filter:blur(14px);
  border-bottom:1px solid var(--border);
  display:flex;align-items:center;gap:8px;padding:0 16px;height:52px}
.logo{display:flex;align-items:center;gap:8px;font-weight:700;
  font-size:.92rem;color:var(--blue);white-space:nowrap}
.logo-text{display:inline}
.topbar-right{margin-left:auto;display:flex;align-items:center;gap:8px}
.clock{font-size:.74rem;color:var(--muted);font-variant-numeric:tabular-nums;white-space:nowrap}
.live-dot{width:7px;height:7px;border-radius:50%;background:var(--green);
  box-shadow:0 0 6px var(--green);animation:pulse 2s infinite;flex-shrink:0}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
@keyframes spin{to{transform:rotate(360deg)}}
@keyframes shimmer{0%{background-position:200% 0}100%{background-position:-200% 0}}
.skel{background:linear-gradient(90deg,#0d172a 25%,#152236 50%,#0d172a 75%);
  background-size:200% 100%;animation:shimmer 1.5s infinite;border-radius:5px;display:inline-block}
.refresh-badge{display:flex;align-items:center;gap:3px;font-size:.7rem;
  color:var(--muted);background:var(--card);border:1px solid var(--border);
  border-radius:99px;padding:3px 9px;white-space:nowrap}

.btn{display:inline-flex;align-items:center;gap:6px;padding:7px 13px;border-radius:8px;
  border:1px solid var(--border);background:var(--card);color:var(--muted);
  font-size:.77rem;cursor:pointer;transition:all .2s cubic-bezier(.4,0,.2,1);white-space:nowrap}
.btn:hover{border-color:var(--blue);color:var(--blue);transform:translateY(-1px);box-shadow:0 4px 12px rgba(79,142,247,.15)}
.btn:active{transform:translateY(0);box-shadow:none}
.btn-primary{background:#1a2f5e;color:var(--blue);border-color:#2a4580}
.btn-primary:hover{background:#223a72}
.btn-danger{background:#3b0f0f;color:var(--red);border-color:#6b1a1a}
.btn-danger:hover{background:#4a1515}

.stat-box{background:var(--surface);border:1px solid var(--border);border-radius:9px;padding:11px 13px;
  transition:border-color .2s,box-shadow .2s}
.stat-box .sv{font-size:.95rem;font-weight:700;margin-bottom:2px}
.stat-box .sl{font-size:.63rem;color:var(--muted);text-transform:uppercase;letter-spacing:.5px}

.chip{padding:5px 11px;border-radius:7px;border:1px solid var(--border);
  background:var(--card);color:var(--muted);font-size:.73rem;cursor:pointer;
  transition:all .2s cubic-bezier(.4,0,.2,1)}
.chip.active{background:#1a2f5e;color:var(--blue);border-color:#2a4580}
.chip:hover:not(.active){border-color:var(--blue);color:var(--blue);transform:translateY(-1px)}
.chip:active{transform:translateY(0)}

.wrap{max-width:1600px;margin:0 auto;padding:20px 22px}

.kpi-row{display:grid;grid-template-columns:repeat(6,minmax(0,1fr)) minmax(0,2fr);gap:10px;margin-bottom:22px}
.kpi-row .kpi:last-child .val{font-size:.95rem}
.kpi{min-width:0;background:var(--card);border:1px solid var(--border);border-radius:12px;
  padding:12px 11px;display:flex;align-items:center;gap:10px;
  transition:border-color .2s,box-shadow .2s,transform .2s cubic-bezier(.4,0,.2,1)}
.kpi:hover{border-color:#2a4580;box-shadow:0 4px 16px rgba(79,142,247,.1);transform:translateY(-2px)}
.kpi-icon{width:40px;height:40px;border-radius:10px;display:flex;
  align-items:center;justify-content:center;flex-shrink:0}
.kpi-body{min-width:0;flex:1;overflow:hidden}
.kpi-body .val{font-size:1.18rem;font-weight:700;line-height:1.15;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.kpi-body .lbl{font-size:.62rem;color:var(--muted);margin-top:2px;
  text-transform:uppercase;letter-spacing:.5px;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}

.sec{display:flex;align-items:center;gap:10px;margin:22px 0 11px}
.sec-title{font-size:.7rem;font-weight:600;text-transform:uppercase;
  letter-spacing:.8px;color:var(--muted);white-space:nowrap}
.sec-line{flex:1;height:1px;background:var(--border)}

.toolbar{display:flex;gap:7px;flex-wrap:wrap;align-items:center;margin-bottom:11px}
.search-wrap{position:relative}
.search-wrap svg{position:absolute;left:10px;top:50%;transform:translateY(-50%);
  pointer-events:none;color:var(--muted)}
.search{background:var(--card);border:1px solid var(--border);border-radius:8px;
  padding:7px 11px 7px 31px;color:var(--text);font-size:.81rem;width:190px;outline:none}
.search:focus{border-color:var(--blue)}
.pg-size-sel{background:var(--card);border:1px solid var(--border);
  border-radius:7px;padding:5px 9px;color:var(--text);font-size:.73rem;
  cursor:pointer;outline:none;transition:border .15s}
.pg-size-sel:focus{border-color:var(--blue)}

.tbl-wrap{background:var(--card);border:1px solid var(--border);border-radius:13px;overflow:hidden}
.tbl-scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
table{width:100%;border-collapse:collapse;min-width:560px}
thead th{background:#090e1a;padding:9px 13px;text-align:left;
  font-size:.66rem;text-transform:uppercase;letter-spacing:.6px;
  color:var(--muted);font-weight:600;white-space:nowrap;
  border-bottom:1px solid var(--border)}
thead th.sortable{cursor:pointer;user-select:none;transition:color .12s}
thead th.sortable:hover{color:var(--text)}
.sort-arrow{font-size:.58rem;margin-left:3px;color:var(--blue)}
td{padding:10px 13px;border-top:1px solid var(--border);vertical-align:middle}
tbody tr{transition:background .18s,box-shadow .18s;cursor:pointer}
tbody tr:hover{background:#0d172a;box-shadow:inset 3px 0 0 var(--blue)}

.prog-wrap{display:flex;align-items:center;gap:7px}
.prog-bg{height:5px;background:#1a2840;border-radius:99px;width:75px;overflow:hidden;flex-shrink:0}
.prog-fill{height:5px;border-radius:99px}
.prog-pct{font-size:.69rem;color:var(--muted);min-width:28px}

.badge{display:inline-flex;align-items:center;gap:4px;padding:3px 8px;
  border-radius:99px;font-size:.64rem;font-weight:600}
.b-ok    {background:#052e16;color:#22c55e;border:1px solid #064e26}
.b-over  {background:#3b0f0f;color:#f04a4a;border:1px solid #6b1a1a}
.b-handle{background:#1e1438;color:#a78bfa;border:1px solid #3b2a6e}
.b-off   {background:#111827;color:var(--muted);border:1px solid #1f2937}
.b-exp   {background:#2d1e00;color:#f59e0b;border:1px solid #5a3c00}
.b-online{background:#062030;color:#06b6d4;border:1px solid #0e4f6e}
.log-toggle{display:inline-flex;align-items:center;gap:6px;padding:4px 11px;border-radius:6px;
  border:1px solid var(--border);background:var(--surface);color:var(--muted);
  cursor:pointer;font-size:.72rem;font-family:inherit;transition:all .18s;white-space:nowrap}
.log-toggle .ldot{width:6px;height:6px;border-radius:50%;background:currentColor;flex-shrink:0;transition:background .18s}
.log-toggle:hover{color:var(--text);border-color:#334}
.log-toggle.on{background:rgba(34,197,94,.1);border-color:rgba(34,197,94,.45);color:#22c55e}
.tf-btn{padding:3px 9px;border-radius:5px;border:1px solid var(--border);background:var(--surface);color:var(--muted);cursor:pointer;font-size:.7rem;font-family:inherit;transition:all .15s}
.tf-btn:hover{color:var(--text);border-color:#334}
.tf-btn.tf-active{background:rgba(6,182,212,.12);border-color:rgba(6,182,212,.5);color:#06b6d4}
.log-gate{position:relative;min-height:60px}
.log-gate-blur{transition:filter .35s,opacity .35s}
.log-gate-blur.off{filter:blur(5px);opacity:.18;pointer-events:none;user-select:none}
.log-gate-btn{position:absolute;inset:0;display:none;align-items:center;justify-content:center;z-index:3;pointer-events:none}
.log-gate-btn.show{display:flex;pointer-events:auto}
.log-on-btn{display:inline-flex;align-items:center;gap:8px;padding:9px 22px;border-radius:10px;
  background:rgba(8,15,26,.8);border:1px solid rgba(34,197,94,.55);color:#22c55e;
  font-size:.8rem;cursor:pointer;font-family:inherit;backdrop-filter:blur(6px);
  -webkit-backdrop-filter:blur(6px);transition:background .15s;white-space:nowrap}
.log-on-btn:hover{background:rgba(34,197,94,.15)}
.online-user-row{display:flex;align-items:center;gap:10px;padding:9px 14px;border-radius:9px;
  background:var(--card);border:1px solid var(--border);margin-bottom:6px;
  color:var(--text);text-decoration:none;font-size:.8rem;transition:background .12s,border-color .12s}
.online-user-row:hover{background:rgba(6,182,212,.08);border-color:#0e4f6e}
.online-pulse{width:8px;height:8px;border-radius:50%;background:#22c55e;flex-shrink:0;
  box-shadow:0 0 0 2px rgba(34,197,94,.25);animation:opluse 2s ease-in-out infinite}
@keyframes opluse{0%,100%{box-shadow:0 0 0 2px rgba(34,197,94,.25)}50%{box-shadow:0 0 0 5px rgba(34,197,94,.08)}}

.pagination{display:flex;align-items:center;gap:5px;justify-content:center;
  padding:12px 14px;border-top:1px solid var(--border)}
.pg-btn{min-width:31px;height:31px;padding:0 6px;border-radius:7px;
  border:1px solid var(--border);background:var(--surface);color:var(--muted);
  font-size:.77rem;cursor:pointer;transition:all .15s;
  display:inline-flex;align-items:center;justify-content:center}
.pg-btn:hover:not(:disabled):not(.active){border-color:var(--blue);color:var(--blue)}
.pg-btn.active{background:#1a2f5e;color:var(--blue);border-color:#2a4580}
.pg-btn:disabled{opacity:.3;cursor:default}
.pg-gap{color:var(--muted);font-size:.77rem;padding:0 2px}

.log-wrap{background:var(--card);border:1px solid var(--border);border-radius:13px;overflow:hidden}
.log-row{display:flex;gap:14px;padding:10px 16px;border-top:1px solid var(--border);align-items:flex-start}
.log-row:first-child{border-top:none}
.log-ts{color:var(--muted);font-size:.69rem;white-space:nowrap;min-width:120px;padding-top:1px}
.log-reason{font-size:.76rem;color:var(--text);line-height:1.5;word-break:break-word}

.empty{padding:28px;text-align:center;color:var(--muted);font-size:.81rem}

.tip{display:inline-flex;align-items:center;justify-content:center;
  width:14px;height:14px;border-radius:50%;background:#162030;
  color:var(--muted);font-size:.6rem;font-weight:700;cursor:help;
  vertical-align:middle;margin-left:5px;border:1px solid var(--border)}

.modal{background:var(--card);border:1px solid var(--border);border-radius:14px;
  padding:26px;max-width:640px}
.form-group{margin-bottom:13px}
.form-group label{display:flex;align-items:center;font-size:.69rem;color:var(--muted);
  text-transform:uppercase;letter-spacing:.6px;margin-bottom:6px}
.form-group input,.form-group select{width:100%;background:var(--surface);
  border:1px solid var(--border);border-radius:8px;padding:9px 11px;
  color:var(--text);font-size:.85rem;outline:none;transition:border .15s}
.form-group input:focus,.form-group select:focus{border-color:var(--blue)}
.form-row{display:grid;grid-template-columns:1fr 1fr;gap:11px}

.toast{position:fixed;bottom:20px;right:20px;z-index:400;
  background:#1a2f5e;color:var(--blue);border:1px solid #2a4580;
  border-radius:9px;padding:11px 17px;font-size:.81rem;
  transform:translateY(80px);opacity:0;transition:all .3s;pointer-events:none}
.toast.show{transform:translateY(0);opacity:1}
.toast.err{background:#3b0f0f;color:var(--red);border-color:#6b1a1a}

.auth-wrap{min-height:100vh;display:flex;align-items:center;justify-content:center}
.auth-box{background:var(--card);border:1px solid var(--border);
  border-radius:16px;padding:38px 34px;width:360px}
.auth-logo{display:flex;align-items:center;gap:10px;font-weight:700;
  font-size:1.02rem;color:var(--blue);margin-bottom:26px;justify-content:center}
.auth-sub{text-align:center;font-size:.76rem;color:var(--muted);
  margin-bottom:22px;margin-top:-18px}
.err-msg{background:#3b0f0f;border:1px solid #6b1a1a;color:#f04a4a;
  padding:9px 13px;border-radius:7px;font-size:.79rem;margin-bottom:14px}
.submit-btn{width:100%;padding:11px;background:var(--blue);color:#fff;border:none;
  border-radius:9px;font-size:.88rem;font-weight:600;cursor:pointer;
  margin-top:6px;transition:all .2s cubic-bezier(.4,0,.2,1);
  box-shadow:0 2px 8px rgba(79,142,247,.3)}
.submit-btn:hover{background:#3b7ce0;transform:translateY(-1px);box-shadow:0 6px 20px rgba(79,142,247,.4)}
.submit-btn:active{transform:translateY(0);box-shadow:0 2px 8px rgba(79,142,247,.3)}

.server-row{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:9px;margin-bottom:9px}
.srv-bar{height:4px;background:#1a2840;border-radius:99px;overflow:hidden;margin-top:6px}
.srv-fill{height:4px;border-radius:99px}
.srv-box{cursor:pointer;transition:border-color .15s,background .15s}
.srv-box:hover{border-color:var(--blue)}
.srv-box.active{border-color:var(--blue);background:#0c1a30}

.chart-card{background:var(--card);border:1px solid var(--border);border-radius:13px;padding:17px;margin-bottom:22px}

::-webkit-scrollbar{width:5px;height:5px}
::-webkit-scrollbar-track{background:var(--bg)}
::-webkit-scrollbar-thumb{background:var(--border);border-radius:3px}

@media(max-width:900px){
  .kpi-row{grid-template-columns:repeat(4,minmax(0,1fr))}
  .server-row{grid-template-columns:repeat(3,minmax(0,1fr))}
  .kpi-row .kpi:last-child{grid-column:span 2}
  .server-row .stat-box:last-child{grid-column:span 2}
}
@media(max-width:680px){
  .server-row{grid-template-columns:repeat(2,minmax(0,1fr))}
  .wrap{padding:12px}
  .topbar{padding:0 10px;gap:6px}
  .logo-text{display:none}
  .clock{display:none}
  .kpi-row{grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}
  .kpi{padding:11px 10px;gap:8px}
  .kpi-icon{width:36px;height:36px;border-radius:9px}
  .kpi-body .val{font-size:1.1rem}
  .kpi-row .kpi:last-child{grid-column:1/-1}
  .server-row .stat-box:last-child{grid-column:1/-1}
  .search{width:145px}
  .form-row{grid-template-columns:1fr}
  .auth-box{width:calc(100vw - 28px);padding:26px 18px}
  .col-desktop{display:none}
  table{min-width:unset}
  td{padding:8px 7px;font-size:.75rem;white-space:nowrap}
  td:nth-child(2){max-width:140px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .prog-pct{font-size:.62rem}
  .prog-bg{width:46px}
}
@media(max-width:400px){
  .refresh-badge{display:none}
  .chip{font-size:.69rem;padding:4px 8px}
}
</style>
"""

COMMON_JS = r"""
<script>
function toggleSB(){
  document.getElementById('sidebar').classList.toggle('open');
  document.getElementById('sb-bd').classList.toggle('open');
}
function closeSB(){
  document.getElementById('sidebar').classList.remove('open');
  document.getElementById('sb-bd').classList.remove('open');
}
document.addEventListener('keydown', e => { if(e.key==='Escape') closeSB(); });
function toast(msg, err=false){
  const t=document.getElementById('toast');
  if(!t)return;
  t.textContent=msg; t.className='toast'+(err?' err':'');
  t.classList.add('show');
  setTimeout(()=>t.classList.remove('show'),3000);
}
</script>
<div id="toast" class="toast"></div>
"""

_NAV = [
    ("dashboard", "/",         "Dashboard",
     '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg>'),
    ("settings",  "/settings", "Settings",
     '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>'),
]

def _refresh_select(current):
    opts = [("5","5s"), ("10","10s"), ("30","30s"), ("60","1m"), ("300","5m")]
    c = str(current)
    inner = "".join(
        f'<option value="{v}"{" selected" if v==c else ""}>{l}</option>'
        for v, l in opts
    )
    return (
        f'<select id="refresh-sel" title="Auto-refresh interval" '
        f'onchange="setRefreshInterval(this.value)" '
        f'style="background:none;border:none;color:var(--muted);font-size:.7rem;'
        f'cursor:pointer;outline:none;margin-left:3px;max-width:38px">'
        f'{inner}</select>'
    )

def topbar(extra="", page="dashboard", refresh_sel="", username=""):
    nav_html = ""
    for key, href, label, icon in _NAV:
        cls = ' on' if page == key else ''
        view = 'main' if key == 'dashboard' else ''
        dv = f' data-view="{view}"' if view else ''
        nav_html += f'<a href="{href}" class="sb-link{cls}"{dv} onclick="closeSB()">{icon}&nbsp;&nbsp;{label}</a>\n'

    panels_html = """  <div class="sb-section">Charts &amp; Logs</div>
  <a href="/?v=traffic"  class="sb-link" data-view="traffic"  onclick="closeSB()"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="3" width="4" height="18" rx="1"/><rect x="9" y="8" width="4" height="13" rx="1"/><rect x="16" y="13" width="4" height="8" rx="1"/></svg>&nbsp;&nbsp;Traffic (24h)</a>
  <a href="/?v=online"   class="sb-link" data-view="online"   onclick="closeSB()"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/></svg>&nbsp;&nbsp;Online History</a>
  <a href="/?v=restarts" class="sb-link" data-view="restarts" onclick="closeSB()"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>&nbsp;&nbsp;Restart Log</a>"""

    user_row = f"""  <div class="sb-user">
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="8" r="4"/><path d="M4 20c0-4 3.6-7 8-7s8 3 8 7"/></svg>
    <span class="sb-username">{username}</span>
  </div>""" if username else ""

    return f"""
<div class="sb-backdrop" id="sb-bd" onclick="closeSB()"></div>
<aside class="sidebar" id="sidebar">
  <div class="sb-hd">
    <div class="sb-logo">
      <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>
      3x-ui Monitor
    </div>
    <button class="sb-x" onclick="closeSB()">
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
    </button>
  </div>
  <div class="sb-section">Navigation</div>
  {nav_html}
  {panels_html}
  <div class="sb-footer">
    {user_row}
    <a href="/logout" class="sb-logout">
      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>
      Log out
    </a>
  </div>
</aside>

<div class="topbar">
  <button class="hamburger" onclick="toggleSB()" title="Menu">
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
      <line x1="3" y1="6"  x2="21" y2="6"/>
      <line x1="3" y1="12" x2="21" y2="12"/>
      <line x1="3" y1="18" x2="21" y2="18"/>
    </svg>
  </button>
  <a href="/" class="logo">
    <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>
    <span class="logo-text">3x-ui Monitor</span>
  </a>
  {extra}
  <div class="topbar-right">
    <div class="refresh-badge" style="cursor:pointer" title="Click to refresh now">
      <span onclick="refresh();_countdown=_refreshSec;document.getElementById('countdown').textContent=_refreshSec" style="display:flex;align-items:center;gap:3px">
      <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>
      <span id="countdown">–</span>s</span>
      {refresh_sel}
    </div>
    <span class="clock" id="clock">--:--:--</span>
  </div>
</div>

"""

REGISTER_HTML = """<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Setup — 3x-ui Monitor</title>
__STYLE__</head><body>
<div class="auth-wrap"><div class="auth-box">
  <div class="auth-logo">
    <svg width="21" height="21" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>
    3x-ui Monitor
  </div>
  <div class="auth-sub">First run — create your admin account</div>
  {% if error %}<div class="err-msg">{{ error }}</div>{% endif %}
  <form method="post">
    <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
    <div class="form-group"><label>Username</label>
      <input name="username" type="text" autocomplete="username" autofocus value="{{ username or '' }}"></div>
    <div class="form-group pw-field"><label>Password</label>
      <input id="reg-pw" name="password" type="password" autocomplete="new-password">
      <button type="button" class="pw-eye" onclick="togglePw('reg-pw',this)" tabindex="-1"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg></button></div>
    <div class="form-group pw-field"><label>Confirm Password</label>
      <input id="reg-pw2" name="password2" type="password" autocomplete="new-password">
      <button type="button" class="pw-eye" onclick="togglePw('reg-pw2',this)" tabindex="-1"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg></button></div>
    <button class="submit-btn" type="submit">Create Account</button>
  </form>
<style>.pw-field{position:relative}.pw-field input{padding-right:36px!important}
.pw-eye{position:absolute;right:8px;bottom:9px;background:none;border:none;cursor:pointer;color:var(--muted);opacity:.45;padding:2px;line-height:0;transition:color .15s,opacity .15s}
.pw-eye:hover{color:var(--text);opacity:.8}</style>
<script>function togglePw(id,btn){const i=document.getElementById(id);i.type=i.type==='password'?'text':'password';btn.style.opacity=i.type==='text'?'1':'.45';}</script>
</div></div></body></html>"""

LOGIN_HTML = """<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Login — 3x-ui Monitor</title>
__STYLE__</head><body>
<div class="auth-wrap"><div class="auth-box">
  <div class="auth-logo">
    <svg width="21" height="21" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>
    3x-ui Monitor
  </div>
  {% if error %}<div class="err-msg">{{ error }}</div>{% endif %}
  <form method="post">
    <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
    <div class="form-group"><label>Username</label>
      <input name="username" type="text" autocomplete="username" autofocus></div>
    <div class="form-group pw-field"><label>Password</label>
      <input id="login-pw" name="password" type="password" autocomplete="current-password" onkeydown="if(event.key==='Enter'){event.preventDefault();document.getElementById('login-btn').click()}">
      <button type="button" class="pw-eye" onclick="togglePw('login-pw',this)" tabindex="-1"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg></button></div>
    <button class="submit-btn" id="login-btn" type="submit">Sign in</button>
  </form>
<style>.pw-field{position:relative}.pw-field input{padding-right:36px!important}
.pw-eye{position:absolute;right:8px;bottom:9px;background:none;border:none;cursor:pointer;color:var(--muted);opacity:.45;padding:2px;line-height:0;transition:color .15s,opacity .15s}
.pw-eye:hover{color:var(--text);opacity:.8}
@keyframes _spin{to{transform:rotate(360deg)}}
.btn-spin{display:inline-block;width:14px;height:14px;border:2px solid rgba(255,255,255,.3);
  border-top-color:#fff;border-radius:50%;animation:_spin .65s linear infinite;vertical-align:middle}</style>
<script>
function togglePw(id,btn){const i=document.getElementById(id);i.type=i.type==='password'?'text':'password';btn.style.opacity=i.type==='text'?'1':'.45';}
document.querySelector('form').addEventListener('submit',function(){
  const b=document.getElementById('login-btn');
  b.innerHTML='<span class="btn-spin"></span>';
  b.disabled=true;
});
</script>
</div></div></body></html>"""

MAIN_HTML = r"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>3x-ui Monitor</title>
__BASE_STYLE__
<script src="/static/chart.min.js"></script>
</head>
<body>
__TOPBAR__
<style>
html,body{height:100%;overflow:hidden}
.wrap{max-width:100%;margin:0;padding:0;height:calc(100vh - 52px);display:flex;flex-direction:column;overflow:hidden}
.view-panel{display:none;flex:1;flex-direction:column;overflow:hidden}
.view-panel.active{display:flex}
.main-top{padding:12px 22px 0;flex-shrink:0}
.users-section{flex:1;overflow:hidden;display:flex;flex-direction:column;padding:0 22px 6px}
.tbl-wrap{flex:1;overflow:hidden;display:flex;flex-direction:column}
.tbl-scroll{flex:1;overflow-y:auto;overflow-x:auto;-webkit-overflow-scrolling:touch}
.pagination{flex-shrink:0}
.side-view{flex:1;overflow-y:auto;padding:18px 22px}
@media(max-width:768px){
  html,body{height:auto;overflow:auto}
  .wrap{height:auto;overflow:visible}
  .view-panel.active{display:block}
  .users-section{flex:none;overflow:visible;padding:0 12px 16px}
  .tbl-wrap{flex:none;overflow:visible;display:block}
  .tbl-scroll{flex:none;overflow-y:visible;overflow-x:auto}
  .pagination{flex-shrink:unset}
  .side-view{flex:none;overflow-y:visible;padding:14px 12px}
}
.panel-hd{padding:10px 22px;flex-shrink:0;display:flex;align-items:center;gap:12px;
  border-bottom:1px solid var(--border);background:var(--card)}
.panel-hd-title{font-size:.76rem;font-weight:600;text-transform:uppercase;letter-spacing:.6px;color:var(--muted)}
.back-btn{background:var(--surface);border:1px solid var(--border);color:var(--muted);
  padding:4px 10px;border-radius:6px;cursor:pointer;font-size:.74rem;font-family:inherit;
  display:flex;align-items:center;gap:5px;transition:color .12s}
.back-btn:hover{color:var(--text)}
</style>
<div class="wrap">

  <div id="view-main" class="view-panel active">
    <div class="main-top">
      <div class="kpi-row" id="kpis">
        <div class="kpi"><span class="skel" style="width:38px;height:38px;border-radius:9px;flex-shrink:0"></span><div style="flex:1;display:flex;flex-direction:column;gap:7px"><span class="skel" style="height:22px;width:65%"></span><span class="skel" style="height:10px;width:45%"></span></div></div>
        <div class="kpi"><span class="skel" style="width:38px;height:38px;border-radius:9px;flex-shrink:0"></span><div style="flex:1;display:flex;flex-direction:column;gap:7px"><span class="skel" style="height:22px;width:65%"></span><span class="skel" style="height:10px;width:45%"></span></div></div>
        <div class="kpi"><span class="skel" style="width:38px;height:38px;border-radius:9px;flex-shrink:0"></span><div style="flex:1;display:flex;flex-direction:column;gap:7px"><span class="skel" style="height:22px;width:65%"></span><span class="skel" style="height:10px;width:45%"></span></div></div>
        <div class="kpi"><span class="skel" style="width:38px;height:38px;border-radius:9px;flex-shrink:0"></span><div style="flex:1;display:flex;flex-direction:column;gap:7px"><span class="skel" style="height:22px;width:65%"></span><span class="skel" style="height:10px;width:45%"></span></div></div>
        <div class="kpi"><span class="skel" style="width:38px;height:38px;border-radius:9px;flex-shrink:0"></span><div style="flex:1;display:flex;flex-direction:column;gap:7px"><span class="skel" style="height:22px;width:65%"></span><span class="skel" style="height:10px;width:45%"></span></div></div>
        <div class="kpi"><span class="skel" style="width:38px;height:38px;border-radius:9px;flex-shrink:0"></span><div style="flex:1;display:flex;flex-direction:column;gap:7px"><span class="skel" style="height:22px;width:65%"></span><span class="skel" style="height:10px;width:45%"></span></div></div>
        <div class="kpi"><span class="skel" style="width:38px;height:38px;border-radius:9px;flex-shrink:0"></span><div style="flex:1;display:flex;flex-direction:column;gap:7px"><span class="skel" style="height:22px;width:65%"></span><span class="skel" style="height:10px;width:45%"></span></div></div>
      </div>
      <div class="sec"><div class="sec-line"></div><span class="sec-title">Server</span><div class="sec-line"></div></div>
      <div class="server-row" id="server-stats">
        <div class="stat-box srv-box" onclick="toggleSrvChart('cpu','CPU Usage')"><div class="sv">—</div><div class="sl">CPU</div></div>
        <div class="stat-box srv-box" onclick="toggleSrvChart('ram','RAM Usage')"><div class="sv">—</div><div class="sl">RAM</div></div>
        <div class="stat-box srv-box" onclick="toggleSrvChart('disk','Disk Usage')"><div class="sv">—</div><div class="sl">Disk</div></div>
        <div class="stat-box"><div class="sv">—</div><div class="sl">Uptime / Xray</div></div>
        <div class="stat-box srv-box" id="bw-srv" onclick="toggleSrvChart('net','Bandwidth')"><div class="sv">—</div><div class="sl">Bandwidth</div></div>
      </div>
      <div id="srv-chart-panel" style="display:none;margin-bottom:10px;margin-top:10px">
        <div class="chart-card" style="margin-bottom:0">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
            <span style="font-size:.7rem;color:var(--muted);text-transform:uppercase;letter-spacing:.5px" id="srv-chart-title"></span>
            <button onclick="closeSrvChart()" style="background:none;border:none;color:var(--muted);cursor:pointer;font-size:1rem;line-height:1">✕</button>
          </div>
          <div id="srv-chart-avg" style="display:none;font-size:.72rem;margin-bottom:8px;color:var(--muted)"></div>
          <canvas id="srv-chart" style="max-height:160px"></canvas>
        </div>
      </div>
      <div class="sec" style="margin-top:4px"><div class="sec-line"></div><span class="sec-title">Users</span><div class="sec-line"></div></div>
    </div>

    <div class="users-section">
      <div class="toolbar">
        <div class="search-wrap">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>
          <input class="search" id="search" placeholder="Search users…" oninput="_page=1;loadPage()">
        </div>
        <button class="chip active" data-f="all"     onclick="setFilter(this)">All</button>
        <button class="chip"        data-f="online"  onclick="setFilter(this)">Online</button>
        <button class="chip"        data-f="over"    onclick="setFilter(this)">Over quota</button>
        <button class="chip"        data-f="expired" onclick="setFilter(this)">Expired</button>
        <button class="chip"        data-f="handled" onclick="setFilter(this)">Blocked</button>
        <button class="chip"        data-f="ok"      onclick="setFilter(this)">Active</button>
        <div style="margin-left:auto;display:flex;align-items:center;gap:8px">
          <span style="font-size:.7rem;color:var(--muted)" id="row-count"></span>
          <span style="font-size:.7rem;color:var(--muted)">Show:</span>
          <select class="pg-size-sel" id="pg-sel" onchange="_pageSize=+this.value;_page=1;loadPage()">
            __PAGE_SIZE_OPTS__
          </select>
        </div>
      </div>
      <div class="tbl-wrap">
        <div class="tbl-scroll">
          <table>
            <thead><tr>
              <th>#</th>
              <th class="sortable" onclick="sortBy('email')">Email<span class="sort-arrow" id="sa-email"></span></th>
              <th class="sortable col-desktop" onclick="sortBy('total')">Used<span class="sort-arrow" id="sa-total"></span></th>
              <th class="sortable col-desktop" onclick="sortBy('quota')">Quota<span class="sort-arrow" id="sa-quota"></span></th>
              <th class="sortable" onclick="sortBy('pct')">Usage<span class="sort-arrow" id="sa-pct"></span></th>
              <th class="col-desktop">Upload</th><th class="col-desktop">Download</th><th>Status</th>
            </tr></thead>
            <tbody id="tbody">
              <tr><td><span class="skel" style="width:18px;height:12px"></span></td><td><span class="skel" style="width:140px;height:12px"></span></td><td class="col-desktop"><span class="skel" style="width:55px;height:12px"></span></td><td class="col-desktop"><span class="skel" style="width:55px;height:12px"></span></td><td><span class="skel" style="width:80px;height:8px;border-radius:99px"></span></td><td class="col-desktop"><span class="skel" style="width:45px;height:12px"></span></td><td class="col-desktop"><span class="skel" style="width:45px;height:12px"></span></td><td><span class="skel" style="width:52px;height:18px;border-radius:99px"></span></td></tr>
              <tr><td><span class="skel" style="width:18px;height:12px"></span></td><td><span class="skel" style="width:110px;height:12px"></span></td><td class="col-desktop"><span class="skel" style="width:55px;height:12px"></span></td><td class="col-desktop"><span class="skel" style="width:55px;height:12px"></span></td><td><span class="skel" style="width:80px;height:8px;border-radius:99px"></span></td><td class="col-desktop"><span class="skel" style="width:45px;height:12px"></span></td><td class="col-desktop"><span class="skel" style="width:45px;height:12px"></span></td><td><span class="skel" style="width:52px;height:18px;border-radius:99px"></span></td></tr>
              <tr><td><span class="skel" style="width:18px;height:12px"></span></td><td><span class="skel" style="width:160px;height:12px"></span></td><td class="col-desktop"><span class="skel" style="width:55px;height:12px"></span></td><td class="col-desktop"><span class="skel" style="width:55px;height:12px"></span></td><td><span class="skel" style="width:80px;height:8px;border-radius:99px"></span></td><td class="col-desktop"><span class="skel" style="width:45px;height:12px"></span></td><td class="col-desktop"><span class="skel" style="width:45px;height:12px"></span></td><td><span class="skel" style="width:52px;height:18px;border-radius:99px"></span></td></tr>
              <tr><td><span class="skel" style="width:18px;height:12px"></span></td><td><span class="skel" style="width:125px;height:12px"></span></td><td class="col-desktop"><span class="skel" style="width:55px;height:12px"></span></td><td class="col-desktop"><span class="skel" style="width:55px;height:12px"></span></td><td><span class="skel" style="width:80px;height:8px;border-radius:99px"></span></td><td class="col-desktop"><span class="skel" style="width:45px;height:12px"></span></td><td class="col-desktop"><span class="skel" style="width:45px;height:12px"></span></td><td><span class="skel" style="width:52px;height:18px;border-radius:99px"></span></td></tr>
              <tr><td><span class="skel" style="width:18px;height:12px"></span></td><td><span class="skel" style="width:150px;height:12px"></span></td><td class="col-desktop"><span class="skel" style="width:55px;height:12px"></span></td><td class="col-desktop"><span class="skel" style="width:55px;height:12px"></span></td><td><span class="skel" style="width:80px;height:8px;border-radius:99px"></span></td><td class="col-desktop"><span class="skel" style="width:45px;height:12px"></span></td><td class="col-desktop"><span class="skel" style="width:45px;height:12px"></span></td><td><span class="skel" style="width:52px;height:18px;border-radius:99px"></span></td></tr>
            </tbody>
          </table>
        </div>
        <div class="pagination" id="pagination"></div>
      </div>
    </div>
  </div>

  <div id="view-traffic" class="view-panel">
    <div class="panel-hd">
      <button class="back-btn" onclick="openView('main')">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="15 18 9 12 15 6"/></svg>Back
      </button>
      <span class="panel-hd-title">Traffic</span>
      <button class="log-toggle" id="toggle-traffic" onclick="toggleLogging('traffic_log_enabled','toggle-traffic')">
        <span class="ldot"></span>Logging
      </button>
      <div style="display:flex;gap:6px;margin-left:auto;align-items:center;flex-wrap:wrap">
        <button class="chip active" data-h="24"  onclick="setTRange(this)">24h</button>
        <button class="chip"        data-h="168" onclick="setTRange(this)">7d</button>
        <button class="chip"        data-h="720" onclick="setTRange(this)">30d</button>
        <button class="chip" onclick="exportTrafficCSV()" style="gap:4px">
          <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>CSV
        </button>
      </div>
    </div>
    <div class="side-view">
      <div class="chart-card" style="margin-bottom:14px">
        <canvas id="traffic-chart" style="max-height:240px"></canvas>
      </div>
      <div class="log-gate" id="gate-traffic">
        <div class="log-gate-blur" id="gate-traffic-blur">
          <div id="traffic-stats" style="display:flex;gap:8px;margin:12px 0;flex-wrap:wrap">
            <div class="stat-box" style="padding:8px 12px"><div class="skel" style="height:18px;width:70px;margin-bottom:7px"></div><div class="skel" style="height:10px;width:50px"></div></div>
            <div class="stat-box" style="padding:8px 12px"><div class="skel" style="height:18px;width:70px;margin-bottom:7px"></div><div class="skel" style="height:10px;width:50px"></div></div>
            <div class="stat-box" style="padding:8px 12px"><div class="skel" style="height:18px;width:70px;margin-bottom:7px"></div><div class="skel" style="height:10px;width:50px"></div></div>
          </div>
          <div class="sec" style="margin-bottom:14px"><div class="sec-line"></div><span class="sec-title">Top Users</span><div class="sec-line"></div></div>
          <div class="chart-card" style="margin-bottom:18px">
            <canvas id="user-chart" style="max-height:260px"></canvas>
          </div>
          <div class="tbl-wrap" style="margin-bottom:24px">
            <div class="tbl-scroll" style="max-height:unset;overflow:visible">
              <table id="top-users-tbl">
                <thead><tr><th>#</th><th>Email</th><th>Traffic</th><th>Share</th></tr></thead>
                <tbody id="top-users-tbody"><tr><td colspan="4" style="text-align:center;color:var(--muted);padding:18px">Loading…</td></tr></tbody>
              </table>
            </div>
          </div>
        </div>
        <div class="log-gate-btn" id="gate-traffic-btn">
          <button class="log-on-btn" onclick="toggleLogging('traffic_log_enabled','toggle-traffic')">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="22 12 16 12 13 21 10 3 7 12 2 12"/></svg>Enable Logging
          </button>
        </div>
      </div>
    </div>
  </div>

  <div id="view-online" class="view-panel">
    <div class="panel-hd">
      <button class="back-btn" onclick="openView('main')">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="15 18 9 12 15 6"/></svg>Back
      </button>
      <span class="panel-hd-title">Online Users (24h)</span>
      <button class="log-toggle" id="toggle-online" style="margin-left:auto" onclick="toggleLogging('online_log_enabled','toggle-online')">
        <span class="ldot"></span>Logging
      </button>
    </div>
    <div class="side-view">
      <div class="chart-card" style="margin-bottom:14px">
        <div style="display:flex;justify-content:flex-end;gap:4px;margin-bottom:8px">
          <button onclick="setOnlineRange(6)"   id="obtn-6"   class="tf-btn">6h</button>
          <button onclick="setOnlineRange(24)"  id="obtn-24"  class="tf-btn tf-active">24h</button>
          <button onclick="setOnlineRange(72)"  id="obtn-72"  class="tf-btn">3d</button>
          <button onclick="setOnlineRange(168)" id="obtn-168" class="tf-btn">7d</button>
        </div>
        <canvas id="online-chart" style="max-height:280px"></canvas>
      </div>
      <div class="log-gate" id="gate-online">
        <div class="log-gate-blur" id="gate-online-blur">
          <div id="online-stats" style="display:flex;gap:8px;margin:12px 0;flex-wrap:wrap">
            <div class="stat-box" style="padding:8px 12px"><div class="skel" style="height:18px;width:50px;margin-bottom:7px"></div><div class="skel" style="height:10px;width:40px"></div></div>
            <div class="stat-box" style="padding:8px 12px"><div class="skel" style="height:18px;width:50px;margin-bottom:7px"></div><div class="skel" style="height:10px;width:40px"></div></div>
          </div>
          <div class="sec" style="margin:0 0 12px"><div class="sec-line"></div><span class="sec-title">Now Online</span><div class="sec-line"></div></div>
          <div id="online-users-list">
            <div class="stat-box" style="padding:10px 14px;margin-bottom:6px"><div class="skel" style="height:14px;width:60%"></div></div>
            <div class="stat-box" style="padding:10px 14px;margin-bottom:6px"><div class="skel" style="height:14px;width:45%"></div></div>
          </div>
        </div>
        <div class="log-gate-btn" id="gate-online-btn">
          <button class="log-on-btn" onclick="toggleLogging('online_log_enabled','toggle-online')">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="22 12 16 12 13 21 10 3 7 12 2 12"/></svg>Enable Logging
          </button>
        </div>
      </div>
    </div>
  </div>

  <div id="view-restarts" class="view-panel">
    <div class="panel-hd">
      <button class="back-btn" onclick="openView('main')">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="15 18 9 12 15 6"/></svg>Back
      </button>
      <span class="panel-hd-title">Restart Log</span>
    </div>
    <div class="side-view">
      <div class="log-wrap" id="restarts"></div>
    </div>
  </div>

</div>
__COMMON_JS__
<script>
const _csrf="__CSRF_TOKEN__";
(()=>{const _f=window.fetch;window.fetch=(u,o={})=>{if(o.method&&o.method.toUpperCase()!=='GET'){o.headers={...(o.headers||{}),'X-CSRF-Token':_csrf};}return _f(u,o);};})();
let _refreshSec = __REFRESH_SEC__;
const CLIENT_TZ  = "__TZ__";
const GB=1073741824, MB=1048576;
const fmt=n=>{if(!n||n<0)return'0 B';if(n>=GB)return(n/GB).toFixed(2)+' GB';if(n>=MB)return(n/MB).toFixed(1)+' MB';return(n/1024).toFixed(0)+' KB';};
const pct=(t,q)=>q>0?t/q*100:0;
const barColor=p=>p>=110?'#f04a4a':p>=90?'#f59e0b':'#4f8ef7';

let _summary={total:0,active:0,over:0,expired:0,blocked:0,total_bytes:0,total_quota:0,online:0};
let _online={count:0,emails:[]};
let _filter='all', _sort={col:null,dir:-1}, _page=1;
let _pageSize=__PAGE_SIZE__;
let _countdown=_refreshSec;
let _lastRestarts=[];

function openView(name){
  document.querySelectorAll('.view-panel').forEach(p=>p.classList.remove('active'));
  document.getElementById('view-'+name).classList.add('active');
  document.querySelectorAll('.sb-link[data-view]').forEach(a=>a.classList.remove('on'));
  const lnk=document.querySelector(`.sb-link[data-view="${name}"]`);
  if(lnk)lnk.classList.add('on');
  if(name==='traffic') refreshTrafficChart();
  else if(name==='online') setTimeout(loadOnlineChart,0);
  else if(name==='restarts') renderRestarts(_lastRestarts);
}

setInterval(()=>{
  const el=document.getElementById('clock');
  if(el) el.textContent=new Date().toLocaleTimeString('en-GB',{timeZone:CLIENT_TZ,hour12:false})+' (IR)';
},1000);

setInterval(()=>{
  const cd=document.getElementById('countdown');
  _countdown--;
  if(cd)cd.textContent=_countdown;
  if(_countdown<=0){_countdown=_refreshSec;refresh();}
},1000);

function setRefreshInterval(val){
  _refreshSec=Math.max(5,+val);
  _countdown=_refreshSec;
  const cd=document.getElementById('countdown');
  if(cd)cd.textContent=_refreshSec;
  fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({dashboard_refresh:String(_refreshSec)})});
}

function setFilter(btn){
  document.querySelectorAll('.chip[data-f]').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active'); _filter=btn.dataset.f; _page=1; loadPage();
}
function sortBy(col){
  if(_sort.col===col)_sort.dir*=-1; else{_sort.col=col;_sort.dir=-1;}
  _page=1; loadPage();
}
function updateArrows(){
  ['email','total','quota','pct'].forEach(c=>{
    const el=document.getElementById('sa-'+c);
    if(el) el.textContent=_sort.col===c?(_sort.dir>0?'↑':'↓'):'';
  });
}

function renderKPI(){
  const s=_summary;
  document.getElementById('kpis').innerHTML=[
    {icon:'<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#4f8ef7" stroke-width="2"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/></svg>',bg:'#0e1e3a',val:s.total,lbl:'Total Users',c:''},
    {icon:'<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#06b6d4" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M8.56 2.75c4.37 6.03 6.02 9.42 8.03 17.72m2.54-15.38c-3.72 4.35-8.94 5.66-16.88 5.85m19.5 1.9c-3.5-.93-6.63-.82-8.94 0-2.58.92-5.01 2.86-7.44 6.32"/></svg>',bg:'#062030',val:s.online,lbl:'Online',c:'#06b6d4'},
    {icon:'<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#22c55e" stroke-width="2"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>',bg:'#052e16',val:s.active,lbl:'Active',c:'#22c55e'},
    {icon:'<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#f04a4a" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>',bg:'#3b0f0f',val:s.over,lbl:'Limit',c:'#f04a4a'},
    {icon:'<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#f59e0b" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>',bg:'#2d1e00',val:s.expired,lbl:'Expired',c:'#f59e0b'},
    {icon:'<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#a78bfa" stroke-width="2"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>',bg:'#1e1438',val:s.blocked,lbl:'Blocked',c:'#a78bfa'},
    {icon:'<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#22c55e" stroke-width="2"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>',bg:'#052e16',val:fmt(s.total_bytes)+(s.total_quota>0?`<span style="font-size:.7rem;color:var(--muted);font-weight:400;margin-left:3px">/ ${fmt(s.total_quota)}</span>`:''),lbl:'Total Traffic',c:'#22c55e'},
  ].map(k=>`<div class="kpi"><div class="kpi-icon" style="background:${k.bg}">${k.icon}</div><div class="kpi-body"><div class="val" style="color:${k.c||'var(--text)'}">${k.val}</div><div class="lbl">${k.lbl}</div></div></div>`).join('');
}

async function loadPage(){
  const search=document.getElementById('search')?.value||'';
  const sortParam=_sort.col||'default';
  const orderParam=_sort.dir>0?'asc':'desc';
  const url=`/api/users?page=${_page}&per_page=${_pageSize}&filter=${_filter}&search=${encodeURIComponent(search)}&sort=${sortParam}&order=${orderParam}`;
  try{
    const {users,total,pages,page}=await fetch(url).then(r=>r.json());
    _page=page;
    const start=(page-1)*_pageSize;
    document.getElementById('row-count').textContent=total?`${start+1}–${Math.min(start+_pageSize,total)} of ${total}`:'0 users';
    updateArrows();
    const tbody=document.getElementById('tbody');
    if(!users.length){tbody.innerHTML='<tr><td colspan="8" class="empty">No users found</td></tr>';renderPagination(1,1);return;}
    tbody.innerHTML=users.map((u,i)=>{
      const p=pct(u.total,u.quota),over=u.quota>0&&u.total>u.quota;
      const bc=barColor(p);
      const bar=u.quota>0
        ?`<div class="prog-wrap"><span class="prog-pct">${p.toFixed(0)}%</span><div class="prog-bg"><div class="prog-fill" style="width:${Math.min(p,100).toFixed(1)}%;background:${bc}"></div></div></div>`
        :'<span style="color:var(--muted)">—</span>';
      const badge=!u.enable?'<span class="badge b-off">Disabled</span>'
        :u.handled?'<span class="badge b-handle">Blocked</span>'
        :over?'<span class="badge b-over">Limit</span>'
        :u.expired?'<span class="badge b-exp">Expired</span>'
        :u.online?'<span class="badge b-online">Online</span>'
        :'<span class="badge b-ok">Active</span>';
      return`<tr onclick="location.href='/user/${encodeURIComponent(u.email)}'">
        <td style="color:var(--muted);font-size:.7rem">${start+i+1}</td>
        <td style="font-weight:500">${u.email||'—'}</td>
        <td class="col-desktop">${fmt(u.total)}</td>
        <td class="col-desktop">${u.quota>0?fmt(u.quota):'<span style="color:var(--muted)">—</span>'}</td>
        <td>${bar}</td>
        <td class="col-desktop" style="color:var(--muted);font-size:.77rem">${fmt(u.up)}</td>
        <td class="col-desktop" style="color:var(--muted);font-size:.77rem">${fmt(u.down)}</td>
        <td>${badge}</td>
      </tr>`;
    }).join('');
    renderPagination(pages,page);
  }catch(e){console.error(e);}
}

function renderPagination(totalPages,currentPage){
  const el=document.getElementById('pagination');
  if(!el)return;
  if(totalPages<=1){el.innerHTML='';return;}
  const cp=currentPage||_page;
  let pages=[];
  for(let i=1;i<=totalPages;i++){
    if(i===1||i===totalPages||Math.abs(i-cp)<=2)pages.push(i);
  }
  let html='',prev=0;
  for(const p of pages){
    if(prev&&p-prev>1)html+='<span class="pg-gap">…</span>';
    html+=`<button class="pg-btn${p===cp?' active':''}" onclick="goPage(${p})">${p}</button>`;
    prev=p;
  }
  el.innerHTML=`<button class="pg-btn" onclick="goPage(${cp-1})" ${cp===1?'disabled':''}>‹</button>${html}<button class="pg-btn" onclick="goPage(${cp+1})" ${cp===totalPages?'disabled':''}>›</button>`;
}

function goPage(p){
  if(p<1)return; _page=p; loadPage();
  document.querySelector('.tbl-wrap')?.scrollIntoView({behavior:'smooth',block:'nearest'});
}

function renderRestarts(list){
  const el=document.getElementById('restarts');
  if(!list.length){el.innerHTML='<div class="empty">No restarts recorded</div>';return;}
  el.innerHTML=list.map(r=>`<div class="log-row"><span class="log-ts">${r.time}</span><span class="log-reason">${r.reason}</span></div>`).join('');
}

async function refresh(){
  try{
    const [summary,restarts,onlineData]=await Promise.all([
      fetchJSON('/api/summary'),
      fetchJSON('/api/restarts'),
      fetchJSON('/api/online').catch(()=>({count:0,emails:[]})),
    ]);
    _lastRestarts=restarts;
    _summary={...summary,online:onlineData.count};
    _online=onlineData;
    renderKPI(); loadPage();
    if(document.getElementById('view-restarts').classList.contains('active')) renderRestarts(restarts);
  }catch(e){console.error(e);}
}

document.getElementById('countdown').textContent=_refreshSec;
let _tChart=null,_tChartHours=0,_tHours=24,_uChart=null,_onlineChart=null,_onlineHours=24;

async function fetchJSON(url,retries=3,delay=2000){
  for(let i=0;i<retries;i++){
    try{
      const r=await fetch(url);
      if(!r.ok)throw new Error(r.status);
      return await r.json();
    }catch(e){
      if(i===retries-1)throw e;
      await new Promise(res=>setTimeout(res,delay*(i+1)));
    }
  }
}

(()=>{const v=new URLSearchParams(location.search).get('v');if(v)openView(v);history.replaceState({},'','/');})();
refresh();

function fmtUptime(sec){
  if(!sec)return'—';
  const d=Math.floor(sec/86400),h=Math.floor(sec%86400/3600),m=Math.floor(sec%3600/60);
  return d?`${d}d ${h}h`:h?`${h}h ${m}m`:`${m}m`;
}
function fmtMem(bytes){
  if(!bytes)return'0';
  if(bytes>=1073741824)return(bytes/1073741824).toFixed(1)+' GB';
  return(bytes/1048576).toFixed(0)+' MB';
}
function fmtRate(bps){
  if(!bps)return'0 B/s';
  if(bps>=1048576)return(bps/1048576).toFixed(1)+' MB/s';
  if(bps>=1024)return(bps/1024).toFixed(0)+' KB/s';
  return bps.toFixed(0)+' B/s';
}
let _lastSrv={};
async function refreshServerStats(){
  try{
    const s=await fetch('/api/server-stats').then(r=>r.json());
    _lastSrv=s;
    const xrayState=s.xray?.state||'—', xrayVer=s.xray?.version||'';
    const xrayC=xrayState==='running'?'#22c55e':'#f04a4a';
    const el=document.getElementById('server-stats');
    if(!el)return;
    const box=el.children[3];
    if(box){
      box.querySelector('.sv').innerHTML=fmtUptime(s.uptime);
      box.querySelector('.sl').innerHTML=`Xray: <span style="color:${xrayC}">${xrayState}</span>${xrayVer?' v'+xrayVer:''}`;
    }
    if(_srvMetric)loadSrvChart();
  }catch(e){}
}
refreshServerStats();
setInterval(refreshServerStats, 20000);

setInterval(async function(){
  try{
    const s=await fetch('/api/live/stats').then(r=>r.json());
    const el=document.getElementById('server-stats');
    if(!el)return;
    const cpu=s.cpu??0;
    const mu=s.mem?.current??0, mt=s.mem?.total??0;
    const du=s.disk?.current??0, dt=s.disk?.total??0;
    const up=s.bw?.up??0, dn=s.bw?.down??0;
    const mp=mt>0?mu/mt*100:0, dp=dt>0?du/dt*100:0;
    const cpuC=cpu>=85?'#f04a4a':cpu>=60?'#f59e0b':'#4f8ef7';
    const memC=mp>=85?'#f04a4a':mp>=60?'#f59e0b':'#22c55e';
    const dskC=dp>=85?'#f04a4a':dp>=60?'#f59e0b':'#f59e0b';
    const live=[
      {sv:`<span style="color:${cpuC}">${cpu.toFixed(1)}%</span>`,
       sl:'CPU',
       bar:`<div class="srv-bar"><div class="srv-fill" style="width:${Math.min(cpu,100).toFixed(1)}%;background:${cpuC}"></div></div>`,
       metric:'cpu'},
      {sv:`<span style="color:${memC}">${fmtMem(mu)}</span> <span style="font-size:.65rem;color:var(--muted)">/ ${fmtMem(mt)}</span>`,
       sl:`RAM — ${mp.toFixed(0)}%`,
       bar:`<div class="srv-bar"><div class="srv-fill" style="width:${Math.min(mp,100).toFixed(1)}%;background:${memC}"></div></div>`,
       metric:'ram'},
      {sv:`<span style="color:${dskC}">${fmtMem(du)}</span> <span style="font-size:.65rem;color:var(--muted)">/ ${fmtMem(dt)}</span>`,
       sl:`Disk — ${dp.toFixed(0)}%`,
       bar:`<div class="srv-bar"><div class="srv-fill" style="width:${Math.min(dp,100).toFixed(1)}%;background:${dskC}"></div></div>`,
       metric:'disk'},
      {sv:`<span style="color:#22c55e">↑ ${fmtRate(up)}</span> <span style="color:var(--muted)">/</span> <span style="color:#4f8ef7">↓ ${fmtRate(dn)}</span>`,
       sl:'Bandwidth',
       bar:'',
       metric:'net'},
    ];
    live.forEach((b,i)=>{
      const box=i===3?document.getElementById('bw-srv'):el.children[i]; if(!box)return;
      const svEl=box.querySelector('.sv'); if(svEl)svEl.innerHTML=b.sv;
      const slEl=box.querySelector('.sl'); if(slEl)slEl.innerHTML=b.sl;
      if(b.bar){
        const barEl=box.querySelector('.srv-bar');
        if(barEl)barEl.outerHTML=b.bar; else box.insertAdjacentHTML('beforeend',b.bar);
      }
      box.classList.toggle('active',_srvMetric===b.metric);
    });
  }catch(e){}
},2000);

let _srvChart=null, _srvMetric=null;
const _srvColors={cpu:'#4f8ef7',ram:'#22c55e',disk:'#f59e0b',net:'#a78bfa'};

function toggleSrvChart(metric,title){
  if(_srvMetric===metric){closeSrvChart();return;}
  _srvMetric=metric;
  document.getElementById('srv-chart-title').textContent=title+' — loading…';
  document.getElementById('srv-chart-panel').style.display='block';
  document.querySelectorAll('.srv-box').forEach(b=>b.classList.remove('active'));
  const idx={cpu:0,ram:1,disk:2,net:3}[metric];
  if(idx!=null)document.getElementById('server-stats').children[idx]?.classList.add('active');
  if(_srvChart){_srvChart.destroy();_srvChart=null;}
  loadSrvChart();
}
function closeSrvChart(){
  _srvMetric=null;
  document.getElementById('srv-chart-panel').style.display='none';
  const avgEl=document.getElementById('srv-chart-avg');
  if(avgEl)avgEl.style.display='none';
  document.querySelectorAll('.srv-box').forEach(b=>b.classList.remove('active'));
  if(_srvChart){_srvChart.destroy();_srvChart=null;}
}
async function loadSrvChart(){
  if(!_srvMetric)return;
  try{
    const range=await fetch('/api/server/data-range').then(r=>r.json());
    const hoursAvail=range.hours||0;
    const chosenH=Math.max(1,Math.min(24,Math.round(hoursAvail)||1));
    const chosenG=chosenH>=12?60:chosenH>=3?30:10;
    const data=await fetch(`/api/server/history?metric=${_srvMetric}&hours=${chosenH}&gran=${chosenG}`).then(r=>r.json());
    if(!data.length){
      document.getElementById('srv-chart-title').textContent=_srvMetric.toUpperCase()+' — collecting data…';
      return;
    }
    const timeLabel=chosenH<24?`last ${chosenH}h`:'last 24h';
    document.getElementById('srv-chart-title').textContent=
      (_srvMetric==='cpu'?'CPU':_srvMetric==='ram'?'RAM':_srvMetric==='disk'?'Disk':'Bandwidth')+
      ` — ${timeLabel}`;
    const labels=data.map(d=>d.hour);
    const values=data.map(d=>d.value);
    const unit=data[0]?.unit||'%';
    const color=_srvColors[_srvMetric]||'#4f8ef7';
    const isNet=_srvMetric==='net';
    const fmtBw=v=>v>=1?v.toFixed(2)+' MB/s':v>0?(v*1024).toFixed(1)+' KB/s':'0 B/s';
    const fmtMB=v=>v>=1024?(v/1024).toFixed(2)+' GB':v.toFixed(1)+' MB';
    const dotStyle=(c)=>({
      borderColor:c,backgroundColor:'transparent',
      borderWidth:2,fill:false,tension:0,
      pointRadius:4,pointBackgroundColor:c,
      pointHoverRadius:6,borderDash:[6,4]
    });
    const avgUp=isNet?data.reduce((s,d)=>s+(d.up||0),0)/Math.max(data.length,1):0;
    const avgDn=isNet?data.reduce((s,d)=>s+(d.down||0),0)/Math.max(data.length,1):0;
    const avgEl=document.getElementById('srv-chart-avg');
    if(avgEl&&isNet){
      avgEl.innerHTML=`Avg: <span style="color:#22c55e">↑ ${fmtBw(avgUp)}</span>&nbsp;&nbsp;<span style="color:#4f8ef7">↓ ${fmtBw(avgDn)}</span>`;
      avgEl.style.display='block';
    } else if(avgEl){avgEl.style.display='none';}
    const datasets=isNet?[
      {label:'Upload',  data:data.map(d=>d.up),   ...dotStyle('#22c55e')},
      {label:'Download',data:data.map(d=>d.down), ...dotStyle('#4f8ef7')},
    ]:[{label:_srvMetric,data:values,
        borderColor:color,backgroundColor:color+'22',
        borderWidth:1.5,fill:true,tension:0.35,
        pointRadius:2,pointHoverRadius:4}];
    const tooltipCbs=isNet?{
      label:c=>{
        const d=data[c.dataIndex];
        const tot=c.datasetIndex===0?d?.total_up:d?.total_down;
        return ` ${c.dataset.label}: ${fmtBw(c.parsed.y)}  (${fmtMB(tot||0)})`;
      },
      afterBody:()=>[`  Avg: ↑ ${fmtBw(avgUp)}  ↓ ${fmtBw(avgDn)}`]
    }:{label:c=>` ${c.dataset.label}: ${c.parsed.y.toFixed(1)} ${unit}`};
    const cfg={
      type:'line',
      data:{labels,datasets},
      options:{responsive:true,
        plugins:{
          legend:{display:isNet,labels:{color:'#dde4f0',font:{size:11},
            usePointStyle:true,pointStyle:'circle'}},
          tooltip:{callbacks:tooltipCbs}
        },
        scales:{
          x:{ticks:{color:'#4a637a',font:{size:10},maxRotation:45},grid:{color:'#1a2840'}},
          y:{ticks:{color:'#4a637a',font:{size:10},
               callback:v=>isNet?fmtBw(v):v.toFixed(0)+'%'},
             grid:{color:'#1a2840'},beginAtZero:true,
             max:isNet?undefined:100}
        }
      }
    };
    _srvChart=new Chart(document.getElementById('srv-chart'),cfg);
  }catch(e){}
}


const _logState={traffic_log_enabled:__TRAFFIC_LOG__,online_log_enabled:__ONLINE_LOG__};

function applyLogGate(type, enabled){
  const blur=document.getElementById('gate-'+type+'-blur');
  const btn =document.getElementById('gate-'+type+'-btn');
  const hdr =document.getElementById('toggle-'+type);
  if(blur)blur.classList.toggle('off',!enabled);
  if(btn) btn.classList.toggle('show',!enabled);
  if(hdr) hdr.style.display=enabled?'':'none';
}

(()=>{
  applyLogGate('traffic',!!_logState.traffic_log_enabled);
  applyLogGate('online', !!_logState.online_log_enabled);
  if(_logState.traffic_log_enabled){const b=document.getElementById('toggle-traffic');if(b)b.classList.add('on');}
  if(_logState.online_log_enabled) {const b=document.getElementById('toggle-online'); if(b)b.classList.add('on');}
})();

async function toggleLogging(key,btnId){
  const type=key==='traffic_log_enabled'?'traffic':'online';
  const hdrBtn=document.getElementById(btnId);
  const isOn=hdrBtn?hdrBtn.classList.contains('on'):!!_logState[key];
  const newVal=!isOn;
  _logState[key]=newVal?1:0;
  if(hdrBtn)hdrBtn.classList.toggle('on',newVal);
  applyLogGate(type,newVal);
  try{
    await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({[key]:newVal?'1':'0'})});
  }catch(e){
    _logState[key]=isOn?1:0;
    if(hdrBtn)hdrBtn.classList.toggle('on',isOn);
    applyLogGate(type,isOn);
  }
}

function setTRange(btn){
  document.querySelectorAll('.panel-hd .chip[data-h]').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');
  _tHours=parseInt(btn.dataset.h);
  _tChart=_tChart&&(_tChart.destroy(),null);
  _uChart=_uChart&&(_uChart.destroy(),null);
  refreshTrafficChart();
}

function exportTrafficCSV(){
  window.open(`/api/traffic/export?hours=${_tHours}`,'_blank');
}

async function refreshTrafficChart(){
  try{
    const hours=_tHours;
    const gran=hours<=24?60:hours<=168?360:1440;
    const [data,topUsers]=await Promise.all([
      fetchJSON(`/api/traffic/hourly?hours=${hours}&gran=${gran}`),
      fetchJSON(`/api/traffic/top-users?hours=${hours}&limit=15`),
    ]);
    const labels=data.map(d=>d.hour);
    const values=data.map(d=>d.gb);
    const total=data.reduce((s,d)=>s+d.bytes,0);
    const peak=data.reduce((a,d)=>Math.max(a,d.bytes),0);
    const dailyAvg=hours>0?total/hours*24:0;
    const tLabel=hours<=24?`${hours}h`:hours<=168?`${hours/24|0}d`:`${hours/24|0}d`;
    document.getElementById('traffic-stats').innerHTML=[
      {l:`Total (${tLabel})`,  v:fmt(total),    c:'#4f8ef7'},
      {l:'Daily Avg',          v:fmt(dailyAvg), c:'#a78bfa'},
      {l:'Peak interval',      v:fmt(peak),     c:'#f59e0b'},
      {l:'Top user',           v:topUsers[0]?.email?.split('@')[0]||'—', c:'#22c55e'},
    ].map(s=>`<div class="stat-box" style="padding:8px 12px"><div class="sv" style="font-size:.82rem;color:${s.c||'var(--text)'}">${s.v}</div><div class="sl">${s.l}</div></div>`).join('');

    const color='#4f8ef7';
    if(_tChart&&_tChartHours===hours){
      _tChart.data.labels=labels;_tChart.data.datasets[0].data=values;_tChart.update();
    }else{
      if(_tChart){_tChart.destroy();}
      _tChartHours=hours;
      _tChart=new Chart(document.getElementById('traffic-chart'),{
        type:'bar',
        data:{labels,datasets:[{label:'Traffic',data:values,
          backgroundColor:color+'22',borderColor:color,borderWidth:1.5,borderRadius:3,
          hoverBackgroundColor:color+'55'}]},
        options:{responsive:true,plugins:{legend:{display:false},tooltip:{callbacks:{
          label:c=>{const gb=c.parsed.y;return'Traffic: '+(gb>=1?gb.toFixed(3)+' GB':(gb*1024).toFixed(1)+' MB');}
        }}},scales:{
          x:{ticks:{color:'#4a637a',font:{size:10},maxRotation:45},grid:{color:'#1a2840'}},
          y:{ticks:{color:'#4a637a',font:{size:10},callback:v=>v>=1?v.toFixed(1)+'G':(v*1024).toFixed(0)+'M'},
             grid:{color:'#1a2840'},beginAtZero:true}
        }}
      });
    }

    {
      const top10=topUsers.slice(0,10);
      const uLabels=top10.map(u=>u.email.length>22?u.email.slice(0,20)+'…':u.email);
      const uValues=top10.map(u=>u.gb);
      const uColors=['#4f8ef7','#22c55e','#f59e0b','#f04a4a','#a78bfa','#06b6d4','#f97316','#ec4899','#84cc16','#14b8a6'];
      if(_uChart){_uChart.destroy();}
      _uChart=new Chart(document.getElementById('user-chart'),{
        type:'bar',
        data:{labels:uLabels,datasets:[{label:'Traffic',data:uValues,
          backgroundColor:uColors.map(c=>c+'cc'),borderColor:uColors,borderWidth:1.5,borderRadius:4}]},
        options:{indexAxis:'y',responsive:true,plugins:{legend:{display:false},tooltip:{callbacks:{
          label:c=>{const gb=c.parsed.x;return'Traffic: '+(gb>=1?gb.toFixed(3)+' GB':(gb*1024).toFixed(1)+' MB');}
        }}},scales:{
          x:{ticks:{color:'#4a637a',font:{size:10},callback:v=>v>=1?v.toFixed(1)+'G':(v*1024).toFixed(0)+'M'},grid:{color:'#1a2840'},beginAtZero:true},
          y:{ticks:{color:'#4a637a',font:{size:10}},grid:{color:'#1a2840'}}
        }}
      });
      const totalBytes=topUsers.reduce((s,u)=>s+u.bytes,0);
      document.getElementById('top-users-tbody').innerHTML=topUsers.length
        ?topUsers.map((u,i)=>{
            const pct=totalBytes>0?(u.bytes/totalBytes*100).toFixed(1):0;
            const bar=`<div style="height:4px;border-radius:99px;background:#1a2840;width:100%;margin-top:3px"><div style="height:4px;border-radius:99px;background:${uColors[i%10]};width:${pct}%"></div></div>`;
            return`<tr onclick="location.href='/user/${encodeURIComponent(u.email)}'" style="cursor:pointer">
              <td style="color:var(--muted);font-size:.7rem">${i+1}</td>
              <td style="font-weight:500">${u.email}</td>
              <td>${fmt(u.bytes)}</td>
              <td style="min-width:90px">${pct}%${bar}</td>
            </tr>`;
          }).join('')
        :'<tr><td colspan="4" style="text-align:center;color:var(--muted);padding:18px">No data</td></tr>';
    }
  }catch(e){console.error(e);}
}
setInterval(()=>{if(document.getElementById('view-traffic').classList.contains('active'))refreshTrafficChart();},60000);

function fmtDur(sec){
  if(sec<60)return sec+'s';
  if(sec<3600)return Math.floor(sec/60)+'m';
  const h=Math.floor(sec/3600),m=Math.floor(sec%3600/60);
  return m?`${h}h ${m}m`:`${h}h`;
}

function renderOnlineUsers(users){
  const el=document.getElementById('online-users-list');
  if(!el)return;
  if(!users||!users.length){
    el.innerHTML='<div style="text-align:center;color:var(--muted);padding:20px 0;font-size:.75rem">No users online right now</div>';
    return;
  }
  el.innerHTML=users.map((u,i)=>{
    const dur=u.duration_sec!=null?`<span style="font-size:.72rem;color:var(--muted);flex-shrink:0">${fmtDur(u.duration_sec)}</span>`:'';
    const rank=`<span style="font-size:.68rem;color:var(--muted);width:16px;text-align:right;flex-shrink:0">${i+1}</span>`;
    const email=u.email||u;
    return`<a href="/user/${encodeURIComponent(email)}" class="online-user-row">${rank}<span class="online-pulse"></span><span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${email}</span>${dur}<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="color:var(--muted);flex-shrink:0"><polyline points="9 18 15 12 9 6"/></svg></a>`;
  }).join('');
}

async function refreshOnlineUsers(){
  try{
    const d=await fetchJSON('/api/online/durations');
    _online={count:d.length,emails:d.map(u=>u.email)};
    _summary.online=d.length;
    renderOnlineUsers(d);
    const nowEl=document.querySelector('#online-stats .stat-box:last-child .sv');
    if(nowEl)nowEl.textContent=d.length;
  }catch(e){}
}

function setOnlineRange(h){
  _onlineHours=h;
  document.querySelectorAll('.tf-btn[id^="obtn-"]').forEach(b=>b.classList.remove('tf-active'));
  const btn=document.getElementById('obtn-'+h);
  if(btn)btn.classList.add('tf-active');
  if(_onlineChart){_onlineChart.destroy();_onlineChart=null;}
  loadOnlineChart();
}
async function loadOnlineChart(){
  try{
    const data=await fetchJSON('/api/online/history?hours='+_onlineHours);
    const rangeLabel=_onlineHours<=6?'6h':_onlineHours<=24?'24h':_onlineHours<=72?'3d':'7d';
    if(!data.length){
      document.getElementById('online-stats').innerHTML='<span style="font-size:.72rem;color:var(--muted)">Not enough data yet — check back in 30 min.</span>';
    }else{
      const multiDay=_onlineHours>24;
      const labels=data.map(d=>{
        const dt=new Date(d.ts*1000);
        if(multiDay) return dt.toLocaleDateString('en-GB',{timeZone:CLIENT_TZ,month:'short',day:'numeric'})
          +' '+dt.toLocaleTimeString('en-GB',{timeZone:CLIENT_TZ,hour:'2-digit',minute:'2-digit',hour12:false});
        return dt.toLocaleTimeString('en-GB',{timeZone:CLIENT_TZ,hour:'2-digit',minute:'2-digit',hour12:false});
      });
      const counts=data.map(d=>d.count);
      const peak=Math.max(...counts);
      const avg=counts.length?Math.round(counts.reduce((a,b)=>a+b,0)/counts.length):0;
      document.getElementById('online-stats').innerHTML=[
        {l:'Peak ('+rangeLabel+')', v:peak,            c:'#06b6d4'},
        {l:'Avg ('+rangeLabel+')',  v:avg,             c:'#4f8ef7'},
        {l:'Now',                   v:_summary.online, c:'#22c55e'},
      ].map(s=>`<div class="stat-box" style="padding:8px 12px"><div class="sv" style="font-size:.85rem;color:${s.c}">${s.v}</div><div class="sl">${s.l}</div></div>`).join('');
      const color='#06b6d4';
      if(_onlineChart){_onlineChart.data.labels=labels;_onlineChart.data.datasets[0].data=counts;_onlineChart.update();}
      else{
        _onlineChart=new Chart(document.getElementById('online-chart'),{
          type:'line',
          data:{labels,datasets:[{label:'Online',data:counts,
            borderColor:color,borderWidth:1.8,pointRadius:2,pointHoverRadius:4,
            backgroundColor:color+'18',fill:true,tension:0.3}]},
          options:{responsive:true,
            plugins:{legend:{display:false},tooltip:{callbacks:{
              label:c=>'Online: '+c.parsed.y+' users'
            }}},
            scales:{
              x:{ticks:{color:'#4a637a',font:{size:10},maxRotation:45,maxTicksLimit:12},grid:{color:'#1a2840'}},
              y:{ticks:{color:'#4a637a',font:{size:10},stepSize:1,precision:0},
                 grid:{color:'#1a2840'},beginAtZero:true}
            }
          }
        });
      }
    }
  }catch(e){}
  refreshOnlineUsers();
}
setInterval(()=>{if(document.getElementById('view-online').classList.contains('active'))loadOnlineChart();},60000);
setInterval(()=>{if(document.getElementById('view-online').classList.contains('active'))refreshOnlineUsers();},15000);
</script>
</body></html>"""

USER_HTML = r"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__EMAIL__ — 3x-ui Monitor</title>
<script src="/static/chart.min.js"></script>
__BASE_STYLE__
<style>
.breadcrumb{display:flex;align-items:center;gap:7px;font-size:.77rem;color:var(--muted);margin-bottom:16px}
.breadcrumb a{color:var(--blue)}
.user-header{background:var(--card);border:1px solid var(--border);border-radius:13px;
  padding:20px 22px;margin-bottom:16px;display:flex;gap:22px;flex-wrap:wrap}
.user-email{font-size:1.08rem;font-weight:700;margin-bottom:4px;word-break:break-all}
.user-meta{display:flex;flex-wrap:wrap;gap:16px;margin-top:11px}
.meta-item{display:flex;flex-direction:column;gap:2px}
.meta-label{font-size:.63rem;color:var(--muted);text-transform:uppercase;letter-spacing:.6px}
.meta-value{font-size:.84rem;font-weight:600}
.big-prog-bar{height:8px;background:#1a2840;border-radius:99px;overflow:hidden;margin-top:5px;width:min(320px,100%)}
.big-prog-fill{height:8px;border-radius:99px}
.big-prog-labels{display:flex;justify-content:space-between;font-size:.67rem;color:var(--muted);margin-top:3px;width:min(320px,100%)}
.gran-row{display:flex;gap:6px;margin-bottom:11px;flex-wrap:wrap}
.chart-full{background:var(--card);border:1px solid var(--border);border-radius:13px;padding:17px}
.chart-stats{display:grid;grid-template-columns:repeat(auto-fill,minmax(118px,1fr));gap:8px;margin-bottom:13px}
.stat-box{background:var(--surface);border:1px solid var(--border);border-radius:9px;padding:10px 12px}
.stat-box .sv{font-size:.93rem;font-weight:700;margin-bottom:2px}
.stat-box .sl{font-size:.63rem;color:var(--muted);text-transform:uppercase;letter-spacing:.5px}
</style>
</head>
<body>
__TOPBAR_BACK__
<div class="wrap">
  <div class="breadcrumb">
    <a href="/">Dashboard</a>
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 18 15 12 9 6"/></svg>
    <span>__EMAIL__</span>
  </div>

  <div class="user-header">
    <div style="flex:1;min-width:200px">
      <div class="user-email">__EMAIL__</div>
      <div id="status-badge" style="margin-top:6px"></div>
      <div class="user-meta" id="user-meta"></div>
      <div id="big-prog" style="margin-top:13px"></div>
    </div>
    <div id="right-stats"></div>
  </div>

  <div class="sec"><div class="sec-line"></div><span class="sec-title">Traffic History</span><div class="sec-line"></div></div>

  <div class="gran-row">
    <button class="chip active" data-g="30"   data-h="24"  onclick="setGran(this)">24h / 30m</button>
    <button class="chip"        data-g="60"   data-h="24"  onclick="setGran(this)">24h / 1h</button>
    <button class="chip"        data-g="180"  data-h="72"  onclick="setGran(this)">3d / 3h</button>
    <button class="chip"        data-g="360"  data-h="168" onclick="setGran(this)">7d / 6h</button>
  </div>

  <div class="chart-full">
    <div class="chart-stats" id="chart-stats"></div>
    <canvas id="main-chart" style="max-height:250px"></canvas>
  </div>
</div>
__COMMON_JS__
<script>
const _csrf="__CSRF_TOKEN__";
(()=>{const _f=window.fetch;window.fetch=(u,o={})=>{if(o.method&&o.method.toUpperCase()!=='GET'){o.headers={...(o.headers||{}),'X-CSRF-Token':_csrf};}return _f(u,o);};})();
const EMAIL=__EMAIL_JSON__;
const CLIENT_TZ="__TZ__";
const GB=1073741824,MB=1048576;
const fmt=n=>{if(!n||n<0)return'0 B';if(n>=GB)return(n/GB).toFixed(2)+' GB';if(n>=MB)return(n/MB).toFixed(1)+' MB';return(n/1024).toFixed(0)+' KB';};
const pct=(t,q)=>q>0?t/q*100:0;
const barColor=p=>p>=110?'#f04a4a':p>=90?'#f59e0b':'#4f8ef7';

let _chart=null,_gran=30,_hours=24;

setInterval(()=>{
  const el=document.getElementById('clock');
  if(el)el.textContent=new Date().toLocaleTimeString('en-GB',{timeZone:CLIENT_TZ,hour12:false})+' (IR)';
},1000);

function setGran(btn){
  document.querySelectorAll('.gran-row .chip').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');
  _gran=parseInt(btn.dataset.g);
  _hours=parseInt(btn.dataset.h);
  loadChart();
}

function renderHeader(u,handled){
  const isHandled=handled.some(h=>h.email===EMAIL);
  const isOver=u.quota>0&&u.total>u.quota;
  const p=pct(u.total,u.quota),bc=barColor(p);
  const badge=!u.enable?'<span class="badge b-off">Disabled</span>'
    :isHandled?'<span class="badge b-handle">Blocked — awaiting renewal</span>'
    :isOver?'<span class="badge b-over">Limit</span>'
    :u.expired?'<span class="badge b-exp">Expired</span>'
    :'<span class="badge b-ok">Active</span>';
  document.getElementById('status-badge').innerHTML=badge;
  document.getElementById('user-meta').innerHTML=[
    {l:'Upload',v:fmt(u.up)},{l:'Download',v:fmt(u.down)},
    {l:'Total Used',v:fmt(u.total)},
    {l:'Quota',v:u.quota>0?fmt(u.quota):'Unlimited'},
    {l:'Expired',v:u.expired?'<span style="color:#f59e0b">Yes</span>':'No'},
    {l:'Enabled',v:u.enable?'Yes':'<span style="color:#4a637a">No</span>'},
  ].map(m=>`<div class="meta-item"><span class="meta-label">${m.l}</span><span class="meta-value">${m.v}</span></div>`).join('');
  if(u.quota>0){
    document.getElementById('big-prog').innerHTML=`
      <div style="font-size:.63rem;color:var(--muted);text-transform:uppercase;letter-spacing:.6px;margin-bottom:3px">Overall Usage</div>
      <div class="big-prog-bar"><div class="big-prog-fill" style="width:${Math.min(p,100).toFixed(1)}%;background:${bc}"></div></div>
      <div class="big-prog-labels"><span>${fmt(u.total)}</span><span>${p.toFixed(1)}%</span><span>${fmt(u.quota)}</span></div>`;
  }
  const h=handled.find(h=>h.email===EMAIL);
  if(h){
    document.getElementById('right-stats').innerHTML=`
      <div class="stat-box" style="min-width:145px">
        <div class="sv" style="color:#a78bfa;font-size:.79rem">${h.at}</div>
        <div class="sl">Blocked at (IR)</div>
        <div class="sv" style="margin-top:7px">${fmt(h.total)}</div>
        <div class="sl">Usage at block</div>
      </div>`;
  }
}

async function loadChart(){
  let hours=_hours, gran=_gran;
  try{
    const range=await fetch('/api/data-range').then(r=>r.json());
    const avail=range.hours||0;
    if(avail>0 && avail<hours){
      hours=Math.max(1,Math.round(avail));
      gran=hours>=12?60:hours>=3?30:10;
    }
  }catch(e){}
  const data=await fetch(
    '/api/user/hourly?email='+encodeURIComponent(EMAIL)+'&gran='+gran+'&hours='+hours
  ).then(r=>r.json());
  const labels=data.map(d=>d.hour),values=data.map(d=>d.gb);
  const total=data.reduce((s,d)=>s+d.bytes,0);
  const peak=data.reduce((a,d)=>Math.max(a,d.bytes),0);
  const avg=data.length?total/data.length:0;
  document.getElementById('chart-stats').innerHTML=[
    {l:'Period Traffic',v:fmt(total),c:'#4f8ef7'},
    {l:'Peak Interval', v:fmt(peak), c:'#f59e0b'},
    {l:'Avg / Interval', v:fmt(avg), c:'#22c55e'},
    {l:'Data Points',    v:data.length,c:''},
  ].map(s=>`<div class="stat-box"><div class="sv" style="color:${s.c||'var(--text)'}">${s.v}</div><div class="sl">${s.l}</div></div>`).join('');
  const color='#4f8ef7';
  if(_chart){_chart.data.labels=labels;_chart.data.datasets[0].data=values;_chart.update();}
  else{
    _chart=new Chart(document.getElementById('main-chart'),{
      type:'bar',
      data:{labels,datasets:[{label:'Traffic',data:values,
        backgroundColor:color+'20',borderColor:color,borderWidth:1.5,borderRadius:4,
        hoverBackgroundColor:color+'45'}]},
      options:{responsive:true,
        plugins:{legend:{display:false},tooltip:{callbacks:{
          title:c=>'Time: '+c[0].label+' (IR)',
          label:c=>{const gb=c.parsed.y;return'Traffic: '+(gb>=1?gb.toFixed(4)+' GB':(gb*1024).toFixed(2)+' MB');}
        }}},
        scales:{
          x:{ticks:{color:'#4a637a',font:{size:10},maxRotation:45},grid:{color:'#1a2840'}},
          y:{ticks:{color:'#4a637a',font:{size:10},callback:v=>v>=1?v.toFixed(1)+'G':(v*1024).toFixed(0)+'M'},
             grid:{color:'#1a2840'},beginAtZero:true}
        }
      }
    });
  }
}

async function refresh(){
  const [snap,handled]=await Promise.all([
    fetch('/api/user/snapshot?email='+encodeURIComponent(EMAIL)).then(r=>r.json()),
    fetch('/api/handled').then(r=>r.json()),
  ]);
  if(snap)renderHeader(snap,handled);
}
refresh(); loadChart();
setInterval(refresh,30000); setInterval(loadChart,60000);
</script>
</body></html>"""

SETTINGS_HTML = r"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Settings — 3x-ui Monitor</title>
__BASE_STYLE__
<style>
.s-card{background:var(--card);border-radius:11px;padding:18px 20px;margin-bottom:13px;border-left:3px solid var(--blue);transition:border-color .25s,background .25s}
.s-head{display:flex;align-items:center;gap:9px;margin-bottom:15px;padding-bottom:11px;border-bottom:1px solid var(--border)}
.s-icon{opacity:.8;flex-shrink:0}
.s-title{font-size:.88rem;font-weight:700;color:var(--fg);flex:1;letter-spacing:.01em}
.s-badge{font-size:.62rem;padding:2px 9px;border-radius:20px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;flex-shrink:0}
.s-badge.on{background:#052e16;color:#4ade80;border:1px solid #166534}
.s-badge.off{background:#1c0a0a;color:#f87171;border:1px solid #7f1d1d}
.s-badge.req{background:#1c1500;color:#fbbf24;border:1px solid #78350f}
.s-badge.info{background:#0f172a;color:#93c5fd;border:1px solid #1e3a5f}
.field-desc{font-size:.69rem;color:var(--muted);margin:.3rem 0 0;line-height:1.5}
label.imp{color:var(--fg)}
label.imp::after{content:' ★';color:#f59e0b;font-size:.58rem;vertical-align:super}
label.crit::after{content:' ●';color:#ef4444;font-size:.5rem;vertical-align:middle;margin-left:3px}
.dim-fields{opacity:.45;pointer-events:none;transition:opacity .25s}
</style>
</head>
<body>
__TOPBAR__
<div class="wrap">
  <div class="breadcrumb" style="display:flex;align-items:center;gap:7px;font-size:.77rem;color:var(--muted);margin-bottom:18px">
    <a href="/" style="color:var(--blue)">Dashboard</a>
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 18 15 12 9 6"/></svg>
    <span>Settings</span>
  </div>

  <form id="sf">

    <div class="s-card" style="border-left-color:#ef4444">
      <div class="s-head">
        <svg class="s-icon" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#ef4444" stroke-width="2"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>
        <span class="s-title">3x-ui Panel</span>
        <span class="s-badge req">Required</span>
      </div>
      <div class="form-group">
        <label class="crit">Panel URL</label>
        <input name="panel_url" value="__PANEL_URL__">
        <p class="field-desc">Full URL including the secret path — e.g. <code>http://1.2.3.4:2096/secretpath</code>. Without this nothing works.</p>
      </div>
      <div class="form-row">
        <div class="form-group">
          <label class="crit">Username</label>
          <input name="panel_user" value="__PANEL_USER__">
          <p class="field-desc">Admin username for the 3x-ui panel login.</p>
        </div>
        <div class="form-group">
          <label class="crit">Password</label>
          <input name="panel_pass" type="password" value="__PANEL_PASS__">
          <p class="field-desc">Admin password. Stored in app.db (file permissions: 600).</p>
        </div>
      </div>
    </div>

    <div class="s-card" style="border-left-color:#f59e0b" id="card-monitor">
      <div class="s-head">
        <svg class="s-icon" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#f59e0b" stroke-width="2"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>
        <span class="s-title">Monitor</span>
        <span class="s-badge" id="badge-ar"></span>
      </div>
      <div class="form-row">
        <div class="form-group">
          <label class="imp">Grace MB</label>
          <input name="grace_mb" type="number" min="0" value="__GRACE_MB__">
          <p class="field-desc">Extra MB a user can consume beyond their quota before Xray restarts. Set <code>0</code> to cut off exactly at quota.</p>
        </div>
        <div class="form-group">
          <label class="imp">Check Interval (sec)</label>
          <input name="check_interval" type="number" min="10" value="__CHECK_INTERVAL__">
          <p class="field-desc">How often the monitor polls user traffic. Lower = faster detection but more server load. Min: 10s.</p>
        </div>
      </div>
      <div class="form-row">
        <div class="form-group">
          <label>Reset Ratio</label>
          <input name="reset_ratio" type="number" step="0.05" min="0" max="1" value="__RESET_RATIO__">
          <p class="field-desc">A blocked user gets unblocked when their usage drops below this fraction of quota (e.g. <code>0.5</code> = 50%). Range: 0–1.</p>
        </div>
        <div class="form-group">
          <label class="imp">Auto-restart Xray</label>
          <select name="auto_restart_xray" id="sel-ar" onchange="updateArCard()">
            <option value="1"__OPT_AR_1__>Enabled (default)</option>
            <option value="0"__OPT_AR_0__>Disabled — monitor only</option>
          </select>
          <p class="field-desc">When a user exceeds quota, restart Xray core to cut their connection. Only the Xray core is restarted — the panel stays up.</p>
        </div>
      </div>
    </div>

    <div class="s-card" style="border-left-color:var(--blue)">
      <div class="s-head">
        <svg class="s-icon" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="var(--blue)" stroke-width="2"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/></svg>
        <span class="s-title">Dashboard</span>
        <span class="s-badge info">Display</span>
      </div>
      <div class="form-row">
        <div class="form-group">
          <label>Auto-refresh (sec)</label>
          <input name="dashboard_refresh" type="number" min="10" value="__DASH_REFRESH__">
          <p class="field-desc">How often the page refreshes data — shown as a progress bar at the top. Default: 30s.</p>
        </div>
        <div class="form-group">
          <label>Users per page</label>
          <input name="page_size" type="number" min="5" max="200" value="__PAGE_SIZE__">
          <p class="field-desc">Default rows in the users table. Can also be changed live from the toolbar. Default: 20.</p>
        </div>
      </div>
      <div class="form-group">
        <label>Timezone</label>
        <input name="timezone" value="__TZ__" placeholder="Asia/Tehran">
        <p class="field-desc">IANA timezone for all displayed times — e.g. <code>Asia/Tehran</code>, <code>UTC</code>, <code>Europe/London</code>.</p>
      </div>
    </div>

    <div class="s-card" id="card-tls">
      <div class="s-head">
        <svg class="s-icon" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#22c55e" stroke-width="2"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>
        <span class="s-title">TLS / HTTPS</span>
        <span class="s-badge" id="badge-tls"></span>
      </div>
      <div class="form-row">
        <div class="form-group">
          <label class="imp">HTTPS</label>
          <select name="tls_enabled" id="tls-enabled-sel" onchange="updateTlsCard()">
            <option value="0"__TLS_EN_0__>Off (HTTP)</option>
            <option value="1"__TLS_EN_1__>On (HTTPS)</option>
          </select>
          <p class="field-desc">Enable HTTPS using an SSL certificate. After saving you'll be asked to restart the service for it to take effect.</p>
        </div>
        <div class="form-group">
          <label>Domain</label>
          <input name="tls_domain" value="__TLS_DOMAIN__" placeholder="example.com">
          <p class="field-desc">Your domain pointing to this server (reference only — not used for routing).</p>
        </div>
      </div>
      <div id="tls-fields" style="display:__TLS_FIELDS_DISPLAY__">
        <div class="form-row">
          <div class="form-group">
            <label class="imp">Certificate path</label>
            <input name="tls_cert" value="__TLS_CERT__" placeholder="/etc/letsencrypt/live/domain/fullchain.pem">
            <p class="field-desc">Absolute server path to <code>fullchain.pem</code>. Must be readable by the service user.</p>
          </div>
          <div class="form-group">
            <label class="imp">Key path</label>
            <input name="tls_key" value="__TLS_KEY__" placeholder="/etc/letsencrypt/live/domain/privkey.pem">
            <p class="field-desc">Absolute server path to <code>privkey.pem</code>. Must be readable by the service user.</p>
          </div>
        </div>
      </div>
    </div>

    <div class="s-card" id="card-cleanup">
      <div class="s-head">
        <svg class="s-icon" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#94a3b8" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6M14 11v6"/><path d="M9 6V4h6v2"/></svg>
        <span class="s-title">Auto Cleanup</span>
        <span class="s-badge" id="badge-cleanup"></span>
      </div>
      <div class="form-row">
        <div class="form-group">
          <label>Enabled</label>
          <select name="cleanup_enabled" id="sel-cleanup" onchange="updateCleanupCard()">
            <option value="0" __C0__>Off</option>
            <option value="1" __C1__>On</option>
          </select>
          <p class="field-desc">Automatically remove users from the panel after they've been expired or over-limit for N days.</p>
        </div>
        <div class="form-group" id="g-cleanup-days">
          <label>Days before deletion</label>
          <input name="cleanup_days" type="number" min="1" value="__CLEANUP_DAYS__">
          <p class="field-desc">Grace period before a user is deleted. The clock starts when they first go expired or over-limit.</p>
        </div>
      </div>
      <div class="form-group" id="g-cleanup-time">
        <label>Nightly run time</label>
        <input name="cleanup_time" value="__CLEANUP_TIME__" placeholder="03:00">
        <p class="field-desc">Time (HH:MM) in your configured timezone when the nightly cleanup job runs.</p>
      </div>
      <div style="border-top:1px solid var(--border);margin-top:14px;padding-top:14px">
        <label style="font-size:.75rem;color:var(--muted);text-transform:uppercase;letter-spacing:.5px">Manual Cleanup</label>
        <div style="display:flex;align-items:center;gap:10px;margin-top:8px;flex-wrap:wrap">
          <div style="display:flex;align-items:center;gap:7px">
            <span style="font-size:.8rem;color:var(--muted)">Delete snapshots older than</span>
            <input id="manual-days" type="number" min="1" max="365" value="__CLEANUP_DAYS__"
              style="width:64px;padding:5px 8px;border-radius:7px;border:1px solid var(--border);
              background:var(--surface);color:var(--text);font-size:.82rem;text-align:center">
            <span style="font-size:.8rem;color:var(--muted)">days</span>
          </div>
          <button type="button" id="btn-manual-clean" onclick="runManualCleanup()"
            class="btn" style="padding:6px 16px;font-size:.78rem;display:flex;align-items:center;gap:6px">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/></svg>
            Run Now
          </button>
          <span id="cleanup-result" style="font-size:.78rem;color:var(--muted)"></span>
        </div>
      </div>
    </div>

    <div class="s-card" id="card-panel-cleanup" style="border-left-color:#a78bfa">
      <div class="s-head">
        <svg class="s-icon" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#a78bfa" stroke-width="2"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/><line x1="18" y1="8" x2="23" y2="13"/><line x1="23" y1="8" x2="18" y2="13"/></svg>
        <span class="s-title">Panel User Cleanup</span>
        <span class="s-badge" style="background:rgba(167,139,250,.15);color:#a78bfa;border-color:#4c3f8a">Panel</span>
      </div>
      <p style="font-size:.78rem;color:var(--muted);margin-bottom:12px">
        Users that are expired (max 90 days) or over their traffic quota.
      </p>

      <div style="display:flex;align-items:center;gap:9px;flex-wrap:wrap;margin-bottom:14px;
           padding:9px 12px;background:var(--surface);border:1px solid var(--border);border-radius:8px">
        <span style="font-size:.74rem;color:var(--muted);text-transform:uppercase;letter-spacing:.5px">Auto Delete</span>
        <select id="sel-panel-cln" onchange="updatePanelClnBadge()"
          style="background:var(--card);border:1px solid var(--border);border-radius:6px;
          padding:4px 8px;color:var(--text);font-size:.78rem;cursor:pointer">
          <option value="0"__PANEL_CLN_0__>Off</option>
          <option value="1"__PANEL_CLN_1__>On</option>
        </select>
        <div style="display:flex;align-items:center;gap:5px">
          <span style="font-size:.74rem;color:var(--muted)">after</span>
          <input id="panel-cln-days" type="number" min="1" max="90" value="__PANEL_CLN_DAYS__"
            style="width:52px;padding:4px 7px;border-radius:6px;border:1px solid var(--border);
            background:var(--card);color:var(--text);font-size:.8rem;text-align:center">
          <span style="font-size:.74rem;color:var(--muted)">days</span>
        </div>
        <input id="panel-cln-time" type="time" value="__PANEL_CLN_TIME__"
          style="background:var(--card);border:1px solid var(--border);border-radius:6px;
          padding:4px 8px;color:var(--text);font-size:.78rem">
        <button type="button" onclick="savePanelClnSettings()" class="btn" style="padding:5px 12px;font-size:.76rem">Save</button>
        <span id="panel-cln-badge" style="font-size:.72rem;border-radius:99px;padding:2px 9px"></span>
        <span id="panel-cln-msg"   style="font-size:.75rem;color:var(--muted)"></span>
      </div>

      <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:12px">
        <button type="button" id="btn-panel-preview" onclick="panelCleanupPreview()"
          class="btn" style="display:flex;align-items:center;gap:6px">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
          Preview
        </button>
        <div style="display:flex;align-items:center;gap:5px">
          <span style="font-size:.76rem;color:var(--muted)">Aged &gt;</span>
          <input id="old-days-input" type="number" min="1" max="90" value="30"
            style="width:52px;padding:4px 7px;border-radius:6px;border:1px solid var(--border);
            background:var(--surface);color:var(--text);font-size:.8rem;text-align:center">
          <span style="font-size:.76rem;color:var(--muted)">days</span>
        </div>
        <span id="panel-preview-msg" style="font-size:.78rem;color:var(--muted)"></span>
      </div>
      <div id="panel-filter-row" style="display:none;flex-wrap:wrap;gap:5px;margin-bottom:10px">
        <button class="chip active" data-filter="all"        onclick="panelFilter(this)">All</button>
        <button class="chip"        data-filter="expired"    onclick="panelFilter(this)">Expired</button>
        <button class="chip"        data-filter="over_quota" onclick="panelFilter(this)">Over Limit</button>
        <button class="chip"        data-filter="aged"       onclick="panelFilter(this)">Aged</button>
      </div>
      <div id="panel-candidates-wrap" style="display:none">
        <div id="panel-candidates-table"></div>
        <div style="display:flex;align-items:center;gap:8px;margin-top:12px;flex-wrap:wrap">
          <button type="button" id="btn-panel-delete" onclick="panelCleanupExecute()"
            class="btn btn-danger" style="display:flex;align-items:center;gap:6px">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/></svg>
            <span id="panel-delete-label">Delete Selected</span>
          </button>
          <button type="button" onclick="panelSelectAll(true)"  class="btn" style="font-size:.74rem;padding:5px 10px">All</button>
          <button type="button" onclick="panelSelectAll(false)" class="btn" style="font-size:.74rem;padding:5px 10px">None</button>
          <span id="panel-delete-msg" style="font-size:.78rem;color:var(--muted)"></span>
        </div>
      </div>
    </div>

    <div style="margin-top:6px;margin-bottom:26px">
      <button type="submit" class="btn btn-primary" style="padding:10px 26px;font-size:.85rem">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg>
        Save Settings
      </button>
    </div>
  </form>

  <div class="s-card" style="border-left-color:#6366f1">
    <div class="s-head">
      <svg class="s-icon" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#6366f1" stroke-width="2"><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/></svg>
      <span class="s-title">Data Management</span>
      <span class="s-badge info">Traffic DB</span>
    </div>

    <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:9px;margin-bottom:16px" id="db-stats">
      <div class="stat-box"><div class="sv">—</div><div class="sl">Traffic DB</div></div>
      <div class="stat-box"><div class="sv">—</div><div class="sl">App DB</div></div>
      <div class="stat-box"><div class="sv">—</div><div class="sl">Snapshots</div></div>
      <div class="stat-box"><div class="sv">—</div><div class="sl">Oldest record</div></div>
    </div>

    <form id="hf" style="margin-bottom:14px">
      <div class="form-row">
        <div class="form-group">
          <label>Keep history (days)</label>
          <input id="history-days" name="history_days" type="number" min="1" max="90" value="__HISTORY_DAYS__">
          <p class="field-desc">Snapshots older than this are deleted hourly. Max 90 days.</p>
        </div>
        <div class="form-group">
          <label>Max DB size (MB)</label>
          <input id="max-db-mb" name="max_db_mb" type="number" min="0" value="__MAX_DB_MB__">
          <p class="field-desc">If traffic.db exceeds this, oldest rows are pruned. Set <code>0</code> to disable.</p>
        </div>
      </div>
      <button type="submit" class="btn btn-primary" style="padding:9px 16px">Save</button>
    </form>

    <div style="display:flex;gap:10px;flex-wrap:wrap">
      <button class="btn" onclick="clearHistory(+document.getElementById('history-days').value)">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6M14 11v6"/><path d="M9 6V4h6v2"/></svg>
        Clear older than <span id="clear-n">__HISTORY_DAYS__</span> days
      </button>
      <button class="btn btn-danger" onclick="clearHistory(0)">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6M14 11v6"/><path d="M9 6V4h6v2"/></svg>
        Clear all history
      </button>
    </div>
  </div>

</div>
__COMMON_JS__
<script>
const _csrf="__CSRF_TOKEN__";
(()=>{const _f=window.fetch;window.fetch=(u,o={})=>{if(o.method&&o.method.toUpperCase()!=='GET'){o.headers={...(o.headers||{}),'X-CSRF-Token':_csrf};}return _f(u,o);};})();
const CLIENT_TZ="__TZ__";
setInterval(()=>{
  const el=document.getElementById('clock');
  if(el)el.textContent=new Date().toLocaleTimeString('en-GB',{timeZone:CLIENT_TZ,hour12:false})+' (IR)';
},1000);

function updateArCard(){
  const v=document.getElementById('sel-ar').value;
  const b=document.getElementById('badge-ar');
  const card=document.getElementById('card-monitor');
  if(v==='1'){
    b.className='s-badge on';b.textContent='Auto-restart ON';
    card.style.borderLeftColor='#f59e0b';
  } else {
    b.className='s-badge off';b.textContent='Monitor only';
    card.style.borderLeftColor='#7f1d1d';
  }
}

function updateTlsCard(){
  const v=document.getElementById('tls-enabled-sel').value;
  const card=document.getElementById('card-tls');
  const badge=document.getElementById('badge-tls');
  const fields=document.getElementById('tls-fields');
  fields.style.display=v==='1'?'':'none';
  if(v==='1'){
    card.style.borderLeftColor='#22c55e';card.style.background='';
    badge.className='s-badge on';badge.textContent='HTTPS ON';
  } else {
    card.style.borderLeftColor='#3f3f46';card.style.background='rgba(239,68,68,.03)';
    badge.className='s-badge off';badge.textContent='HTTP only';
  }
}

function updateCleanupCard(){
  const v=document.getElementById('sel-cleanup').value;
  const card=document.getElementById('card-cleanup');
  const badge=document.getElementById('badge-cleanup');
  const dg=document.getElementById('g-cleanup-days');
  const tg=document.getElementById('g-cleanup-time');
  if(v==='1'){
    card.style.borderLeftColor='#22c55e';card.style.background='';
    badge.className='s-badge on';badge.textContent='ON';
    dg.classList.remove('dim-fields');tg.classList.remove('dim-fields');
  } else {
    card.style.borderLeftColor='#3f3f46';card.style.background='rgba(239,68,68,.03)';
    badge.className='s-badge off';badge.textContent='OFF';
    dg.classList.add('dim-fields');tg.classList.add('dim-fields');
  }
}

async function runManualCleanup(){
  const days=parseInt(document.getElementById('manual-days')?.value)||7;
  const btn=document.getElementById('btn-manual-clean');
  const res=document.getElementById('cleanup-result');
  btn.disabled=true;res.textContent='Running…';res.style.color='var(--muted)';
  try{
    const r=await fetch('/api/clear-history',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({keep_days:days})});
    const j=await r.json();
    if(j.ok){
      res.textContent=`Done — ${j.deleted.toLocaleString()} rows deleted (kept last ${days}d)`;
      res.style.color='#22c55e';
    }else{
      res.textContent='Error: '+(j.error||'unknown');res.style.color='var(--red)';
    }
  }catch(e){res.textContent='Request failed';res.style.color='var(--red)';}
  btn.disabled=false;
  setTimeout(()=>{res.textContent='';},8000);
}

updateArCard();updateTlsCard();updateCleanupCard();

const _origTls=document.getElementById('tls-enabled-sel')?.value||'0';

document.getElementById('sf').onsubmit=async e=>{
  e.preventDefault();
  const data=Object.fromEntries(new FormData(e.target));
  const r=await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  const j=await r.json();
  if(!j.ok){toast('Error: '+j.error,true);return;}
  toast('Settings saved.');
  if(data.tls_enabled==='1'&&_origTls!=='1'){
    const ok=confirm('TLS is enabled.\nRestart the dashboard service now to apply HTTPS?');
    if(ok){
      toast('Restarting service…');
      const rr=await fetch('/api/restart-dashboard',{method:'POST'}).then(x=>x.json()).catch(()=>({ok:false}));
      if(rr.ok) setTimeout(()=>{location.reload();},3500);
      else toast('Restart failed — run: sudo systemctl restart xui-dashboard',true);
    }
  }
};

document.getElementById('hf').onsubmit=async e=>{
  e.preventDefault();
  const days=document.getElementById('history-days').value;
  const mb=document.getElementById('max-db-mb').value;
  const r=await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({history_days:days,max_db_mb:mb})});
  const j=await r.json();
  toast(j.ok?'History limit saved.':'Error: '+j.error,!j.ok);
};
document.getElementById('history-days').addEventListener('input',function(){
  const n=document.getElementById('clear-n');if(n)n.textContent=this.value;
});

async function loadDbStats(){
  try{
    const s=await fetch('/api/db-stats').then(r=>r.json());
    document.getElementById('db-stats').innerHTML=[
      {v:s.traffic_db_mb+' MB',l:'Traffic DB'},
      {v:s.app_db_mb+' MB',l:'App DB'},
      {v:s.snapshot_count.toLocaleString(),l:'Snapshots'},
      {v:s.oldest||'—',l:'Oldest record'},
    ].map(x=>`<div class="stat-box"><div class="sv">${x.v}</div><div class="sl">${x.l}</div></div>`).join('');
  }catch(e){}
}
loadDbStats();

async function clearHistory(keepDays){
  const msg=keepDays===0?'Clear ALL traffic history? This cannot be undone.'
    :`Clear snapshots older than ${keepDays} day(s)?`;
  if(!confirm(msg))return;
  const r=await fetch('/api/clear-history',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({keep_days:keepDays})});
  const j=await r.json();
  if(j.ok){toast(`Deleted ${j.deleted} rows.`);loadDbStats();}
  else toast('Error: '+j.error,true);
}

async function vacuumDb(){
  const btn=document.getElementById('btn-vacuum');
  const orig=btn.innerHTML;
  btn.disabled=true;
  btn.innerHTML='<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="animation:spin .8s linear infinite"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg> Compressing…';
  const r=await fetch('/api/vacuum-db',{method:'POST'}).then(x=>x.json()).catch(()=>({ok:false,error:'Network error'}));
  btn.disabled=false;btn.innerHTML=orig;
  if(r.ok){
    const msg=r.saved_mb>0?`Compressed — saved ${r.saved_mb} MB`:'Compressed — no free space to reclaim';
    toast(msg);loadDbStats();
  } else toast('Error: '+r.error,true);
}

function updatePanelClnBadge(){
  const v=document.getElementById('sel-panel-cln').value;
  const b=document.getElementById('panel-cln-badge');
  if(v==='1'){b.textContent='ON';b.style.cssText='font-size:.72rem;border-radius:99px;padding:2px 9px;background:rgba(34,197,94,.15);color:#22c55e;border:1px solid rgba(34,197,94,.3)';}
  else{b.textContent='OFF';b.style.cssText='font-size:.72rem;border-radius:99px;padding:2px 9px;background:rgba(74,99,122,.15);color:var(--muted);border:1px solid var(--border)';}
}
async function savePanelClnSettings(){
  const enabled=document.getElementById('sel-panel-cln').value;
  const time=document.getElementById('panel-cln-time').value;
  const days=document.getElementById('panel-cln-days').value;
  const msg=document.getElementById('panel-cln-msg');
  const r=await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({panel_cleanup_enabled:enabled,panel_cleanup_time:time,panel_cleanup_days:days})});
  const j=await r.json();
  msg.textContent=j.ok?'Saved.':'Error: '+(j.error||'unknown');
  msg.style.color=j.ok?'var(--green)':'var(--red)';
  setTimeout(()=>msg.textContent='',3000);
}
updatePanelClnBadge();

let _panelCandidates=[];
let _panelFilter='all';

async function panelCleanupPreview(){
  const oldDays=parseInt(document.getElementById('old-days-input')?.value)||30;
  const btn=document.getElementById('btn-panel-preview');
  const msg=document.getElementById('panel-preview-msg');
  const wrap=document.getElementById('panel-candidates-wrap');
  const filterRow=document.getElementById('panel-filter-row');
  btn.disabled=true;msg.textContent='Loading…';msg.style.color='var(--muted)';
  try{
    const r=await fetch(`/api/cleanup/panel-preview?old_days=${oldDays}`);
    const j=await r.json();
    if(!j.ok){msg.textContent='Error: '+(j.error||'unknown');msg.style.color='var(--red)';return;}
    _panelCandidates=j.candidates;
    if(!_panelCandidates.length){
      msg.textContent='No candidates found.';msg.style.color='var(--green)';
      wrap.style.display='none';filterRow.style.display='none';return;
    }
    const cnt={all:_panelCandidates.length,expired:0,over_quota:0,aged:0};
    _panelCandidates.forEach(c=>{
      if(c.expired)   cnt.expired++;
      if(c.over_quota)cnt.over_quota++;
      if(c.aged)      cnt.aged++;
    });
    filterRow.querySelectorAll('.chip').forEach(ch=>{
      const f=ch.dataset.filter;
      ch.textContent=f==='all'?`All (${cnt.all})`:f==='expired'?`Expired (${cnt.expired})`:
                     f==='over_quota'?`Over Limit (${cnt.over_quota})`:`Aged (${cnt.aged})`;
    });
    filterRow.style.display='flex';
    msg.textContent=`Found ${cnt.all} user(s)`;msg.style.color='var(--amber)';
    renderPanelCandidates();wrap.style.display='';
  }catch(e){msg.textContent='Request failed';msg.style.color='var(--red)';}
  finally{btn.disabled=false;}
}

function panelFilter(chip){
  document.querySelectorAll('#panel-filter-row .chip').forEach(c=>c.classList.remove('active'));
  chip.classList.add('active');_panelFilter=chip.dataset.filter;renderPanelCandidates();
}

function renderPanelCandidates(){
  const visible=_panelCandidates.map((c,i)=>({...c,_idx:i})).filter(c=>{
    if(_panelFilter==='all')       return true;
    if(_panelFilter==='expired')   return c.expired;
    if(_panelFilter==='over_quota')return c.over_quota;
    if(_panelFilter==='aged')      return c.aged;
    return true;
  });
  const tbl=document.getElementById('panel-candidates-table');
  if(!visible.length){
    tbl.innerHTML='<p style="color:var(--muted);font-size:.8rem;padding:6px 0">No users in this category.</p>';
    return;
  }
  let html='<div class="tbl-wrap"><div class="tbl-scroll"><table>'
    +'<thead><tr>'
    +'<th><input type="checkbox" id="chk-all-panel" onchange="panelSelectAll(this.checked)" checked></th>'
    +'<th>Email</th><th>Status</th><th>Usage</th>'
    +'</tr></thead><tbody>';
  visible.forEach(c=>{
    const usage=c.quota_gb?`${c.total_gb} / ${c.quota_gb} GB (${c.pct}%)`:`${c.total_gb} GB`;
    const badges=[];
    if(c.expired)   badges.push('<span style="color:var(--red);font-size:.7rem;background:rgba(240,74,74,.12);padding:2px 7px;border-radius:4px">expired</span>');
    if(c.over_quota)badges.push('<span style="color:var(--amber);font-size:.7rem;background:rgba(245,158,11,.12);padding:2px 7px;border-radius:4px">over limit</span>');
    if(c.aged)      badges.push(`<span style="color:#a78bfa;font-size:.7rem;background:rgba(167,139,250,.12);padding:2px 7px;border-radius:4px">aged${c.expired_days?' Â· '+c.expired_days+'d ago':''}</span>`);
    html+=`<tr>
      <td><input type="checkbox" class="chk-panel" data-idx="${c._idx}" checked onchange="updatePanelDeleteBtn()"></td>
      <td style="font-size:.82rem">${c.email}</td>
      <td style="padding:8px 13px"><div style="display:flex;gap:4px;flex-wrap:wrap">${badges.join('')}</div></td>
      <td style="font-size:.78rem;color:var(--muted)">${usage}</td>
    </tr>`;
  });
  html+='</tbody></table></div></div>';tbl.innerHTML=html;
  updatePanelDeleteBtn();
}

function updatePanelDeleteBtn(){
  const n=document.querySelectorAll('.chk-panel:checked').length;
  const lbl=document.getElementById('panel-delete-label');
  if(lbl)lbl.textContent=n?`Delete Selected (${n})`:'Delete Selected';
}

function panelSelectAll(checked){
  document.querySelectorAll('.chk-panel').forEach(cb=>cb.checked=checked);
  const ca=document.getElementById('chk-all-panel');if(ca)ca.checked=checked;
  updatePanelDeleteBtn();
}

async function panelCleanupExecute(){
  const checked=[...document.querySelectorAll('.chk-panel:checked')];
  const dmsg=document.getElementById('panel-delete-msg');
  if(!checked.length){dmsg.textContent='No users selected.';dmsg.style.color='var(--amber)';return;}
  const seen=new Set();const targets=[];
  checked.forEach(cb=>{
    const c=_panelCandidates[+cb.dataset.idx];
    if(!seen.has(c.client_id)){seen.add(c.client_id);targets.push(c);}
  });
  const emailList=targets.map(t=>t.email).join('\n');
  if(!confirm(`Permanently delete ${targets.length} user(s) from the panel?\n\n${emailList}\n\nThis cannot be undone.`))return;
  const btn=document.getElementById('btn-panel-delete');
  btn.disabled=true;dmsg.textContent=`Deleting ${targets.length} user(s)…`;dmsg.style.color='var(--muted)';
  try{
    const r=await fetch('/api/cleanup/panel-execute',{method:'POST',
      headers:{'Content-Type':'application/json'},body:JSON.stringify({targets})});
    const j=await r.json();
    if(j.ok){
      const failed=j.results.filter(r=>!r.ok);
      const bp=j.backup_path?`\nBackup: ${j.backup_path}`:'';
      dmsg.textContent=failed.length
        ?`${j.deleted} deleted, ${failed.length} failed.${bp}`
        :`Done — ${j.deleted} user(s) deleted.${bp}`;
      dmsg.style.color=failed.length?'var(--amber)':'var(--green)';
      setTimeout(panelCleanupPreview,1200);
    }else{dmsg.textContent='Error: '+(j.error||'unknown');dmsg.style.color='var(--red)';}
  }catch(e){dmsg.textContent='Request failed';dmsg.style.color='var(--red)';}
  finally{btn.disabled=false;}
}
</script>
</body></html>"""

def _page_size_opts(current):
    c = int(current)
    sizes = [10, 20, 50, 100]
    if c not in sizes:
        sizes = sorted(set(sizes + [c]))
    return "".join(
        f'<option value="{s}"{" selected" if s==c else ""}>{s}</option>'
        for s in sizes
    )

def render_main():
    s  = get_all_settings()
    un = session.get("username", "")
    return (MAIN_HTML
            .replace("__BASE_STYLE__",    BASE_STYLE)
            .replace("__TOPBAR__",        topbar(page="dashboard",
                                                 refresh_sel=_refresh_select(s.get("dashboard_refresh","30")),
                                                 username=un))
            .replace("__COMMON_JS__",     COMMON_JS)
            .replace("__REFRESH_SEC__",   s.get("dashboard_refresh", "30"))
            .replace("__PAGE_SIZE__",     s.get("page_size", "20"))
            .replace("__PAGE_SIZE_OPTS__",_page_size_opts(s.get("page_size", "20")))
            .replace("__TZ__",            s.get("timezone", "Asia/Tehran"))
            .replace("__TRAFFIC_LOG__",   s.get("traffic_log_enabled", "0"))
            .replace("__ONLINE_LOG__",    s.get("online_log_enabled", "0"))
            .replace("__CSRF_TOKEN__",    _get_csrf_token()))

def render_user(email):
    s = get_all_settings()
    back = """<a href="/" class="btn" style="margin-left:5px">
      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <line x1="19" y1="12" x2="5" y2="12"/><polyline points="12 19 5 12 12 5"/>
      </svg>Back</a>"""
    un = session.get("username", "")
    return (USER_HTML
            .replace("__BASE_STYLE__",  BASE_STYLE)
            .replace("__TOPBAR_BACK__", topbar(extra=back, page="dashboard", username=un))
            .replace("__COMMON_JS__",   COMMON_JS)
            .replace("__TZ__",          s.get("timezone", "Asia/Tehran"))
            .replace("__EMAIL__",       email)
            .replace("__EMAIL_JSON__",  json.dumps(email))
            .replace("__CSRF_TOKEN__",  _get_csrf_token()))

def render_settings():
    s  = get_all_settings()
    c0 = "selected" if s.get("cleanup_enabled", "0") == "0" else ""
    c1 = "selected" if s.get("cleanup_enabled", "0") == "1" else ""
    un = session.get("username", "")
    return (SETTINGS_HTML
            .replace("__BASE_STYLE__",    BASE_STYLE)
            .replace("__TOPBAR__",        topbar(page="settings", username=un))
            .replace("__COMMON_JS__",     COMMON_JS)
            .replace("__TZ__",            s.get("timezone", "Asia/Tehran"))
            .replace("__PANEL_URL__",     s.get("panel_url", ""))
            .replace("__PANEL_USER__",    s.get("panel_user", ""))
            .replace("__PANEL_PASS__",    s.get("panel_pass", ""))
            .replace("__GRACE_MB__",      s.get("grace_mb", "100"))
            .replace("__CHECK_INTERVAL__",s.get("check_interval", "30"))
            .replace("__RESET_RATIO__",   s.get("reset_ratio", "0.5"))
            .replace("__OPT_AR_1__",      " selected" if s.get("auto_restart_xray","1")=="1" else "")
            .replace("__OPT_AR_0__",      " selected" if s.get("auto_restart_xray","1")=="0" else "")
            .replace("__TLS_EN_0__",      " selected" if s.get("tls_enabled","0")=="0" else "")
            .replace("__TLS_EN_1__",      " selected" if s.get("tls_enabled","0")=="1" else "")
            .replace("__TLS_DOMAIN__",    s.get("tls_domain",""))
            .replace("__TLS_CERT__",      s.get("tls_cert",""))
            .replace("__TLS_KEY__",       s.get("tls_key",""))
            .replace("__TLS_FIELDS_DISPLAY__", "block" if s.get("tls_enabled","0")=="1" else "none")
            .replace("__DASH_REFRESH__",  s.get("dashboard_refresh", "30"))
            .replace("__PAGE_SIZE__",     s.get("page_size", "20"))
            .replace("__CLEANUP_DAYS__",  s.get("cleanup_days", "7"))
            .replace("__CLEANUP_TIME__",  s.get("cleanup_time", "03:00"))
            .replace("__HISTORY_DAYS__",  s.get("history_days", "7"))
            .replace("__MAX_DB_MB__",     s.get("max_db_mb", "0"))
            .replace("__PANEL_CLN_TIME__", s.get("panel_cleanup_time", "00:00"))
            .replace("__PANEL_CLN_DAYS__", s.get("panel_cleanup_days", "7"))
            .replace("__PANEL_CLN_0__",   " selected" if s.get("panel_cleanup_enabled","0")=="0" else "")
            .replace("__PANEL_CLN_1__",   " selected" if s.get("panel_cleanup_enabled","0")=="1" else "")
            .replace("__C0__", c0).replace("__C1__", c1)
            .replace("__CSRF_TOKEN__",    _get_csrf_token()))

@app.route("/register", methods=["GET", "POST"])
def register():
    if count_admins() > 0:
        return redirect(url_for("login"))
    csrf_token = _get_csrf_token()
    error = username = ""
    if request.method == "POST":
        if not _csrf_ok():
            error = "Invalid request. Please refresh and try again."
        else:
            username = request.form.get("username", "").strip()
            pw  = request.form.get("password", "")
            pw2 = request.form.get("password2", "")
            if not username:       error = "Username is required."
            elif len(username)<3:  error = "Username must be at least 3 characters."
            elif not pw:           error = "Password is required."
            elif len(pw)<6:        error = "Password must be at least 6 characters."
            elif pw != pw2:        error = "Passwords do not match."
            else:
                create_admin(username, pw, role="superadmin")
                session.permanent    = True
                session["logged_in"] = True
                session["username"]  = username
                return redirect(url_for("index"))
    return render_template_string(
        REGISTER_HTML.replace("__STYLE__", BASE_STYLE), error=error, username=username, csrf_token=csrf_token
    )

@app.route("/login", methods=["GET", "POST"])
def login():
    if count_admins() == 0:
        return redirect(url_for("register"))
    if request.method == "GET" and session.get("logged_in"):
        return redirect(url_for("index"))
    csrf_token = _get_csrf_token()
    ip    = request.remote_addr or ""
    error = None
    if request.method == "POST":
        if not _csrf_ok():
            error = "Invalid request. Please refresh and try again."
        elif _is_rate_limited(ip):
            error = "Too many failed attempts. Please wait 5 minutes."
        else:
            u = request.form.get("username", "")
            p = request.form.get("password", "")
            if check_credentials(u, p):
                _clear_fail(ip)
                session.permanent    = True
                session["logged_in"] = True
                session["username"]  = u
                return redirect(url_for("index"))
            _record_fail(ip)
            error = "Wrong username or password."
    return render_template_string(
        LOGIN_HTML.replace("__STYLE__", BASE_STYLE), error=error, csrf_token=csrf_token
    )

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/")
@require_login
def index():
    return render_main()

@app.route("/user/<path:email>")
@require_login
def user_detail(email):
    return render_user(email)

@app.route("/settings")
@require_login
def settings():
    return render_settings()

@app.route("/static/<path:filename>")
def static_files(filename):
    return send_from_directory(STATIC_DIR, filename)

@app.route("/api/snapshot")
@require_login
def api_snapshot():
    return jsonify(latest_snapshot())

@app.route("/api/summary")
@require_login
def api_summary():
    return jsonify(_cached("summary", 30, snapshot_summary))

@app.route("/api/users")
@require_login
def api_users():
    page     = max(1, int(request.args.get("page",     1)))
    per_page = min(max(int(request.args.get("per_page", 10)), 5), 200)
    filter_  = request.args.get("filter", "all")
    search   = request.args.get("search", "")
    sort     = request.args.get("sort",   "total")
    order    = request.args.get("order",  "desc")
    key = f"users:{page}:{per_page}:{filter_}:{search}:{sort}:{order}"
    return jsonify(_cached(key, 15, lambda: paginated_users(page, per_page, filter_, search, sort, order)))

@app.route("/api/user/snapshot")
@require_login
def api_user_snapshot():
    return jsonify(user_snapshot(request.args.get("email", "")))

@app.route("/api/user/hourly")
@require_login
def api_user_hourly():
    email = request.args.get("email", "")
    gran  = min(max(int(request.args.get("gran",  30)),   10), 1440)
    hours = min(max(int(request.args.get("hours", 24)),    1),  168)
    return jsonify(user_hourly(email, hours=hours, bucket_min=gran))

@app.route("/api/restarts")
@require_login
def api_restarts():
    return jsonify(_cached("restarts", 30, recent_restarts))

@app.route("/api/handled")
@require_login
def api_handled():
    return jsonify(_cached("handled", 20, handled_list))

@app.route("/api/online")
@require_login
def api_online():
    count, emails = fetch_online()
    return jsonify({"count": count, "emails": emails})

@app.route("/api/online/durations")
@require_login
def api_online_durations():
    count, emails = fetch_online()
    now = time.time()
    result = []
    for email in emails:
        since    = _user_online_since.get(email, now)
        duration = int(now - since)
        result.append({"email": email, "since": int(since), "duration_sec": duration})
    result.sort(key=lambda x: -x["duration_sec"])
    return jsonify(result)

@app.route("/api/online/history")
@require_login
def api_online_history():
    hours  = min(max(int(request.args.get("hours", 24)), 1), 168)
    bucket = 900 if hours <= 6 else 1800 if hours <= 24 else 3600 if hours <= 72 else 7200
    cutoff = int(time.time()) - hours * 3600
    with traffic_db() as c:
        rows = c.execute(
            "SELECT (ts/:b)*:b AS t, ROUND(AVG(count)) AS cnt "
            "FROM online_log WHERE ts >= :c GROUP BY t ORDER BY t",
            {"b": bucket, "c": cutoff}
        ).fetchall()
    return jsonify([{"ts": r["t"], "count": int(r["cnt"])} for r in rows])

@app.route("/api/settings", methods=["POST"])
@require_login
def api_save_settings():
    data    = request.get_json() or {}
    allowed = {"panel_url","panel_user","panel_pass","grace_mb","check_interval",
               "reset_ratio","auto_restart_xray","cleanup_enabled","cleanup_days","cleanup_time",
               "timezone","dashboard_refresh","page_size","history_days","max_db_mb",
               "tls_enabled","tls_cert","tls_key","tls_domain",
               "online_log_enabled","traffic_log_enabled",
               "panel_cleanup_enabled","panel_cleanup_time","panel_cleanup_days"}
    try:
        for k, v in data.items():
            if k in allowed:
                set_setting(k, str(v))
        _tz_cache["ts"] = 0  # invalidate tz cache
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400

@app.route("/api/restart-dashboard", methods=["POST"])
@require_login
def api_restart_dashboard():
    import subprocess, threading
    def _do():
        import time as _t; _t.sleep(0.8)
        subprocess.run(["systemctl", "restart", "xui-dashboard"], check=False)
    threading.Thread(target=_do, daemon=True).start()
    return jsonify({"ok": True})

@app.route("/api/live/bandwidth")
@require_login
def api_live_bandwidth():
    return jsonify(get_live_bandwidth())

@app.route("/api/live/stats")
@require_login
def api_live_stats():
    bw = get_live_bandwidth()
    mu, mt = _read_mem()
    du, dt = _read_disk()
    return jsonify({
        "cpu":  _read_cpu_pct(),
        "mem":  {"current": mu, "total": mt},
        "disk": {"current": du, "total": dt},
        "bw":   {"up": round(bw["up"], 1), "down": round(bw["down"], 1)},
    })

@app.route("/api/data-range")
@require_login
def api_data_range():
    def _fetch():
        with traffic_db() as c:
            row = c.execute("SELECT MIN(ts) AS oldest, MAX(ts) AS newest, COUNT(*) AS cnt FROM snapshots").fetchone()
        oldest, newest, cnt = row["oldest"], row["newest"], row["cnt"]
        if not oldest:
            return {"hours": 0, "cnt": 0}
        return {"hours": round((newest - oldest) / 3600, 1), "cnt": cnt,
                "oldest": iran_fmt(oldest), "newest": iran_fmt(newest)}
    return jsonify(_cached("data_range", 30, _fetch))

@app.route("/api/debug/online")
@require_login
def api_debug_online():
    panel_url = get_setting("panel_url", "").rstrip("/")
    pu = get_setting("panel_user", "")
    pp = get_setting("panel_pass", "")
    out = {"panel_url": panel_url, "steps": []}
    try:
        s = _req.Session()
        lr = s.post(f"{panel_url}/login", json={"username": pu, "password": pp}, timeout=10)
        out["login_status"] = lr.status_code
        out["login_raw"]    = lr.text[:300]
        try: out["login_json"] = lr.json()
        except Exception: out["login_json"] = None

        r = s.get(f"{panel_url}/panel/api/inbounds/onlines", timeout=8)
        out["online_status"] = r.status_code
        out["online_raw"]    = r.text[:500]
        try: out["online_json"] = r.json()
        except Exception: out["online_json"] = None
    except Exception as e:
        out["error"] = str(e)
    return jsonify(out)

@app.route("/api/server-stats")
@require_login
def api_server_stats():
    return jsonify(fetch_server_stats())

@app.route("/api/server/data-range")
@require_login
def api_server_data_range():
    with traffic_db() as c:
        row = c.execute(
            "SELECT MIN(ts) AS oldest, MAX(ts) AS newest FROM server_snapshots"
        ).fetchone()
    if not row or not row["oldest"]:
        return jsonify({"hours": 0})
    return jsonify({"hours": round((row["newest"] - row["oldest"]) / 3600, 1)})

@app.route("/api/server/history")
@require_login
def api_server_history():
    metric = request.args.get("metric", "cpu")
    hours  = min(max(int(request.args.get("hours", 24)), 1), 168)
    gran   = min(max(int(request.args.get("gran",  60)), 10), 1440)
    if metric not in ("cpu", "ram", "disk", "net"):
        return jsonify([])
    return jsonify(server_history(metric=metric, hours=hours, bucket_min=gran))

@app.route("/api/traffic/hourly")
@require_login
def api_traffic_hourly():
    hours = min(max(int(request.args.get("hours", 24)), 1), 720)
    gran  = min(max(int(request.args.get("gran",  60)), 10), 1440)
    return jsonify(_cached(f"traffic_hourly:{hours}:{gran}", 60, lambda: total_hourly(hours=hours, bucket_min=gran)))

@app.route("/api/traffic/top-users")
@require_login
def api_traffic_top_users():
    hours = min(max(int(request.args.get("hours", 24)), 1), 720)
    limit = min(max(int(request.args.get("limit", 15)), 1), 100)
    ttl = 300 if hours > 24 else 60
    return jsonify(_cached(f"top_users:{hours}:{limit}", ttl, lambda: traffic_top_users(hours, limit)))

@app.route("/api/traffic/export")
@require_login
def api_traffic_export():
    import io
    hours = min(max(int(request.args.get("hours", 24)), 1), 720)
    rows  = traffic_top_users(hours, limit=1000)
    out   = io.StringIO()
    out.write("email,bytes,gb\n")
    for r in rows:
        out.write(f"{r['email']},{r['bytes']},{r['gb']}\n")
    from flask import Response
    return Response(out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment;filename=traffic_{hours}h.csv"})

@app.route("/api/db-stats")
@require_login
def api_db_stats():
    t_size = os.path.getsize(TRAFFIC_DB) if os.path.exists(TRAFFIC_DB) else 0
    a_size = os.path.getsize(APP_DB)     if os.path.exists(APP_DB)     else 0
    with traffic_db() as c:
        count  = c.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
        oldest = c.execute("SELECT MIN(ts) FROM snapshots").fetchone()[0]
    return jsonify({
        "traffic_db_mb":  round(t_size / 1024**2, 2),
        "app_db_mb":      round(a_size / 1024**2, 2),
        "snapshot_count": count,
        "oldest":         iran_fmt(oldest) if oldest else None,
    })

@app.route("/api/vacuum-db", methods=["POST"])
@require_login
def api_vacuum_db():
    try:
        before_t = os.path.getsize(TRAFFIC_DB) if os.path.exists(TRAFFIC_DB) else 0
        before_a = os.path.getsize(APP_DB)     if os.path.exists(APP_DB)     else 0
        with traffic_db() as c:
            c.execute("VACUUM")
        with app_db() as c:
            c.execute("VACUUM")
        after_t = os.path.getsize(TRAFFIC_DB) if os.path.exists(TRAFFIC_DB) else 0
        after_a = os.path.getsize(APP_DB)     if os.path.exists(APP_DB)     else 0
        saved = round((before_t + before_a - after_t - after_a) / 1024**2, 2)
        return jsonify({"ok": True, "saved_mb": saved})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/clear-history", methods=["POST"])
@require_login
def api_clear_history():
    data      = request.get_json() or {}
    keep_days = max(int(data.get("keep_days", 0)), 0)
    try:
        with traffic_db() as c:
            if keep_days > 0:
                cutoff  = int(time.time()) - keep_days * 86400
                deleted = c.execute(
                    "DELETE FROM snapshots WHERE ts < ?", (cutoff,)
                ).rowcount
            else:
                deleted = c.execute("DELETE FROM snapshots").rowcount
            c.execute("VACUUM")
        return jsonify({"ok": True, "deleted": deleted})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/cleanup/panel-preview")
@require_login
def api_panel_preview():
    old_days = max(1, int(request.args.get("old_days", 30)))
    candidates, err = fetch_panel_candidates(old_days)
    if err:
        return jsonify({"ok": False, "error": err}), 500
    return jsonify({"ok": True, "candidates": candidates})

@app.route("/api/cleanup/panel-execute", methods=["POST"])
@require_login
def api_panel_execute():
    body    = request.get_json() or {}
    targets = body.get("targets", [])
    if not targets:
        return jsonify({"ok": False, "error": "No targets specified"}), 400
    backup_path = _backup_deleted_users(targets)
    results = []
    for t in targets:
        ok, msg = delete_panel_client(int(t["inbound_id"]), str(t["client_id"]))
        results.append({"email": t.get("email", ""), "ok": ok, "msg": msg})
    deleted = sum(1 for r in results if r["ok"])
    return jsonify({"ok": True, "deleted": deleted, "results": results, "backup_path": backup_path})

if __name__ == "__main__":
    init_app_db()
    with app_db() as _c:
        _sk = _c.execute("SELECT value FROM settings WHERE key='secret_key'").fetchone()
        app.secret_key = _sk["value"] if _sk else "xui-monitor-2026-fallback"
    _ensure_online_log()
    tls_en   = get_setting("tls_enabled", "0") == "1"
    tls_cert = get_setting("tls_cert", "").strip()
    tls_key  = get_setting("tls_key",  "").strip()
    ssl_ctx  = (tls_cert, tls_key) if tls_en and tls_cert and tls_key else None
    if ssl_ctx:
        import logging as _log
        _log.getLogger("werkzeug").info("Starting with HTTPS (cert=%s)", tls_cert)
    port = int(get_setting("port", "5000") or 5000)
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True, ssl_context=ssl_ctx)
