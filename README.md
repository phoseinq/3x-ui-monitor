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

**System CLI tools** (pre-installed on Ubuntu / Debian):

| Tool | Purpose |
|---|---|
| `curl` | Downloads and runs the installer |
| `pip3` | Installs Python packages (`flask`, `requests`, `tzdata`) |
| `systemctl` | Manages `xui-dashboard` and `xui-monitor` services |
| `journalctl` | Views live service logs |

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
curl -fsSL https://raw.githubusercontent.com/phoseinq/3x-ui-monitor/main/install.sh | sudo bash
```

If your server needs a SOCKS5 proxy to reach the internet:

```bash
# inline flag
curl -fsSL https://raw.githubusercontent.com/phoseinq/3x-ui-monitor/main/install.sh | sudo bash -s -- --proxy socks5://1.2.3.4:1080

# with username and password
curl -fsSL https://raw.githubusercontent.com/phoseinq/3x-ui-monitor/main/install.sh | sudo bash -s -- --proxy socks5://user:pass@1.2.3.4:1080

# or via environment variable
export PROXY=socks5://1.2.3.4:1080
curl -fsSL https://raw.githubusercontent.com/phoseinq/3x-ui-monitor/main/install.sh | sudo bash
```

The proxy is used for both file downloads (`curl`) and Python package installation (`pip`).

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

### xui-mon CLI

The installer adds `xui-mon` — a management CLI for controlling the dashboard from the terminal.

```bash
xui-mon status               # show service status + current settings
xui-mon start                # start both services
xui-mon stop                 # stop both services
xui-mon restart              # restart both services
xui-mon user <new-username>  # change admin username (no restart needed)
xui-mon pass <new-password>  # change admin password (no restart needed)
xui-mon port <number>        # change dashboard port (restarts dashboard)
xui-mon https on --cert /path/fullchain.pem --key /path/privkey.pem   # enable HTTPS
xui-mon https off            # disable HTTPS (back to HTTP)
xui-mon remove               # stop, disable, and remove both services (data kept)
```

**Notes:**
- Run as root (`sudo xui-mon ...`)
- `xui-mon remove` asks for confirmation before removing services
- `xui-mon https on` will prompt for cert/key paths if not supplied as flags

---

### Service logs

```bash
journalctl -u xui-dashboard -f   # live dashboard logs
journalctl -u xui-monitor -f     # live monitor logs
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

**ابزارهای CLI سیستم** (از پیش روی Ubuntu / Debian نصب هستند):

| ابزار | کاربرد |
|---|---|
| `curl` | دانلود و اجرای نصب‌کننده |
| `pip3` | نصب بسته‌های پایتون (`flask`، `requests`، `tzdata`) |
| `systemctl` | مدیریت سرویس‌های `xui-dashboard` و `xui-monitor` |
| `journalctl` | مشاهده لاگ زنده سرویس‌ها |

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
curl -fsSL https://raw.githubusercontent.com/phoseinq/3x-ui-monitor/main/install.sh | sudo bash
```

اگر سرور شما برای دسترسی به اینترنت نیاز به پروکسی دارد:

```bash
# با پرچم مستقیم
curl -fsSL https://raw.githubusercontent.com/phoseinq/3x-ui-monitor/main/install.sh | sudo bash -s -- --proxy socks5://1.2.3.4:1080

# با نام کاربری و رمز عبور
curl -fsSL https://raw.githubusercontent.com/phoseinq/3x-ui-monitor/main/install.sh | sudo bash -s -- --proxy socks5://user:pass@1.2.3.4:1080

# از طریق متغیر محیطی
export PROXY=socks5://1.2.3.4:1080
curl -fsSL https://raw.githubusercontent.com/phoseinq/3x-ui-monitor/main/install.sh | sudo bash
```

پروکسی هم برای دانلود فایل‌ها (`curl`) و هم برای نصب بسته‌های پایتون (`pip`) استفاده می‌شود.

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

### ابزار مدیریتی xui-mon

نصب‌کننده دستور `xui-mon` را در اختیار می‌گذارد — یک ابزار مدیریتی برای کنترل داشبورد از طریق خط فرمان.

```bash
xui-mon status               # نمایش وضعیت سرویس‌ها و تنظیمات جاری
xui-mon start                # راه‌اندازی هر دو سرویس
xui-mon stop                 # توقف هر دو سرویس
xui-mon restart              # راه‌اندازی مجدد هر دو سرویس
xui-mon user <نام-کاربری>    # تغییر نام کاربری مدیر (نیاز به راه‌اندازی مجدد ندارد)
xui-mon pass <رمز-عبور>      # تغییر رمز عبور مدیر (نیاز به راه‌اندازی مجدد ندارد)
xui-mon port <عدد>           # تغییر درگاه داشبورد (داشبورد مجدداً راه‌اندازی می‌شود)
xui-mon https on --cert /path/fullchain.pem --key /path/privkey.pem   # فعال‌سازی HTTPS
xui-mon https off            # غیرفعال‌سازی HTTPS (بازگشت به HTTP)
xui-mon remove               # توقف، غیرفعال‌سازی و حذف سرویس‌ها (داده‌ها حفظ می‌شوند)
```

**نکات:**
- به عنوان root اجرا کنید (`sudo xui-mon ...`)
- دستور `xui-mon remove` قبل از حذف سرویس‌ها تأییدیه می‌خواهد
- دستور `xui-mon https on` در صورت عدم ارائه مسیر گواهینامه، آن را از شما می‌پرسد

---

### لاگ سرویس‌ها

```bash
journalctl -u xui-dashboard -f   # لاگ زنده داشبورد
journalctl -u xui-monitor -f     # لاگ زنده مانیتور
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
