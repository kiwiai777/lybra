"""AIPOS-C4B 大项A/B 测试: 分发清单 + 工位 pull(sync)+ 版本信号。

覆盖:
- 清单构建器: 按 (role, harness) 组装, 每个分发物 = 文件列表 + sha256 + 源 commit。
- sync 纯函数: 差异检测(哈希比对)→ 拉取落盘 → 写 .version-{role} manifest。
- gate 拉取面: lybra_distribution_manifest / lybra_distribution_fetch(只读, 角色 scope,
  path traversal 拒绝)。

红线: 工位发起 pull; 禁任何 gate 侧 push; _distributed 生成物不入库。
"""
from __future__ import annotations

import base64
import hashlib
import json
import tempfile
from pathlib import Path

import pytest

from tools.distribution_manifest import (
    build_full_manifest,
    build_role_manifest,
    get_product_commit,
    target_base_for_kind,
)
from tools.aipos_cli import distribution_sync as ds


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


class TestManifestBuilder:
    def test_full_manifest_has_all_roles_and_hashes(self):
        repo = Path(__file__).resolve().parents[3]
        m = build_full_manifest(repo)
        assert set(m["roles"].keys()) >= {"executor", "auditor", "advisor"}
        exec_m = m["roles"]["executor"]
        assert exec_m["harness"] == "pi"
        kinds = {d["kind"] for d in exec_m["distributions"]}
        assert kinds >= {"extension", "skills", "charter", "schema"}
        for d in exec_m["distributions"]:
            assert d["source_commit"], d["distribution_id"]
            assert len(d["files"]) > 0
            for f in d["files"]:
                assert "path" in f and "sha256" in f and len(f["sha256"]) == 64

    def test_charter_is_file_distribution(self):
        repo = Path(__file__).resolve().parents[3]
        m = build_role_manifest(repo, "executor")
        charter = next(d for d in m["distributions"] if d["kind"] == "charter")
        assert charter["source_is_file"] is True
        assert charter["target_base"] == "harness_root"
        assert charter["target_path"] == "AGENTS.md"

    def test_skills_filtered_per_role(self):
        repo = Path(__file__).resolve().parents[3]
        ex = build_role_manifest(repo, "executor")
        au = build_role_manifest(repo, "auditor")
        ex_skills = next(d for d in ex["distributions"] if d["kind"] == "skills")
        au_skills = next(d for d in au["distributions"] if d["kind"] == "skills")
        ex_paths = {f["path"] for f in ex_skills["files"]}
        au_paths = {f["path"] for f in au_skills["files"]}
        # 执行体有 finalize-slice, 审计体没有; 审计体有 audit-independent-evidence, 执行体没有
        assert any(p.startswith("finalize-slice/") for p in ex_paths)
        assert not any(p.startswith("finalize-slice/") for p in au_paths)
        assert any(p.startswith("audit-independent-evidence/") for p in au_paths)
        assert not any(p.startswith("audit-independent-evidence/") for p in ex_paths)

    def test_product_commit_shape(self):
        repo = Path(__file__).resolve().parents[3]
        c = get_product_commit(repo)
        assert isinstance(c, str) and len(c) >= 7

    def test_target_base_rule(self):
        assert target_base_for_kind("charter") == "harness_root"
        assert target_base_for_kind("extension") == "harness_parent"
        assert target_base_for_kind("skills") == "harness_parent"
        assert target_base_for_kind("schema") == "harness_parent"


