# -*- coding: utf-8 -*-
"""锁、脱敏、清单、快照/恢复、素材只读。"""
import json
import os
import shutil
import stat
import unittest
from unittest import mock

import rtest
from rtest import Env, real, sh

from relaylib import audit, common, lock, manifest, redact, snapshot  # noqa: E402
from relaylib.common import RelayError  # noqa: E402


class LockTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.addCleanup(self.env.cleanup)
        self.env.inproc()
        self.cfg = common.load_config()

    def test_no_lock_file_means_claude(self):
        """没有锁文件＝Claude 持棒。"""
        self.assertEqual(lock.holder("X"), "claude")

    def test_transfer_ok(self):
        """transfer 成功：锁变 codex，抄本同步变 codex。"""
        lock.transfer(self.cfg, "X", "codex", "claude", "test")
        self.assertEqual(lock.holder("X"), "codex")
        self.assertEqual(lock.read_holders(self.cfg)["projects"]["X"]["holder"], "codex")

    def test_duplicate_take_rejected(self):
        """重复拿锁被拒：已经是 codex 再交一次 claude→codex 抛错，锁不变。"""
        lock.transfer(self.cfg, "X", "codex", "claude", "test")
        with self.assertRaises(RelayError):
            lock.transfer(self.cfg, "X", "codex", "claude", "test")
        self.assertEqual(lock.holder("X"), "codex")

    def test_wrong_expected_holder_rejected(self):
        """期望持棒者不符时被拒：现在是 claude，却声称 codex 交出，被拒。"""
        with self.assertRaises(RelayError):
            lock.transfer(self.cfg, "X", "claude", "codex", "test")
        self.assertEqual(lock.holder("X"), "claude")

    def test_corrupt_lock_raises(self):
        """锁文件损坏（不是合法 JSON / holder 非法）时 read_lock 抛错。"""
        os.makedirs(os.path.dirname(self.env.lock_path()))
        with open(self.env.lock_path(), "w") as f:
            f.write("{not json")
        with self.assertRaises(Exception):
            lock.read_lock("X")
        with open(self.env.lock_path(), "w") as f:
            f.write('{"holder": "nobody"}')
        with self.assertRaises(RelayError):
            lock.read_lock("X")


class RedactTests(unittest.TestCase):
    CASES = [
        ("sk-FAKEFAKEFAKEFAKEFAKE1234", "sk-FAKEFAKEFAKEFAKEFAKE1234"),
        ("key AIzaSyFAKEFAKEFAKEFAKE1234567 end", "AIzaSyFAKEFAKEFAKEFAKE1234567"),
        ("tok ghp_" + "a1B2c3D4e5" * 3, "ghp_" + "a1B2c3D4e5" * 3),
        ("id AKIAABCDEFGHIJKL1234 end", "AKIAABCDEFGHIJKL1234"),
        ("Authorization: Bearer abcdefgh12345678xyz", "abcdefgh12345678xyz"),
        ("export MY_SECRET=topsecretvalue99", "topsecretvalue99"),
        ("password: hunter2hunter2", "hunter2hunter2"),
    ]

    def setUp(self):
        self.env = Env()
        self.addCleanup(self.env.cleanup)
        self.env.inproc()

    def test_secrets_not_in_audit_log(self):
        """各种密钥格式写进审计日志后，日志里搜不到原文。"""
        for text, needle in self.CASES:
            with self.subTest(needle=needle):
                audit.log("X", "test", "事件", "前缀 %s 后缀" % text)
                self.assertNotIn(needle, self.env.audit_text())
        self.assertIn(redact.MASK, self.env.audit_text())

    def test_redact_function(self):
        """redact() 本身把这些格式都换掉。"""
        for text, needle in self.CASES:
            with self.subTest(needle=needle):
                self.assertNotIn(needle, redact.redact(text))

    def test_secret_file_paths(self):
        """.env 之类路径判定为密钥文件，普通文件不是。"""
        for p in (".env", "a/b/.env.local", "id_rsa", "x/server.pem", "credentials.json", "/home/u/.netrc"):
            self.assertTrue(redact.is_secret_file(p), p)
        for p in ("a.txt", "environment.py", "src/main.py", "envfile", "docs/key_notes.md"):
            self.assertFalse(redact.is_secret_file(p), p)


