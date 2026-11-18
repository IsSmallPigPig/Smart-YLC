"""安装脚本的静态检查：Inno 的 Pascal 注释不能嵌套，只有 ISCC 才管这事。

踩过一次：注释里写了 `{localappdata}`，内层左花括号提前闭合注释，剩下的路径
文字被当成代码，四个 Windows 变体全在「编译安装包」这一步挂掉，而 Python
打包、冒烟都是绿的——只有安装包编译会报。
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _code_section() -> str:
    text = (ROOT / "installer.iss").read_text(encoding="utf-8")
    return text.split("[Code]", 1)[1]


def test_code_comments_do_not_nest():
    """注释块内不允许再出现左花括号。"""
    code = _code_section()
    i, line = 0, 1
    problems = []
    while i < len(code):
        ch = code[i]
        if ch == "\n":
            line += 1
            i += 1
            continue
        if ch == "'":                      # 字符串：'' 是一个转义引号
            i += 1
            while i < len(code):
                if code[i] == "\n":
                    line += 1
                if code[i] == "'":
                    if i + 1 < len(code) and code[i + 1] == "'":
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            continue
        if ch == "{":
            start = line
            i += 1
            while i < len(code) and code[i] != "}":
                if code[i] == "{":
                    problems.append(start)
                    break
                if code[i] == "\n":
                    line += 1
                i += 1
            i += 1
            continue
        i += 1
    assert not problems, f"[Code] 第 {problems} 行的注释里嵌了花括号，ISCC 会编译失败"


def test_code_comments_are_closed():
    """注释都要有收尾：漏一个右花括号会把后面的过程一起吃掉。"""
    code = _code_section()
    depth = 0
    i = 0
    while i < len(code):
        ch = code[i]
        if ch == "'":
            i += 1
            while i < len(code):
                if code[i] == "'":
                    if i + 1 < len(code) and code[i + 1] == "'":
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        i += 1
    assert depth == 0, "installer.iss 的 [Code] 段有未闭合的注释"


def test_install_dir_is_ascii():
    """安装目录必须纯 ASCII：Qt5 的 WebEngine 用 ANSI 代码页解析路径。"""
    text = (ROOT / "installer.iss").read_text(encoding="utf-8")
    line = next(l for l in text.splitlines() if l.startswith("DefaultDirName="))
    _, value = line.split("=", 1)
    assert value.isascii(), f"安装目录里有非 ASCII 字符：{value}"
    assert "UsePreviousAppDir=no" in text, "升级时要换到新目录，否则老用户仍装在中文路径"


# 预处理器认得的行首指令；其余以 # 开头的行会被 ISCC 当成未知指令直接中断
ISPP_DIRECTIVES = {
    "if", "ifdef", "ifndef", "elif", "else", "endif", "define", "undef",
    "include", "pragma", "error", "expr", "insert", "append", "emit",
    "file", "sub", "endsub", "for", "redir", "dim", "option",
}


def test_code_lines_never_start_with_hash():
    """[Code] 里的续行不能以 # 开头。

    踩过一次：MsgBox 的多行拼接把 `#13#10` 放在续行行首，预处理器把 #13 当成
    指令，报「Unknown preprocessor directive」，四个 Windows 变体全在编译安装包
    这一步挂掉。换行符写在上一行末尾即可。"""
    bad = []
    in_code = False
    for no, line in enumerate((ROOT / "installer.iss").read_text(encoding="utf-8").splitlines(), 1):
        if line.strip() == "[Code]":
            in_code = True
            continue
        if not line.lstrip().startswith("#"):
            continue
        word = line.lstrip()[1:].split("(")[0].split()[0].lower() if line.lstrip()[1:].strip() else ""
        if in_code and word not in ISPP_DIRECTIVES:
            bad.append(f"{no}: {line.strip()[:40]}")
    assert not bad, "这些行以 # 开头，会被当成预处理器指令：\n" + "\n".join(bad)
