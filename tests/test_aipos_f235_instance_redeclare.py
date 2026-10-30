"""AIPOS-F235 — 已接入实例改登记 + 子 agent 卡 harness 随派发顾问。

件① `lybra roles redeclare`(唯一实现 enrollment.redeclare_instance): 两阶段; 预演列新旧差异; 正式 = 追加 redeclare 位置事件(与 land / relocate
    同族同写口, 只追加)+ 经 enroll_client.write_role_file 更新治理根实例槽, 一次落账; registered_harness / instance_enrollment / location_event
    读最新事件; 实例槽与日志不一致 = state lint ENROLLMENT_REGISTRATION_DRIFT(以日志为准)。
件② 授权: 改到项目常设授权已列主机 / 拉起方式内 = 本实例或项目顾问可做(第二档, 记录 = 位置事件 authorized_by); 授权外 = 第一档进 Owner 收件箱,
    批准后重跑生效; 凭据不动。
件③ 子 agent 卡缺省 harness = 派发顾问现登记(enrollment.dispatch_harness; 起草缺省 / card_harness / next 与 kickoff 不符出口同读)。
件④ 看板工位页 / workstation-plan 显示登记 harness / 主机 / 来源事件。

靶场: 临时治理根(chris 形, 自造人肉期样本: 顾问 land 无 harness=、实例槽 codex@他机)/ 临时 HOME; 不读真实凭据、不连真实门、不写真实治理根。
"""
from __future__ import annotations

import json
import re
import socket
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

import test_aipos_f78_engine_agnostic as f78  # noqa: E402  — 靶场骨架唯一来源
from test_aipos_f187_kickoff_worktree_cleanup import CHRIS, CHRIS_EXEC, _cli, _land_subagent, _update_project  # noqa: E402
from test_aipos_f73d_loop_driver import init_governance_repo  # noqa: E402  — 靶场治理仓构件唯一来源
from tools.aipos_cli.frontmatter import parse_markdown_frontmatter  # noqa: E402

LOCAL = socket.gethostname()
MAC = "advmac"
ADV_OTA = f"advisor-ota.{CHRIS}.{MAC}"     # 仍在 Mac Codex 的顾问
ADV_LANTU = f"advisor-lantu.{CHRIS}.{MAC}"  # 换到本机 Claude Code 的顾问(实例名主机段只是历史)
AUDIT = f"audit.{CHRIS}.dev"
MAC_DIR = "/Users/kiwi/chris-demo"


def _show(text: str) -> None:
    print(text, flush=True)


def _land(gov: Path, instance: str, role: str, reason: str) -> None:
    from tools.aipos_cli.enrollment import _trail_line, _write_trail_line, enrollment_trail_path

    _write_trail_line(enrollment_trail_path(gov), _trail_line(action="land", code_id=f"enroll_fx_{instance.split('.')[0]}", role=role,
                                                              instance=instance, project=CHRIS, by="(agent-enroll)", reason=reason))


def _authorize(gov: Path, *, hosts: list[str], launch: list[str]) -> str:
    """项目常设授权 r0001(靶场自造; 渲染 = 产品唯一 build_authorization_markdown, 落点 = authorization_revision_relpath)。"""
    from tools.aipos_cli.autonomy_policy import authorization_id_for, authorization_revision_relpath, build_authorization_markdown

    aid = authorization_id_for(CHRIS)
    record = {"authorization_id": aid, "revision": 1, "project": CHRIS, "status": "active", "approved_by_owner": True,
              "owner_approval_ref": "fixture-owner", "action": "grant", "revised_at": "2026-10-01T00:00:00Z",
              "active_from": "2026-10-01T00:00:00Z", "expires_at": "2999-01-01T00:00:00Z", "max_tasks": 50,
              "lanes": [{"repo": "android-app", "task_modes": ["code"], "drivers": [ADV_OTA, ADV_LANTU]}], "hosts": hosts,
              "launch_harnesses": launch, "zones_open": [], "supersedes_policies": [], "previous_revision": None, "notes": ""}
    path = gov / authorization_revision_relpath(gov, aid, 1)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_authorization_markdown(record), encoding="utf-8")
    return aid


