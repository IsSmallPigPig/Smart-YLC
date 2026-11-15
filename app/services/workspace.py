"""工作区待上传列表（pending_uploads.json）+ 新文件自动处理（纯函数部分）。

工作区目录里出现新 PDF 时：
  1. 命名检查：能解析出「姓名+手机号」但格式不对 → 自动改名为标准名
  2. 压缩：超过 9.5MB → 光栅化压缩到阈值内（覆盖原文件前先写临时文件）
  3. 记入待上传列表，批量上传页从这里取文件
"""

from __future__ import annotations

import json
import os

from app.constants import LIMITS
from app.domain.naming import analyze_file_name


def _pending_path() -> str:
    """待上传列表放在配置旁边；路径跟 store 走，测试里换配置目录时一起隔离。"""
    from app.services import store

    return os.path.join(os.path.dirname(store.CONFIG_FILE), "pending_uploads.json")


def load_pending() -> list[dict]:
    try:
        with open(_pending_path(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def save_pending(entries: list[dict]) -> None:
    os.makedirs(os.path.dirname(_pending_path()), exist_ok=True)
    with open(_pending_path(), "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)


def add_pending(path: str, message: str = "") -> bool:
    entries = load_pending()
    if any(e.get("path") == path for e in entries):
        return False
    entries.append({
        "path": path,
        "name": os.path.basename(path),
        "size": os.path.getsize(path) if os.path.isfile(path) else 0,
        "message": message,
    })
    save_pending(entries)
    return True


def remove_pending(path: str) -> None:
    save_pending([e for e in load_pending() if e.get("path") != path])


def load_workspace_dirs(cfg: dict | None = None) -> list[str]:
    """参与扫描的目录列表。

    `workspaceDirs` 是正式字段（可多个）；只有旧的单值 `workspaceDir` 时
    把它当成唯一一个目录，老配置不用迁移也能直接用。"""
    if cfg is None:
        from app.services import store

        cfg = store.load()
    dirs = [str(d).strip() for d in (cfg.get("workspaceDirs") or []) if str(d).strip()]
    if not dirs:
        single = str(cfg.get("workspaceDir") or "").strip()
        dirs = [single] if single else []
    return list(dict.fromkeys(dirs))


def save_workspace_dirs(dirs: list[str]) -> list[str]:
    """写入目录列表（去重保序）；`workspaceDir` 跟着第一个目录走。

    旧字段留着是因为设置页只有一个输入框、托盘启动也读它；两者写在同一处，
    不会出现「列表有 A、单值却是 B」的分叉。"""
    from app.services import store

    clean = list(dict.fromkeys(str(d).strip() for d in dirs if str(d).strip()))

    def _write(cfg: dict):
        cfg["workspaceDirs"] = clean
        cfg["workspaceDir"] = clean[0] if clean else ""

    store.update(_write)
    return clean


def new_pdf_files(directory: str, known: set[str], recursive: bool = True) -> list[str]:
    """目录里新出现的 PDF（不在 known 集合里的绝对路径）。

    recursive 时连子目录一起找：来源目录允许是「整个年级」这种带层次的文件夹，
    只扫一层会漏掉子文件夹里的档案。"""
    if not os.path.isdir(directory):
        return []
    out = []
    if recursive:
        for dirpath, dirnames, filenames in os.walk(directory):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
            for f in sorted(filenames):
                p = os.path.join(dirpath, f)
                if f.lower().endswith(".pdf") and p not in known:
                    out.append(p)
        return out
    for f in sorted(os.listdir(directory)):
        p = os.path.join(directory, f)
        if f.lower().endswith(".pdf") and os.path.isfile(p) and p not in known:
            out.append(p)
    return out


def prepare_pdf(path: str, target_bytes: int = LIMITS["PDF_DEFAULT_TARGET_BYTES"]) -> dict:
    """对单个工作区 PDF 执行：命名检查（可解析则改名）→ 压缩。

    返回 {path, renamed_from, compressed, name, size, problem, message}。
    problem 非空表示命名无法自动修复（文件保留原样，照样入列表，上传页会拦）。
    """
    info = {"renamed_from": "", "compressed": False, "problem": "", "message": ""}
    d = os.path.dirname(path)
    cur = path

    a = analyze_file_name(os.path.basename(cur))
    if a.parsed and a.suggested_name != os.path.basename(cur):
        dst = os.path.join(d, a.suggested_name)
        if not os.path.exists(dst):
            os.rename(cur, dst)
            info["renamed_from"] = os.path.basename(cur)
            cur = dst
    elif not a.parsed:
        info["problem"] = a.problem

    if os.path.getsize(cur) > target_bytes:
        from app.services.compress import compress_pdf

        tmp = cur + ".tmp.pdf"
        r = compress_pdf(cur, tmp, target_bytes)
        if r.get("over"):
            os.remove(tmp)
            info["message"] = f"压缩到最低参数仍 {r['dst_size'] / 1048576:.1f}MB，保持原样"
        else:
            os.replace(tmp, cur)
            info["compressed"] = True
            info["message"] = f"已压缩 {r['src_size'] / 1048576:.1f}MB → {r['dst_size'] / 1048576:.1f}MB"

    info["path"] = cur
    info["name"] = os.path.basename(cur)
    info["size"] = os.path.getsize(cur)
    return info
