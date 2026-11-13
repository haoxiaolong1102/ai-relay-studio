# -*- coding: utf-8 -*-
"""无人值守交给 Antigravity 的自测（用假 agy，不调模型）。"""
import glob
import json
import os
import unittest

from rtest import HERE, Env

FAKE_AGY = os.path.join(HERE, "fake_agy.py")


class AgyBase(unittest.TestCase):
    git = True
    agy_cfg = {"permissions": "full", "max_segments": 3, "segment_timeout_min": 1, "total_timeout_min": 5}

    def setUp(self):
        self.e = Env(git=self.git)
        self.addCleanup(self.e.cleanup)
        self.e.set_cfg(antigravity=dict(self.agy_cfg, bin="/nonexistent/agy", model="fake-model"))
        self.args_out = os.path.join(self.e.tmp, "agy-args.json")

    def handoff(self, scenario, answers=None, extra=()):
        return self.e.relay("handoff", "--project", "X", "--to", "antigravity", *extra, answers=answers or [],
                            FAKE_AGY_SCENARIO=scenario, RELAY_AGY_BIN=FAKE_AGY, FAKE_AGY_ARGS_OUT=self.args_out)

    def outcome(self):
        return self.e.summary()["outcome"]

    def argv(self):
        with open(self.args_out, encoding="utf-8") as f:
            return json.load(f)


class AgyFlowTests(AgyBase):
    def test_handoff_ok_then_takeback(self):
        e = self.e
        r = self.handoff("ok")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(e.lock_holder(), "antigravity")
        self.assertEqual(e.holders_holder(), "antigravity")
        self.assertIn("Antigravity 报告：完成", self.outcome())
        a = self.argv()
        self.assertEqual(os.path.realpath(a["cwd"]), os.path.realpath(e.proj))     # 在工程文件夹里跑
        self.assertIn("--dangerously-skip-permissions", a["argv"])
        self.assertIn("fake-model", a["argv"])
        self.assertIn("整台电脑全权", r.stdout)
        # 密钥不留在运行记录里
        for p in glob.glob(os.path.join(e.state, "runs", "X", "*")):
            with open(p, encoding="utf-8", errors="replace") as f:
                self.assertNotIn("sk-ant-FAKEFAKE", f.read(), p)
        self.assertFalse(glob.glob(os.path.join(e.state, "runs", "X", "*.raw.tmp")))
        # 再交一次（给谁都）被拒
        self.assertNotEqual(self.handoff("ok").returncode, 0)
        self.assertNotEqual(e.relay("handoff", "--project", "X", answers=[]).returncode, 0)
        r3 = e.relay("takeback", "--project", "X", answers=[])
        self.assertEqual(r3.returncode, 0, r3.stdout + r3.stderr)
        self.assertEqual(e.lock_holder(), "claude")
        b = e.read("docs/relay/BATON.md")
        self.assertIn("接手的工具：Antigravity（无人值守分段跑）", b)
        self.assertIn("src/main.py", b[b.index("接力期间"):])
        self.assertIn("Antigravity 写的交接说明", b)

    def test_stale_handback_not_mistaken_for_done(self):
        """上一轮留下“状态：完成”的 HANDBACK；这一轮工具什么都没做成 → 不能报“完成”。"""
        e = self.e
        self.assertEqual(self.handoff("ok").returncode, 0)
        self.assertEqual(e.relay("takeback", "--project", "X", answers=[]).returncode, 0)
        r = self.handoff("denied")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("报告：完成", self.outcome())
        self.assertIn("被权限拦下", self.outcome())
        self.assertTrue(glob.glob(os.path.join(e.proj, "docs", "relay", "HANDBACK.prev-*.md")))

    def test_partial_runs_to_max_segments(self):
        r = self.handoff("partial")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("已跑满 3 段", self.outcome())
        self.assertEqual(len(self.e.summary()["segments"]), 3)

    def test_denied_stops_with_plain_reason(self):
        r = self.handoff("denied")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("被权限拦下", self.outcome())
        self.assertIn("command", self.outcome())
        self.assertEqual(len(self.e.summary()["segments"]), 1)
        self.assertEqual(self.e.lock_holder(), "antigravity")

    def test_quota(self):
        r = self.handoff("quota")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Antigravity 的额度也用完了", self.outcome())

    def test_from_chat_cancel(self):
        r = self.handoff("ok", answers=["取消"], extra=("--from-chat",))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.e.lock_holder(), "claude")


class AgyNoGit(AgyFlowTests):
    git = False


class AgyEditsOnlyTests(AgyBase):
    agy_cfg = {"permissions": "edits", "max_segments": 2, "segment_timeout_min": 1, "total_timeout_min": 5}

    def test_edits_mode_uses_sandbox_flags(self):
        r = self.handoff("ok")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        a = self.argv()["argv"]
        self.assertNotIn("--dangerously-skip-permissions", a)
        self.assertIn("--sandbox", a)
        self.assertIn("accept-edits", a)


if __name__ == "__main__":
    unittest.main()
