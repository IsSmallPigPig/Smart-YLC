"""云端备份：客户端加密，服务端只存密文。

协议 v2（与 server 分支 netlify/functions/backup.mts 对应）：

* 提取码 6 位，字符集去掉 0/1/I/O，避免口头转述时认错；
* 密码 4 位数字：与提取码一起进 PBKDF2，猜中提取码也解不开密文；
* 密钥 = PBKDF2-HMAC-SHA256("提取码:密码", 随机 salt, 200000 次)，32 字节；
* 明文用 AES-256-GCM 加密，12 字节 nonce，标签附在密文尾部；
* 信封 = {v, salt, nonce, ciphertext}，字段均为 base64；
* 所有权：建档时客户端生成 256 位 owner key，服务端存它的 SHA-256。
  上传必须带 owner key 且与服务端记录一致，否则 403——别人的提取码
  只能拉取，不能覆盖；
* 查重：建档前先 probe，服务端已有这个提取码就拒绝创建。

信封 v1（只由提取码派生密钥、无所有权校验）仍可解密，老备份不会作废。
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
from typing import Any

import requests

ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # 32 个字符，去掉 0/1/I/O
CODE_LEN = 6
PASSWORD_RE = re.compile(r"^\d{4}$")
KDF_ITERATIONS = 200_000
SALT_BYTES = 16
NONCE_BYTES = 12
MAX_BYTES = 5 * 1024 * 1024
MAX_SERVERS = 10
DEFAULT_TIMEOUT = 20
DEFAULT_SERVER = "https://smartylc.netlify.app"
DEFAULT_SERVERS = (DEFAULT_SERVER,)


def generate_code() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(CODE_LEN))


def generate_owner_key() -> str:
    """建档时生成的所有权凭据：32 字节随机数，hex 编码存本地。"""
    return secrets.token_hex(32)


def normalize_code(raw: str) -> str:
    code = (raw or "").strip().upper().replace(" ", "")
    if len(code) != CODE_LEN or any(ch not in ALPHABET for ch in code):
        raise ValueError("提取码是 6 位字母或数字，不含 0、1、I、O")
    return code


def normalize_password(raw: str) -> str:
    pwd = (raw or "").strip()
    if not PASSWORD_RE.match(pwd):
        raise ValueError("密码是 4 位数字")
    return pwd


def normalize_server(url: str) -> str:
    value = (url or "").strip().rstrip("/")
    if not value:
        raise ValueError("请填写服务端地址")
    if not value.startswith(("http://", "https://")):
        value = "https://" + value
    if any(ch.isspace() for ch in value):
        raise ValueError("服务端地址里不能有空格")
    return value


# ---- 站点列表 -------------------------------------------------------------

def site_choices(custom: Any) -> list[str]:
    """默认站点在前，用户新增的站点在后；去重、保持顺序。"""
    out = list(DEFAULT_SERVERS)
    for item in [str(s) for s in (custom or []) if str(s).strip()]:
        try:
            url = normalize_server(item)
        except ValueError:
            continue
        if url not in out:
            out.append(url)
    return out


def add_site(custom: Any, url: str) -> list[str]:
    """把一个新站点追加进自定义列表（超出上限时挤掉最早的自定义项）。"""
    server = normalize_server(url)
    if server in DEFAULT_SERVERS:
        return [str(s) for s in (custom or [])]
    items = []
    for s in (custom or []):
        try:
            items.append(normalize_server(str(s)))
        except ValueError:
            continue
    if server not in items:
        items.append(server)
        items = items[-MAX_SERVERS:]
    return items


# ---- 本地档案（多配置） -----------------------------------------------------

def valid_profile(raw: Any) -> dict[str, str] | None:
    """从配置里的一个条目还原成档案；字段不对就丢弃（返回 None）。"""
    if not isinstance(raw, dict):
        return None
    try:
        code = normalize_code(str(raw.get("code") or ""))
        server = normalize_server(str(raw.get("server") or ""))
    except ValueError:
        return None
    owner_key = str(raw.get("ownerKey") or "")
    if len(owner_key) != 64:
        owner_key = ""  # 加入他人档案：没有所有权凭据，只能拉取
    name = str(raw.get("name") or "").strip()[:40] or f"配置 {code}"
    return {"name": name, "server": server, "code": code, "ownerKey": owner_key}


def load_profiles(cfg: dict) -> list[dict[str, str]]:
    out = []
    for raw in (cfg.get("cloudProfiles") or []):
        profile = valid_profile(raw)
        if profile:
            out.append(profile)
    return out


def local_config_codes(cfg: dict | None = None) -> list[str]:
    """本机配置档案的提取码列表，作为「相同配置的设备」的判据。

    手机与电脑填同一个提取码即视为同一套配置：局域网扫码配对时用它校验双方
    是否同源，无法直连时它又作为中转通道的标识。没配过云端档案时返回空列表，
    表示本机不启用这项校验（扫码即可配对，兼容未使用备份功能的用户）。
    """
    if cfg is None:
        from app.services import store
        cfg = store.load()
    return [p["code"] for p in load_profiles(cfg) if p.get("code")]


# ---- 加密 -----------------------------------------------------------------

def _derive(secret: str, salt: bytes) -> bytes:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt,
                     iterations=KDF_ITERATIONS)
    return kdf.derive(secret.encode("utf-8"))


def _secret(code: str, password: str) -> str:
    return f"{code}:{password}"


def encrypt(payload: bytes, code: str, password: str) -> dict[str, Any]:
    """把明文封成信封；同一份内容每次加密结果都不同（随机 salt/nonce）。"""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    code = normalize_code(code)
    password = normalize_password(password)
    salt = os.urandom(SALT_BYTES)
    nonce = os.urandom(NONCE_BYTES)
    sealed = AESGCM(_derive(_secret(code, password), salt)).encrypt(nonce, payload, None)
    return {
        "v": 2,
        "salt": base64.b64encode(salt).decode(),
        "nonce": base64.b64encode(nonce).decode(),
        "ciphertext": base64.b64encode(sealed).decode(),
    }


def decrypt(envelope: dict[str, Any], code: str, password: str = "") -> bytes:
    """解不开（码错、密码错、被改动、信封残缺）一律抛 ValueError。

    v1 信封只用提取码派生密钥，为兼容旧备份，password 留空即可。
    """
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    code = normalize_code(code)
    version = envelope.get("v") if isinstance(envelope, dict) else None
    if version == 2:
        if not password:
            raise ValueError("这份备份需要 4 位密码")
        secret = _secret(code, normalize_password(password))
    else:
        secret = code
    try:
        salt = base64.b64decode(envelope["salt"])
        nonce = base64.b64decode(envelope["nonce"])
        sealed = base64.b64decode(envelope["ciphertext"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("备份内容不完整") from exc
    try:
        return AESGCM(_derive(secret, salt)).decrypt(nonce, sealed, None)
    except InvalidTag as exc:
        raise ValueError("提取码或密码不对，或这份备份已被改动") from exc


def encrypt_config(cfg: dict, code: str, password: str) -> dict[str, Any]:
    raw = json.dumps(cfg, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(raw) > MAX_BYTES:
        raise ValueError("设置体积超过 5MB，云端备份不接受")
    return encrypt(raw, code, password)


def decrypt_config(envelope: dict[str, Any], code: str, password: str = "") -> dict:
    cfg = json.loads(decrypt(envelope, code, password).decode("utf-8"))
    if not isinstance(cfg, dict) or not isinstance(cfg.get("accounts"), list):
        raise ValueError("这份备份不是本程序的系统备份")
    return cfg


# ---- HTTP -----------------------------------------------------------------

def owner_hash(owner_key: str) -> str:
    return hashlib.sha256(owner_key.encode()).hexdigest()


def upload(server: str, code: str, envelope: dict[str, Any],
           owner_key: str = "", timeout: int = DEFAULT_TIMEOUT) -> None:
    """上传信封；建档/覆盖必须带 owner key，服务端据此拒绝他人篡改。"""
    base = normalize_server(server)
    code = normalize_code(code)
    headers = {"X-Owner-Key": owner_key} if owner_key else {}
    resp = requests.put(f"{base}/api/backup", params={"id": code},
                        json=envelope, headers=headers, timeout=timeout)
    _raise_for(resp, "上传失败")


def probe(server: str, code: str, timeout: int = DEFAULT_TIMEOUT) -> bool:
    """这个提取码在服务端上是否已有档案（建档前查重）。"""
    base = normalize_server(server)
    code = normalize_code(code)
    resp = requests.get(f"{base}/api/backup",
                        params={"id": code, "probe": "1"}, timeout=timeout)
    if resp.status_code == 404:
        return False
    _raise_for(resp, "查重失败")
    try:
        return bool((resp.json() or {}).get("exists"))
    except ValueError as exc:
        raise RuntimeError("服务端不支持查重，请确认地址指向云端备份服务 v2") from exc


def download(server: str, code: str, timeout: int = DEFAULT_TIMEOUT) -> dict[str, Any]:
    base = normalize_server(server)
    code = normalize_code(code)
    resp = requests.get(f"{base}/api/backup", params={"id": code}, timeout=timeout)
    _raise_for(resp, "下载失败")
    try:
        return resp.json()
    except ValueError as exc:
        raise RuntimeError("服务端返回的不是备份内容，请确认地址指向云端备份服务") from exc


def _raise_for(resp: requests.Response, prefix: str) -> None:
    if resp.status_code == 404:
        raise RuntimeError("没有找到这份备份，检查提取码或服务端地址")
    if resp.status_code == 403:
        message = ""
        try:
            message = (resp.json() or {}).get("error", "")
        except ValueError:
            pass
        raise RuntimeError(message or "这个提取码的配置属于别人，只能拉取，不能覆盖")
    if not resp.ok:
        message = ""
        try:
            message = (resp.json() or {}).get("error", "")
        except ValueError:
            message = resp.text[:120]
        raise RuntimeError(f"{prefix}：{message or resp.status_code}")
