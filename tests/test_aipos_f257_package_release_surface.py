"""AIPOS-F257(产品化 P1 发布面)— 安装包可用: 打包清单 + 版本单源 + 干净 HOME 装包冒烟 + 发布前自检 + 快照态红用例根因。

- 件① 打包清单: 对真实 `npm pack` 产物(本仓, 打到临时目录)跑 package_check.check_pack——distribution.schema npm_package.required
  (paths / trees / distributions 源)齐全、forbidden(测试文件 / 禁入式样 / 非 git 跟踪件)为零、包内文本凭据判据(F239 同一判定)零命中;
  判定本体 check_files 的缺件 / 禁入件 / 非跟踪件反例; 凭据命中反例。
- 件② 版本单源: package.json version 是唯一版本源(semver), pyproject.toml / package-lock.json 只校验一致(不一致反例点名文件与键);
  `lybra --version` 四种运行身份(npm 安装包 / 部署快照 / 开发模式 / 工作树)输出原文。
- 件③ 干净 HOME 装包冒烟: tarball 装进临时 prefix, 全新环境(临时 HOME, 无 LYBRA_* / AIPOS_*)逐步跑声明的 lybra 命令。
- 件④ 测试会话钉住宿主运行身份: 从部署快照目录跑测试时宿主部署状态不泄漏(deploy_runtime.hermetic_host_identity), 自造快照照判 deploy。
- 件⑤ 发布前自检: preflight 一次跑完版本 / 清单 / 冒烟 / 全量(靶场以桩命令替换全量), 末行一行结论; package.json 入口不含 npm publish。
只在临时目录打包 / 安装; 不发布、不登录 registry、不碰真实 ~/.lybra ~/.claude ~/.pi 与治理根。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from tools.aipos_cli import deploy_runtime as dr  # noqa: E402
from tools.aipos_cli import package_check as pc  # noqa: E402


def _show(text: str) -> None:
    print(text, flush=True)


@pytest.fixture(scope="module")
def packed(tmp_path_factory) -> dict:
    """本仓真实 npm pack 一次(临时目录), 件①③⑤共用。"""
    work = tmp_path_factory.mktemp("f257-pack")
    tarball = pc.pack(REPO_ROOT, work / "dist", work)
    return {"work": work, "tarball": tarball}


# ===========================================================================
# 件① 打包清单
# ===========================================================================

def test_item1_npm_pack_manifest_complete_and_clean(packed):
    decl = pc.package_declaration()
    result = pc.check_pack(REPO_ROOT, packed["tarball"], packed["work"], decl)
    _show(pc.manifest_summary(result))
    assert result["ok"], pc.manifest_summary(result)
    files = set(pc.tarball_files(packed["tarball"]))
    for top in ("schema", "agents", "tools", "web", "config", "bin"):
        assert result["tops"].get(top, 0) > 0, f"包内缺顶层 {top}/"
    # 必需件逐项(声明 paths + 分发源): schema 全部、顾问技能、角色章程母本、pi 扩展母本
    for rel in decl["required"]["paths"]:
        assert rel in files, rel
    for src in pc._distribution_sources(REPO_ROOT):
        assert src in files or any(f.startswith(src + "/") for f in files), src
    shown = sorted(f for f in files if f.startswith(("schema/", "agents/")))
    _show(f"[件①] schema/ + agents/ 包内 {len(shown)} 件:\n  " + "\n  ".join(shown))
    # 禁入件为零: 测试、夹具会话配置、卡报告、部署快照、tgz
    assert not [f for f in files if "/tests/" in f or f.startswith("tests/") or f.endswith("conftest.py") or "/test_" in f
                or f.endswith(".tgz") or "task_cards/" in f or ".deploy/" in f]
    assert result["credential_hits"] == [] and result["credential_scanned"] > 0


def test_item1_check_files_reports_missing_forbidden_untracked():
    decl = pc.package_declaration()
    sources = ["schema/verbs.schema.json", "schema/card.schema.json", "agents/skills/x/SKILL.md", "tools/aipos_cli/a.py",
               "web/board/.gitignore", "tools/aipos_cli/tests/test_a.py", *decl["required"]["paths"]]
    packed = [p for p in sources if p not in ("schema/verbs.schema.json", "agents/skills/x/SKILL.md", "web/board/.gitignore",
                                              "tools/aipos_cli/tests/test_a.py")]
    packed += ["tools/test_local.py", "tests/run-all.sh", "task_cards/AIPOS-X/RETURN.md", "lybra-0.2.0.tgz", "tools/aipos_cli/conftest.py",
               ".lybra/connection.json", "tools/scratch_local.py"]
    sources += ["tools/test_local.py", "tests/run-all.sh", "task_cards/AIPOS-X/RETURN.md", "tools/aipos_cli/conftest.py",
                ".lybra/connection.json"]
    result = pc.check_files(packed, sources, decl, source_basis="git", distribution_sources=["agents/skills", "agents/harness/pi/go.ts"])
    _show(pc.manifest_summary({**result}))
    missing = {m["path"]: m["why"] for m in result["missing"]}
    assert missing["schema/verbs.schema.json"] == "required.paths"
    assert missing["agents/skills/x/SKILL.md"] == "required.trees agents"
    assert missing["agents/harness/pi/go.ts"] == "distributions[].source.path"
    assert "web/board/.gitignore" not in missing and "tools/aipos_cli/tests/test_a.py" not in missing  # npm 省略件 / 测试件不算缺
    forbidden = {f["path"]: f["why"] for f in result["forbidden"]}
    assert forbidden["tools/test_local.py"].startswith("测试文件")
    assert forbidden["tests/run-all.sh"] == "禁入式样 tests/*"
    assert forbidden["task_cards/AIPOS-X/RETURN.md"] == "禁入式样 task_cards/*"
    assert forbidden["lybra-0.2.0.tgz"] == "禁入式样 *.tgz" and forbidden[".lybra/connection.json"] == "禁入式样 .lybra/*"
    assert forbidden["tools/aipos_cli/conftest.py"] == "禁入式样 */conftest.py"
    assert result["untracked"] == ["lybra-0.2.0.tgz", "tools/scratch_local.py"] and result["ok"] is False
    # 非 git 依据(如部署快照无 .git): 不判非跟踪件
    assert pc.check_files(packed, sources, decl, source_basis="filesystem", distribution_sources=[])["untracked"] == []


def test_item1_credential_in_packaged_text_is_caught(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "leak.md").write_text("# 部署\npassword: Zx9fQ2mPq7LwR4tY\n", encoding="utf-8")
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "x.py").write_text("password: Zx9fQ2mPq7LwR4tY\n", encoding="utf-8")
    hits, scanned = pc._credential_scan_tree(tmp_path, ["docs/leak.md", "tools/x.py"], skip_code_files=True)
    _show(f"[件①·凭据反例] 命中 {hits}(扫描 {scanned} 个文本文件, 代码文件跳过)")
    assert hits == [{"path": "docs/leak.md", "line": 2, "kind": "keyvalue_secret", "label": hits[0]["label"]}] and scanned == 1
    assert "Zx9f" not in json.dumps(hits, ensure_ascii=False)


# ===========================================================================
# 件② 版本单源
# ===========================================================================

def test_item2_version_single_source_and_mirrors(tmp_path):
    decl = pc.package_declaration()
    version = pc.declared_version(REPO_ROOT, decl)
    pkg = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))
    _show(f"[件②] 版本源 {decl['version_source']['file']} = {version}; mirrors = "
          + ", ".join(f"{m['file']}:{'.'.join(m['key_path'])}" for m in decl["version_source"]["mirrors"]))
    assert version == pkg["version"] == "0.3.0"
    assert pc.version_mirror_problems(REPO_ROOT, decl) == []
    for name in ("package.json", "package-lock.json", "pyproject.toml"):
        (tmp_path / name).write_text((REPO_ROOT / name).read_text(encoding="utf-8"), encoding="utf-8")
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(pyproject.read_text(encoding="utf-8").replace('version = "0.3.0"', 'version = "0.2.9"'), encoding="utf-8")
    problems = pc.version_mirror_problems(tmp_path, decl)
    _show(f"[件②·反例] {problems}")
    assert problems == ["pyproject.toml project.version = '0.2.9' ≠ 版本源 package.json '0.3.0'"]
    (tmp_path / "package.json").write_text(json.dumps({**pkg, "version": "0.3"}), encoding="utf-8")
    with pytest.raises(pc.PackageCheckError, match="不是 semver"):
        pc.declared_version(tmp_path, decl)


def _fake_tree(base: Path) -> Path:
    base.mkdir(parents=True)
    (base / "schema").symlink_to(REPO_ROOT / "schema")
    (base / "package.json").write_text((REPO_ROOT / "package.json").read_text(encoding="utf-8"), encoding="utf-8")
    return base


def test_item2_version_line_by_running_form(tmp_path):
    version = pc.declared_version(REPO_ROOT)
    package_root = _fake_tree(tmp_path / "prefix" / "lib" / "node_modules" / "lybra")
    scoped_root = _fake_tree(tmp_path / "proj" / "node_modules" / "@kiwiai" / "lybra")
    snapshot = _fake_tree(tmp_path / "prod" / ".deploy" / "releases" / "20261031_000000-abc1234")
    (snapshot / "VERSION").write_text("release: 20261031_000000-abc1234\ngit_commit: abc1234" + "0" * 33 + "\n", encoding="utf-8")
    (tmp_path / "prod" / ".deploy" / "current").symlink_to(snapshot)
    lines = {
        "package": dr.version_line(dr.running_code(code_root=package_root)),
        "package_scoped": dr.version_line(dr.running_code(code_root=scoped_root)),
        "deploy": dr.version_line(dr.running_code(code_root=snapshot)),
        "dev": dr.version_line(dr.RunningCode(REPO_ROOT, "dev")),
        "worktree": dr.version_line(dr.RunningCode(REPO_ROOT, "worktree")),
    }
    for k, v in lines.items():
        _show(f"[件②·--version {k}] {v}")
    assert lines["package"] == f"lybra {version}(npm 安装包; 代码根 {package_root})"
    assert lines["package_scoped"].startswith(f"lybra {version}(npm 安装包")
    assert lines["deploy"].startswith("lybra 部署快照 20261031_000000-abc1234(commit abc1234") and lines["deploy"].endswith(f"; 版本 {version}")
    assert lines["dev"].startswith(f"lybra 开发模式 --dev-tree {REPO_ROOT}(非部署快照; ") and lines["dev"].endswith(f"; 版本 {version}")
    assert lines["worktree"].startswith(f"lybra 工作树代码 {REPO_ROOT}(未经部署快照入口; ") and lines["worktree"].endswith(f"; 版本 {version}")
    broken = tmp_path / "broken" / "node_modules" / "lybra"
    _fake_tree(broken)
    (broken / "package.json").write_text("{}", encoding="utf-8")
    assert dr.version_line(dr.running_code(code_root=broken)).startswith("lybra 版本不明(")
    bare = tmp_path / "bare-tree"  # 无版本源的夹具树(F152 / F166 靶场形态): 原有输出不变, 不附版本
    bare.mkdir()
    assert dr.version_line(dr.RunningCode(bare, "worktree")).endswith("装入口: tools/lybra-deploy install-entry)")
    out = subprocess.run([sys.executable, "-m", "tools.aipos_cli", "--version"], cwd=str(REPO_ROOT), capture_output=True, text=True,
                         timeout=120)
    _show(f"[件②·工作树 python -m tools.aipos_cli --version 原文] exit {out.returncode}: {out.stdout.strip()}")
    assert out.returncode == 0 and out.stdout.strip().endswith(f"; 版本 {version}")


# ===========================================================================
# 件③ 干净 HOME 装包冒烟
# ===========================================================================

def test_item3_clean_home_install_smoke(packed):
    version = pc.declared_version(REPO_ROOT)
    work = packed["work"] / "smoke"
    result = pc.smoke_install(packed["tarball"], work, version=version)
    _show(pc.smoke_summary(result))
    for step in result["steps"]:
        _show(f"---- lybra {' '.join(step['argv'])} → exit {step['rc']} ----\n{step['output'][:1500]}")
    assert result["ok"], pc.smoke_summary(result)
    assert not [k for k in result["env_keys"] if k.startswith(("LYBRA_", "AIPOS_"))] and "HOME" in result["env_keys"]
    home = Path(result["home"])
    assert home != Path.home() and not any((home / d).exists() for d in (".lybra", ".claude", ".pi"))
    _show(f"[件③] 冒烟后临时 HOME 下: {sorted(p.name for p in home.iterdir())}")
    steps = {s["name"]: s for s in result["steps"]}
    assert steps["version"]["output"].startswith(f"lybra {version}(npm 安装包; 代码根 {result['prefix']}")
    assert (Path(result["home_root"]) / "smokeproj" / "project.json").is_file()


# ===========================================================================
# 件④ 测试会话钉住宿主运行身份
# ===========================================================================

def test_item4_hermetic_host_identity_only_pins_real_code_root(tmp_path, monkeypatch):
    snapshot = _fake_tree(tmp_path / "prod" / ".deploy" / "releases" / "20261031_000000-def5678")
    (snapshot / "VERSION").write_text("release: x\ngit_commit: def5678" + "0" * 33 + "\n", encoding="utf-8")
    (tmp_path / "prod" / ".deploy" / "current").symlink_to(snapshot)
    monkeypatch.setenv(dr.HERMETIC_ROOT_ENV, "")  # 记下原值, 用例结束复原(hermetic_host_identity 直接写 os.environ)
    monkeypatch.setattr(dr, "_code_root", lambda: snapshot)
    assert dr.running_code().mode == "deploy" and dr.DeployGuard.for_running_process().applicable
    assert dr.hermetic_host_identity() == str(snapshot.resolve())
    assert os.environ[dr.HERMETIC_ROOT_ENV] == str(snapshot.resolve())
    pinned = dr.running_code()
    _show(f"[件④] 钉住后本进程(代码根 = 快照 {snapshot.name}) running_code mode={pinned.mode}; DeployGuard 适用="
          f"{dr.DeployGuard.for_running_process().applicable}")
    assert pinned.mode == "worktree" and not dr.DeployGuard.for_running_process().applicable
    other = _fake_tree(tmp_path / "other" / ".deploy" / "releases" / "20261031_000000-aaa0000")
    (other / "VERSION").write_text("release: y\ngit_commit: aaa0000" + "0" * 33 + "\n", encoding="utf-8")
    (tmp_path / "other" / ".deploy" / "current").symlink_to(other)
    assert dr.running_code(code_root=other).mode == "deploy"  # 自造快照照真实判据
    # 子进程随环境继承: 同一代码根(此处以 running_code(code_root=快照) 判)仍非快照; 别的快照照判
    code = ("import sys; sys.path.insert(0, sys.argv[1]); from tools.aipos_cli import deploy_runtime as d; "
            "print(d.running_code(code_root=sys.argv[2]).mode, d.running_code(code_root=sys.argv[3]).mode)")
    out = subprocess.run([sys.executable, "-c", code, str(REPO_ROOT), str(snapshot), str(other)], capture_output=True, text=True,
                         timeout=60, env=dict(os.environ))
    _show(f"[件④·子进程继承] {out.stdout.strip()} {out.stderr[-300:]}")
    assert out.stdout.split() == ["worktree", "deploy"]


def test_item4_test_session_running_identity_is_not_host_deploy():
    """本测试进程(conftest 已调 isolate_test_session): 无论从卡工作树还是部署快照目录跑, 宿主运行身份都不是 deploy。"""
    running = dr.running_code()
    _show(f"[件④] 本测试进程代码根 {running.code_root} → mode={running.mode}; 钉住 = {os.environ.get(dr.HERMETIC_ROOT_ENV)}; "
          f"快照目录 = {dr.snapshot_deploy_dir(running.code_root)}")
    assert running.mode != "deploy" and not dr.DeployGuard.for_running_process().applicable


# ===========================================================================
# 件⑤ 发布前自检
# ===========================================================================

def test_item5_preflight_one_line_and_no_publish(tmp_path):
    pkg = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))
    _show(f"[件⑤] package.json scripts = {json.dumps(pkg['scripts'], ensure_ascii=False)}")
    assert pkg["scripts"]["preflight"] == "python3 -m tools.aipos_cli.package_check preflight"
    assert not [k for k, v in pkg["scripts"].items() if "publish" in k or "npm publish" in v]
    decl = pc.package_declaration()
    assert decl["preflight"]["suite_command"] == ["bash", "tests/run-all.sh"] and decl["preflight"]["pass_line"] == "ALL TEST FILES PASS"
    said: list[str] = []
    ok = pc.preflight(REPO_ROOT, work=tmp_path / "pass", say=said.append,
                      suite_command=[sys.executable, "-c", "print('x'); print(' ALL TEST FILES PASS')"])
    _show("\n".join(said) + "\n" + ok["line"])
    assert ok["ok"] and ok["line"].startswith(f"lybra 发布前自检 {pc.declared_version(REPO_ROOT)}: PASS(")
    assert "未执行 npm publish" in ok["line"] and "\n" not in ok["line"]
    bad = pc.preflight(REPO_ROOT, work=tmp_path / "fail", say=lambda _t: None,
                       suite_command=[sys.executable, "-c", "print(' SOME TESTS FAILED'); raise SystemExit(1)"])
    _show(bad["line"])
    assert not bad["ok"] and ": FAIL(" in bad["line"] and "全量测试 ✗" in bad["line"] and "打包清单 ✓" in bad["line"]


def test_item5_tarball_has_no_repo_root_artifacts(packed):
    with tarfile.open(packed["tarball"], "r:gz") as tf:
        names = [m.name for m in tf.getmembers()]
    assert all(n.startswith("package/") for n in names)
    assert not [n for n in names if n.endswith((".tgz", "/.env", "connection.json", ".pyc")) or "/.lybra/" in n or "/.deploy/" in n]
    assert os.path.getsize(packed["tarball"]) > 0
