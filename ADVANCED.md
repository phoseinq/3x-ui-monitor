<div dir="rtl" align="right">

# مستندات کامل

[← دستورات boy](CLI.md)

---

## قابلیت‌ها

- **نمودار ترافیک** — گراف مصرف ساعتی به تفکیک هر کاربر و مجموع کل
- **کاربران آنلاین** — شناسایی لحظه‌ای با نمایش مدت اتصال
- **سلامت سرور** — CPU، RAM، دیسک و پهنای باند زنده
- **ری‌استارت خودکار Xray** — هنگام عبور کاربر از کوتا (با مقدار مجاز قابل تنظیم)
- **پاک‌سازی پنل** — مشاهده و حذف انبوه کاربران منقضی، لیمیت‌شده یا قدیمی مستقیم از پنل
- **بکاپ قبل از حذف** — فایل CSV از اطلاعات کاربران قبل از هر حذف ذخیره می‌شه
- **زمان‌بند پاک‌سازی** — حذف خودکار شبانه در ساعت دلخواه
- **HTTPS** — فعال‌سازی اختیاری با گواهینامه دلخواه
- **منطقه زمانی** — تمام زمان‌ها در timezone تنظیم‌شده نمایش داده می‌شن
- **چند مدیر** — حساب‌های مدیریتی با رمزنگاری PBKDF2

---

## سرویس‌ها

| سرویس | نقش |
|---|---|
| `xui-dashboard` | رابط تحت‌وب — روی پورت ۵۰۰۰ اجرا می‌شه |
| `xui-monitor` | پردازش پس‌زمینه — پنل رو بررسی می‌کنه و در صورت تجاوز از کوتا Xray رو ری‌استارت می‌کنه |

هر دو سرویس با راه‌اندازی سیستم شروع می‌شن و در صورت خطا خودکار ری‌استارت می‌شن.

---

## بکاپ کاربران حذف‌شده

قبل از هر حذف (دستی یا خودکار)، یک فایل CSV در این مسیر ذخیره می‌شه:

```
/opt/xui-monitor/deleted_backup/YYYY-MM-DD_HH-MM-SS.csv
```

فیلدها: `email`، `client_id`، `subscription`، `tg_id`، `comment`، `quota_gb`، `up_gb`، `down_gb`، `total_gb`، `pct`، `expiry_date`

---

## فایل‌ها

| فایل | مکان |
|---|---|
| داشبورد | `/opt/xui-monitor/dashboard.py` |
| مانیتور | `/opt/xui-monitor/monitor.py` |
| boy CLI | `/opt/xui-monitor/boy.py` → `/usr/local/bin/boy` |
| Chart.js | `/opt/xui-monitor/static/chart.min.js` |
| DB ترافیک | `/opt/xui-monitor/traffic.db` |
| DB تنظیمات | `/opt/xui-monitor/app.db` |
| بکاپ حذف | `/opt/xui-monitor/deleted_backup/` |

---

## کتابخانه‌ها

**نصب خودکار توسط installer:**

| بسته | کاربرد |
|---|---|
| `flask` | وب‌فریمورک — رابط کاربری و API |
| `requests` | ارتباط با API پنل 3x-ui |
| `tzdata` | پایگاه داده منطقه‌های زمانی |

**استاندارد پایتون (نیاز به نصب ندارن):**

`sqlite3`, `hashlib`, `threading`, `zoneinfo`, `logging`, `json`, `pathlib`, `datetime`, `csv`

</div>

---

# Advanced Docs

[← boy CLI](CLI.md)

---

## Features

- **Traffic charts** — Hourly usage per user and totals
- **Online users** — Live detection with connection duration
- **Server health** — CPU, RAM, disk, live bandwidth
- **Auto-restart Xray** — Triggers on quota breach (configurable grace)
- **Panel cleanup** — Preview and bulk-delete expired / over-limit / aged users
- **Deletion backup** — CSV saved before every delete (auto or manual)
- **Scheduled cleanup** — Nightly auto-delete at a time you choose
- **HTTPS** — Optional TLS with your own certificate
- **Timezone-aware** — All times in your configured timezone
- **Multi-admin** — PBKDF2-hashed admin accounts

---

## Services

| Service | Role |
|---|---|
| `xui-dashboard` | Flask web UI — listens on port 5000 |
| `xui-monitor` | Background poller — checks panel every N seconds, restarts Xray on quota breach |

Both start on boot and restart automatically on failure.

---

## Deletion backup

Before any delete (manual or scheduled), a CSV is saved to:

```
/opt/xui-monitor/deleted_backup/YYYY-MM-DD_HH-MM-SS.csv
```

Fields: `email`, `client_id`, `subscription`, `tg_id`, `comment`, `quota_gb`, `up_gb`, `down_gb`, `total_gb`, `pct`, `expiry_date`

---

## File locations

| File | Path |
|---|---|
| Dashboard | `/opt/xui-monitor/dashboard.py` |
| Monitor | `/opt/xui-monitor/monitor.py` |
| boy CLI | `/opt/xui-monitor/boy.py` → `/usr/local/bin/boy` |
| Chart.js | `/opt/xui-monitor/static/chart.min.js` |
| Traffic DB | `/opt/xui-monitor/traffic.db` |
| Settings DB | `/opt/xui-monitor/app.db` |
| Deletion backups | `/opt/xui-monitor/deleted_backup/` |

---

## Libraries

**Installed automatically:**

| Package | Purpose |
|---|---|
| `flask` | Web framework — dashboard UI and API routes |
| `requests` | HTTP client — communicates with the 3x-ui panel API |
| `tzdata` | Timezone database |

**Python standard library (no install needed):**

`sqlite3`, `hashlib`, `threading`, `zoneinfo`, `logging`, `json`, `pathlib`, `datetime`, `csv`

---

## Reverse proxy

By default the dashboard listens on `http://0.0.0.0:5000`. If you want HTTPS on port 443 or need to run multiple services on one IP, put nginx in front.

**nginx example:**

```nginx
server {
    listen 443 ssl;
    server_name your.domain.com;

    ssl_certificate     /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

Then restrict Flask to localhost only — edit `/etc/systemd/system/xui-dashboard.service`:

```ini
[Service]
Environment=HOST=127.0.0.1
ExecStart=/usr/bin/python3 /opt/xui-monitor/dashboard.py
```

Reload: `systemctl daemon-reload && systemctl restart xui-dashboard`

> If you only need HTTPS without nginx, use `boy https on --cert /path/cert.pem --key /path/key.pem` — no reverse proxy needed.

---

## Log management

Logs go to journald. To limit disk usage:

```bash
# keep only last 7 days
journalctl --vacuum-time=7d

# or cap by size
journalctl --vacuum-size=100M
```

---

MIT License
