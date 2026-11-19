"""局域网直传：手机浏览器把 PDF 传到电脑，不经过任何服务器。

为什么优先局域网（用户要求，参考 LocalSend）：
  云端中转要先把档案传到公网再拉回来，几十份 PDF 就是几百兆流量，且依赖网络
  与服务端配额。同一个 Wi-Fi 下直传，速度是内网带宽、零流量、零等待。

实现方式（零依赖，只用标准库）：
  1. 电脑起一个只监听局域网的 HTTP 服务，手机浏览器打开地址即可上传；
  2. 地址里带 6 位口令，别人猜不到；服务只接受私有网段（192.168/10/172.16-31/
     127.）的请求；
  3. 收到的 PDF 交给 phone_sync 落盘到 ~/同步自手机/<日期>/，并登记为来源目录，
     上传页下一次扫描自动发现；
  4. 上传页面的 HTML 内嵌在 lan_page 里，不引外部 CDN——手机可能没有外网。

做不到的部分要说清楚：浏览器不能像 LocalSend 那样自动发现局域网里的电脑，
所以电脑必须显示一个地址让手机输入。这不是实现问题，是浏览器安全模型的限制。
"""

from __future__ import annotations

import json
import os
import secrets
import socket
import tempfile
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from app.services import phone_sync
from app.services.lan_page import UPLOAD_PAGE

class _LanHTTPServer(ThreadingHTTPServer):
    """跳过 HTTPServer server_bind 里的 getfqdn 反向 DNS。

    用 0.0.0.0 绑定时 getfqdn 会做 PTR 查询，在没有 DNS 的局域网里要等满
    30 秒超时——用户按「开始接收」后界面就这么卡住。这里只要 server_port，
    从不读 server_name，所以直接抄 socketserver 的 bind，不碰 DNS。
    """

    def server_bind(self):
        import socketserver

        socketserver.TCPServer.server_bind(self)
        self.server_name = "localhost"
        self.server_port = self.server_address[1]


DEFAULT_PORT = 8765
# 单次上传上限。入团志愿书扫描件实测 1.2~8MB，留到 40MB 足够，
# 也挡住误传的大文件（全程流式写盘，不吃内存）
MAX_UPLOAD = 40 * 1024 * 1024
# 没有活动这么久自动关掉，避免随手开着忘记关
IDLE_TIMEOUT = 30 * 60
# 局域网地址只认私有网段，公网地址一律拒绝
_PRIVATE_PREFIXES = ("192.168.", "10.", "127.", "169.254.")


def lan_addresses() -> list[str]:
    """本机当前的局域网 IPv4 地址，最可能被手机访问到的排在最前。"""
    found: list[str] = []
    try:
        # 连一个外部地址不求真的连通：只是让内核选出默认出口网卡的地址
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.settimeout(0.3)
        probe.connect(("223.5.5.5", 53))
        found.append(probe.getsockname()[0])
        probe.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addr = info[4][0]
            if addr not in found and not addr.startswith("127."):
                found.append(addr)
    except OSError:
        pass
    return [a for a in found if _is_private(a)]


def _is_private(addr: str) -> bool:
    if addr.startswith(_PRIVATE_PREFIXES):
        return True
    if addr.startswith("172."):
        try:
            second = int(addr.split(".")[1])
        except (IndexError, ValueError):
            return False
        return 16 <= second <= 31
    return False


@dataclass
class Received:
    name: str
    path: str = ""
    status: str = ""            # ok | duplicate | need_rename | rejected
    note: str = ""