class ManifestTests(unittest.TestCase):
    git = True

    def setUp(self):
        self.env = Env(git=self.git)
        self.addCleanup(self.env.cleanup)
        self.raw = ("raw",)

    def _w(self, rel, text):
        p = os.path.join(self.env.proj, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(text)

    def test_diff_exact_three_changes(self):
        """改一个、删一个、加一个未跟踪文件：diff 恰好报这三处，别处无。"""
        m0 = manifest.take(self.env.proj)
        self._w("a.txt", "alpha changed longer\n")
        os.remove(os.path.join(self.env.proj, "src", "main.py"))
        self._w("new_untracked.txt", "n\n")
        d = manifest.diff(m0, manifest.take(self.env.proj), self.raw)
        self.assertEqual(d["changed"], ["a.txt"])
        self.assertEqual(d["removed"], ["src/main.py"])
        self.assertEqual(d["added"], ["new_untracked.txt"])
        for k in ("raw", "protected", "git"):
            self.assertEqual(d[k], [], k)

    def test_raw_changes_go_to_raw(self):
        """raw 目录里的变化只归到 raw，不混进普通清单。"""
        m0 = manifest.take(self.env.proj)
        os.chmod(os.path.join(self.env.proj, "raw", "notes.txt"), 0o644)
        self._w("raw/notes.txt", "changed notes!!\n")
        self._w("raw/sub/new.wav", "x")
        d = manifest.diff(m0, manifest.take(self.env.proj), self.raw)
        self.assertEqual(len(d["raw"]), 2, d["raw"])
        self.assertTrue(any("raw/notes.txt" in x for x in d["raw"]))
        self.assertEqual((d["added"], d["changed"], d["removed"], d["protected"]), ([], [], [], []))

    def test_protected_changes(self):
        """.claude/ 的变化归 protected；Git 工程里 .git/hooks、.git/config 的变化也归 protected。"""
        m0 = manifest.take(self.env.proj)
        self._w(".claude/settings.json", '{"hooks": 1}\n')
        if self.git:
            gd = os.path.join(self.env.proj, ".git")
            with open(os.path.join(gd, "hooks", "my-hook"), "w") as f:
                f.write("#!/bin/sh\n")
            with open(os.path.join(gd, "config"), "a") as f:
                f.write("[alias]\n\tx = status\n")
        d = manifest.diff(m0, manifest.take(self.env.proj), self.raw)
        self.assertTrue(any(".claude/settings.json" in x for x in d["protected"]), d["protected"])
        if self.git:
            self.assertTrue(any("<git>/hooks/my-hook" in x for x in d["protected"]), d["protected"])
            self.assertTrue(any("<git>/config" in x for x in d["protected"]), d["protected"])
        self.assertEqual((d["added"], d["changed"], d["removed"], d["raw"]), ([], [], [], []))

    def test_light_vs_full(self):
        """轻量清单对完整清单能比：没改时无差异，改了能报出来。"""
        full = manifest.take(self.env.proj)
        light = manifest.take(self.env.proj, light=True)
        self.assertTrue(manifest.is_empty(manifest.diff(full, light, self.raw), ignore_git=True))
        self.assertTrue(manifest.is_empty(manifest.diff(light, full, self.raw), ignore_git=True))
        self._w("a.txt", "alpha changed longer\n")
        d = manifest.diff(full, manifest.take(self.env.proj, light=True), self.raw)
        self.assertEqual(d["changed"], ["a.txt"])
        d = manifest.diff(light, manifest.take(self.env.proj), self.raw)
        self.assertEqual(d["changed"], ["a.txt"])


class ManifestNoGit(ManifestTests):
    git = False


class SnapshotBase(object):
    git = True

    def setUp(self):
        self.env = Env(git=self.git)
        self.addCleanup(self.env.cleanup)

    def _gitstate(self):
        if not self.git:
            return None
        outs = []
        for args in (["status", "--porcelain=v1", "-b"], ["diff", "--cached"], ["diff"], ["rev-parse", "HEAD"],
                     ["branch", "-a"], ["stash", "list"], ["ls-files", "-s"]):
            outs.append(sh(["git"] + args, cwd=self.env.proj).stdout)
        return outs

    def _dirty(self):
        e = self.env
        with open(os.path.join(e.proj, "a.txt"), "a") as f:
            f.write("staged edit\n")
        if self.git:
            sh(["git", "add", "a.txt"], cwd=e.proj)
        with open(os.path.join(e.proj, "src", "main.py"), "a") as f:
            f.write("# unstaged edit\n")
        with open(os.path.join(e.proj, "untracked.txt"), "w") as f:
            f.write("u\n")

    def test_snapshot_restore_roundtrip(self):
        """拍快照→改一个、删一个、加一个→restore 到新文件夹：与拍快照时逐文件一致；原工程不被覆盖；Git 状态不变。"""
        e = self.env
        self._dirty()
        before_git = self._gitstate()
        expected = e.tree()
        r = e.relay("snapshot", e.code)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self._gitstate(), before_git, "拍快照改动了暂存区/分支/工作区")
        self.assertEqual(e.tree(), expected, "拍快照改动了工作区文件")
        # 之后再动工程
        with open(os.path.join(e.proj, "a.txt"), "w") as f:
            f.write("changed after snapshot\n")
        os.remove(os.path.join(e.proj, "docs", "STATE.md"))
        with open(os.path.join(e.proj, "added_later.txt"), "w") as f:
            f.write("later\n")
        after_edit = e.tree()
        r = e.relay("restore", e.code)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        dests = [n for n in os.listdir(os.path.join(e.snaps, e.code)) if n.startswith("restore-")]
        self.assertEqual(len(dests), 1, dests)
        dest = os.path.join(e.snaps, e.code, dests[0])
        restored = e.tree(root=dest)
        self.assertEqual(sorted(restored), sorted(expected))
        for k in expected:
            self.assertEqual(restored[k], expected[k], k)
        self.assertEqual(e.tree(), after_edit, "restore 动了原工程")
        self.assertIn("added_later.txt", r.stdout)

    def test_space_check_refuses(self):
        """放快照的盘剩余空间不足 2 倍时拒绝，且不留快照记录。"""
        e = self.env
        e.inproc()
        cfg = common.load_config()
        fake = shutil._ntuple_diskusage(10 ** 9, 10 ** 9, 10)  # 只剩 10 字节
        with mock.patch("shutil.disk_usage", return_value=fake):
            with self.assertRaises(RelayError):
                snapshot.take(cfg, e.code, "t")
        self.assertEqual(snapshot.list_snaps(cfg, e.code), [])


