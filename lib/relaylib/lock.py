# -*- coding: utf-8 -*-
"""单写锁（谁持棒）+ 内置盘上的持棒者抄本。

锁在程序文件夹 state/locks/<工程代号>.json，只有 relay 命令改它。
抄本在内置盘（默认 ~/.claude/relay-holders.json），外接盘没插时垫片靠它判断。
写入顺序“宁严勿松”：交给 Codex 先改抄本再改锁；交回 Claude 先改锁再改抄本。
"""
import contextlib
import fcntl
import json
import os
import time

from . import audit, common
from .common import HOLDER_CLAUDE, RelayError


def lock_file(code):
    return common.STATE / "locks" / ("%s.json" % code)


def read_lock(code):
    """读锁。没有锁文件＝Claude 持棒。锁文件坏了就抛异常（调用方按“拦下”处理）。"""
    data = common.read_json(lock_file(code))
    if data is None:
        return {"holder": HOLDER_CLAUDE, "since": None, "segment": 0, "by": None, "seq": 0}
    if not isinstance(data, dict) or data.get("holder") not in common.HOLDERS:
        raise RelayError("锁文件损坏：%s" % lock_file(code))
    return data


def holder(code):
    return read_lock(code)["holder"]


@contextlib.contextmanager
def _mutex():
    """改锁时的短互斥（进程死掉自动放开）。"""
    common.ensure_private_dir(common.STATE / "locks")
    fd = os.open(str(common.STATE / "locks" / ".mutex"), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


# ---------- 抄本 ----------

def read_holders(cfg=None):
    """读抄本。不存在返回 None；损坏抛异常。"""
    data = common.read_json(common.holders_path(cfg))
    if data is None:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("projects"), dict):
        raise RelayError("持棒者抄本损坏")
    return data


def _build_holders(cfg, override=None):
    old = None
    try:
        old = read_holders(cfg)
    except Exception:
        old = None
    projects = {}
    for code, proj in cfg.get("projects", {}).items():
        try:
            h = holder(code)
        except Exception:
            h = "unknown"
        projects[code] = {"holder": h, "path": common.real(proj["path"])}
    if override:
        projects[override[0]]["holder"] = override[1]
    return {
        "seq": int((old or {}).get("seq", 0)) + 1,
        "updated": common.now_iso(),
        "live_enabled": bool(cfg.get("live_enabled")),
        "projects": projects,
    }


def write_holders(cfg, override=None):
    """原子写抄本。失败抛 RelayError。"""
    try:
        common.atomic_write_json(common.holders_path(cfg), _build_holders(cfg, override))
    except Exception as e:
        raise RelayError("内置盘上的持棒者抄本写不进去（%s）" % e)


# ---------- 交接 ----------

def transfer(cfg, code, to, expect, by, extra=None):
    """把棒从 expect 交给 to。当前持棒者不是 expect 就拒绝。"""
    common.project(cfg, code)
    with _mutex():
        cur = read_lock(code)
        if cur["holder"] != expect:
            raise RelayError("现在持棒的是 %s，不是 %s，不能交接。" % (cur["holder"], expect))
        new = {
            "holder": to,
            "since": common.now_iso(),
            "segment": 0,
            "by": by,
            "seq": int(cur.get("seq", 0)) + 1,
        }
        if extra:
            new.update(extra)
        pause = float(os.environ.get("RELAY_TEST_PAUSE_BETWEEN", "0") or 0)  # 只给自测用
        if to != HOLDER_CLAUDE:
            write_holders(cfg, (code, to))          # 先抄本（先变严）
            if pause:
                time.sleep(pause)
            common.atomic_write_json(lock_file(code), new)
        else:
            common.atomic_write_json(lock_file(code), new)
            if pause:
                time.sleep(pause)
            try:
                write_holders(cfg, (code, to))      # 后抄本（后变松）
            except RelayError as e:
                audit.log(code, "relay", "抄本未同步", str(e))
                raise RelayError("棒已交回 Claude，但内置盘抄本没同步上（现在偏严，会多拦）。"
                                 "请双击“接力总开关”点“修复状态”。")
    audit.log(code, by, "交接", "%s → %s" % (expect, to))
    return new


def update_lock(code, **fields):
    with _mutex():
        cur = read_lock(code)
        cur.update(fields)
        common.atomic_write_json(lock_file(code), cur)
    return cur


def compare(cfg):
    """对比锁和抄本，返回不一致清单（空＝一致）。"""
    diffs = []
    try:
        copy = read_holders(cfg)
    except Exception as e:
        return ["抄本读不出：%s" % e]
    if copy is None:
        return ["抄本不存在"]
    if bool(copy.get("live_enabled")) != bool(cfg.get("live_enabled")):
        diffs.append("总开关：配置=%s，抄本=%s" % (cfg.get("live_enabled"), copy.get("live_enabled")))
    for code, proj in cfg.get("projects", {}).items():
        try:
            h = holder(code)
        except Exception:
            h = "unknown"
        c = copy["projects"].get(code)
        if not c:
            diffs.append("%s：抄本里没有" % code)
        elif c.get("holder") != h or c.get("path") != common.real(proj["path"]):
            diffs.append("%s：锁=%s，抄本=%s" % (code, h, c.get("holder")))
    for code in copy["projects"]:
        if code not in cfg.get("projects", {}):
            diffs.append("%s：抄本里多出来" % code)
    return diffs


def reconcile(cfg):
    """自愈：以锁为准修好抄本。返回修之前的不一致清单。"""
    diffs = compare(cfg)
    if diffs:
        with _mutex():
            write_holders(cfg)
        audit.log(None, "relay", "修复抄本", "; ".join(diffs))
    return diffs


# ---------- “接力正在跑”的占位（进程死了自动放开） ----------

class Busy(object):
    def __init__(self, code):
        self.code = code
        self.path = common.STATE / "locks" / ("%s.busy" % code)
        self.fd = None

    def acquire(self, info=None):
        common.ensure_private_dir(self.path.parent)
        fd = os.open(str(self.path), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False
        self.fd = fd
        self.write(info or {})
        return True

    def write(self, info):
        info = dict(info, pid=os.getpid(), updated=common.now_iso())
        os.ftruncate(self.fd, 0)
        os.lseek(self.fd, 0, os.SEEK_SET)
        os.write(self.fd, json.dumps(info, ensure_ascii=False).encode("utf-8"))

    def release(self):
        if self.fd is not None:
            try:
                os.ftruncate(self.fd, 0)
            finally:
                os.close(self.fd)
                self.fd = None

    @staticmethod
    def peek(code):
        """别的进程用：接力是不是正在跑？是就返回它写的信息，不是返回 None。"""
        path = common.STATE / "locks" / ("%s.busy" % code)
        try:
            fd = os.open(str(path), os.O_RDWR)
        except OSError:
            return None
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                try:
                    return json.loads(os.read(fd, 65536).decode("utf-8") or "{}")
                except Exception:
                    return {}
            return None
        finally:
            os.close(fd)
