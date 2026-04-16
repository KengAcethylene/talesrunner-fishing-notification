import os
import requests
from dotenv import load_dotenv

load_dotenv()

token   = os.getenv("TELEGRAM_BOT_TOKEN", "")
chat_id = os.getenv("TELEGRAM_CHAT_ID", "")

if not token or not chat_id:
    print("[ERROR] TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set in .env")
    exit(1)

text = "[TEST] Bot check alert — solve the puzzle to continue fishing."

url = f"https://api.telegram.org/bot{token}/sendMessage"
r = requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=5)

if r.ok:
    print("[OK] Message sent successfully.")
else:
    print(f"[ERROR] Status: {r.status_code} — {r.text}")
