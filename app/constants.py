"""站点实测硬约束与业务规则（移植自 Electron 版 constants.ts）。

页面 / 静态资源在 tuan.12355.net，所有 AJAX 走 tuanapi.12355.net
（League.path = League.newPath = env.publish = 'https://tuanapi.12355.net'，
$.ajaxSetup({ xhrFields: { withCredentials: true } }) 跨域带 Cookie）。
"""

import re

SITE_BASE_URL = "https://tuan.12355.net"
API_BASE_URL = "https://tuanapi.12355.net"
"""新接口（League.newApiProxy）要求把 sessionStorage.admin 放进这个请求头。"""
AUTHORIZE_HEADER = "authorize_name"
"""新接口响应 code === '200' 为成功；旧接口响应 status === 'OK' 为成功。"""
NEW_API_OK_CODE = "200"
LEGACY_API_OK_STATUS = "OK"

MB = 1024 * 1024

LIMITS = {
    "MAX_UPLOAD_PER_BATCH": 50,
    "SITE_SINGLE_FILE_BYTES": 10 * MB,
    "PDF_DEFAULT_TARGET_BYTES": round(9.5 * MB),
    "PDF_20MB_PRESET_BYTES": round(19.5 * MB),
    "CHUNK_BYTES": 10 * MB,
    "DEFAULT_CONCURRENCY": 2,
    "DEFAULT_MIN_INTERVAL_MS": 500,
    "MAX_COMPRESS_ITERATIONS": 12,
    "MIN_DPI": 72,
    "MIN_JPEG_QUALITY": 35,
}

MOBILE_RE = re.compile(r"^1[3-9]\d{9}$")
NAME_SUFFIX = "入团志愿书"
FULLWIDTH_PLUS = "\uFF0B"
FULLWIDTH_MINUS = "\uFF0D"
ILLEGAL_CHARS_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
PDF_EXT_RE = re.compile(r"\.pdf$", re.IGNORECASE)
"""真实上传前需要在确认框里手输的短语。"""
CONFIRM_PHRASE = "确认上传"

"""站点控件同时接受半角 + 与半角 -；上传说明规定 +。"""
SEPARATORS = {"standard": "+", "alternate": "-"}

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
)

"""只读 GET 静态资源，用于检测接口漂移。"""
FINGERPRINT_ASSETS = (
    "/bg/index_main.html",
    "/bg/public/js/module/League.js",
    "/bg/public/js/module/LeagueMemberApi.js?v=20230808",
    "/bg/public/js/module/FeeManagementApi.js",
    "/bg/public/vendor/webuploader/chunk_uploader.js",
)


def human_size(n: float) -> str:
    if n >= MB:
        return f"{n / MB:.2f} MB"
    if n >= 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n} B"


import os as _os

CONFIG_FILE = _os.path.join(
    _os.path.expanduser("~"), ".gd12355-helper", "config.json"
)

# 入团志愿书的默认页数。纸质件实测 13 页（封面 + 誓词 + 信息表 + 经历 +
# 支部大会决议 + 志愿正文两页 + 审批 + 备注 + 说明两页）。
# 设置里留空时按这个值核对，用户可自行改。
DEFAULT_EXPECTED_PAGES = 13
