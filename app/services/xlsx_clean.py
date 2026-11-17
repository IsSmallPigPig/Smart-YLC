"""xlsx 写盘前的字符清洗。

系统抓回来的成员姓名、单位等字段偶发混入不可见控制字符（\x00-\x08、\x0B-\x0C、
\x0E-\x1F 等）。openpyxl 对单元格值会直接抛
`IllegalCharacterError: <值> cannot be used in worksheets`——值原样打印进报错，
控制字符显示为乱码，用户看到的就是「乱码 cannot be used in worksheet」。
Excel 本身也不能显示这些字符，写入前一律剥掉；剥空了写空串，不让一行数据毁掉整个导出。

缓存里已存的历史脏数据也靠这里兜住：清洗放在写盘边界，而不是抓取端，
这样老用户不用重新同步全部账号也能导出。
"""

from __future__ import annotations

import re

# openpyxl ILLEGAL_CHARACTERS_RE 同款口径：ASCII 控制字符（保留 \t \n \r）
ILLEGAL_XLSX_CHARS = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F]")


def clean(value) -> object:
    """单元格值清洗：字符串剥控制字符，其他类型原样交给 openpyxl。"""
    if isinstance(value, str):
        return ILLEGAL_XLSX_CHARS.sub("", value)
    return value


def clean_row(row: dict, headers) -> list:
    """按表头取一行数据并清洗，用于 ws.append()。"""
    return [clean(row.get(h, "")) for h in headers]
