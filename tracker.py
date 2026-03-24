#!/usr/bin/env python3
"""
Price & Availability Tracker
- Price tracking: bol.com, coolblue.nl, cameranu.nl, kamera-express.nl, nivo-schweitzer.nl
- Availability tracking: kamerastore.com, mpb.com
Sends Telegram alerts on price drops or stock changes.
Requests routed through WebShare residential proxy to avoid Cloudflare blocks.
"""

import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ── Config ────────────────────────────────────────────────────────────────────
PRICES_FILE = Path("prices.json")
PRODUCTS_FILE = Path("products.json")
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "nl-NL,nl;q=0.9,en-GB;q=0.8,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
}
REQUEST_TIMEOUT = 20
DELAY_BETWEEN_REQUESTS = 3  # seconds — be polite to servers


# ── Proxy ─────────────────────────────────────────────────────────────────────
def get_proxies() -> dict | None:
    """Build a WebShare residential proxy config from environment secrets."""
    username = os.environ.get("PROXY_USERNAME")
    password = os.environ.get("PROXY_PASSWORD")
    if not username or not password:
        print("  ℹ️  No proxy credentials set — connecting directly")
        return None
    proxy_url = f"http://{username}:{password}@31.58.9.4:6077"
    return {"http": proxy_url, "https": proxy_url}


# ── Data model for availability results ──────────────────────────────────────
@dataclass
class AvailabilityResult:
    in_stock: bool
    stock_count: int | None = None
    price_from: float | None = None
    price_to: float | None = None
    currency: str = "EUR"
    conditions: list[str] = field(default_factory=list)
    raw: str = ""