def _rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, launch: list[str]) -> Path:
    """chris 形治理根: 两顾问(人肉期样本: land 无 harness=, 实例槽 codex@advmac)+ 共享子 agent 执行身份(land mode=subagent harness=codex)
    + 执行方式声明(缺省子 agent)+ 项目常设授权(hosts = 本机 + advmac; 拉起方式按参数)。"""
    from tools.aipos_cli.enroll_client import write_role_file

    gov = f78._make_gov(tmp_path, monkeypatch, shape="chris", project=CHRIS)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    app = f78._init_product_repo(tmp_path / "repos" / "android-app")
    _update_project(gov, code_repo=str(app), repos={"default": "android-app", "items": {"android-app": str(app)}},
                    test_contract={"runall_path": "tests/run-all.sh", "post_merge_regression": {"timeout_seconds": 1500}},
                    execution={"default_mode": "subagent", "subagent_executor": CHRIS_EXEC, "auditor": AUDIT, "pi_executor": None,
                               "pi_allowed_task_modes": []})
    (gov / "5_tasks" / "records" / "sessions").mkdir(parents=True, exist_ok=True)
    for adv in (ADV_OTA, ADV_LANTU):
        _land(gov, adv, "advisor", f"host={LOCAL} workstation={gov} files=['connection.json', 'role']")
        write_role_file(gov / ".lybra", "advisor", adv, None, harness={"kind": "codex", "dir": MAC_DIR, "host": MAC},
                        slot_by_instance=True)
    _land_subagent(gov, CHRIS_EXEC, project=CHRIS, harness="codex", host=MAC)
    _authorize(gov, hosts=[LOCAL, MAC], launch=launch)
    init_governance_repo(gov)
    return gov


def _redeclare(gov: Path, *extra: str, confirm: bool = False, actor: str = ADV_LANTU) -> tuple[int, str, str]:
    argv = ["roles", "redeclare", "--workspace-root", str(gov), "--instance", ADV_LANTU, "--actor", actor, *extra]
    return _cli(*argv, *(["--confirm"] if confirm else []))



def _log_lines(gov: Path) -> list[str]:
    from tools.aipos_cli.enrollment import enrollment_trail_path

    return [ln for ln in enrollment_trail_path(gov).read_text(encoding="utf-8").splitlines() if ln.startswith("- ")]


def _role_doc(gov: Path) -> dict:
    return json.loads((gov / ".lybra" / "role").read_text(encoding="utf-8"))


# ===========================================================================
# 件① 改登记正门: codex@他机 → claude-code@本机(第二档)
# ===========================================================================

