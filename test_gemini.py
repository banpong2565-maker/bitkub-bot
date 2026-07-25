import requests
import json
import os
from dotenv import load_dotenv

load_dotenv()

keys = os.getenv("GEMINI_API_KEYS", "").split(",")
keys = [k.strip() for k in keys if k.strip()]

if not keys:
    print("No keys found in .env")
    exit(1)

api_key = keys[0]
print(f"Testing with key: {api_key[:10]}...")

versions = ["v1", "v1beta"]
models = ["gemini-1.5-flash", "gemini-2.5-flash", "gemini-1.5-flash-latest", "gemini-2.5-flash"]

for version in versions:
    for model in models:
        url = f"https://generativelanguage.googleapis.com/{version}/models/{model}:generateContent?key={api_key}"
        headers = {'Content-Type': 'application/json'}
        payload = {
            "contents": [{
                "parts": [{
                    "text": "test"
                }]
            }]
        }
        try:
            res = requests.post(url, headers=headers, json=payload, timeout=10)
            print(f"[{version}] Model '{model}': HTTP {res.status_code}")
            if res.status_code == 200:
                print("  Success!")
            else:
                print(f"  Error: {res.text.strip()[:150]}")
        except Exception as e:
            print(f"  Exception: {e}")
