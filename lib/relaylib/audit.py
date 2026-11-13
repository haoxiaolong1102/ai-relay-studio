# -*- coding: utf-8 -*-
"""审计日志：纯文本、一行一条、只追加。格式：时间 | 工程 | 谁 | 事件 | 细节"""
import os

from . import common
from .redact import one_line


def log_path(code):
    return common.STATE / "audit" / ("%s.log" % (code or "_global"))


def log(code, who, event, detail=""):
    """写一条。写失败不抛异常（记录不能卡住正事），返回 True/False。"""
    try:
        common.ensure_private_dir(common.STATE / "audit")
        line = "%s | %s | %s | %s | %s\n" % (
            common.now_iso(), code or "-", who, event, one_line(detail, 500))
        fd = os.open(str(log_path(code)), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)
        return True
    except Exception:
        return False


def tail(code, n=50):
    try:
        with open(log_path(code), encoding="utf-8") as f:
            return f.readlines()[-n:]
    except FileNotFoundError:
        return []