def test_item1_redeclare_codex_mac_to_claude_code_local(tmp_path, monkeypatch):
    from tools.aipos_cli.enrollment import instance_enrollment, location_event, registered_harness, registration_drift

    gov = _rig(tmp_path, monkeypatch, launch=["pi", "claude-code"])
    before = _log_lines(gov)
    reg0 = registered_harness(gov, ADV_LANTU)
    _show(f"[件①·人肉期样本现登记] {reg0['kind']}@{reg0['host']}:{reg0['dir']} 来源 {reg0['source']}; "
          f"instance_enrollment.harness={instance_enrollment(gov, ADV_LANTU)['harness']}")
    assert (reg0["kind"], reg0["host"], reg0["dir"], reg0["source"]) == ("codex", MAC, MAC_DIR, "role_slot")

    rc, out, err = _redeclare(gov, "--harness", "claude-code", "--dir", str(gov))
    _show(f"---- 件①·预演原文 rc={rc} ----\n{out}{err}")
    assert rc == 0 and "预览" in out
    assert f"kind: codex → claude-code" in out and f"host: {MAC} → {LOCAL}" in out and f"dir: {MAC_DIR} → {gov}" in out
    assert "第二档" in out and "auth_chris-demo@r1" in out and "将追加位置事件" in out and "凭据不动、不重发码" in out
    assert _log_lines(gov) == before, "预演零写入"

    rc, out, err = _redeclare(gov, "--harness", "claude-code", "--dir", str(gov), confirm=True)
    _show(f"---- 件①·正式原文 rc={rc} ----\n{out}{err}")
    assert rc == 0 and "已写入" in out and "落账: verdict=PASS" in out
    after = _log_lines(gov)
    assert after[:len(before)] == before and len(after) == len(before) + 1, "只追加, 旧事件保留"
    _show(f"[件①·新位置事件] {after[-1]}")
    assert re.search(r"\sredeclare\s+code_id=enroll_fx_advisor-lantu\s+role=advisor\s", after[-1])
    assert f"harness=claude-code harness_host={LOCAL} harness_dir={gov} from_harness=codex from_host={MAC} from_dir={MAC_DIR}" in after[-1]
    assert f"authorized_by=auth_chris-demo@r1 workstation={gov}" in after[-1]
    slot = _role_doc(gov)["instances"][ADV_LANTU]
    _show(f"[件①·实例槽] {json.dumps(slot, ensure_ascii=False)}")
    assert slot["harness"] == {"kind": "claude-code", "dir": str(gov)} and slot["redeclared_at"] and slot["enrolled_at"]
    reg = registered_harness(gov, ADV_LANTU)
    view = instance_enrollment(gov, ADV_LANTU)
    ev = location_event(gov, ADV_LANTU)
    _show(f"[件①·读口] registered={reg['kind']}@{reg['host']}:{reg['dir']}({reg['source_label']}) instance_enrollment.harness={view['harness']} "
          f"location_event.action={ev['action']} workstation={view['workstation']['dir']}")
    assert (reg["kind"], reg["host"], reg["dir"], reg["source"]) == ("claude-code", LOCAL, str(gov), "event")
    assert view["harness"] == "claude-code" and view["role"] == "advisor" and ev["action"] == "redeclare"
    assert view["workstation"]["found"] and view["workstation"]["dir"] == str(gov), "凭据落点(治理根)不变"
    assert registration_drift(gov) == []

    # 再跑同一改登记 = 无变化拒(零写入)
    rc, out, err = _redeclare(gov, "--harness", "claude-code", "--dir", str(gov), confirm=True)
    _show(f"[件①·重复改登记] rc={rc} {err.strip()}")
    assert rc == 1 and "REDECLARE_NO_CHANGE" in err and len(_log_lines(gov)) == len(after)


def test_item1_slot_drift_log_wins_and_state_lint(tmp_path, monkeypatch):
    from tools.aipos_cli.enroll_client import write_role_file
    from tools.aipos_cli.enrollment import registered_harness
    from tools.aipos_cli.state_lint import run_state_lint

    gov = _rig(tmp_path, monkeypatch, launch=["pi", "claude-code"])
    assert _redeclare(gov, "--harness", "claude-code", "--dir", str(gov), confirm=True)[0] == 0
    clean = run_state_lint(gov)
    assert not [n for n in clean["notes"] if n.get("code") == "ENROLLMENT_REGISTRATION_DRIFT"]
    # 实例槽被旧写法改回(与日志不一致)= 读口以日志为准, state lint 报出
    write_role_file(gov / ".lybra", "advisor", ADV_LANTU, None, harness={"kind": "codex", "dir": MAC_DIR, "host": MAC}, slot_by_instance=True)
    reg = registered_harness(gov, ADV_LANTU)
    lint = run_state_lint(gov)
    drift = [n for n in lint["notes"] if n.get("code") == "ENROLLMENT_REGISTRATION_DRIFT"]
    _show(f"[件①·槽漂移] 读口 {reg['kind']}@{reg['host']}({reg['source']}); lint notes:\n" + "\n".join(n["message"] for n in drift))
    assert reg["kind"] == "claude-code" and reg["source"] == "event"
    assert len(drift) == 1 and drift[0]["task_id"] == ADV_LANTU and drift[0]["severity"] == "WARN"
    assert "['kind', 'dir', 'host']" in drift[0]["message"] and "lybra roles redeclare" in drift[0]["message"]
    assert not lint["issues"], "信息级, 不计断层"


