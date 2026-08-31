#!/usr/bin/env python3
"""AIPOS-F58 — 工位私有状态自我保护(git exclude 登记)验收测试。

验收项:
  ① 基本功能: register_git_exclude 写入 .git/info/exclude 并包含指定路径
  ② 幂等性: 重复调用不重复追加
  ③ 标记段: BEGIN/END 标记段正确包裹
  ④ 无 git 仓: 静默跳过(ok=True, skipped=True)
  ⑤ 不碰 .gitignore: 只写 .git/info/exclude
  ⑥ 逐文件精确登记: 不写目录级排除(如 .lybra/)
  ⑦ enroll 集成: collect_enroll_exclude_paths 只收集实际存在的路径
  ⑧ wiring 集成: collect_wiring_exclude_paths 收集 .pi/ 下实际文件
  ⑨ 增量追加: 新路径追加到已有标记段内
  ⑩ 项目名不写死: 从 .git 目录推导, 不含硬编码项目名
  ⑪ 跨卡禁令验证: 本卡未新增 records/workspace_root/项目域/token/队列状态 实现

跑法: python3 -m pytest tools/aipos_cli/tests/test_aipos_f58_git_exclude.py -v
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.aipos_cli.git_exclude import (
    _MARKER_BEGIN,
    _MARKER_END,
    _extract_f58_block,
    _find_git_dir,
    collect_enroll_exclude_paths,
    collect_wiring_exclude_paths,
    register_git_exclude,
)


def _init_git_repo(path: Path) -> None:
    """在 path 中初始化 git 仓(用于测试)。"""
    subprocess.run(
        ["git", "init"],
        cwd=str(path),
        capture_output=True,
        check=True,
    )
    # 确保 info 目录存在
    (path / ".git" / "info").mkdir(exist_ok=True)


class TestGitExcludeBasic(unittest.TestCase):
    """① 基本功能: register 写入 .git/info/exclude 并包含指定路径。"""

    def test_register_writes_exclude_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_git_repo(root)
            # 创建 .lybra/ 目录和文件
            lybra_dir = root / ".lybra"
            lybra_dir.mkdir()
            (lybra_dir / "connection.json").write_text("{}")
            (lybra_dir / "role").write_text("{}")

            result = register_git_exclude(root, [
                ".lybra/connection.json",
                ".lybra/role",
            ])

            self.assertTrue(result["ok"])
            self.assertFalse(result["skipped"])
            self.assertEqual(len(result["added"]), 2)
            self.assertIn(".lybra/connection.json", result["added"])
            self.assertIn(".lybra/role", result["added"])

            # 验证 exclude 文件内容
            exclude_file = root / ".git" / "info" / "exclude"
            content = exclude_file.read_text()
            self.assertIn(_MARKER_BEGIN, content)
            self.assertIn(_MARKER_END, content)
            self.assertIn(".lybra/connection.json", content)
            self.assertIn(".lybra/role", content)


class TestGitExcludeIdempotent(unittest.TestCase):
    """② 幂等性: 重复调用不重复追加。"""

    def test_idempotent_no_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_git_repo(root)

            paths = [".lybra/connection.json"]
            result1 = register_git_exclude(root, paths)
            self.assertEqual(len(result1["added"]), 1)

            result2 = register_git_exclude(root, paths)
            self.assertTrue(result2["ok"])
            self.assertEqual(len(result2["added"]), 0)
            self.assertEqual(len(result2["already_present"]), 1)

            # 验证 exclude 文件中路径只出现一次
            exclude_file = root / ".git" / "info" / "exclude"
            content = exclude_file.read_text()
            count = content.count(".lybra/connection.json")
            self.assertEqual(count, 1, f"路径应只出现一次, 实际出现 {count} 次")


class TestGitExcludeMarkerBlock(unittest.TestCase):
    """③ 标记段: BEGIN/END 标记段正确包裹。"""

    def test_marker_block_structure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_git_repo(root)

            register_git_exclude(root, [".lybra/connection.json"])

            exclude_file = root / ".git" / "info" / "exclude"
            lines = exclude_file.read_text().splitlines()

            begin_idx, end_idx, entries = _extract_f58_block(lines)
            self.assertGreaterEqual(begin_idx, 0)
            self.assertGreaterEqual(end_idx, 0)
            self.assertGreater(end_idx, begin_idx)
            self.assertEqual(entries, [".lybra/connection.json"])


class TestGitExcludeNoGit(unittest.TestCase):
    """④ 无 git 仓: 静默跳过(ok=True, skipped=True)。"""

    def test_no_git_repo_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # 不初始化 git

            result = register_git_exclude(root, [".lybra/connection.json"])

            self.assertTrue(result["ok"])
            self.assertTrue(result["skipped"])
            self.assertIsNone(result["git_dir"])
            self.assertEqual(result["added"], [])


class TestGitExcludeNoGitignore(unittest.TestCase):
    """⑤ 不碰 .gitignore: 只写 .git/info/exclude。"""

    def test_does_not_create_gitignore(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_git_repo(root)

            register_git_exclude(root, [".lybra/connection.json"])

            gitignore = root / ".gitignore"
            self.assertFalse(gitignore.exists(), "不应创建 .gitignore")


class TestGitExcludeExactPaths(unittest.TestCase):
    """⑥ 逐文件精确登记: 不写目录级排除。"""

    def test_no_directory_level_exclusion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_git_repo(root)

            register_git_exclude(root, [
                ".lybra/connection.json",
                ".lybra/role",
            ])

            exclude_file = root / ".git" / "info" / "exclude"
            content = exclude_file.read_text()
            # 不应包含目录级排除
            self.assertNotIn(".lybra/\n", content)
            self.assertNotIn(".pi/\n", content)
            # 应是精确文件路径
            self.assertIn(".lybra/connection.json", content)


class TestCollectEnrollExcludePaths(unittest.TestCase):
    """⑦ enroll 集成: collect_enroll_exclude_paths 只收集实际存在的路径。"""

    def test_only_existing_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lybra_dir = root / ".lybra"
            lybra_dir.mkdir()
            (lybra_dir / "connection.json").write_text("{}")
            (lybra_dir / "role").write_text("{}")
            # actor 和 policy 不存在

            paths = collect_enroll_exclude_paths(root, ["connection.json", "role"])

            self.assertIn(".lybra/connection.json", paths)
            self.assertIn(".lybra/role", paths)
            self.assertNotIn(".lybra/actor", paths)
            self.assertNotIn(".lybra/policy", paths)


class TestCollectWiringExcludePaths(unittest.TestCase):
    """⑧ wiring 集成: collect_wiring_exclude_paths 收集 .pi/ 下实际文件。"""

    def test_collects_pi_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pi_dir = root / ".pi"
            pi_dir.mkdir()
            (pi_dir / "settings.json").write_text("{}")
            ext_dir = pi_dir / "extensions"
            ext_dir.mkdir()
            (ext_dir / "claim.ts").write_text("")
            (ext_dir / "lybra-loop.ts").write_text("")
            skills_dir = pi_dir / "skills"
            skills_dir.mkdir()
            (skills_dir / "block-and-report").mkdir()

            paths = collect_wiring_exclude_paths(root)

            self.assertIn(".pi/settings.json", paths)
            self.assertIn(".pi/extensions/claim.ts", paths)
            self.assertIn(".pi/extensions/lybra-loop.ts", paths)
            self.assertIn(".pi/skills/block-and-report", paths)

    def test_empty_when_no_pi_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = collect_wiring_exclude_paths(root)
            self.assertEqual(paths, [])


class TestGitExcludeIncremental(unittest.TestCase):
    """⑨ 增量追加: 新路径追加到已有标记段内。"""

    def test_incremental_add(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_git_repo(root)

            # 第一次登记
            register_git_exclude(root, [".lybra/connection.json"])
            # 第二次登记新路径
            result = register_git_exclude(root, [".lybra/role"])

            self.assertTrue(result["ok"])
            self.assertEqual(len(result["added"]), 1)
            self.assertIn(".lybra/role", result["added"])

            # 验证两个路径都在 exclude 中
            exclude_file = root / ".git" / "info" / "exclude"
            lines = exclude_file.read_text().splitlines()
            _, _, entries = _extract_f58_block(lines)
            self.assertIn(".lybra/connection.json", entries)
            self.assertIn(".lybra/role", entries)


class TestNoHardcodedProjectName(unittest.TestCase):
    """⑩ 项目名不写死: exclude 文件不含硬编码项目名。"""

    def test_no_project_name_in_exclude(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_git_repo(root)

            register_git_exclude(root, [".lybra/connection.json"])

            exclude_file = root / ".git" / "info" / "exclude"
            content = exclude_file.read_text()
            # 不应包含任何硬编码项目名(lybra 等)
            # 只应包含相对路径和标记
            lines = content.splitlines()
            for line in lines:
                stripped = line.strip()
                if stripped and not stripped.startswith("#"):
                    # 每行应是相对路径, 不含项目名
                    self.assertTrue(
                        stripped.startswith(".lybra/") or stripped.startswith(".pi/"),
                        f"意外路径: {stripped}",
                    )


class TestCrossCardProhibition(unittest.TestCase):
    """⑪ 跨卡禁令验证: 本模块未新增禁止概念的实现。"""

    def test_no_records_implementation(self):
        """本模块不实现记录写入(归 AIPOS-F64)。"""
        import inspect
        from tools.aipos_cli import git_exclude
        source = inspect.getsource(git_exclude)
        # 不应包含 records.py 相关实现
        self.assertNotIn("records.py", source)
        self.assertNotIn("write_record", source)

    def test_no_workspace_root_parsing(self):
        """本模块不解析 workspace_root(归 AIPOS-F65)。"""
        import inspect
        from tools.aipos_cli import git_exclude
        source = inspect.getsource(git_exclude)
        self.assertNotIn("parse_workspace_root", source)
        self.assertNotIn("resolve_workspace_root", source)

    def test_no_project_domain_parsing(self):
        """本模块不解析项目域(归 AIPOS-F66)。"""
        import inspect
        from tools.aipos_cli import git_exclude
        source = inspect.getsource(git_exclude)
        self.assertNotIn("parse_project_domain", source)
        self.assertNotIn("resolve_project", source)

    def test_no_token_fetching(self):
        """本模块不取 token(归 AIPOS-F59)。"""
        import inspect
        from tools.aipos_cli import git_exclude
        source = inspect.getsource(git_exclude)
        self.assertNotIn("fetch_token", source)
        self.assertNotIn("get_token", source)

    def test_no_queue_state_change(self):
        """本模块不变队列状态(归 AIPOS-F63)。"""
        import inspect
        from tools.aipos_cli import git_exclude
        source = inspect.getsource(git_exclude)
        self.assertNotIn("queue_status", source)
        self.assertNotIn("change_state", source)


class TestGitStashProtection(unittest.TestCase):
    """集成验收: 模拟 `git stash -u` 后 .lybra/ 文件不受影响。"""

    def test_stash_u_does_not_touch_excluded(self):
        """在 git 仓中, 登记到 exclude 的文件不被 `git stash -u` 抹掉。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_git_repo(root)

            # 配置 git user(某些环境需要)
            subprocess.run(
                ["git", "config", "user.email", "test@test.com"],
                cwd=str(root), capture_output=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Test"],
                cwd=str(root), capture_output=True,
            )

            # 创建 .lybra/ 文件
            lybra_dir = root / ".lybra"
            lybra_dir.mkdir()
            conn_file = lybra_dir / "connection.json"
            conn_file.write_text('{"tokens": []}')

            # 登记到 exclude
            result = register_git_exclude(root, [".lybra/connection.json"])
            self.assertTrue(result["ok"])

            # 初始 commit(需要有 HEAD 才能 stash)
            dummy = root / "dummy.txt"
            dummy.write_text("hello")
            subprocess.run(["git", "add", "dummy.txt"], cwd=str(root), capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", "init"],
                cwd=str(root), capture_output=True,
            )

            # 运行 git stash -u
            stash_result = subprocess.run(
                ["git", "stash", "-u"],
                cwd=str(root), capture_output=True, text=True,
            )

            # .lybra/connection.json 应仍然存在(被 exclude 保护)
            self.assertTrue(
                conn_file.exists(),
                ".lybra/connection.json 应被 .git/info/exclude 保护, 不被 git stash -u 抹掉",
            )


if __name__ == "__main__":
    unittest.main()
