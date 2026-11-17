"""账号模板生成与导入解析（账号/密码必填，备注/届别可选）。"""

import tempfile

from app.services.excel import save_account_template


def test_account_template_roundtrip(tmp_path=None):
    """模板直接批量导入：备注/届别留空也能整行读到。"""
    import os

    path = os.path.join(tempfile.mkdtemp(), "tpl.xlsx")
    n = save_account_template(path)
    assert n == 2

    from openpyxl import load_workbook

    wb = load_workbook(path, read_only=True, data_only=True)
    data = list(wb.active.iter_rows(values_only=True))
    wb.close()
    header = [str(c or "").strip() for c in data[0]]
    assert "账号" in header and "密码" in header
    u = next(i for i, h in enumerate(header) if h == "账号")
    p = next(i for i, h in enumerate(header) if h == "密码")
    r = next(i for i, h in enumerate(header) if h == "备注")
    y = next(i for i, h in enumerate(header) if h == "届别")
    rows = []
    for raw in data[1:]:
        cell = lambda i: str(raw[i]).strip() if raw[i] is not None else ""
        if cell(u) and cell(p):
            rows.append((cell(u), cell(p), cell(r), cell(y)))
    assert rows[0] == ("ssfz12407", "-abc12345", "普通高中高三七班", "2026")
    # 备注与届别留空的行不丢
    assert rows[1] == ("ssfz12401", "-abc12345", "", "")