def test_item1_refusals_zero_write(tmp_path, monkeypatch):
    gov = _rig(tmp_path, monkeypatch, launch=["pi", "claude-code"])
    pi_ws = tmp_path / "ws-pi"
    pi_ws.mkdir()
    _land(gov, f"hbj-coder.{CHRIS}.dev", "executor", f"host={LOCAL} workstation={pi_ws} files=['connection.json']")
    before = _log_lines(gov)
    cases = [
        (["--instance", f"ghost.{CHRIS}.x", "--harness", "claude-code", "--dir", str(gov), "--actor", ADV_OTA], "REDECLARE_NOT_ENROLLED"),
        (["--instance", f"hbj-coder.{CHRIS}.dev", "--harness", "claude-code", "--dir", str(gov), "--actor", ADV_OTA], "REDECLARE_WORKSTATION"),
        (["--instance", ADV_LANTU, "--harness", "claude-code", "--dir", str(gov), "--actor", f"exec.{CHRIS}.x"], "REDECLARE_ACTOR"),
        (["--instance", ADV_LANTU, "--harness", "pi", "--actor", ADV_LANTU], "REDECLARE_INVALID"),
        # AIPOS-F253 件②: 本机 claude-code 不给 --dir 不再拒(缺省 = 治理根 / 治理根内声明子路径; 见 test_aipos_f253_advisor_session_dir)
        # AIPOS-F243 件③: claude-code 可登记在他机, 但目录仍必给(他机缺 --dir = 拒)
        (["--instance", ADV_LANTU, "--harness", "claude-code", "--host", MAC, "--actor", ADV_LANTU], "REDECLARE_INVALID"),
        (["--instance", CHRIS_EXEC, "--harness", "claude-code", "--dir", str(gov), "--actor", ADV_OTA], "REDECLARE_INVALID"),
        (["--instance", ADV_LANTU, "--actor", ADV_LANTU], "REDECLARE_INVALID"),
    ]
    for argv, code in cases:
        rc, out, err = _cli("roles", "redeclare", "--workspace-root", str(gov), *argv, "--confirm")
        _show(f"[件①·拒 {code}] rc={rc} {err.strip()}")
        assert rc == 1 and code in err and "登记未改" in err, (argv, out, err)
    assert _log_lines(gov) == before


def test_item1_keep_top_and_subagent_identity(tmp_path, monkeypatch):
    """改登记写分槽中别的实例 = 顶层(最近接入实例)不变; 子 agent 执行身份改登记只记位置事件(无实例槽), 项目顾问可做。"""
    from tools.aipos_cli.enrollment import instance_enrollment

    gov = _rig(tmp_path, monkeypatch, launch=["pi", "claude-code"])
    top_before = {k: v for k, v in _role_doc(gov).items() if k != "instances"}
    rc, out, err = _cli("roles", "redeclare", "--workspace-root", str(gov), "--instance", ADV_OTA, "--dir", "/Users/kiwi/other",
                        "--actor", ADV_OTA, "--confirm")
    _show(f"---- 件①·只改工作目录(第二档)原文 rc={rc} ----\n{out}{err}")
    assert rc == 0 and "只改工作目录" in out
    doc = _role_doc(gov)
    assert {k: v for k, v in doc.items() if k != "instances"} == top_before, "顶层不随改登记切换"
    assert doc["instances"][ADV_OTA]["harness"] == {"kind": "codex", "dir": "/Users/kiwi/other", "host": MAC}
    rc, out, err = _cli("roles", "redeclare", "--workspace-root", str(gov), "--instance", CHRIS_EXEC, "--harness", "claude-code",
                        "--host", LOCAL, "--actor", ADV_OTA, "--confirm")
    _show(f"---- 件①·子 agent 执行身份改登记原文 rc={rc} ----\n{out}{err}")
    assert rc == 0 and "子 agent 执行身份无实例槽" in out
    view = instance_enrollment(gov, CHRIS_EXEC)
    assert (view["harness"], view["host"], view["mode"], view["governance_root_mode"]) == ("claude-code", LOCAL, "subagent", True)
    assert CHRIS_EXEC not in doc["instances"] and CHRIS_EXEC not in _role_doc(gov).get("instances", {})


# ===========================================================================
# 件② 授权外 = 第一档进 Owner 收件箱; 批准后重跑生效 / 不批准 = 拒
# ===========================================================================

