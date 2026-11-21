"""OrgPage 的组织状况持久化行为 mixin：方法体从 org_page.py 原样提取。

_read/_write 的 store 调用走 self 与模块 import；_persist_account 被 _offline_snapshot
通过 self 调用，MRO 置前、无 __init__，行为不变。
"""

from __future__ import annotations

from datetime import datetime

from app.domain.org_stats import KIND_LABEL, aggregate_org_stats
from app.services import store
from app.ui.org_page_widgets import KIND_COLOR, _UNPAID

class OrgPagePersistMixin:
    """组织状况缓存的落盘与恢复。"""

    GRAD_SUFFIX = "（毕业应转出）"

    @staticmethod
    def _transfer_dict(t) -> dict:
        return {"applied": t.applied, "total": t.total, "succeeded": t.succeeded,
                "pending": t.pending, "returned": t.returned, "names": list(t.names)}

    def _aggregate(self, cache: dict) -> dict:
        """整份 orgStatsCache 的汇总口径在 domain 层，UI 只做转调。"""
        return aggregate_org_stats(cache)

    def _persist_account(self, user, branch, total, uploaded, by_kind, transfer, rows,
                         unpaid: float = 0.0, extras: dict | None = None):
        """本支部统计成功 → 写入配置 orgStatsCache；重启后从缓存恢复，不被清除。

        transfer 为 None（离线重算）时保留上次在线结果；已删除账号的旧条目
        随本次写盘一并清理，避免僵尸支部常驻。"""

        valid = {a.get("userName", "") for a in self.window_ref.settings_page.accounts}
        key = user.replace(self.GRAD_SUFFIX, "")

        def _write(cfg: dict):
            cache = cfg.setdefault("orgStatsCache", {})
            # valid 为空只可能是账号还没载入；这时清理会把整份统计抹掉
            if valid:
                for k in [k for k in cache if k not in valid]:
                    del cache[k]
            existing = cache.get(key) or {}
            cache[key] = {
                "branch": branch,
                "total": total,
                "uploaded": uploaded,
                "by_kind": {k: by_kind.get(k, 0) for k in ("identity", "report", "archive", "fees")},
                "transfer": self._transfer_dict(transfer) if transfer else existing.get("transfer"),
                "error_rows": [vars(r) for r in rows],
                # 金额单独存，别混进 by_kind 的四个分类里。
                # 在线路径从旁路键取，离线路径由调用方显式传入。
                "unpaidAmount": round(float(unpaid or by_kind.get(_UNPAID, 0) or 0), 2),
                "extras": dict(extras or {}),
                "updatedAt": datetime.now().isoformat(timespec="seconds"),
            }

        store.update(_write)

    def _restore_persisted(self):
        """启动时从 orgStatsCache 恢复上次的组织状况（在线统计成功/离线重算
        都会更新缓存，重启不清除；正在统计时不覆盖）。"""
        if self.worker is not None and self.worker.isRunning():
            return

        cache = store.load().get("orgStatsCache") or {}
        if not cache:
            # 有成员缓存却没有统计缓存（首次启动、导入配置、换机恢复备份）：
            # 页面会一直停在 0，直到 30 秒后的成员同步才重算。这里直接离线算一次，
            # 不联网、不打扰，打开就有数。
            if (store.load().get("memberCache") or {}):
                self.refresh_offline()
            return
        agg = self._aggregate(cache)
        self._total, self._uploaded = agg["total"], agg["uploaded"]
        self._by_kind = agg["by_kind"]
        self._error_rows = agg["rows"]
        self._transfer_applied = agg["applied"]
        self._set_ratio(agg["total"], agg["uploaded"])
        self._update_bars()
        self._clear_cards()
        for r in agg["rows"]:
            self._append_card(r.name, r.mobile, [k for k in KIND_COLOR if KIND_LABEL[k] in r.kinds],
                              r.problems, r.branch)
        self.error_title.setText(f"异常团员 {len(agg['rows'])}")
        if self._detail_open:
            self._apply_detail_filter()  # 统计刷新时保持当前类别筛选
        # 缓存里是 ISO 时间串，直接显示会带 T 和秒；截图/阅读都不好看
        updated = agg["updated"]
        stamp = f"，更新于 {updated.replace('T', ' ')[:16]}" if updated else ""
        self.transfer_note.setText(
            f"转接申请 {agg['applied']} 人，成功 {agg['succeeded']}，"
            f"审核中 {agg['pending']}，退回 {agg['returned']}{stamp}")

