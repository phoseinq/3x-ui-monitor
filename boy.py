#!/usr/bin/env python3
"""
Boy — management CLI for xui-dashboard & xui-monitor.

Run without arguments for interactive menu, or pass a command directly:

  boy                        interactive menu
  boy status                 service status + settings overview
  boy start                  start both services
  boy stop                   stop both services
  boy restart                restart both services
  boy remove                 stop, disable and delete service files
  boy user   <new-username>  change dashboard login username
  boy pass   <new-password>  change dashboard login password
  boy port   <number>        change dashboard port (auto-restart)
  boy https  on  [--cert <path> --key <path>]   enable HTTPS
  boy https  off             switch back to HTTP
  boy help   [command]       show help (optionally for one command)
"""

import hashlib
import os
import sqlite3
import subprocess
import sys
import textwrap

APP_DB   = "/opt/xui-monitor/app.db"
SVC_DASH = "xui-dashboard"
SVC_MON  = "xui-monitor"

# ── ANSI ──────────────────────────────────────────────────────────────────────
RED  = "\033[0;31m";  GRN  = "\033[0;32m";  YLW  = "\033[0;33m"
BLU  = "\033[0;34m";  CYN  = "\033[0;36m";  DIM  = "\033[2m"
BLD  = "\033[1m";     RST  = "\033[0m";      UL   = "\033[4m"

def _c(color, text): return f"{color}{text}{RST}"
def ok(msg):    print(f"  {_c(GRN,'✔')}  {msg}")
def fail(msg):  print(f"  {_c(RED,'✘')}  {msg}"); sys.exit(1)
def info(msg):  print(f"  {_c(CYN,'→')}  {msg}")
def warn(msg):  print(f"  {_c(YLW,'!')}  {msg}")
def head(msg):  print(f"\n{BLD}{_c(CYN,'┌')} {msg}{RST}")
def sep():      print(f"  {_c(DIM,'─'*46)}")

def ask(prompt, default="") -> str:
    hint = f" {_c(DIM,'['+default+']')}" if default else ""
    try:
        val = input(f"  {_c(YLW,'?')}  {prompt}{hint}: ").strip()
        return val or default
    except (KeyboardInterrupt, EOFError):
        print(); sys.exit(0)

def confirm(prompt) -> bool:
    try:
        ans = input(f"  {_c(YLW,'?')}  {prompt} {_c(DIM,'[y/N]')}: ").strip().lower()
        return ans in ("y", "yes")
    except (KeyboardInterrupt, EOFError):
        print(); return False

# ── DB ────────────────────────────────────────────────────────────────────────
def _db():
    c = sqlite3.connect(APP_DB)
    c.row_factory = sqlite3.Row
    return c

def db_get(key: str, default: str = "") -> str:
    try:
        with _db() as c:
            row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row[0] if row else default
    except Exception:
        return default

def db_set(key: str, value: str):
    try:
        with _db() as c:
            c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (key, value))
    except Exception as e:
        fail(f"DB write failed: {e}")

def db_get_admin() -> tuple[str, str]:
    try:
        with _db() as c:
            row = c.execute("SELECT username, password FROM admin_users LIMIT 1").fetchone()
        return (row["username"], row["password"]) if row else ("", "")
    except Exception:
        return ("", "")

def db_set_username(old: str, new: str):
    with _db() as c:
        c.execute("UPDATE admin_users SET username=? WHERE username=?", (new, old))

def db_set_password(new_pass: str):
    salt   = os.urandom(16).hex()
    h      = hashlib.pbkdf2_hmac("sha256", new_pass.encode(), salt.encode(), 260_000)
    hashed = f"pbkdf2${salt}${h.hex()}"
    with _db() as c:
        c.execute("UPDATE admin_users SET password=?", (hashed,))

# ── systemctl ─────────────────────────────────────────────────────────────────
def _svc(*args) -> tuple[int, str]:
    r = subprocess.run(["systemctl", *args], capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr).strip()

def is_active(svc):  return _svc("is-active",  "--quiet", svc)[0] == 0
def is_enabled(svc): return _svc("is-enabled", "--quiet", svc)[0] == 0

