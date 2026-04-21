"""Expose chatgpt2api accounts as CLIProxyAPI-compatible auth files.

Mirrors the subset of CLIProxyAPI (CPA) management API that other
chatgpt2api / CPA clients consume for remote token import:

  - GET /v0/management/auth-files
  - GET /v0/management/auth-files/download?name=<file.json>

Only read-only endpoints are implemented. Each chatgpt2api account is
synthesized as a standalone auth-file entry keyed by a stable sha1 hash
of its access_token, so list and download are always consistent.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from services.account_service import AccountService, account_service
from services.config import config

AUTH_FILE_PREFIX = "chatgpt-"
AUTH_FILE_SUFFIX = ".json"
PROVIDER = "chatgpt"
ACCOUNT_TYPE = "chatgpt"

_STATUS_MAP: dict[str, str] = {
    "正常": "ready",
    "限流": "throttled",
    "异常": "error",
    "禁用": "disabled",
}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _sha1_16(access_token: str) -> str:
    return hashlib.sha1(access_token.encode("utf-8")).hexdigest()[:16]


def auth_file_id_for(access_token: str) -> str:
    return _sha1_16(_clean(access_token))


def auth_file_name_for(access_token: str) -> str:
    return f"{AUTH_FILE_PREFIX}{auth_file_id_for(access_token)}{AUTH_FILE_SUFFIX}"


def _is_valid_auth_file_name(name: str) -> bool:
    lowered = name.lower()
    if not lowered.endswith(AUTH_FILE_SUFFIX):
        return False
    if not name.startswith(AUTH_FILE_PREFIX):
        return False
    return len(name) > len(AUTH_FILE_PREFIX) + len(AUTH_FILE_SUFFIX)


def _store_mtime_iso() -> str:
    try:
        timestamp = config.accounts_file.stat().st_mtime
    except (FileNotFoundError, OSError):
        timestamp = datetime.now(timezone.utc).timestamp()
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _synth_path_for(name: str) -> str:
    return str(config.accounts_file.parent / name)


def _build_download_payload(account: dict) -> dict:
    access_token = _clean(account.get("access_token"))
    label = _clean(account.get("type")) or "Free"
    payload: dict[str, Any] = {
        "access_token": access_token,
        "provider": PROVIDER,
        "account_type": ACCOUNT_TYPE,
        "label": label,
        "email": _clean(account.get("email")) or None,
        "user_id": _clean(account.get("user_id")) or None,
        "status": _clean(account.get("status")) or "正常",
        "quota": int(account.get("quota") or 0),
    }
    return payload


def _build_entry(account: dict, modtime: str) -> dict | None:
    access_token = _clean(account.get("access_token"))
    if not access_token:
        return None

    file_id = _sha1_16(access_token)
    name = f"{AUTH_FILE_PREFIX}{file_id}{AUTH_FILE_SUFFIX}"
    status_value = _clean(account.get("status")) or "正常"
    disabled = status_value == "禁用"
    quota = int(account.get("quota") or 0)
    unavailable = disabled or status_value == "异常" or quota <= 0

    payload = _build_download_payload(account)
    size_bytes = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    email = _clean(account.get("email")) or None
    label = _clean(account.get("type")) or "Free"
    last_used = _clean(account.get("last_used_at")) or modtime

    return {
        "id": file_id,
        "name": name,
        "provider": PROVIDER,
        "label": label,
        "status": _STATUS_MAP.get(status_value, "ready"),
        "status_message": status_value,
        "disabled": disabled,
        "unavailable": unavailable,
        "runtime_only": False,
        "source": "file",
        "path": _synth_path_for(name),
        "size": size_bytes,
        "modtime": modtime,
        "email": email,
        "account_type": ACCOUNT_TYPE,
        "account": label,
        "created_at": modtime,
        "updated_at": last_used,
        "last_refresh": last_used,
    }


def list_auth_files(service: AccountService | None = None) -> list[dict]:
    svc = service or account_service
    modtime = _store_mtime_iso()
    entries: list[dict] = []
    for access_token in svc.list_tokens():
        account = svc.get_account(access_token)
        if account is None:
            continue
        entry = _build_entry(account, modtime)
        if entry is not None:
            entries.append(entry)
    entries.sort(key=lambda item: str(item.get("name") or "").lower())
    return entries


def _find_account_by_file_name(name: str, service: AccountService | None = None) -> dict | None:
    svc = service or account_service
    target = _clean(name)
    if not _is_valid_auth_file_name(target):
        return None
    file_id = target[len(AUTH_FILE_PREFIX): -len(AUTH_FILE_SUFFIX)]
    if not file_id:
        return None
    for access_token in svc.list_tokens():
        if _sha1_16(access_token) == file_id:
            return svc.get_account(access_token)
    return None


def build_download_payload_for_name(
    name: str,
    service: AccountService | None = None,
) -> dict | None:
    account = _find_account_by_file_name(name, service)
    if account is None:
        return None
    return _build_download_payload(account)
