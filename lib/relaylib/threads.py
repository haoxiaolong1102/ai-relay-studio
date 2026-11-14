# -*- coding: utf-8 -*-
"""对话框级（Thread-to-Thread）精准接力引擎：
解决“同一个工程文件夹下有多个项目/多个对话框同时在跑”的问题。
支持：
1. 自动扫描本机 OpenAI Codex 真实对话框（~/.codex/sqlite/codex.db + ~/.codex/sessions/**/rollout-*.jsonl）
2. 自动扫描本机 Google Gemini / Antigravity 真实对话框（~/.gemini/antigravity/brain/*/transcript.jsonl）
3. 自动扫描本机 Claude Code 真实对话框（~/.claude/projects/*/*.jsonl）
4. 提取单个指定对话框的专属上下文（最近用户需求、AI 最新进展、该对话涉及的文件），并在 Codex 对话 ↔ Gemini 对话之间建立一对一绑定与精准续接。
"""
import datetime as dt
import glob
import json
import os
import re
import sqlite3

from . import common
from .redact import one_line, redact


def _fmt_ts(ts):
    if not ts:
        return ""
    try:
        return dt.datetime.fromtimestamp(float(ts)).strftime("%m-%d %H:%M")
    except Exception:
        return str(ts)[:16]


def _clean_user_text(text):
    if not text:
        return ""
    text = re.sub(r"<environment_context>.*?</environment_context>", "", text, flags=re.DOTALL)
    text = re.sub(r"<external_codex_apps_open_page>.*?</external_codex_apps_open_page>", "", text, flags=re.DOTALL)
    text = re.sub(r"<ADDITIONAL_METADATA>.*?</ADDITIONAL_METADATA>", "", text, flags=re.DOTALL)
    text = re.sub(r"<USER_SETTINGS_CHANGE>.*?</USER_SETTINGS_CHANGE>", "", text, flags=re.DOTALL)
    m = re.findall(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", text, flags=re.DOTALL)
    if m:
        text = "\n".join(x.strip() for x in m if x.strip())
    return text.strip()


def _extract_codex_content(content_obj):
    if isinstance(content_obj, str):
        return _clean_user_text(content_obj)
    if isinstance(content_obj, list):
        parts = []
        for item in content_obj:
            if isinstance(item, dict):
                t = item.get("text") or ""
                if t and item.get("type") in ("input_text", "output_text", "text", None):
                    cleaned = _clean_user_text(t)
                    if cleaned and not cleaned.startswith("You are Codex, an agent"):
                        parts.append(cleaned)
        return "\n".join(parts).strip()
    return ""


def find_codex_rollout(thread_id):
    if not thread_id:
        return None
    pattern = os.path.expanduser("~/.codex/sessions/*/*/*/rollout-*%s.jsonl" % thread_id)
    matches = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
    return matches[0] if matches else None


def parse_codex_rollout(thread_id, max_turns=6):
    """从指定 Codex 对话的 rollout jsonl 中提取仅属于该对话的用户指令、AI 回复与涉及文件。"""
    path = find_codex_rollout(thread_id)
    if not path or not os.path.isfile(path):
        return {"found": False, "turns": [], "files": [], "cwd": ""}
    user_msgs = []
    asst_msgs = []
    touched_files = []
    cwd = ""
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                etype = ev.get("type")
                payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
                if etype == "session_meta" and not cwd:
                    cwd = payload.get("cwd") or ""
                elif etype == "response_item":
                    ptype = payload.get("type")
                    role = payload.get("role")
                    if ptype == "message" and role == "user":
                        txt = _extract_codex_content(payload.get("content"))
                        if txt:
                            user_msgs.append(txt[:600])
                    elif ptype == "message" and role == "assistant":
                        txt = _extract_codex_content(payload.get("content"))
                        if txt:
                            asst_msgs.append(txt[:800])
                    elif ptype in ("function_call", "custom_tool_call"):
                        args_raw = str(payload.get("arguments") or payload.get("input") or "")
                        for m in re.findall(r'[\w\-\./]+\.(?:py|swift|ts|tsx|js|json|md|html|css|sh)', args_raw):
                            if not m.startswith("/") and m not in touched_files and len(touched_files) < 25:
                                touched_files.append(m)
    except OSError:
        pass
    turns = []
    for u in user_msgs[-max_turns:]:
        turns.append({"role": "user", "text": redact(u, 600)})
    for a in asst_msgs[-max_turns:]:
        turns.append({"role": "assistant", "text": redact(a, 800)})
    return {
        "found": True,
        "rollout_path": path,
        "cwd": cwd,
        "user_messages": [redact(u, 600) for u in user_msgs[-max_turns:]],
        "assistant_messages": [redact(a, 800) for a in asst_msgs[-max_turns:]],
        "files": touched_files[:20],
    }


def list_codex_threads(project_path=None, limit=30):
    """列出本机 Codex 真实对话框。如果传了 project_path，优先把匹配该路径的排在最前面，同时也返回其它最近活跃对话。"""
    db_path = os.path.expanduser("~/.codex/sqlite/codex.db")
    items = []
    seen = set()
    norm_proj = common.real(project_path) if project_path and os.path.exists(str(project_path)) else ""
    if os.path.isfile(db_path):
        try:
            conn = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True, timeout=3)
            cur = conn.cursor()
            rows = cur.execute(
                "SELECT thread_id, display_title, cwd, source_updated_at "
                "FROM local_thread_catalog WHERE missing_candidate = 0 "
                "ORDER BY source_updated_at DESC LIMIT 80"
            ).fetchall()
            conn.close()
            for tid, title, cwd, updated in rows:
                if not tid or tid in seen:
                    continue
                seen.add(tid)
                real_cwd = common.real(cwd) if cwd else ""
                in_proj = bool(norm_proj and real_cwd and (real_cwd == norm_proj or common.is_inside(real_cwd, norm_proj) or common.is_inside(norm_proj, real_cwd)))
                items.append({
                    "id": tid,
                    "tool": "codex",
                    "title": (title or tid[:8]).strip(),
                    "cwd": cwd or "",
                    "updated": _fmt_ts(updated),
                    "updated_raw": float(updated or 0),
                    "in_project": in_proj,
                })
        except Exception:
            pass
    # 补充扫描最近会话文件（防止有些新对话还没进 catalog 表）
    for f in sorted(glob.glob(os.path.expanduser("~/.codex/sessions/*/*/*/rollout-*.jsonl")), key=os.path.getmtime, reverse=True)[:25]:
        m = re.search(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$", os.path.basename(f))
        if not m:
            continue
        tid = m.group(1)
        if tid in seen:
            continue
        seen.add(tid)
        info = parse_codex_rollout(tid, max_turns=2)
        cwd = info.get("cwd") or ""
        if "/state/precheck-work" in cwd:
            continue
        first_u = (info.get("user_messages") or [""])[0]
        title = one_line(first_u, 42) or ("Codex 会话 " + tid[:8])
        mtime = os.path.getmtime(f)
        real_cwd = common.real(cwd) if cwd else ""
        in_proj = bool(norm_proj and real_cwd and (real_cwd == norm_proj or common.is_inside(real_cwd, norm_proj)))
        items.append({
            "id": tid,
            "tool": "codex",
            "title": title,
            "cwd": cwd,
            "updated": _fmt_ts(mtime),
            "updated_raw": mtime,
            "in_project": in_proj,
        })
    items.sort(key=lambda x: (1 if x.get("in_project") else 0, x.get("updated_raw", 0)), reverse=True)
    return items[:limit]


def parse_agy_conversation(conv_id, max_turns=6):
    """从指定 Gemini / Antigravity 对话的 transcript.jsonl 提取仅属于该对话的用户指令与最新进展。"""
    tf = os.path.expanduser("~/.gemini/antigravity/brain/%s/.system_generated/logs/transcript.jsonl" % conv_id)
    if not os.path.isfile(tf):
        return {"found": False, "user_messages": [], "assistant_messages": [], "files": []}
    user_msgs = []
    asst_msgs = []
    touched_files = []
    try:
        with open(tf, encoding="utf-8", errors="ignore") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                t = obj.get("type")
                if t == "USER_INPUT":
                    txt = _clean_user_text(str(obj.get("content") or ""))
                    if txt and not txt.startswith("The following is a <SYSTEM_MESSAGE>") and not txt.startswith("Error:"):
                        user_msgs.append(txt[:600])
                elif t == "PLANNER_RESPONSE":
                    txt = str(obj.get("content") or "").strip()
                    if txt:
                        asst_msgs.append(txt[:800])
                    for tc in (obj.get("tool_calls") or []):
                        if isinstance(tc, dict):
                            args_str = json.dumps(tc.get("arguments") or {}, ensure_ascii=False)
                            for m in re.findall(r'[\w\-\./\u4e00-\u9fa5]+\.(?:py|swift|ts|tsx|js|mjs|cjs|json|md|html|css|sh)', args_str):
                                if m not in touched_files and len(touched_files) < 25:
                                    touched_files.append(m)
                    for m in re.findall(r'`?([\w\-\./\u4e00-\u9fa5]+\.(?:py|swift|ts|tsx|js|mjs|cjs|json|md|html|css|sh))`?', txt):
                        if m not in touched_files and len(touched_files) < 25:
                            touched_files.append(m)
    except OSError:
        pass
    return {
        "found": True,
        "transcript_path": tf,
        "user_messages": [redact(u, 600) for u in user_msgs[-max_turns:]],
        "assistant_messages": [redact(a, 800) for a in asst_msgs[-max_turns:]],
        "files": touched_files[:20],
    }


def list_agy_conversations(limit=25):
    """列出本机 Gemini / Antigravity 的真实对话框列表。"""
    brain_root = os.path.expanduser("~/.gemini/antigravity/brain")
    items = []
    if not os.path.isdir(brain_root):
        return items
    dirs = []
    for name in os.listdir(brain_root):
        if name == "tempmediaStorage" or name.startswith("."):
            continue
        full = os.path.join(brain_root, name)
        tf = os.path.join(full, ".system_generated", "logs", "transcript.jsonl")
        if os.path.isfile(tf):
            dirs.append((os.path.getmtime(tf), name, tf))
    dirs.sort(reverse=True)
    for mtime, cid, _ in dirs[:limit]:
        info = parse_agy_conversation(cid, max_turns=4)
        u_list = info.get("user_messages") or []
        latest_u = u_list[-1] if u_list else ""
        first_u = u_list[0] if u_list else ""
        title = one_line(latest_u or first_u, 42) or ("Gemini 会话 " + cid[:8])
        if first_u and latest_u and first_u != latest_u:
            s_first = one_line(first_u, 18)
            s_last = one_line(latest_u, 22)
            title = "%s ➔ %s" % (s_first, s_last)
        items.append({
            "id": cid,
            "tool": "antigravity",
            "title": title,
            "updated": _fmt_ts(mtime),
            "updated_raw": mtime,
        })
    return items


# ---------- 对话框绑定存储（记在 state/thread_bindings.json） ----------

def _bindings_file():
    return common.STATE / "thread_bindings.json"


def load_bindings():
    return common.read_json(_bindings_file(), {}) or {}


def save_binding(code, codex_thread_id=None, agy_conv_id=None, note=None):
    all_b = load_bindings()
    cur = dict(all_b.get(code) or {})
    if codex_thread_id is not None:
        cur["codex_thread_id"] = codex_thread_id
    if agy_conv_id is not None:
        cur["agy_conv_id"] = agy_conv_id
    if note is not None:
        cur["note"] = note
    cur["updated"] = common.now_iso()
    all_b[code] = cur
    common.atomic_write_json(_bindings_file(), all_b)
    return cur


def get_binding(code):
    return dict(load_bindings().get(code) or {})


def build_thread_relay_prompt(code, project_path, source_tool, source_thread_id, target_tool_name, extra_task=""):
    """核心：只提取『选中的那一个具体对话框』的上下文，绝不混入同目录下其它无关项目！"""
    project_path = common.real(project_path)
    if source_tool == "codex" and source_thread_id:
        info = parse_codex_rollout(source_thread_id, max_turns=6)
        src_label = "OpenAI Codex 会话 `%s`" % source_thread_id
    elif source_tool == "antigravity" and source_thread_id:
        info = parse_agy_conversation(source_thread_id, max_turns=6)
        src_label = "Gemini (Antigravity) 会话 `%s`" % source_thread_id
    else:
        info = {"user_messages": [], "assistant_messages": [], "files": []}
        src_label = "%s 会话" % common.HOLDER_NAMES.get(source_tool, source_tool)

    work_dir = info.get("cwd") or project_path
    lines = [
        "# ⚡ 合流 (Heliu) · 对话框级精准接力单",
        "",
        "- **接力方向**：从 %s ➔ 交给 **%s** 继续推进" % (src_label, target_tool_name),
        "- **当前子项目工作目录**：`%s`" % work_dir,
    ]
    if info.get("files"):
        lines.append("- **该对话近期涉及的文件（只关注这些，勿动同目录其它无关子项目）**：")
        for f in info["files"][:15]:
            lines.append("  - `%s`" % f)

    lines.append("")
    lines.append("## 1. 原对话框里的用户历史需求（按时间顺序）")
    if info.get("user_messages"):
        for i, m in enumerate(info["user_messages"], 1):
            lines.append("### 需求 %d\n%s\n" % (i, m))
    else:
        lines.append("（未读取到历史提问，请参考下方任务说明）\n")

    lines.append("## 2. 原对话框停下前的最新 AI 进展与回复")
    if info.get("assistant_messages"):
        for i, m in enumerate(info["assistant_messages"][-3:], 1):
            lines.append("### 进展 %d\n%s\n" % (i, m))
    else:
        lines.append("（无历史回复记录）\n")

    if extra_task and extra_task.strip():
        lines += [
            "## 3. 本次接力你需要接着完成的具体任务",
            extra_task.strip(),
            "",
        ]
    lines += [
        "## 4. 接力规矩",
        "- 请直接顺着上面【原对话框】的进度继续往下做，不要重新从头提问，也不要改动该目录下其它无关项目的代码。",
        "- 完成本轮工作后，请简要总结你修改了哪些文件、当前完成了什么、下一步还剩什么，方便我稍后一键交回原对话框。",
    ]
    return "\n".join(lines) + "\n"
