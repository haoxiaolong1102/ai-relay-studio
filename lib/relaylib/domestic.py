# -*- coding: utf-8 -*-
"""国内大模型接入（豆包工作台 / DeepSeek）：
1. 手动/工作台接力模式：自动组装结构化上下文（工程路径、目录树摘要、STATE.md 下一步任务、BATON.md 交接记录），
   一键写入系统剪贴板并自动唤醒豆包应用/网页或 DeepSeek 工作台。
2. 后台 API 自动接力模式：只用 Python 标准库 urllib 调用 DeepSeek / 火山方舟（豆包）OpenAI 兼容接口，
   自动在工程内落盘修改文件并生成 docs/relay/HANDBACK.md。
"""
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request

from . import common, manifest
from .redact import one_line, redact

DEFAULT_SETTINGS = {
    "doubao": {
        "api_url": "https://ark.cn-beijing.volces.com/api/v3/chat/completions",
        "api_key": "",
        "model": "doubao-seed-1-6-250615",
        "web_url": "https://www.doubao.com/chat/",
        "app_path": "/Applications/豆包浏览器.app",
    },
    "deepseek": {
        "api_url": "https://api.deepseek.com/chat/completions",
        "api_key": "",
        "model": "deepseek-chat",
        "web_url": "https://chat.deepseek.com/",
        "app_path": "",
    },
}


def settings(cfg, tool):
    base = dict(DEFAULT_SETTINGS.get(tool) or {})
    user_cfg = (cfg or {}).get(tool) or {}
    base.update({k: v for k, v in user_cfg.items() if v is not None})
    if not base.get("api_key"):
        env_key = "DEEPSEEK_API_KEY" if tool == "deepseek" else "ARK_API_KEY"
        base["api_key"] = os.environ.get(env_key) or os.environ.get("DOUBAO_API_KEY") or ""
    return base


def has_api_key(cfg, tool):
    return bool(settings(cfg, tool).get("api_key", "").strip())


def _project_tree_preview(project_path, max_files=60):
    man = manifest.take(project_path, light=True)
    files = sorted(man.get("files", {}).keys())
    lines = ["  - " + f for f in files[:max_files]]
    if len(files) > max_files:
        lines.append("  - ...（另有 %d 个文件）" % (len(files) - max_files))
    return "\n".join(lines) if lines else "  （空工程）"


def read_state_md(project_path):
    p = os.path.join(common.real(project_path), "docs", "STATE.md")
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except OSError:
        return ""


def write_state_md(project_path, text):
    p = os.path.join(common.real(project_path), "docs", "STATE.md")
    common.atomic_write_text(p, text.strip() + "\n", mode=0o644)
    return p


def build_clipboard_prompt(code, project_path, tool_name="AI 助手"):
    project_path = common.real(project_path)
    state_md = read_state_md(project_path) or "（尚未填写 docs/STATE.md，请根据工程现状继续推进）"
    baton_path = os.path.join(project_path, "docs", "relay", "BATON.md")
    baton_md = ""
    if os.path.isfile(baton_path):
        try:
            with open(baton_path, encoding="utf-8", errors="replace") as f:
                baton_md = f.read().strip()
        except OSError:
            pass
    tree_txt = _project_tree_preview(project_path)
    parts = [
        "# AI 接力任务交接单（目标工具：%s）" % tool_name,
        "",
        "- **工程代号**：`%s`" % code,
        "- **本地路径**：`%s`" % project_path,
        "",
        "## 1. 当前任务与下一步目标（来自 `docs/STATE.md`）",
        state_md,
        "",
        "## 2. 工程文件结构概览",
        tree_txt,
    ]
    if baton_md:
        parts += ["", "## 3. 上一棒交接记录（来自 `docs/relay/BATON.md`）", baton_md[:3000]]
    parts += [
        "",
        "## 4. 协作要求",
        "- 请基于上述任务目标给出完整代码修改或直接在工作区修改文件。",
        "- 不要删除或改动素材目录（`raw/`），不要自行执行 `git commit` 或 `git push`（交回时接力台会自动生成隔离分支）。",
    ]
    return "\n".join(parts) + "\n"


def copy_to_clipboard(text):
    try:
        env = dict(os.environ, LANG="en_US.UTF-8", LC_CTYPE="UTF-8")
        p = subprocess.Popen(["/usr/bin/pbcopy"], stdin=subprocess.PIPE, env=env)
        p.communicate(input=text.encode("utf-8"), timeout=5)
        return p.returncode == 0
    except Exception:
        return False


def open_tool_workspace(cfg, tool, project_path=None):
    st = settings(cfg, tool)
    app_path = st.get("app_path") or ""
    web_url = st.get("web_url") or ""
    try:
        if app_path and os.path.exists(app_path):
            subprocess.Popen(["/usr/bin/open", "-a", app_path])
            return "已唤醒本地应用：%s" % os.path.basename(app_path)
        if web_url:
            subprocess.Popen(["/usr/bin/open", web_url])
            return "已打开工作台网页：%s" % web_url
    except Exception as e:
        return "未能自动打开工作台（%s）" % e
    return ""


def load_prompt(cfg, seg, max_seg):
    return ("这是 AI 接力的第 %d/%d 段自动执行。请阅读工程现状与任务要求，输出需要新增或修改的文件。" % (seg, max_seg))


