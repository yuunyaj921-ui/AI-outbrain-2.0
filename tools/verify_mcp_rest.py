#!/usr/bin/env python3
"""Verify Obsidian Local REST API connectivity by completing 4 checks:
1. List vault root
2. Read _知识卡片模板.md
3. Create a temporary test note in 00_Inbox/_待审核/
4. Delete that test note

Fixed: URL-safe percent-encoding for non-ASCII path segments (e.g. _待审核).
Also probes the /mcp endpoint with proper Accept headers to detect native MCP support.
"""

import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request

API_KEY = os.environ.get("OBSIDIAN_API_KEY", "").strip()
BASE = os.environ.get("OBSIDIAN_API_URL", "https://127.0.0.1:27124")

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE


def encode_vault_path(path: str) -> str:
    """Percent-encode a vault path, preserving slashes.

    Each path segment is individually quoted so non-ASCII characters
    like 待审核 are properly encoded for urllib.request.
    """
    parts = path.split("/")
    return "/vault/" + "/".join(urllib.parse.quote(p, safe="") for p in parts)


def api_call(method, path, data=None, content_type=None, accept=None):
    """Make an HTTP request to the Local REST API. Returns (status, body_text)."""
    url = BASE + path
    body = None
    headers = {"Authorization": f"Bearer {API_KEY}"}
    if accept:
        headers["Accept"] = accept

    if data is not None:
        if isinstance(data, (dict, list)):
            body = json.dumps(data).encode("utf-8")
            headers["Content-Type"] = content_type or "application/vnd.olrapi.note+json"
        elif isinstance(data, str):
            body = data.encode("utf-8")
            headers["Content-Type"] = content_type or "text/markdown"

    req = urllib.request.Request(url, method=method, headers=headers, data=body)
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return resp.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace") if e.fp else str(e)
        return e.code, raw
    except (OSError, ValueError) as e:
        return -1, f"{type(e).__name__}: {e}"


def main():
    if not API_KEY:
        print(
            "ERROR: OBSIDIAN_API_KEY is required for verification; "
            "set it in the local environment."
        )
        return 2

    results = {}

    # --- Step 1: List vault root ---
    print("=== STEP 1: List vault root ===")
    status, body = api_call("GET", "/vault/", accept="application/json")
    print(f"  Status: {status}")
    if status == 200:
        try:
            obj = json.loads(body)
            files = obj.get("files", obj.get("children", []))
            print(f"  Vault root: {len(files)} entries")
            for f in files[:15]:
                print(f"    - {f}")
            results["listed_vault"] = True
        except json.JSONDecodeError:
            print(f"  Non-JSON response (first 300): {body[:300]}")
            results["listed_vault"] = "non_json"
    else:
        print(f"  ERROR body: {body[:300]}")
        results["listed_vault"] = False

    # --- Step 2: Read template ---
    print("\n=== STEP 2: Read _知识卡片模板.md ===")
    encoded = encode_vault_path("_知识卡片模板.md")
    status, body = api_call("GET", encoded)
    print(f"  Status: {status}")
    if status == 200:
        print(f"  Template length: {len(body)} chars")
        print(f"  First 80 chars: {body[:80]}")
        results["read_template"] = True
    else:
        print(f"  ERROR: {body[:300]}")
        results["read_template"] = False

    # --- Step 3: Create test note ---
    print("\n=== STEP 3: Create test note in 00_Inbox/_待审核/ ===")
    test_path_raw = "00_Inbox/_待审核/mcp_verify_test.md"
    test_content = (
        "---\n"
        "tags: [test, mcp-verify]\n"
        "created: 2026-07-30\n"
        "---\n"
        "\n"
        "# MCP Verification Test Note\n"
        "\n"
        "Temporary note created to verify Obsidian MCP/REST connectivity.\n"
        "Should be deleted immediately after verification.\n"
    )

    encoded_test = encode_vault_path(test_path_raw)
    status, body = api_call(
        "PUT", encoded_test, data=test_content, content_type="text/markdown"
    )
    print(f"  Create status: {status}")
    if status in (200, 201, 204):
        print("  Note created successfully")
    else:
        print(f"  Create response: {body[:300]}")

    # Verify creation by reading back
    status_rb, body_rb = api_call("GET", encoded_test)
    print(f"  Read-back status: {status_rb}")
    if status_rb == 200:
        print(f"  Read-back length: {len(body_rb)} chars")
        results["created_test_note"] = True
    else:
        # Try alternate: PUT with JSON body
        print("  Trying alternate create: PUT with JSON body...")
        alt_data = {"content": test_content}
        status_alt, body_alt = api_call(
            "PUT",
            encoded_test,
            data=alt_data,
            content_type="application/vnd.olrapi.note+json",
        )
        print(f"  Alt create status: {status_alt}")
        if status_alt in (200, 201, 204):
            status_rb2, _body_rb2 = api_call("GET", encoded_test)
            print(f"  Alt read-back status: {status_rb2}")
            results["created_test_note"] = status_rb2 == 200
        else:
            print(f"  Alt create response: {body_alt[:300]}")
            results["created_test_note"] = False

    # --- Step 4: Delete test note ---
    print("\n=== STEP 4: Delete test note ===")
    status_del, body_del = api_call("DELETE", encoded_test)
    print(f"  Delete status: {status_del}")
    if status_del in (200, 202, 204):
        print("  Note deleted successfully")
    else:
        print(f"  Delete response: {body_del[:300]}")

    # Verify deletion
    status_post, _ = api_call("GET", encoded_test)
    print(f"  Post-delete read status: {status_post} (expect 404)")
    results["deleted_test_note"] = status_post == 404

    # --- Probe MCP endpoints ---
    print("\n=== Probing MCP endpoints ===")
    mcp_found = False
    for ep in ["/mcp", "/sse", "/api/mcp", "/api/sse"]:
        for accept in [
            "application/json",
            "text/event-stream",
            "application/vnd.olrapi.mcp+json",
        ]:
            status, body = api_call("GET", ep, accept=accept)
            found = status not in (-1, 404, 405)
            if found:
                print(
                    f"  {ep:20s} Accept={accept:30s} -> status={status} {'*** FOUND ***' if found else ''}"
                )
                if status == 200 and len(body) < 500:
                    print(f"    Body: {body}")
                mcp_found = mcp_found or (status == 200)
                break
        else:
            # Only print not-found for the first Accept
            if accept == "application/json":
                status, _ = api_call("GET", ep, accept="application/json")
                print(f"  {ep:20s} -> status={status}")

    # --- Summary ---
    print("\n" + "=" * 60)
    print("VERIFICATION SUMMARY")
    print("=" * 60)
    for k, v in results.items():
        status_str = (
            "✓ PASS" if v is True else ("~ PARTIAL" if v == "non_json" else "✗ FAIL")
        )
        print(f"  {k:25s} {status_str}")

    all_ok = all(v is True or v == "non_json" for v in results.values())
    print(f"\n  Overall: {'ALL CHECKS PASSED ✓' if all_ok else 'SOME CHECKS FAILED ✗'}")
    print(f"  MCP endpoint detected: {'Yes' if mcp_found else 'No'}")

    print("\n---JSON_START---")
    print(
        json.dumps(
            {
                "results": results,
                "all_ok": all_ok,
                "mcp_endpoint_detected": mcp_found,
            },
            ensure_ascii=False,
        )
    )
    print("---JSON_END---")

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