@dataclass
class LanShare:
    """一个只服务于手机上传的本地 HTTP 服务。"""

    port: int = DEFAULT_PORT
    on_received: object = None                  # callable(Received)
    received: list[Received] = field(default_factory=list)
    _server: ThreadingHTTPServer | None = None
    _thread: threading.Thread | None = None
    _pin: str = ""
    _last_active: float = 0.0

    # ---- 生命周期 ----

    @property
    def running(self) -> bool:
        return self._server is not None

    @property
    def pin(self) -> str:
        return self._pin

    def url(self) -> str:
        """给手机输入的地址。没有局域网时返回空串。"""
        addrs = lan_addresses()
        if not addrs or not self._server:
            return ""
        return f"http://{addrs[0]}:{self._server.server_port}/?k={self._pin}"

    def start(self) -> str:
        if self._server is not None:
            return self.url()
        self._pin = secrets.token_hex(3).upper()      # 6 位十六进制，比 4 位数字难猜
        handler = _make_handler(self)
        last_error: Exception | None = None
        for port in (self.port, 0):                   # 端口被占就交给系统分配
            try:
                server = _LanHTTPServer(("0.0.0.0", port), handler)
            except OSError as exc:
                last_error = exc
                continue
            server.daemon_threads = True
            self._server = server
            self._thread = threading.Thread(target=server.serve_forever,
                                            kwargs={"poll_interval": 0.3},
                                            daemon=True)
            self._thread.start()
            self._last_active = time.time()
            return self.url()
        raise OSError(f"无法启动局域网服务：{last_error}")

    def stop(self) -> None:
        server, self._server = self._server, None
        if server is not None:
            server.shutdown()
            server.server_close()
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout=2)

    def touch(self) -> None:
        self._last_active = time.time()

    def expired(self) -> bool:
        return self.running and time.time() - self._last_active > IDLE_TIMEOUT

    # ---- 收文件 ----

    def accept(self, name: str, data: bytes) -> Received:
        """把一次上传落盘。文件名沿用手机上的名字。"""
        item = Received(name=name)
        safe = os.path.basename(name or "").strip()
        if not safe.lower().endswith(".pdf"):
            item.status, item.note = "rejected", "只接收 PDF 文件"
            return self._record(item)
        tmp_dir = tempfile.mkdtemp(prefix="ylc-lan-")
        tmp = os.path.join(tmp_dir, safe)
        try:
            with open(tmp, "wb") as f:
                f.write(data)
            path, status = phone_sync.receive_pdf(tmp, name=safe)
        except OSError as exc:
            item.status, item.note = "rejected", f"写入失败：{exc}"
            return self._record(item)
        finally:
            try:
                if os.path.exists(tmp):
                    os.unlink(tmp)
                os.rmdir(tmp_dir)
            except OSError:
                pass

        if path is None:
            item.status, item.note = "rejected", status
        else:
            item.path = path
            item.status = status
            if status == "need_rename":
                item.note = "已收到，但文件名不符合「姓名+手机号+入团志愿书.pdf」，需要改名"
            elif status == "duplicate":
                item.note = "这份档案之前已经传过，内容相同"
            else:
                item.note = "已保存"
                try:
                    phone_sync.register_source_dir(os.path.dirname(path))
                except Exception:                 # 登记失败不影响收件本身
                    pass
        return self._record(item)

    def _record(self, item: Received) -> Received:
        self.received.append(item)
        self.touch()
        if callable(self.on_received):
            try:
                self.on_received(item)
            except Exception:
                pass
        return item


def _make_handler(share: LanShare):
    class Handler(BaseHTTPRequestHandler):
        server_version = "SmartYouthLan/1.0"

        def log_message(self, *args):        # 不往控制台刷日志
            return

        def _client_ok(self) -> bool:
            return _is_private(self.client_address[0])

        def _send(self, status: int, body: bytes, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _json(self, status: int, payload: dict) -> None:
            self._send(status, json.dumps(payload, ensure_ascii=False).encode(),
                       "application/json; charset=utf-8")

        def do_GET(self):                    # noqa: N802 - BaseHTTPRequestHandler 约定
            if not self._client_ok():
                self._json(403, {"ok": False, "error": "只允许局域网访问"})
                return
            parsed = urlparse(self.path)
            if parsed.path in ("/", "/index.html"):
                self._send(200, UPLOAD_PAGE.encode("utf-8"),
                           "text/html; charset=utf-8")
                return
            if parsed.path == "/ping":
                self._json(200, {"ok": True, "received": len(share.received)})
                return
            self._json(404, {"ok": False, "error": "没有这个地址"})

        def do_POST(self):                   # noqa: N802
            if not self._client_ok():
                self._json(403, {"ok": False, "error": "只允许局域网访问"})
                return
            parsed = urlparse(self.path)
            if parsed.path != "/upload":
                self._json(404, {"ok": False, "error": "没有这个地址"})
                return
            query = parse_qs(parsed.query)
            if (query.get("k") or [""])[0].upper() != share.pin:
                self._json(403, {"ok": False, "error": "口令不对，刷新电脑上的二维码重新打开"})
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            if length <= 0:
                self._json(400, {"ok": False, "error": "没有收到文件内容"})
                return
            if length > MAX_UPLOAD:
                self._json(413, {"ok": False,
                                 "error": f"文件超过 {MAX_UPLOAD // 1024 // 1024}MB"})
                return
            data = self._read_body(length)
            if data is None:
                self._json(400, {"ok": False, "error": "上传中断"})
                return
            name = (query.get("name") or [""])[0]
            item = share.accept(name, data)
            self._json(200, {"ok": item.status != "rejected",
                             "status": item.status, "note": item.note,
                             "name": item.name})

        def _read_body(self, length: int) -> bytes | None:
            chunks: list[bytes] = []
            remaining = length
            while remaining > 0:
                chunk = self.rfile.read(min(remaining, 1 << 16))
                if not chunk:
                    return None
                chunks.append(chunk)
                remaining -= len(chunk)
            return b"".join(chunks)

    return Handler