class TestSyncPureFunctions:
    def _remote(self) -> dict:
        return {
            "role": "executor",
            "product_commit": "abc123abc123",
            "harness": "pi",
            "distributions": [
                {
                    "distribution_id": "executor-loop-extension",
                    "kind": "extension",
                    "source_commit": "abc123abc123",
                    "source_is_file": False,
                    "target_base": "harness_parent",
                    "target_path": "_distributed/extensions/lybra-loop",
                    "files": [
                        {"path": "gate-client.ts", "sha256": _sha(b"new"), "size": 3},
                        {"path": "loop-engine.ts", "sha256": _sha(b"eng"), "size": 3},
                    ],
                },
                {
                    "distribution_id": "executor-charter",
                    "kind": "charter",
                    "source_commit": "abc123abc123",
                    "source_is_file": True,
                    "target_base": "harness_root",
                    "target_path": "AGENTS.md",
                    "files": [{"path": "AGENTS.md", "sha256": _sha(b"charter"), "size": 7}],
                },
            ],
        }

    def test_diff_detect_and_apply(self):
        tmp = Path(tempfile.mkdtemp())
        harness = tmp / "lybra-executor"
        harness.mkdir()
        remote = self._remote()

        # 陈旧文件: gate-client.ts 旧内容
        stale = harness.parent / "_distributed" / "extensions" / "lybra-loop" / "gate-client.ts"
        stale.parent.mkdir(parents=True, exist_ok=True)
        stale.write_text("old")

        diffs, _ = ds.compute_diffs(harness, remote)
        by_id = {d["dist"]["distribution_id"]: d["paths"] for d in diffs}
        assert "gate-client.ts" in by_id["executor-loop-extension"]
        assert "loop-engine.ts" in by_id["executor-loop-extension"]
        assert "AGENTS.md" in by_id["executor-charter"]

        content = {"gate-client.ts": b"new", "loop-engine.ts": b"eng", "AGENTS.md": b"charter"}
        for item in diffs:
            dist = item["dist"]
            files = [
                {"path": rel, "content_b64": base64.b64encode(content[rel]).decode()}
                for rel in item["paths"]
            ]
            ds.apply_fetch(harness, dist, files)

        assert (harness.parent / "_distributed" / "extensions" / "lybra-loop" / "gate-client.ts").read_bytes() == b"new"
        assert (harness / "AGENTS.md").read_bytes() == b"charter"

        # 二次 sync: 无差异
        diffs2, _ = ds.compute_diffs(harness, remote)
        assert diffs2 == []

    def test_apply_fetch_rejects_hash_mismatch(self):
        tmp = Path(tempfile.mkdtemp())
        harness = tmp / "lybra-executor"
        harness.mkdir()
        remote = self._remote()
        dist = remote["distributions"][0]
        with pytest.raises(ValueError):
            ds.apply_fetch(harness, dist, [{"path": "gate-client.ts", "content_b64": base64.b64encode(b"tampered").decode()}])

    def test_write_local_manifest(self):
        tmp = Path(tempfile.mkdtemp())
        harness = tmp / "lybra-executor"
        harness.mkdir()
        remote = self._remote()
        mp = ds.write_local_manifest(harness, remote)
        data = json.loads(mp.read_text())
        assert data["version"] == "abc123abc123"
        assert data["role"] == "executor"
        assert len(data["distributions"]) == 2
        # 连接器版本信号只读 version 字段(源 commit 短哈希)
        assert data["version"] == remote["product_commit"]


class TestHarnessRootResolution:
    """AIPOS-F3: sync 工位根解析——裸跑禁猜角色, 解析不到即出声停。"""

    def test_no_harness_root_no_env_raises_with_tried(self, monkeypatch):
        """无 --harness-root + 无 env → 报错并列出找过哪几层。"""
        monkeypatch.delenv("LYBRA_HARNESS_ROOT", raising=False)
        with pytest.raises(ValueError, match="无法确定 harness-root") as exc_info:
            ds.resolve_sync_context()
        msg = str(exc_info.value)
        assert "--harness-root=<未提供>" in msg
        assert "LYBRA_HARNESS_ROOT=<未设置>" in msg
        assert "正确用法" in msg

    def _make_enrolled_harness(self, path: Path, role: str, token: str = "test-token") -> Path:
        """Helper: 创建一个已 enroll 的工位目录。"""
        path.mkdir(parents=True, exist_ok=True)
        lybra = path / ".lybra"
        lybra.mkdir()
        (lybra / "role").write_text(role)
        (lybra / "connection.json").write_text(json.dumps({
            "mcp": {"rpc_url": "http://localhost:7118/mcp"},
            "tokens": [{"role": role, "token": token}],
        }))
        return path

    def test_env_fallback(self, monkeypatch, tmp_path):
        """LYBRA_HARNESS_ROOT 环境变量可作为 fallback。"""
        harness = self._make_enrolled_harness(tmp_path / "my-harness", "executor", "test-token-123")
        monkeypatch.setenv("LYBRA_HARNESS_ROOT", str(harness))
        ctx = ds.resolve_sync_context()
        assert ctx["harness_root"] == harness.resolve()
        assert ctx["role"] == "executor"

    def test_explicit_overrides_env(self, monkeypatch, tmp_path):
        """显式 --harness-root 优先于 env。"""
        explicit = self._make_enrolled_harness(tmp_path / "explicit-harness", "auditor", "test-token-456")
        monkeypatch.setenv("LYBRA_HARNESS_ROOT", str(tmp_path / "other"))
        ctx = ds.resolve_sync_context(harness_root=explicit)
        assert ctx["harness_root"] == explicit.resolve()
        assert ctx["role"] == "auditor"


