"""AIPOS-F203 件③ — 验证 helper 版本变化随更新通知说明兼容性(部署唯一口 compile_on_deploy 自动附条目, `lybra updates` 可见)。

靶场 = AIPOS-F167 同一靶场(临时 HOME / 脚手架临时治理根 + 两顾问实例 / 临时产品仓 + 假部署目录; 本进程运行代码伪装为该部署快照)。
临时产品仓里按声明路径(roles.schema validation_root.helper_module)提交 helper v1 → 主线合入改了 helper 的卡(v2: 新增子命令 / 改参数),
部署 = 合入提交。不碰真实治理根、真实 .deploy、生产门与 pi。验收原文经 _show 打到 stdout(RETURN 证据)。
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_aipos_f131_loop_run_status import _cli  # noqa: E402  — 进程内 CLI 读法唯一来源
from test_aipos_f167_advisor_updates import ADV_A, NOTICE, _card, _fake_deploy, _git, _show, _write, rig  # noqa: E402,F401  — F167 靶场
from tools.aipos_cli import advisor_updates as au  # noqa: E402

HELPER = REPO_ROOT / "tools" / "aipos_cli" / "validation_root_helper.py"
V1 = '''import argparse


def _parser():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("fingerprint")
    check = sub.add_parser("check")
    check.add_argument("--run-dir")
    for verb in (("sim-create", "x"), ("sim-delete", "y")):
        sp = sub.add_parser(verb[0])
        sp.add_argument("--run-dir")
    return parser
'''
V2 = V1.replace('    check.add_argument("--run-dir")\n', '    check.add_argument("--run-dir")\n    check.add_argument("--sim-udid")\n') \
       .replace("    return parser\n", '    evm = sub.add_parser("evidence-manifest")\n    evm.add_argument("--seal")\n    return parser\n')


def _commit_helper(product: Path, text: str, message: str) -> str:
    _write(product / au._helper_module_rel(), text)
    _git(product, "add", "-A")
    _git(product, "-c", "user.email=f203@example.invalid", "-c", "user.name=f203", "commit", "-q", "-m", message)
    return _git(product, "rev-parse", "HEAD")


def _merge_card(product: Path, branch: str, text: str) -> str:
    """卡分支改 helper → 主线 merge --no-ff(与 finalize 合入同形); 返回合入提交。"""
    _git(product, "checkout", "-q", "-b", branch)
    _commit_helper(product, text, f"{branch}: change helper")
    _git(product, "checkout", "-q", "main")
    _git(product, "-c", "user.email=f203@example.invalid", "-c", "user.name=f203", "merge", "-q", "--no-ff", branch, "-m", f"Merge {branch}")
    return _git(product, "rev-parse", "HEAD")


def test_item3_helper_change_notice_on_deploy_visible_in_updates(rig):
    gov, product = rig.gov, rig.product
    _commit_helper(product, V1, "helper v1")
    merge = _merge_card(product, "card/F203PROBE-1", V2)
    _fake_deploy(product, merge, release="r-b")
    _card(gov, "F203PROBE-1", "completed")  # 本卡卡面无 advisor_notice: 兼容说明仍由部署口自动附
    compiled = au.compile_on_deploy(product, gov, "F203PROBE-1", merge_commit=merge)
    _show(f"---- 件③ 部署汇总: {compiled['line']} ----")
    assert compiled["written"] and [e["source"] for e in compiled["entries"]] == ["helper_change"]
    entry = compiled["entries"][0]
    _show(f"[件③ 兼容说明条目] {entry}")
    import hashlib

    old12, new12 = hashlib.sha256(V1.encode()).hexdigest()[:12], hashlib.sha256(V2.encode()).hexdigest()[:12]
    assert entry["summary"] == (f"验证 helper 摘要变化: {old12} → {new12}(子命令 新增 evidence-manifest / 移除 无 / 参数变化 check); "
                                "进行中 run 不受影响(按 run 固定 run-start 时版本), 新 run 用新版")
    assert entry["audience"] == ["advisor", "executor", "auditor"] and entry["task_id"] == "F203PROBE-1"
    assert any("helper --id <声明> --run-id <run> --sha256" in a and "禁写死" in a for a in entry["actions"])

    rc, out, err = _cli(["updates", "--workspace-root", str(gov), "--actor", ADV_A])
    _show(f"---- 件③ `lybra updates --actor {ADV_A}` 原文 rc={rc} ----\n{out}{err}")
    assert rc == 0 and entry["summary"] in out and "lybra project validation-root helper --id <声明> --run-id <run> --sha256" in out
    again = au.compile_on_deploy(product, gov, "F203PROBE-1", merge_commit=merge)
    _show(f"[件③ 同卡重跑(幂等)] {again['line']}")
    assert again["written"] is None


def test_item3_unchanged_helper_no_notice_and_card_notice_kept(rig):
    gov, product = rig.gov, rig.product
    _commit_helper(product, V1, "helper v1")
    _git(product, "checkout", "-q", "-b", "card/F203PROBE-2")
    _write(product / "docs" / "x.md", "doc\n")
    _git(product, "add", "-A")
    _git(product, "-c", "user.email=f203@example.invalid", "-c", "user.name=f203", "commit", "-q", "-m", "docs only")
    _git(product, "checkout", "-q", "main")
    _git(product, "-c", "user.email=f203@example.invalid", "-c", "user.name=f203", "merge", "-q", "--no-ff", "card/F203PROBE-2", "-m", "Merge")
    merge = _git(product, "rev-parse", "HEAD")
    _fake_deploy(product, merge, release="r-c")
    _card(gov, "F203PROBE-2", "completed", {"advisor_notice": NOTICE})
    compiled = au.compile_on_deploy(product, gov, "F203PROBE-2", merge_commit=merge)
    _show(f"[件③ helper 未变] {compiled['line']} sources={[e['source'] for e in compiled['entries']]}")
    assert [e["source"] for e in compiled["entries"]] == ["card"], "helper 未变 = 不附兼容说明(行为不变)"
    assert au.helper_change_entry(product, merge, "F203PROBE-2") is None
    assert au.helper_change_entry(rig.tmp, "a" * 40, "X") is None, "非 Lybra 产品仓 / 无此版本 = 不附(两版都无 helper)"


def test_item3_parses_real_helper_and_history():
    subs = au.helper_subcommands(HELPER.read_text(encoding="utf-8"))
    _show(f"[件③ 现行 helper 子命令] {sorted(subs)}")
    assert {"fingerprint", "check", "init-run", "src-verify", "sim-create", "sim-delete", "evidence-manifest", "evidence-bundle"} <= set(subs)
    assert "--device-type" in subs["sim-create"] and "--device-type" not in subs["sim-delete"], "循环内按子命令分支归参"
    assert au._helper_module_rel() == "tools/aipos_cli/validation_root_helper.py"
    decl = au.helper_change_declaration()
    assert "{old12}" in decl["summary"] and "{new12}" in decl["summary"] and "进行中 run 不受影响" in decl["summary"]
