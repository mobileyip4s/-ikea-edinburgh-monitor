import os
import json
import hashlib
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from playwright.sync_api import sync_playwright

URL = "https://www.ikea.com/gb/en/second-hand/buy-from-ikea/#/edinburgh"
STATE_FILE = "data/seen_items.json"

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465
EMAIL_TO = os.environ["EMAIL_TO"]
EMAIL_FROM = os.environ["EMAIL_FROM"]
EMAIL_APP_PASSWORD = os.environ["EMAIL_APP_PASSWORD"]

def load_seen():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()

def save_seen(items):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(items), f, ensure_ascii=False, indent=2)

def stable_id(item):
    raw = "|".join([
        item.get("name",""),
        item.get("price",""),
        item.get("url",""),
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

def scrape():
    items = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(URL, wait_until="domcontentloaded", timeout=120000)

        # IKEA's Re-shop & Re-use page is a client-rendered application.
        # Give the app time to load its Edinburgh inventory.
        page.wait_for_timeout(15000)

        # Collect links/buttons that look like individual Re-shop items.
        candidates = page.locator("a").all()
        for a in candidates:
            try:
                text = " ".join((a.inner_text() or "").split())
                href = a.get_attribute("href") or ""
                if not text:
                    continue
                if "edinburgh" not in text.lower() and "second-hand" not in href.lower():
                    continue

                # Keep this deliberately conservative. IKEA can change markup.
                price = ""
                parent = a.locator("xpath=..")
                ptxt = " ".join((parent.inner_text() or "").split())
                for token in ptxt.split():
                    if "£" in token:
                        price = token
                        break

                if len(text) >= 3:
                    items.append({
                        "name": text[:180],
                        "price": price,
                        "url": href if href.startswith("http") else (
                            "https://www.ikea.com" + href if href.startswith("/") else URL
                        )
                    })
            except Exception:
                pass

        browser.close()

    # De-duplicate
    unique = {}
    for item in items:
        key = stable_id(item)
        unique[key] = item
    return list(unique.values())

def send_email(new_items):
    subject = f"IKEA Edinburgh Re-shop & Re-use: {len(new_items)} new item(s)"
    lines = [
        "IKEA Edinburgh Re-shop & Re-use has new item(s).",
        "",
    ]
    for item in new_items:
        lines.append(f"• {item['name']}")
        if item["price"]:
            lines.append(f"  Price: {item['price']}")
        lines.append(f"  {item['url']}")
        lines.append("")

    msg = MIMEMultipart()
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO
    msg["Subject"] = subject
    msg.attach(MIMEText("\n".join(lines), "plain", "utf-8"))

    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
        server.login(EMAIL_FROM, EMAIL_APP_PASSWORD)
        server.sendmail(EMAIL_FROM, [EMAIL_TO], msg.as_string())

def main():
    seen = load_seen()
    items = scrape()
    current_ids = {stable_id(i) for i in items}

    # On first successful run, establish a baseline without emailing every existing item.
    first_run = not seen
    new_ids = current_ids if False else current_ids - seen

    if first_run:
        save_seen(current_ids)
        print(f"Baseline created: {len(items)} item(s). No email sent on first run.")
        return

    new_items = [i for i in items if stable_id(i) in new_ids]
    if new_items:
        send_email(new_items)
        print(f"Sent notification for {len(new_items)} new item(s).")
    else:
        print("No new items found.")

    save_seen(current_ids)

if __name__ == "__main__":
    main()
