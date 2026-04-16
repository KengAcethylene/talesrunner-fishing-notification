import os
import requests
from dotenv import load_dotenv

load_dotenv()

webhook = os.getenv("DISCORD_WEBHOOK_URL", "")
mention = os.getenv("DISCORD_MENTION", "")
rdp_url = os.getenv("REMOTE_DESKTOP_URL", "")

if not webhook:
    print("[ERROR] DISCORD_WEBHOOK_URL is not set in .env")
    exit(1)

content = f"{mention} [TEST] Bot check alert — solve the puzzle to continue fishing."
if rdp_url:
    content += f"\n{rdp_url}"

r = requests.post(webhook, json={"content": content}, timeout=5)

if r.status_code == 204:
    print("[OK] Message sent successfully.")
else:
    print(f"[ERROR] Failed to send. Status: {r.status_code} — {r.text}")
