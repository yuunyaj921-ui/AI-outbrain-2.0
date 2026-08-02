import urllib.request, ssl, json, sys

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

api_key = "7d5fae81891539d8d79eaf97d3891fc42bb5389204af980447eb49564ccb8e4b"
base = "https://127.0.0.1:27124"

endpoints = ["/", "/vault/", "/vault/00_Inbox/"]

for ep in endpoints:
    url = base + ep
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key}"})
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
            body = resp.read().decode("utf-8")
            print(f"=== {ep} status={resp.status} ===")
            print(body[:500])
            print()
    except Exception as e:
        print(f"=== {ep} ERROR: {e} ===")
        print()
