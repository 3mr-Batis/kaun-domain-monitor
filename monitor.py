#!/usr/bin/env python3
"""
Domain Monitor - kaun.com
مراقب نطاق تلقائي | يعمل كل يوم مجاناً على GitHub Actions
"""

import requests
import json
import os
import sys
from datetime import datetime, timezone

# ══════════════════════════════════════════════
#  تحميل الإعدادات
# ══════════════════════════════════════════════
with open("config.json", "r", encoding="utf-8") as _f:
    CONFIG = json.load(_f)

DOMAIN        = CONFIG["domain"]
AUTO_BUY      = CONFIG.get("auto_buy", False)
MAX_PRICE     = float(CONFIG.get("max_buy_price_usd", 0))
NTFY_TOPIC    = os.environ.get("NTFY_TOPIC", "")
NC_API_KEY    = os.environ.get("NAMECHEAP_API_KEY", "")
NC_API_USER   = os.environ.get("NAMECHEAP_API_USER", "")
NC_CLIENT_IP  = os.environ.get("NAMECHEAP_CLIENT_IP", "")

HEADERS = {"User-Agent": "kaun-domain-monitor/2.0"}


# ══════════════════════════════════════════════
#  1. فحص حالة النطاق
#     - محاولة أولى  : Verisign RDAP (السلطة الرسمية لـ .com)
#     - محاولة ثانية : rdap.org
# ══════════════════════════════════════════════
RDAP_SOURCES = [
    f"https://rdap.verisign.com/com/v1/domain/{DOMAIN}",
    f"https://rdap.org/domain/{DOMAIN}",
]

def check_domain_status() -> dict | None:
    for url in RDAP_SOURCES:
        try:
            r = requests.get(url, timeout=25, headers=HEADERS)

            if r.status_code == 404:
                return {"status": "AVAILABLE", "expiry": None}

            if r.status_code != 200:
                continue

            data   = r.json()
            expiry = next(
                (e["eventDate"][:10]
                 for e in data.get("events", [])
                 if e.get("eventAction") == "expiration"),
                None,
            )
            return {"status": "REGISTERED", "expiry": expiry}

        except requests.exceptions.Timeout:
            print(f"[RDAP] Timeout: {url}")
        except Exception as e:
            print(f"[RDAP] Error ({url}): {e}")

    return None   # كلا المصدرين فشلا


# ══════════════════════════════════════════════
#  2. مقارنة الأسعار
# ══════════════════════════════════════════════
REGISTRARS = [
    ("Porkbun",   9.73,  f"https://porkbun.com/checkout/search?q={DOMAIN}"),
    ("NameSilo",  8.99,  f"https://www.namesilo.com/domain/search-domains?query={DOMAIN}"),
    ("Spaceship",  9.00, f"https://www.spaceship.com/domain-checker/?search={DOMAIN}"),
    ("Dynadot",   9.99,  f"https://www.dynadot.com/domain/search.html?domain={DOMAIN}"),
    ("Namecheap", 9.98,  f"https://www.namecheap.com/domains/registration/results/?domain={DOMAIN}"),
    ("GoDaddy",  14.99,  f"https://www.godaddy.com/domainsearch/find?domainToCheck={DOMAIN}"),
]

def get_live_godaddy_price() -> float | None:
    try:
        r = requests.get(
            f"https://api.godaddy.com/v1/domains/available?domain={DOMAIN}",
            headers={"accept": "application/json"},
            timeout=8,
        )
        data = r.json()
        if data.get("available") and "price" in data:
            return round(data["price"] / 1_000_000, 2)
    except Exception:
        pass
    return None

def compare_prices() -> list[dict]:
    gd_live  = get_live_godaddy_price()
    results  = []
    for name, base_price, url in REGISTRARS:
        price = gd_live if (name == "GoDaddy" and gd_live) else base_price
        results.append({"name": name, "price": price, "url": url})
    results.sort(key=lambda x: x["price"])
    return results


# ══════════════════════════════════════════════
#  3. شراء تلقائي عبر Namecheap API (اختياري)
# ══════════════════════════════════════════════
def auto_buy_namecheap() -> bool:
    if not all([NC_API_KEY, NC_API_USER, NC_CLIENT_IP]):
        print("[AUTO-BUY] بيانات Namecheap API غير مكتملة")
        return False
    try:
        r = requests.get(
            "https://api.namecheap.com/xml.response",
            params={
                "ApiUser":   NC_API_USER,
                "ApiKey":    NC_API_KEY,
                "UserName":  NC_API_USER,
                "ClientIp":  NC_CLIENT_IP,
                "Command":   "namecheap.domains.create",
                "DomainName": DOMAIN,
                "Years":     1,
                "RegistrantFirstName":     NC_API_USER,
                "RegistrantLastName":      "Monitor",
                "RegistrantAddress1":      "N/A",
                "RegistrantCity":          "N/A",
                "RegistrantCountry":       "SA",
                "RegistrantPhone":         "+966.500000000",
                "RegistrantEmailAddress":  "admin@example.com",
                "RegistrantStateProvince": "N/A",
                "RegistrantPostalCode":    "00000",
            },
            timeout=25,
        )
        ok = "<DomainCreateResult" in r.text and 'Registered="true"' in r.text
        print(f"[AUTO-BUY] {'Success' if ok else 'Failed'}")
        return ok
    except Exception as e:
        print(f"[AUTO-BUY ERROR] {e}")
        return False