def _collect_context_files(project_path, max_bytes=32000):
    man = manifest.take(project_path, light=True)
    files = sorted(man.get("files", {}).keys())
    chunks = []
    used = 0
    for rel in files:
        if rel.startswith("raw/") or rel.startswith(".claude/"):
            continue
        full = os.path.join(project_path, rel)
        if not os.path.isfile(full):
            continue
        try:
            sz = os.path.getsize(full)
            if sz > 16000:
                continue
            with open(full, encoding="utf-8") as f:
                content = f.read()
        except Exception:
            continue
        piece = "### 文件: `%s`\n```\n%s\n```\n" % (rel, content)
        if used + len(piece) > max_bytes:
            break
        chunks.append(piece)
        used += len(piece)
    return "\n".join(chunks)


def _apply_model_response(project_path, raw_dirs, text):
    """解析模型输出的 <file path="相对路径">内容</file> 与 <handback>内容</handback> 并安全落盘。"""
    project_path = common.real(project_path)
    raw_prefixes = tuple(d.strip("/") + "/" for d in raw_dirs)
    written = []
    for m in re.finditer(r'<file\s+path=["\']([^"\']+)["\']\s*>\n?(.*?)</file>', text, re.DOTALL):
        rel = m.group(1).strip().lstrip("/")
        body = m.group(2)
        if not rel or ".." in rel.split("/") or rel.startswith(".git") or rel.startswith(".claude"):
            continue
        if raw_prefixes and (rel + "/").startswith(raw_prefixes):
            continue
        dest = common.real(os.path.join(project_path, rel))
        if not common.is_inside(dest, project_path) or dest == project_path:
            continue
        common.atomic_write_text(dest, body if body.endswith("\n") else body + "\n", mode=0o644)
        written.append(rel)
    hb_match = re.search(r"<handback>\n?(.*?)</handback>", text, re.DOTALL)
    hb_text = hb_match.group(1).strip() if hb_match else (
        "# 交接说明\n\n状态：完成\n\n## 本段工作摘要\n" + one_line(text, 500) + "\n"
    )
    hb_path = os.path.join(project_path, "docs", "relay", "HANDBACK.md")
    common.atomic_write_text(hb_path, hb_text + "\n", mode=0o644)
    return written


def run_segment(cfg, tool, code, project_path, seg, prompt, is_git, timeout_s, stop_flag=None, on_start=None):
    """使用内置 OpenAI 兼容客户端调用 DeepSeek 或豆包 API 自动完成一段代码修改。"""
    st = settings(cfg, tool)
    runs = common.ensure_private_dir(common.STATE / "runs" / code)
    run_id = "%s-%s-seg%d" % (common.stamp(), tool, seg)
    res = {
        "run_id": run_id,
        "segment": seg,
        "rc": 0,
        "timed_out": False,
        "stopped": False,
        "thread_id": None,
        "usage": None,
        "rate_limits": None,
        "denials": 0,
        "errors": [],
        "commands": 0,
        "quota_hit": False,
        "tool": tool,
    }
    t0 = time.time()
    if on_start:
        on_start(os.getpid())
    api_key = st.get("api_key", "").strip()
    if not api_key:
        res["rc"] = 1
        res["errors"].append("未配置 %s 的 API Key（可在“模型设置”中填写，或直接使用“一键复制上下文去工作台”模式）" % common.HOLDER_NAMES.get(tool, tool))
        res["seconds"] = round(time.time() - t0, 1)
        common.atomic_write_json(runs / ("%s.result.json" % run_id), res)
        return res

    sys_prompt = (
        "你是一个严谨的本地工程接力编程 Agent。\n"
        "请阅读用户的工程上下文与 `docs/STATE.md` 中的下一步任务，直接输出需要新增或修改的文件。\n"
        "输出格式要求：\n"
        "1. 每个需要写入或修改的文件，必须严格用 `<file path=\"相对路径\">完整文件内容</file>` 包裹。\n"
        "2. 最后必须用 `<handback>状态：完成（或未完成/受阻）\\n说明你做了什么</handback>` 包裹交接说明。\n"
        "3. 严禁修改 `raw/` 素材目录或 `.claude/` 配置。"
    )
    user_msg = (
        build_clipboard_prompt(code, project_path, common.HOLDER_NAMES.get(tool, tool))
        + "\n## 5. 工程现有关键源码\n"
        + _collect_context_files(project_path)
    )
    payload = {
        "model": st["model"],
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_msg},
        ],
        "temperature": 0.2,
    }
    req = urllib.request.Request(
        st["api_url"],
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer %s" % api_key,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=min(int(timeout_s), 300)) as resp:
            body = json.loads(resp.read().decode("utf-8", errors="replace"))
        choice = ((body.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        res["usage"] = body.get("usage")
        proj = common.project(cfg, code)
        _apply_model_response(project_path, proj.get("raw_dirs", ()), choice)
        res["last_message"] = one_line(choice, 600)
        common.atomic_write_text(runs / ("%s.last.txt" % run_id), redact(choice, 4000) + "\n")
    except urllib.error.HTTPError as e:
        err_body = ""
        try:
            err_body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        res["rc"] = e.code
        msg = "HTTP %s: %s" % (e.code, one_line(err_body or str(e), 240))
        res["errors"].append(msg)
        if e.code in (402, 429) or "quota" in err_body.lower() or "insufficient" in err_body.lower():
            res["quota_hit"] = True
    except Exception as e:
        res["rc"] = 1
        res["errors"].append("%s: %s" % (type(e).__name__, one_line(str(e), 240)))
    res["seconds"] = round(time.time() - t0, 1)
    common.atomic_write_json(runs / ("%s.result.json" % run_id), res)
    return res
