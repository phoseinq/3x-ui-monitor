<div align="center">

# 3x-ui Monitor

**داشبورد مانیتورینگ ترافیک برای پنل‌های 3x-ui**

<img width="2559" height="1363" alt="image" src="https://github.com/user-attachments/assets/d5eb19c3-1fe0-4d0a-98f8-18e47b9bf77b" />

</div>

---

<div dir="rtl" align="right">

## نصب

### نصب معمولی

```bash
curl -fsSL https://raw.githubusercontent.com/phoseinq/3x-ui-monitor/main/install.sh | sudo bash
```

---

### نصب با پروکسی (سرور ایران)

سرور ایران بدون پروکسی به GitHub وصل نمی‌شه — هم دانلود اسکریپت و هم تمام مراحل نصب باید از طریق پروکسی بره:

```bash
curl -fsSL --proxy socks5://HOST:PORT \
  https://raw.githubusercontent.com/phoseinq/3x-ui-monitor/main/install.sh \
  | sudo bash -s -- --proxy socks5://HOST:PORT
```

اگه پروکسی یوزر و پسورد داره:

```bash
curl -fsSL --proxy socks5://user:pass@HOST:PORT \
  https://raw.githubusercontent.com/phoseinq/3x-ui-monitor/main/install.sh \
  | sudo bash -s -- --proxy socks5://user:pass@HOST:PORT
```

---

[نصب دستی (بدون اسکریپت)](MANUAL.md) · **[بعدی: راه‌اندازی اولیه و تنظیمات ←](SETUP.md)**
</div>

---

## Installation

### Standard

```bash
curl -fsSL https://raw.githubusercontent.com/phoseinq/3x-ui-monitor/main/install.sh | sudo bash
```

**[Next: First run & settings →](SETUP.md)**

MIT License
