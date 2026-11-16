"""开机自启动：三平台分支各测各的落点，Linux 不许再把 plist 写进 ~/Library。"""

from __future__ import annotations

import sys


def test_linux_autostart_roundtrip(monkeypatch, tmp_path):
    from app.services import autostart

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(autostart, "AUTOSTART_DESKTOP_DIR",
                        str(home / ".config/autostart"))
    monkeypatch.setattr(autostart, "AUTOSTART_DESKTOP_PATH",
                        str(home / ".config/autostart" / f"{autostart.LABEL}.desktop"))

    assert not autostart.is_installed()
    path = autostart.install()
    assert path == autostart.AUTOSTART_DESKTOP_PATH
    assert autostart.is_installed()
    text = open(path, encoding="utf-8").read()
    assert "--tray" in text and "[Desktop Entry]" in text
    autostart.uninstall()
    assert not autostart.is_installed()


def test_macos_autostart_untouched_by_linux_branch(monkeypatch, tmp_path):
    """macOS 分支仍走 plist，不被新增的 linux 判断截走。"""
    from app.services import autostart

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(autostart, "PLIST_DIR", str(home / "LaunchAgents"))
    monkeypatch.setattr(autostart, "PLIST_PATH",
                        str(home / "LaunchAgents" / f"{autostart.LABEL}.plist"))
    monkeypatch.setattr(autostart, "_launchctl", lambda *a: None)

    assert not autostart.is_installed()
    autostart.install()
    assert autostart.is_installed()
    autostart.uninstall()
    assert not autostart.is_installed()
