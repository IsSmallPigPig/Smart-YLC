"""已配对的手机设备：配对成功后记下来，弹窗里作为「已绑定设备」展示。

为什么单独存而不是塞进 config.json：
* 配对是局域网服务写入的，和账号/通知设置无关，单独一份文件读写互不影响；
* 列表只保留最近 N 台，旧记录自动淘汰，不会把主配置撑大。

失败要显式报：读不出来时抛 RuntimeError（不能返回空列表让下一次写入抹掉历史），
写不进去时也抛，由调用方在界面上提示——用户看不到"绑定成功"却丢记录。
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from dataclasses import dataclass

from app.constants import CONFIG_FILE

PAIRED_FILE = os.path.join(os.path.dirname(CONFIG_FILE), "paired_devices.json")
MAX_DEVICES = 20


@dataclass
class PairedDevice:
    name: str
    host: str = ""
    paired_at: float = 0.0
    last_at: float = 0.0

    def to_dict(self) -> dict:
        return {"name": self.name, "host": self.host,
                "pairedAt": self.paired_at, "lastAt": self.last_at}


def _load_raw() -> list[dict]:
    if not os.path.exists(PAIRED_FILE):
        return []
    try:
        with open(PAIRED_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"已配对设备记录读取失败：{exc}") from exc
    if not isinstance(data, list):
        raise RuntimeError("已配对设备记录格式不对，已停止写入以免覆盖原数据")
    return [x for x in data if isinstance(x, dict)]


def _save_raw(items: list[dict]) -> None:
    directory = os.path.dirname(PAIRED_FILE)
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".paired-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
        shutil.copyfile(tmp, PAIRED_FILE + ".bak")
        os.replace(tmp, PAIRED_FILE)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def list_devices() -> list[PairedDevice]:
    out: list[PairedDevice] = []
    for item in _load_raw():
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        out.append(PairedDevice(
            name=name[:60],
            host=str(item.get("host") or "")[:64],
            paired_at=float(item.get("pairedAt") or 0),
            last_at=float(item.get("lastAt") or 0),
        ))
    return out


def remember(name: str, host: str = "", now: float | None = None) -> list[PairedDevice]:
    """记一次配对：同名设备合并，保留首次时间、更新最近时间。"""
    stamp = time.time() if now is None else now
    clean_name = str(name or "手机").strip()[:60] or "手机"
    items = [d.to_dict() for d in list_devices()]
    for item in items:
        if item["name"] == clean_name and item["host"] == str(host):
            item["lastAt"] = stamp
            break
    else:
        items.append(PairedDevice(clean_name, str(host), stamp, stamp).to_dict())
    # 按最近配对时间排序，只留最近的若干台
    items.sort(key=lambda d: float(d.get("lastAt") or 0), reverse=True)
    _save_raw(items[:MAX_DEVICES])
    return list_devices()


def forget(name: str, host: str = "") -> list[PairedDevice]:
    items = [d.to_dict() for d in list_devices()
             if not (d.name == name and d.host == host)]
    _save_raw(items)
    return list_devices()
