"""AIPOS-F100 件④ — 零依赖全链路夹具: 屏蔽 PyYAML 跑 草稿创建→发布→认领→交回 ingest(门自动派审)→审计认领→裁决 ingest
→ finalization ingest → close, 每步全部记录经「必须读出」入口回读, 且与有 PyYAML 时逐字段相同。

靶场 = 临时治理根(tmp) + 临时产品仓(git) + 临时门: 门的 MCP 工具函数(tools/mcp_server/tools.dispatch_tool)进程内直调,
只替换 HTTP 传输(GateClient → 进程内), 凭据/信封/两阶段/记录写入全部走产品真实实现; 驱动方每步由推导核
derive_next_step 派生命令, 经产品 CLI 入口(next_resolver._run_cli_in_process = aipos_cli.main)执行; return/verdict/finalization
经产物入口 artifact_ingest.ingest_task_artifact。

屏蔽手法(与 F98 同): 子进程启动即在 sys.meta_path 插入拦截 `yaml` 的 finder, 早于任何产品模块 import——全进程无 PyYAML,
产品读(frontmatter 兜底解析器)写(record_writer stdlib 写出器)都走零依赖路径。两种模式各在独立子进程跑同一链路脚本
(本文件 `python3 <本文件> <yaml|block> <工作目录>`), 输出全部 md 的回读快照 JSON, 夹具比对:
  - 每步: 链路动作成功; 该步后治理根全部 md 经 frontmatter.require_frontmatter 读出(读不出 = 红);
  - 两模式: 步序/动作结果相同; 全部记录(路径、frontmatter 键序与值、正文)在规整时间戳/运行目录/随机 id 后逐字相同;
  - 写出器: 屏蔽模式下门写的记录文本为 stdlib 写出器形(双引号标量), 有 PyYAML 时为 safe_dump 形——两种文本读出同值。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

if __name__ == "__main__" and len(sys.argv) == 3 and sys.argv[1] == "block":
    class _BlockYaml:
        """sys.meta_path finder: 任何 `yaml` / `yaml.*` import 一律 ImportError(全进程零 PyYAML)。"""

        def find_spec(self, name, path=None, target=None):  # noqa: ANN001
            if name == "yaml" or name.startswith("yaml."):
                raise ImportError("yaml blocked (AIPOS-F100 zero-dependency full chain)")
            return None

    sys.meta_path.insert(0, _BlockYaml())

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

TASK = "F100Z-1"
AUDIT = f"{TASK}R"
EXEC = "exec.zd.test"
AUDITOR = "audit.zd.test"
DRIVER = "advisor.zd.test"
POLICY = "pol_zd_loop"
GIT_ENV = {"GIT_AUTHOR_NAME": "fixture", "GIT_AUTHOR_EMAIL": "fixture@lybra.local", "GIT_COMMITTER_NAME": "fixture",
           "GIT_COMMITTER_EMAIL": "fixture@lybra.local", "GIT_AUTHOR_DATE": "2026-10-04T00:00:00Z",
           "GIT_COMMITTER_DATE": "2026-10-04T00:00:00Z"}
# 执行体 / 审计体手写的产物(固定文本, 两模式字节相同; 不经产品写出器——它们是 agent 的产物, 不是门写的记录)
RETURN_TEXT = ("---\ncommit_sha: {sha}\ntree_hash: {tree}\nbranch: card/{task}\nmodel: fixture-model\n---\n"
               "# RETURN — {task}\n\n## 一句话结论\n完成: 零依赖全链路。\n\n## 改动清单\n- tools/x.py\n")
VERDICT_TEXT = ("---\nverdict: PASS\ncommit_sha: {sha}\nreviewed_task_id: {task}\nmodel: fixture-model\n---\n"
                "# 审计报告 — {audit}\n\n## 一句话结论\nPASS: 零依赖链路可回读。\n")
FINALIZE_TEXT = ("---\nmerge_commit: {sha}\nremote_ref: origin/main\ndeploy_status: deployed\n---\n"
                 "# RETURN — {task}-FINALIZE\n\n## 一句话结论\n已合并。\n")
# 规整: 随机 dry_run id / 内容 sha256(含时刻; 两模式写出的文本风格不同, 其 sha256 本就不同) / 运行时刻——先 id 与 sha256
# 再时间戳: 时间戳式样 \d{8}_?\d{6} 会吃进碰巧含 14 位连续数字的 sha256, 使后者不再被整体规整(两模式偶发不等)
_NORMALIZE = [
    (re.compile(r"dryrun_[0-9a-f]{32}"), "dryrun_<ID>"),
    (re.compile(r"\b[0-9a-f]{64}\b"), "<H64>"),
    (re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?"), "<TS>"),
    (re.compile(r"\d{8}T?_?\d{6}"), "<TSC>"),
]


# ---------------------------------------------------------------------------
# 链路脚本(子进程内执行)
# ---------------------------------------------------------------------------

def _run_chain(mode: str, work: Path) -> dict:
    env_git = {**os.environ, **GIT_ENV}

    def git(cwd: Path, *argv: str) -> str:
        return subprocess.run(["git", *argv], cwd=str(cwd), check=True, capture_output=True, text=True, env=env_git).stdout.strip()

    gov, prod = work / "gov", work / "product"
    for sub in ("pending", "claimed", "completed", "blocked"):
        (gov / "5_tasks" / "queue" / sub).mkdir(parents=True)
    for sub in ("claims", "returns", "audit_dispatches", "audit_verdicts", "finalizations", "closures", "events", "sessions"):
        (gov / "5_tasks" / "records" / sub).mkdir(parents=True)
    for sub in ("5_tasks/policies", "5_tasks/drafts", "task_cards", ".lybra"):
        (gov / sub).mkdir(parents=True, exist_ok=True)
    prod.mkdir()
    git(prod, "init", "-q", "-b", "main")
    (prod / "tools").mkdir()
    (prod / "tools" / "x.py").write_text("x = 1\n", encoding="utf-8")
    git(prod, "add", "-A")
    git(prod, "commit", "-q", "-m", "init")
    # finalize_mode=external: finalization 记录由产物入口经同一 writer 铸(不在靶场做 merge/push/部署)
    (gov / "project.json").write_text(json.dumps({"project": "lybra", "code_repo": str(prod), "config_version": 1,
                                                  "paths": {"finalize_mode": "external"}}), encoding="utf-8")
    (gov / ".lybra" / "role").write_text(json.dumps({"role": "advisor", "instance": DRIVER}), encoding="utf-8")
    conn = gov / ".lybra" / "connection.json"
    conn.write_text(json.dumps({"mcp": {"rpc_url": "http://in-process.invalid/mcp"},
                                "tokens": [{"role": "advisor", "role_class": "advisor", "token": "fixture-not-a-secret",
                                            "scopes": [], "agent_instance": DRIVER}]}), encoding="utf-8")
    os.environ["LYBRA_CONNECTION_JSON"] = str(conn)
    os.environ["AIPOS_WORKSPACE_ROOT"] = str(gov)

    import tools.aipos_cli.confirm_client as confirm_client
    import tools.aipos_cli.frontmatter as fm_mod
    import tools.aipos_cli.record_writer as record_writer
    import tools.aipos_cli.two_phase_shell_factory as two_phase
    from tools.aipos_cli.artifact_ingest import ingest_task_artifact
    from tools.aipos_cli.autonomy_policy import build_autonomy_policy_markdown
    from tools.aipos_cli.draft_writer import create_draft, publish_draft
    from tools.aipos_cli.frontmatter import require_frontmatter
    from tools.aipos_cli.next_resolver import _action_type_for_command, _run_cli_in_process, derive_next_step
    from tools.mcp_server import tools as gate

    blocked = mode == "block"
    try:
        import yaml  # noqa: F401
        yaml_importable = True
    except ImportError:
        yaml_importable = False
    out: dict = {"mode": mode, "yaml_importable": yaml_importable,
                 "frontmatter_yaml_is_none": fm_mod.yaml is None, "record_writer_yaml_is_none": record_writer.yaml is None,
                 "steps": []}

    (gov / "5_tasks" / "policies" / f"{POLICY}.md").write_text(build_autonomy_policy_markdown(
        policy_id=POLICY, agent_or_role=DRIVER, active_from="2026-01-01T00:00:00Z", expires_at="2999-01-01T00:00:00Z",
        max_tasks=20, owner_approval_ref="fixture-envelope", task_selector_task_mode="code"), encoding="utf-8")

    capability = {"token_ref": "fixture-driver", "expires_at": "2999-01-01T00:00:00Z", "role": "advisor",
                  "agent_instance": DRIVER}

    class InProcessGate:
        """临时门: GateClient 的进程内替身——tools/call 直进门的唯一派发口 dispatch_tool(capability = 驱动方 advisor)。"""

        def __init__(self, base_url: str, token: str, *, timeout: float | None = None) -> None:
            self.base_url = base_url

        def initialize(self) -> dict:
            return {}

        def call_tool(self, name: str, arguments: dict, *, timeout: float | None = None) -> dict:
            with gate.request_capability_scope(capability):
                result = gate.dispatch_tool(name, arguments)
            structured = result.get("structuredContent")
            if not isinstance(structured, dict):
                raise confirm_client.GateError(f"tool {name} returned no structuredContent: {result}")
            return structured

        @property
        def token_fingerprint(self) -> str:
            return "fixture"

    confirm_client.GateClient = InProcessGate
    two_phase.GateClient = InProcessGate

    def readback_all() -> list[str]:
        """该步后治理根全部 md 经「必须读出」入口回读; 返回读不出的原文(应为空)。"""
        problems = []
        for path in sorted(gov.rglob("*.md")):
            try:
                require_frontmatter(path)
            except ValueError as exc:
                problems.append(str(exc))
        return problems

    def record(label: str, ok: bool, detail: str) -> None:
        out["steps"].append({"step": label, "ok": bool(ok), "detail": detail, "unreadable": readback_all()})

    def drive(card: str, label: str, expected_verb: str) -> None:
        d = derive_next_step(card, gov)
        if not d.get("derivable") or d.get("verb") != expected_verb:
            record(label, False, f"推导核未到 {expected_verb}: {d.get('verb')} {d.get('missing_records')} {d.get('action')}")
            return
        command = d["command"]
        action = _action_type_for_command(command)
        if "artifact ingest" in command:
            kind = action if action in ("return", "verdict") else None
            res = ingest_task_artifact(card, gov, kind=kind,
                                       execute=lambda dd, root, cj=None: _run_cli_in_process(dd["command"], action))
            record(label, res["ok"], f"{d['verb']}: {res.get('category')} {res.get('message')} {res.get('reasons') if not res['ok'] else ''}")
        else:
            res = _run_cli_in_process(command, action)
            record(label, res["ok"], f"{d['verb']}: {res.get('message')}" + ("" if res["ok"] else f" {res.get('output', '')[-2000:]}"))

    meta = {"task_id": TASK, "title": "零依赖全链路", "project": "lybra", "assigned_to": EXEC, "agent_instance": EXEC,
            "context_bundle": EXEC, "task_mode": "code", "priority": "high", "status": "pending", "created_by": DRIVER,
            "needs_owner": False, "artifact_policy": "formal_write", "model_tier": "L2", "output_target": "tools/",
            "audit": "required", "audit_by": AUDITOR, "governance_refs": ["★依据: 零依赖", "Fix: 冒号 #号 [x] 'q' \"dq\""]}
    draft = create_draft(gov, meta, "# 零依赖全链路\n\n## 验收\n- 跑通\n")
    record("1 草稿创建", bool(draft.get("wrote")) and draft.get("verdict") != "BLOCK", f"draft {draft.get('verdict')}")
    published = publish_draft(gov, gov / draft["target_path"], actor=DRIVER, dry_run=False)
    record("2 发布", published.get("verdict") != "BLOCK" and bool(published.get("target_path")), f"publish {published.get('verdict')}")
    drive(TASK, "3 认领", "lybra_queue_claim_dry_run")

    worktree = prod / ".worktrees" / TASK
    (worktree / "tools" / "x.py").write_text("x = 2\n", encoding="utf-8")
    git(worktree, "add", "-A")
    git(worktree, "commit", "-q", "-m", f"{TASK}: change")
    sha = git(worktree, "rev-parse", "HEAD")
    tree = git(worktree, "rev-parse", "HEAD^{tree}")
    (gov / "task_cards" / TASK / "RETURN.md").write_text(RETURN_TEXT.format(sha=sha, tree=tree, task=TASK), encoding="utf-8")
    drive(TASK, "4 交回 ingest", "lybra_queue_return_dry_run")
    dispatches = sorted((gov / "5_tasks" / "records" / "audit_dispatches" / AUDIT).glob("*.md"))
    record("5 派审(门随交回派生审计卡 + 派审记录)", bool(dispatches), f"dispatch records {len(dispatches)}")
    drive(AUDIT, "6 审计认领", "lybra_queue_claim_dry_run")
    (gov / "task_cards" / AUDIT / "RETURN.md").write_text(VERDICT_TEXT.format(sha=sha, task=TASK, audit=AUDIT), encoding="utf-8")
    drive(AUDIT, "7 裁决 ingest", "lybra_audit_verdict_dry_run")
    fin_dir = gov / "task_cards" / f"{TASK}-FINALIZE"
    fin_dir.mkdir(parents=True, exist_ok=True)
    (fin_dir / "RETURN.md").write_text(FINALIZE_TEXT.format(sha=sha, task=TASK), encoding="utf-8")
    drive(TASK, "8 finalization ingest", "lybra_finalize")
    drive(TASK, "9 close", "lybra_queue_close_dry_run")
    final = derive_next_step(TASK, gov)
    record("10 结案态", final.get("current_node") == "close" and final.get("current_state") == "completed",
           f"{final.get('current_node')}/{final.get('current_state')}")

    def normalize(value: object) -> str:
        text = json.dumps(value, ensure_ascii=False, default=str).replace(str(work), "<WORK>")
        for pattern, repl in _NORMALIZE:
            text = pattern.sub(repl, text)
        return text

    snapshot = {}
    for path in sorted(gov.rglob("*.md")):
        data, body = require_frontmatter(path)
        snapshot[json.loads(normalize(str(path.relative_to(gov))))] = {
            "keys": list(data), "frontmatter": json.loads(normalize(data)), "body": json.loads(normalize(body))}
    out["records"] = snapshot
    published_card = gov / "5_tasks" / "queue" / "completed" / f"{TASK.lower()}.md"
    out["card_task_id_line"] = next((ln for ln in published_card.read_text(encoding="utf-8").splitlines()
                                     if ln.startswith("task_id:")), "")
    out["yaml_in_sys_modules"] = "yaml" in sys.modules
    out["blocked"] = blocked
    return out


# ---------------------------------------------------------------------------
# 夹具(pytest)
# ---------------------------------------------------------------------------

def _chain_in_subprocess(mode: str, work: Path) -> dict:
    work.mkdir(parents=True)
    home = work / "home"
    home.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("LYBRA_", "AIPOS_"))}
    env.update({"HOME": str(home), "PYTHONPATH": str(REPO_ROOT)})
    proc = subprocess.run([sys.executable, str(Path(__file__).resolve()), mode, str(work)], cwd=str(work), env=env,
                          capture_output=True, text=True, timeout=600)
    assert proc.returncode == 0, f"{mode} chain crashed:\n{proc.stdout[-4000:]}\n{proc.stderr[-6000:]}"
    return json.loads((work / "chain.json").read_text(encoding="utf-8"))


def _show(title: str, payload: object) -> None:
    print(f"[F100 件④] {title}: {json.dumps(payload, ensure_ascii=False)}")


def test_f100_item4_zero_dependency_full_chain_matches_pyyaml(tmp_path: Path) -> None:
    blocked = _chain_in_subprocess("block", tmp_path / "block")
    with_yaml = _chain_in_subprocess("yaml", tmp_path / "yaml")

    # 屏蔽确实生效: 全进程 import yaml 失败, 读写两侧都走零依赖路径; 有 yaml 一侧对照组真有 PyYAML
    assert blocked["yaml_importable"] is False and blocked["yaml_in_sys_modules"] is False
    assert blocked["frontmatter_yaml_is_none"] and blocked["record_writer_yaml_is_none"]
    assert with_yaml["yaml_importable"] is True and not with_yaml["record_writer_yaml_is_none"]

    for step in blocked["steps"]:
        _show(f"屏蔽 yaml · {step['step']}", {"ok": step["ok"], "detail": step["detail"][:300], "unreadable": step["unreadable"]})
    # 每步成功, 且每步后治理根全部 md 经「必须读出」入口读出
    assert [s["step"] for s in blocked["steps"]] == [s["step"] for s in with_yaml["steps"]]
    for mode in (blocked, with_yaml):
        for step in mode["steps"]:
            assert step["ok"], f"{mode['mode']} {step['step']}: {step['detail']}"
            assert step["unreadable"] == [], f"{mode['mode']} {step['step']}: {step['unreadable']}"

    # 两种写出器都真被用到: 屏蔽侧卡面是 stdlib 写出器形(双引号), 有 yaml 侧是 safe_dump 形
    _show("卡面 task_id 行(屏蔽 / 有 yaml)", [blocked["card_task_id_line"], with_yaml["card_task_id_line"]])
    assert blocked["card_task_id_line"] == f'task_id: "{TASK}"'
    assert with_yaml["card_task_id_line"] == f"task_id: {TASK}"

    # 全部记录: 同一组文件, 每份 frontmatter(键序 + 值)与正文在规整后逐字相同
    assert sorted(blocked["records"]) == sorted(with_yaml["records"]), (
        sorted(set(blocked["records"]) ^ set(with_yaml["records"])))
    differing = [path for path in blocked["records"] if blocked["records"][path] != with_yaml["records"][path]]
    _show("记录份数 / 不同份数", [len(blocked["records"]), len(differing)])
    for path in sorted(blocked["records"]):
        _show("记录", {"path": path, "keys": len(blocked["records"][path]["keys"]), "same": path not in differing})
    assert differing == [], {p: (blocked["records"][p], with_yaml["records"][p]) for p in differing[:2]}
    for expected in ("5_tasks/drafts/", "5_tasks/queue/completed/f100z-1.md", "5_tasks/queue/completed/f100z-1r.md",
                     "5_tasks/records/claims/F100Z-1/", "5_tasks/records/returns/F100Z-1/",
                     "5_tasks/records/audit_dispatches/F100Z-1R/", "5_tasks/records/audit_verdicts/F100Z-1/",
                     "5_tasks/records/finalizations/F100Z-1/", "5_tasks/records/closures/F100Z-1/",
                     "5_tasks/records/sessions/F100Z-1/", "5_tasks/records/publishes/f100z-1/"):
        assert any(path.startswith(expected) for path in blocked["records"]), f"缺 {expected}"


if __name__ == "__main__":
    work_dir = Path(sys.argv[2])
    result = _run_chain(sys.argv[1], work_dir)
    (work_dir / "chain.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