# ══════════════════════════════════════════════
#  4. الإشعارات عبر ntfy.sh
#     ملاحظة: HTTP headers تدعم ASCII فقط
#     لذا العنوان بالإنجليزية والمحتوى بالعربية في الـ body
# ══════════════════════════════════════════════
def notify(title_en: str, body_ar: str, priority: str = "high") -> None:
    if not NTFY_TOPIC:
        print(f"[NOTIFY] NTFY_TOPIC not set\n  Title: {title_en}\n  Body: {body_ar[:80]}")
        return
    try:
        requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=body_ar.encode("utf-8"),
            headers={
                "Title":        title_en,        # ASCII only
                "Priority":     priority,
                "Tags":         "globe_with_meridians,bell",
                "Content-Type": "text/plain; charset=utf-8",
            },
            timeout=12,
        )
        print(f"[NOTIFY] Sent: {title_en}")
    except Exception as e:
        print(f"[NOTIFY ERROR] {e}")


# ══════════════════════════════════════════════
#  5. حفظ وتحميل الحالة
# ══════════════════════════════════════════════
def load_state() -> dict:
    try:
        with open("domain_state.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"status": "UNKNOWN", "expiry": None, "last_checked": None}

def save_state(state: dict) -> None:
    state["last_checked"] = datetime.now(timezone.utc).isoformat()
    with open("domain_state.json", "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


# ══════════════════════════════════════════════
#  6. المنطق الرئيسي
# ══════════════════════════════════════════════
def main() -> None:
    sep = "=" * 54
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"\n{sep}")
    print(f"  Domain Monitor: {DOMAIN}  |  {now}")
    print(sep)

    current  = check_domain_status()
    previous = load_state()

    if current is None:
        # فشل الفحص — نرسل تنبيه ونخرج بنجاح (لا نفشل الـ workflow)
        notify(
            "Monitor Warning",
            f"فشل فحص {DOMAIN} — سيُعاد المحاولة غداً تلقائياً",
            priority="low",
        )
        print("  [WARNING] Check failed — will retry tomorrow")
        sys.exit(0)   # exit 0 حتى لا يُعلّم الـ workflow بالفشل

    print(f"  Previous : {previous.get('status')} | expiry={previous.get('expiry')}")
    print(f"  Current  : {current['status']} | expiry={current.get('expiry')}")
    print(sep)

    # ── الحالة 1: النطاق أصبح متاحاً ─────────────────
    if current["status"] == "AVAILABLE" and previous["status"] != "AVAILABLE":

        prices   = compare_prices()
        cheapest = prices[0]

        price_lines = "\n".join(
            f"{'>>> ' if i == 0 else '    '}{p['name']:10} ${p['price']:.2f}"
            for i, p in enumerate(prices)
        )
        buy_links = "\n".join(f"  {p['name']}: {p['url']}" for p in prices[:3])

        bought = False
        if AUTO_BUY and MAX_PRICE > 0 and cheapest["price"] <= MAX_PRICE:
            print(f"\n  [AUTO-BUY] ${cheapest['price']} <= limit ${MAX_PRICE} — buying...")
            bought = auto_buy_namechaep()

        if bought:
            notify(
                "BOUGHT! kaun.com is yours",
                f"تم شراء {DOMAIN} تلقائياً!\n"
                f"المسجّل: Namecheap\n"
                f"السعر: ${cheapest['price']}/سنة",
                priority="urgent",
            )
        else:
            notify(
                "AVAILABLE NOW: kaun.com",
                f"النطاق {DOMAIN} اصبح متاحاً الآن!\n\n"
                f"مقارنة الاسعار (من الارخص):\n"
                f"{price_lines}\n\n"
                f"روابط مباشرة:\n{buy_links}\n\n"
                f"سجّله بسرعة قبل ان ياخذه احد!",
                priority="urgent",
            )

    # ── الحالة 2: المالك جدّد النطاق ─────────────────
    elif (
        current["status"] == "REGISTERED"
        and previous.get("expiry")
        and current.get("expiry")
        and current["expiry"] != previous["expiry"]
    ):
        notify(
            "kaun.com was RENEWED",
            f"المالك جدّد النطاق — انتهت فرصة الانتظار\n\n"
            f"التاريخ القديم: {previous['expiry']}\n"
            f"التاريخ الجديد: {current['expiry']}\n\n"
            f"نصيحة: ابدأ Backorder على SnapNames الآن\n"
            f"https://www.snapnames.com/searchDomain.action?domain={DOMAIN}",
            priority="high",
        )

    # ── الحالة 3: لا تغيير ────────────────────────────
    else:
        print(f"  No change — still registered until {current.get('expiry')}")

    save_state(current)
    print(f"{sep}\n")


if __name__ == "__main__":
    main()
