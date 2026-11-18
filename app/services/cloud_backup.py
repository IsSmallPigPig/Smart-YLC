"""云端备份：客户端加密，服务端只存密文。

协议（与 netlify/functions/backup.mts 对应）：

* 提取码 6 位，字符集去掉 0/1/I/O，避免口头转述时认错；
* 密钥 = PBKDF2-HMAC-SHA256(提取码, 随机 salt, 200000 次)，32 字节；
* 明文用 AES-256-GCM 加密，12 字节 nonce，标签附在密文尾部；
* 信封 = {v, salt, nonce, ciphertext}，字段均为 base64。

服务端看不到明文，也不知道提取码对应的账号内容；猜中提取码也只是拿到密文，
真正的成本在 PBKDF2 的迭代次数上。
"""

from __future__ import annotations

import base64
import json
import os
import secrets
from typing import Any

import requests

ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # 32 个字符，去掉 0/1/I/O
CODE_LEN = 6
KDF_ITERATIONS = 200_000
SALT_BYTES = 16
NONCE_BYTES = 12
MAX_BYTES = 5 * 1024 * 1024
DEFAULT_TIMEOUT = 20


def generate_code() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(CODE_LEN))


def normalize_code(raw: str) -> str:
    code = (raw or "").strip().upper().replace(" ", "")
    if len(code) != CODE_LEN or any(ch not in ALPHABET for ch in code):
        raise ValueError("提取码是 6 位字母或数字，不含 0、1、I、O")
    return code


def normalize_server(url: str) -> str:
    value = (url or "").strip().rstrip("/")
    if not value:
        raise ValueError("请填写服务端地址")
    if not value.startswith(("http://", "https://")):
        value = "https://" + value
    return value


def _derive(code: str, salt: bytes) -> bytes:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt,
                     iterations=KDF_ITERATIONS)
    return kdf.derive(code.encode("utf-8"))


def encrypt(payload: bytes, code: str) -> dict[str, Any]:
    """把明文封成信封；同一份内容每次加密结果都不同（随机 salt/nonce）。"""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    code = normalize_code(code)
    salt = os.urandom(SALT_BYTES)
    nonce = os.urandom(NONCE_BYTES)
    sealed = AESGCM(_derive(code, salt)).encrypt(nonce, payload, None)
    return {
        "v": 1,
        "salt": base64.b64encode(salt).decode(),
        "nonce": base64.b64encode(nonce).decode(),
        "ciphertext": base64.b64encode(sealed).decode(),
    }


def decrypt(envelope: dict[str, Any], code: str) -> bytes:
    """解不开（码错、被改动、信封残缺）一律抛 ValueError。"""
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    code = normalize_code(code)
    try:
        salt = base64.b64decode(envelope["salt"])
        nonce = base64.b64decode(envelope["nonce"])
        sealed = base64.b64decode(envelope["ciphertext"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("备份内容不完整") from exc
    try:
        return AESGCM(_derive(code, salt)).decrypt(nonce, sealed, None)
    except InvalidTag as exc:
        raise ValueError("提取码不对，或这份备份已被改动") from exc


def encrypt_config(cfg: dict, code: str) -> dict[str, Any]:
    raw = json.dumps(cfg, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(raw) > MAX_BYTES:
        raise ValueError("设置体积超过 5MB，云端备份不接受")
    return encrypt(raw, code)


def decrypt_config(envelope: dict[str, Any], code: str) -> dict:
    cfg = json.loads(decrypt(envelope, code).decode("utf-8"))
    if not isinstance(cfg, dict) or not isinstance(cfg.get("accounts"), list):
        raise ValueError("这份备份不是本程序的系统备份")
    return cfg


def upload(server: str, code: str, envelope: dict[str, Any], timeout: int = DEFAULT_TIMEOUT) -> None:
    base = normalize_server(server)
    code = normalize_code(code)
    resp = requests.put(f"{base}/api/backup", params={"id": code}, json=envelope, timeout=timeout)
    _raise_for(resp, "上传失败")


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
    if not resp.ok:
        message = ""
        try:
            message = (resp.json() or {}).get("error", "")
        except ValueError:
            message = resp.text[:120]
        raise RuntimeError(f"{prefix}：{message or resp.status_code}")
