<div dir="rtl" align="right">

# دستورات boy

[← راه‌اندازی](SETUP.md)

---

## منوی تعاملی

اجرا بدون آرگومان منوی تعاملی رو باز می‌کنه:

```bash
sudo boy
```

---

## دستورات مستقیم

```bash
boy status       # وضعیت سرویس‌ها و تنظیمات جاری
boy start        # راه‌اندازی هر دو سرویس
boy stop         # توقف هر دو سرویس
boy restart      # ری‌استارت هر دو سرویس
boy update       # آپدیت به آخرین نسخه از GitHub
```

---

## مدیریت حساب

```bash
boy user <نام>   # تغییر یوزرنیم مدیر (بدون ری‌استارت)
boy pass         # تغییر پسورد — بدون نمایش روی صفحه
```

---

## پورت و HTTPS

```bash
boy port <عدد>   # تغییر پورت داشبورد (داشبورد ری‌استارت می‌شه)

boy https on --cert /path/fullchain.pem --key /path/privkey.pem
boy https off    # بازگشت به HTTP
```

اگه مسیر cert و key رو ندی، خودش می‌پرسه.

---

## حذف

```bash
boy remove       # حذف سرویس‌ها — داده‌ها حفظ می‌شن
```

قبل از حذف تأییدیه می‌خواد.

---

## لاگ‌ها

```bash
journalctl -u xui-dashboard -f   # لاگ زنده داشبورد
journalctl -u xui-monitor -f     # لاگ زنده مانیتور
```

---

> همه دستورات باید به عنوان root اجرا شوند: `sudo boy ...`

---

**[← بعدی: مستندات کامل](ADVANCED.md)**

</div>

---

# boy CLI

[← Setup](SETUP.md)

---

## Interactive menu

Run without arguments to open the interactive menu:

```bash
sudo boy
```

---

## Direct commands

```bash
boy status       # service status + current settings
boy start        # start both services
boy stop         # stop both services
boy restart      # restart both services
boy update       # update to latest version from GitHub
```

---

## Account management

```bash
boy user <name>  # change admin username (no restart needed)
boy pass         # change admin password — no echo
```

---

## Port & HTTPS

```bash
boy port <num>   # change dashboard port (restarts dashboard)

boy https on --cert /path/fullchain.pem --key /path/privkey.pem
boy https off    # switch back to HTTP
```

If you omit `--cert` / `--key`, it will prompt for the paths.

---

## Remove

```bash
boy remove       # stop, disable, and remove services (data is kept)
```

Asks for confirmation before removing anything.

---

## Logs

```bash
journalctl -u xui-dashboard -f   # live dashboard logs
journalctl -u xui-monitor -f     # live monitor logs
```

---

> Always run as root: `sudo boy ...`

---

**[Next: Advanced docs →](ADVANCED.md)**
