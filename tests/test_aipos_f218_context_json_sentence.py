"""AIPOS-F218 件④ — 开工稿 / 执行与审计章程「跨机临时验证根」节补一句: run 内输入文件(如项目脚本读的 context.json)由持 run 方在自己的
run 内用只读命令(validation-root list --json / helper --sha256)生成, 不由顾问代生成; 改动走既有声明出口(verbs.schema
lybra_kickoff.validation_root_texts 的 helper_pin / charter_rules), 章程体积守卫(roles.schema charter_size_guard)内。

靶场 = AIPOS-F190 chris 形验证根靶场(临时治理根 / 临时 HOME / 本机模拟目标主机; 禁真门 / 真治理根 / 真工位)。原文经 _show 打到 stdout。
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

import test_aipos_f190_validation_root as f190  # noqa: E402  — chris 形验证根靶场唯一来源
from test_aipos_f190_validation_root import AUD, CARD, EXEC, _cli, _declare_both, _run_start, _show, rig  # noqa: E402,F401

VR_TITLE = "## 跨机临时验证根"


def test_item4_kickoff_and_charter_context_json_sentence(rig):  # noqa: F811
    from tools.aipos_cli.charter_render import charter_size_guard

    _declare_both(rig)
    assert _run_start(rig, "vr-hbx-60-executor-1")[0] == 0
    rc, brief, err = _cli("kickoff", "--task-id", CARD, "--workspace-root", rig["gov"])
    assert rc == 0, err
    lines = [ln for ln in brief.splitlines() if "context.json" in ln]
    _show("---- ⑤ lybra kickoff 验证根节 context.json 责任句原文 ----\n" + "\n".join(lines))
    assert lines and all("由持 run 方" in ln and "不由顾问代生成" in ln and "validation-root list --json" in ln for ln in lines)
    assert any("--sha256" in ln for ln in lines)
    guard = charter_size_guard()
    for role, inst in (("executor", EXEC), ("auditor", AUD)):
        rc, text, err = _cli("charter", "--role", role, "--instance", inst, "--workspace-root", rig["gov"])
        assert rc == 0, err
        start = text.index(VR_TITLE)
        section = text[start:text.index("<!-- lybra:charter-render", start)].rstrip("\n")
        hit = [ln for ln in section.splitlines() if "context.json" in ln]
        size, total = len(section.encode("utf-8")), len(text.encode("utf-8"))
        _show(f"---- ⑤ {role} 章程验证根节 context.json 句原文(节 {size} 字节 ≤ {guard['validation_root_section_max_bytes']}; "
              f"章程总长 {total} 字节 < {guard['max_bytes']})----\n" + "\n".join(hit))
        assert hit and "由持 run 方" in hit[0] and "不由顾问代生成" in hit[0]
        assert size <= guard["validation_root_section_max_bytes"] and total < guard["max_bytes"]


def test_f218_item4_fixture_discovered_by_runall():
    from tools.aipos_cli.workspace_config import default_test_contract, discover_test_files

    rel = "tests/test_aipos_f218_context_json_sentence.py"
    assert rel in discover_test_files([rel], default_test_contract())