def svc_do(svc: str, action: str):
    code, out = _svc(action, svc)
    label = svc.replace("xui-", "")
    if code == 0:
        ok(f"{_c(BLD, label):25}  {action}ed")
    else:
        warn(f"{label}: {out or 'no output'}")

# ── commands ──────────────────────────────────────────────────────────────────

def cmd_status():
    head("Service status")
    sep()
    for svc in (SVC_DASH, SVC_MON):
        active  = _c(GRN, "● active")   if is_active(svc)  else _c(RED, "○ inactive")
        enabled = _c(GRN, "enabled")    if is_enabled(svc) else _c(YLW, "disabled")
        label   = svc.replace("xui-", "")
        print(f"  {_c(BLD, label):<22}  {active}  /  {enabled}")
    sep()

    head("Dashboard settings")
    sep()
    username, _ = db_get_admin()
    port        = db_get("port",       "5000")
    tls         = db_get("tls_enabled","0")
    tls_cert    = db_get("tls_cert",   "")
    tls_key     = db_get("tls_key",    "")
    domain      = db_get("tls_domain", "")
    scheme      = "https" if tls == "1" else "http"
    ip_hint     = f"  {_c(DIM, f'→ {scheme}://YOUR_IP:{port}/')}"

    def row(label, val): print(f"  {_c(DIM, label+'  ')}{val}")

    row("Username  ", _c(BLD, username) if username else _c(RED, "(not set)"))
    row("Port      ", _c(BLD, port) + ip_hint)
    if tls == "1":
        row("HTTPS     ", _c(GRN, "ON  ✔"))
        row("  cert    ", tls_cert or _c(RED, "(not set)"))
        row("  key     ", tls_key  or _c(RED, "(not set)"))
        if domain:
            row("  domain  ", domain)
    else:
        row("HTTPS     ", _c(YLW, "OFF  (HTTP)"))
    sep()
    print()


def cmd_start():
    head("Start services"); sep()
    svc_do(SVC_DASH, "start")
    svc_do(SVC_MON,  "start")
    print()

def cmd_stop():
    head("Stop services"); sep()
    svc_do(SVC_DASH, "stop")
    svc_do(SVC_MON,  "stop")
    print()

def cmd_restart():
    head("Restart services"); sep()
    svc_do(SVC_DASH, "restart")
    svc_do(SVC_MON,  "restart")
    print()


def cmd_remove():
    head("Remove services"); sep()
    warn("Both xui-dashboard and xui-monitor services will be")
    warn("stopped, disabled and their .service files deleted.")
    warn(f"Files in {_c(BLD,'/opt/xui-monitor/')} will {_c(UL,'NOT')} be deleted.")
    sep()
    if not confirm("Are you sure you want to remove the services?"):
        info("Aborted — nothing changed."); print(); return

    for svc in (SVC_DASH, SVC_MON):
        _svc("stop",    svc)
        _svc("disable", svc)
        svc_file = f"/etc/systemd/system/{svc}.service"
        if os.path.exists(svc_file):
            try:
                os.remove(svc_file)
                ok(f"Deleted {svc_file}")
            except PermissionError:
                fail(f"Permission denied — run as root: sudo boy remove")
    _svc("daemon-reload")
    ok("Services removed.  Data at /opt/xui-monitor/ is intact.")
    print()


def cmd_user(new_name: str = ""):
    head("Change username"); sep()
    old, _ = db_get_admin()
    if not old:
        fail("No admin user found in the database.")
    if not new_name:
        new_name = ask(f"New username", default=old)
    if new_name == old:
        info("Username unchanged."); print(); return
    db_set_username(old, new_name)
    ok(f"Username  {_c(DIM,old)}  →  {_c(BLD,new_name)}")
    info("Takes effect on next login — no restart needed.")
    print()


def cmd_pass(new_pass: str = ""):
    head("Change password"); sep()
    if not new_pass:
        import getpass
        new_pass  = getpass.getpass(f"  {_c(YLW,'?')}  New password: ")
        new_pass2 = getpass.getpass(f"  {_c(YLW,'?')}  Confirm     : ")
        if new_pass != new_pass2:
            fail("Passwords do not match.")
    if len(new_pass) < 6:
        fail("Password must be at least 6 characters.")
    db_set_password(new_pass)
    ok("Password updated  (PBKDF2-SHA256)")
    info("Takes effect on next login — no restart needed.")
    print()


