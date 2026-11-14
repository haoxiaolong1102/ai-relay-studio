#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""AI 接力台桌面界面与菜单栏共用后端桥接脚本：
  relay_state.py json                        打印完整现状 JSON（给桌面窗口与菜单栏读）
  relay_state.py live on|off                 开/关总开关
  relay_state.py add-project <名称> <路径>    一键导入并初始化工程（补 .gitignore、建 STATE.md、配钩子）
  relay_state.py remove-project <代号>       从列表移除工程（不删工程文件）
  relay_state.py save-state <代号> <内容>    保存工程的 docs/STATE.md
  relay_state.py copy-prompt <代号> [工具名] 生成并复制接力上下文到剪贴板
  relay_state.py save-settings <JSON字符串>  保存各模型与 API Key 设置
"""
import importlib.machinery
import importlib.util
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))
sys.dont_write_bytecode = True
from relaylib import audit, common, domestic, lock, snapshot  # noqa: E402


def _read_text(path, limit=4000):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()[:limit]
    except OSError:
        return ""


def state():
    cfg = common.load_config()
    projects = []
    for code, proj in cfg["projects"].items():
        ppath = common.real(proj.get("path") or "")
        item = {
            "code": code,
            "name": proj.get("name") or code,
            "path": ppath,
            "holder": "unknown",
            "since": "",
            "segment": None,
            "running": False,
            "snapshots_count": 0,
            "latest_snapshot": "",
            "state_md": "",
            "baton_md": "",
        }
        try:
            lk = lock.read_lock(code)
            item["holder"] = lk["holder"]
            item["since"] = lk.get("since") or ""
            item["segment"] = lk.get("segment")
        except Exception as e:
            item["error"] = str(e)
        try:
            item["running"] = lock.Busy.peek(code) is not None
        except Exception:
            pass
        try:
            snaps = snapshot.list_snaps(cfg, code)
            item["snapshots_count"] = len(snaps)
            if snaps:
                item["latest_snapshot"] = snaps[-1]["id"]
        except Exception:
            pass
        if ppath and os.path.isdir(ppath):
            item["state_md"] = _read_text(os.path.join(ppath, "docs", "STATE.md"), 8000)
            item["baton_md"] = _read_text(os.path.join(ppath, "docs", "relay", "BATON.md"), 8000)
        projects.append(item)
    try:
        diffs = lock.compare(cfg)
    except Exception as e:
        diffs = [str(e)]
    logs = []
    for c in [None] + list(cfg["projects"].keys()):
        for row in audit.tail(c, 20):
            logs.append("[%s] %s" % (c or "全局", row.strip()))
    doubao_st = domestic.settings(cfg, "doubao")
    deepseek_st = domestic.settings(cfg, "deepseek")
    settings_info = {
        "codex_model": (cfg.get("codex") or {}).get("model") or "gpt-6.1-sol",
        "agy_model": (cfg.get("antigravity") or {}).get("model") or "gemini-4-argon-medium",
        "doubao_model": doubao_st.get("model") or "doubao-seed-1-6-250615",
        "doubao_api_url": doubao_st.get("api_url") or "https://ark.cn-beijing.volces.com/api/v3/chat/completions",
        "doubao_api_key": doubao_st.get("api_key") or "",
        "doubao_web_url": doubao_st.get("web_url") or "https://www.doubao.com/chat/",
        "deepseek_model": deepseek_st.get("model") or "deepseek-chat",
        "deepseek_api_url": deepseek_st.get("api_url") or "https://api.deepseek.com/chat/completions",
        "deepseek_api_key": deepseek_st.get("api_key") or "",
        "deepseek_web_url": deepseek_st.get("web_url") or "https://chat.deepseek.com/",
    }
    return {
        "ok": True,
        "live": bool(cfg.get("live_enabled")),
        "projects": projects,
        "diffs": diffs,
        "logs": logs[-40:],
        "settings": settings_info,
    }


def _slug(name, existing):
    base = re.sub(r"[^a-zA-Z0-9_\-]", "", name.lower()) or "proj"
    base = base[:20]
    cand = base
    n = 2
    while cand in existing:
        cand = "%s%d" % (base, n)
        n += 1
    return cand


def add_project(name, path):
    cfg = common.load_config()
    real_path = common.real(path)
    if not os.path.isdir(real_path):
        raise common.RelayError("文件夹不存在：%s" % real_path)
    code = _slug(os.path.basename(real_path) or name, cfg["projects"])
    # 1. 确保 .gitignore 忽略 docs/relay/
    gi_path = os.path.join(real_path, ".gitignore")
    gi_text = _read_text(gi_path, 100000)
    if "docs/relay/" not in gi_text and "docs/relay" not in gi_text:
        new_gi = (gi_text.rstrip("\n") + "\n" if gi_text.strip() else "") + "docs/relay/\n"
        common.atomic_write_text(gi_path, new_gi, mode=0o644)
    # 2. 如果不是 Git 仓库，初始化一下方便快照和隔离分支
    if not os.path.isdir(os.path.join(real_path, ".git")):
        try:
            subprocess.run(["git", "-C", real_path, "init"], capture_output=True, timeout=15)
        except Exception:
            pass
    # 3. 创建默认 docs/STATE.md 与 raw/
    os.makedirs(os.path.join(real_path, "raw"), exist_ok=True)
    state_file = os.path.join(real_path, "docs", "STATE.md")
    if not os.path.exists(state_file):
        default_md = (
            "# %s — 工程现状与下一步\n\n"
            "## 现状\n"
            "已接入 AI 接力台。\n\n"
            "## 下一步\n"
            "- [ ] 在这里填写接下来要交给 Codex / Antigravity / 豆包工作台 / DeepSeek 完成的具体任务。\n"
        ) % (name or code)
        common.atomic_write_text(state_file, default_md, mode=0o644)
    # 4. 自动合并 Claude 钩子到 .claude/settings.json
    hooks_tpl_path = os.path.join(ROOT, "config", "claude-hooks.json")
    if os.path.isfile(hooks_tpl_path):
        try:
            tpl = common.read_json(hooks_tpl_path, {}) or {}
            c_settings_path = os.path.join(real_path, ".claude", "settings.json")
            cur_s = common.read_json(c_settings_path, {}) or {}
            if "hooks" not in cur_s and "hooks" in tpl:
                cur_s["hooks"] = tpl["hooks"]
                common.atomic_write_json(c_settings_path, cur_s, mode=0o644)
        except Exception:
            pass
    cfg["projects"][code] = {
        "name": name or os.path.basename(real_path) or code,
        "path": real_path,
        "raw_dirs": ["raw"],
        "test_cmd": [],
        "readonly_mcp": [],
    }
    common.save_config(cfg)
    lock.reconcile(cfg)
    audit.log(code, "user", "导入工程", "%s (%s)" % (name, real_path))
    return state()


def remove_project(code):
    cfg = common.load_config()
    if code in cfg["projects"]:
        del cfg["projects"][code]
        common.save_config(cfg)
        lock.reconcile(cfg)
        audit.log(code, "user", "移除工程", code)
    return state()


def save_settings(payload_str):
    cfg = common.load_config()
    data = json.loads(payload_str)
    cfg.setdefault("codex", {})
    cfg.setdefault("antigravity", {})
    cfg.setdefault("doubao", {})
    cfg.setdefault("deepseek", {})
    if "codex_model" in data:
        cfg["codex"]["model"] = data["codex_model"].strip()
    if "agy_model" in data:
        cfg["antigravity"]["model"] = data["agy_model"].strip()
    for k in ("model", "api_url", "api_key", "web_url"):
        dk = "doubao_" + k
        if dk in data:
            cfg["doubao"][k] = data[dk].strip()
        sk = "deepseek_" + k
        if sk in data:
            cfg["deepseek"][k] = data[sk].strip()
    common.save_config(cfg)
    audit.log(None, "user", "更新设置", "模型与 API 配置已保存")
    return state()


def relay_module():
    path = os.path.join(ROOT, "bin", "relay")
    loader = importlib.machinery.SourceFileLoader("relay_main", path)
    spec = importlib.util.spec_from_loader("relay_main", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def main():
    args = sys.argv[1:]
    try:
        if args[:1] == ["json"]:
            print(json.dumps(state(), ensure_ascii=False))
            return 0
        if args[:1] == ["live"] and args[1:2] in (["on"], ["off"]):
            relay_module().set_live(common.load_config(), args[1] == "on")
            print(json.dumps(state(), ensure_ascii=False))
            return 0
        if args[:1] == ["add-project"] and len(args) >= 3:
            print(json.dumps(add_project(args[1], args[2]), ensure_ascii=False))
            return 0
        if args[:1] == ["remove-project"] and len(args) >= 2:
            print(json.dumps(remove_project(args[1]), ensure_ascii=False))
            return 0
        if args[:1] == ["save-state"] and len(args) >= 3:
            cfg = common.load_config()
            proj = common.project(cfg, args[1])
            domestic.write_state_md(proj["path"], args[2])
            audit.log(args[1], "user", "更新任务", "保存了 docs/STATE.md")
            print(json.dumps(state(), ensure_ascii=False))
            return 0
        if args[:1] == ["copy-prompt"] and len(args) >= 2:
            cfg = common.load_config()
            proj = common.project(cfg, args[1])
            tname = args[2] if len(args) >= 3 else "AI 助手"
            prompt = domestic.build_clipboard_prompt(args[1], proj["path"], tname)
            ok = domestic.copy_to_clipboard(prompt)
            print(json.dumps({"ok": ok, "prompt": prompt}, ensure_ascii=False))
            return 0
        if args[:1] == ["save-settings"] and len(args) >= 2:
            print(json.dumps(save_settings(args[1]), ensure_ascii=False))
            return 0
    except Exception as e:
        print(json.dumps({"ok": False, "error": "%s: %s" % (type(e).__name__, e)}, ensure_ascii=False))
        return 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