def test_item2_out_of_authorization_owner_inbox_then_apply(tmp_path, monkeypatch):
    from tools.aipos_cli.enrollment import registered_harness
    from tools.aipos_cli.owner_inbox import collect_project_inbox, decide

    gov = _rig(tmp_path, monkeypatch, launch=["pi"])  # 授权拉起方式只有 pi: 换到 claude-code = 授权外
    rc, out, err = _redeclare(gov, "--harness", "claude-code", "--dir", str(gov))
    _show(f"---- 件②·授权外预演原文 rc={rc} ----\n{out}{err}")
    assert rc == 0 and "第一档" in out and "harness:claude-code" in out and "将追加改登记请求" in out
    before = _log_lines(gov)
    rc, out, err = _redeclare(gov, "--harness", "claude-code", "--dir", str(gov), confirm=True)
    _show(f"---- 件②·授权外正式原文 rc={rc} ----\n{out}{err}")
    assert rc == 3 and "追加改登记请求" in out and len(_log_lines(gov)) == len(before) + 1
    assert " redeclare_request " in _log_lines(gov)[-1] and registered_harness(gov, ADV_LANTU)["kind"] == "codex", "未生效"
    rc, out, err = _redeclare(gov, "--harness", "claude-code", "--dir", str(gov), confirm=True)
    assert rc == 3 and "待裁决" in out and len(_log_lines(gov)) == len(before) + 1, "同一请求不重复登记"

    inbox = collect_project_inbox(gov, home_root=gov.parent)
    items = [i for i in inbox["items"] if i["category"] == "enrollment_redeclare"]
    _show("---- 件②·收件箱事项原文 ----\n" + json.dumps({k: items[0][k] for k in ("item_id", "category_label", "title", "ask", "options",
                                                                                     "owner_tier", "owner_reason")}, ensure_ascii=False, indent=1))
    assert len(items) == 1 and items[0]["owner_tier"] == 1 and items[0]["owner_reason"]["key"] == "beyond_authority"
    assert [o["key"] for o in items[0]["options"]] == ["approve_redeclare", "deny_redeclare"] and ADV_LANTU in items[0]["ask"]

    report = decide(items[0]["item_id"], "approve_redeclare", home_root=gov.parent, relayed_by=ADV_OTA, owner_words="岚图换 Claude Code, 批准",
                    confirm=True)
    assert report["ok"], report
    rc, out, err = _redeclare(gov, "--harness", "claude-code", "--dir", str(gov), confirm=True)
    _show(f"---- 件②·Owner 批准后重跑原文 rc={rc} ----\n{out}{err}")
    assert rc == 0 and "Owner 已批准" in out and f"authorized_by=owner:{items[0]['decision_id']}" in _log_lines(gov)[-1]
    assert registered_harness(gov, ADV_LANTU)["kind"] == "claude-code"
    left = [i for i in collect_project_inbox(gov, home_root=gov.parent)["items"] if i["category"] == "enrollment_redeclare"]
    assert left == [], "请求生效后不再列"

    # 授权外主机 + Owner 不批准 = 拒(零写入)
    argv = ("roles", "redeclare", "--workspace-root", str(gov), "--instance", ADV_OTA, "--host", "otherhost", "--actor", ADV_OTA)
    rc, out, err = _cli(*argv, "--confirm")
    assert rc == 3 and "host:otherhost" in out
    item = next(i for i in collect_project_inbox(gov, home_root=gov.parent)["items"] if i["category"] == "enrollment_redeclare")
    assert decide(item["item_id"], "deny_redeclare", home_root=gov.parent, relayed_by=ADV_OTA, owner_words="不换主机", confirm=True)["ok"]
    n = len(_log_lines(gov))
    rc, out, err = _cli(*argv, "--confirm")
    _show(f"[件②·Owner 不批准后重跑] rc={rc} {err.strip()}")
    assert rc == 1 and "REDECLARE_OWNER_DENIED" in err and len(_log_lines(gov)) == n


# ===========================================================================
# 件③ 子 agent 卡 harness 随派发顾问
# ===========================================================================

def _meta(task_id: str, created_by: str, **extra) -> dict:
    meta = {"task_id": task_id, "title": f"{task_id} t", "project": CHRIS, "context_bundle": "t", "task_mode": "code", "priority": "high",
            "status": "pending", "created_by": created_by, "needs_owner": False, "output_target": "app/", "artifact_policy": "formal_write",
            "lane": {"repo": "android-app", "paths": ["app/"]}}
    meta.update(extra)
    return meta


