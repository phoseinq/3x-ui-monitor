<div dir="rtl" align="right">

# راه‌اندازی اولیه و تنظیمات

[← نصب](README.md)

---

## راه‌اندازی اولیه

بعد از نصب، مرورگر را باز کنید:

```
http://IP_سرور:5000
```

۱. **ساخت حساب مدیر** — فقط بار اول از شما می‌خواد
۲. وارد داشبورد می‌شید
۳. به **Settings** برید و اطلاعات پنل رو وارد کنید

---

## تنظیمات پنل

| تنظیم | توضیح |
|---|---|
| آدرس پنل | آدرس کامل پنل 3x-ui — مثلاً `http://1.2.3.4:2096` یا `https://panel.example.com` |
| نام کاربری | یوزرنیم ورود به پنل 3x-ui |
| رمز عبور | پسورد ورود به پنل 3x-ui |

آدرس پنل می‌تونه هم IP لوکال، هم IP پابلیک، هم دامنه باشه — با یا بدون `/` آخر فرقی نمی‌کنه.

---

## تنظیمات مانیتور

| تنظیم | توضیح | پیش‌فرض |
|---|---|---|
| فاصله بررسی | هر چند ثانیه پنل بررسی شود | ۳۰ ثانیه |
| مجاز اضافی (MB) | ترافیک اضافه مجاز بعد از کوتا قبل از ری‌استارت Xray | ۱۰۰ MB |
| راه‌اندازی مجدد Xray | ری‌استارت خودکار Xray هنگام عبور از کوتا | فعال |

---

## تنظیمات پیشرفته

| تنظیم | توضیح |
|---|---|
| منطقه زمانی | مثلاً `Asia/Tehran` — روی تمام زمان‌های داشبورد اثر دارد |
| HTTPS | فعال‌سازی با مسیر cert و key |
| پاک‌سازی خودکار | حذف شبانه رکوردهای قدیمی از DB محلی |
| پاک‌سازی پنل | حذف کاربران منقضی یا لیمیت‌شده مستقیم از پنل 3x-ui |

---

**[← بعدی: دستورات boy](CLI.md)**

</div>

---

# First Run & Settings

[← Installation](README.md)

---

## First run

After install, open your browser:

```
http://SERVER_IP:5000
```

1. **Register your admin account** — only asked on first visit
2. You're in the dashboard
3. Go to **Settings** and enter your panel details

---

## Panel settings

| Setting | Description |
|---|---|
| Panel URL | Full URL to your 3x-ui panel — e.g. `http://1.2.3.4:2096` or `https://panel.example.com` |
| Username | Your 3x-ui login username |
| Password | Your 3x-ui login password |

The panel URL can be a local IP, public IP, or domain — trailing `/` is handled automatically.

---

## Monitor settings

| Setting | Description | Default |
|---|---|---|
| Check interval | How often the monitor polls the panel | 30 s |
| Grace MB | Extra traffic allowed after quota before Xray restarts | 100 MB |
| Auto-restart Xray | Restart Xray core on quota breach | enabled |

---

## Advanced settings

| Setting | Description |
|---|---|
| Timezone | e.g. `Asia/Tehran` — affects all displayed timestamps |
| HTTPS | Enable with cert and key paths |
| Auto cleanup | Nightly deletion of old records from local DB |
| Panel cleanup | Delete expired / over-limit users directly from the 3x-ui panel |

---

**[Next: boy CLI →](CLI.md)**
