# -*- coding: utf-8 -*-
"""公共部分：路径、配置、原子写入、工程查找。只用标准库，兼容 Python 3.9。"""
import json
import os
import tempfile
import time
from pathlib import Path

ROOT = Path(os.environ.get("RELAY_ROOT") or Path(__file__).resolve().parents[2])
STATE = Path(os.environ.get("RELAY_STATE") or ROOT / "state")
CONFIG = Path(os.environ.get("RELAY_CONFIG") or ROOT / "config" / "projects.json")

HOLDER_CLAUDE = "claude"
HOLDER_CODEX = "codex"
HOLDER_ANTIGRAVITY = "antigravity"
HOLDER_DOUBAO = "doubao"
HOLDER_DEEPSEEK = "deepseek"
HOLDERS = (HOLDER_CLAUDE, HOLDER_CODEX, HOLDER_ANTIGRAVITY, HOLDER_DOUBAO, HOLDER_DEEPSEEK)
HOLDER_NAMES = {
    HOLDER_CLAUDE: "Claude",
    HOLDER_CODEX: "Codex",
    HOLDER_ANTIGRAVITY: "Antigravity",
    HOLDER_DOUBAO: "豆包工作台",
    HOLDER_DEEPSEEK: "DeepSeek",
}


class RelayError(Exception):
    """要用白话告诉用户的错误。"""


def now_iso():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def stamp():
    return time.strftime("%Y%m%d-%H%M%S")


def load_config():
    with open(CONFIG, encoding="utf-8") as f:
        cfg = json.load(f)
    if not isinstance(cfg, dict) or "projects" not in cfg:
        raise RelayError("配置文件 projects.json 格式不对")
    return cfg


def save_config(cfg):
    atomic_write_json(CONFIG, cfg)


def holders_path(cfg=None):
    p = os.environ.get("RELAY_HOLDERS")
    if not p:
        p = (cfg or load_config()).get("holders_copy") or "~/.claude/relay-holders.json"
    return Path(os.path.expanduser(p))


def snapshots_dir(cfg=None):
    p = os.environ.get("RELAY_SNAPSHOTS")
    if not p:
        p = (cfg or load_config()).get("snapshots_dir") or str(ROOT.parent / "_relay-snapshots")
    return Path(os.path.expanduser(p))


def atomic_write_text(path, text, mode=0o600):
    """先写临时文件、落盘、再改名。失败就抛异常，原文件不动。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_json(path, obj, mode=0o600):
    atomic_write_text(path, json.dumps(obj, ensure_ascii=False, indent=2) + "\n", mode)


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def real(p):
    return os.path.realpath(os.path.expanduser(str(p)))


def is_inside(child, parent):
    """child（已解开符号链接）是否在 parent 里面或就是 parent。"""
    child, parent = real(child), real(parent)
    return child == parent or child.startswith(parent.rstrip("/") + "/")


def find_project(cfg, path):
    """按路径找登记的工程，返回 (代号, 工程配置) 或 (None, None)。"""
    if not path:
        return None, None
    best = (None, None)
    for code, proj in cfg.get("projects", {}).items():
        ppath = proj.get("path")
        if ppath and is_inside(path, ppath):
            if best[0] is None or len(real(ppath)) > len(real(best[1]["path"])):
                best = (code, proj)
    return best


def project(cfg, code):
    proj = cfg.get("projects", {}).get(code)
    if not proj:
        raise RelayError("没有登记这个工程：%s" % code)
    return proj


def ensure_private_dir(path):
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(str(path), 0o700)
    except OSError:
        pass
    return path