class SnapshotGit(SnapshotBase, unittest.TestCase):
    git = True


class SnapshotNoGit(SnapshotBase, unittest.TestCase):
    git = False


class ProtectTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.addCleanup(self.env.cleanup)
        self.raw = os.path.join(self.env.proj, "raw")

    def _modes(self):
        return {p: stat.S_IMODE(os.lstat(p).st_mode) for p in
                (self.raw, os.path.join(self.raw, "notes.txt"), os.path.join(self.raw, "sub"),
                 os.path.join(self.raw, "sub", "clip.wav"))}

    def test_protect_blocks_then_unprotect_restores(self):
        """protect 后删/改/改名/新建 raw 里的文件都被系统拒绝；unprotect 后恢复正常且权限与原来一致。"""
        before = self._modes()
        r = self.env.relay("protect", self.env.code)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        notes = os.path.join(self.raw, "notes.txt")
        with self.assertRaises(OSError):
            os.remove(notes)
        with self.assertRaises(OSError):
            open(notes, "a").close()
        with self.assertRaises(OSError):
            os.rename(notes, notes + ".bak")
        with self.assertRaises(OSError):
            open(os.path.join(self.raw, "new.txt"), "w").close()
        with self.assertRaises(OSError):
            os.remove(os.path.join(self.raw, "sub", "clip.wav"))
        r = self.env.relay("unprotect", self.env.code)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self._modes(), before)
        with open(notes, "a") as f:
            f.write("ok\n")
        os.rename(notes, notes + ".bak")
        os.rename(notes + ".bak", notes)
        os.remove(os.path.join(self.raw, "sub", "clip.wav"))


if __name__ == "__main__":
    unittest.main()
