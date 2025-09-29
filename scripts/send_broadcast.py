# scripts/send_broadcast.py
import os
import requests
from dotenv import load_dotenv

load_dotenv()  # .env を読み込む（Tuji_hack 直下の .env）

CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
FORM_URL = "https://docs.google.com/forms/d/e/1FAIpQLSdu02FU5rc1U3KT-2fSvUC9wDmyWlgu2s11s7n9EHgqvUDinw/viewform"

def main():
    if not CHANNEL_ACCESS_TOKEN:
        raise RuntimeError("LINE_CHANNEL_ACCESS_TOKEN が未設定である．.env を確認せよ．")

    headers = {
        "Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    data = {
        "messages": [
            {"type": "text", "text": f"本日のフォームはこちらである：\n{FORM_URL}"}
        ]
    }
    resp = requests.post("https://api.line.me/v2/bot/message/broadcast",
                         headers=headers, json=data, timeout=15)
    print(resp.status_code, resp.text)

if __name__ == "__main__":
    main()