class TestValidateEnrolled:
    """AIPOS-F3: 落盘前校验目标为已 enroll 工位。"""

    def test_no_lybra_dir_rejected(self, tmp_path):
        """无 .lybra/ 目录 → 拒绝。"""
        with pytest.raises(ValueError, match="不是已注册工位"):
            ds._validate_enrolled(tmp_path)

    def test_no_role_file_rejected(self, tmp_path):
        """有 .lybra/ 但无 role 文件 → 拒绝。"""
        (tmp_path / ".lybra").mkdir()
        with pytest.raises(ValueError, match="未完成 enroll"):
            ds._validate_enrolled(tmp_path)

    def test_enrolled_accepted(self, tmp_path):
        """有 .lybra/role → 通过。"""
        lybra = tmp_path / ".lybra"
        lybra.mkdir()
        (lybra / "role").write_text("executor")
        ds._validate_enrolled(tmp_path)  # 不抛即过


class TestGatePullSurface:
    def _cap(self) -> str:
        return '{"role":"executor","token_ref":"svc-executor","expires_at":"2099-01-01T00:00:00Z"}'

    def test_manifest_verb_role_scoped(self, monkeypatch):
        from tools.mcp_server import tools as mt
        monkeypatch.setenv(mt.CAPABILITY_ENV_VAR, self._cap())
        # REQUEST_CAPABILITY contextvar 未设 → 走 env fallback
        r = mt.lybra_distribution_manifest({})
        sc = r["structuredContent"]
        assert sc["ok"] is True
        assert sc["role"] == "executor"
        assert sc["product_commit"]
        assert len(sc["distributions"]) >= 3

    def test_manifest_verb_requires_role(self, monkeypatch):
        from tools.mcp_server import tools as mt
        monkeypatch.setenv(mt.CAPABILITY_ENV_VAR, '{"token_ref":"x","expires_at":"2099-01-01T00:00:00Z"}')
        r = mt.lybra_distribution_manifest({})
        sc = r["structuredContent"]
        assert sc["ok"] is False

    def test_fetch_verb_rejects_traversal(self, monkeypatch):
        from tools.mcp_server import tools as mt
        monkeypatch.setenv(mt.CAPABILITY_ENV_VAR, self._cap())
        r = mt.lybra_distribution_fetch({
            "distribution_id": "executor-loop-extension",
            "paths": ["../../../../etc/passwd"],
        })
        sc = r["structuredContent"]
        assert sc["ok"] is False

    def test_fetch_verb_reads_real_file(self, monkeypatch):
        from tools.mcp_server import tools as mt
        monkeypatch.setenv(mt.CAPABILITY_ENV_VAR, self._cap())
        r = mt.lybra_distribution_fetch({
            "distribution_id": "executor-charter",
            "paths": ["AGENTS.md"],
        })
        sc = r["structuredContent"]
        assert sc["ok"] is True
        assert len(sc["files"]) == 1
        content = base64.b64decode(sc["files"][0]["content_b64"]).decode()
        assert "lybra-executor" in content
