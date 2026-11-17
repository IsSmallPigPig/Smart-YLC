"""分片上传协议（移植自 chunk-protocol.ts，站点只读侦察结论）。

/file/breakpoint-upload 协议规格（来源 chunk_uploader.js / league_member.js）：
1) 校验（POST JSON） /members/bg/getMemberVarNames
     body   { fileNames: [...] } → { status:'OK', msg:{ data:[{ name,status,msg }] } }
2) 分片（POST multipart） /file/breakpoint-upload，顺序上传，成功一片再下一片
     file | id | chunks | chunk(0-based) | size | name | md5(可空)，分片 10MB
3) 入库（POST JSON） /file/add  body { fileName, suffix } → { status:'OK', msg:{ mid, retpath, token } }
4) 审核（POST JSON） /members/bg/auditVoluntaryBook?mid=<团员id>&status=2（通过）/ 1+refuse（拒绝），
   响应 { status:'OK' } 即成功；与网页端「审核」按钮同一接口（league_member.js auditVoluntaryBook），
   上传入库成功后由应用自动调用。
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

from app.constants import CONFIRM_PHRASE, LIMITS, MB

CHUNK_BYTES = LIMITS["CHUNK_BYTES"]
MAX_BATCH = LIMITS["MAX_UPLOAD_PER_BATCH"]
SITE_LIMIT = LIMITS["SITE_SINGLE_FILE_BYTES"]


def build_file_id(index: int, now: float | None = None) -> str:
    return f"file_{int(now if now is not None else time.time() * 1000)}_{index}"


def chunk_count(size: int, chunk_bytes: int = CHUNK_BYTES) -> int:
    if size <= 0:
        return 1
    return math.ceil(size / chunk_bytes)


def chunk_range(index: int, size: int, chunk_bytes: int = CHUNK_BYTES) -> tuple[int, int]:
    start = index * chunk_bytes
    return start, min(start + chunk_bytes, size)


def build_chunk_fields(file_id: str, name: str, size: int, chunk_index: int, chunk_size: int, md5: str = "") -> dict:
    return {
        "id": file_id,
        "chunks": str(chunk_count(size)),
        "chunk": str(chunk_index),
        "size": str(chunk_size),
        "name": name,
        "md5": md5,
    }


def build_validation_body(file_names: list[str]) -> dict:
    return {"fileNames": file_names}


def build_file_add_body(file_name: str) -> dict:
    suffix = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
    return {"fileName": file_name, "suffix": suffix}


@dataclass
class ValidationItem:
    name: str
    status: str
    msg: str
    ok: bool


def parse_validation_response(raw) -> list[ValidationItem]:
    """宽容解析：status 可能为 '0'/'1' 或 'OK'/'ERROR'，msg 可能是字符串或对象。"""
    if not isinstance(raw, dict):
        return []
    data = (raw.get("msg") or {}).get("data") if isinstance(raw.get("msg"), dict) else None
    if not isinstance(data, list):
        return []
    out: list[ValidationItem] = []
    for item in data:
        o = item if isinstance(item, dict) else {}
        status = str(o.get("status", ""))
        up = status.upper()
        out.append(
            ValidationItem(
                str(o.get("name", "")),
                status,
                str(o.get("msg", "")),
                status == "0" or up in ("OK", "SUCCESS"),
            )
        )
    return out


@dataclass
class FileAddResult:
    ok: bool
    reason: str
    mid: str = ""
    retpath: str = ""
    token: str = ""


def parse_file_add_response(raw) -> FileAddResult:
    if not isinstance(raw, dict):
        return FileAddResult(False, "入库接口返回空")
    status = str(raw.get("status") or raw.get("code") or "")
    msg = raw.get("msg")
    text = msg if isinstance(msg, str) else str(raw.get("message") or "")
    ok = status in ("OK", "200", "0")
    if not ok:
        return FileAddResult(False, text or f"入库失败（status={status or '空'}）")
    o = msg if isinstance(msg, dict) else {}
    return FileAddResult(
        True,
        text or "已入库，随后自动提交审核",
        str(o.get("mid") or ""),
        str(o.get("retpath") or ""),
        str(o.get("token") or ""),
    )


def parse_audit_response(raw) -> tuple[bool, str]:
    """审核接口响应：status=='OK' 即成功（对照网页端 auditVoluntaryBook 的判定）。"""
    if not isinstance(raw, dict):
        return False, "审核接口返回空"
    status = str(raw.get("status") or "")
    msg = raw.get("msg") if isinstance(raw.get("msg"), str) else ""
    if status == "OK":
        return True, msg or "审核通过"
    return False, msg or f"审核未通过（status={status or '空'}）"


def parse_chunk_response(raw) -> tuple[bool, str]:
    if raw is None:
        return True, "空响应按成功处理（站点原逻辑只判 HTTP 成功）"
    if isinstance(raw, dict):
        status = str(raw.get("status") or raw.get("code") or "")
        if status in ("ERROR", "1") or ("code" in raw and str(raw["code"]) != "200"):
            return False, str(raw.get("msg") or raw.get("message") or "分片被拒绝")
    return True, "分片已接收"


def check_site_limits(files: list[dict]) -> list[str]:
    """站点硬限：单文件 ≤10MB、单次 ≤50 份、仅 PDF。超限时返回明确原因。"""
    reasons: list[str] = []
    if len(files) > MAX_BATCH:
        reasons.append(f"一次最多 {MAX_BATCH} 份，当前 {len(files)} 份；请先分批")
    for f in files:
        name, size = f["name"], f["size"]
        if not name.lower().endswith(".pdf"):
            reasons.append(f"{name}：站点仅支持 .pdf")
        if size > SITE_LIMIT:
            reasons.append(f"{name}：{size / MB:.2f}MB 超过站点 10MB 限制，请回到「批量压缩」用 9.5MB 预设重压")
    return reasons
