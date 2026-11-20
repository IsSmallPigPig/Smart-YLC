"""中转接收：手机连不上局域网时，从云端中转站把 PDF 拉回电脑。

和局域网直传的关系：正常同一 Wi-Fi 走 lan_share（内网带宽、零流量）；手机用流量、
两边不在同一网络时才走这里。两条路最后都交给 phone_sync 落盘，后续流程完全一致。

中转站契约见 docs/app-pair-protocol.md 第 9 节，服务端实现在 server 分支的
netlify/functions/archive.mts。鉴权沿用云端备份的提取码模型：code 即读权限，
owner key 是写权限（只有建档的手机能传）。
"""

from __future__ import annotations

import os
import tempfile

import requests

from app.services import phone_sync
from app.services.cloud_backup import (DEFAULT_TIMEOUT, normalize_code,
                                       normalize_server)


class RelayError(RuntimeError):
    """中转站返回错误或数据不完整。"""


def _raise_for(resp: requests.Response, action: str) -> None:
    if resp.ok:
        return
    try:
        msg = (resp.json() or {}).get("error", "")
    except ValueError:
        msg = resp.text[:120]
    raise RelayError(f"{action}失败（{resp.status_code}）：{msg or '无更多信息'}")


def list_pending(server: str, code: str,
                 timeout: int = DEFAULT_TIMEOUT) -> list[dict]:
    """列出中转站里该提取码下待接收的文件元数据。

    每项含 id / name / size / sha256 / uploadedAt。已落盘的由调用方按 sha256 过滤。
    """
    base = normalize_server(server)
    code = normalize_code(code)
    resp = requests.get(f"{base}/api/archive",
                        params={"action": "list", "id": code}, timeout=timeout)
    _raise_for(resp, "查询中转清单")
    try:
        data = resp.json() or {}
    except ValueError as exc:
        raise RelayError("中转站返回的不是 JSON，请确认服务地址") from exc
    if not data.get("ok"):
        raise RelayError(data.get("error") or "查询中转清单失败")
    files = data.get("files") or []
    return [f for f in files if isinstance(f, dict) and f.get("id")]


def pull(server: str, code: str, file_id: str,
         timeout: int = DEFAULT_TIMEOUT) -> bytes:
    """拉取单个文件的完整字节。分片已在服务端合并，这里按字节流读回。"""
    base = normalize_server(server)
    code = normalize_code(code)
    resp = requests.get(f"{base}/api/archive",
                        params={"action": "pull", "id": code, "file": file_id},
                        timeout=timeout)
    _raise_for(resp, "拉取文件")
    return resp.content


def clear(server: str, code: str, file_id: str, owner_key: str,
          timeout: int = DEFAULT_TIMEOUT) -> None:
    """从云端删掉已落盘的文件副本。只有 owner 能删，失败不回滚本地文件。"""
    base = normalize_server(server)
    code = normalize_code(code)
    resp = requests.delete(f"{base}/api/archive",
                           params={"action": "clear", "id": code, "file": file_id},
                           headers={"X-Owner-Key": owner_key}, timeout=timeout)
    _raise_for(resp, "删除云端副本")


def receive_all(server: str, code: str, *, already: set[str] | None = None,
                owner_key: str = "", timeout: int = DEFAULT_TIMEOUT) -> dict:
    """把中转站里所有未落盘的 PDF 拉回本地。

    already 是已有的 sha256 集合（来自 LanShare.known_digests），跳过重复。
    返回 {saved, duplicates, rejected, skipped, errors}，逐项可追溯——静默跳过
    会让用户以为传完了，实际少了文件。
    """
    summary = {"saved": [], "duplicates": [], "rejected": [],
               "skipped": [], "errors": []}
    already = set(already or set())
    for meta in list_pending(server, code, timeout):
        digest = str(meta.get("sha256") or "").lower()
        name = str(meta.get("name") or "")
        if digest and digest in already:
            summary["skipped"].append(name)
            continue
        try:
            data = pull(server, code, str(meta["id"]), timeout)
            if digest and len(data) and _digest_bytes(data) != digest:
                summary["rejected"].append((name, "内容摘要不符，传输可能损坏"))
                continue
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
                f.write(data)
                tmp = f.name
            try:
                path, status = phone_sync.receive_pdf(tmp, name=name)
            finally:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
            if path is None:
                summary["rejected"].append((name, status))
            elif status == "duplicate":
                summary["duplicates"].append(path)
            else:
                summary["saved"].append(path)
                if status == "need_rename":
                    summary["rejected"].append(
                        (name, "已保存，但文件名不符合「姓名+手机号+入团志愿书.pdf」，需改名"))
        except (RelayError, OSError, ValueError) as exc:
            summary["errors"].append((name, str(exc)))
            continue
        # 落盘成功后删云端副本；owner_key 为空时跳过（没有删除权限）
        if path and owner_key:
            try:
                clear(server, code, str(meta["id"]), owner_key, timeout)
            except RelayError:
                pass                      # 本地已存好，云端残留下次还能跳过
    if summary["saved"]:
        phone_sync.register_source_dir(phone_sync.sync_dir())
    return summary


def _digest_bytes(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()
