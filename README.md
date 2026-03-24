# 📸 Price Tracker

Monitors product prices across Dutch camera/photo retailers and sends a Telegram message on any price drop.

**Price tracking** (alerts on drops):
- bol.com, coolblue.nl, cameranu.nl, kamera-express.nl, nivo-schweitzer.nl

**Availability tracking** (alerts when stock appears or changes):
- kamerastore.com — uses Shopify's product JSON API, no HTML scraping
- mpb.com — uses JSON-LD structured data + HTML fallback

Runs automatically on GitHub Actions — no server required.

---

## Setup

### 1. Create a Telegram bot

1. Open Telegram and message **@BotFather**
2. Send `/newbot` and follow the prompts
3. Copy the **bot token** (looks like `123456789:AAFxxx...`)
4. Start a chat with your new bot, then visit:
   `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
5. Send any message to the bot, refresh the URL, and copy your **chat_id** from the JSON response

### 2. Create a GitHub repository

1. Create a new repo (public or private)
2. Push all files from this folder to it

### 3. Add GitHub Secrets

In your repo → **Settings → Secrets and variables → Actions → New repository secret**:

| Name | Value |
|------|-------|
| `TELEGRAM_BOT_TOKEN` | Your bot token from BotFather |
| `TELEGRAM_CHAT_ID` | Your numeric chat ID |

### 4. Configure your products

Edit `products.json` with the products you want to track. The tracker automatically detects which mode to use based on the domain.

**Price tracking** (bol.com, coolblue.nl, cameranu.nl, kamera-express.nl, nivo-schweitzer.nl):
```json
{
  "name": "Fujifilm GFX 100S II Body",
  "url": "https://www.cameranu.nl/nl/p123/fujifilm-gfx-100s-ii/",
  "alert_below": 4500
}
```
- `alert_below` — optional €-threshold; set to `null` to alert on any price drop

**Availability tracking** (kamerastore.com, mpb.com):
```json
{
  "name": "Fujifilm GFX 100S II — Kamerastore",
  "url": "https://kamerastore.com/en-us/products/fujifilm-gfx-100s-ii"
},
{
  "name": "Fujifilm GFX 100S II — MPB",
  "url": "https://www.mpb.com/en-eu/product/fujifilm-gfx-100s-ii"
}
```
- No extra fields needed — the tracker alerts on: back in stock, out of stock, and new units listed
- For MPB, use `en-eu` in the URL to get EUR pricing (the tracker enforces this automatically)
- For Kamerastore, use any locale (`/en-us/`, `/en-eu/` etc.) — the slug is what matters

### 5. Enable Actions and trigger a first run

1. Go to **Actions** in your repo and enable workflows if prompted
2. Click **Price Tracker → Run workflow** to do an initial run
3. The first run records baseline prices (no alerts sent yet)
4. Subsequent runs compare against the stored history

---

## How it works

```
GitHub Actions (cron: every 6h)
        │
        ▼
  tracker.py runs
        │
        ├─ for each product, detects mode by domain:
        │
        ├─ PRICE MODE (Dutch retailers)
        │   ├─ fetches product page HTML
        │   ├─ parses price with site-specific CSS selector
        │   ├─ compares to last price in prices.json
        │   └─ alerts on drop (or threshold breach) 📉
        │
        ├─ AVAILABILITY MODE (Kamerastore, MPB)
        │   ├─ Kamerastore: calls /products/[slug].json (Shopify API)
        │   ├─ MPB: parses JSON-LD structured data → HTML fallback
        │   ├─ tracks stock count and price range
        │   └─ alerts on: back in stock 🟢 / out of stock 🔴 / new units 📬
        │
        └─ commits updated prices.json back to repo
```

All history is stored in `prices.json` (auto-committed). Price history keeps the last 90 data points per product. Availability state stores only the most recent snapshot.

---

## Adjusting the schedule

Edit `.github/workflows/tracker.yml` and change the cron expression:

```yaml
- cron: "0 */6 * * *"   # every 6 hours (default)
- cron: "0 */4 * * *"   # every 4 hours
- cron: "0 8,20 * * *"  # twice daily at 08:00 and 20:00 UTC
```

Note: GitHub Actions free tier has generous limits — 2,000 minutes/month for private repos, unlimited for public.

---

## Adding more retailers

Open `tracker.py` and add a parser function and register it in the `PARSERS` dict:

```python
def parse_myshop(soup: BeautifulSoup) -> float | None:
    el = soup.select_one(".my-price-class")
    return _parse_float(el.get_text()) if el else None

PARSERS = {
    ...
    "myshop.nl": parse_myshop,
}
```

To find the right CSS selector: open the product page in Chrome → right-click the price → Inspect → look for a unique class on the price element.

---

## Troubleshooting

**Price shows as `None`** — the retailer may have changed their HTML. Re-inspect the page and update the parser's CSS selectors.

**No Telegram message** — check that your secrets are set correctly. Run manually from Actions and read the logs.

**Rate limiting / blocked** — increase `DELAY_BETWEEN_REQUESTS` in `tracker.py`, or reduce how often the cron runs.
