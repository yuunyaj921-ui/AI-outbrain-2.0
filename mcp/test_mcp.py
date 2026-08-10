import os
import ssl
import urllib.error
import urllib.request

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

base = "https://127.0.0.1:27124"
endpoints = ["/", "/vault/", "/vault/00_Inbox/"]


def main() -> int:
    api_key = os.environ.get("OBSIDIAN_API_KEY", "").strip()
    if not api_key:
        print(
            "ERROR: OBSIDIAN_API_KEY is required; "
            "store it in the local environment, never in source control."
        )
        return 2

    for endpoint in endpoints:
        url = base + endpoint
        request = urllib.request.Request(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
        )
        try:
            with urllib.request.urlopen(request, context=ctx, timeout=10) as response:
                body = response.read().decode("utf-8")
                print(f"=== {endpoint} status={response.status} ===")
                print(body[:500])
                print()
        except (OSError, urllib.error.URLError) as exc:
            print(f"=== {endpoint} ERROR: {exc} ===")
            print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