def _claimed_card(gov: Path, task_id: str, created_by: str, harness: str) -> None:
    from tools.aipos_cli.agent_profiles import load_agent_profiles
    from tools.aipos_cli.queue_mutation import mutate_queue_task

    f78._card(gov, task_id, "pending", assigned=CHRIS_EXEC,
              extra={"project": CHRIS, "lane": {"repo": "android-app", "paths": ["app/"]}, "harness": harness, "created_by": created_by})
    claimed = mutate_queue_task(gov, "claim", task_id=task_id, actor=CHRIS_EXEC, dry_run=False, profiles=load_agent_profiles(gov),
                                with_records=True)
    assert claimed.get("moved"), claimed


def _runall_line(gov: Path, card: str) -> tuple[int, str, str]:
    rc, out, err = _cli("kickoff", "--task-id", card, "--workspace-root", str(gov))
    line = next((ln for ln in out.splitlines() if ln.startswith("- 全量命令: ")), "")
    return rc, line, err


def test_item3_two_advisors_drafts_and_kickoff_follow_registration(tmp_path, monkeypatch):
    from tools.aipos_cli.draft_writer import create_draft
    from tools.aipos_cli.enrollment import dispatch_harness

    gov = _rig(tmp_path, monkeypatch, launch=["pi", "claude-code"])
    assert _redeclare(gov, "--harness", "claude-code", "--dir", str(gov), confirm=True)[0] == 0
    got = {}
    for card, adv in (("HBX-201", ADV_OTA), ("HBX-202", ADV_LANTU)):
        created = create_draft(gov, _meta(card, adv), "## Goal\n\nx\n")
        fm = parse_markdown_frontmatter((gov / created["target_path"]).read_text(encoding="utf-8"))[0]
        view = dispatch_harness(gov, advisor=adv, executor=CHRIS_EXEC)
        got[card] = fm["harness"]
        _show(f"[件③·{adv} 起草 {card}] harness={fm['harness']} assigned_to={fm['assigned_to']} 来源={view['source']} "
              f"(共享执行身份 land harness={view['executor_harness']})")
    assert got == {"HBX-201": "codex", "HBX-202": "claude-code"}
    # 派发顾问无登记 = 兜底共享执行身份 land 事件 harness(行为同修前)
    assert dispatch_harness(gov, advisor=f"advisor.{CHRIS}.none", executor=CHRIS_EXEC)["harness"] == "codex"

    lines = {}
    for card, adv in (("HBX-211", ADV_OTA), ("HBX-212", ADV_LANTU)):
        _claimed_card(gov, card, adv, got["HBX-201" if adv == ADV_OTA else "HBX-202"])
        rc, line, err = _runall_line(gov, card)
        _show(f"---- 件③·{adv} 派发卡 {card} 开工稿全量句原文 rc={rc} ----\n{line}{err}")
        assert rc == 0, err
        lines[adv] = line
    assert "本卡 harness codex 未声明单次工具时限" in lines[ADV_OTA]
    assert "超过本卡 harness claude-code 的单次工具时限 600 秒" in lines[ADV_LANTU]