# ── Telegram ──────────────────────────────────────────────────────────────────
def send_telegram(message: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("⚠️  TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set — skipping notification")
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message, "parse_mode": "HTML"}
    try:
        r = requests.post(url, json=payload, timeout=10)
        r.raise_for_status()
        print("✅ Telegram notification sent")
    except Exception as e:
        print(f"❌ Telegram error: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — PRICE TRACKING (Dutch retailers)
# ══════════════════════════════════════════════════════════════════════════════

def parse_bol(soup: BeautifulSoup) -> float | None:
    """bol.com"""
    el = soup.select_one("[data-price]")
    if el:
        return _parse_float(el["data-price"])
    for sel in [".priceLabel .price", ".buy-block__price", '[class*="price--"]']:
        el = soup.select_one(sel)
        if el:
            return _parse_float(el.get_text())
    return None


def parse_coolblue(soup: BeautifulSoup) -> float | None:
    """coolblue.nl"""
    for sel in [
        'span[class*="sales-price__item"]',
        ".js-product-order-form .price",
        '[class*="price"] strong',
    ]:
        el = soup.select_one(sel)
        if el:
            v = _parse_float(el.get_text())
            if v:
                return v
    return None


def parse_cameranu(soup: BeautifulSoup) -> float | None:
    """cameranu.nl"""
    for sel in [
        ".product-info__price .price",
        ".summary__price .price",
        '[class*="current-price"]',
        ".price-box .price",
    ]:
        el = soup.select_one(sel)
        if el:
            v = _parse_float(el.get_text())
            if v:
                return v
    return None


def parse_kameraexpress(soup: BeautifulSoup) -> float | None:
    """kamera-express.nl"""
    for sel in [
        ".current-price",
        ".product-price__current",
        '[class*="current-price"]',
        ".price-box .price",
    ]:
        el = soup.select_one(sel)
        if el:
            v = _parse_float(el.get_text())
            if v:
                return v
    return None


def parse_nivo(soup: BeautifulSoup) -> float | None:
    """nivo-schweitzer.nl"""
    for sel in [
        ".product-price",
        ".price .amount",
        '[class*="product__price"]',
        ".price-block .price",
    ]:
        el = soup.select_one(sel)
        if el:
            v = _parse_float(el.get_text())
            if v:
                return v
    return None


PRICE_PARSERS: dict = {
    "bol.com": parse_bol,
    "coolblue.nl": parse_coolblue,
    "cameranu.nl": parse_cameranu,
    "kamera-express.nl": parse_kameraexpress,
    "nivo-schweitzer.nl": parse_nivo,
}


def fetch_price(url: str) -> float | None:
    parser = next((fn for domain, fn in PRICE_PARSERS.items() if domain in url), None)
    if not parser:
        print(f"  ⚠️  No price parser for: {url}")
        return None
    try:
        r = requests.get(
            url,
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
            proxies=get_proxies(),
        )
        r.raise_for_status()
        return parser(BeautifulSoup(r.text, "html.parser"))
    except requests.RequestException as e:
        print(f"  ❌ Fetch error: {e}")
        return None


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — AVAILABILITY TRACKING (Kamerastore & MPB)
# ══════════════════════════════════════════════════════════════════════════════

def fetch_kamerastore_availability(url: str) -> AvailabilityResult | None:
    """
    Kamerastore runs on Shopify. We call /products/[slug].json directly —
    no HTML scraping needed, clean structured data every time.

    Page URL:  https://kamerastore.com/en-us/products/fujifilm-gfx-50s
    API URL:   https://kamerastore.com/products/fujifilm-gfx-50s.json
    """
    match = re.search(r"/products/([^/?#]+)", url)
    if not match:
        print(f"  ⚠️  Could not extract product slug from: {url}")
        return None

    slug = match.group(1)
    api_url = f"https://kamerastore.com/products/{slug}.json"

    try:
        r = requests.get(
            api_url,
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
            proxies=get_proxies(),
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"  ❌ Kamerastore API error: {e}")
        return None

    product = data.get("product", {})
    variants = product.get("variants", [])

    if not variants:
        return AvailabilityResult(in_stock=False, raw="No variants found")

    available_variants = [v for v in variants if v.get("available", False)]
    prices = []
    conditions = []

    for v in available_variants:
        price = _parse_float(str(v.get("price", "0")))
        if price:
            prices.append(price)
        title = v.get("title", "") or v.get("option1", "")
        if title and title not in ("Default Title", ""):
            conditions.append(title)

    in_stock = len(available_variants) > 0

    return AvailabilityResult(
        in_stock=in_stock,
        stock_count=len(available_variants),
        price_from=min(prices) if prices else None,
        price_to=max(prices) if prices else None,
        currency="EUR",
        conditions=sorted(set(conditions)),
        raw=f"{len(available_variants)}/{len(variants)} variants available",
    )


def fetch_mpb_availability(url: str) -> AvailabilityResult | None:
    """
    MPB product pages are server-rendered. We normalise to en-eu for EUR
    pricing, try JSON-LD structured data first, then fall back to HTML parsing.
    """
    # Normalise any MPB locale to en-eu for EUR pricing
    eu_url = re.sub(r"mpb\.com/[a-z]{2}-[a-z]{2}/", "mpb.com/en-eu/", url)
    if "/en-eu/" not in eu_url:
        eu_url = eu_url.replace("mpb.com/", "mpb.com/en-eu/")

    try:
        r = requests.get(
            eu_url,
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
            proxies=get_proxies(),
        )
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
    except requests.RequestException as e:
        print(f"  ❌ MPB fetch error: {e}")
        return None

    # ── Strategy 1: JSON-LD structured data ──────────────────────────────────
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            ld = json.loads(script.string or "")
            items = ld if isinstance(ld, list) else [ld]
            for item in items:
                if item.get("@type") in ("Product", "ItemList"):
                    offers = item.get("offers", {})
                    if isinstance(offers, dict):
                        offers = [offers]
                    if offers:
                        avail_url = offers[0].get("availability", "")
                        in_stock = any(
                            kw in avail_url
                            for kw in ("InStock", "LimitedAvailability", "PreOrder")
                        )
                        prices = [
                            p for p in (
                                _parse_float(str(o.get("price", ""))) for o in offers
                            ) if p
                        ]
                        return AvailabilityResult(
                            in_stock=in_stock,
                            stock_count=len(offers) if in_stock else 0,
                            price_from=min(prices) if prices else None,
                            price_to=max(prices) if prices else None,
                            currency=offers[0].get("priceCurrency", "EUR"),
                            raw=f"JSON-LD: {avail_url}",
                        )
        except (json.JSONDecodeError, AttributeError):
            continue

    # ── Strategy 2: visible text patterns ────────────────────────────────────
    page_text = soup.get_text(" ", strip=True)

    out_of_stock = bool(
        re.search(r"out of stock|no stock|not available|0 available", page_text, re.IGNORECASE)
    )
    stock_match = re.search(r"(\d+)\s+available\s+used", page_text, re.IGNORECASE)

    if out_of_stock and not stock_match:
        return AvailabilityResult(in_stock=False, stock_count=0, raw="HTML: out of stock")

    stock_count = int(stock_match.group(1)) if stock_match else None
    in_stock = stock_count is not None and stock_count > 0

    price_match = re.search(
        r"from\s*[€£$]?\s*([\d.,]+)(?:\s*[-–]\s*[€£$]?\s*([\d.,]+))?",
        page_text,
        re.IGNORECASE,
    )
    price_from = _parse_float(price_match.group(1)) if price_match else None
    price_to = (
        _parse_float(price_match.group(2))
        if (price_match and price_match.group(2))
        else price_from
    )

    if price_from is None:
        for sel in ['[class*="price"]', '[data-testid*="price"]', ".product-price"]:
            el = soup.select_one(sel)
            if el:
                v = _parse_float(el.get_text())
                if v and v > 10:
                    price_from = price_to = v
                    break

    return AvailabilityResult(
        in_stock=in_stock,
        stock_count=stock_count,
        price_from=price_from,
        price_to=price_to,
        currency="EUR",
        raw=f"HTML: {stock_count} unit(s) available",
    )


AVAILABILITY_FETCHERS: dict = {
    "kamerastore.com": fetch_kamerastore_availability,
    "mpb.com": fetch_mpb_availability,
}


def fetch_availability(url: str) -> AvailabilityResult | None:
    fetcher = next(
        (fn for domain, fn in AVAILABILITY_FETCHERS.items() if domain in url), None
    )
    if not fetcher:
        return None
    return fetcher(url)


def is_availability_site(url: str) -> bool:
    return any(domain in url for domain in AVAILABILITY_FETCHERS)


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 3 — SHARED HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _parse_float(text: str) -> float | None:
    """Extract a price float from strings like '€ 1.299,99' or '1299.99'"""
    text = str(text).strip()
    text = re.sub(r"[€$£\s]", "", text)
    if re.search(r"\d+\.\d{3},\d{2}", text):
        text = text.replace(".", "").replace(",", ".")
    elif "," in text and "." not in text:
        text = text.replace(",", ".")
    text = re.sub(r"[^\d.]", "", text)
    try:
        val = float(text)
        return val if val > 0 else None
    except ValueError:
        return None


def load_json(path: Path, default):
    if path.exists():
        return json.loads(path.read_text())
    return default


def save_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def format_price_range(result: AvailabilityResult) -> str:
    sym = "£" if result.currency == "GBP" else "€"
    if result.price_from and result.price_to and result.price_from != result.price_to:
        return f"{sym}{result.price_from:.0f}–{sym}{result.price_to:.0f}"
    elif result.price_from:
        return f"{sym}{result.price_from:.0f}"
    return "price unknown"


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 4 — PROCESSING FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════════

def process_price_product(product: dict, price_history: dict, now: str) -> list:
    """Track price for a standard retail product. Returns list of alert strings."""
    name = product["name"]
    url = product["url"]
    threshold = product.get("alert_below")
    alerts = []

    print(f"\n🏷️  PRICE  │ {name}")
    print(f"           │ {url}")

    current_price = fetch_price(url)
    time.sleep(DELAY_BETWEEN_REQUESTS)

    if current_price is None:
        print("           │ ❓ Could not parse price")
        return alerts

    print(f"           │ 💶 €{current_price:.2f}")

    history = price_history.get(url, [])
    last_entry = history[-1] if history else None
    last_price = last_entry["price"] if last_entry else None

    history.append({"ts": now, "price": current_price})
    price_history[url] = history[-90:]

    if last_price is None:
        print("           │ 📝 First check — baseline recorded")
        return alerts

    if current_price < last_price:
        diff = last_price - current_price
        pct = (diff / last_price) * 100
        print(f"           │ 📉 €{last_price:.2f} → €{current_price:.2f} (-€{diff:.2f}, -{pct:.1f}%)")
        below = threshold is not None and current_price <= threshold
        msg = (
            f"{'🔔 <b>Price alert!</b>' if below else '📉 <b>Price drop!</b>'}\n"
            f"📦 {name}\n"
            f"💶 €{last_price:.2f} → <b>€{current_price:.2f}</b> (-€{diff:.2f}, -{pct:.1f}%)\n"
        )
        if below:
            msg += f"🎯 Below your threshold of €{threshold:.2f}\n"
        msg += f"🔗 {url}"
        alerts.append(msg)
    elif current_price > last_price:
        diff = current_price - last_price
        print(f"           │ 📈 €{last_price:.2f} → €{current_price:.2f} (+€{diff:.2f})")
    else:
        print(f"           │ ✅ Unchanged at €{current_price:.2f}")

    return alerts


def process_availability_product(product: dict, avail_history: dict, now: str) -> list:
    """Track availability for a secondhand marketplace product. Returns alert strings."""
    name = product["name"]
    url = product["url"]
    alerts = []

    print(f"\n📦 STOCK   │ {name}")
    print(f"           │ {url}")

    result = fetch_availability(url)
    time.sleep(DELAY_BETWEEN_REQUESTS)

    if result is None:
        print("           │ ❓ Could not check availability")
        return alerts

    status_icon = "✅" if result.in_stock else "❌"
    stock_str = f"{result.stock_count} unit(s)" if result.stock_count is not None else "unknown qty"
    price_str = format_price_range(result)
    print(f"           │ {status_icon} {'In stock' if result.in_stock else 'Out of stock'} — {stock_str} — {price_str}")
    if result.conditions:
        print(f"           │ 🏷️  Conditions: {', '.join(result.conditions)}")
    print(f"           │ 🔍 {result.raw}")

    prev = avail_history.get(url, {})
    prev_in_stock = prev.get("in_stock")
    prev_count = prev.get("stock_count")

    avail_history[url] = {
        "ts": now,
        "in_stock": result.in_stock,
        "stock_count": result.stock_count,
        "price_from": result.price_from,
        "price_to": result.price_to,
    }

    if prev_in_stock is None:
        print("           │ 📝 First check — baseline recorded")
        return alerts

    # Back in stock
    if not prev_in_stock and result.in_stock:
        msg = (
            f"🟢 <b>Back in stock!</b>\n"
            f"📦 {name}\n"
            f"📊 {stock_str} available — {price_str}\n"
        )
        if result.conditions:
            msg += f"🏷️  Conditions: {', '.join(result.conditions)}\n"
        msg += f"🔗 {url}"
        alerts.append(msg)
        print("           │ 🟢 ALERT: Back in stock!")

    # Went out of stock
    elif prev_in_stock and not result.in_stock:
        msg = f"🔴 <b>Now out of stock</b>\n📦 {name}\n🔗 {url}"
        alerts.append(msg)
        print("           │ 🔴 ALERT: Went out of stock")

    # New units listed
    elif (
        result.in_stock
        and prev_count is not None
        and result.stock_count is not None
        and result.stock_count > prev_count
    ):
        added = result.stock_count - prev_count
        msg = (
            f"📬 <b>New units listed!</b>\n"
            f"📦 {name}\n"
            f"➕ {added} new unit(s) added → {result.stock_count} total — {price_str}\n"
        )
        if result.conditions:
            msg += f"🏷️  Conditions: {', '.join(result.conditions)}\n"
        msg += f"🔗 {url}"
        alerts.append(msg)
        print(f"           │ 📬 ALERT: {added} new unit(s) (was {prev_count})")

    else:
        print("           │ ✅ No availability change")

    return alerts


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 5 — ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def main():
    products = load_json(PRODUCTS_FILE, [])
    if not products:
        print("⚠️  products.json is empty. Add products first.")
        sys.exit(0)

    price_history = load_json(PRICES_FILE, {})
    avail_history = price_history.setdefault("__availability__", {})

    alerts = []
    now = datetime.now(timezone.utc).isoformat(timespec="minutes")

    print("=" * 60)
    print(f"  Price & Availability Tracker  —  {now} UTC")
    print("=" * 60)

    for product in products:
        url = product.get("url", "")
        if not url or url.startswith("_"):
            continue  # skip comment-only entries
        if is_availability_site(url):
            alerts.extend(process_availability_product(product, avail_history, now))
        else:
            alerts.extend(process_price_product(product, price_history, now))

    print("\n" + "=" * 60)
    save_json(PRICES_FILE, price_history)
    print(f"💾 Saved history to {PRICES_FILE}")

    if alerts:
        print(f"📣 Sending {len(alerts)} alert(s) via Telegram...")
        for alert in alerts:
            send_telegram(alert)
    else:
        print("🔕 No alerts to send")


if __name__ == "__main__":
    main()
