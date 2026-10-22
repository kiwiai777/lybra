"""AIPOS-F213 — 碎片化整理 E: 队列写入口落账对表(F208 P1-6 / P1-8 选项 / P1-9 / P2-1)。

靶场 = 临时 git 仓 + 临时远端裸仓 + 临时治理根(复用 F184 / F207 靶场夹具, 禁第二份); 写命令走真实产品实现(CLI main 本进程执行 /
门动词函数进程内直调), 落账 = 真提交 + 推送到临时远端。项目名 / 实例名 / 卡号均为靶场值(人肉期接入样本自造, 产品不写死)。
全程不读真实凭据、不碰真实治理根、不对真实门写、不起 pi。

件① 落账单壳: CLI queue amend / 门 lybra_queue_amend_confirm / 门 lybra_queue_withdraw_confirm / CLI queue return-repair 写后即经
   写后落账唯一薄壳 board_adapter.land_after_write 落账(git log 原文); 落账失败不静默(CLI 退出码 3 + 补账命令, 门应答 warnings);
   owner decide / CLI / 门同一薄壳(AST)。
棘轮 R5: 队列写出口(withdraw_task / amend_task / close_task / mark_concluded_task / supersede_task / correct_closed_task /
   mutate_queue_task)的每个调用点须在 verbs post_write_landing.queue_write_sites 登记(即时 / N6 / 交调用方 / 只预演), 未登记即红;
   N6 登记 = 基线 tests/f213_queue_write_n6_baseline.jsonl(只减不增)。
件② queue supersede / queue correct --decision-id 引用收件箱已有裁决: 只一条 owner_decision; 选项 / 卡 / 已用过 / 不存在 = 拒;
   不带 = 行为不变; 收件箱「已定待转达」给带 --decision-id 的命令。
件③ owner_reaudit.choices 与收件箱 pinned 选项交叉校验(篡改即红); schema 内全部 choices / decision_choice 同一校验。
件④ R / R2 两轮审计卡下 audit-verdict 自发现命中 R2(审计卡查找唯一实现 audit_derivation.find_open_audit_task)。
件⑤ supersede 卡面键读声明 card_fields(角色 → 键名), transition_engine 不写死。
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

import test_aipos_f184_queue_landing_supersede as f184  # noqa: E402  — F184 队列落账靶场(禁第二份)
import test_aipos_f207_closure_correct as f207  # noqa: E402  — F207 纠正已结案卡靶场(禁第二份)
from ratchet_baseline import canonical_line, read_entries  # noqa: E402
from test_aipos_f73d_loop_driver import DRIVER, EXEC  # noqa: E402

GOV_REL = f184.GOV_REL
N6_BASELINE = REPO_ROOT / "tests" / "f213_queue_write_n6_baseline.jsonl"
_git, _cli, _card, _head_files = f184._git, f184._cli, f184._card, f184._head_files


def _show(text: str) -> None:
    print(text, flush=True)


@pytest.fixture
def qrig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    """F184 队列落账靶场(临时 git 仓 + 远端裸仓 + 治理根)。"""
    return f184.rig.__wrapped__(tmp_path, monkeypatch)


@pytest.fixture
def crig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    """F207 纠正已结案卡靶场(临时 HOME + home 根 git 仓 + 远端; lybra project new 建靶场项目)。"""
    return f207.rig.__wrapped__(tmp_path, monkeypatch)


def _publish(repo: Path, message: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", message)
    _git(repo, "push", "-q", "origin", "main")


def _cap(instance: str = DRIVER) -> dict:
    """门侧按 bearer 定的能力(进程内直调门动词; 夹具值, 非真实凭据)。"""
    return {"role": "advisor", "role_class": "advisor", "agent_instance": instance, "token_ref": "ref-fixture",
            "expires_at": "2999-01-01T00:00:00Z", "scopes": ["owner_confirm"]}


def _sc(res: dict) -> dict:
    if isinstance(res.get("structuredContent"), dict):
        return res["structuredContent"]
    if res.get("content"):
        return json.loads(res["content"][0]["text"])
    return res


def _log1(repo: Path) -> str:
    return _git(repo, "log", "-1", "--format=%s")


# ===========================================================================
# 件① 落账单壳
# ===========================================================================

def test_item1_cli_amend_lands(qrig: dict) -> None:
    repo, gov = qrig["repo"], qrig["gov"]
    _card(gov, "AIPOS-F213A", "pending")
    _publish(repo, "publish F213A")
    rc, out, err = _cli(gov, "queue", "amend", "--task-id", "AIPOS-F213A", "--actor", DRIVER, "--amendments", '{"priority": "low"}',
                        "--amendment-reason", "Owner 调低优先级")
    files = sorted(_head_files(repo))
    _show(f"[件①·CLI queue amend] rc={rc} git log -1: {_log1(repo)}; 文件: {files}; stdout 落账行: "
          f"{[ln for ln in out.splitlines() if ln.startswith('落账')]}")
    assert rc == 0, (out, err)
    assert _log1(repo) == "chore(governance): 队列写落账 amend AIPOS-F213A"
    assert f"{GOV_REL}/5_tasks/queue/pending/aipos-f213a.md" in files and any("/records/amendments/AIPOS-F213A/" in f for f in files)
    assert _git(repo, "rev-parse", "HEAD") == _git(repo, "rev-parse", "origin/main")


def test_item1_gate_amend_confirm_lands(qrig: dict) -> None:
    from tools.mcp_server import tools as gate

    repo, gov = qrig["repo"], qrig["gov"]
    _card(gov, "AIPOS-F213M", "pending")
    _publish(repo, "publish F213M")
    args = {"task_id": "AIPOS-F213M", "actor": DRIVER, "amendments": {"priority": "low"}, "amendment_reason": "r",
            "workspace_root": str(gov)}
    with gate.request_capability_scope(_cap()):
        dry = _sc(gate.lybra_queue_amend_dry_run(dict(args)))
        done = _sc(gate.lybra_queue_amend_confirm({"dry_run_token": dry["dry_run_token"], "actor": DRIVER,
                                                   "owner_confirmation_token": "OWNER_CONFIRMED", "workspace_root": str(gov)}))
    landing = done.get("landing") or {}
    _show(f"[件①·门 lybra_queue_amend_confirm] ok={done.get('ok')} landing.landed={landing.get('landed')} commit="
          f"{str(landing.get('commit_hash'))[:12]}; git log -1: {_log1(repo)}; 文件: {sorted(_head_files(repo))}")
    assert done.get("ok") and landing.get("landed") is True
    assert _log1(repo) == "chore(governance): 队列写落账 amend AIPOS-F213M"
    assert _git(repo, "rev-parse", "HEAD") == _git(repo, "rev-parse", "origin/main")


def test_item1_gate_withdraw_confirm_lands(qrig: dict) -> None:
    from tools.mcp_server import tools as gate

    repo, gov = qrig["repo"], qrig["gov"]
    _card(gov, "AIPOS-F213W", "pending")
    _publish(repo, "publish F213W")
    with gate.request_capability_scope(_cap()):
        done = _sc(gate.lybra_queue_withdraw_confirm({"task_id": "AIPOS-F213W", "actor": DRIVER, "reason": "方向变了",
                                                      "workspace_root": str(gov)}))
    landing = done.get("landing") or {}
    files = _head_files(repo)
    _show(f"[件①·门 lybra_queue_withdraw_confirm] ok={done.get('ok')} landing.landed={landing.get('landed')}; git log -1: "
          f"{_log1(repo)}; 文件: {sorted(files)}")
    assert done.get("ok") and landing.get("landed") is True
    assert _log1(repo) == "chore(governance): 队列写落账 withdraw AIPOS-F213W"
    assert {f"{GOV_REL}/5_tasks/queue/pending/aipos-f213w.md", f"{GOV_REL}/5_tasks/queue/withdrawn/aipos-f213w.md"} <= files


def test_item1_gate_withdraw_landing_failure_not_silent(qrig: dict) -> None:
    from tools.mcp_server import tools as gate

    repo, gov, remote = qrig["repo"], qrig["gov"], qrig["remote"]
    _card(gov, "AIPOS-F213X", "pending")
    _publish(repo, "publish F213X")
    remote.rename(remote.with_name(remote.name + ".offline"))
    with gate.request_capability_scope(_cap()):
        done = _sc(gate.lybra_queue_withdraw_confirm({"task_id": "AIPOS-F213X", "actor": DRIVER, "reason": "r",
                                                      "workspace_root": str(gov)}))
    warn = [w for w in done.get("warnings") or [] if "落账未完成" in str(w)]
    _show(f"[件①·门落账失败] ok={done.get('ok')} landing.landed={(done.get('landing') or {}).get('landed')} warnings: {warn}")
    assert done.get("ok") and (done.get("landing") or {}).get("landed") is False
    assert warn and "补账:" in warn[0]
    assert (gov / "5_tasks" / "queue" / "withdrawn" / "aipos-f213x.md").is_file()  # 写入已生效, 不回滚


def test_item1_cli_amend_landing_failure_exit3(qrig: dict) -> None:
    repo, gov, remote = qrig["repo"], qrig["gov"], qrig["remote"]
    _card(gov, "AIPOS-F213Y", "pending")
    _publish(repo, "publish F213Y")
    remote.rename(remote.with_name(remote.name + ".offline"))
    rc, out, err = _cli(gov, "queue", "amend", "--task-id", "AIPOS-F213Y", "--actor", DRIVER, "--amendments", '{"priority": "low"}',
                        "--amendment-reason", "r")
    _show(f"[件①·CLI amend 落账失败] rc={rc} stderr: {err.strip()[:300]}")
    assert rc == 3 and "落账未完成" in err and "补账:" in err


def test_item1_return_repair_lands(qrig: dict) -> None:
    repo, gov = qrig["repo"], qrig["gov"]
    _card(gov, "AIPOS-F213R", "claimed", extra={"claimed_by": EXEC, "claimed_at": "2026-10-18T00:00:00Z", "claim_id": "claim_AIPOS-F213R_x",
                                               "active_session_id": "session_AIPOS-F213R_lost"})
    _publish(repo, "claimed F213R(卡面有会话号、会话记录缺 = 卡住的交回)")
    rc, out, err = _cli(gov, "queue", "return-repair", "--task-id", "AIPOS-F213R", "--actor", EXEC)
    files = sorted(_head_files(repo))
    _show(f"[件①·CLI queue return-repair 原文] rc={rc}\nstdout: {out}\nstderr: {err}")
    report = json.loads(out[:out.rindex("}") + 1])
    _show(f"[件①·CLI queue return-repair] rc={rc} verdict={report.get('verdict')} actions={report.get('repair_actions')}; "
          f"git log -1: {_log1(repo)}; 文件: {files}")
    assert rc == 0, (out, err)
    assert report["verdict"] == "REPAIRED" and (report.get("landing") or {}).get("landed") is True
    assert _log1(repo) == "chore(governance): 队列写落账 return-repair AIPOS-F213R"
    assert f"{GOV_REL}/5_tasks/queue/claimed/aipos-f213r.md" in files and any("/records/sessions/AIPOS-F213R/" in f for f in files)
    assert _git(repo, "status", "--porcelain", "--", GOV_REL + "/5_tasks") == ""


def _calls_in(path: Path, func: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == func)
    return {c.func.id if isinstance(c.func, ast.Name) else getattr(c.func, "attr", "") for c in ast.walk(node) if isinstance(c, ast.Call)}


def test_item1_one_shell_shared_by_cli_gate_owner_decide() -> None:
    """防碎片化: CLI 队列写 / 门确认分支 / owner decide 都经 board_adapter.land_after_write; 原 CLI 专属 _land_queue_write 与
    owner_inbox._land 已删(git grep 零定义); land_written_paths 在队列写路径上只由薄壳调用。"""
    ba = REPO_ROOT / "tools/aipos_cli/board_adapter.py"
    cli = REPO_ROOT / "tools/aipos_cli/aipos_cli.py"
    inbox = REPO_ROOT / "tools/aipos_cli/owner_inbox.py"
    tools = REPO_ROOT / "tools/mcp_server/tools.py"
    assert "land_written_paths" in _calls_in(ba, "land_after_write")
    assert "land_after_write" in _calls_in(cli, "_land_queue_entry") and "land_after_write" in _calls_in(cli, "_land_project_writes")
    assert "land_after_write" in _calls_in(inbox, "decide") and "land_after_write" in _calls_in(ba, "execute_dry_run")
    assert "land_after_write" in _calls_in(tools, "lybra_queue_withdraw_confirm")
    grep = subprocess.run(["git", "grep", "-n", "-E", r"def (_land_queue_write|_queue_write_paths|_land)\(", "--", "tools/aipos_cli/*.py",
                           "tools/mcp_server/*.py", "web/board/*.py"],
                          cwd=REPO_ROOT, capture_output=True, text=True).stdout
    direct = sorted(f"{p.relative_to(REPO_ROOT)}:{c.lineno}" for p in (cli, inbox, tools)
                    for c in ast.walk(ast.parse(p.read_text(encoding="utf-8"))) if isinstance(c, ast.Call)
                    and (getattr(c.func, "id", None) or getattr(c.func, "attr", None)) == "land_written_paths")
    direct = "\n".join(direct)
    _show(f"[件①·单壳] 旧专属壳定义: {grep.strip() or '无'}; 队列写入口直调 land_written_paths: {direct.strip() or '无'}")
    assert grep.strip() == "" and direct.strip() == ""


def test_item1_n6_landing_declared_for_close_and_mark_concluded() -> None:
    from tools.aipos_cli.governance_commit import post_write_landing_declaration

    sites = post_write_landing_declaration()["queue_write_sites"]["sites"]
    n6 = {k: v["modes"]["n6"] for k, v in sites.items() if "n6" in v["modes"] and ("close_task" in k or "mark_concluded_task" in k)}
    for key, note in sorted(n6.items()):
        _show(f"[件①·N6 声明] {key}: {note}")
    assert {"tools/aipos_cli/aipos_cli.py::main->close_task", "tools/aipos_cli/aipos_cli.py::main->mark_concluded_task",
            "tools/mcp_server/tools.py::lybra_queue_close_confirm->close_task",
            "tools/mcp_server/tools.py::lybra_mark_concluded->mark_concluded_task"} <= set(n6)


# ===========================================================================
# 棘轮 R5: 队列写出口调用点逐个登记
# ===========================================================================

def _product_sources() -> dict[str, str]:
    from tools.aipos_cli.workspace_config import is_test_file  # noqa: F401  — 与 run-all 同一测试文件判定(只排除, 不发现)

    files = subprocess.run(["git", "ls-files", "*.py"], cwd=REPO_ROOT, capture_output=True, text=True, check=True).stdout.split()
    out: dict[str, str] = {}
    for rel in files:
        parts = Path(rel).parts
        if rel.startswith("tests/") or "tests" in parts[:-1] or Path(rel).name.startswith("test_") or Path(rel).name == "conftest.py":
            continue
        out[rel] = (REPO_ROOT / rel).read_text(encoding="utf-8")
    return out


def enumerate_write_sites(sources: dict[str, str], writers: list[str]) -> dict[str, dict]:
    """<文件>::<所在函数(类.函数 链)>-><出口> → {count, calls[{line, dry_run}], shell_called}; import 别名同算(from … import 出口 as 别名)。"""
    wanted = set(writers)
    sites: dict[str, dict] = {}
    for rel, src in sources.items():
        tree = ast.parse(src)
        alias = {n.asname: n.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) for n in node.names
                 if n.name in wanted and n.asname}
        stack: list[str] = []

        def visit(node: ast.AST) -> None:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                stack.append(node.name)
                for child in ast.iter_child_nodes(node):
                    visit(child)
                stack.pop()
                return
            if isinstance(node, ast.Call):
                fn = node.func
                name = fn.id if isinstance(fn, ast.Name) else (fn.attr if isinstance(fn, ast.Attribute) else None)
                name = alias.get(name, name)
                if name in wanted and not (isinstance(fn, ast.Name) and fn.id == name and False):
                    key = f"{rel}::{'.'.join(stack) or '<module>'}->{name}"
                    dry = next((k.value for k in node.keywords if k.arg == "dry_run"), None)
                    entry = sites.setdefault(key, {"count": 0, "calls": []})
                    entry["count"] += 1
                    entry["calls"].append({"line": node.lineno, "dry_run_literal_true": isinstance(dry, ast.Constant) and dry.value is True})
            for child in ast.iter_child_nodes(node):
                visit(child)

        visit(tree)
    return sites


def _function_calls(sources: dict[str, str], qualified: str) -> set[str]:
    rel, func = qualified.split("::", 1)
    tree = ast.parse(sources[rel])
    names = func.split(".")
    scope: list[ast.AST] = [tree]
    node: ast.AST | None = None
    for part in names:
        node = next((n for s in scope for n in ast.iter_child_nodes(s)
                     if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.name == part), None)
        if node is None:
            return set()
        scope = [node]
    return {c.func.id if isinstance(c.func, ast.Name) else getattr(c.func, "attr", "") for c in ast.walk(node) if isinstance(c, ast.Call)}


def r5_problems(sources: dict[str, str], registry: dict, baseline: list[dict]) -> list[str]:
    """R5 判定: 返回红项(空 = 绿)。"""
    problems: list[str] = []
    writers = list(registry["writers"])
    found = enumerate_write_sites(sources, writers)
    declared = registry["sites"]
    shell_fn = registry["shell"]["function"].split("::", 1)[1]
    shell_names = {shell_fn} | {a.split("::", 1)[1] for a in registry["shell"]["adapters"]}
    for key, site in sorted(found.items()):
        if key not in declared:
            problems.append(f"未登记的队列写调用点 {key}(行 {[c['line'] for c in site['calls']]}): 在 verbs post_write_landing."
                            "queue_write_sites.sites 登记落账方式(instant / n6 / caller / preview)")
            continue
        entry = declared[key]
        if entry.get("count") != site["count"]:
            problems.append(f"{key}: 登记 count={entry.get('count')}, 实际 {site['count']} 处(行 {[c['line'] for c in site['calls']]})")
        modes = set(entry.get("modes") or {})
        if not modes or not modes <= set(registry["modes"]):
            problems.append(f"{key}: modes {sorted(modes)} 须为 {registry['modes']} 的非空子集")
        rel, rest = key.split("::", 1)
        func = rest.rsplit("->", 1)[0]
        calls = _function_calls(sources, f"{rel}::{func}") if func != "<module>" else set()
        if "instant" in modes and not calls & shell_names:
            problems.append(f"{key}: 登记 instant 但所在函数 {func} 不调写后落账薄壳 {sorted(shell_names)}")
        if modes == {"preview"} and not all(c["dry_run_literal_true"] for c in site["calls"]):
            problems.append(f"{key}: 只登记 preview 但调用点不是 dry_run=True 字面")
        if "caller" in modes and func.split(".")[-1] not in writers:
            caller = str(entry.get("caller") or "")
            if not caller or not (_function_calls(sources, caller) & shell_names):
                problems.append(f"{key}: 登记 caller 但所在函数不是队列写出口, 且 caller={caller or '(未给)'} 不调写后落账薄壳")
    for key in sorted(set(declared) - set(found)):
        problems.append(f"登记已无此调用点 {key}: 删登记")
    n6_sites = {k for k, v in declared.items() if "n6" in (v.get("modes") or {})}
    base = {str(e.get("site")) for e in baseline}
    for key in sorted(n6_sites - base):
        problems.append(f"新增 N6 收账(非即时落账)的队列写调用点 {key}: 基线只减不增——改为即时落账(经写后落账唯一薄壳), 或说明后由审计放行")
    for key in sorted(base - n6_sites):
        problems.append(f"基线条目已不是 N6 登记(已改即时 / 已删) {key}: 删基线行")
    return problems


def _registry() -> dict:
    from tools.aipos_cli.governance_commit import post_write_landing_declaration

    return post_write_landing_declaration()["queue_write_sites"]


def test_r5_every_queue_write_call_site_is_registered() -> None:
    registry = _registry()
    sources = _product_sources()
    found = enumerate_write_sites(sources, list(registry["writers"]))
    for key, site in sorted(found.items()):
        modes = (registry["sites"].get(key) or {}).get("modes") or {}
        _show(f"[R5] {key} ×{site['count']} 行 {[c['line'] for c in site['calls']]} → {'/'.join(sorted(modes)) or '未登记'}")
    problems = r5_problems(sources, registry, read_entries(N6_BASELINE))
    _show(f"[R5] 调用点 {len(found)} 个; N6 基线 {len(read_entries(N6_BASELINE))} 条; 红项: {problems or '无'}")
    assert not problems, "\n".join(problems)


def test_r5_detects_unregistered_site_and_stale_baseline_red() -> None:
    registry = _registry()
    sources = dict(_product_sources())
    rel = "tools/aipos_cli/owner_inbox.py"
    sources[rel] = sources[rel] + "\n\ndef _sneaky_amend(root):\n    from tools.aipos_cli.board_adapter import amend_task as _a\n" \
                                  "    return _a(task_id='X', actor='a', amendments={}, amendment_reason='r', dry_run=False, repo_root=root)\n"
    baseline = read_entries(N6_BASELINE) + [{"reason": "夹具: 已不是 N6 的陈旧行", "site": "tools/aipos_cli/x.py::gone->amend_task"}]
    problems = r5_problems(sources, registry, baseline)
    _show(f"[R5·红样例] {problems}")
    assert any("未登记的队列写调用点 tools/aipos_cli/owner_inbox.py::_sneaky_amend->amend_task" in p for p in problems)
    assert any("基线条目已不是 N6 登记" in p for p in problems)


def test_r5_baseline_well_formed_one_line_per_site() -> None:
    entries = read_entries(N6_BASELINE)
    lines = N6_BASELINE.read_text(encoding="utf-8").splitlines()
    assert lines == sorted(canonical_line(e) for e in entries)
    assert all(set(e) == {"site", "reason"} and str(e["reason"]).strip() for e in entries)
    _show(f"[R5·基线] {len(entries)} 条: {[e['site'] for e in entries]}")


# ===========================================================================
# 件② --decision-id 引用收件箱已有裁决
# ===========================================================================

def _inbox_ruling(gov: Path, *, choice: str, task_id: str, anchor: str, words: str) -> str:
    """经收件箱同一构造与写路径(owner_inbox.queue_ruling_payload → 门侧 record_owner_decision + execute_dry_run)落一条 Owner 裁决。"""
    from tools.aipos_cli.board_adapter import execute_dry_run, record_owner_decision
    from tools.aipos_cli.clock import iso_z
    from tools.aipos_cli.controlled_execute import OWNER_CONFIRMATION_TOKEN
    from tools.aipos_cli.owner_inbox import queue_ruling_payload

    payload = queue_ruling_payload(gov, choice=choice, task_id=task_id, anchor=anchor, subject=f"{task_id} → {anchor}", relayed_by=DRIVER,
                                   owner_words=words, owner_ref=None, decided_at=iso_z())
    dry = record_owner_decision(payload, dry_run=True, repo_root=gov, actor="owner")
    done = execute_dry_run(str(dry["dry_run_token"]), "owner", owner_confirmation_token=OWNER_CONFIRMATION_TOKEN, repo_root=gov)
    assert done.get("ok"), done
    return str(payload["decision_id"])


def _decisions(gov: Path) -> list[Path]:
    from tools.aipos_cli.record_writer import record_root

    return sorted((gov / record_root("owner_decisions")).glob("*.md"))


def test_item2_supersede_references_inbox_ruling_single_decision(qrig: dict) -> None:
    from tools.aipos_cli.frontmatter import require_frontmatter
    from tools.aipos_cli.owner_inbox import collect_project_inbox

    repo, gov = qrig["repo"], qrig["gov"]
    _card(gov, "AIPOS-F213S", "claimed", extra={"claimed_by": EXEC, "claimed_at": "2026-10-18T00:00:00Z", "claim_id": "claim_AIPOS-F213S_x",
                                               "active_session_id": "session_AIPOS-F213S_x"})
    _card(gov, "AIPOS-F213N", "pending")
    did = _inbox_ruling(gov, choice="supersede", task_id="AIPOS-F213S", anchor="inbox-item", words="夹具: 目标改了, 用新卡")
    _publish(repo, "Owner 在收件箱定撤换")
    relay = [r for r in collect_project_inbox(gov)["relays"] if r.get("decision_id") == did]
    _show(f"[件②·已定待转达] {[r['advisor_next'] for r in relay]}")
    assert relay and f"queue supersede --task-id AIPOS-F213S --by <新卡号> --decision-id {did}" in relay[0]["advisor_next"]
    before = _decisions(gov)
    rc, out, err = _cli(gov, "queue", "supersede", "--task-id", "AIPOS-F213S", "--by", "AIPOS-F213N", "--actor", DRIVER,
                        "--decision-id", did)
    _show(f"[件②·supersede --decision-id 预演] rc={rc}: {out.strip().splitlines()[0]}")
    assert rc == 0 and "引用收件箱已有裁决" in out and _decisions(gov) == before
    rc, out, err = _cli(gov, "queue", "supersede", "--task-id", "AIPOS-F213S", "--by", "AIPOS-F213N", "--actor", DRIVER,
                        "--decision-id", did, "--confirm")
    fm, _ = require_frontmatter(gov / "5_tasks" / "queue" / "withdrawn" / "aipos-f213s.md")
    files = sorted(_head_files(repo))
    _show(f"[件②·supersede --decision-id 确认] rc={rc}: {out.strip().splitlines()[0]}; 决策记录 {len(before)} → {len(_decisions(gov))} 份: "
          f"{[p.name for p in _decisions(gov)]}; 卡面 superseded_decision_id={fm.get('superseded_decision_id')} "
          f"superseded_owner_words={fm.get('superseded_owner_words')}; git log -1: {_log1(repo)}; 提交文件: {files}")
    assert rc == 0, (out, err)
    assert len(_decisions(gov)) == 1 and fm["superseded_decision_id"] == did and fm["superseded_owner_words"] == "夹具: 目标改了, 用新卡"
    assert not any("/owner_decisions/" in f for f in files) and _log1(repo) == "chore(governance): 队列写落账 supersede AIPOS-F213S"
    assert not [r for r in collect_project_inbox(gov)["relays"] if r.get("decision_id") == did]  # 已执行 = 转达消失


def test_item2_supersede_reference_refusals_zero_write(qrig: dict) -> None:
    repo, gov = qrig["repo"], qrig["gov"]
    _card(gov, "AIPOS-F213T", "pending")
    _card(gov, "AIPOS-F213U", "pending")
    _card(gov, "AIPOS-F213N", "pending")
    wrong_choice = _inbox_ruling(gov, choice="withdraw", task_id="AIPOS-F213T", anchor="a1", words="夹具: 撤")
    other_card = _inbox_ruling(gov, choice="supersede", task_id="AIPOS-F213U", anchor="a2", words="夹具: 换 U")
    used = _inbox_ruling(gov, choice="supersede", task_id="AIPOS-F213T", anchor="a3", words="夹具: 换 T")
    _card(gov, "AIPOS-F213OLD", "withdrawn", extra={"superseded_by": "AIPOS-F213N", "superseded_decision_id": used})  # 已被别的撤换用过
    _publish(repo, "fixture rulings")
    head = _git(repo, "rev-parse", "HEAD")
    cases = {"选项不符": (wrong_choice, "DECISION_REF_CHOICE"), "裁的不是本卡": (other_card, "DECISION_REF_TASK"),
             "已被用过": (used, "DECISION_REF_CONSUMED"), "不存在": ("owner-inbox-nope", "DECISION_REF_NOT_FOUND")}
    for label, (did, code) in cases.items():
        rc, out, err = _cli(gov, "queue", "supersede", "--task-id", "AIPOS-F213T", "--by", "AIPOS-F213N", "--actor", DRIVER,
                            "--decision-id", did, "--confirm")
        _show(f"[件②·拒·{label}] rc={rc}: {out.strip()}")
        assert rc == 1 and code in out and out.startswith("撤换被拒(零写入)")
    assert _git(repo, "rev-parse", "HEAD") == head and (gov / "5_tasks" / "queue" / "pending" / "aipos-f213t.md").is_file()
    assert _git(repo, "status", "--porcelain") == ""


def test_item2_supersede_without_decision_id_unchanged(qrig: dict) -> None:
    repo, gov = qrig["repo"], qrig["gov"]
    _card(gov, "AIPOS-F213V", "pending")
    _card(gov, "AIPOS-F213N", "pending")
    _publish(repo, "publish")
    rc, out, err = _cli(gov, "queue", "supersede", "--task-id", "AIPOS-F213V", "--by", "AIPOS-F213N", "--actor", DRIVER,
                        "--owner-words", "夹具: 直接转达的原话", "--confirm")
    _show(f"[件②·不带 --decision-id] rc={rc}: {out.strip().splitlines()[0]}; 决策记录 {[p.name for p in _decisions(gov)]}")
    assert rc == 0 and out.startswith("已撤换: AIPOS-F213V [pending] → withdrawn, superseded_by=AIPOS-F213N; 决策记录 ")
    assert len(_decisions(gov)) == 1 and any("/owner_decisions/" in f for f in _head_files(repo))


def test_item2_correct_references_inbox_ruling_single_decision(crig: dict) -> None:
    from tools.aipos_cli.frontmatter import require_frontmatter
    from tools.aipos_cli.owner_inbox import collect_project_inbox
    from tools.aipos_cli.queue_mutation import correct_declaration
    from tools.aipos_cli.task_complexity import closure_disputes
    from tools.aipos_cli.task_loader import find_task_card

    home, gov, ws = crig["home"], crig["gov"], crig["ws"]
    f207._closed_docs_card(gov, "LX-27")
    choice = str(correct_declaration()["decision_choice"])
    from tools.aipos_cli.board_adapter import execute_dry_run, record_owner_decision
    from tools.aipos_cli.clock import iso_z
    from tools.aipos_cli.controlled_execute import OWNER_CONFIRMATION_TOKEN
    from tools.aipos_cli.owner_inbox import queue_ruling_payload

    payload = queue_ruling_payload(gov, choice=choice, task_id="LX-27", anchor="inbox", subject="LX-27 [completed]", relayed_by=f207.ADVISOR,
                                   owner_words=f207.WORDS, owner_ref=None, decided_at=iso_z())
    dry = record_owner_decision(payload, dry_run=True, repo_root=gov, actor="owner")
    assert execute_dry_run(str(dry["dry_run_token"]), "owner", owner_confirmation_token=OWNER_CONFIRMATION_TOKEN, repo_root=gov).get("ok")
    did = str(payload["decision_id"])
    f207._commit(home, "Owner 在收件箱定纠正")
    relay = [r for r in collect_project_inbox(gov)["relays"] if r.get("decision_id") == did]
    _show(f"[件②·已定待转达·纠正] {[r['advisor_next'] for r in relay]}")
    assert relay and f"queue correct --task-id LX-27 --decision-id {did} --relayed-by" in relay[0]["advisor_next"]
    rc, out, err = f207._cli(*ws, "queue", "correct", "--task-id", "LX-27", "--relayed-by", f207.ADVISOR, "--decision-id", did, "--confirm")
    _show(f"[件②·correct --decision-id 确认] rc={rc}\n{out.strip()}")
    assert rc == 0, (out, err)
    decisions = _decisions(gov)
    dispute = closure_disputes(gov, "LX-27")
    path, state = find_task_card(gov, "LX-27C1")
    ref_field = str(correct_declaration()["followup"]["ref_field"])
    fm, _ = require_frontmatter(path)
    _show(f"[件②·correct] 决策记录 {[p.name for p in decisions]}; 争议标记 decision_id={dispute[-1]['decision_id']}; "
          f"承接卡 LX-27C1[{state}] {ref_field}={fm.get(ref_field)}")
    assert len(decisions) == 1 and dispute[-1]["decision_id"] == did and fm.get(ref_field) == did and state == "pending"
    assert "引用收件箱已有裁决: " + did in out
    rc, out, err = f207._cli(*ws, "queue", "correct", "--task-id", "LX-27", "--relayed-by", f207.ADVISOR, "--decision-id", did)
    _show(f"[件②·同一裁决再用] rc={rc}: {out.strip()}")
    assert rc == 1 and "DECISION_REF_CONSUMED" in out


def test_item2_correct_reference_choice_mismatch_refused(crig: dict) -> None:
    gov, ws = crig["gov"], crig["ws"]
    f207._closed_docs_card(gov, "LX-28")
    did = _inbox_ruling(gov, choice="supersede", task_id="LX-28", anchor="x", words="夹具: 撤换(不是纠正)")
    rc, out, err = f207._cli(*ws, "queue", "correct", "--task-id", "LX-28", "--relayed-by", f207.ADVISOR, "--decision-id", did, "--confirm")
    _show(f"[件②·correct 选项不符] rc={rc}: {out.strip()}")
    assert rc == 1 and "DECISION_REF_CHOICE" in out and len(_decisions(gov)) == 1


# ===========================================================================
# 件③ 选项交叉校验
# ===========================================================================

def _walk_choice_refs(node, path: str = ""):
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{path}.{key}" if path else key
            if key in ("choices", "decision_choice"):
                yield here, value
            yield from _walk_choice_refs(value, here)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _walk_choice_refs(value, f"{path}[{i}]")


def test_item3_all_schema_choice_refs_checked_against_inbox() -> None:
    from tools.aipos_cli.next_resolver import audit_blocked_declaration
    from tools.aipos_cli.queue_mutation import correct_declaration, require_inbox_choices, supersede_declaration

    refs = []
    for schema in sorted((REPO_ROOT / "schema").glob("*.json")):
        for where, value in _walk_choice_refs(json.loads(schema.read_text(encoding="utf-8"))):
            refs.append((f"{schema.name}:{where}", value))
            require_inbox_choices(value if isinstance(value, list) else [value], f"{schema.name}:{where}")
    _show(f"[件③] schema 内选项引用: {refs}")
    assert {r[0] for r in refs} == {"transitions.schema.json:nodes.N4.audit_blocked.owner_reaudit.choices",
                                    "transitions.schema.json:queue_mutations.transitions.supersede.decision_choice",
                                    "transitions.schema.json:queue_mutations.correct.decision_choice"}
    audit_blocked_declaration(), correct_declaration(), supersede_declaration()  # 加载口都过同一校验
    src = (REPO_ROOT / "tools/aipos_cli/queue_mutation.py").read_text(encoding="utf-8")
    assert src.count('not in inbox_declaration()["options"]') == 0 and src.count("def require_inbox_choices(") == 1  # 无第二份内联校验


def test_item3_tampered_reaudit_choices_red(monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.aipos_cli import next_resolver, owner_inbox
    from tools.schema_loader import SchemaLoadError

    real_node = next_resolver._transition_node

    def renamed_choice(name: str):
        node = json.loads(json.dumps(real_node(name)))
        if name == "N4":
            node["audit_blocked"]["owner_reaudit"]["choices"] = ["reaudit_now"]
        return node

    monkeypatch.setattr(next_resolver, "_transition_node", renamed_choice)
    with pytest.raises(SchemaLoadError) as exc:
        next_resolver.audit_blocked_declaration()
    _show(f"[件③·篡改 transitions owner_reaudit.choices] {exc.value}")
    monkeypatch.setattr(next_resolver, "_transition_node", real_node)
    real_decl = owner_inbox.inbox_declaration()

    def unpinned():
        decl = json.loads(json.dumps(real_decl))
        decl["categories"]["loop_owner_needed"]["pinned_options_by_reason"]["audit_blocked_repeated"] = ["wait_product"]
        return decl

    monkeypatch.setattr(owner_inbox, "inbox_declaration", unpinned)
    with pytest.raises(SchemaLoadError) as exc2:
        next_resolver.audit_blocked_declaration()
    _show(f"[件③·篡改收件箱 pinned_options_by_reason] {exc2.value}")
    assert "reaudit_now" in str(exc.value) and "pinned_options_by_reason.audit_blocked_repeated" in str(exc2.value)


# ===========================================================================
# 件④ 审计卡查找单源
# ===========================================================================

class _FakeGate:
    calls: list[tuple[str, dict]] = []

    def __init__(self, base_url: str, token: str, **_kw) -> None:
        pass

    def initialize(self) -> None:
        pass

    def call_tool(self, name: str, arguments: dict, **_kw) -> dict:
        _FakeGate.calls.append((name, dict(arguments)))
        return {"ok": False, "verdict": "BLOCK", "blocking_reasons": ["夹具门: 只记参数"]}


def test_item4_audit_verdict_self_discovery_hits_latest_round(qrig: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.aipos_cli import audit_helpers, confirm_client
    from tools.aipos_cli.audit_derivation import audit_round_ids, find_open_audit_task

    gov = qrig["gov"]
    _card(gov, "AIPOS-F213Q", "claimed", extra={"claimed_by": EXEC, "claimed_at": "2026-10-18T00:00:00Z", "claim_id": "c"})
    for audit_id in ("AIPOS-F213QR", "AIPOS-F213QR2"):
        _card(gov, audit_id, "claimed", extra={"task_mode": "audit", "reviewed_task_id": "AIPOS-F213Q", "derived_from": "AIPOS-F213Q"})
    monkeypatch.setattr(confirm_client, "GateClient", _FakeGate)
    monkeypatch.setattr(audit_helpers, "resolve_audit_credential", lambda **_kw: ("auditor", "fixture-token", None))
    _FakeGate.calls = []
    rc, out, err = _cli(gov, "audit-verdict", "--reviewed-task-id", "AIPOS-F213Q", "--verdict", "PASS", "--actor", "audit.f213.h1",
                        "--agent-instance", "audit.f213.h1", "--owner-policy-ref", "pol_fixture", "--gate-url", "http://127.0.0.1:1",
                        "--findings-summary", "s")
    sent = _FakeGate.calls[0][1] if _FakeGate.calls else {}
    _show(f"[件④] 审计轮 {audit_round_ids('AIPOS-F213Q', gov)}; find_open_audit_task = {find_open_audit_task('AIPOS-F213Q', gov)}; "
          f"audit-verdict 自发现 → lybra_audit_verdict_dry_run.audit_task_id = {sent.get('audit_task_id')!r}(rc={rc})")
    assert sent.get("audit_task_id") == "AIPOS-F213QR2"
    (gov / "5_tasks" / "queue" / "claimed" / "aipos-f213qr2.md").rename(gov / "5_tasks" / "queue" / "completed" / "aipos-f213qr2.md")
    assert find_open_audit_task("AIPOS-F213Q", gov) is None  # 最新一轮已结 = 无在途(不回落到已被取代的 R)
    src = (REPO_ROOT / "tools/aipos_cli/audit_helpers.py").read_text(encoding="utf-8")
    grep = subprocess.run(["git", "grep", "-n", "def derive_audit_task_id\\|def find_open_audit_task", "--", "tools"], cwd=REPO_ROOT,
                          capture_output=True, text=True).stdout.strip().splitlines()
    _show(f"[件④·单源] {grep}")
    assert "derive_audit_task_id" not in src.split('"""', 2)[2].split("def resolve_audit_credential", 1)[0]
    assert sorted(ln.split(":", 1)[0] for ln in grep) == ["tools/aipos_cli/audit_derivation.py", "tools/aipos_cli/audit_derivation.py"]


