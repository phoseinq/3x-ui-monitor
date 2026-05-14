#!/usr/bin/env python3
"""
3x-ui traffic monitor.
Settings (panel URL, credentials, thresholds) are read from app.db at runtime
and cached for 60 s — changes saved in the dashboard take effect automatically.
"""

import json
import logging
import sqlite3
import time
from pathlib import Path

import requests
from requests.exceptions import RequestException

COOKIE_FILE = "/opt/xui-monitor/session.json"
DB_FILE     = "/opt/xui-monitor/traffic.db"
APP_DB      = "/opt/xui-monitor/app.db"
LOG_FILE    = "/opt/xui-monitor/monitor.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()],
)
log = logging.getLogger(__name__)

_cfg_cache: dict = {"ts": 0.0, "data": {}}

_DEFAULTS = {
    "panel_url":      "",
    "panel_user":     "",
    "panel_pass":     "",
    "check_interval": "30",
    "grace_mb":       "100",
    "reset_ratio":      "0.5",
    "auto_restart_xray":"1",
}

def _load_cfg() -> dict:
    now = time.time()
    if now - _cfg_cache["ts"] < 60:
        return _cfg_cache["data"]
    try:
        with sqlite3.connect(APP_DB, timeout=30) as c:
            rows = c.execute("SELECT key, value FROM settings").fetchall()
        data = {r[0]: r[1] for r in rows}
    except Exception:
        data = dict(_cfg_cache["data"])   # keep stale on DB error
    _cfg_cache.update({"ts": now, "data": data})
    return data

def _cfg(key: str) -> str:
    return _load_cfg().get(key, _DEFAULTS.get(key, ""))

def panel_url()      -> str:   return _cfg("panel_url").rstrip("/")
def panel_user()     -> str:   return _cfg("panel_user")
def panel_pass()     -> str:   return _cfg("panel_pass")
def check_interval() -> int:   return max(10, int(_cfg("check_interval") or 30))
def grace_bytes()    -> float: return max(0, int(_cfg("grace_mb") or 100)) * 1024 * 1024
def reset_ratio()    -> float: return float(_cfg("reset_ratio") or 0.5)

