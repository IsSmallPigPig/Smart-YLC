"""站点 API 客户端：requests 会话 + 登录 + 团员/组织/分片上传。

只实现只读侦察确认过的接口；写操作仅限上传志愿书（业务必需），
其余一律 GET/POST 查询，绝不触碰站点的管理类写接口。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

import requests

from app.constants import API_BASE_URL, AUTHORIZE_HEADER, SITE_BASE_URL, USER_AGENT
from app.domain.chunk_protocol import (
    build_chunk_fields,
    build_file_add_body,
    build_file_id,
    build_validation_body,
    chunk_count,
    chunk_range,
    parse_chunk_response,
    parse_file_add_response,
    parse_validation_response,
)
from app.domain.check_rules import Diagnostic, MemberRecord
from app.domain.field_map import parse_member, pick_list
from app.domain.login_protocol import (
    CAPTCHA_HEADER,
    CAPTCHA_PATH,
    LOGIN_PATH,
    build_captcha_query,
    build_login_query,
    parse_login_response,
)

DEFAULT_TIMEOUT = 20


@dataclass
class ApiResult:
    ok: bool
    message: str
    data: object = None


class SiteClient:
    """一个客户端 = 一个站点会话（Cookie 隔离），线程内串行使用。"""

    def __init__(self, user_name: str = "", password: str = ""):
        self.user_name = user_name
        self.password = password
        self.authorize_name = ""
        self.http = requests.Session()
        self.http.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Referer": f"{SITE_BASE_URL}/bg/index_main.html",
                "Origin": SITE_BASE_URL,
                "X-Requested-With": "XMLHttpRequest",
            }
        )
        self.logged_in = False

    # ---------- 登录 ----------

    def login(self, login_valid_code: str = "") -> ApiResult:
        """GET /login/adminLogin。密码错一次会触发验证码，调用方捕获 captcha 后让用户看图重试。"""
        if not self.user_name or not self.password:
            return ApiResult(False, "未配置账号密码，请先在「系统配置」填写")
        q = build_login_query(self.user_name, self.password, login_valid_code)
        try:
            resp = self.http.get(API_BASE_URL + LOGIN_PATH, params=q, timeout=DEFAULT_TIMEOUT)
            resp.raise_for_status()
            raw = resp.json()
        except requests.RequestException as e:
            return ApiResult(False, f"网络错误：{e}")
        verdict = parse_login_response(raw)
        if verdict.kind == "ok":
            self.logged_in = True
            self.authorize_name = self.user_name
        return ApiResult(verdict.kind == "ok", verdict.message, {"kind": verdict.kind, "status": verdict.status})

    def fetch_captcha(self) -> bytes | None:
        """获取图形验证码 PNG（带站点要求的特殊头）。"""
        try:
            resp = self.http.get(
                API_BASE_URL + CAPTCHA_PATH,
                params=build_captcha_query(),
                headers={CAPTCHA_HEADER: "Approve"},
                timeout=DEFAULT_TIMEOUT,
            )
            if resp.ok and resp.headers.get("content-type", "").startswith("image"):
                return resp.content
        except requests.RequestException:
            pass
        return None

    def session_account(self) -> ApiResult:
        """只读探测会话有效性。"""
        try:
            resp = self.http.get(API_BASE_URL + "/login/getSessionAccount", timeout=DEFAULT_TIMEOUT)
            resp.raise_for_status()
            raw = resp.json()
        except (requests.RequestException, ValueError) as e:
            return ApiResult(False, f"会话探测失败：{e}")
        r = raw if isinstance(raw, dict) else {}
        status = str(r.get("status") or r.get("code") or "")
        msg = str(r.get("msg") or r.get("message") or "")
        expired = ("失效" in msg or "超时" in msg or "重新登录" in msg or "未登录" in msg) or status == "EXPIRE"
        ok = status in ("OK", "200")
        if ok and not expired:
            data = r.get("msg") if isinstance(r.get("msg"), dict) else r
            username = str((data or {}).get("username") or (data or {}).get("account") or "")
            return ApiResult(True, username or "会话有效", username)
        return ApiResult(False, msg or "会话已失效")

    # ---------- 组织 / 团员（只读） ----------

    def get_messages_page(self, page_no: int = 1, page_size: int = 100, msg_type: int = 0) -> dict:
        """GET /api/v1/admin/message/findAllMessage（前端 MessageApi.findAllMessage）。

        type=0 组织账号；返回信封 {code, total, listData}（total 为本页条数，翻页看 listData）。"""
        try:
            resp = self.http.get(
                API_BASE_URL + "/api/v1/admin/message/findAllMessage",
                params={"type": msg_type, "pageNo": page_no, "pageSize": page_size},
                timeout=DEFAULT_TIMEOUT,
            )
            resp.raise_for_status()
            raw = resp.json()
        except (requests.RequestException, ValueError) as e:
            raise RuntimeError(f"站内信请求失败：{e}") from e
        code = str((raw or {}).get("code", ""))
        if code and code != "200":
            raise RuntimeError(f"站内信接口返回异常：{raw.get('message') or raw.get('msg') or code}")
        return raw

    def _post_api(self, path: str, payload: dict):
        headers = {AUTHORIZE_HEADER: self.authorize_name} if self.authorize_name else {}
        resp = self.http.post(
            API_BASE_URL + path, json=payload, headers=headers, timeout=DEFAULT_TIMEOUT
        )
        resp.raise_for_status()
        return resp.json()

    def get_org_tree(self) -> tuple[list[dict], list[Diagnostic]]:
        """GET/POST /api/v1/admin/org/getOrgTree，返回 [{oid, orgName, parentOid}]。"""
        diagnostics: list[Diagnostic] = []
        try:
            raw = self._post_api("/api/v1/admin/org/getOrgTree", {})
        except (requests.RequestException, ValueError) as e:
            return [], [Diagnostic("error", "org", "-", "NETWORK", f"组织树请求失败：{e}")]
        found: dict[str, dict] = {}
        keys = ("leagueList", "list", "rows", "records", "children", "data")

        def visit(node, depth=0):
            if node is None or depth > 8:
                return
            if isinstance(node, list):
                for n in node:
                    visit(n, depth + 1)
                return
            if not isinstance(node, dict):
                return
            oid = node.get("leagueId") or node.get("oid") or node.get("orgId") or node.get("id")
            name = node.get("leagueName") or node.get("orgName") or node.get("name") or node.get("text")
            if oid is not None and name is not None:
                found[str(oid)] = {"oid": str(oid), "orgName": str(name),
                                   "parentOid": str(node["parentId"]) if node.get("parentId") else ""}
            for k in keys:
                if k in node and node[k] is not node:
                    visit(node[k], depth + 1)

        for pool_key in ("data", "msg"):
            if isinstance(raw, dict) and raw.get(pool_key) is not None:
                visit(raw[pool_key])
        if isinstance(raw, dict):
            visit({k: v for k, v in raw.items() if k not in ("data", "msg")}, -1)
        return list(found.values()), diagnostics

    def get_members_page(self, page_no: int, page_size: int = 100, oid: str = "") -> tuple[list[MemberRecord], int, list[Diagnostic]]:
        """POST /api/v1/admin/members/page。参数名是 page/pageSize（LeagueMemberApi.list 实测）。"""
        diagnostics: list[Diagnostic] = []
        payload = {"page": page_no, "pageSize": page_size}
        if oid:
            payload["oid"] = oid
        try:
            raw = self._post_api("/api/v1/admin/members/page", payload)
        except (requests.RequestException, ValueError) as e:
            raise RuntimeError(f"团员列表请求失败：{e}") from e
        code = str((raw or {}).get("code", ""))
        if code and code != "200":
            raise RuntimeError(
                f"团员列表接口返回业务失败：code={code} message={(raw or {}).get('message', '')}"
            )
        rows, total = pick_list(raw)
        members: list[MemberRecord] = []
        for row in rows:
            try:
                member, diags = parse_member(row, oid)
                members.append(member)
                diagnostics.extend(diags)
            except Exception as e:
                diagnostics.append(Diagnostic("warn", "member", json.dumps(row, ensure_ascii=False)[:80], "PARSE_DRIFT", str(e)))
        return members, total, diagnostics

    def get_all_members(self, oid: str = "", page_size: int = 100, progress=None) -> tuple[list[MemberRecord], list[Diagnostic]]:
        members: list[MemberRecord] = []
        diagnostics: list[Diagnostic] = []
        page_no, total = 1, None
        while True:
            page, t, diags = self.get_members_page(page_no, page_size, oid)
            members.extend(page)
            diagnostics.extend(diags)
            total = t if total is None else total
            if progress:
                progress(len(members), total or 0)
            if not page or (total is not None and len(members) >= total):
                break
            page_no += 1
        return members, diagnostics

    # ---------- 上传（业务必需的写操作） ----------

    def validate_file_names(self, file_names: list[str]):
        try:
            raw = self._post_api("/members/bg/getMemberVarNames", build_validation_body(file_names))
        except (requests.RequestException, ValueError) as e:
            return [None], f"校验请求失败：{e}"
        items = parse_validation_response(raw)
        if not items:
            return [None], "校验接口返回无法解析（可能接口漂移），已保守拦截"
        return items, ""

    def upload_file(self, path: str, progress=None) -> ApiResult:
        """按站点分片协议上传一个 PDF：校验 → 分片 → 入库。"""
        import hashlib
        import os

        name = os.path.basename(path)
        size = os.path.getsize(path)
        md5 = hashlib.md5(open(path, "rb").read()).hexdigest() if size <= 32 * 1024 * 1024 else ""
        file_id = build_file_id(0)

        items, err = self.validate_file_names([name])
        if err and items[0] is None:
            return ApiResult(False, err)
        bad = [i for i in items if i is not None and not i.ok]
        if bad:
            return ApiResult(False, f"站点校验拒绝：{bad[0].name} {bad[0].msg}")

        total_chunks = chunk_count(size)
        with open(path, "rb") as fh:
            for idx in range(total_chunks):
                start, end = chunk_range(idx, size)
                fh.seek(start)
                fields = build_chunk_fields(file_id, name, size, idx, end - start, md5)
                files = {"file": (name, fh.read(end - start), "application/octet-stream")}
                try:
                    resp = self.http.post(
                        API_BASE_URL + "/file/breakpoint-upload",
                        data=fields, files=files, timeout=120,
                    )
                    resp.raise_for_status()
                    try:
                        raw = resp.json()
                    except ValueError:
                        raw = None
                except requests.RequestException as e:
                    return ApiResult(False, f"分片 {idx + 1}/{total_chunks} 上传失败：{e}")
                ok, reason = parse_chunk_response(raw)
                if not ok:
                    return ApiResult(False, f"分片 {idx + 1}/{total_chunks} 被拒绝：{reason}")
                if progress:
                    progress(idx + 1, total_chunks)

        try:
            raw = self._post_api("/file/add", build_file_add_body(name))
        except (requests.RequestException, ValueError) as e:
            return ApiResult(False, f"分片已传完，但入库请求失败：{e}")
        result = parse_file_add_response(raw)
        return ApiResult(result.ok, result.reason, {"mid": result.mid, "retpath": result.retpath})
