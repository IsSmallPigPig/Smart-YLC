"""团员字段解析：null 是「未采集」，不是接口漂移。"""

from __future__ import annotations

from app.domain.field_map import parse_member


def _raw(**overrides):
    row = {
        "name": "张三",
        "mobile": "13800000001",
        "mid": "1001",
        "oid": "o1",
        "isRealName": None,
        "auditStatus": None,
        "volunteerBookModifyStatus": None,
        "electronicHeadPictureStatus": None,
        "electronicHeadPicture": None,
    }
    row.update(overrides)
    return row


def test_null_ecard_status_is_not_drift():
    """站点对未采集照片返回 null；不能报「字段没返回」，否则每次同步都一堆误报。"""
    member, diags = parse_member(_raw())
    assert member.ecard_photo_status == "未上传"
    assert not [d for d in diags if d.code == "PARSE_DRIFT"]


def test_translated_null_status_stays_empty_without_drift():
    member, diags = parse_member(_raw())
    assert member.real_name_status == ""
    assert member.ebook_status == ""
    assert not [d for d in diags if d.code == "PARSE_DRIFT"]


def test_really_missing_field_still_drifts():
    """键真的消失时仍要报，别把接口改名也一起放过。"""
    row = _raw()
    row.pop("isRealName")
    _, diags = parse_member(row)
    assert any(d.code == "PARSE_DRIFT" and "real_name_status" in d.reason
               for d in diags)


def test_fonts_dir_prefers_packaged_assets(tmp_path, monkeypatch):
    """打包后字体在 _MEIPASS/assets/fonts，不能拿源码路径误报缺失。"""
    import sys

    from app.fonts import fonts_dir

    packed = tmp_path / "assets" / "fonts"
    packed.mkdir(parents=True)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert fonts_dir() == packed
