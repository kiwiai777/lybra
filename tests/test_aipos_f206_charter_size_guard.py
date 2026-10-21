"""AIPOS-F206 — 工位章程不再渲染全项目验证根明细 + 章程体积上限守卫。

实撞(chris 岚图 LANTU-VALID1R, 10-21): 审计 pi 工位章程 hbj-auditor/AGENTS.md 234180 字节, 其中「跨机临时验证根」节 169394 字节
(逐声明 / 逐 run 列全项目命令), 开工首轮 input 84221 tokens 后 stopReason=length 连败。

靶场(禁真门 / 真治理根 / 真工位 / 真 pi): AIPOS-F190 chris 形临时治理根 rig(执行者 = Codex 子 agent 在他机, 审计者 = 本机 pi 工位)
+ 直接造 50 条验证根声明(project.json validation_roots, 形同 declare 写入)。

验收原文经 _show 打到 stdout(RETURN 证据):
 ① 50 条声明: 章程验证根节与声明条数无关(1 条与 50 条逐字节相同)、节长 ≤ validation_root_section_max_bytes、总长 < max_bytes;
    对照: 旧渲染(本实例全部声明明细)随条数线性增长
 ② 有声明的卡: lybra kickoff 与 my-tasks next_card.kickoff 含本卡全部声明 / run / 命令原文, 他卡内容不出现; 同会话续接不重复整份 kickoff
 ③ 超限守卫: 渲染拒(CHARTER_OVERSIZE 原文); 工位旧章程超限 / loop 拉起前停见 test_aipos_f206_charter_oversize_launch.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

import test_aipos_f190_validation_root as f190  # noqa: E402  — chris 形验证根靶场唯一来源
from test_aipos_f190_validation_root import AUD, CARD, EXEC, HOST, _cli, _declare_both, _fp, _run_start, _show, rig  # noqa: E402,F401

N_DECLARATIONS = 50
VR_TITLE = "## 跨机临时验证根"


def _guard() -> dict:
    from tools.aipos_cli.charter_render import charter_size_guard

    return charter_size_guard()


def _charter(gov: Path, role: str, instance: str) -> str:
    rc, text, err = _cli("charter", "--role", role, "--instance", instance, "--workspace-root", gov)
    assert rc == 0, err
    return text


def _vr_section(charter: str) -> str:
    start = charter.index(VR_TITLE)
    end = charter.index("<!-- lybra:charter-render", start)
    return charter[start:end].rstrip("\n")


def _add_declarations(r: dict, n: int) -> list[str]:
    """造 n 条他卡验证根声明(执行 / 审计实例各半, 每条一个活动 run), 形同 `validation-root declare` + `run-start` 写入。返回他卡卡号。"""
    gov = r["gov"]
    data = json.loads((gov / "project.json").read_text(encoding="utf-8"))
    section = data.get("validation_roots") or {"declarations": []}
    cards = []
    for i in range(n):
        executor = i % 2 == 0
        task = f"HBX-{200 + i}" + ("" if executor else "R")
        cards.append(task)
        role = "executor" if executor else "auditor"
        section["declarations"].append({
            "id": f"vr-{task.lower()}-{role}-1", "host": HOST, "host_fingerprint": _fp(), "base": str(r["mac"] / f"HBX-{200 + i}"),
            "task_id": task, "instance": EXEC if executor else AUD, "role_class": role, "owner_ref": f190.OWNER_REF,
            "declared_by": f190.ADVISOR, "declared_at": "2026-10-21T00:00:00Z", "expires_at": "2999-01-01T00:00:00Z",
            "revoked_at": None, "revoked_by": None, "revoke_reason": None,
            "runs": [{"run_id": "run-1", "candidate": r["candidate"], "started_at": "2026-10-21T00:00:00Z", "started_by": f190.ADVISOR,
                      "finished_at": None, "finished_by": None}],
        })
    data["validation_roots"] = section
    (gov / "project.json").write_text(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    return cards


# ===========================================================================
# ① 章程验证根节与声明条数无关 / 有界
# ===========================================================================

def test_item1_charter_section_independent_of_declaration_count(rig):
    from tools.aipos_cli.validation_root import project_validation_roots, render_section_lines

    gov = rig["gov"]
    guard = _guard()
    zero = {role: _charter(gov, role, inst) for role, inst in (("executor", EXEC), ("auditor", AUD))}
    assert all(VR_TITLE not in text for text in zero.values()), "项目无声明 = 章程无验证根节(行为不变)"
    _declare_both(rig)
    assert _run_start(rig, "vr-hbx-60-executor-1")[0] == 0
    one = {role: _charter(gov, role, inst) for role, inst in (("executor", EXEC), ("auditor", AUD))}
    old_one = len("\n".join(render_section_lines(gov, instance=AUD)).encode("utf-8"))
    others = _add_declarations(rig, N_DECLARATIONS - 2)
    assert len(project_validation_roots(gov)) == N_DECLARATIONS
    fifty = {role: _charter(gov, role, inst) for role, inst in (("executor", EXEC), ("auditor", AUD))}
    old_fifty = len("\n".join(render_section_lines(gov, instance=AUD)).encode("utf-8"))
    for role in ("executor", "auditor"):
        sec1, sec50 = _vr_section(one[role]), _vr_section(fifty[role])
        size1, size50 = len(sec1.encode("utf-8")), len(sec50.encode("utf-8"))
        total = len(fifty[role].encode("utf-8"))
        _show(f"[① {role}] 声明 1 条 → 章程验证根节 {size1} 字节; 声明 {N_DECLARATIONS} 条 → {size50} 字节; "
              f"章程总长 {total} 字节 < 上限 {guard['max_bytes']}(节上限 {guard['validation_root_section_max_bytes']})")
        assert sec1 == sec50, "章程验证根节与声明条数 / run 无关(逐字节相同)"
        assert size50 <= guard["validation_root_section_max_bytes"] and total < guard["max_bytes"]
        assert "本卡的验证根声明、可写目录、run" in sec50 and "kickoff --task-id <本卡ID>" in sec50
        for needle in ("vr-", "run-1", str(rig["mac"]), CARD, *others[:3]):
            assert needle not in sec50, f"章程不列声明 / run / 目录明细: {needle!r}"
    _show(f"---- ① 审计者章程验证根节原文({N_DECLARATIONS} 条声明)----\n{_vr_section(fifty['auditor'])}")
    _show(f"[① 对照] 旧渲染(本实例全部声明明细, 审计实例): 1 条 {old_one} 字节 → {N_DECLARATIONS} 条 {old_fifty} 字节(随条数线性增长)")
    assert old_fifty > guard["max_bytes"] > old_one


def test_item1_section_texts_and_guard_declared_single_source():
    from tools.aipos_cli import charter_render, validation_root

    guard = _guard()
    assert guard["max_bytes"] == 40960 and guard["validation_root_section_max_bytes"] == 6144 and guard["refusal_code"] == "CHARTER_OVERSIZE"
    texts = validation_root.charter_section_texts()
    assert texts["charter_scope"] and len(texts["charter_rules"]) >= 5
    src = (REPO_ROOT / "tools" / "aipos_cli" / "charter_render.py").read_text(encoding="utf-8")
    assert "render_section_lines(" not in src.replace("render_charter_section_lines(", ""), "章程不再调逐声明明细渲染"
    assert "render_charter_section_lines(" in src and "check_charter_size(rendered" in src
    assert charter_render.CharterOversize.__mro__[1] is ValueError  # 消费方(sync / enroll 种子 / lybra charter)既有 ValueError 出口同拒


# ===========================================================================
# ② 本卡 kickoff 承接全部明细, 他卡不出现
# ===========================================================================

def test_item2_kickoff_carries_own_card_only(rig):
    from tools.aipos_cli.validation_root import render_section_lines

    gov = rig["gov"]
    _declare_both(rig)
    assert _run_start(rig, "vr-hbx-60-executor-1")[0] == 0
    others = _add_declarations(rig, N_DECLARATIONS - 2)
    own = render_section_lines(gov, instance=EXEC, task_id=CARD, harness="codex", include_inactive=True)
    assert own and any(f"- 声明 vr-hbx-60-executor-1" in ln for ln in own)
    rc, brief, err = _cli("kickoff", "--task-id", CARD, "--workspace-root", gov)
    assert rc == 0, err
    rc, out, err = _cli("--workspace-root", gov, "my-tasks", "--actor", EXEC, "--task-id", CARD, "--json")
    assert rc == 0, err
    data = json.loads(out)
    assert data.get("next_card"), json.dumps({k: v for k, v in data.items() if k not in ("workstation", "tasks")}, ensure_ascii=False)[:3000]
    my_tasks = data["next_card"]["kickoff"]
    section = brief[brief.index(VR_TITLE):]
    _show(f"---- ② lybra kickoff --task-id {CARD} 验证根节原文({N_DECLARATIONS} 条声明中本卡 1 条)----\n"
          + section[:section.find("\n## ", 1) if "\n## " in section[1:] else len(section)])
    for label, text in (("lybra kickoff", brief), ("my-tasks next_card.kickoff", my_tasks)):
        missing = [ln for ln in own if ln not in text]
        leaked = [c for c in others if c in text] + (["vr-hbx-60r-auditor-1"] if "vr-hbx-60r-auditor-1" in text else [])
        _show(f"[② {label}] 本卡验证根行 {len(own)} 行, 缺 {len(missing)}; 他卡({len(others)} 张 + 审计声明)出现 {len(leaked)}")
        assert not missing and not leaked, (missing[:2], leaked[:5])
        assert "当前活动 run: `run-1`" in text and f"{rig['base']}/{CARD}/run-1" in text and f"roles write-boundary --instance {EXEC} --task-id {CARD}" in text


def test_item2_resume_does_not_repeat_kickoff():
    from tools.aipos_cli.loop_driver import LaunchPlan

    kickoff = "整份开工提示\n" * 200
    plan = LaunchPlan(card=CARD, kickoff=kickoff, argv=["pi", "--session", "s1", kickoff])
    resumed = plan.resumed("同会话续接: 写完 RETURN.md")
    _show(f"[② 续接] 原 argv 末参 {len(kickoff)} 字 → 续接 argv {resumed.argv}")
    assert kickoff not in resumed.argv and resumed.argv[-1] == "同会话续接: 写完 RETURN.md" and resumed.kickoff == resumed.argv[-1]


# ===========================================================================
# ③ 体积守卫: 渲染拒
# ===========================================================================

def _small_guard(monkeypatch, max_bytes: int) -> None:
    from tools.aipos_cli import charter_render

    real = charter_render.charter_size_guard()
    monkeypatch.setattr(charter_render, "charter_size_guard", lambda: {**real, "max_bytes": max_bytes})


def test_item3_render_refuses_oversize(rig, monkeypatch):
    gov = rig["gov"]
    _declare_both(rig)
    size = len(_charter(gov, "auditor", AUD).encode("utf-8"))
    _small_guard(monkeypatch, size - 1)
    rc, out, err = _cli("charter", "--role", "auditor", "--instance", AUD, "--workspace-root", gov)
    _show(f"---- ③ 超限守卫(上限 {size - 1} 字节)lybra charter exit {rc} 原文 ----\n{(out + err).strip()}")
    assert rc != 0 and out.strip() == ""
    assert f"CHARTER_OVERSIZE: 实例 {AUD}(角色 auditor)章程渲染 {size} 字节 > 上限 {size - 1} 字节" in err
    assert "最大的节:" in err and "禁调高上限绕过" in err


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q", "-s"]))