def cmd_port(new_port: str = ""):
    head("Change port"); sep()
    old = db_get("port", "5000")
    if not new_port:
        new_port = ask("New port", default=old)
    try:
        p = int(new_port)
        if not (1 <= p <= 65535): raise ValueError
    except ValueError:
        fail(f"Invalid port: {new_port!r}  (must be 1–65535)")
    if str(p) == old:
        info("Port unchanged."); print(); return
    db_set("port", str(p))
    ok(f"Port  {_c(DIM,old)}  →  {_c(BLD,str(p))}")
    info("Restarting dashboard to apply…")
    svc_do(SVC_DASH, "restart")
    print()


def cmd_https(args: list):
    head("HTTPS / TLS"); sep()
    if not args:
        sub = ask("Enable or disable HTTPS? (on/off)", default="on").lower()
    else:
        sub = args[0].lower()
        args = args[1:]

    if sub == "off":
        db_set("tls_enabled", "0")
        ok("HTTPS disabled  →  HTTP mode")
        info("Restarting dashboard…")
        svc_do(SVC_DASH, "restart")
        print(); return

    if sub == "on":
        cert = key = ""
        i = 0
        while i < len(args):
            if args[i] in ("--cert", "-c") and i+1 < len(args): cert = args[i+1]; i += 2
            elif args[i] in ("--key",  "-k") and i+1 < len(args): key  = args[i+1]; i += 2
            else: i += 1

        ex_cert = db_get("tls_cert", "")
        ex_key  = db_get("tls_key",  "")

        if not cert:
            cert = ask("Certificate path (fullchain.pem)", default=ex_cert)
        if not key:
            key  = ask("Key path (privkey.pem)",           default=ex_key)

        if not cert or not key:
            fail("Both certificate and key paths are required.")
        if not os.path.isfile(cert):
            fail(f"Certificate file not found:\n     {cert}")
        if not os.path.isfile(key):
            fail(f"Key file not found:\n     {key}")

        db_set("tls_enabled", "1")
        db_set("tls_cert", cert)
        db_set("tls_key",  key)
        ok("HTTPS enabled")
        print(f"  {_c(DIM,'  cert')}  {cert}")
        print(f"  {_c(DIM,'  key ')}  {key}")
        info("Restarting dashboard…")
        svc_do(SVC_DASH, "restart")
        print(); return

    fail(f"Unknown option: {sub!r}  — use  on  or  off")


# ── help ──────────────────────────────────────────────────────────────────────

HELP_TOPICS = {
"status": """\
  boy status

  Shows the current state of both services (active/inactive, enabled/disabled)
  and a summary of key dashboard settings: username, port, HTTPS status.""",

"start": """\
  boy start

  Starts xui-dashboard and xui-monitor if they are not already running.""",

"stop": """\
  boy stop

  Stops both services. The dashboard becomes unreachable until started again.""",

"restart": """\
  boy restart

  Restarts both services. Useful after editing config files manually.
  The dashboard briefly goes offline during the restart (~2 s).""",

"remove": """\
  boy remove

  Stops, disables and deletes the systemd .service files for both services.
  Files in /opt/xui-monitor/ are NOT deleted — your data is safe.
  You will be asked to confirm before anything is deleted.""",

"user": """\
  boy user [new-username]

  Changes the dashboard login username.
  If new-username is omitted you will be prompted.
  No service restart is needed — takes effect on the next login.""",

"pass": """\
  boy pass [new-password]

  Changes the dashboard login password (PBKDF2-SHA256 hashed).
  If new-password is omitted you will be prompted securely (no echo).
  No service restart is needed — takes effect on the next login.""",

"port": """\
  boy port [number]

  Changes the port the dashboard listens on (default: 5000).
  If number is omitted you will be prompted.
  The dashboard service is restarted automatically to apply the change.""",

"https": """\
  boy https on  [--cert /path/fullchain.pem --key /path/privkey.pem]
  boy https off

  on:  Enables HTTPS using the given SSL certificate and key files.
       Paths must be absolute and readable by the service user (root).
       If --cert / --key are omitted you will be prompted; previously
       saved paths are offered as defaults.
       The service is restarted automatically.

  off: Switches back to plain HTTP and restarts the service.""",
}

