"""整轮测试共用一个 QApplication。

PyQt5 里 QApplication 归 Python 所有：模块级 app 夹具一收尾，最后一个引用掉光，
Qt 就把 QApplication 连同它的所有 QObject 一起删掉（qconfig、qrouter 这些单例
都在其中）。下一批用例再碰 qconfig 就会拿到已析构的对象，报
`RuntimeError: wrapped C/C++ object of type QConfig has been deleted`。

PySide6 的绑定不销毁 C++ 侧对象，所以迁移前的模块级夹具够用；换成 PyQt5 后必须
在会话范围内握住一个引用。各文件现有的 `app` 夹具拿到的是同一个实例，不用改。
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication

_APP = None


@pytest.fixture(scope="session", autouse=True)
def qapp_session():
    global _APP
    # 内嵌浏览器要求共享 OpenGL 上下文，必须在 QApplication 之前设；
    # 应用入口 app/main.py 里也是这么做的，测试少了这一步会撞在 QtWebEngine 初始化上
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
    _APP = QApplication.instance() or QApplication([])
    # PyQt5 在 GC 期间动态加载 Qt 扩展模块会段错误（表现为随机 abort，
    # 栈顶固定是 Garbage-collecting → importlib → 某个 PyQt5 子模块）。
    # 两条一起做：
    #   1) 把用到的 Qt 子模块在这里预先导入，之后不再触发「GC 中途加载扩展」；
    #   2) 冻结当前对象，减少需要扫描的总量。
    import gc

    for _mod in ("PyQt5.QtNetwork", "PyQt5.QtSvg", "PyQt5.QtXml",
                 "PyQt5.QtTest", "PyQt5.QtWebEngineWidgets"):
        try:
            __import__(_mod)
        except Exception:
            pass
    gc.collect()
    gc.freeze()
    yield _APP
    # 不 quit：Qt 在解释器退出时自行收尾，提前 quit 会让残留的 QTimer 依次报错


@pytest.fixture(scope="session", autouse=True)
def isolated_config_root(tmp_path_factory):
    """整轮测试的配置根目录一律指向临时目录。

    不少用例会 store.save / store.update（造数据、验导入），它们不一定带
    isolated_store 夹具——漏掉的那些就直接写进了 ~/.gd12355-helper/config.json，
    把用户真实账号覆盖成测试用的 old1。

    做法有三层，缺一不可：
      1. 改 constants.CONFIG_FILE（后续 import 该名字的模块拿到新值）
      2. 改 store.CONFIG_FILE（store 模块在 import 时就绑定了旧值）
      3. 给已经 import 完、自己留了副本的模块补一遍

    第 3 步只列真实持有副本的模块：遍历 sys.modules 给所有模块 setattr 会碰到
    已加载的扩展模块，解释器在 GC 阶段直接段错误。
    """
    from app import constants
    from app.services import store as store_mod

    root = tmp_path_factory.mktemp("gd-config-root")
    fake = str(root / "config.json")

    constants.CONFIG_FILE = fake
    store_mod.CONFIG_FILE = fake
    for name in ("app.ui.browser_page", "app.ui.upload_page",
                 "app.ui.settings_page", "app.services.single_instance",
                 "app.ui.home_page", "app.ui.org_page"):
        mod = sys.modules.get(name)
        if mod is not None and getattr(mod, "CONFIG_FILE", None):
            mod.CONFIG_FILE = fake
            # 同模块里由 CONFIG_FILE 派生的常量也要跟着走，否则它们还指着
            # 真实的 ~/.gd12355-helper（截图目录、webprofiles 目录）
            base = os.path.dirname(fake)
            if hasattr(mod, "SHOT_DIR"):
                mod.SHOT_DIR = os.path.join(base, "screenshots")
            if hasattr(mod, "PROFILE_ROOT"):
                mod.PROFILE_ROOT = os.path.join(base, "webprofiles")
    yield root


@pytest.fixture(scope="session", autouse=True)
def no_real_login(qapp_session):
    """测试里不许真连官网：SettingsPage 载入了账号就会自动起测活线程，
    真实请求挂在 DNS 上时页面已被下一个用例冲掉，PyQt5 销毁运行中的
    QThread 直接 abort（整轮测试随机崩在这里）。

    封在最底层的 requests.Session.request：FakeClient 类用例不受影响，
    漏网的真实请求立刻抛异常快速失败，而不是挂几十秒等超时。本机回环地址
    除外——云端备份的接口契约要用本地 HTTP 服务验证，那不是联网。"""
    import requests

    _real = requests.Session.request

    def _blocked(self, method, url, **kwargs):
        if "//127.0.0.1" in url or "//localhost" in url:
            return _real(self, method, url, **kwargs)
        raise requests.ConnectionError(f"测试环境禁止联网：{method} {url}")

    requests.Session.request = _blocked