def init_db():
    with sqlite3.connect(DB_FILE, timeout=30) as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("""
            CREATE TABLE IF NOT EXISTS snapshots (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                ts      INTEGER NOT NULL,
                email   TEXT    NOT NULL,
                up      REAL    NOT NULL DEFAULT 0,
                down    REAL    NOT NULL DEFAULT 0,
                total   REAL    NOT NULL DEFAULT 0,
                quota   REAL    NOT NULL DEFAULT 0,
                expired INTEGER NOT NULL DEFAULT 0,
                enable  INTEGER NOT NULL DEFAULT 0
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS handled (
                email            TEXT    PRIMARY KEY,
                triggered_at     INTEGER NOT NULL DEFAULT (unixepoch()),
                total_at_trigger REAL    NOT NULL DEFAULT 0
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS restarts (
                id     INTEGER PRIMARY KEY AUTOINCREMENT,
                ts     INTEGER NOT NULL,
                reason TEXT    NOT NULL
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_snap_ts    ON snapshots(ts)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_snap_email ON snapshots(email)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_snap_ts_email_total ON snapshots(ts, email, total)")

def save_snapshot(rows: list[dict]):
    ts = int(time.time())
    with sqlite3.connect(DB_FILE, timeout=30) as c:
        c.executemany(
            "INSERT INTO snapshots(ts,email,up,down,total,quota,expired,enable) "
            "VALUES(?,?,?,?,?,?,?,?)",
            [(ts, r["email"], r["up"], r["down"], r["total"],
              r["quota"], int(r["expired"]), int(r["enable"])) for r in rows],
        )

def log_restart(reason: str):
    with sqlite3.connect(DB_FILE, timeout=30) as c:
        c.execute("INSERT INTO restarts(ts,reason) VALUES(?,?)", (int(time.time()), reason))

def get_handled() -> set[str]:
    with sqlite3.connect(DB_FILE, timeout=30) as c:
        return {r[0] for r in c.execute("SELECT email FROM handled").fetchall()}

def add_handled(clients: list[dict]):
    with sqlite3.connect(DB_FILE, timeout=30) as c:
        c.executemany(
            "INSERT OR REPLACE INTO handled(email, triggered_at, total_at_trigger) "
            "VALUES(?, unixepoch(), ?)",
            [(cl["email"], cl["total"]) for cl in clients],
        )

def cleanup_handled(clients: list[dict]):
    handled = get_handled()
    if not handled:
        return
    ratio   = reset_ratio()
    current = {c["email"]: c for c in clients}
    renewed = [
        email for email in handled
        if (u := current.get(email))
        and u["quota"] > 0
        and u["total"] < u["quota"] * ratio
    ]
    if renewed:
        with sqlite3.connect(DB_FILE, timeout=30) as c:
            c.executemany("DELETE FROM handled WHERE email=?", [(e,) for e in renewed])
        log.info("Renewed users removed from handled: %s", ", ".join(renewed))

_session = requests.Session()

def _new_session() -> requests.Session:
    global _session
    s = requests.Session()
    s.headers.update({"Connection": "close"})
    _session = s
    return _session

def _save_cookie():
    Path(COOKIE_FILE).parent.mkdir(parents=True, exist_ok=True)
    Path(COOKIE_FILE).write_text(json.dumps(dict(_session.cookies)))

def _load_cookie() -> bool:
    try:
        _session.cookies.update(json.loads(Path(COOKIE_FILE).read_text()))
        return True
    except Exception:
        return False

def login() -> bool:
    _new_session()
    try:
        r = _session.post(
            f"{panel_url()}/login",
            json={"username": panel_user(), "password": panel_pass()},
            timeout=30,
        )
        if r.json().get("success"):
            _save_cookie()
            log.info("Login successful, cookie cached.")
            return True
        log.error("Login failed: %s", r.json())
    except RequestException as e:
        log.error("Login request failed: %s", e)
    return False

def api_get(path: str) -> dict | None:
    base = panel_url()
    for attempt in range(2):
        try:
            r    = _session.get(f"{base}{path}", timeout=(10, 60))
            data = r.json()
            if r.status_code == 401 or (not data.get("success") and attempt == 0):
                log.info("Session expired, re-authenticating...")
                if not login():
                    return None
                continue
            return data
        except RequestException as e:
            log.error("Request error %s: %s", path, e)
            if attempt == 0:
                log.info("Retrying after error — re-authenticating...")
                if login():
                    continue
            return None
    return None

def restart_xray(reason: str) -> bool:
    log.warning("Restarting Xray core — %s", reason)
    try:
        r  = _session.post(f"{panel_url()}/panel/api/server/restartXrayService", timeout=15)
        ok = r.json().get("success", False)
    except Exception as e:
        log.error("Xray restart API failed: %s", e)
        ok = False
    if ok:
        log.info("Xray core restarted successfully.")
        log_restart(reason)
    else:
        log.error("Xray restart failed — will retry next cycle.")
    return ok

def parse_clients(inbounds: list) -> list[dict]:
    now_ms  = int(time.time() * 1000)
    clients = []
    for ib in inbounds:
        stats = {s["email"]: s for s in (ib.get("clientStats") or [])}
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
            clients.append({
                "email":   email,
                "up":      up,
                "down":    down,
                "total":   up + down,
                "quota":   quota,
                "expired": bool(exp_ms and exp_ms < now_ms),
                "enable":  bool(c.get("enable", True)),
            })
    return clients

def check_once() -> bool:
    data = api_get("/panel/api/inbounds/list")
    if not data or not data.get("success"):
        log.warning("Failed to fetch inbounds: %s", data)
        return False

    clients = parse_clients(data.get("obj", []))
    save_snapshot(clients)
    cleanup_handled(clients)

    grace   = grace_bytes()
    handled = get_handled()
    new_offenders = [
        c for c in clients
        if c["enable"]
        and c["quota"] > 0
        and c["total"] > c["quota"] + grace
        and c["email"] not in handled
    ]

    if not new_offenders:
        return False

    names = ", ".join(f"{c['email']} ({c['total']/c['quota']*100:.0f}%)" for c in new_offenders)
    log.warning("New overquota clients (%d): %s", len(new_offenders), names)
    if _cfg("auto_restart_xray") != "0":
        restart_xray(f"overquota: {names}")
    else:
        log.info("Auto-restart disabled — skipping Xray restart.")
    add_handled(new_offenders)
    return True

def main():
    init_db()
    _load_cookie() or login()
    log.info(
        "Monitor started — interval=%ds  grace=%.0fMB  reset_ratio=%.2f",
        check_interval(), grace_bytes() / 1024**2, reset_ratio(),
    )
    while True:
        try:
            check_once()
        except Exception as e:
            log.exception("Unexpected error: %s", e)
        time.sleep(check_interval())

if __name__ == "__main__":
    main()