def cmd_help(topic: str = ""):
    topic = topic.lower().strip()
    if topic and topic in HELP_TOPICS:
        head(f"Help: {topic}"); sep()
        print(textwrap.dedent(HELP_TOPICS[topic]))
        print()
        return

    head("Boy — xui-dashboard management CLI"); sep()
    print(f"""
  {BLD}Usage:{RST}  boy {_c(CYN,'<command>')} [arguments]
         boy  {_c(DIM,'(no args — interactive menu)')}

  {BLD}Commands:{RST}

    {_c(CYN,'status')}              service status + settings overview
    {_c(CYN,'start')}               start both services
    {_c(CYN,'stop')}                stop both services
    {_c(CYN,'restart')}             restart both services
    {_c(CYN,'remove')}              stop, disable and delete service files

    {_c(CYN,'user')}  <new-name>    change dashboard login username
    {_c(CYN,'pass')}  <new-pass>    change dashboard login password
    {_c(CYN,'port')}  <number>      change dashboard port  (auto-restart)

    {_c(CYN,'https')} on   [--cert <path> --key <path>]
    {_c(CYN,'https')} off           toggle HTTPS / HTTP

    {_c(CYN,'help')}  [command]     show this help or help for a command

  {BLD}Examples:{RST}

    boy status
    boy restart
    boy user admin
    boy port 8443
    boy https on --cert /root/fullchain.pem --key /root/privkey.pem
    boy https off
    boy help https
""")


# ── interactive menu ──────────────────────────────────────────────────────────

MENU_ITEMS = [
    ("Status",           lambda: cmd_status()),
    ("Restart services", lambda: cmd_restart()),
    ("Start services",   lambda: cmd_start()),
    ("Stop services",    lambda: cmd_stop()),
    ("Change username",  lambda: cmd_user()),
    ("Change password",  lambda: cmd_pass()),
    ("Change port",      lambda: cmd_port()),
    ("Enable HTTPS",     lambda: cmd_https(["on"])),
    ("Disable HTTPS",    lambda: cmd_https(["off"])),
    ("Remove services",  lambda: cmd_remove()),
    ("Help",             lambda: cmd_help()),
]

def interactive():
    while True:
        head("Boy — xui-dashboard CLI"); sep()
        for i, (label, _) in enumerate(MENU_ITEMS, 1):
            num = _c(CYN, f"{i:>2}")
            print(f"  {num}  {label}")
        print(f"  {_c(DIM,' q')}  Quit")
        sep()

        choice = ask("Select").lower()
        if choice in ("q", "quit", "exit", ""):
            print(); break
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(MENU_ITEMS):
                MENU_ITEMS[idx][1]()
            else:
                warn("Invalid choice — enter a number from the list.\n")
        except ValueError:
            warn("Invalid choice.\n")


# ── entry point ───────────────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]
    if not args:
        interactive(); return

    cmd  = args[0].lower()
    rest = args[1:]

    dispatch = {
        "status":  lambda: cmd_status(),
        "start":   lambda: cmd_start(),
        "stop":    lambda: cmd_stop(),
        "restart": lambda: cmd_restart(),
        "remove":  lambda: cmd_remove(),
        "user":    lambda: cmd_user(rest[0] if rest else ""),
        "pass":    lambda: cmd_pass(rest[0] if rest else ""),
        "port":    lambda: cmd_port(rest[0] if rest else ""),
        "https":   lambda: cmd_https(rest),
        "help":    lambda: cmd_help(rest[0] if rest else ""),
    }

    if cmd in dispatch:
        dispatch[cmd]()
    else:
        warn(f"Unknown command: {cmd!r}")
        cmd_help()
        sys.exit(1)

if __name__ == "__main__":
    main()
