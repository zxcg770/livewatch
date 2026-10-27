"""LiveWatch：AI 盯播。"""

import sys

# 中文 Windows 的控制台和重定向输出默认是 GBK，打印"✓""●"这类字符会直接抛 UnicodeEncodeError。
# 统一改成 UTF-8，无法编码的字符替换掉而不是崩溃；放在包初始化里，所有入口都会生效。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")
