#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════╗
║   🌐  Domain Monitor — kaun.com              ║
║   مراقب نطاق تلقائي | يعمل كل يوم مجاناً   ║
╚══════════════════════════════════════════════╝
"""

import requests
import json
import os
import sys
from datetime import datetime

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


# ══════════════════════════════════════════════
#  1. فحص حالة النطاق عبر RDAP (مجاني بلا مفتاح)
# ══════════════════════════════════════════════
def check_domain_status() -> dict | None:
    """يرجع dict فيه status و expiry، أو None عند الخطأ."""
    try:
        r = requests.get(
            f"https://rdap.org/domain/{DOMAIN}",
            timeout=15,
            headers={"User-Agent": "kaun-domain-monitor/1.0"}
        )
        if r.status_code == 404:
            return {"status": "AVAILABLE", "expiry": None}

        data   = r.json()
        expiry = next(
            (e["eventDate"][:10]
             for e in data.get("events", [])
             if e.get("eventAction") == "expiration"),
            None
        )
        return {"status": "REGISTERED", "expiry": expiry}

    except Exception as e:
        print(f"[RDAP ERROR] {e}")
        return None


# ══════════════════════════════════════════════
#  2. مقارنة الأسعار من مسجّلين مختلفين
# ══════════════════════════════════════════════
REGISTRARS = [
    # (الاسم, السعر الثابت لـ .com, رابط الشراء المباشر)
    ("Porkbun",   9.73,  f"https://porkbun.com/checkout/search?q={DOMAIN}"),
    ("NameSilo",  8.99,  f"https://www.namesilo.com/domain/search-domains?query={DOMAIN}"),
    ("Dynadot",   9.99,  f"https://www.dynadot.com/domain/search.html?domain={DOMAIN}"),
    ("Spaceship",  9.00, f"https://www.spaceship.com/domain-checker/?search={DOMAIN}"),
    ("Namecheap", 9.98,  f"https://www.namecheap.com/domains/registration/results/?domain={DOMAIN}"),
    ("GoDaddy",  14.99,  f"https://www.godaddy.com/domainsearch/find?domainToCheck={DOMAIN}"),
]

def get_live_godaddy_price() -> float | None:
    """يحاول جلب سعر GoDaddy الحقيقي عبر API العام."""
    try:
        r = requests.get(
            f"https://api.godaddy.com/v1/domains/available?domain={DOMAIN}",
            headers={"accept": "application/json"},
            timeout=8
        )
        data = r.json()
        if data.get("available") and "price" in data:
            return round(data["price"] / 1_000_000, 2)
    except Exception:
        pass
    return None

def compare_prices() -> list[dict]:
    """يرجع قائمة مرتّبة من الأرخص للأغلى."""
    results = []

    # تحديث سعر GoDaddy مباشرة
    gd_live = get_live_godaddy_price()

    for name, base_price, url in REGISTRARS:
        price = gd_live if (name == "GoDaddy" and gd_live) else base_price
        results.append({"name": name, "price": price, "url": url})

    results.sort(key=lambda x: x["price"])
    return results


# ══════════════════════════════════════════════
#  3. شراء تلقائي عبر Namecheap API (اختياري)
# ══════════════════════════════════════════════
def auto_buy_namecheap() -> bool:
    """
    يشتري النطاق تلقائياً عبر Namecheap API.
    يحتاج: NAMECHEAP_API_KEY + NAMECHEAP_API_USER + NAMECHEAP_CLIENT_IP
    في GitHub Secrets.
    """
    if not all([NC_API_KEY, NC_API_USER, NC_CLIENT_IP]):
        print("[AUTO-BUY] بيانات Namecheap API غير مكتملة — تم التخطي")
        return False

    sld, tld = DOMAIN.rsplit(".", 1)

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
                # بيانات التسجيل — سيتم أخذها من الحساب
                "RegistrantFirstName": NC_API_USER,
                "RegistrantLastName":  "Auto",
                "RegistrantAddress1":  "N/A",
                "RegistrantCity":      "N/A",
                "RegistrantCountry":   "SA",
                "RegistrantPhone":     "+966.500000000",
                "RegistrantEmailAddress": "admin@example.com",
                "RegistrantStateProvince": "N/A",
                "RegistrantPostalCode": "00000",
            },
            timeout=20
        )
        success = "<DomainCreateResult" in r.text and 'Registered="true"' in r.text
        print(f"[AUTO-BUY] {'✅ نجح الشراء!' if success else '❌ فشل الشراء'}")
        return success

    except Exception as e:
        print(f"[AUTO-BUY ERROR] {e}")
        return False


# ══════════════════════════════════════════════
#  4. الإشعارات عبر ntfy.sh
# ══════════════════════════════════════════════
def notify(title: str, body: str, priority: str = "high") -> None:
    if not NTFY_TOPIC:
        print(f"[NOTIFY] ⚠️  NTFY_TOPIC غير محدد في Secrets\nالعنوان: {title}\n{body}")
        return
    try:
        requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=body.encode("utf-8"),
            headers={
                "Title":        title,
                "Priority":     priority,
                "Tags":         "globe_with_meridians,bell",
                "Content-Type": "text/plain; charset=utf-8",
            },
            timeout=10,
        )
        print(f"[NOTIFY] ✅ أُرسل: {title}")
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
    state["last_checked"] = datetime.utcnow().isoformat() + "Z"
    with open("domain_state.json", "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


# ══════════════════════════════════════════════
#  6. المنطق الرئيسي
# ══════════════════════════════════════════════
def main() -> None:
    sep = "═" * 52
    print(f"\n{sep}")
    print(f"  🔍 فحص {DOMAIN} — {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    print(sep)

    current  = check_domain_status()
    previous = load_state()

    if current is None:
        notify("⚠️ خطأ في المراقبة", f"فشل فحص {DOMAIN} — سيعاد المحاولة غداً", priority="default")
        sys.exit(1)

    print(f"  الحالة السابقة : {previous.get('status')} | {previous.get('expiry')}")
    print(f"  الحالة الحالية : {current['status']} | {current.get('expiry')}")
    print(sep)

    # ── الحالة 1: النطاق أصبح متاحاً 🎉 ──────────────
    if current["status"] == "AVAILABLE" and previous["status"] != "AVAILABLE":
        prices  = compare_prices()
        cheapest = prices[0]

        price_lines = "\n".join(
            f"  {'👑' if i == 0 else '  '} {p['name']:10} ${p['price']:.2f}  →  {p['url']}"
            for i, p in enumerate(prices)
        )

        bought = False
        if AUTO_BUY and MAX_PRICE > 0 and cheapest["price"] <= MAX_PRICE:
            print(f"\n[AUTO-BUY] السعر ${cheapest['price']} ≤ الحد ${MAX_PRICE} — جاري الشراء...")
            bought = auto_buy_namecheap()

        if bought:
            notify(
                f"✅ تم شراء {DOMAIN}!",
                f"تم شراء النطاق تلقائياً عبر Namecheap بسعر ${cheapest['price']}/سنة",
                priority="urgent",
            )
        else:
            body = (
                f"النطاق {DOMAIN} أصبح متاحاً الآن!\n\n"
                f"💰 مقارنة الأسعار (من الأرخص):\n"
                f"{price_lines}\n\n"
                f"⚡ أسرع وسجّله قبل أن يسبقك أحد!"
            )
            notify(f"🎉 {DOMAIN} متاح!", body, priority="urgent")

    # ── الحالة 2: المالك جدّد النطاق 🔄 ───────────────
    elif (
        current["status"] == "REGISTERED"
        and previous.get("expiry")
        and current.get("expiry")
        and current["expiry"] != previous["expiry"]
    ):
        notify(
            f"🔄 {DOMAIN} تم تجديده",
            f"المالك جدّد النطاق — انتهت فرصة الانتظار قريباً\n\n"
            f"التاريخ القديم : {previous['expiry']}\n"
            f"التاريخ الجديد : {current['expiry']}\n\n"
            f"نصيحة: ابدأ Backorder على SnapNames الآن.",
            priority="high",
        )

    # ── الحالة 3: لا تغيير ────────────────────────────
    else:
        print(f"  ✅ مسجّل — تاريخ الانتهاء: {current.get('expiry')} — لا تغيير")

    save_state(current)
    print(f"{sep}\n")


if __name__ == "__main__":
    main()
