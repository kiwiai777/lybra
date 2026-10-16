"""AIPOS-F167 — 产品内更新通知: 卡声明顾问须知 → 部署时汇总更新说明 → 顾问开局 / loop status 自动看到未读更新(Owner 不再转述)。

验收(卡面 ★验收):
 ① 临时根: 带 advisor_notice 的卡 finalize --deploy 合入部署后生成更新说明(原文); 回填清单随同一口汇总; 去重幂等; 已部署卡重跑补汇总。
 ② 两顾问实例: charter / loop status / next / brief 显示未读原文; A ack 后 A 不再显示而 B 仍显示原文。
 ③ 缺 advisor_notice 的行为变更卡出卡警告原文(不拒); 形不合 = 拒; 其他项目业务仓的卡不警告。
 附: 库定位(部署快照 / --dev-tree 读, 卡工作树不读)、读不出 = 一行告警不掀翻原命令、声明单源与合并后预载闭包、回填清单形。

靶场一律临时目录: 临时 HOME / `lybra project new` 同一脚手架建的临时治理根(git 仓 + 临时裸仓作 origin, 供落账推送)/ 临时产品仓 +
假部署目录(布局同 tools/lybra-deploy)。不碰真实治理根、真实 .deploy、生产门与 pi。卡号形状用靶场自造声明(不写死项目卡号前缀)。
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_aipos_f131_loop_run_status import _cli  # noqa: E402  — 进程内 CLI 读法唯一来源
from test_aipos_f130_finalize_unlock import EXEC as FZ_EXEC  # noqa: E402
from test_aipos_f130_finalize_unlock import _card_branch, _gov as _fz_gov, _product, _verdict  # noqa: E402  — finalize 靶场唯一来源
from tools.aipos_cli import advisor_updates as au  # noqa: E402
from tools.aipos_cli import deploy_runtime as dr  # noqa: E402
from tools.aipos_cli.verb_contract import declared_exit_code  # noqa: E402

PROJECT = "f167probe"
ADV_A = f"advisor.{PROJECT}.hosta"
OTA_ROLE = "advisor-ota"
ADV_B = f"{OTA_ROLE}.{PROJECT}.hostb"
NOTICE = {"audience": ["advisor"], "summary": "靶场须知: 新增 lybra probe-cmd, 顾问开局先跑它",
          "actions": ["开局跑 lybra probe-cmd --workspace-root <治理根>", "读完继续推进"]}


def _show(text: str) -> None:
    print(text, flush=True)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True).stdout.strip()


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _card(gov: Path, task_id: str, state: str, extra: dict | None = None) -> Path:
    from tools.aipos_cli.record_writer import render_markdown

    meta = {"task_id": task_id, "title": f"{task_id} 靶场卡", "status": state, **(extra or {})}
    path = gov / "5_tasks" / "queue" / state / f"{task_id.lower()}.md"
    _write(path, render_markdown(meta, f"# {task_id}\n"))
    return path


def _fake_deploy(product: Path, commit: str, release: str = "r-a") -> Path:
    """假部署目录(布局同 tools/lybra-deploy): <产品仓>/.deploy/releases/<名>/VERSION + current → 该快照。返回快照目录。"""
    rel = product / dr.DEPLOY_DIRNAME / dr.RELEASES_DIRNAME / release
    _write(rel / dr.VERSION_FILENAME, f"release: {release}\ngit_commit: {commit}\n")
    link = product / dr.DEPLOY_DIRNAME / dr.CURRENT_LINKNAME
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to(rel)
    return rel


@pytest.fixture()
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """临时治理根(脚手架 + 两个顾问实例接入 + 临时 origin)+ 临时产品仓与假部署; 本进程运行代码伪装为该部署快照(库定位走真实判定)。"""
    from types import SimpleNamespace

    from tools.aipos_cli.custom_roles import register_custom_role
    from tools.aipos_cli.enroll_client import write_role_file
    from tools.aipos_cli.enrollment import _append_enrollment_trail
    from tools.aipos_cli.workspace_config import scaffold_project

    for var in ("LYBRA_CONNECTION_JSON", "AIPOS_WORKSPACE_ROOT", "LYBRA_WORKSPACE_ROOT", "LYBRA_HOME_ROOT"):
        monkeypatch.delenv(var, raising=False)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    product = tmp_path / "product"
    product.mkdir()
    _git(product, "init", "-q", "-b", "main")
    gov = scaffold_project(tmp_path / "hroot", PROJECT, code_repo=product, registered_by="owner")
    (gov / ".lybra").mkdir(exist_ok=True)
    _write(gov / ".lybra" / "connection.json", json.dumps({
        "config_version": 1, "governance_root": str(gov), "mcp": {"rpc_url": "http://127.0.0.1:1/mcp"},
        "tokens": [{"role": "advisor", "role_class": "advisor", "agent_instance": ADV_A, "token": "fixture-f167-a-not-a-secret",
                    "token_ref": "fixture-a", "scopes": []}]}, indent=2))
    session = tmp_path / "session"
    session.mkdir()
    (gov / "governance" / "decision_log").mkdir(parents=True, exist_ok=True)  # brief 读决策日志目录(脚手架未建)
    register_custom_role(gov, OTA_ROLE, "advisor", by="owner", reason="F167 夹具: 第二个顾问角色")
    for inst, role, harness in ((ADV_A, "advisor", {"kind": "claude-code", "dir": str(session)}),
                                (ADV_B, OTA_ROLE, {"kind": "codex", "dir": None, "host": "hostb"})):
        write_role_file(gov / ".lybra", role, inst, None, harness=harness, slot_by_instance=True)
        _append_enrollment_trail(gov, action="land", code_id=f"enroll_f167_{role}", role=role, instance=inst, by="(agent-enroll)",
                                 reason=f"host={socket.gethostname()} workstation={gov} files=['connection.json', 'role']")
    _git(gov, "init", "-q", "-b", "main")
    _git(gov, "config", "user.email", "f167@example.invalid")
    _git(gov, "config", "user.name", "f167")
    _write(gov / ".gitignore", ".lybra/\n")
    _git(gov, "add", "-A")
    _git(gov, "commit", "-q", "-m", "scaffold")
    origin = tmp_path / "gov-origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True, capture_output=True)
    _git(gov, "remote", "add", "origin", str(origin))
    _git(gov, "push", "-q", "-u", "origin", "main")
    snapshot = _fake_deploy(product, "a" * 40)
    monkeypatch.setattr(dr, "_code_root", lambda: snapshot)  # 本进程「运行代码」= 该部署快照(running_code 照真实判据判 deploy)
    return SimpleNamespace(gov=gov, product=product, snapshot=snapshot, store=au.store_dir_for_product_repo(product), tmp=tmp_path)


def _banner_present(text: str) -> bool:
    """提示行(行首「Lybra 有 N 条未读更新(自 <版本>)」); 章程正文里提及该提示的守则文字不算。"""
    return any(ln.startswith("Lybra 有 ") and "条未读更新(自 " in ln for ln in text.splitlines())


# ===========================================================================
# ① 部署后汇总(finalize --deploy 唯一挂点)+ 回填 + 去重 + 补汇总
# ===========================================================================

def test_acceptance1_finalize_deploy_compiles_update_note(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from tools.aipos_cli import finalize as fz

    gov = _fz_gov(tmp_path / "gov")
    monkeypatch.setenv("LYBRA_WORKSPACE_ROOT", str(gov))
    repo = tmp_path / "product"
    _product(repo, origin=tmp_path / "origin.git")
    # 产品仓带回填清单(真实清单原样复制; 去重键 task_id)
    _write(repo / "docs" / "advisor_notice_backfill.json", (REPO_ROOT / "docs" / "advisor_notice_backfill.json").read_text(encoding="utf-8"))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "backfill list")
    _git(repo, "push", "-q")
    first = "RNG-167"
    tip = _card_branch(repo, first)
    _verdict(gov, first, tip)
    _card(gov, first, "claimed", {"claimed_by": FZ_EXEC, "advisor_notice": NOTICE})

    res = fz.finalize_task(first, FZ_EXEC, repo, governance_root=gov, push=True, deploy=True)
    _show("---- ① finalize --push --deploy 操作行原文 ----\n  " + "\n  ".join(res["operations"]))
    assert res["verdict"] == "PASS", res["message"]
    line = next(op for op in res["operations"] if "更新说明已汇总" in op)
    store = au.store_dir_for_product_repo(repo)
    files = sorted(store.glob("update_*.json"))
    assert len(files) == 1 and str(files[0]) in line
    record = json.loads(files[0].read_text(encoding="utf-8"))
    _show(f"---- ① 更新说明原文 {files[0]} ----\n{files[0].read_text(encoding='utf-8')}")
    merge = _git(repo, "rev-parse", "HEAD")
    assert record["record_type"] == "advisor_update" and record["deploy_commit"] == merge
    assert record["release"] == Path(os.path.realpath(repo / ".deploy" / "current")).name
    assert record["source_task_id"] == first and record["update_id"] == files[0].stem
    ids = [e["task_id"] for e in record["entries"]]
    backfill = json.loads((REPO_ROOT / "docs" / "advisor_notice_backfill.json").read_text(encoding="utf-8"))["entries"]
    assert ids == [first] + [b["task_id"] for b in backfill]
    own = record["entries"][0]
    assert own == {"task_id": first, **NOTICE, "source": "card"}
    assert all(e["source"] == "backfill" for e in record["entries"][1:])
    assert not (gov / ".deploy").exists()  # 库在产品侧部署目录, 不在治理根

    # 同卡重跑 finalize --deploy(已部署 = 续做判定无事可做)→ 经同一挂点补汇总: 无新条目, 不重复写
    again = fz.finalize_task(first, FZ_EXEC, repo, governance_root=gov, push=True, deploy=True)
    _show("---- ① 已部署卡重跑(补汇总口)操作行 ----\n  " + "\n  ".join(o for o in again["operations"] if "更新说明" in o))
    assert any("更新说明: 无新条目" in o and "已汇总过" in o for o in again["operations"])
    assert len(sorted(store.glob("update_*.json"))) == 1

    # 下一张卡无 advisor_notice: 回填已汇总过 → 不生成(操作行如实说明)
    second = "RNG-168"
    tip2 = _card_branch(repo, second)
    _verdict(gov, second, tip2, stamp="20261008_020000")
    _card(gov, second, "claimed", {"claimed_by": FZ_EXEC})
    res2 = fz.finalize_task(second, FZ_EXEC, repo, governance_root=gov, push=True, deploy=True)
    _show("---- ① 第二张卡(无须知)操作行 ----\n  " + "\n  ".join(o for o in res2["operations"] if "更新说明" in o))
    assert res2["verdict"] == "PASS", res2["message"]
    assert any(o.startswith("更新说明: 无新条目(本卡卡面无 advisor_notice") for o in res2["operations"])
    assert len(sorted(store.glob("update_*.json"))) == 1


def test_item2_compile_failure_does_not_undo_deploy_and_names_the_exit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    from tools.aipos_cli import finalize as fz

    gov = _fz_gov(tmp_path / "gov")
    monkeypatch.setenv("LYBRA_WORKSPACE_ROOT", str(gov))
    repo = tmp_path / "product"
    _product(repo, origin=tmp_path / "origin.git")
    task = "RNG-169"
    tip = _card_branch(repo, task)
    _verdict(gov, task, tip)
    _card(gov, task, "claimed", {"claimed_by": FZ_EXEC, "advisor_notice": {"audience": ["nobody"], "summary": "x"}})
    res = fz.finalize_task(task, FZ_EXEC, repo, governance_root=gov, push=True, deploy=True)
    line = next(op for op in res["operations"] if "更新说明汇总失败" in op)
    _show(f"---- ② 汇总失败(卡面须知形不合)操作行原文 ----\n  {line}")
    assert res["verdict"] == "PASS" and res["deployed"] is True  # 合并 / 部署照常, 不回滚
    assert "AdvisorNoticeError" in line and "audience" in line and "重跑同一条 finalize --deploy" in line
    assert "更新说明汇总失败" in capsys.readouterr().err  # 出声
    assert not au.store_dir_for_product_repo(repo).exists()


# ===========================================================================
# ② 两顾问实例: 四出口显示未读原文; A ack 后 A 不再显示, B 仍显示
# ===========================================================================

def test_acceptance2_two_advisors_banner_then_ack_is_per_instance(rig):
    gov = rig.gov
    _card(gov, "F167PROBE-1", "completed", {"advisor_notice": NOTICE})
    compiled = au.compile_on_deploy(rig.product, gov, "F167PROBE-1")
    _show(f"---- ② 靶场库: {compiled['line']} ----")
    assert compiled["written"] and Path(compiled["written"]).parent == rig.store

    outputs: dict[tuple[str, str], str] = {}
    for inst in (ADV_A, ADV_B):
        rc, out, err = _cli(["charter", "--role", "advisor", "--instance", inst, "--workspace-root", str(gov)])
        assert rc == 0, err
        outputs[(inst, "charter")] = out
        rc, out, err = _cli(["loop", "status", "--workspace-root", str(gov), "--actor", inst])
        assert rc == declared_exit_code("lybra_loop_status", "ok"), err
        outputs[(inst, "loop status")] = out
        rc, out, err = _cli(["next", "--workspace-root", str(gov), "--actor", inst])
        assert rc == 0, err
        outputs[(inst, "next")] = out
        rc, out, err = _cli(["brief", "--workspace-root", str(gov), "--actor", inst])
        assert rc == 0, err
        outputs[(inst, "brief")] = out
    for (inst, cmd), out in outputs.items():
        lines = [ln for ln in out.splitlines() if ln.startswith("Lybra 有 ") or "看全文:" in ln]
        _show(f"---- ② ack 前 `{cmd}` ({inst}) 提示原文 ----\n" + "\n".join(lines))
        assert _banner_present(out), (inst, cmd)
        assert "Lybra 有 1 条未读更新(自 r-a(aaaaaaaaaaaa)): [F167PROBE-1] 靶场须知: 新增 lybra probe-cmd, 顾问开局先跑它; 读完后继续推进, 不必等 Owner" in out
        assert f"lybra updates --ack --workspace-root {gov} --actor {inst} --confirm" in out
    charter_a = outputs[(ADV_A, "charter")]
    assert charter_a.startswith("Lybra 有 1 条未读更新")  # 章程开头
    for cmd in ("loop status", "next", "brief"):  # 末尾(brief 在收尾分隔线之前)
        tail = [ln for ln in outputs[(ADV_A, cmd)].splitlines() if ln.strip() and not set(ln.strip()) <= {"="}]
        assert "看全文:" in tail[-1], (cmd, tail[-3:])

    # 全文 + 预演 ack(零写入)+ 确认 ack(写记录并落账)
    rc, out, err = _cli(["updates", "--workspace-root", str(gov), "--actor", ADV_A])
    _show(f"---- ② `lybra updates --actor {ADV_A}` 原文 rc={rc} ----\n{out}{err}")
    assert rc == 0 and "顾问动作 1. 开局跑 lybra probe-cmd --workspace-root <治理根>" in out and "本实例未读 1 份" in out
    acks = gov / "5_tasks" / "records" / "advisor_update_acks"
    rc, out, err = _cli(["updates", "--ack", "--workspace-root", str(gov), "--actor", ADV_A])
    _show(f"---- ② `lybra updates --ack`(预演)原文 rc={rc} ----\n{out}{err}")
    assert rc == 0 and "预览(未写; 加 --confirm 记已读并落账)" in out and not acks.exists()
    rc, out, err = _cli(["updates", "--ack", "--workspace-root", str(gov), "--actor", ADV_A, "--confirm"])
    _show(f"---- ② `lybra updates --ack --confirm` 原文 rc={rc} ----\n{out}{err}")
    assert rc == 0 and "已记已读" in out and "落账: verdict=PASS" in out
    written = sorted((acks / ADV_A).glob("ack_*.md"))
    assert len(written) == 1
    text = written[0].read_text(encoding="utf-8")
    _show(f"---- ② 已读标记记录原文 {written[0].relative_to(gov)} ----\n{text}")
    assert "record_type: advisor_update_ack" in text and compiled["update_id"] in text
    assert _git(gov, "status", "--porcelain", "--", "5_tasks/records") == ""  # 已读标记已提交(靶场卡文件本就未入库, 不在此列)
    assert _git(gov, "rev-parse", "HEAD") == _git(gov, "rev-parse", "origin/main")
    assert _git(gov, "show", "--name-only", "--format=%s", "HEAD").splitlines() == [
        f"chore(governance): Lybra 更新已读标记 {ADV_A} 1 份更新说明", "", written[0].relative_to(gov).as_posix()]

    for cmd_argv, name in ((["charter", "--role", "advisor", "--instance", "{i}", "--workspace-root", str(gov)], "charter"),
                           (["loop", "status", "--workspace-root", str(gov), "--actor", "{i}"], "loop status"),
                           (["next", "--workspace-root", str(gov), "--actor", "{i}"], "next"),
                           (["brief", "--workspace-root", str(gov), "--actor", "{i}"], "brief")):
        _rc_a, out_a, _ = _cli([a.format(i=ADV_A) for a in cmd_argv])
        _rc_b, out_b, _ = _cli([a.format(i=ADV_B) for a in cmd_argv])
        _show(f"---- ② ack 后 `{name}`: A 提示行 {[ln for ln in out_a.splitlines() if ln.startswith('Lybra 有')]} / "
              f"B 提示行 {[ln for ln in out_b.splitlines() if ln.startswith('Lybra 有')]} ----")
        assert not _banner_present(out_a), name
        assert _banner_present(out_b), name
    # 重复 ack = 无未读(未写)
    rc, out, _ = _cli(["updates", "--ack", "--workspace-root", str(gov), "--actor", ADV_A, "--confirm"])
    assert rc == 0 and "无未读(未写)" in out and len(sorted((acks / ADV_A).glob("ack_*.md"))) == 1


def test_item3_ack_refusals_and_usage(rig):
    gov = rig.gov
    rc, out, err = _cli(["updates", "--ack", "--workspace-root", str(gov)])
    _show(f"---- ③ --ack 缺 --actor rc={rc} ----\n{err}")
    assert rc == declared_exit_code("lybra_updates", "refused") and "须给 --actor" in err
    ghost = f"advisor.{PROJECT}.nowhere"
    rc, out, err = _cli(["updates", "--ack", "--workspace-root", str(gov), "--actor", ghost, "--confirm"])
    _show(f"---- ③ --ack 未接入实例 rc={rc} ----\n{err}")
    assert rc == declared_exit_code("lybra_updates", "refused") and "未在本治理根接入" in err
    rc, _out, err = _cli(["updates", "--confirm", "--workspace-root", str(gov)])
    assert rc == declared_exit_code("lybra_updates", "usage") and "只与 --ack 同用" in err
    assert not (gov / "5_tasks" / "records" / "advisor_update_acks").exists()


def test_item3_unreadable_store_is_one_loud_line_not_a_crash(rig):
    rig.store.mkdir(parents=True)
    _write(rig.store / "update_20990101T000000_bad.json", "{not json")
    rc, out, err = _cli(["loop", "status", "--workspace-root", str(rig.gov), "--actor", ADV_A])
    line = next(ln for ln in out.splitlines() if "更新说明读不出" in ln)
    _show(f"---- ③ 库读不出: loop status rc={rc}, 告警行原文 ----\n{line}")
    assert rc == declared_exit_code("lybra_loop_status", "ok") and "JSONDecodeError" in line
    rc, _out, err = _cli(["updates", "--workspace-root", str(rig.gov), "--actor", ADV_A])
    assert rc == declared_exit_code("lybra_updates", "unreadable") and "读不出" in err


def test_item3_store_location_by_running_mode(rig, tmp_path: Path):
    """部署快照 = 其部署目录下的库; 卡工作树(python -m / 测试)= 不可用; --dev-tree = 所属产品仓主检出部署目录(与 lybra-deploy 同推导)。"""
    deploy = au.store_dir_for_running_code(dr.running_code(code_root=rig.snapshot))
    work = au.store_dir_for_running_code(dr.RunningCode(REPO_ROOT, "worktree"))
    dev = au.store_dir_for_running_code(dr.RunningCode(REPO_ROOT, "dev"))
    _show(f"---- 库定位 ----\n  deploy: {deploy}\n  worktree: {work}\n  dev: {dev}")
    assert deploy["available"] and deploy["store"] == rig.product / ".deploy" / "advisor_updates"
    assert not work["available"] and work["store"] is None and "mode=worktree" in work["reason"]
    assert dev["available"] and dev["store"] == dr.default_deploy_current(REPO_ROOT).parent / "advisor_updates"
    # 卡工作树里跑(本进程未伪装)= 提示不显示、updates 如实说明
    rc = subprocess.run([sys.executable, "-m", "tools.aipos_cli", "updates", "--workspace-root", str(rig.gov), "--actor", ADV_A],
                        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=300,
                        env={**{k: v for k, v in os.environ.items() if not k.startswith(("LYBRA_", "AIPOS_"))},
                             "HOME": str(tmp_path / "home")})
    _show(f"---- 卡工作树 `python -m tools.aipos_cli updates` rc={rc.returncode} ----\n{rc.stdout}{rc.stderr}")
    assert rc.returncode == 0 and "更新说明库不可用" in rc.stdout and "mode=worktree" in rc.stdout


# ===========================================================================
# ③ 出卡: 行为变更卡缺须知 = 警告(不拒); 形不合 = 拒; 其他项目业务仓不警告
# ===========================================================================

def _draft_payload(path: Path, task_id: str, *, notice=None, paths=("tools/aipos_cli/",)) -> Path:
    fm = {"task_id": task_id, "title": f"{task_id} 靶场", "context_bundle": "t", "task_mode": "code", "priority": "low",
          "assigned_to": "exec.f167draft.host", "agent_instance": "exec.f167draft.host",
          "status": "pending", "needs_owner": False, "output_target": ", ".join(paths), "artifact_policy": "formal_write",
          "lane": {"paths": list(paths), "roles": ["executor"]}}
    if notice is not None:
        fm["advisor_notice"] = notice
    _write(path, json.dumps({"frontmatter": fm, "body": f"# {task_id}\n\n## Goal\n靶场。\n"}, ensure_ascii=False))
    return path


@pytest.fixture()
def lybra_like(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """治理根声明的产品仓 = 运行代码所在产品仓(只作路径引用, 不写它): 改其 CLI / schema 的卡 = 行为变更卡。"""
    from tools.aipos_cli.workspace_config import scaffold_project

    for var in ("LYBRA_CONNECTION_JSON", "AIPOS_WORKSPACE_ROOT", "LYBRA_WORKSPACE_ROOT", "LYBRA_HOME_ROOT"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()

    def make(name: str, code_repo: Path) -> Path:
        gov = scaffold_project(tmp_path / f"hroot-{name}", name, code_repo=code_repo, registered_by="owner")
        (gov / ".lybra").mkdir(exist_ok=True)
        _write(gov / ".lybra" / "connection.json", json.dumps({
            "config_version": 1, "governance_root": str(gov), "mcp": {"rpc_url": "http://127.0.0.1:1/mcp"},
            "tokens": [{"role": "advisor", "role_class": "advisor", "agent_instance": f"advisor.{name}.host",
                        "token": "fixture-f167-draft-not-a-secret", "token_ref": "fixture-d", "scopes": []}]}))
        return gov

    return make


def test_acceptance3_draft_warns_on_behavior_change_card_without_notice(lybra_like, tmp_path: Path):
    gov = lybra_like("f167lybra", au.product_repo_root())
    actor = "advisor.f167lybra.host"
    before = subprocess.run(["git", "-C", str(au.product_repo_root()), "status", "--porcelain"], capture_output=True, text=True).stdout

    rc, out, err = _cli(["--workspace-root", str(gov), "draft", "create", "--from-json",
                         str(_draft_payload(tmp_path / "d1.json", "F167L-1")), "--actor", actor])
    _show(f"---- ③ 缺 advisor_notice 的行为变更卡 draft create rc={rc} 原文 ----\n{out}{err}")
    assert rc == 0, out + err  # 警告不拒
    assert "ADVISOR_NOTICE_MISSING: 本卡改动 Lybra 产品的顾问/审计/执行可见面(tools/aipos_cli/)" in out
    assert (gov / "5_tasks" / "drafts" / "f167l-1.md").is_file()
    rc, out, err = _cli(["--workspace-root", str(gov), "draft", "publish", "--path", "5_tasks/drafts/f167l-1.md", "--actor", actor,
                         "--dry-run"])
    _show(f"---- ③ draft publish --dry-run 同一警告 rc={rc} ----\n" + "\n".join(l for l in (out + err).splitlines() if "ADVISOR_NOTICE" in l))
    assert "ADVISOR_NOTICE_MISSING" in out + err

    rc, out, err = _cli(["--workspace-root", str(gov), "draft", "create", "--from-json",
                         str(_draft_payload(tmp_path / "d2.json", "F167L-2", notice=NOTICE)), "--actor", actor])
    assert rc == 0 and "ADVISOR_NOTICE" not in out, out + err
    card = (gov / "5_tasks" / "drafts" / "f167l-2.md").read_text(encoding="utf-8")
    _show("---- ③ 带须知卡稿 frontmatter 片段 ----\n" + card[card.index("advisor_notice"):card.index("advisor_notice") + 260])
    from tools.aipos_cli.frontmatter import parse_markdown_frontmatter

    assert parse_markdown_frontmatter(card)[0]["advisor_notice"] == NOTICE

    rc, out, err = _cli(["--workspace-root", str(gov), "draft", "create", "--from-json",
                         str(_draft_payload(tmp_path / "d3.json", "F167L-3", notice={"audience": ["advisor"], "summary": "两行\n要点"})),
                         "--actor", actor])
    _show(f"---- ③ 须知形不合 rc={rc} ----\n" + "\n".join(l for l in (out + err).splitlines() if "ADVISOR_NOTICE" in l))
    assert rc == 1 and "ADVISOR_NOTICE_INVALID: summary 须为非空单行串" in out
    assert not (gov / "5_tasks" / "drafts" / "f167l-3.md").exists()

    rc, out, err = _cli(["--workspace-root", str(gov), "draft", "create", "--from-json",
                         str(_draft_payload(tmp_path / "d4.json", "F167L-4", paths=("docs/",))), "--actor", actor])
    assert rc == 0 and "ADVISOR_NOTICE" not in out, out  # 只动文档的卡不算行为变更面
    after = subprocess.run(["git", "-C", str(au.product_repo_root()), "status", "--porcelain"], capture_output=True, text=True).stdout
    assert after == before  # 产品仓只作路径引用, 未被写


def test_item3_other_project_business_repo_never_warns(lybra_like, tmp_path: Path):
    biz = tmp_path / "biz"
    biz.mkdir()
    _git(biz, "init", "-q", "-b", "main")
    gov = lybra_like("f167biz", biz)
    rc, out, err = _cli(["--workspace-root", str(gov), "draft", "create", "--from-json",
                         str(_draft_payload(tmp_path / "b1.json", "F167B-1", paths=("tools/aipos_cli/", "schema/"))),
                         "--actor", "advisor.f167biz.host"])
    _show(f"---- ③ 其他项目业务仓同名路径 rc={rc}: ADVISOR_NOTICE 行 = "
          f"{[l for l in out.splitlines() if 'ADVISOR_NOTICE' in l]} ----")
    assert rc == 0 and "ADVISOR_NOTICE" not in out


def test_item1_parse_notice_shapes():
    ok = au.parse_advisor_notice({"audience": "advisor", "summary": " 要点 ", "actions": []})
    assert ok == {"audience": ["advisor"], "summary": "要点", "actions": []}
    for bad, needle in (([], "须为对象"), ({"audience": [], "summary": "x"}, "audience"), ({"audience": ["owner"], "summary": "x"}, "audience"),
                        ({"audience": ["advisor"], "summary": ""}, "summary"), ({"audience": ["advisor"], "summary": "x", "actions": "a"}, "actions"),
                        ({"audience": ["advisor"], "summary": "x", "actions": [""]}, r"actions\[0\]"),
                        ({"audience": ["advisor"], "summary": "x", "extra": 1}, "未声明的键")):
        with pytest.raises(au.AdvisorNoticeError, match=needle):
            au.parse_advisor_notice(bad)


# ===========================================================================
# 声明单源 / 回填清单 / 合并后预载闭包 / 章程守则
# ===========================================================================

def test_declarations_single_source_and_backfill_list():
    from tools.aipos_cli.aipos_cli import _two_phase_family_declaration, build_parser
    from tools.aipos_cli.governance_commit import post_write_landing_declaration
    from tools.aipos_cli.record_writer import record_kind_for_type
    from tools.schema_loader import get_enum_values, load_schema

    decl = au.updates_declaration()
    notice = au.notice_declaration()
    roles = {str(r.get("role_class") or r.get("role")) for r in load_schema("roles")["roles"]}
    assert set(notice["audiences"]) <= roles and decl["banner"]["audience"] in notice["audiences"]
    assert decl["exit_codes"]["landing_failed"]["code"] == post_write_landing_declaration()["landing_failed_exit_code"]
    assert decl["ack"]["landing_command"] in post_write_landing_declaration()["commands"]
    assert _two_phase_family_declaration("advisor_update_writers")["commands"] == ["updates"]
    assert record_kind_for_type(decl["ack"]["record_type"]) == decl["ack"]["record_kind"]
    enum = set(get_enum_values("record_type"))
    assert {decl["ack"]["record_type"], decl["store"]["record_type"]} <= enum
    assert "advisor_notice" in load_schema("card")["advisor_zone"]["fields"]
    assert "updates" in load_schema("verbs")["governance_root_resolution"]["commands"]
    sub = next(a for a in build_parser()._actions if getattr(a, "choices", None) and "updates" in a.choices).choices["updates"]
    flags = {o for a in sub._actions for o in a.option_strings if o.startswith("--")} - {"--help"}
    assert flags == {"--" + k.replace("_", "-") for k in decl["parameters"]["properties"]}, flags

    backfill = json.loads((REPO_ROOT / decl["compile"]["backfill_source"]).read_text(encoding="utf-8"))
    ids = [e["task_id"] for e in backfill["entries"]]
    _show(f"---- ④ 回填清单 {decl['compile']['backfill_source']}: {ids} ----")
    assert ids == [f"AIPOS-F{n}" for n in range(160, 168)] and len(set(ids)) == len(ids)
    for entry in backfill["entries"]:
        parsed = au.parse_advisor_notice({k: v for k, v in entry.items() if k != "task_id"})
        assert "advisor" in parsed["audience"] and parsed["actions"], entry["task_id"]


def test_post_merge_closure_and_single_hook():
    from tools.aipos_cli import finalize as fz

    closure = fz._post_merge_import_closure()
    assert "tools.aipos_cli.advisor_updates" in closure and "tools.aipos_cli.deploy_runtime" in closure
    assert "tools.aipos_cli.advisor_updates_cli" not in closure  # 送达面(章程 / 接入 / 落账)不拖进合并后预载
    src = (REPO_ROOT / "tools" / "aipos_cli" / "finalize.py").read_text(encoding="utf-8")
    assert src.count("compile_on_deploy(") == 1 and src.count("def _compile_advisor_updates(") == 1
    tools_src = "\n".join(p.read_text(encoding="utf-8") for p in (REPO_ROOT / "tools").rglob("*.py")
                          if "tests" not in p.parts and p.name != "advisor_updates.py" and not p.name.startswith("test_"))
    assert tools_src.count("compile_on_deploy(") == 1  # 汇总只在部署唯一口(finalize)


def test_advisor_charter_says_read_ack_continue():
    from tools.aipos_cli import charter_render as cr

    master = (REPO_ROOT / "agents" / "roles" / "advisor" / "AGENTS.md").read_text(encoding="utf-8")
    start = master.index("**收到 Lybra 更新通知**")
    para = master[start:master.index("**禁止**", start)]
    _show("---- 顾问章程母本「收到 Lybra 更新通知」原文 ----\n" + para)
    assert "看到未读更新 → 读 → ack → 继续" in para
    assert "lybra updates --ack --workspace-root {{governance_root}} --actor {{instance}} --confirm" in para
    assert cr.render_charter.__name__ == "render_charter"
