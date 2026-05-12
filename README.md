<div align="center">

# 📊 3x-ui Monitor

<img src="https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python">
<img src="https://img.shields.io/badge/Flask-000000?style=for-the-badge&logo=flask&logoColor=white" alt="Flask">
<img src="https://img.shields.io/badge/3x--ui-Compatible-orange?style=for-the-badge" alt="3x-ui">
<img src="https://img.shields.io/badge/license-MIT-blue.svg?style=for-the-badge" alt="License">

**Traffic monitor & web dashboard for 3x-ui VPN panels**  
**مانیتور ترافیک و داشبورد وب برای پنل‌های 3x-ui**

[English](#english) | [فارسی](#فارسی)

---

</div>

## English

### 📖 Description

**3x-ui Monitor** is a self-hosted web dashboard that connects to your [3x-ui](https://github.com/MHSanaei/3x-ui) panel and gives you a real-time view of user traffic, online activity, server health, and automatic quota enforcement — all from a clean dark UI.

**Key Features:**
- 📈 **Traffic charts** — Hourly usage graphs per user and total
- 🟢 **Online users** — Live detection + duration tracking, sorted by longest online
- 🖥️ **Server health** — CPU, RAM, disk, and live bandwidth gauges
- 🔄 **Auto-restart Xray** — Kicks in when users exceed their quota (with grace allowance)
- 🧹 **Panel cleanup** — Preview and delete expired / over-limit users directly from the panel
- ⏰ **Auto-cleanup scheduler** — Nightly auto-delete of aged accounts
- 🔒 **HTTPS support** — Optional TLS with your own certificate
- 🌐 **Timezone-aware** — All times shown in your configured timezone
- 👤 **Multi-admin** — PBKDF2-hashed admin accounts

---

### 🚀 Installation

Run this single command on your Ubuntu/Debian server:

```bash
curl -fsSL https://raw.githubusercontent.com/phoseinq/xui-monitor/main/install.sh | sudo bash
```

The script will:
1. Install `flask` and `requests` (via pip, falls back to apt if needed)
2. Download files to `/opt/xui-monitor/`
3. Generate a random secret key
4. Create and start two systemd services: `xui-dashboard` and `xui-monitor`
5. Print your dashboard URL

**After install:**
1. Open `http://YOUR_SERVER_IP:5000` in your browser
2. Register your admin account (first visit only)
3. Go to **Settings** → enter your 3x-ui panel URL, username, and password

---

### ⚙️ Settings

| Setting | Description |
|---|---|
| **Panel URL** | Full URL to your 3x-ui panel (e.g. `http://1.2.3.4:2096/path`) |
| **Panel User / Pass** | Your 3x-ui login credentials |
| **Check Interval** | How often the monitor polls the panel (seconds) |
| **Grace MB** | Extra traffic allowed after quota before Xray restarts |
| **Auto-restart Xray** | Restart Xray core when a user exceeds their quota |
| **Timezone** | Timezone for all displayed times (e.g. `Asia/Tehran`) |
| **TLS / HTTPS** | Enable HTTPS with a custom cert/key path |
| **Auto Cleanup** | Nightly deletion of expired users from local DB |
| **Panel Cleanup** | Preview + delete expired / over-limit users from the panel |

---

### 🖥️ Services

| Service | Description |
|---|---|
| `xui-dashboard` | Flask web UI — runs on port 5000 |
| `xui-monitor` | Background poller — checks panel every N seconds, restarts Xray on quota breach |

Both services start on boot and restart automatically on failure.

---

### 🔧 CLI Commands

```bash
# View live dashboard logs
journalctl -u xui-dashboard -f

# View live monitor logs
journalctl -u xui-monitor -f

# Restart both services
systemctl restart xui-dashboard xui-monitor

# Stop both services
systemctl stop xui-dashboard xui-monitor

# Uninstall completely
systemctl disable --now xui-dashboard xui-monitor && rm -rf /opt/xui-monitor
```

---

### 📄 License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.

---

### 🤝 Contributing

Contributions are welcome! Feel free to:
- 🐛 Report bugs
- 💡 Suggest features
- 🔧 Submit pull requests

---

### ⭐ Support

If this project helped you, please consider:
- ⭐ Starring the repository
- 🐛 Reporting issues
- 📢 Sharing with others

---

<div dir="rtl" align="right">

## فارسی

### 📖 معرفی

**3x-ui Monitor** یه داشبورد وب self-hosted هست که به پنل [3x-ui](https://github.com/MHSanaei/3x-ui) شما وصل می‌شه و یه دید لحظه‌ای از ترافیک کاربرا، فعالیت آنلاین، سلامت سرور، و کنترل خودکار کوتا رو با یه UI تاریک و تمیز بهتون می‌ده.

**امکانات کلیدی:**
- 📈 **نمودار ترافیک** — گراف ساعتی مصرف هر کاربر و کل
- 🟢 **کاربران آنلاین** — تشخیص لحظه‌ای + ردیابی مدت زمان، مرتب‌شده بر اساس طولانی‌ترین آنلاین
- 🖥️ **سلامت سرور** — گیج‌های CPU، RAM، دیسک و پهنای باند زنده
- 🔄 **ری‌استارت خودکار Xray** — وقتی کاربر از کوتاش رد شد فعال می‌شه (با مارجین قابل تنظیم)
- 🧹 **پاکسازی پنل** — پیش‌نمایش و حذف کاربران اکسپایر یا لیمیت‌شده مستقیم از پنل
- ⏰ **زمان‌بند پاکسازی** — حذف خودکار شبانه حساب‌های قدیمی
- 🔒 **پشتیبانی HTTPS** — TLS اختیاری با گواهینامه خودتون
- 🌐 **آگاه از تایم‌زون** — همه زمان‌ها در تایم‌زون تنظیم‌شده نمایش داده می‌شن
- 👤 **چند ادمین** — حساب‌های ادمین با هش PBKDF2

---

### 🚀 نصب

این یه دستور رو روی سرور Ubuntu/Debian اجرا کن:

```bash
curl -fsSL https://raw.githubusercontent.com/phoseinq/xui-monitor/main/install.sh | sudo bash
```

اسکریپت این کارا رو می‌کنه:
1. نصب `flask` و `requests` (از pip، در صورت نیاز از apt)
2. دانلود فایل‌ها به `/opt/xui-monitor/`
3. تولید یه secret key رندوم
4. ساخت و راه‌اندازی دو سرویس systemd: `xui-dashboard` و `xui-monitor`
5. نمایش لینک داشبورد

**بعد از نصب:**
1. مرورگر رو باز کن، برو به `http://IP_سرور:5000`
2. حساب ادمین بساز (فقط اولین بار)
3. برو به **Settings** ← آدرس پنل 3x-ui، نام کاربری و رمز رو وارد کن

---

### ⚙️ تنظیمات

| تنظیم | توضیح |
|---|---|
| **Panel URL** | آدرس کامل پنل 3x-ui (مثلاً `http://1.2.3.4:2096/path`) |
| **Panel User / Pass** | اطلاعات ورود به پنل 3x-ui |
| **Check Interval** | هر چند ثانیه یه‌بار پنل چک بشه |
| **Grace MB** | ترافیک اضافه‌ای که بعد از کوتا مجازه قبل از ری‌استارت |
| **Auto-restart Xray** | ری‌استارت Xray وقتی کاربر از کوتا رد شد |
| **Timezone** | تایم‌زون برای نمایش زمان‌ها (مثلاً `Asia/Tehran`) |
| **TLS / HTTPS** | فعال‌سازی HTTPS با مسیر cert/key دلخواه |
| **Auto Cleanup** | حذف شبانه کاربران اکسپایر از دیتابیس محلی |
| **Panel Cleanup** | پیش‌نمایش + حذف کاربران اکسپایر / لیمیت‌شده از پنل |

---

### 🖥️ سرویس‌ها

| سرویس | توضیح |
|---|---|
| `xui-dashboard` | رابط وب Flask — روی پورت 5000 اجرا می‌شه |
| `xui-monitor` | پولر پس‌زمینه — هر N ثانیه پنل رو چک می‌کنه، در صورت نقض کوتا Xray رو ری‌استارت می‌کنه |

هر دو سرویس با بوت سیستم شروع می‌شن و در صورت خطا خودکار ری‌استارت می‌کنن.

---

### 🔧 دستورات CLI

```bash
# لاگ زنده داشبورد
journalctl -u xui-dashboard -f

# لاگ زنده مانیتور
journalctl -u xui-monitor -f

# ری‌استارت هر دو سرویس
systemctl restart xui-dashboard xui-monitor

# توقف هر دو سرویس
systemctl stop xui-dashboard xui-monitor

# حذف کامل
systemctl disable --now xui-dashboard xui-monitor && rm -rf /opt/xui-monitor
```

---

### 📄 مجوز

این پروژه تحت مجوز MIT منتشر شده — فایل [LICENSE](LICENSE) رو ببین.

---

### 🤝 مشارکت

مشارکت‌ها خوش‌آمدن! می‌تونی:
- 🐛 باگ گزارش کنی
- 💡 ایده پیشنهاد بدی
- 🔧 پول ریکوئست بفرستی

---

### ⭐ حمایت

اگه این پروژه بهت کمک کرد، لطفاً:
- ⭐ به ریپازیتوری ستاره بده
- 🐛 مشکلات رو گزارش کن
- 📢 با دیگران به اشتراک بذار

---

</div>

<div align="center">

**Made with ❤️ for 3x-ui users**

[Report a Bug](https://github.com/phoseinq/xui-monitor/issues) · [گزارش باگ](https://github.com/phoseinq/xui-monitor/issues)

</div>