def test_item3_mismatch_next_and_kickoff_give_amend_exit(tmp_path, monkeypatch):
    gov = _rig(tmp_path, monkeypatch, launch=["pi", "claude-code"])
    assert _redeclare(gov, "--harness", "claude-code", "--dir", str(gov), confirm=True)[0] == 0
    # 已发布未认领: 卡面 codex(换 harness 前起草)≠ 派发顾问现登记 claude-code → next 不派生认领, 给 amend 出口原文
    f78._card(gov, "HBX-221", "pending", assigned=CHRIS_EXEC,
              extra={"project": CHRIS, "lane": {"repo": "android-app", "paths": ["app/"]}, "harness": "codex", "created_by": ADV_LANTU})
    rc, out, err = _cli("next", "--task-id", "HBX-221", "--workspace-root", str(gov), "--json")
    res = json.loads(out)
    _show(f"---- 件③·next 不符原文 ----\n{json.dumps({k: res[k] for k in ('derivable', 'suggested_action', 'notes')}, ensure_ascii=False, indent=1)}")
    assert res["derivable"] is False and res["action"]["type"] == "harness_mismatch"
    assert "queue amend --task-id HBX-221" in res["suggested_action"] and '{"harness": "claude-code"}' in res["suggested_action"]
    assert "--restricted" not in res["suggested_action"]
    amend = res["suggested_action"].split("lybra ", 1)[1]
    import shlex
    rc, out, err = _cli(*shlex.split(amend))
    _show(f"[件③·按出口 amend(未认领)] rc={rc} {out.strip()[:300]} {err.strip()[:300]}")
    assert rc == 0, err
    rc, out, err = _cli("next", "--task-id", "HBX-221", "--workspace-root", str(gov), "--json")
    assert json.loads(out).get("action", {}).get("type") != "harness_mismatch"

    # 已认领: kickoff 拒 KICKOFF_HARNESS_MISMATCH, 出口 = 受限 amend(--restricted); 照做后开工稿按新 harness 出
    _claimed_card(gov, "HBX-222", ADV_LANTU, "codex")
    rc, line, err = _runall_line(gov, "HBX-222")
    _show(f"---- 件③·kickoff 不符原文 rc={rc} ----\n{err}")
    assert rc != 0 and "KICKOFF_HARNESS_MISMATCH" in err and "--restricted" in err
    amend = re.search(r"出口: lybra (.+?) —— 改后重出开工稿", err).group(1)
    rc, out, err2 = _cli(*shlex.split(amend))
    _show(f"[件③·按出口受限 amend(已认领)] rc={rc} {out.strip()[:300]} {err2.strip()[:300]}")
    assert rc == 0, err2
    rc, line, err = _runall_line(gov, "HBX-222")
    _show(f"[件③·受限 amend 后开工稿全量句] rc={rc} {line}")
    assert rc == 0 and "claude-code 的单次工具时限 600 秒" in line


# ===========================================================================
# 件④ 看板工位页 / workstation-plan 显示登记
# ===========================================================================

def test_item4_workstation_plan_shows_registration(tmp_path, monkeypatch):
    from tools.aipos_cli.workstation_plan import build_plan, status_rows, texts

    gov = _rig(tmp_path, monkeypatch, launch=["pi", "claude-code"])
    assert _redeclare(gov, "--harness", "claude-code", "--dir", str(gov), confirm=True)[0] == 0
    plan = build_plan(gov, project=CHRIS, home_root=gov.parent)
    rows = dict(status_rows(plan, texts("zh")))
    _show(f"[件④·workstation-plan 现状] 子 agent: {rows['row_subagent']}\n  顾问登记: {rows['row_advisors']}")
    assert f"登记 codex@{MAC}(redeclare" not in rows["row_advisors"]
    assert f"{ADV_LANTU}(已接入) 登记 claude-code@{LOCAL}:{gov}(redeclare@" in rows["row_advisors"]
    assert f"{ADV_OTA}(已接入) 登记 codex@{MAC}:{MAC_DIR}({gov / '.lybra' / 'role'})" in rows["row_advisors"]
    assert f"登记 codex@{MAC}(land@" in rows["row_subagent"]
    rc, out, err = _cli("project", "workstation-plan", "--workspace-root", str(gov))
    _show(f"---- 件④·lybra project workstation-plan 原文 rc={rc} ----\n{out}{err}")
    assert rc == 0 and "顾问登记" in out and f"claude-code@{LOCAL}" in out
    import html

    from web.board.readonly_pages import render_workstation

    page = render_workstation(gov.parent, {"project": [gov.name]})
    row = re.search(r'<tr class="ws-row" data-row="row_advisors">.*?</tr>', page, re.S)
    _show(f"[件④·看板工位页顾问登记行原文] {row.group(0) if row else page[:800]}")
    assert row and f"登记 claude-code@{LOCAL}:{gov}(redeclare@" in html.unescape(row.group(0))
    sub = re.search(r'<tr class="ws-row[^"]*" data-row="subagent".*?</tr>', page, re.S)
    assert sub and f"登记 codex@{MAC}(land@" in html.unescape(sub.group(0))
