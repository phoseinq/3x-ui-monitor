<div align="center">

# 📊 3x-ui Monitor

<img src="https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python">
<img src="https://img.shields.io/badge/Flask-000000?style=for-the-badge&logo=flask&logoColor=white" alt="Flask">
<img src="https://img.shields.io/badge/3x--ui-Compatible-orange?style=for-the-badge" alt="3x-ui">
<img src="https://img.shields.io/badge/license-MIT-blue.svg?style=for-the-badge" alt="License">

**داشبورد مانیتورینگ ترافیک برای پنل‌های 3x-ui**

[English](#english) · [فارسی](#فارسی)

---

<img width="2559" height="1363" alt="image" src="https://github.com/user-attachments/assets/d5eb19c3-1fe0-4d0a-98f8-18e47b9bf77b" />



</div>

---

## English

### About

**3x-ui Monitor** is a self-hosted web dashboard for [3x-ui](https://github.com/MHSanaei/3x-ui) VPN panels. It shows real-time traffic, online users, server health, and handles automatic quota enforcement — all from a clean dark UI running on your own server.

---

### Features

- 📈 **Traffic charts** — Hourly usage graphs per user and total
- 🟢 **Online users** — Live detection with duration tracking, sorted by longest connected
- 🖥️ **Server health** — CPU, RAM, disk, and live bandwidth gauges
- 🔄 **Auto-restart Xray** — Triggers when users exceed their quota (configurable grace allowance)
- 🧹 **Panel cleanup** — Preview and bulk-delete expired / over-limit / aged users directly from the panel
- ⏰ **Scheduled cleanup** — Nightly auto-delete of aged accounts at a time you choose
- 🔒 **HTTPS support** — Optional TLS with your own certificate
- 🌐 **Timezone-aware** — All timestamps shown in your configured timezone
- 👤 **Multi-admin** — PBKDF2-hashed admin accounts

---

### Requirements & libraries

**Third-party packages** (installed automatically by the installer):

| Package | Version | Purpose |
|---|---|---|
| `flask` | latest | Web framework — serves the dashboard UI and API routes |
| `requests` | latest | HTTP client — communicates with the 3x-ui panel API |
| `tzdata` | latest | Timezone database — needed for `zoneinfo` on some older Linux systems |

**Standard library** (built into Python, no install needed):

| Module | Purpose |
|---|---|
| `sqlite3` | Local database for traffic snapshots and settings |
| `hashlib` | PBKDF2 password hashing for admin accounts |
| `threading` | Background threads for monitor and auto-cleanup |
| `zoneinfo` | Timezone-aware datetime handling (Python 3.9+) |
| `logging` | Structured log output for the monitor service |
| `json` | Serialising panel API responses and session cookies |
| `pathlib` | File path handling |
| `datetime` | Timestamp formatting |
| `collections` | `defaultdict` for traffic aggregation |
| `time`, `os`, `functools` | Utilities |

> The installer handles all third-party packages. You only need `python3` (3.9+) pre-installed.

---

### Installation

Run this single command on your Ubuntu / Debian server (as root):

```bash
curl -fsSL https://raw.githubusercontent.com/phoseinq/3x-ui/main/install.sh | sudo bash
```

The script will:
1. Install `flask` and `requests` (via pip, falls back to apt if needed)
2. Download `dashboard.py` and `monitor.py` to `/opt/xui-monitor/`
3. Generate a random secret key
4. Create and start two systemd services: `xui-dashboard` and `xui-monitor`
5. Print your dashboard URL

---

### First-time setup

1. Open `http://YOUR_SERVER_IP:5000` in your browser
2. Register your admin account (first visit only)
3. Go to **Settings** → enter your 3x-ui panel URL, username, and password

---

### Settings reference

| Setting | Description |
|---|---|
| Panel URL | Full URL to your 3x-ui panel (e.g. `http://1.2.3.4:2096/path`) |
| Panel User / Pass | Your 3x-ui login credentials |
| Check Interval | How often the monitor polls the panel (seconds) |
| Grace MB | Extra traffic allowed after quota before Xray restarts |
| Auto-restart Xray | Restart Xray core when a user exceeds their quota |
| Timezone | Timezone for all displayed times (e.g. `Asia/Tehran`) |
| TLS / HTTPS | Enable HTTPS with a custom certificate path |
| Auto Cleanup | Nightly deletion of expired records from local DB |
| Panel Cleanup | Preview and delete expired / over-limit users from the panel |

---

### Services

| Service | Role |
|---|---|
| `xui-dashboard` | Flask web UI — listens on port 5000 |
| `xui-monitor` | Background poller — checks the panel every N seconds, restarts Xray on quota breach |

Both services start on boot and restart automatically on failure.

---

### CLI commands

```bash
# Live dashboard logs
journalctl -u xui-dashboard -f

# Live monitor logs
journalctl -u xui-monitor -f

# Restart both services
systemctl restart xui-dashboard xui-monitor

# Stop both services
systemctl stop xui-dashboard xui-monitor

# Uninstall
systemctl disable --now xui-dashboard xui-monitor && rm -rf /opt/xui-monitor
```

---

### License

MIT — see [LICENSE](LICENSE).

---

<div dir="rtl" align="right">

## فارسی

### درباره پروژه

**3x-ui Monitor** یک داشبورد تحت‌وب است که روی سرور خودتان نصب می‌شود و به پنل [3x-ui](https://github.com/MHSanaei/3x-ui) شما متصل می‌گردد. از طریق این داشبورد می‌توانید مصرف ترافیک کاربران، کاربران آنلاین، وضعیت سرور، و مدیریت خودکار کوتا را در یک رابط تاریک و ساده دنبال کنید.

---

### قابلیت‌ها

- 📈 **نمودار ترافیک** — گراف مصرف ساعتی به تفکیک هر کاربر و مجموع کل
- 🟢 **کاربران آنلاین** — شناسایی لحظه‌ای با نمایش مدت اتصال، مرتب‌شده از بیشترین زمان اتصال
- 🖥️ **سلامت سرور** — گیج‌های زنده برای پردازنده، حافظه، دیسک و پهنای باند
- 🔄 **راه‌اندازی مجدد خودکار Xray** — هنگامی که کاربر از کوتایش رد شد فعال می‌شود (با مقدار مجاز قابل تنظیم)
- 🧹 **پاک‌سازی پنل** — مشاهده و حذف انبوه کاربران منقضی، لیمیت‌شده یا قدیمی مستقیم از پنل
- ⏰ **زمان‌بند پاک‌سازی** — حذف خودکار شبانه در ساعتی که خودتان تعیین می‌کنید
- 🔒 **پشتیبانی از HTTPS** — فعال‌سازی اختیاری با گواهینامه دلخواه
- 🌐 **آگاهی از منطقه زمانی** — تمام زمان‌ها در منطقه زمانی تنظیم‌شده نمایش داده می‌شوند
- 👤 **چند مدیر** — حساب‌های مدیریتی با رمزنگاری PBKDF2

---

### پیش‌نیازها و کتابخانه‌ها

**بسته‌های خارجی** (توسط نصب‌کننده به‌طور خودکار نصب می‌شوند):

| بسته | کاربرد |
|---|---|
| `flask` | چارچوب وب — رابط کاربری داشبورد و مسیرهای API را ارائه می‌دهد |
| `requests` | ارتباط با API پنل 3x-ui |
| `tzdata` | پایگاه داده منطقه‌های زمانی — برای برخی سیستم‌های قدیمی‌تر لینوکس |

**کتابخانه‌های استاندارد** (داخل پایتون، نیاز به نصب ندارند):

| ماژول | کاربرد |
|---|---|
| `sqlite3` | پایگاه داده محلی برای ذخیره داده‌های ترافیک و تنظیمات |
| `hashlib` | رمزنگاری PBKDF2 برای رمز عبور حساب‌های مدیریتی |
| `threading` | اجرای موازی مانیتور و پاک‌سازی خودکار در پس‌زمینه |
| `zoneinfo` | مدیریت زمان بر اساس منطقه زمانی (پایتون ۳.۹ به بالا) |
| `logging` | ثبت رویدادهای سرویس مانیتور |
| `json` | پردازش پاسخ‌های API پنل و ذخیره نشست |
| `pathlib` | مدیریت مسیر فایل‌ها |
| `datetime` | قالب‌بندی زمان‌ها |
| `collections` | `defaultdict` برای جمع‌بندی ترافیک |
| `time`, `os`, `functools` | ابزارهای کمکی |

> نصب‌کننده تمام بسته‌های خارجی را خودکار نصب می‌کند. فقط `python3` نسخه ۳.۹ یا بالاتر باید از پیش روی سرور موجود باشد.

---

### نصب

این یک دستور را روی سرور Ubuntu یا Debian خود (به عنوان root) اجرا کنید:

```bash
curl -fsSL https://raw.githubusercontent.com/phoseinq/3x-ui/main/install.sh | sudo bash
```

این دستور به ترتیب:
۱. بسته‌های `flask` و `requests` را نصب می‌کند (از pip، در صورت نیاز از apt)
۲. فایل‌های برنامه را در `/opt/xui-monitor/` دانلود می‌کند
۳. یک کلید امنیتی تصادفی تولید می‌کند
۴. دو سرویس سیستمی `xui-dashboard` و `xui-monitor` را می‌سازد و راه‌اندازی می‌کند
۵. آدرس داشبورد را نمایش می‌دهد

---

### راه‌اندازی اولیه

۱. مرورگر را باز کنید و به آدرس `http://آی‌پی_سرور:5000` بروید
۲. حساب مدیر خود را بسازید (فقط بار اول)
۳. به بخش **تنظیمات** بروید و آدرس پنل 3x-ui، نام کاربری و رمز عبور را وارد کنید

---

### راهنمای تنظیمات

| تنظیم | توضیح |
|---|---|
| آدرس پنل | آدرس کامل پنل 3x-ui (مثلاً `http://1.2.3.4:2096/path`) |
| نام کاربری / رمز پنل | اطلاعات ورود به پنل 3x-ui |
| فاصله بررسی | هر چند ثانیه یک‌بار پنل بررسی شود |
| مجاز اضافی | مقدار ترافیک اضافه مجاز بعد از کوتا (مگابایت) قبل از راه‌اندازی مجدد |
| راه‌اندازی مجدد Xray | هنگام عبور کاربر از کوتا، Xray را راه‌اندازی مجدد کند |
| منطقه زمانی | منطقه زمانی برای نمایش تمام زمان‌ها (مثلاً `Asia/Tehran`) |
| HTTPS | فعال‌سازی اتصال امن با گواهینامه دلخواه |
| پاک‌سازی خودکار | حذف شبانه رکوردهای قدیمی از پایگاه داده محلی |
| پاک‌سازی پنل | مشاهده و حذف کاربران منقضی یا لیمیت‌شده مستقیم از پنل |

---

### سرویس‌ها

| سرویس | نقش |
|---|---|
| `xui-dashboard` | رابط تحت‌وب — روی درگاه ۵۰۰۰ اجرا می‌شود |
| `xui-monitor` | پردازش پس‌زمینه — هر چند ثانیه پنل را بررسی می‌کند و در صورت تجاوز از کوتا، Xray را مجدداً راه‌اندازی می‌کند |

هر دو سرویس با راه‌اندازی سیستم شروع می‌شوند و در صورت خطا به‌طور خودکار مجدداً راه‌اندازی می‌شوند.

---

### دستورات مفید

```bash
# مشاهده لاگ زنده داشبورد
journalctl -u xui-dashboard -f

# مشاهده لاگ زنده مانیتور
journalctl -u xui-monitor -f

# راه‌اندازی مجدد هر دو سرویس
systemctl restart xui-dashboard xui-monitor

# توقف هر دو سرویس
systemctl stop xui-dashboard xui-monitor

# حذف کامل برنامه
systemctl disable --now xui-dashboard xui-monitor && rm -rf /opt/xui-monitor
```

---

### مجوز

این پروژه تحت مجوز MIT منتشر شده — فایل [LICENSE](LICENSE) را ببینید.

---

### مشارکت

خوشحال می‌شویم مشارکت کنید:
- 🐛 گزارش اشکال
- 💡 پیشنهاد قابلیت جدید
- 🔧 ارسال درخواست ادغام

---

### حمایت

اگر این پروژه برایتان مفید بود:
- ⭐ به مخزن ستاره بدهید
- 📢 با دیگران به اشتراک بگذارید

</div>

---

<div align="center">

[گزارش مشکل](https://github.com/phoseinq/3x-ui/issues) · [Report a Bug](https://github.com/phoseinq/3x-ui/issues)

</div>
