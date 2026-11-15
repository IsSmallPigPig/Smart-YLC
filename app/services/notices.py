"""应用内通知中心：把「当前存在的问题」收敛成一份清单。

设计目的（对应「消息不重复弹、公告面板要稳」）：
- 弹窗/系统通知只对**新出现**的问题发一次。同一问题内容不变就静默，改动了才更新；
- 页面上的公告面板读同一份清单，按 key 增量更新，不整块清空重画，因此不会闪；
- 每条通知带一个 `action`，形如 `accounts:<账号>` / `org:<支部>` / `messages`，
  由主窗口解释成「切到某页并定位到具体位置」，弹窗据此生成对应的处理按钮。
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QObject, Signal

LEVEL_ISSUE = "issue"
LEVEL_INFO = "info"


@dataclass(frozen=True)
class Notice:
    """一条通知。frozen 便于直接比较内容是否变化。"""

    key: str
    title: str
    detail: str = ""
    level: str = LEVEL_ISSUE
    action: str = ""          # 目标：accounts:<账号> / org:<支部> / messages / upload
    action_text: str = ""     # 操作按钮文案，如「去设置」「查看」


class NoticeCenter(QObject):
    """当前通知清单的唯一持有者。UI 只订阅信号，不自己造状态。"""

    changed = Signal()        # 清单有变化（面板据此增量刷新）
    new_issue = Signal(object)  # 新出现的 issue（弹窗/系统通知据此触发）

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items: dict[str, Notice] = {}

    # ---------- 读 ----------

    def snapshot(self) -> list[Notice]:
        """问题在前、按标题排序；面板按这个顺序铺。"""
        return sorted(self._items.values(),
                      key=lambda n: (n.level != LEVEL_ISSUE, n.title))

    def issues(self) -> list[Notice]:
        return [n for n in self.snapshot() if n.level == LEVEL_ISSUE]

    def issue_count(self) -> int:
        return sum(1 for n in self._items.values() if n.level == LEVEL_ISSUE)

    def get(self, key: str) -> Notice | None:
        return self._items.get(key)

    # ---------- 写 ----------

    def upsert(self, notice: Notice) -> bool:
        """新增或更新一条。内容没变返回 False（不触发信号，避免无谓闪烁）。"""
        old = self._items.get(notice.key)
        if old == notice:
            return False
        self._items[notice.key] = notice
        self.changed.emit()
        if old is None and notice.level == LEVEL_ISSUE:
            self.new_issue.emit(notice)
        return True

    def remove(self, key: str) -> bool:
        if self._items.pop(key, None) is not None:
            self.changed.emit()
            return True
        return False

    def replace_prefix(self, prefix: str, notices: list[Notice]) -> None:
        """用一组通知整体替换 key 以 prefix 开头的旧通知，其余保持不动。

        用于「每轮同步重算某类问题」：先算出当前全部异常再整体替换，
        比逐条 remove 干净，且只发一次 changed。
        """
        new_keys = {n.key for n in notices}
        changed = False
        for key in [k for k in self._items if k.startswith(prefix) and k not in new_keys]:
            del self._items[key]
            changed = True
        fresh: list[Notice] = []
        for n in notices:
            if self._items.get(n.key) == n:
                continue
            if n.key not in self._items and n.level == LEVEL_ISSUE:
                fresh.append(n)
            self._items[n.key] = n
            changed = True
        if changed:
            self.changed.emit()
        for n in fresh:
            self.new_issue.emit(n)