# ===========================================================================
# 件⑤ supersede 卡面键读声明
# ===========================================================================

def test_item5_supersede_card_keys_read_from_declaration(monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.aipos_cli import queue_mutation
    from tools.aipos_cli.transition_engine import apply_transition_metadata
    from tools.schema_loader import SchemaLoadError

    decl = queue_mutation.supersede_declaration()
    meta = {"task_id": "X", "status": "pending"}
    out = apply_transition_metadata(metadata=dict(meta), transition_name="supersede", actor="advisor.x.h1", timestamp="2026-10-22T00:00:00Z",
                                    superseded_by="Y", owner_words="w", owner_ref=None, decision_id="d1", reason="r")
    written = {k: out[k] for k in decl["card_fields"].values() if k in out}
    _show(f"[件⑤] 声明 card_fields={decl['card_fields']}; 写入卡面 {written}")
    assert written == {"superseded_by": "Y", "superseded_at": "2026-10-22T00:00:00Z", "superseded_owner_words": "w",
                       "superseded_decision_id": "d1"}
    renamed = {**decl, "card_fields": {**decl["card_fields"], "by": "replaced_by"}}
    monkeypatch.setattr(queue_mutation, "supersede_declaration", lambda: renamed)
    out2 = apply_transition_metadata(metadata=dict(meta), transition_name="supersede", actor="advisor.x.h1", timestamp="t",
                                     superseded_by="Y", owner_words="w", reason="r")
    _show(f"[件⑤·声明改键名 by → replaced_by] 卡面: replaced_by={out2.get('replaced_by')} superseded_by={out2.get('superseded_by')}")
    assert out2.get("replaced_by") == "Y" and "superseded_by" not in out2
    src = (REPO_ROOT / "tools/aipos_cli/transition_engine.py").read_text(encoding="utf-8")
    assert not any(f'updated["{k}"]' in src for k in decl["card_fields"].values())
    monkeypatch.undo()
    from tools.schema_loader import load_schema

    real = load_schema("transitions")
    for bad in ({"by": "superseded_by"}, {**decl["card_fields"], "at": "no_such_card_field"}):
        tampered = json.loads(json.dumps(real))
        tampered["queue_mutations"]["transitions"]["supersede"]["card_fields"] = bad
        monkeypatch.setattr("tools.schema_loader.load_schema",
                            lambda name, *a, _t=tampered, _real=load_schema, **kw: _t if name == "transitions" else _real(name, *a, **kw))
        with pytest.raises(SchemaLoadError) as exc:
            queue_mutation.supersede_declaration()
        _show(f"[件⑤·声明不合即红] {exc.value}")
        monkeypatch.undo()
