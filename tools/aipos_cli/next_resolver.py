"""AIPOS-F71 — lybra next 推导核。

唯一实现:合并 turn-advancer(AIPOS-340)与 next-step(AIPOS-R7A)为单一推导器。
推导只认记录(queue 目录 + records 三查 + 卡 frontmatter),禁读日志/会话残留。
状态不明 → 输出"不可推导 + 缺哪份记录 + 建议动作",禁猜。
token 值永不出现在输出。

推导核从 transitions.schema.json(节点与迁移)+ verbs.schema.json(动词参数配方)
派生含全参数的可照抄命令。命令指向产品 CLI 薄壳(lybra queue claim/return/close
--confirm 等),零本地状态变更逻辑。

两种模式:
- 无参:项目级扫描(队列 → 当前最小待办卡 + 所处节点 + 该谁动)
- --task-id:单卡模式

项目无关:推导全由声明 + 工作区推导,不写死项目名。

AIPOS-F71 返工第5件:所有治理路径经 schema_loader 单一读取口,禁手拼路径字面量。
"""
from __future__ import annotations

import os
import re
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from tools.schema_loader import code_repo_schema_root, resolve_governance_path
from tools.aipos_cli.record_writer import record_dir

# 产品仓根(schema 所在地)。AIPOS-F91(L5): 不再自算, 读唯一实现 schema_loader.code_repo_schema_root
REPO_ROOT = code_repo_schema_root()

# AIPOS-F90 件①: `lybra loop` 启动时已校验的驱动方身份(--actor)与信封(--envelope)贯穿推导与执行——
# 推导核派生账务命令(claim/return/verdict/close)的 owner_policy_ref 与执行体的信封预检同读此处, 禁 loop 收到 --envelope
# 却派生不带 owner_policy_ref 的命令(2026-10-03 F90 活体实撞: 门拒 OWNER_POLICY_REF_REQUIRED)。无 scope 时按工位声明自发现。
_DRIVER_SCOPE: ContextVar[dict[str, str] | None] = ContextVar("lybra_driver_scope", default=None)


@contextmanager
def driver_scope(*, actor: str | None, policy_id: str | None) -> Iterator[None]:
    """推导/执行期间固定驱动方身份与信封(loop 已按 match_claim_envelope 校验过)。嵌套调用(ingest→推导核)同见。"""
    token = _DRIVER_SCOPE.set({"actor": str(actor or "").strip(), "policy_id": str(policy_id or "").strip()})
    try:
        yield
    finally:
        _DRIVER_SCOPE.reset(token)


def _scoped_driver() -> dict[str, str]:
    return _DRIVER_SCOPE.get() or {}


def _resolve_governance_path_with_relative(key: str, governance_root: Path) -> Path:
    """解析治理路径,处理 relative_to 链。
    
    如 records 相对于 tasks_root,需递归解析:
    tasks_root → 5_tasks/
    records → tasks_root + records/ = 5_tasks/records/
    (队列根不经此处: 只读 task_loader.queue_root_for, AIPOS-F89 件① M8)
    """
    from tools.schema_loader import get_governance_path, resolve_governance_path
    
    entry = get_governance_path(key, REPO_ROOT)
    relative_to = entry.get("relative_to")
    
    # governance_root 是终点,直接返回根目录
    if key == "governance_root" or not relative_to:
        # 无 relative_to 或到达根,直接用 path
        rel_path = str(entry.get("path", "")).strip().strip("/")
        if not rel_path:
            # governance_root 没有 path,返回根本身
            return governance_root
        return governance_root / rel_path
    else:
        # 递归解析父路径
        parent_path = _resolve_governance_path_with_relative(relative_to, governance_root)
        # 拼接当前路径
        rel_path = str(entry.get("path", "")).strip().strip("/")
        return parent_path / rel_path

# ---------------------------------------------------------------------------
# AIPOS-F73D: 声明读取口(transitions 节点 / 产品仓根 / worktree 落点 / 驱动方身份)——全部读声明, 禁写死
# ---------------------------------------------------------------------------

_RETURN_PLACEHOLDER_PREFIX = "(待填写"  # record_writer.build_return_skeleton_markdown 骨架占位符前缀(N2.artifact.readiness)


def _transition_node(node_id: str) -> dict[str, Any]:
    """读 transitions.schema.json nodes[<id>](产品根单一源)。缺失 = 声明缺失, 抛 SchemaLoadError(fail-closed)。"""
    from tools.schema_loader import SchemaLoadError, load_schema

    nodes = load_schema("transitions", REPO_ROOT).get("nodes") or {}
    node = nodes.get(node_id) if isinstance(nodes, dict) else None
    if not isinstance(node, dict):
        raise SchemaLoadError(f"transitions.schema.json nodes.{node_id} 未声明")
    return node


def _card_repo_root(workspace_root: Path, task_id: str, card_frontmatter: dict[str, Any] | None = None,
                    *, allow_governance_root: bool = True) -> Path:
    """AIPOS-F78C 件②: 卡所在产品仓 = workspace_config.resolve_card_repo(唯一解析: 卡 lane.repo → project.json repos 清单
    → code_repo 别名 → 治理根)。card_frontmatter 缺则按 task_id 读卡。解析不到 = CardRepoUnresolved(调用方转不可派生/拒, fail-closed)。
    allow_governance_root=False(finalize 派生): 无声明时不把治理根当产品仓(F73D 前置一②)。"""
    from tools.aipos_cli.workspace_config import resolve_card_repo

    fm = card_frontmatter
    if fm is None:
        task_path, _ = _find_task_in_queue(workspace_root, task_id)
        fm = _read_frontmatter(task_path) if task_path else {}
    return resolve_card_repo(workspace_root, {**fm, "task_id": str(fm.get("task_id") or task_id)},
                             allow_governance_root=allow_governance_root)


def _resolve_worktree_root(workspace_root: Path, code_repo: Path) -> Path:
    """AIPOS-F73D 前置三: worktree 落点读声明——config.schema configuration_sources.workspace_config.schema.worktree_root
    (default `{code_repo}/.worktrees`, 产品仓 .gitignore 已声明), 治理根 .lybra/config.json 的 worktree_root 可覆盖。禁写死。"""
    from tools.schema_loader import load_schema
    from tools.aipos_cli.workspace_config import CONFIG_RELATIVE_PATH, load_workspace_config

    decl = (
        load_schema("config", REPO_ROOT)
        .get("configuration_sources", {})
        .get("workspace_config", {})
        .get("schema", {})
        .get("worktree_root", {})
    )
    template = str(decl.get("default") or "").strip()
    if not template:
        from tools.schema_loader import SchemaLoadError

        raise SchemaLoadError("config.schema.json workspace_config.schema.worktree_root.default 未声明")
    override = ""
    cfg_path = workspace_root / CONFIG_RELATIVE_PATH
    if cfg_path.is_file():
        try:
            override = str(load_workspace_config(cfg_path).get("worktree_root") or "").strip()
        except (OSError, ValueError) as exc:
            import sys

            print(f"Warning: {cfg_path} unreadable, using declared default worktree_root: {exc}", file=sys.stderr)
    chosen = override or template
    return Path(chosen.replace("{code_repo}", str(code_repo))).expanduser()


def _artifact_ingest_declaration() -> dict[str, Any]:
    """AIPOS-F78 件④: 读 transitions.schema.json 顶层 artifact_ingest(文件候选/必填 frontmatter 的唯一声明)。缺 = SchemaLoadError。"""
    from tools.schema_loader import SchemaLoadError, load_schema

    decl = load_schema("transitions", REPO_ROOT).get("artifact_ingest")
    if not isinstance(decl, dict) or "return" not in decl or "verdict" not in decl:
        raise SchemaLoadError("transitions.schema.json artifact_ingest(return/verdict 落点与必填字段)未声明")
    return decl


def _project_paths(workspace_root: Path) -> dict[str, Any]:
    """AIPOS-F78 件④: 项目落点声明(project.json paths, 缺省=现行路径)——唯一读取口 workspace_config.project_paths。"""
    from tools.aipos_cli.workspace_config import project_paths

    return project_paths(workspace_root)


def _pick_candidate(directory: Path, candidates: list[str], *, ready: Any = None) -> Path | None:
    """按候选顺序取首个存在的文件; 通配候选取 mtime 最新; ready(path) 谓词可选(骨架/无 verdict 不算)。"""
    if not directory.is_dir():
        return None
    for cand in candidates:
        cand = str(cand)
        if any(ch in cand for ch in "*?["):
            matches = sorted((p for p in directory.glob(cand) if p.is_file()), key=lambda p: p.stat().st_mtime, reverse=True)
        else:
            matches = [directory / cand] if (directory / cand).is_file() else []
        for path in matches:
            if ready is None or ready(path):
                return path
    return None


def return_artifact_dir(workspace_root: Path, task_id: str) -> Path:
    """执行体 Return 落点目录 = <paths.return_root>/<task_id>(声明驱动, lybra 默认 task_cards/<ID>)。"""
    return Path(_project_paths(workspace_root)["return_root"]) / task_id


def find_return_artifact(workspace_root: Path, task_id: str) -> Path | None:
    """AIPOS-F78 件④: 按项目声明找执行体 Return 文件(候选序读 transitions artifact_ingest.return.return_file_candidates)。"""
    cands = list(_artifact_ingest_declaration()["return"].get("return_file_candidates") or [])
    if not cands:
        from tools.schema_loader import SchemaLoadError

        raise SchemaLoadError("transitions.schema.json artifact_ingest.return.return_file_candidates 未声明")
    return _pick_candidate(return_artifact_dir(workspace_root, task_id), cands)


def default_return_file(workspace_root: Path, task_id: str) -> Path:
    """执行体 Return 的声明位默认文件 = <paths.return_root>/<task_id>/<首个非通配候选>(AIPOS-F89 件① H9 唯一推导:
    认领骨架 / 门侧 return_body 落盘 / lybra_return_content 读取同此, 禁写死 task_cards/<ID>/RETURN.md)。候选缺 = SchemaLoadError。"""
    cands = list(_artifact_ingest_declaration()["return"].get("return_file_candidates") or [])
    first = next((str(c) for c in cands if not any(ch in str(c) for ch in "*?[")), None)
    if first is None:
        from tools.schema_loader import SchemaLoadError

        raise SchemaLoadError("transitions.schema.json artifact_ingest.return.return_file_candidates 无非通配候选")
    return return_artifact_dir(workspace_root, task_id) / first


def _return_artifact_path(workspace_root: Path, task_id: str) -> Path:
    """执行体产物路径: 已落盘的 Return 文件; 未落盘时返回声明位默认文件(default_return_file)。"""
    found = find_return_artifact(workspace_root, task_id)
    if found is not None:
        return found
    return default_return_file(workspace_root, task_id)


def executor_artifact_watch(workspace_root: Path, task_id: str) -> tuple[Path, list[str]]:
    """AIPOS-F78: loop 等待执行体产物的 (watch 根, 相对 glob 列表)——落点读声明; 声明根在治理根外时以落点根为 watch 根。"""
    directory = return_artifact_dir(workspace_root, task_id)
    cands = [str(c) for c in (_artifact_ingest_declaration()["return"].get("return_file_candidates") or [])]
    try:
        rel = directory.resolve().relative_to(Path(workspace_root).resolve())
        return Path(workspace_root), [str(rel / c) for c in cands]
    except ValueError:
        return directory.parent, [f"{directory.name}/{c}" for c in cands]


def auditor_artifact_watch(workspace_root: Path, audit_task_id: str) -> tuple[Path, list[str]]:
    """AIPOS-F89 件① H9: loop 等待审计报告的 (watch 根, 相对 glob 列表)——与 executor_artifact_watch 同构: 落点根读项目声明
    paths.verdict_root(verdict_artifact_dir), 文件候选读 transitions artifact_ingest.verdict.verdict_file_candidates;
    原读 transitions N4.audit_report.location_candidates(写死 task_cards/{audit_task_id}/…, 第二份声明)已删。"""
    directory = verdict_artifact_dir(workspace_root, audit_task_id)
    cands = [str(c) for c in (_artifact_ingest_declaration()["verdict"].get("verdict_file_candidates") or [])]
    if not cands:
        from tools.schema_loader import SchemaLoadError

        raise SchemaLoadError("transitions.schema.json artifact_ingest.verdict.verdict_file_candidates 未声明")
    try:
        rel = directory.resolve().relative_to(Path(workspace_root).resolve())
        return Path(workspace_root), [str(rel / c) for c in cands]
    except ValueError:
        return directory.parent, [f"{directory.name}/{c}" for c in cands]


def verdict_artifact_dir(workspace_root: Path, audit_task_id: str) -> Path:
    """审计报告落点目录 = <paths.verdict_root>/<audit_task_id>(声明驱动, lybra 默认 task_cards/<ID>R)。"""
    return Path(_project_paths(workspace_root)["verdict_root"]) / audit_task_id


def _audit_report_candidates(workspace_root: Path, audit_task_id: str) -> list[Path]:
    """审计体产物候选: 落点根读项目声明(verdict_root), 文件候选读 transitions artifact_ingest.verdict.verdict_file_candidates
    (RETURN.md 优先, 回退 audit_report.md; N4.audit_report.location_ref 指向同一声明)。"""
    directory = verdict_artifact_dir(workspace_root, audit_task_id)
    cands = list(_artifact_ingest_declaration()["verdict"].get("verdict_file_candidates") or [])
    out: list[Path] = []
    for cand in cands:
        cand = str(cand)
        if any(ch in cand for ch in "*?["):
            if directory.is_dir():
                out.extend(sorted((p for p in directory.glob(cand) if p.is_file()), key=lambda p: p.stat().st_mtime, reverse=True))
        else:
            out.append(directory / cand)
    return out


def audit_report_artifact_path(workspace_root: Path, audit_task_id: str) -> Path:
    """AIPOS-F66B 件③: 审计报告落点的唯一读取口 = 已落盘的报告文件; 未落盘时返回声明位默认文件
    (<paths.verdict_root>/<审计卡ID>/<首个非通配候选>)。与 _return_artifact_path 同构:
    落点根读项目声明 verdict_root, 文件候选读 transitions artifact_ingest.verdict.verdict_file_candidates。
    派生审计卡文案 / card render / ingest 全部经此, 禁写死 task_cards/<被审卡ID>/。"""
    for path in _audit_report_candidates(workspace_root, audit_task_id):
        if path.is_file():
            return path
    cands = list(_artifact_ingest_declaration()["verdict"].get("verdict_file_candidates") or [])
    first = next((str(c) for c in cands if not any(ch in str(c) for ch in "*?[")), None)
    if first is None:
        from tools.schema_loader import SchemaLoadError

        raise SchemaLoadError("transitions.schema.json artifact_ingest.verdict.verdict_file_candidates 无非通配候选")
    return verdict_artifact_dir(workspace_root, audit_task_id) / first


# ---------------------------------------------------------------------------
# AIPOS-F86 件①: 工位开工面单源——卡工作树落点 / 报告落点的唯一推导(claim 建树、card render、写边界、my-tasks 同函数)
# ---------------------------------------------------------------------------

def forensic_subject(card_frontmatter: dict[str, Any] | None) -> str | None:
    """AIPOS-F89 件③a: 审计卡(task_mode=audit)的被审卡 ID(卡面 reviewed_task_id → derived_from); 非审计卡 = None;
    审计卡缺两字段 = ""(建取证树时 fail-closed)。"""
    fm = card_frontmatter if isinstance(card_frontmatter, dict) else {}
    if str(fm.get("task_mode") or "").strip().lower() != "audit":
        return None
    return str(fm.get("reviewed_task_id") or fm.get("derived_from") or "").strip()


def card_needs_worktree(workspace_root: Path, card_frontmatter: dict[str, Any] | None) -> bool:
    """认领是否须建卡工作树(唯一判据, 门认领 queue_mutation 与 loop 认领核验同读): 代码卡 = 卡分支工作树;
    AIPOS-F89 件③a: 审计卡且被审卡为代码卡(或被审卡查不到 = fail-closed 按需建) = 被审分支 tip 的只读 detached 取证工作树。
    其余(非代码卡及其审计卡)不建树(与 F90 前语义一致)。"""
    fm = card_frontmatter if isinstance(card_frontmatter, dict) else {}
    mode = str(fm.get("task_mode") or "").strip().lower()
    if mode == "code":
        return True
    reviewed = forensic_subject(fm)
    if not reviewed:
        return reviewed == ""  # 审计卡缺被审卡字段 → 需要(建树时给出拒因); 非审计卡 → 不需要
    task_path, _ = _find_task_in_queue(workspace_root, reviewed)
    if task_path is None:
        return True
    return str(_read_frontmatter(task_path).get("task_mode") or "code").strip().lower() == "code"


def card_worktree_location(workspace_root: Path, task_id: str,
                           card_frontmatter: dict[str, Any] | None = None) -> tuple[Path, Path]:
    """卡工作树落点的唯一推导 = (产品仓, 工作树路径)。

    产品仓 = _card_repo_root(workspace_config.resolve_card_repo 唯一解析: 卡 lane.repo → project.json repos → code_repo 别名),
    工作树 = _resolve_worktree_root(config.schema worktree_root 声明, 治理根 .lybra/config.json 可覆盖) / <task_id>。
    AIPOS-F89 件③a: 审计卡的产品仓 = 被审卡的仓(取证工作树建在被审分支所在仓), 路径仍按审计卡 ID(<worktree_root>/<审计卡ID>)。
    claim 建树(_ensure_worktree)/ card render / write_boundary / my-tasks 开工面全部经此; 工位侧(go.ts)禁第二处拼接
    (F73B 原 `<治理根>/card/<ID>` 之误)。解析不到 = CardRepoUnresolved / SchemaLoadError 向上抛, 调用方 fail-closed。
    """
    fm = card_frontmatter
    if fm is None:
        task_path, _ = _find_task_in_queue(workspace_root, task_id)
        fm = _read_frontmatter(task_path) if task_path else {}
    reviewed = forensic_subject(fm)
    if reviewed:
        code_repo = _card_repo_root(workspace_root, reviewed)
    else:
        code_repo = _card_repo_root(workspace_root, task_id, fm)
    return code_repo, _resolve_worktree_root(workspace_root, code_repo) / task_id


def card_report_path(workspace_root: Path, task_id: str, card_frontmatter: dict[str, Any] | None = None) -> Path:
    """卡报告落点的唯一读取口: 审计卡(task_mode=audit)= audit_report_artifact_path(F66B 件③, 与
    audit_derivation.render_audit_report_location 同一读取口); 执行卡 = _return_artifact_path(project.json paths.return_root +
    transitions artifact_ingest.return.return_file_candidates)。card render / my-tasks 同此函数, 禁写死 task_cards/<ID>/。"""
    fm = card_frontmatter
    if fm is None:
        task_path, _ = _find_task_in_queue(workspace_root, task_id)
        fm = _read_frontmatter(task_path) if task_path else {}
    if str(fm.get("task_mode") or "").strip().lower() == "audit":
        return audit_report_artifact_path(workspace_root, task_id)
    return _return_artifact_path(workspace_root, task_id)


def card_workstation_view(workspace_root: Path, task_id: str, card_frontmatter: dict[str, Any] | None) -> dict[str, Any]:
    """`lybra my-tasks --json` 每张 claimed 卡的开工面字段(工位 /go 只读这些字段, 不自行推导)。

    - worktree_path: 推导出的工作树路径(不可推导 = None)
    - worktree_exists: 该路径是否已在盘上(认领时由驱动方建)
    - worktree_refusal: None | {code, reason} —— 不可推导(CardRepoUnresolved.code / WORKTREE_ROOT_UNDECLARED)
      或尚未建立(WORKTREE_NOT_CREATED); 从不输出空串
    - report_path / report_refusal: 同构(REPORT_LOCATION_UNDECLARED)
    - report_required_frontmatter: 报告必填字段 [{key, hint, value}](AIPOS-F93 件①, card_report_contract); 不可推导 =
      report_refusal REPORT_CONTRACT_UNRESOLVED 且 report_path 置空
    """
    from tools.aipos_cli.workspace_config import CardRepoUnresolved
    from tools.schema_loader import SchemaLoadError

    fm = card_frontmatter if isinstance(card_frontmatter, dict) else {}
    view: dict[str, Any] = {
        "worktree_path": None,
        "worktree_exists": False,
        "worktree_refusal": None,
        "report_path": None,
        "report_refusal": None,
    }
    task_id = str(task_id or "").strip()
    if not task_id:
        missing = {"code": "TASK_ID_MISSING", "reason": "卡 frontmatter 无 task_id, 工作树/报告落点按卡 ID 推导, 不可推导"}
        return {**view, "worktree_refusal": missing, "report_refusal": missing}
    try:
        _code_repo, worktree = card_worktree_location(workspace_root, task_id, fm)
    except CardRepoUnresolved as exc:
        view["worktree_refusal"] = {"code": exc.code, "reason": exc.reason}
    except (SchemaLoadError, OSError, ValueError) as exc:
        view["worktree_refusal"] = {"code": "WORKTREE_ROOT_UNDECLARED", "reason": f"工作树落点声明读取失败: {exc}"}
    else:
        view["worktree_path"] = str(worktree)
        view["worktree_exists"] = worktree.is_dir()
        if not view["worktree_exists"]:
            view["worktree_refusal"] = {
                "code": "WORKTREE_NOT_CREATED",
                "reason": f"工作树 {worktree} 尚未建立: 认领由驱动方完成并建树, 工位等待驱动方完成认领; 持续存在按 block-and-report 上报",
            }
    try:
        view["report_path"] = str(card_report_path(workspace_root, task_id, fm))
    except (SchemaLoadError, OSError, ValueError) as exc:
        view["report_refusal"] = {"code": "REPORT_LOCATION_UNDECLARED", "reason": f"报告落点声明读取失败: {exc}"}
        return view
    # AIPOS-F93 件①: 报告必填字段(声明单源 report_frontmatter_contract); 审计卡附被审 tip/tree 实值(取证工作树 HEAD)。
    # 不可推导 = 报告契约拒因(report_path 置空, select_next_card 按报告不可推导排除), 不出无清单的开工提示。
    try:
        view["report_required_frontmatter"] = card_report_contract(
            workspace_root, task_id, fm, worktree=Path(view["worktree_path"]) if view["worktree_exists"] else None)
    except (SchemaLoadError, OSError, ValueError) as exc:
        view["report_path"] = None
        view["report_refusal"] = {"code": "REPORT_CONTRACT_UNRESOLVED", "reason": f"报告必填字段不可推导: {exc}"}
    return view


def card_report_contract(workspace_root: Path, task_id: str, card_frontmatter: dict[str, Any] | None,
                         *, worktree: Path | None = None, branch_pattern: str | None = None) -> list[dict[str, Any]]:
    """AIPOS-F93 件①: 一张卡的报告必填 frontmatter 契约(my-tasks 开工面 / 认领模板同读)。

    执行卡 = return 契约(分支 = 本卡分支); 审计卡 = verdict 契约(分支 = 被审卡分支), 取证工作树已建时附被审 tip/tree 实值
    (= 取证工作树 HEAD, 认领时由门建于被审分支 tip)。取证工作树在盘但读不出 HEAD = ValueError(fail-closed, 不给空值)。"""
    fm = card_frontmatter if isinstance(card_frontmatter, dict) else {}
    reviewed = forensic_subject(fm)
    if reviewed is None:
        return report_frontmatter_contract("return", branch_task_id=task_id, branch_pattern=branch_pattern)
    subject = forensic_worktree_subject(worktree) if worktree is not None else None
    return report_frontmatter_contract("verdict", branch_task_id=reviewed or None, subject=subject, branch_pattern=branch_pattern)


def forensic_worktree_subject(worktree: Path) -> dict[str, str]:
    """取证工作树 HEAD 的 {commit_sha, tree_hash}(只读 git rev-parse)。读不出 = ValueError(含 git 原文)。"""
    import subprocess

    out: dict[str, str] = {}
    for key, rev in (("commit_sha", "HEAD"), ("tree_hash", "HEAD^{tree}")):
        try:
            proc = subprocess.run(["git", "-C", str(worktree), "rev-parse", rev], capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError) as exc:
            raise ValueError(f"取证工作树 {worktree} git rev-parse {rev} 失败: {exc}") from exc
        value = proc.stdout.strip()
        if proc.returncode != 0 or not value:
            raise ValueError(f"取证工作树 {worktree} git rev-parse {rev} 失败: {(proc.stderr or proc.stdout).strip()}")
        out[key] = value
    return out


# ---------------------------------------------------------------------------
# AIPOS-F87 件③: 开工选卡由产品给出——选卡判据唯一声明(my-tasks 输出 next_card; 工位 go.ts 只读, 禁另判/禁取首张)
# ---------------------------------------------------------------------------

NEXT_CARD_RULE: dict[str, Any] = {
    "source": "AIPOS-F87 件③(Owner 2026-10-02 裁定: 开工选卡由产品给出); 唯一实现 next_resolver.select_next_card",
    "eligible": [
        "queue_state == claimed",
        "卡面 frontmatter 可解析(产品唯一读取口 parse_markdown_frontmatter 无告警)",
        "worktree_exists == true(工作树由驱动方认领时建立; AIPOS-F89 件③a: 审计卡 = 被审分支 tip 的只读 detached 取证工作树)",
        "report_path 可推导(开工提示必需的报告落点)",
        "report_required_frontmatter 可推导(AIPOS-F93 件①: 报告必填字段, 声明 transitions artifact_ingest 单源; 审计卡附被审 tip/tree)",
        "kickoff_refusal 为空(AIPOS-F90 件③: 本实例在办、未结案、产物未交——判据 next_resolver.kickoff_refusal)",
        "kickoff 可渲染(AIPOS-F95 件①: 开工提示按 verbs.schema lybra_my_tasks.kickoff 声明渲染, next_resolver.render_kickoff)",
    ],
    "order": "claimed_at 最近优先; claimed_at 缺失/不可解析者排最后; 同时刻按 task_id 升序",
}


KICKOFF_REFUSAL_CODES: dict[str, str] = {
    "NOT_FOUND": "队列中找不到该卡",
    "NOT_CLAIMED": "卡不在 claimed(未认领/已阻塞/已撤销), 不能开工",
    "CONCLUDED": "卡已结案(completed 或有 closure 记录), 不能再开工",
    "NOT_MINE": "卡的认领实例不是本实例, 不能开工",
    "NOT_YOUR_TURN": "卡当前不在本角色的工作节点(如已交回待派审/审计中), 不能开工",
    "ARTIFACT_SUBMITTED": "本角色的产物已落盘待产品入门, 不再开工(防覆盖原报告)",
    "FRONTMATTER_UNREADABLE": "卡或其记录的 frontmatter 读不出, 不能开工(AIPOS-F100: 读不出即拒)",
    "RETURN_STALE": "交回已过期, 等驱动方重交回(被审卡分支在交回后又前进, 本轮审的不是当前产物; AIPOS-F114)",
    "ROUND_SUPERSEDED": "本轮审计已被下一轮取代, 不再开工(AIPOS-F114)",
}


def kickoff_refusal(workspace_root: Path, task_id: str, actor: str, *, queue_state: str | None,
                    card_frontmatter: dict[str, Any] | None = None) -> dict[str, str] | None:
    """AIPOS-F90 件③: 开工核验唯一判据(/go 选卡与「以卡号/路径冷启动」同读)——本实例在办、未结案、产物未交才可开工。

    事实只读: 队列位置 + 记录(claim 记录实例 / closure) + 推导核结论(triggered_by / derivable), 禁另判。
    返回 None(可开工) 或 {code, reason}(code ∈ KICKOFF_REFUSAL_CODES; 文案零门动词, 工位原样转述)。
    2026-10-02 事故: 审计会话被贴错卡号, 对已结案卡重审并覆盖原报告 → 已结案/产物已交一律拒。"""
    task_id = str(task_id or "").strip()
    fm = card_frontmatter if isinstance(card_frontmatter, dict) else {}

    def refuse(code: str, detail: str) -> dict[str, str]:
        return {"task_id": task_id, "code": code, "reason": f"{KICKOFF_REFUSAL_CODES[code]}: {detail}"}

    if not queue_state:
        return refuse("NOT_FOUND", f"{task_id} 不在任何队列目录")
    from tools.aipos_cli.frontmatter import FrontmatterReadError

    try:
        records = _read_task_records(workspace_root, task_id)
    except FrontmatterReadError as exc:
        return refuse("FRONTMATTER_UNREADABLE", str(exc))
    if queue_state == "completed" or records.get("latest_closure"):
        return refuse("CONCLUDED", f"{task_id} queue_state={queue_state}")
    if queue_state != "claimed":
        return refuse("NOT_CLAIMED", f"{task_id} queue_state={queue_state}")
    claimer = _claimer_instance(records) or str(fm.get("claimed_by") or "").strip()
    if claimer and actor and claimer != actor:
        return refuse("NOT_MINE", f"{task_id} 认领实例={claimer}, 本实例={actor}")
    derivation = derive_next_step(task_id, workspace_root)
    if (derivation.get("action") or {}).get("type") == "frontmatter_unreadable":
        return refuse("FRONTMATTER_UNREADABLE", "; ".join(derivation.get("missing_records") or []))
    from tools.aipos_cli.audit_derivation import is_audit_card

    role = "auditor" if is_audit_card(task_id, fm) else "executor"  # AIPOS-F112: 审计卡判据唯一实现(认得 R2/R3…)
    # AIPOS-F114 件②: 审计轮作废 / 所审交回已过期 = 专属拒因(推导核 _audit_round_guard 同一判据), 不让审计体审旧 tip
    if isinstance(derivation.get("superseded_by"), dict):
        return refuse("ROUND_SUPERSEDED", f"{task_id} {derivation.get('suggested_action') or ''}")
    if isinstance(derivation.get("return_stale"), dict) and role == "auditor":
        stale = derivation["return_stale"]
        return refuse("RETURN_STALE", f"{task_id} {stale.get('reason')}; {derivation.get('suggested_action') or ''}")
    if derivation.get("triggered_by") != role:
        return refuse("NOT_YOUR_TURN", f"{task_id} 推导核当前节点 {derivation.get('current_node')}/{derivation.get('current_state')}, "
                                       f"该动的是 {derivation.get('triggered_by')}({derivation.get('suggested_action') or ''})")
    if derivation.get("derivable") and derivation.get("verb") in ("lybra_queue_return_dry_run", "lybra_audit_verdict_dry_run"):
        return refuse("ARTIFACT_SUBMITTED", f"{task_id} 的{'审计报告' if role == 'auditor' else ' Return'}已落盘, 产品将自动入门")
    return None


def _claimed_at_sort_key(value: Any) -> float | None:
    from datetime import datetime, timezone

    if isinstance(value, datetime):
        moment = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return moment.timestamp()
    text = str(value or "").strip()
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)).timestamp()


class KickoffRenderError(ValueError):
    """AIPOS-F95 件①: 开工提示渲染失败(声明缺 / 占位缺值 / 报告必填字段形变)。"""


_KICKOFF_PLACEHOLDER_RE = re.compile(r"\{([a-z_]+)\}")


def kickoff_declaration() -> dict[str, Any]:
    """verbs.schema lybra_my_tasks.kickoff(开工提示唯一声明)。缺 = KickoffRenderError(fail-closed)。"""
    from tools.schema_loader import SchemaLoadError, load_schema

    try:
        decl = ((load_schema("verbs", REPO_ROOT).get("verbs") or {}).get("lybra_my_tasks") or {}).get("kickoff")
    except SchemaLoadError as exc:
        raise KickoffRenderError(f"verbs.schema.json 读取失败: {exc}") from exc
    if not isinstance(decl, dict) or not isinstance(decl.get("template"), str) or not isinstance(decl.get("report_field_line"), dict):
        raise KickoffRenderError("verbs.schema.json verbs.lybra_my_tasks.kickoff(template / report_field_line)未声明")
    return decl


def render_kickoff(next_card: dict[str, Any], *, remote: dict[str, Any] | None = None) -> str:
    """AIPOS-F95 件①: 按声明把 next_card 渲染为完整开工提示(唯一实现; 工位 /go 原样发送, loop 拉起原样传入)。

    报告必填字段每项须为 {key: 非空串, hint: 串, value: 串|None}; 列表空/项形变/占位缺值 = KickoffRenderError。
    占位单遍替换(值内的花括号不再被解析)。
    AIPOS-F110 件②: remote = 跨机工位上下文 {gate_host, governance_root, workstation_host, gate_ssh_alias, material_access}
    (loop 按 land 事件 + project.json workstations 声明给出) → 按声明 remote_material 渲染门机材料段插入模板 {remote_material};
    remote=None(本机工位 / 工位 /go)= 空串, 与 F110 前逐字节相同。"""
    decl = kickoff_declaration()
    line_decl = decl["report_field_line"]
    contract = next_card.get("report_required_frontmatter")
    if not isinstance(contract, list) or not contract:
        raise KickoffRenderError("report_required_frontmatter 缺或为空")
    field_lines: list[str] = []
    for item in contract:
        if not isinstance(item, dict) or not isinstance(item.get("key"), str) or not item.get("key") or not isinstance(item.get("hint"), str):
            raise KickoffRenderError(f"report_required_frontmatter 项形变: {item!r}")
        value = item.get("value")
        if value is not None and not isinstance(value, str):
            raise KickoffRenderError(f"report_required_frontmatter[{item['key']}].value 非串: {value!r}")
        line_template = line_decl.get("with_value" if value else "without_value")
        if not isinstance(line_template, str) or not line_template:
            raise KickoffRenderError("verbs.schema.json lybra_my_tasks.kickoff.report_field_line 缺 with_value/without_value")
        entry = {"key": item["key"], "hint": item["hint"], "value": value or ""}
        field_lines.append(_KICKOFF_PLACEHOLDER_RE.sub(lambda m: _kickoff_value(entry, m.group(1)), line_template))
    values = {
        "task_id": next_card.get("task_id"),
        "worktree_path": next_card.get("worktree_path"),
        "report_path": next_card.get("report_path"),
        "card_path": next_card.get("card_path"),
        "report_field_lines": "\n".join(field_lines),
        "remote_material": "",
    }
    if remote is not None:
        material_template = decl.get("remote_material")
        if not isinstance(material_template, str) or not material_template:
            raise KickoffRenderError("verbs.schema.json verbs.lybra_my_tasks.kickoff.remote_material 未声明")
        if not isinstance(remote, dict):
            raise KickoffRenderError(f"remote 上下文须为对象: {remote!r}")
        values["remote_material"] = _KICKOFF_PLACEHOLDER_RE.sub(lambda m: _kickoff_value(remote, m.group(1)), material_template)
    return _KICKOFF_PLACEHOLDER_RE.sub(lambda m: _kickoff_value(values, m.group(1)), decl["template"])


def remote_kickoff_context(workspace_root: Path, instance: str) -> dict[str, str]:
    """AIPOS-F110 件②: 跨机工位开工提示的门机材料上下文(render_kickoff remote=)。门机读不到远端工位目录, 身份 = land 事件实例:
    位置 = enrollment.workstation_location(该实例最新 land 事件, 须 transport=remote), 材料 = workspace_config.project_workstation
    (project.json workstations.<实例>)。不可得 = ValueError(含 WORKSTATION_MATERIAL_* 拒因码 / 位置拒因; fail-closed)。"""
    import socket

    from tools.aipos_cli.enrollment import workstation_location
    from tools.aipos_cli.workspace_config import project_workstation
    from tools.schema_loader import SchemaLoadError

    try:
        loc = workstation_location(workspace_root, instance)
    except SchemaLoadError as exc:
        raise ValueError(f"工位位置声明读取失败: {exc}") from exc
    if not loc.get("found"):
        raise ValueError(f"实例 {instance} 工位位置定位不到: {loc.get('reason')}")
    if loc.get("transport") != "remote":
        raise ValueError(f"实例 {instance} 工位 transport={loc.get('transport')}(非跨机), 不附门机材料段")
    try:
        material = project_workstation(workspace_root, instance)
    except SchemaLoadError as exc:
        raise ValueError(f"跨机工位材料声明读取失败: {exc}") from exc
    return {"gate_host": socket.gethostname(), "governance_root": str(Path(workspace_root).resolve()),
            "workstation_host": str(loc.get("host") or ""), **material}


def _kickoff_value(values: dict[str, Any], name: str) -> str:
    if name not in values:
        raise KickoffRenderError(f"开工提示模板占位 {{{name}}} 未知")
    value = values[name]
    if not isinstance(value, str) or (not value and name not in ("value", "hint", "remote_material")):
        raise KickoffRenderError(f"开工提示占位 {{{name}}} 缺值")
    return value


def select_next_card(cards: list[dict[str, Any]], *, remote: dict[str, Any] | None = None) -> dict[str, Any]:
    """按 NEXT_CARD_RULE 从 my-tasks 的卡视图中选出当前应开工的卡(纯函数)。

    每项卡视图字段: task_id / queue_state / card_path / worktree_path / worktree_exists / worktree_refusal /
    report_path / report_refusal / claimed_at / frontmatter_warnings(card_workstation_view + 卡面解析告警)。
    返回 {next_card: None | {task_id, card_path, worktree_path, report_path, report_required_frontmatter, claimed_at},
          next_card_excluded: [{task_id, code, reason}](每张未入选的 claimed 卡为何不入选), next_card_rule}。
    拒因文案只陈述事实与上报出口, 零门动词(工位原样转述)。
    AIPOS-F110: remote = 跨机工位门机材料上下文(remote_kickoff_context), 原样交 render_kickoff(本机 = None, 提示逐字节不变)。
    """
    eligible: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for card in cards:
        if card.get("queue_state") != "claimed":
            continue
        task_id = str(card.get("task_id") or "")
        kickoff = card.get("kickoff_refusal") if isinstance(card.get("kickoff_refusal"), dict) else None
        warnings = [str(w) for w in (card.get("frontmatter_warnings") or [])]
        if warnings:
            # 卡面读不出是根因, 先于开工核验(AIPOS-F100 件②: 开工核验对读不出的卡面另给 FRONTMATTER_UNREADABLE, 同一事实)
            excluded.append({
                "task_id": task_id,
                "code": "FRONTMATTER_INVALID",
                "reason": f"卡面 frontmatter 不可解析({warnings[0]}), 不能开工; 卡面由顾问经产品规整, 工位按 block-and-report 上报",
            })
            continue
        if kickoff:
            # AIPOS-F90 件③: 非本人在办 / 已结案 / 产物已交 → 不入选(拒因原样转述, 零门动词)
            excluded.append({"task_id": task_id, "code": str(kickoff.get("code") or "NOT_YOUR_TURN"), "reason": str(kickoff.get("reason") or "")})
            continue
        refusal = card.get("worktree_refusal") if isinstance(card.get("worktree_refusal"), dict) else {}
        if not card.get("worktree_path"):
            excluded.append({
                "task_id": task_id,
                "code": str(refusal.get("code") or "WORKTREE_UNRESOLVED"),
                "reason": f"工作树不可推导: {refusal.get('reason') or '产品未给出拒因'}; 按 block-and-report 上报",
            })
            continue
        if card.get("worktree_exists") is not True:
            excluded.append({
                "task_id": task_id,
                "code": str(refusal.get("code") or "WORKTREE_NOT_CREATED"),
                "reason": str(refusal.get("reason") or f"工作树 {card.get('worktree_path')} 尚未建立"),
            })
            continue
        if not card.get("report_path"):
            report_refusal = card.get("report_refusal") if isinstance(card.get("report_refusal"), dict) else {}
            excluded.append({
                "task_id": task_id,
                "code": str(report_refusal.get("code") or "REPORT_LOCATION_UNDECLARED"),
                "reason": f"报告落点不可推导: {report_refusal.get('reason') or '产品未给出拒因'}; 按 block-and-report 上报",
            })
            continue
        contract = card.get("report_required_frontmatter")
        if not isinstance(contract, list) or not contract:
            # AIPOS-F93 件①: 开工提示必须带报告必填字段(声明单源), 缺 = 不开工(fail-closed, 不出无清单的开工提示)
            excluded.append({
                "task_id": task_id,
                "code": "REPORT_CONTRACT_UNRESOLVED",
                "reason": "报告必填字段不可推导(产品未给出 report_required_frontmatter); 按 block-and-report 上报",
            })
            continue
        eligible.append(card)

    def order_key(card: dict[str, Any]) -> tuple[int, float, str]:
        ts = _claimed_at_sort_key(card.get("claimed_at"))
        return (1 if ts is None else 0, -(ts or 0.0), str(card.get("task_id") or ""))

    eligible.sort(key=order_key)
    next_card = None
    if eligible:
        chosen = eligible[0]
        claimed_at = chosen.get("claimed_at")
        next_card = {
            "task_id": str(chosen.get("task_id") or ""),
            "card_path": chosen.get("card_path"),
            "worktree_path": chosen.get("worktree_path"),
            "report_path": chosen.get("report_path"),
            # AIPOS-F93 件①: 报告必填字段(声明 artifact_ingest.<kind>.required_frontmatter 单源渲染; 审计卡带被审 tip/tree 实值),
            # 工位 go.ts 开工提示只读本字段原样列出
            "report_required_frontmatter": chosen.get("report_required_frontmatter"),
            "claimed_at": claimed_at.isoformat() if hasattr(claimed_at, "isoformat") else claimed_at,
        }
        # AIPOS-F95 件①: 完整开工提示由产品按声明渲染(工位 /go 与 loop 拉起同读本字段, 逐字节相同); 渲染不出 = 不开工(fail-closed)
        try:
            next_card["kickoff"] = render_kickoff(next_card, remote=remote)
        except KickoffRenderError as exc:
            excluded.append({"task_id": next_card["task_id"], "code": "KICKOFF_UNRESOLVED",
                             "reason": f"开工提示不可渲染: {exc}; 按 block-and-report 上报"})
            next_card = None
    if next_card is not None:
        for other in eligible[1:]:
            excluded.append({
                "task_id": str(other.get("task_id") or ""),
                "code": "NOT_MOST_RECENT",
                "reason": f"可开工, 但认领时间不是最近(排在 {next_card['task_id']} 之后)",
            })
    return {"next_card": next_card, "next_card_excluded": excluded, "next_card_rule": NEXT_CARD_RULE}


def _finalize_mode(workspace_root: Path) -> str:
    """AIPOS-F78B 件②: finalize 场地声明(project.json paths.finalize_mode, config.schema 声明表; 缺省 internal)。"""
    return str(_project_paths(workspace_root).get("finalize_mode") or "internal")


def _finalization_ingest_declaration() -> dict[str, Any]:
    """AIPOS-F78B 件②: transitions artifact_ingest.finalization(FINALIZE 卡 ID 规则 + Return 必填字段)唯一声明; 缺 = SchemaLoadError。"""
    from tools.schema_loader import SchemaLoadError, load_schema

    decl = (load_schema("transitions", REPO_ROOT).get("artifact_ingest") or {}).get("finalization")
    if not isinstance(decl, dict) or not isinstance(decl.get("finalize_task_id"), dict) or not decl.get("required_frontmatter"):
        raise SchemaLoadError("transitions.schema.json artifact_ingest.finalization(FINALIZE 卡 ID 规则/Return 必填字段)未声明")
    return decl


def finalize_task_id_for(task_id: str, task_fm: dict[str, Any]) -> str:
    """AIPOS-F78B 件②: 外部 FINALIZE 卡 ID = 卡面 <card_field> 声明优先, 缺省 <task_id><suffix>(规则读 artifact_ingest.finalization.finalize_task_id)。"""
    rule = _finalization_ingest_declaration()["finalize_task_id"]
    declared = str(task_fm.get(str(rule.get("card_field") or "finalize_task_id")) or "").strip()
    if declared:
        return declared
    suffix = str(rule.get("suffix") or "").strip()
    if not suffix:
        from tools.schema_loader import SchemaLoadError

        raise SchemaLoadError("transitions.schema.json artifact_ingest.finalization.finalize_task_id.suffix 未声明")
    return f"{task_id}{suffix}"


def missing_finalization_frontmatter(frontmatter: dict[str, Any]) -> list[str]:
    """外部 FINALIZE Return 缺失的必填 frontmatter(声明: artifact_ingest.finalization.required_frontmatter)。"""
    missing = []
    for key in [str(k) for k in _finalization_ingest_declaration()["required_frontmatter"]]:
        value = str(frontmatter.get(key) or "").strip()
        if not value or value.startswith(_RETURN_PLACEHOLDER_PREFIX) or value.startswith("<"):
            missing.append(key)
    return missing


def invalid_finalization_frontmatter(frontmatter: dict[str, Any]) -> list[str]:
    """外部 FINALIZE Return 不合规项(唯一判据, 推导核与 artifact ingest 共用): 必填缺项 + deploy_status 不在 N5.record.deploy_status.values。"""
    problems = [f"FINALIZE Return frontmatter 缺 {k}" for k in missing_finalization_frontmatter(frontmatter)]
    values = [str(v) for v in (_transition_node("N5").get("record", {}).get("deploy_status", {}).get("values") or [])]
    status = str(frontmatter.get("deploy_status") or "").strip()
    if status and values and status not in values:
        problems.append(f"FINALIZE Return deploy_status={status!r} 不在声明值域 {values}(transitions N5.record.deploy_status.values)")
    return problems


def _derive_external_finalize(
    workspace_root: Path,
    task_id: str,
    fm: dict[str, Any],
    *,
    claimer: str,
    verdict_result: str,
) -> dict[str, Any]:
    """AIPOS-F78B 件②: finalize_mode=external 的 N4→N5——不派生 `lybra finalize`, 等外部 FINALIZE 卡的 Return 落盘
    (落点 = return_root/<finalize_task_id>, 与执行体 Return 同一候选序), 齐则派生 `lybra artifact ingest --task-id <被审卡>` 铸 finalization 记录。"""
    base = {"task_id": task_id, "current_node": "audit_verdict", "current_state": "claimed", "verb": "lybra_finalize", "command": ""}
    fin_id = finalize_task_id_for(task_id, fm)
    ret = find_return_artifact(workspace_root, fin_id)
    if ret is None:
        cands = ", ".join(str(c) for c in (_artifact_ingest_declaration()["return"].get("return_file_candidates") or []))
        return {
            **base,
            "derivable": False,
            "triggered_by": "executor",
            "missing_records": [f"FINALIZE 卡 {fin_id} 的 Return({return_artifact_dir(workspace_root, fin_id)}/ 候选: {cands})"],
            "suggested_action": f"等待外部 FINALIZE 卡 {fin_id}(merge/push/部署在产品仓所在机执行)交回 Return, frontmatter 含 "
                                f"{', '.join(str(k) for k in _finalization_ingest_declaration()['required_frontmatter'])}",
            "notes": f"N4→N5(external): 裁决 {verdict_result}, finalize_mode=external, 等 FINALIZE Return(声明: transitions N5.finalize_mode)",
            "action": {"type": "await_artifact", "card": fin_id, "kind": "finalize_return"},
        }
    problems = invalid_finalization_frontmatter(_read_frontmatter(ret, allow_missing_block=True))
    if problems:
        return {
            **base,
            "derivable": False,
            "triggered_by": "executor",
            "missing_records": problems,
            "suggested_action": f"FINALIZE 卡执行体在 {ret} 的 frontmatter 补齐/修正(声明: transitions artifact_ingest.finalization)",
            "notes": "N4→N5(external): FINALIZE Return 已落盘但不合规, 产品无法铸 finalization 记录(fail-closed)",
            "action": {"type": "artifact_invalid", "card": fin_id, "path": str(ret)},
        }
    if not claimer:
        return _not_derivable_no_claim(task_id, node="audit_verdict", state="claimed", verb="lybra_finalize", triggered_by="advisor",
                                       notes=f"N4→N5(external): 裁决 {verdict_result}, 但无 claim 记录, finalization actor 无据(AIPOS-F73E 件①)")
    return {
        **base,
        "derivable": True,
        "triggered_by": "advisor",
        "command": f"lybra artifact ingest --task-id {task_id} --workspace-root {workspace_root}",
        "missing_records": [],
        "suggested_action": "外部 FINALIZE Return 已落盘, 经产物入口铸 finalization 记录",
        "notes": f"N4→N5(external): 裁决 {verdict_result}, FINALIZE 卡 {fin_id} Return 在 {ret}",
    }


def _driver_envelope_ref(workspace_root: Path, task_id: str, task_fm: dict[str, Any], connection_json: str | None) -> str | None:
    """AIPOS-F78B 件③: 覆盖驱动方身份与本卡的有效 PreAuthorized 信封 policy_id(loop_driver.find_envelope 同一判据); 无 = None
    (派生命令回落 Supervised 形, execute_derived_action 对账务动词 fail-closed exit 5 带申领出口)。"""
    from tools.aipos_cli.loop_driver import find_envelope  # 延迟导入(loop_driver 依赖本模块)

    driver_actor = _driver_actor(workspace_root, connection_json=connection_json)
    if not driver_actor:
        return None
    # AIPOS-F90 件①: loop 显式 --envelope 在 scope 里 → 只认该信封(同一判据重核, 不另挑)
    scoped_policy = _scoped_driver().get("policy_id") or None
    policy, _reasons = find_envelope(workspace_root, task_id=task_id, task_fm=task_fm, driver_actor=driver_actor,
                                     policy_id=scoped_policy, driver_role=_driver_role_name(workspace_root, connection_json))
    return str(policy.get("policy_id")) if policy else None


def _driver_token_instance(workspace_root: Path, connection_json: str | None = None) -> str:
    """驱动方 token 绑定的实例: connection.json 中 role_class==roles.schema driver.role_class 的 token 的 agent_instance。"""
    import json

    from tools.aipos_cli.two_phase_shell_factory import driver_role_class

    conn = connection_json or _find_connection_json(workspace_root)
    if not conn or not Path(conn).is_file():
        return ""
    try:
        tokens = json.loads(Path(conn).read_text(encoding="utf-8")).get("tokens") or []
    except (OSError, ValueError) as exc:
        import sys

        print(f"Warning: {conn} unreadable, driver instance unresolved: {exc}", file=sys.stderr)
        return ""
    wanted = driver_role_class()
    for tok in tokens:
        if not isinstance(tok, dict):
            continue
        role_class = str(tok.get("role_class") or tok.get("role") or "").strip()
        if role_class == wanted:
            return str(tok.get("agent_instance") or "").strip()
    return ""


def _driver_actor(workspace_root: Path, fallback: str | None = None, *, connection_json: str | None = None) -> str:
    """驱动方身份(AIPOS-F78 前置零②: 禁回退占位 advisor)。

    顺序: ⓪ loop 显式 --actor(driver_scope, AIPOS-F90 件①) → ① 治理根 .lybra/role 的 instance(工位声明)
    → ② connection.json 驱动方 token 绑定的 agent_instance → ③ 调用方显式 fallback(仅靶场/显式传入)
    → 解析不到返回 ""(调用方 fail-closed: 不可推导 + 点名缺项)。
    AIPOS-F106 件④: ① 经 ConnectionResolver.resolve_identity(.lybra/role 唯一读取实现之一)。
    AIPOS-F115 件②: role 文件存在但不可读/格式坏 = WorkstationFileError 原样抛出(fail-closed, 拒因带出口), 不再按「未声明」落下一层。
    """
    scoped_actor = _scoped_driver().get("actor")
    if scoped_actor:
        return scoped_actor
    # AIPOS-F106 件④: 治理根 .lybra/role 只经 ConnectionResolver.resolve_identity 读(唯一实现; env 不参与 = 只认工位声明层)
    from tools.loop_context import ConnectionResolver

    declared = ConnectionResolver.resolve_identity(workspace_root=workspace_root, env={})["agent_instance"]
    if declared["source"] == ".lybra/role" and str(declared["value"] or "").strip():
        return str(declared["value"]).strip()
    instance = _driver_token_instance(workspace_root, connection_json)
    if instance:
        return instance
    return str(fallback or "")


DRIVER_ACTOR_MISSING = "驱动方身份(治理根 .lybra/role 的 instance, 或 connection.json 驱动方 token 的 agent_instance)"


def _driver_role_name(workspace_root: Path, connection_json: str | None = None) -> str:
    """AIPOS-F78B 件③: 驱动方的角色名(自定义角色如 chris 的 hbj-advisor)——工位声明 .lybra/role 的 role, 其次 connection.json
    驱动方 token(role_class==driver.role_class)的 role; 解析不到返回 ""(调用方回退角色类 advisor)。信封 agent_or_role 可写角色名。"""
    import json

    from tools.aipos_cli.two_phase_shell_factory import driver_role_class
    from tools.loop_context import ConnectionResolver

    # AIPOS-F106 件④: 治理根 .lybra/role 只经 ConnectionResolver.resolve_role 读(唯一实现; env={} = 只认工位声明层)
    role = str(ConnectionResolver.resolve_role(workspace_root=workspace_root, env={}) or "").strip()
    if role:
        return role
    conn = connection_json or _find_connection_json(workspace_root)
    if not conn or not Path(conn).is_file():
        return ""
    try:
        tokens = json.loads(Path(conn).read_text(encoding="utf-8")).get("tokens") or []
    except (OSError, ValueError):
        return ""
    wanted = driver_role_class()
    for tok in tokens:
        if isinstance(tok, dict) and str(tok.get("role_class") or tok.get("role") or "").strip() == wanted:
            return str(tok.get("role") or "").strip()
    return ""


def _claimer_instance(records: dict[str, Any]) -> str:
    """AIPOS-F73E 件①: 账务动词(return/verdict/finalize/close)的 actor/agent_instance = 该卡 claim 记录的 agent_instance
    (审计卡=审计实例, 执行卡=执行实例); token 仍归驱动方(two_phase_shell_factory.resolve_driver_role_from_connection)。
    唯一实现: 只读 claim 记录, 无记录返回 ""(调用方 fail-closed: 不可推导 + 点名缺项), 禁回退卡面/驱动方实例。
    声明: transitions.schema record_authenticity.submission_identity。"""
    latest_claim = records.get("latest_claim") or {}
    return str(latest_claim.get("agent_instance") or latest_claim.get("actor") or "").strip()


def _claim_record_missing(task_id: str) -> str:
    return f"claim 记录 agent_instance(5_tasks/records/claims/{task_id}/claim_*.md, 账务动词 actor 依据)"


def _not_derivable_no_claim(task_id: str, *, node: str, state: str, verb: str, triggered_by: str, notes: str) -> dict[str, Any]:
    """AIPOS-F73E 件①: 无 claim 记录 → 不可推导(exit 4)带出口。"""
    return {
        "task_id": task_id,
        "derivable": False,
        "current_node": node,
        "current_state": state,
        "triggered_by": triggered_by,
        "command": "",
        "verb": verb,
        "missing_records": [_claim_record_missing(task_id)],
        "suggested_action": (f"lybra state repair --task-id {task_id} --workspace-root <治理根>(按 records 重建卡态; 手写记录不算 record_authenticity)。"
                             f"仍无 claim 记录 = 人肉期在途卡, 经门收编(AIPOS-F123, transitions nodes.N1.adoption): "
                             f"lybra queue adopt --task-id {task_id} --branch <lane.repo 内既有分支> --actor <驱动方实例> "
                             f"--owner-policy-ref <信封> --dry-run 预览, 无拒因后改 --confirm, 再重推导(审计卡不收编: 收编被审卡后由 loop 重新派审)"),
        "notes": notes,
        # loop 据此硬停 exit 4(与 artifact_invalid 同款), 不把「缺 claim 记录」误当「执行体/审计体还在干活」空等
        "action": {"type": "record_missing", "card": task_id, "record": "claim"},
    }


def _action_type_for_command(command: str) -> str:
    """派生命令 → action_type(唯一映射, next --run 与 lybra loop 共用)。
    先匹配 "queue close" 再匹配 "finalize", 避免 close 命令被误判为 finalize。"""
    from tools.aipos_cli.governance_commit import governance_commit_cli, n6_landing_declaration

    if command.startswith(governance_commit_cli() + " "):
        return str(n6_landing_declaration()["action_type"])  # AIPOS-F94 件①: N6 落账步(声明 transitions nodes.N6.landing)
    if "artifact ingest" in command:
        # AIPOS-F90 件②: 产物入口按 --kind 声明所入的节点(return/verdict 账务步); 无 --kind = F78B 件② external finalize 步
        kind = re.search(r"--kind\s+(\S+)", command)
        if kind and kind.group(1) in ("return", "verdict"):
            return kind.group(1)
        return "finalize"  # AIPOS-F78B 件②: finalize_mode=external 的 finalize 步 = 产物入口铸 finalization 记录
    if "queue claim" in command:
        return "claim"
    if "queue return" in command:
        return "return"
    if "queue close" in command:
        return "close"
    if "audit-verdict" in command or "audit verdict" in command:
        return "verdict"
    if "audit dispatch" in command:
        return "dispatch"
    if "finalize" in command:
        return "finalize"
    return "unknown"


# ---------------------------------------------------------------------------
# 状态 → 节点映射(读 transitions.schema.json nodes 声明)
# ---------------------------------------------------------------------------

# 队列目录名 → 状态机节点
# 按 transitions.schema.json nodes 定义:
#   N0: publish (draft → pending)
#   N1: claim   (pending → claimed)
#   N2: return  (claimed → returned)
#   N3: audit_dispatch (returned → audit_dispatched)
#   N4: audit_verdict  (audit_dispatched → verdict_issued)
#   N5: finalize (verdict_issued → finalized)
#   N6: close   (finalized → completed)

# 状态推导:从记录推,不从 frontmatter 猜
# queue 目录位置 = 事实;records = 事实;frontmatter = 声明(辅助)


def _read_frontmatter(task_path: Path, *, allow_missing_block: bool = False) -> dict[str, Any]:
    """推导核读卡/记录/产物 frontmatter = frontmatter.require_frontmatter(「必须读出」唯一入口)的调用名。

    AIPOS-F100 件②: 读不出(文件不可读 / 无 frontmatter 块 / 任何解析告警)抛 FrontmatterReadError(路径 + 行号 + 原因),
    derive_next_step 统一转硬停(action=frontmatter_unreadable, 点名文件与出口); 原「返回 {} + stderr 告警」退役——
    下游曾据空 dict 取缺省(task_mode→code、缺省仓)继续推导。allow_missing_block=True 只给「产物尚未写 frontmatter
    = 未交/缺项」有既有判据的读点(RETURN / 审计报告 / FINALIZE Return)。"""
    from tools.aipos_cli.frontmatter import require_frontmatter

    return require_frontmatter(task_path, allow_missing_block=allow_missing_block)[0]


def frontmatter_unreadable_stop(task_id: str, exc: Any) -> dict[str, Any]:
    """AIPOS-F100 件②: 推导核遇 frontmatter 读不出 = 硬停(loop exit 4 点名文件与出口), 不推导任何放行动作。"""
    path = str(getattr(exc, "path", "") or "")
    queue_card = "/5_tasks/queue/" in path.replace("\\", "/")
    exit_hint = (f"卡面: `lybra state repair --task-id {task_id}`(规整单行标量, 规整不了的按拒因手工修正)后重推导"
                 if queue_card else f"按拒因修正 {path} 的 frontmatter 后重推导(记录/产物由写它的一方重写, 禁手工猜值)")
    return {
        "task_id": task_id,
        "derivable": False,
        "current_node": None,
        "current_state": "frontmatter_unreadable",
        "triggered_by": "advisor",
        "command": "",
        "verb": "",
        "missing_records": [str(exc)],
        "suggested_action": exit_hint,
        "notes": "AIPOS-F100 件②: frontmatter 读不出即硬停(fail-closed), 不取缺省继续推导",
        "action": {"type": "frontmatter_unreadable", "card": task_id, "path": path},
    }


def _find_task_in_queue(workspace_root: Path, task_id: str) -> tuple[Path | None, str | None]:
    """AIPOS-F78B 件①: 卡查找唯一实现 = task_loader.find_task_card(frontmatter task_id 精确匹配, 文件名不限);
    本名仅为推导核内的调用点, 不含第二套查找逻辑。多义(AmbiguousTaskCard)向上抛, 由调用方 fail-closed。"""
    from tools.aipos_cli.task_loader import find_task_card

    return find_task_card(workspace_root, task_id)


def _find_latest_record(records_dir: Path, prefix: str) -> dict[str, Any] | None:
    """在 records 子目录中找最新记录(按修改时间)。返回 frontmatter dict 或 None。"""
    from tools.aipos_cli.record_writer import record_files_with_prefix

    files = sorted(record_files_with_prefix(records_dir, prefix), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return None
    return _read_frontmatter(files[0]) or None


def _read_task_records(workspace_root: Path, task_id: str) -> dict[str, Any]:
    """读取任务的全部记录状态。纯读事实,不判活。"""

    result: dict[str, Any] = {
        "latest_claim": None,
        "latest_return": None,
        "latest_audit_dispatch": None,
        "latest_verdict": None,
        "latest_closure": None,
        "events": [],
    }

    # AIPOS-F122 件④: 门生记录文件判据 = record_writer.record_file_prefix(声明 record_locations.gate_record_file_criterion),
    # 与 state lint 推导同一实现(原各处写死前缀字面量)
    from tools.aipos_cli.record_writer import record_file_prefix

    # claims
    claims_dir = record_dir(workspace_root, "claims", task_id)
    result["latest_claim"] = _find_latest_record(claims_dir, record_file_prefix("claims"))

    # returns
    returns_dir = record_dir(workspace_root, "returns", task_id)
    result["latest_return"] = _find_latest_record(returns_dir, record_file_prefix("returns"))

    # audit_dispatches (AIPOS-F73 前置一: 门写在审计卡 ID 目录下,如 AIPOS-F75R)
    # 派审记录在 audit_dispatches/<audit_task_id>/ 而非 <task_id>/
    # AIPOS-F112: 审计卡 = 当前一轮(R → R2 → R3…, 声明 fix_card_closure.revision_card_numbering; 原写死 <ID>R, 复审轮永远读不到)
    from tools.aipos_cli.audit_derivation import audit_round_ids, current_audit_task_id

    audit_task_id = current_audit_task_id(task_id, workspace_root)
    result["audit_task_id"] = audit_task_id
    result["audit_round_ids"] = audit_round_ids(task_id, workspace_root)
    dispatches_dir = record_dir(workspace_root, "audit_dispatches", audit_task_id)
    result["latest_audit_dispatch"] = _find_latest_record(dispatches_dir, record_file_prefix("audit_dispatches"))

    # audit_verdicts (keyed by reviewed_task_id)
    verdicts_dir = record_dir(workspace_root, "audit_verdicts", task_id)
    result["latest_verdict"] = _find_latest_record(verdicts_dir, record_file_prefix("audit_verdicts"))

    # closures(AIPOS-F73E: 门落 close_*, 读 closure_* 即永远找不到 → loop 不得 exit 0; AIPOS-F122 件④: 前缀读声明 N6.record.location,
    # 与写侧 record_writer.CLOSURE_ID_PREFIX 相等由夹具钉住)
    closures_dir = record_dir(workspace_root, "closures", task_id)
    result["latest_closure"] = _find_latest_record(closures_dir, record_file_prefix("closures"))

    # events
    events_dir = record_dir(workspace_root, "events", task_id)
    if events_dir.is_dir():
        for ef in sorted(events_dir.glob("*.md"), key=lambda p: p.stat().st_mtime):
            fm = _read_frontmatter(ef)
            if fm:
                result["events"].append(fm)

    return result


def _check_return_artifact(workspace_root: Path, task_id: str) -> bool:
    """执行体产物就绪判据(唯一): 路径读 transitions N2.artifact.location; 存在且非骨架
    (「一句话结论」非 `(待填写` 占位)才算。骨架由 claim 创建(N1.return_skeleton), 不是交回。"""
    path = _return_artifact_path(workspace_root, task_id)
    if not path.is_file():
        return False
    return _extract_return_summary(workspace_root, task_id) is not None


def _check_verdict_artifact(workspace_root: Path, task_id: str, card_frontmatter: dict[str, Any] | None = None) -> Path | None:
    """审计体产物就绪判据(唯一): 落点根读项目声明(verdict_root), 文件候选读 transitions artifact_ingest.verdict
    (RETURN.md 优先, 回退 audit_report.md), 取首个「已交」的文件(AIPOS-F89 件③c: verdict_report_submitted = 声明必填键
    至少一个已填实值; 认领时生成的同名同位空模板全为占位 = 未交); 完成与否另由 verdict_report_ready 判(推导核对已交未完成
    判 artifact_invalid)。兼容旧命名 VERDICT-<ID>R.md。返回路径或 None。
    AIPOS-F112: 是否审计卡 = audit_derivation.is_audit_card 唯一判据(认得复审轮 R2/R3…; 原 endswith("R") 认不出)。"""
    from tools.aipos_cli.audit_derivation import is_audit_card

    if not is_audit_card(task_id, card_frontmatter):
        return None
    task_work_dir = verdict_artifact_dir(workspace_root, task_id)
    candidates = list(_audit_report_candidates(workspace_root, task_id))
    candidates.append(task_work_dir / f"VERDICT-{task_id}.md")
    candidates.append(task_work_dir / f"verdict-{task_id.lower()}.md")
    seen: set[Path] = set()
    for cand in candidates:
        if cand in seen or not cand.is_file():
            continue
        seen.add(cand)
        if verdict_report_submitted(_read_frontmatter(cand, allow_missing_block=True)):
            return cand
    return None


def _check_audit_card(workspace_root: Path, task_id: str) -> bool:
    """检查审计卡是否已生成(AIPOS-F112: 本卡审计轮 R/R2… 在 queue 中, 轮号读 audit_derivation.audit_round_ids)。"""
    from tools.aipos_cli.audit_derivation import audit_round_ids

    return bool(audit_round_ids(task_id, workspace_root))


# ---------------------------------------------------------------------------
# 推导核心:状态 → 下一步节点 + 角色 + 命令
# ---------------------------------------------------------------------------

def _extract_return_summary(workspace_root: Path, task_id: str) -> str | None:
    """从 RETURN.md 提取一句话结论。
    
    第4轮③: result_summary 必须从 RETURN.md「一句话结论」节提取,不存在→该步不该是return。
    AIPOS-F73D: 占位符(骨架 `(待填写`)不算结论 → None(= 产物未就绪)。路径读 N2.artifact.location。
    """
    try:
        return_path = _return_artifact_path(workspace_root, task_id)

        if not return_path.is_file():
            return None

        return extract_return_summary_text(return_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        import sys

        print(f"Warning: RETURN.md unreadable for {task_id}: {exc}", file=sys.stderr)
        return None


def extract_return_summary_text(content: str) -> str | None:
    """从 Return 正文提取「一句话结论」(唯一解析; 推导核与 artifact ingest 共用)。骨架占位 → None。"""
    lines = content.split("\n")
    in_summary_section = False
    for line in lines:
        if "一句话结论" in line or "## 一句话" in line:
            in_summary_section = True
            continue
        if in_summary_section:
            # 跳过空行
            if not line.strip():
                continue
            # 遇到下一个标题,结束
            if line.startswith("##"):
                break
            # 找到内容
            if line.strip():
                # 去掉 **完成** 等格式标记
                summary = line.strip().lstrip("*").rstrip("*").strip()
                # 去掉句号
                summary = summary.rstrip("。")
                if summary.startswith(_RETURN_PLACEHOLDER_PREFIX):
                    return None  # 骨架占位, 未交回
                return summary
    return None


def _find_connection_json(workspace_root: Path) -> str | None:
    """查找 connection.json 路径。"""
    # 优先治理仓(AIPOS-F106 件④: .lybra 经 ConnectionResolver.discover_lybra_dir 既有原语定位)
    from tools.loop_context import ConnectionResolver

    lybra_dir = ConnectionResolver.discover_lybra_dir(workspace_root)
    gov_conn = lybra_dir / "connection.json" if lybra_dir is not None else None
    if gov_conn is not None and gov_conn.is_file():
        return str(gov_conn)
    # 环境变量
    env_conn = os.environ.get("LYBRA_CONNECTION_JSON")
    if env_conn and Path(env_conn).is_file():
        return env_conn
    return None


def _extract_artifact_subject_from_branch(
    workspace_root: Path,
    reviewed_task_id: str,
    task_mode: str = "code",
) -> dict[str, str] | None:
    """AIPOS-F73前置①: 从卡分支 tip 提取 artifact_subject (repository/commit_sha/tree_hash)。
    
    Args:
        workspace_root: 产品仓根目录
        reviewed_task_id: 被审任务 ID
        task_mode: 任务模式（code 卡需要 artifact_subject）
    
    Returns:
        artifact_subject dict 或 None（非 code 卡或提取失败）
    """
    if task_mode != "code":
        return None
    
    import subprocess
    
    # 推导分支名(AIPOS-F112: 读声明 N5.branch_integration.branch_pattern 唯一口 card_branch_name, 原写死 card/<ID>)
    branch_name = card_branch_name(reviewed_task_id)
    
    try:
        # 获取分支 tip commit SHA
        result = subprocess.run(
            ["git", "rev-parse", branch_name],
            cwd=workspace_root,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return None
        
        commit_sha = result.stdout.strip()
        if not commit_sha or len(commit_sha) != 40:
            return None
        
        # 获取 tree hash
        result = subprocess.run(
            ["git", "rev-parse", f"{commit_sha}^{{tree}}"],
            cwd=workspace_root,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return None
        
        tree_hash = result.stdout.strip()
        if not tree_hash or len(tree_hash) != 40:
            return None
        
        # 推导 repository（从 workspace_root 名称）
        repository = workspace_root.name
        
        return {
            "repository": repository,
            "commit_sha": commit_sha,
            "tree_hash": tree_hash,
        }
    except (OSError, subprocess.SubprocessError) as exc:
        import sys

        print(f"Warning: artifact_subject extraction failed for {branch_name}: {exc}", file=sys.stderr)
        return None


def _card_branch_tip(workspace_root: Path, task_id: str,
                     card_frontmatter: dict[str, Any] | None) -> tuple[Path, dict[str, str], str] | None:
    """AIPOS-F114: 「卡分支当前 tip」唯一读取 = (产品仓, tip 主体 {repository, commit_sha, tree_hash}, 分支名)。
    tip 读取口 = _extract_artifact_subject_from_branch(裁决提交绑 tip / 门交回记录绑 tip 同一函数), 分支名 = card_branch_name(声明)。
    None = 非代码卡 / 产品仓不可解析(交给既有派生点出声)/ 无卡分支。"""
    fm = card_frontmatter if isinstance(card_frontmatter, dict) else {}
    if str(fm.get("task_mode") or "code").strip() != "code":
        return None
    from tools.aipos_cli.workspace_config import CardRepoUnresolved

    try:
        code_repo = _card_repo_root(workspace_root, task_id, fm, allow_governance_root=False)
    except CardRepoUnresolved:
        return None  # 产品仓不可解析: 交给既有 finalize 派生点出声(不可推导点名), 本判据不另判
    subject = _extract_artifact_subject_from_branch(code_repo, task_id, "code")
    if not subject:
        return None
    return code_repo, subject, card_branch_name(task_id)


def _live_card_branch(workspace_root: Path, task_id: str,
                      card_frontmatter: dict[str, Any] | None) -> tuple[Path, dict[str, str], str] | None:
    """AIPOS-F114: 过期判定前置(verdict_staleness / return_staleness 共用, 原 F112 内联于 verdict_staleness)= _card_branch_tip,
    且卡分支未并入基线分支(card_base_branch 声明; 已并入 = finalize 不再要求精确覆盖, 不算过期)。"""
    tip = _card_branch_tip(workspace_root, task_id, card_frontmatter)
    if tip is None:
        return None
    from tools.aipos_cli.finalize import _git_branch_merged_into_main

    if _git_branch_merged_into_main(tip[0], tip[2], card_base_branch()):  # AIPOS-F108 件②: 基线读声明(唯一读取口)
        return None
    return tip


def verdict_staleness(workspace_root: Path, task_id: str, card_frontmatter: dict[str, Any] | None) -> dict[str, Any] | None:
    """AIPOS-F112 件①: 「裁决已过期」判据唯一实现(推导核 audit_verdict 判定前 / 门侧交回自动派审 同读, 禁第二份)。

    比较对象与 finalize 的 F70 核对同源, 推导核与 finalize 不会各说各话:
      - 卡分支 tip: 分支名 = card_branch_name(声明), 读取口 = _extract_artifact_subject_from_branch(裁决提交绑 tip 的同一函数);
      - 最新门生裁决: deployment_authorization.find_gate_pass_verdict_for_task(required_commit_sha=tip)(finalize 同一判定)。
    过期 = 最新门生裁决属 PASS 族(N5.guards.has_pass_verdict 声明)且带 artifact_subject, 但其 commit_sha ≠ 卡分支 tip。
    不过期(None): 非代码卡 / 产品仓不可解析 / 无卡分支 / 卡分支已并入 main(finalize 不再要求精确覆盖)/ legacy 裁决 / 非 PASS 族 / 已覆盖。
    返回 {verdict_id, verdict, verdict_at, verdict_commit_sha, audit_task_id, branch, tip, tree_hash, reason}。"""
    live = _live_card_branch(workspace_root, task_id, card_frontmatter)
    if live is None:
        return None
    _code_repo, subject, branch = live
    from tools.aipos_cli.deployment_authorization import find_gate_pass_verdict_for_task

    check = find_gate_pass_verdict_for_task(task_id, Path(workspace_root), required_commit_sha=subject["commit_sha"])
    allowed = [str(v) for v in (_transition_node("N5").get("guards", {}).get("has_pass_verdict", {}).get("allowed_verdict_values") or [])]
    covered = check.get("artifact_subject") if isinstance(check.get("artifact_subject"), dict) else None
    if check.get("found") or str(check.get("verdict") or "") not in allowed or covered is None:
        return None
    verdict_file = str(check.get("verdict_file") or "")
    verdict_fm = _read_frontmatter(Path(verdict_file)) if verdict_file else {}
    return {
        "verdict_id": str(check.get("verdict_id") or ""),
        "verdict": str(check.get("verdict") or ""),
        "verdict_at": str(check.get("verdict_at") or ""),
        "verdict_commit_sha": str(covered.get("commit_sha") or ""),
        "audit_task_id": str(verdict_fm.get("audit_task_id") or ""),
        "reviewed_return_record_ref": str(verdict_fm.get("reviewed_return_record_ref") or ""),
        "branch": branch,
        "tip": subject["commit_sha"],
        "tree_hash": subject["tree_hash"],
        "reason": str(check.get("reason") or ""),
    }


def return_binding_subject(workspace_root: Path, task_id: str, card_frontmatter: dict[str, Any] | None) -> dict[str, str] | None:
    """AIPOS-F114 件①: 门 queue_return 落交回记录时绑定的被交回产物主体 = 卡分支此刻 tip {repository, commit_sha, tree_hash, branch}
    (_card_branch_tip 唯一读取, 与裁决绑 tip / 过期判据同一函数)。非代码卡 / 产品仓不可解析 / 无卡分支 = None
    (交回记录不带绑定, 与存量记录同形; 代码卡经产物入口交回时入口已核 Return commit_sha == 此 tip)。"""
    tip = _card_branch_tip(workspace_root, task_id, card_frontmatter)
    return {**tip[1], "branch": tip[2]} if tip else None


def return_record_subject(workspace_root: Path, task_id: str, return_id: str) -> dict[str, Any] | None:
    """AIPOS-F114: 门交回记录绑定的被交回产物主体(return 记录 artifact_subject: 门在 queue_return 落记录时以
    _extract_artifact_subject_from_branch 读卡分支 tip 写入, 与裁决绑 tip 同一读取口)。存量记录无该字段 / 记录不在 = None。
    记录在盘但 frontmatter 读不出 = FrontmatterReadError 上抛(AIPOS-F100: 读不出即拒, 不取缺省)。"""
    return_id = str(return_id or "").strip()
    if not return_id:
        return None
    returns_dir = record_dir(workspace_root, "returns", task_id)
    path = returns_dir / f"{return_id}.md"
    if not path.is_file():
        return None
    fm = _read_frontmatter(path)
    subject = fm.get("artifact_subject") if isinstance(fm.get("artifact_subject"), dict) else None
    sha = str((subject or {}).get("commit_sha") or "").strip()
    return {**subject, "commit_sha": sha} if sha else None


def return_staleness(workspace_root: Path, task_id: str, card_frontmatter: dict[str, Any] | None,
                     records: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """AIPOS-F114 件①: 「交回已过期」判据唯一实现(推导核等待审计/提交裁决前、审计卡开工与裁决入口、门侧交回自动派审同读, 禁第二份)。

    适用 = 当前一轮审计卡(audit_derivation.current_audit_task_id)已派审且该轮尚无裁决(在途); 已有本轮裁决归
    verdict_staleness(F112), 未派审归既有派审。被比较的两端(判据风格与 F112 verdict_staleness 同, 卡分支 tip 同一读取口 _live_card_branch):
      - 本轮所审交回绑定的 tip: 派审记录 reviewed_return_record_ref 指向的门交回记录 artifact_subject(return_record_subject);
        存量交回记录无绑定(门版本早于本卡)时 = 本轮取证工作树 HEAD(门认领时建于被审分支 tip, 即审计体正在审的提交);
        两者都有时任一 ≠ 卡分支 tip 即过期(审计开工以分支当前 tip 为准)。两者都无(存量记录且本轮未认领)= 不判(认领时取证树建于当前 tip)。
      - 卡分支当前 tip。
    返回 {return_id, bound_commit_sha, bound_source(return_record|forensic_worktree), audit_task_id, dispatch_id, branch, tip, tree_hash,
    reason}; None = 不过期 / 不适用。"""
    live = _live_card_branch(workspace_root, task_id, card_frontmatter)
    if live is None:
        return None
    _code_repo, subject, branch = live
    recs = records if isinstance(records, dict) else _read_task_records(workspace_root, task_id)
    dispatch = recs.get("latest_audit_dispatch") or {}
    current = str(recs.get("audit_task_id") or "")
    if not dispatch or not current:
        return None
    latest_verdict = recs.get("latest_verdict") or {}
    if latest_verdict and str(latest_verdict.get("audit_task_id") or "") == current:
        return None  # 本轮已出裁决: 归 verdict_staleness(F112)
    tip = subject["commit_sha"]
    reviewed_ref = str(dispatch.get("reviewed_return_record_ref") or "")
    bound: list[tuple[str, str]] = []
    record_subject = return_record_subject(workspace_root, task_id, reviewed_ref)
    if record_subject:
        bound.append(("return_record", str(record_subject["commit_sha"])))
    audit_path, _q = _find_task_in_queue(workspace_root, current)
    audit_fm = _read_frontmatter(audit_path) if audit_path else {}
    from tools.aipos_cli.workspace_config import CardRepoUnresolved
    from tools.schema_loader import SchemaLoadError

    try:
        _repo, worktree = card_worktree_location(workspace_root, current, audit_fm) if audit_path else (None, None)
    except (CardRepoUnresolved, SchemaLoadError) as exc:
        import sys

        print(f"Warning: 审计卡 {current} 取证工作树落点不可推导(交回过期判据只用交回记录绑定): {exc}", file=sys.stderr)
        worktree = None
    if worktree is not None and worktree.is_dir():
        try:
            bound.append(("forensic_worktree", forensic_worktree_subject(worktree)["commit_sha"]))
        except ValueError as exc:
            # 取证树读不出 HEAD: 开工面已按 REPORT_CONTRACT_UNRESOLVED 拒开工(card_report_contract 同一读取); 此处只用交回记录绑定判
            import sys

            print(f"Warning: {exc}(交回过期判据只用交回记录绑定)", file=sys.stderr)
    for source, sha in bound:
        if sha != tip:
            where = (f"门交回记录 {reviewed_ref} 绑定 {sha[:12]}" if source == "return_record"
                     else f"本轮取证工作树 {worktree} HEAD {sha[:12]}(交回记录 {reviewed_ref or '?'} 无绑定, 存量)")
            return {
                "return_id": reviewed_ref,
                "bound_commit_sha": sha,
                "bound_source": source,
                "audit_task_id": current,
                "dispatch_id": str(dispatch.get("dispatch_id") or ""),
                "branch": branch,
                "tip": tip,
                "tree_hash": subject["tree_hash"],
                "reason": f"{where} ≠ 卡分支 {branch} tip {tip[:12]}: 交回后卡分支又前进, 审计轮 {current} 审的不是当前产物",
            }
    return None


def return_stale_declaration() -> dict[str, Any]:
    """AIPOS-F114: 交回过期状态声明(transitions nodes.N3.return_stale; 状态名/出口/保护均读此)。缺 = SchemaLoadError(fail-closed)。"""
    from tools.schema_loader import SchemaLoadError

    decl = _transition_node("N3").get("return_stale")
    if not isinstance(decl, dict) or not str(decl.get("state") or "").strip():
        raise SchemaLoadError("transitions.schema.json nodes.N3.return_stale(含 state)未声明")
    return decl


def _build_copyable_command(
    *,
    verb_base: str,
    task_id: str,
    actor: str,
    agent_instance: str,
    autonomy_mode: str,
    owner_policy_ref: str | None,
    connection_json: str | None,
    extra_args: dict[str, str] | None = None,
) -> str:
    """构建可照抄 CLI 命令。token 值永不出现。

    命令指向产品 CLI 薄壳(--confirm 两阶段一段式)。
    """
    parts = [f"lybra queue {verb_base}"]
    parts.append(f"--task-id {task_id}")
    parts.append(f"--actor {actor}")
    parts.append("--confirm")

    if connection_json:
        parts.append(f"--connection-json {connection_json}")

    parts.append(f"--agent-instance {agent_instance}")
    # AIPOS-F73D 前置一③(parser 夹具实撞): 只有 `queue claim` 声明了 --autonomy-mode; `queue return` 的 argparse 无此参数,
    # 带上即 "unrecognized arguments" 禁执行。参数集以 aipos_cli 声明为准。
    if verb_base in ("claim", "return"):
        parts.append(f"--autonomy-mode {autonomy_mode}")

    if owner_policy_ref:
        parts.append(f"--owner-policy-ref {owner_policy_ref}")

    if extra_args:
        for k, v in extra_args.items():
            parts.append(f"--{k} {v}")

    return " ".join(parts)


def _is_placeholder_value(value: Any) -> bool:
    """报告 frontmatter 值是否未填: 空 / 骨架占位(`(待填写` 前缀, record_writer 骨架)/ 尖括号占位(`<…>`)。"""
    text = str(value or "").strip()
    return not text or text.startswith(_RETURN_PLACEHOLDER_PREFIX) or text.startswith("<")


def required_verdict_frontmatter() -> list[str]:
    """审计报告必填 frontmatter 键(唯一声明: transitions artifact_ingest.verdict.required_frontmatter)。"""
    keys = list(_artifact_ingest_declaration()["verdict"].get("required_frontmatter") or [])
    if not keys:
        from tools.schema_loader import SchemaLoadError

        raise SchemaLoadError("transitions.schema.json artifact_ingest.verdict.required_frontmatter 未声明")
    return [str(k) for k in keys]


def missing_verdict_frontmatter(frontmatter: dict[str, Any]) -> list[str]:
    """审计报告缺失的必填 frontmatter 键(空值/占位视为缺)。AIPOS-F89 件③c: 报告完成判据(artifact_ingest.verdict.readiness)
    = 本函数返回空; 认领时生成的同名同位空模板(全占位)不算完成。"""
    return [k for k in required_verdict_frontmatter() if _is_placeholder_value(frontmatter.get(k))]


def verdict_report_submitted(frontmatter: dict[str, Any]) -> bool:
    """AIPOS-F89 件③c: 审计报告「已动笔」= 至少一个声明必填键已填实值(认领时的空模板全为占位 = 未交, 继续等待)。
    已交但未填全 → 推导核判 artifact_invalid(loop 硬停点名缺项), 不当完成也不空等。"""
    return any(not _is_placeholder_value(frontmatter.get(k)) for k in required_verdict_frontmatter())


def verdict_report_ready(frontmatter: dict[str, Any]) -> bool:
    """AIPOS-F89 件③c: 审计报告「写完」的唯一判据(推导核 / loop 等待 / artifact ingest / 开工核验同读):
    声明的必填 frontmatter(verdict / commit_sha)全部已填且非占位。文件存在 ≠ 完成。"""
    return not missing_verdict_frontmatter(frontmatter)


def required_return_frontmatter() -> list[str]:
    """Return 必填 frontmatter 键(唯一声明: transitions artifact_ingest.return.required_frontmatter)。"""
    keys = list(_artifact_ingest_declaration()["return"].get("required_frontmatter") or [])
    if not keys:
        from tools.schema_loader import SchemaLoadError

        raise SchemaLoadError("transitions.schema.json artifact_ingest.return.required_frontmatter 未声明")
    return [str(k) for k in keys]


def missing_return_frontmatter(frontmatter: dict[str, Any]) -> list[str]:
    """缺失的 Return 必填 frontmatter 键列表(空值/占位视为缺)。"""
    return [k for k in required_return_frontmatter() if _is_placeholder_value(frontmatter.get(k))]


# ---------------------------------------------------------------------------
# AIPOS-F93 件①: 报告必填字段单源告知——声明 transitions artifact_ingest.<return|verdict>.required_frontmatter(+ field_hints)
# 的唯一渲染。派生审计卡落点句 / 执行卡落点句 / my-tasks next_card / 认领模板 / 章程报告节 全部经此二函数, 禁第二份清单/文案。
# ---------------------------------------------------------------------------

REPORT_KINDS = ("return", "verdict")


def report_frontmatter_contract(kind: str, *, branch_task_id: str | None = None,
                                subject: dict[str, str] | None = None,
                                branch_pattern: str | None = None) -> list[dict[str, Any]]:
    """报告必填 frontmatter 契约(唯一渲染源): [{key, hint, value}], 键序 = 声明 required_frontmatter。

    - kind: return(执行卡 Return)/ verdict(审计报告)。
    - branch_task_id: 报告对应的卡分支所属卡(执行卡 = 本卡, 审计卡 = 被审卡); 缺 = 分支声明套占位卡号(card/<卡ID>)。
    - subject: 产品已知的实值(审计卡 = 取证工作树 HEAD 的 {commit_sha, tree_hash}); 有值的键 value=实值, 提示带 tree, agent 照抄。
    - branch_pattern: 调用方已读的分支声明(machine_zone 按 product_root 读 N5.branch_integration); 缺 = card_branch_name。
    键缺说明(field_hints 未声明该键)= 通用提示「按声明填写实值」(仍列出, 不漏键)。声明缺 = SchemaLoadError(fail-closed)。
    """
    if kind not in REPORT_KINDS:
        raise ValueError(f"report_frontmatter_contract: kind={kind!r} 不在 {REPORT_KINDS}")
    keys = required_return_frontmatter() if kind == "return" else required_verdict_frontmatter()
    hints = _artifact_ingest_declaration()[kind].get("field_hints") or {}
    if not isinstance(hints, dict):
        from tools.schema_loader import SchemaLoadError

        raise SchemaLoadError(f"transitions.schema.json artifact_ingest.{kind}.field_hints 须为 {{键: 说明}}")
    # 卡未定(章程等通用文案)= 分支声明套占位卡号(`card/<卡ID>` / `card/<被审卡ID>`), 仍读声明不写死
    subject_id = branch_task_id or ("<被审卡ID>" if kind == "verdict" else "<卡ID>")
    branch = branch_pattern.replace("{task_id}", subject_id) if branch_pattern else card_branch_name(subject_id)
    verdict_values = " / ".join(str(v) for v in (_transition_node("N4").get("record", {}).get("allowed_verdict_values") or []))
    values = subject if isinstance(subject, dict) else {}
    entries: list[dict[str, Any]] = []
    for key in keys:
        template = str(hints.get(key) or "按声明填写实值")
        hint = template.replace("{branch}", branch).replace("{allowed_verdict_values}", verdict_values or "裁决值")
        value = str(values.get(key) or "").strip() or None
        if value and key == "commit_sha" and values.get("tree_hash"):
            hint = f"{hint}; 产品给出: {branch} tip = {value}, tree = {values['tree_hash']}(取证工作树 HEAD), 照抄"
        entries.append({"key": key, "hint": hint, "value": value})
    return entries


def render_report_frontmatter_clause(entries: list[dict[str, Any]]) -> str:
    """报告必填 frontmatter 的唯一一句话文案(落点句 / 章程 / card render 共用)。entries = report_frontmatter_contract 输出。"""
    parts = []
    for entry in entries:
        value = entry.get("value")
        parts.append(f"`{entry['key']}`({f'= {value}; ' if value else ''}{entry['hint']})")
    return "报告 frontmatter 必填: " + "; ".join(parts) + "。缺任一项或仍为占位, 产品拒收该报告"


def ingest_command(task_id: str, kind: str, workspace_root: Path, connection_json: str | None) -> str:
    """AIPOS-F90 件②: return/verdict 步对驱动方派生的命令 = 产物入口 `lybra artifact ingest --kind <kind>`(唯一形)。"""
    parts = [f"lybra artifact ingest --task-id {task_id} --kind {kind} --workspace-root {workspace_root}"]
    if connection_json:
        parts.append(f"--connection-json {connection_json}")
    return " ".join(parts)


def with_runtime_model(shell_command: str, *, kind: str, agent_runtime: dict[str, Any]) -> str:
    """AIPOS-F90 件②: 给入口内部的薄壳命令补产品填写的模型字段(唯一拼接口): return 补 --actual-model(=运行时模型) 与
    --agent-runtime; verdict 补 --agent-runtime。值经 shlex 引用, 永不含 token。"""
    import json as _json
    import shlex as _shlex

    parts = [shell_command]
    if kind == "return":
        parts.append(f"--actual-model {_shlex.quote(str(agent_runtime.get('model') or ''))}")
    parts.append(f"--agent-runtime {_shlex.quote(_json.dumps(agent_runtime, ensure_ascii=False, sort_keys=True))}")
    return " ".join(parts)


def build_return_command_from_artifact(
    workspace_root: Path,
    task_id: str,
    *,
    claimer: str,
    owner_policy_ref: str | None,
    connection_json: str | None,
    result_summary: str,
    return_path: Path,
    autonomy_mode: str = "Supervised",
) -> str:
    """AIPOS-F78 件③: 由 Return 文件派生 `lybra queue return --confirm` 命令(推导核与 artifact ingest 共用, 禁第二路径)。
    AIPOS-F78B 件③: autonomy_mode=PreAuthorized + owner_policy_ref=驱动方信封 → 门一阶段落记录。

    completion_report_ref = artifact_refs[0] = Return 相对治理根路径(分支/sha 在 Return frontmatter, 入口核 tip)。
    actual_model / agent_runtime 由产物入口补(AIPOS-F90 件②: 运行时模型取自会话记录, 自报只作对照)。
    """
    import json as _json

    fm = _read_frontmatter(return_path, allow_missing_block=True)
    try:
        report_ref = str(Path(return_path).resolve().relative_to(Path(workspace_root).resolve()))
    except ValueError:
        report_ref = str(return_path)
    extra: dict[str, str] = {"result-summary": _json.dumps(result_summary, ensure_ascii=False)}
    extra["completion-report-ref"] = report_ref
    # AIPOS-F90 件②: artifact_refs = [Return 相对治理根路径]——门的交回落点判据(board_adapter 报告材料: 文件须在
    # task_cards/<ID>/ 或 records/ 下)只认治理面引用; 原 `<branch>@<sha>` 形被真门拒 RETURN_ARTIFACT_WRONG_LOCATION
    # (loop 从未对真门交回成功的病根之一)。分支与 sha 由 Return frontmatter 携带, 产物入口已核 tip==commit_sha。
    extra["artifact-refs"] = "'" + _json.dumps([report_ref], ensure_ascii=False) + "'"
    # AIPOS-F90 件②: actual_model 不再采信 Return 自报(frontmatter.model 只作对照), 由产物入口按卡面 harness 的会话记录定位器
    # 取运行时模型后补入(artifact_ingest.ingest_task_artifact → with_runtime_model)
    return _build_copyable_command(
        verb_base="return",
        task_id=task_id,
        actor=claimer,
        agent_instance=claimer,
        autonomy_mode=autonomy_mode,
        owner_policy_ref=owner_policy_ref,
        connection_json=connection_json,
        extra_args=extra,
    )


def _build_audit_dispatch_command(
    *,
    task_id: str,
    actor: str,
    agent_instance: str,
    owner_policy_ref: str | None,
    connection_json: str | None,
    audit_task_id: str,
    audit_agent_instance: str,
) -> str:
    """构建审计派发命令。"""
    parts = ["lybra audit dispatch"]
    parts.append(f"--source-task-id {task_id}")
    parts.append(f"--actor {actor}")
    parts.append(f"--agent-instance {agent_instance}")
    if owner_policy_ref:
        parts.append(f"--owner-policy-ref {owner_policy_ref}")
    parts.append(f"--audit-task-id {audit_task_id}")
    parts.append(f"--audit-agent-instance {audit_agent_instance}")
    return " ".join(parts)


def _build_verdict_submit_command(
    *,
    reviewed_task_id: str,
    audit_task_id: str,
    actor: str,
    agent_instance: str,
    owner_policy_ref: str | None,
    connection_json: str | None,
    verdict: str = "PASS",
    artifact_subject: dict[str, str] | None = None,
    autonomy_mode: str = "Supervised",
    findings_summary: str | None = None,
    evidence_refs: list[str] | None = None,
) -> str:
    """构建审计裁决提交命令。
    
    AIPOS-F73前置①: artifact_subject 从卡分支 tip 提取，code 卡必填。
    AIPOS-F78B 件③: autonomy_mode=PreAuthorized + owner_policy_ref=驱动方信封 → 门一阶段落记录。
    """
    parts = ["lybra audit-verdict"]
    parts.append(f"--reviewed-task-id {reviewed_task_id}")
    parts.append(f"--audit-task-id {audit_task_id}")
    parts.append(f"--actor {actor}")
    parts.append("--confirm")
    if connection_json:
        parts.append(f"--connection-json {connection_json}")
    parts.append(f"--agent-instance {agent_instance}")
    if autonomy_mode == "PreAuthorized":
        parts.append(f"--autonomy-mode {autonomy_mode}")
    if owner_policy_ref:
        parts.append(f"--owner-policy-ref {owner_policy_ref}")
    parts.append(f"--verdict {verdict}")
    # AIPOS-F90 件②: 裁决证据读审计报告(PASS 类裁决门要求非空证据 AIPOS-F63): findings_summary=报告「一句话结论」,
    # evidence_refs=[报告相对治理根路径]; 原派生命令不带证据, 真门必拒 EMPTY_EVIDENCE
    if findings_summary:
        parts.append(f"--findings-summary {_shlex_quote(findings_summary)}")
    if evidence_refs:
        import json as _json

        parts.append(f"--evidence-refs {_shlex_quote(_json.dumps(list(evidence_refs), ensure_ascii=False))}")
    
    # AIPOS-F73前置①: artifact_subject for code tasks
    if artifact_subject:
        repo = artifact_subject.get("repository", "")
        commit_sha = artifact_subject.get("commit_sha", "")
        tree_hash = artifact_subject.get("tree_hash", "")
        if repo:
            parts.append(f"--artifact-subject-repository {repo}")
        if commit_sha:
            parts.append(f"--artifact-subject-commit-sha {commit_sha}")
        if tree_hash:
            parts.append(f"--artifact-subject-tree-hash {tree_hash}")
    
    return " ".join(parts)


def _shlex_quote(value: str) -> str:
    import shlex as _shlex

    return _shlex.quote(str(value))


def _build_finalize_command(
    *,
    task_id: str,
    actor: str,
    code_repo_root: Path,
    governance_root: Path,
) -> str:
    """AIPOS-F73D 前置一②: finalize 命令模板(唯一)。必带 --actor; --workspace-root=产品仓根(git 操作地),
    --governance-root=治理根(裁决记录地)——两根永不混用(F75/F73C2 实撞同病)。"""
    parts = ["lybra finalize"]
    parts.append(f"--task-id {task_id}")
    parts.append(f"--actor {actor}")
    parts.append("--push")
    parts.append("--deploy")
    parts.append(f"--workspace-root {code_repo_root}")
    parts.append(f"--governance-root {governance_root}")
    return " ".join(parts)


def _build_close_command(
    *,
    task_id: str,
    actor: str,
    connection_json: str | None,
    closure_evidence_json: str = '{}',
    autonomy_mode: str = "Supervised",
    owner_policy_ref: str | None = None,
) -> str:
    """构建结案命令。AIPOS-F78B 件③: 驱动方信封在 → `--confirm --autonomy-mode PreAuthorized --owner-policy-ref <pid>`
    经门一阶段落记录(queue close 薄壳走 MCP); 否则存量本地 close_task 形。"""
    parts = ["lybra queue close"]
    parts.append(f"--task-id {task_id}")
    parts.append(f"--actor {actor}")
    parts.append(f"--closure-evidence '{closure_evidence_json}'")
    if autonomy_mode == "PreAuthorized" and owner_policy_ref:
        parts.append("--confirm")
        parts.append(f"--autonomy-mode {autonomy_mode}")
        parts.append(f"--owner-policy-ref {owner_policy_ref}")
        if connection_json:
            parts.append(f"--connection-json {connection_json}")
    return " ".join(parts)


def _derive_n6_landing(workspace_root: Path, task_id: str, state: str, connection_json: str | None) -> dict[str, Any] | None:
    """AIPOS-F94 件①: N6 落账步。卡已结案(有 closure 记录)后, 判据 = 该卡落账范围(governance_commit.task_landing:
    本卡与审计卡的队列文件 / 草稿 / 记录 / 台账落点 + 卡编年史)已在治理仓提交且已推送。

    已落账 → None(调用方给「已结案」); 未落账 → 派生 `lybra governance-commit --task-id <卡> --actor <驱动方> --governance-root <根>`
    (唯一渲染 governance_commit_command; task 范围精确提交, 幂等); 治理根不在 git 工作树内 / 驱动方身份解析不到 = 不可推导点名缺项。
    """
    import subprocess

    from tools.aipos_cli.governance_commit import governance_commit_command, n6_landing_declaration, non_git_exit_command, task_landing

    landing_verb = str(n6_landing_declaration()["verb"])
    try:
        landing = task_landing(workspace_root, task_id)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError, ValueError) as exc:
        detail = (getattr(exc, "stderr", "") or str(exc)).strip()
        return {"task_id": task_id, "derivable": False, "current_node": "close", "current_state": state, "triggered_by": "advisor",
                "command": "", "verb": landing_verb, "missing_records": [f"落账判据不可读: {detail}"],
                "suggested_action": "按拒因修复(卡多义 / git 只读失败)后重推导",
                "notes": "N6 落账: 落账判据读取失败(fail-closed)",
                "action": {"type": "record_missing", "card": task_id, "record": "governance_landing"}}
    if landing["landed"]:
        return None
    base = {
        "task_id": task_id,
        "current_node": "close",
        "current_state": state,
        "triggered_by": "advisor",
        "verb": landing_verb,
        "landing": landing,
    }
    if not landing["git"]:
        return {**base, "derivable": False, "command": "",
                "missing_records": [f"治理仓(git): {landing['reason']}"],
                "suggested_action": f"{non_git_exit_command(workspace_root)}(一次性本地 git init; Owner 按其输出配 origin 并首推)后重跑; 卡已结案, 只差落账",
                "notes": "N6 落账: 已结案但治理根不在 git 仓, 无从落账(AIPOS-F94 fail-closed)",
                "action": {"type": "record_missing", "card": task_id, "record": "governance_repo"}}
    driver = _driver_actor(workspace_root, connection_json=connection_json)
    if not driver:
        return {**base, "derivable": False, "command": "", "missing_records": [DRIVER_ACTOR_MISSING],
                "suggested_action": "补驱动方身份(lybra loop --actor 或治理根 .lybra/role instance)后重推导",
                "notes": "N6 落账: 落账 actor 无据(驱动方身份解析不到)",
                "action": {"type": "record_missing", "card": task_id, "record": "driver_actor"}}
    return {**base, "derivable": True,
            "command": governance_commit_command(task_id, driver, workspace_root),
            "missing_records": [],
            "suggested_action": "N6 落账: task 范围精确提交并推送治理仓",
            "notes": f"N6 落账: 已结案但治理未落账({landing['reason']}); loop 自动执行, 幂等(已提交即 no-op)"}


def _return_submission_step(workspace_root: Path, task_id: str, fm: dict[str, Any], *, claimer: str, conn_arg: str | None,
                            result_summary: str, return_path: Path, node: str, suggested_action: str, notes: str) -> dict[str, Any]:
    """交回步(N1→N2 首交 / AIPOS-F112 verdict_stale 重交回)的唯一构建: 驱动方见产物入口命令 `lybra artifact ingest --kind return`,
    shell_command = 入口内部执行的同一条薄壳命令(build_return_command_from_artifact 唯一构建), 禁第二条交回路径。"""
    # AIPOS-F78B 件③; AIPOS-F103 件④: 信封 = 覆盖驱动方与本卡的有效信封(唯一挑选 autonomy_policy.select_envelope), 不另从认领记录取
    driver_policy = _driver_envelope_ref(workspace_root, task_id, fm, conn_arg)
    cmd = build_return_command_from_artifact(
        workspace_root,
        task_id,
        claimer=claimer,
        owner_policy_ref=driver_policy,
        connection_json=conn_arg,
        result_summary=result_summary,
        return_path=return_path,
        autonomy_mode="PreAuthorized" if driver_policy else "Supervised",
    )
    return {
        "task_id": task_id,
        "derivable": True,
        "current_node": node,
        "current_state": "claimed",
        "triggered_by": "executor",
        # AIPOS-F90 件②: 驱动方只见产物入口命令; shell_command = 入口内部执行的同一条薄壳命令
        "command": ingest_command(task_id, "return", workspace_root, conn_arg),
        "shell_command": cmd,
        "verb": "lybra_queue_return_dry_run",
        "missing_records": [],
        "suggested_action": suggested_action,
        "notes": notes,
    }


def verdict_stale_declaration() -> dict[str, Any]:
    """AIPOS-F112: 裁决过期状态声明(transitions nodes.N4.verdict_stale; 状态名/出口/保护均读此)。缺 = SchemaLoadError(fail-closed)。"""
    from tools.schema_loader import SchemaLoadError

    decl = _transition_node("N4").get("verdict_stale")
    if not isinstance(decl, dict) or not str(decl.get("state") or "").strip():
        raise SchemaLoadError("transitions.schema.json nodes.N4.verdict_stale(含 state)未声明")
    return decl


def _derive_verdict_stale(workspace_root: Path, task_id: str, fm: dict[str, Any], records: dict[str, Any],
                          stale: dict[str, Any], *, claimer: str, conn_arg: str | None) -> dict[str, Any]:
    """AIPOS-F112 件②③: 裁决已过期(卡分支 tip ≠ 最新 PASS 裁决覆盖的 commit)的出口——复用既有交回: 对执行体 Return 再走
    `lybra artifact ingest --kind return`(门落新 return 记录, 门侧既有自动派审按卡号演进声明派生下一轮审计卡 R2/R3…)。
    保护: Return 未随卡分支更新(commit_sha 仍是旧 tip 等)= 产物入口同一校验(artifact_ingest.validate_task_artifact)拒,
    此处 artifact_invalid 点名原文(loop exit 4), 不派旧 Return 去审; 已重交回但门未派下一轮 = 不可推导(不重复交回)。
    构建唯一实现 _derive_stale_re_return(AIPOS-F114 return_stale 同用)。"""
    state = str(verdict_stale_declaration()["state"])
    covered = str(stale.get("verdict_commit_sha") or "")
    tip = str(stale.get("tip") or "")
    head = (f"裁决 {stale.get('verdict_id')}({stale.get('verdict')}, 审计卡 {stale.get('audit_task_id') or '?'})覆盖 {covered[:12]}, "
            f"卡分支 {stale.get('branch')} tip 已是 {tip[:12]}")
    return _derive_stale_re_return(
        workspace_root, task_id, fm, records, stale, claimer=claimer, conn_arg=conn_arg, state=state, stale_key="verdict_stale",
        head=head, reviewed_ref=str(stale.get("reviewed_return_record_ref") or ""), since=str(stale.get("verdict_at") or ""),
        reviewed_what="过期裁决审的是",
        without_round_suggested="核门侧自动派审(transitions nodes.N4.verdict_stale.outlet; 门须为含 AIPOS-F112 的版本)后重推导; 不重复交回",
        without_round_note="已重交回仍无复审轮(AIPOS-F112 fail-closed)",
        not_updated_note="Return 未随卡分支更新, 不得用旧 Return 派审(AIPOS-F112 件③)",
        re_return_notes=f"{state}: {head}; 产物已变化须复审 → 重交回派下一轮(AIPOS-F112, 声明 transitions nodes.N4.verdict_stale)",
    )


def _derive_return_stale(workspace_root: Path, task_id: str, fm: dict[str, Any], records: dict[str, Any],
                         stale: dict[str, Any], *, claimer: str, conn_arg: str | None) -> dict[str, Any]:
    """AIPOS-F114 件①: 交回已过期(本轮在途审计所审交回绑定的 tip ≠ 卡分支 tip, 典型: 交回后执行体合 main)的出口——与 F112 同一
    重交回构建(_derive_stale_re_return): 重交回 → 门侧交回自动派审按卡号演进派下一轮(派审记录 supersedes=在途旧轮), 在途旧轮作废;
    Return 未随卡分支更新 = 产物入口同一校验拒(artifact_invalid); 已重交回但门未派下一轮 = 不可推导(不重复交回)。"""
    state = str(return_stale_declaration()["state"])
    head = (f"审计轮 {stale.get('audit_task_id')} 在途(未出裁决), {stale.get('reason')}")
    return _derive_stale_re_return(
        workspace_root, task_id, fm, records, stale, claimer=claimer, conn_arg=conn_arg, state=state, stale_key="return_stale",
        head=head, reviewed_ref=str(stale.get("return_id") or ""), since=None,
        reviewed_what=f"在途审计轮 {stale.get('audit_task_id')} 审的是",
        without_round_suggested="核门侧自动派审(transitions nodes.N3.return_stale.outlet; 门须为含 AIPOS-F114 的版本)后重推导; 不重复交回",
        without_round_note="已重交回仍无下一轮审计(AIPOS-F114 fail-closed)",
        not_updated_note="Return 未随卡分支更新, 不得用旧 Return 重交回派审(AIPOS-F114 件①)",
        re_return_notes=(f"{state}: {head}; 交回已过期 → 重交回派下一轮审计, 在途旧轮 {stale.get('audit_task_id')} 作废"
                         "(AIPOS-F114, 声明 transitions nodes.N3.return_stale)"),
    )


def _derive_stale_re_return(workspace_root: Path, task_id: str, fm: dict[str, Any], records: dict[str, Any],
                            stale: dict[str, Any], *, claimer: str, conn_arg: str | None, state: str, stale_key: str, head: str,
                            reviewed_ref: str, since: str | None, reviewed_what: str, without_round_suggested: str,
                            without_round_note: str, not_updated_note: str, re_return_notes: str) -> dict[str, Any]:
    """F112 verdict_stale / F114 return_stale 的重交回出口唯一构建(禁第二份): 已重交回未派下一轮 → 不可推导;
    Return 不合规 → artifact_invalid(产物入口同一校验 validate_task_artifact 原文); 否则 _return_submission_step 重交回。
    已重交回 = 最新 return 记录 ≠ 过期一轮所审的那份(reviewed_ref); 存量缺 reviewed_ref 时退回比时间(since; None = 无时间可比, 不算)。"""
    from tools.aipos_cli.artifact_ingest import validate_task_artifact

    tip = str(stale.get("tip") or "")
    base = {"task_id": task_id, "current_node": state, "current_state": "claimed", "verb": "lybra_queue_return_dry_run",
            stale_key: stale}
    latest_return = records.get("latest_return") or {}
    latest_ref = str(latest_return.get("return_id") or "")
    re_returned = (latest_ref != reviewed_ref) if (reviewed_ref and latest_ref) else (
        since is not None and str(latest_return.get("returned_at") or "") > since)
    if re_returned:
        from tools.aipos_cli.audit_derivation import audit_round_suffixes

        return {**base, "derivable": False, "triggered_by": "advisor", "command": "",
                "missing_records": [f"重交回 {latest_return.get('return_id')} 已落({reviewed_what} {reviewed_ref or '更早一份'}), 但门未派生下一轮审计卡"
                                    f"(审计轮 {records.get('audit_round_ids') or []}, 演进声明 {audit_round_suffixes()[:3]}…)"],
                "suggested_action": without_round_suggested,
                "notes": f"{state}: {head}; {without_round_note}"}
    check = validate_task_artifact(task_id, workspace_root)
    if not check.get("ok"):
        return {**base, "derivable": False, "triggered_by": "executor", "command": "",
                "missing_records": [f"{check.get('category')}: {r}" for r in check.get("reasons") or []],
                "suggested_action": (f"执行体把 Return frontmatter 更新为卡分支当前 tip(commit_sha={tip}, tree_hash={stale.get('tree_hash')}), "
                                     "产品随后重交回并派复审"),
                "notes": f"{state}: {head}; {not_updated_note}",
                "action": {"type": "artifact_invalid", "card": task_id, "path": str(check.get("path") or "")}}
    if not claimer:
        return _not_derivable_no_claim(task_id, node=state, state="claimed", verb="lybra_queue_return_dry_run",
                                       triggered_by="executor", notes=f"{state}: {head}; 无 claim 记录, 重交回 actor 无据(AIPOS-F73E 件①)")
    return_path = Path(str(check["path"]))
    summary = extract_return_summary_text(return_path.read_text(encoding="utf-8")) or ""
    step = _return_submission_step(
        workspace_root, task_id, fm, claimer=claimer, conn_arg=conn_arg, result_summary=summary, return_path=return_path,
        node=state,
        suggested_action="重交回(Return 已是卡分支当前 tip): 门落新 return 记录并按卡号演进声明派下一轮审计",
        notes=re_return_notes,
    )
    step[stale_key] = stale
    return step


def _audit_round_guard(workspace_root: Path, audit_task_id: str, fm: dict[str, Any], conn_arg: str | None,
                       *, queue_dir: str = "claimed") -> dict[str, Any] | None:
    """AIPOS-F114 件②: 审计卡(pending/claimed)是否还可认领/开工/入门。None = 可(走既有审计卡推导); 否则不可推导步:
      - 本轮已被取代(audit_derivation.superseding_round: 后一轮派审记录 supersedes=本轮)→ triggered_by none, superseded_by;
      - 被审卡交回已过期且过期的正是本轮(return_staleness 唯一判据)→ triggered_by advisor(驱动方), return_stale, 出口 = 重交回命令。
    开工核验(kickoff_refusal)据 superseded_by / return_stale 给 ROUND_SUPERSEDED / RETURN_STALE; 产物入口据此拒报告入门。"""
    from tools.aipos_cli.audit_derivation import audit_card_reviewed_id, superseding_round

    state = str(return_stale_declaration()["state"])
    successor = superseding_round(audit_task_id, workspace_root, fm)
    if successor is not None:
        return {
            "task_id": audit_task_id, "derivable": False, "current_node": state, "current_state": queue_dir, "triggered_by": "none",
            "command": "", "verb": "", "missing_records": [],
            "suggested_action": f"无: 本轮已被 {successor['audit_task_id']} 取代(派审记录 {successor['dispatch_id']} supersedes={audit_task_id}), "
                                f"本轮不开工、报告不入门; 审计在 {successor['audit_task_id']} 进行",
            "notes": f"{state}: 审计轮 {audit_task_id} 已作废(AIPOS-F114, 声明 transitions nodes.N3.return_stale.outlet.superseded_round)",
            "superseded_by": successor,
        }
    reviewed = audit_card_reviewed_id(audit_task_id, fm)
    if not reviewed:
        return None
    reviewed_path, _q = _find_task_in_queue(workspace_root, reviewed)
    if reviewed_path is None:
        return None
    stale = return_staleness(workspace_root, reviewed, _read_frontmatter(reviewed_path))
    if stale is None or str(stale.get("audit_task_id") or "") != audit_task_id:
        return None
    re_return = ingest_command(reviewed, "return", workspace_root, conn_arg)
    return {
        "task_id": audit_task_id, "derivable": False, "current_node": state, "current_state": queue_dir, "triggered_by": "advisor",
        "command": "", "verb": "", "missing_records": [f"交回已过期: {stale['reason']}"],
        "suggested_action": (f"交回已过期, 等驱动方重交回被审卡 {reviewed}: {re_return}"
                             f"(产品随后派下一轮审计, 本轮 {audit_task_id} 作废; 审计体不开工、旧 tip 报告不入门)"),
        "notes": f"{state}: 审计轮 {audit_task_id} 所审交回已过期, 不得审旧 tip(AIPOS-F114 件②, 声明 transitions nodes.N3.return_stale)",
        "return_stale": stale,
        "action": {"type": "return_stale", "card": reviewed, "command": re_return},
    }


def derive_next_step(
    task_id: str,
    workspace_root: Path,
) -> dict[str, Any]:
    """推导单卡下一步(见 _derive_next_step)。AIPOS-F100 件②: 推导途中任何卡/记录/产物 frontmatter 读不出
    (FrontmatterReadError)= 硬停 frontmatter_unreadable_stop, 不推导放行动作。"""
    from tools.aipos_cli.frontmatter import FrontmatterReadError
    from tools.aipos_cli.task_loader import task_card_lookup_scope

    try:
        # AIPOS-F112: 推导只读, 一次推导内的卡查找(本卡/审计轮 R…R2/被审卡)共用一趟队列索引(既有 task_card_lookup_scope)
        with task_card_lookup_scope(Path(workspace_root)):
            return _derive_next_step(task_id, workspace_root)
    except FrontmatterReadError as exc:
        return frontmatter_unreadable_stop(task_id, exc)


def _derive_next_step(
    task_id: str,
    workspace_root: Path,
) -> dict[str, Any]:
    """推导单卡下一步。

    返回:
    {
        "task_id": str,
        "derivable": bool,  # 是否可推导
        "current_node": str | None,  # 当前节点名(如 "claim", "return")
        "current_state": str,  # 队列位置
        "triggered_by": str,  # 该谁动(角色)
        "command": str,  # 可照抄命令
        "verb": str,  # 门动词名
        "missing_records": list[str],  # 缺失记录(fail-closed 时用)
        "suggested_action": str,  # 建议动作
        "notes": str,
    }
    """
    workspace_root = Path(workspace_root)

    # AIPOS-F122 件③: 存量冻结卡(判定唯一实现 legacy_baseline.frozen_tasks)= 历史, 不推导任何推进步
    # (先于找卡/读卡面: 冻结卡卡面可能读不出, 清单按文件名回落记卡号)
    frozen_stop = legacy_frozen_stop(workspace_root, task_id, None)
    if frozen_stop is not None:
        return frozen_stop

    # 1. 找任务卡(AIPOS-F78B 件①: frontmatter task_id 精确匹配; 多义 = 不可推导点名两份文件)
    from tools.aipos_cli.task_loader import AmbiguousTaskCard

    try:
        task_path, queue_dir = _find_task_in_queue(workspace_root, task_id)
    except AmbiguousTaskCard as exc:
        return {
            "task_id": task_id,
            "derivable": False,
            "current_node": None,
            "current_state": "ambiguous",
            "triggered_by": "advisor",
            "command": "",
            "verb": "",
            "missing_records": [str(exc)],
            "suggested_action": "同一 task_id 只能有一份卡文件: 撤废/合并多余的那份后重推导",
            "notes": "",
        }
    if not task_path:
        return {
            "task_id": task_id,
            "derivable": False,
            "current_node": None,
            "current_state": "not_found",
            "triggered_by": "unknown",
            "command": "",
            "verb": "",
            "missing_records": [f"queue 目录中找不到任务卡 {task_id}"],
            "suggested_action": f"确认任务 ID 正确,或检查 5_tasks/queue/ 各子目录",
            "notes": "",
        }

    fm = _read_frontmatter(task_path)
    records = _read_task_records(workspace_root, task_id)
    has_return_artifact = _check_return_artifact(workspace_root, task_id)
    has_audit_card = _check_audit_card(workspace_root, task_id)
    verdict_artifact = _check_verdict_artifact(workspace_root, task_id, fm)
    from tools.aipos_cli.audit_derivation import audit_card_reviewed_id, is_audit_card as _is_audit_card

    task_mode = fm.get("task_mode", "code")
    assigned_to = fm.get("assigned_to") or fm.get("agent_instance") or ""
    audit_required = fm.get("audit") == "required"
    owner_verify = fm.get("owner_verify") == "required"
    is_audit_card = _is_audit_card(task_id, fm)  # AIPOS-F112: 认得复审轮 R2/R3…(原 endswith("R"))

    connection_json = _find_connection_json(workspace_root)

    # 公共参数
    conn_arg = connection_json

    # -----------------------------------------------------------------------
    # AIPOS-F114 件②: 审计卡开工/裁决入口以当前一致为准——本轮已被取代(后一轮派审记录 supersedes)或被审卡交回已过期
    # (本轮审的不是卡分支当前 tip)= 不可推导: 不开工、报告不入门, 出口 = 驱动方重交回被审卡(产品随后派下一轮)
    # -----------------------------------------------------------------------
    if is_audit_card and queue_dir in ("pending", "claimed"):  # 未认领的在途轮同判: 作废/过期的轮次不再派认领
        round_guard = _audit_round_guard(workspace_root, task_id, fm, conn_arg, queue_dir=queue_dir)
        if round_guard is not None:
            return round_guard

    # -----------------------------------------------------------------------
    # 审计卡特殊处理: claimed + 有 verdict 报告 → 提交裁决
    # -----------------------------------------------------------------------
    if is_audit_card and queue_dir == "claimed" and verdict_artifact:
        # 从 verdict 报告提取参数
        verdict_fm = _read_frontmatter(verdict_artifact, allow_missing_block=True)
        reviewed_task_id = verdict_fm.get("reviewed_task_id") or audit_card_reviewed_id(task_id, fm)
        verdict = verdict_fm.get("verdict", "PASS")
        # AIPOS-F73E 件①: actor/agent_instance = 审计卡 claim 记录的审计实例(禁读报告自报/卡面/驱动方); 无 claim 记录不可推导
        actor = _claimer_instance(records)
        if not actor:
            return _not_derivable_no_claim(task_id, node="audit_verdict", state="claimed", verb="lybra_audit_verdict_dry_run",
                                           triggered_by="auditor", notes="N4: 审计卡有 VERDICT 报告但无 claim 记录, 裁决 actor 无据(AIPOS-F73E 件①)")
        # AIPOS-F89 件③c: 报告完成判据(artifact_ingest.verdict.readiness)不成立 = 已交未完成 → 硬停点名缺项(不空等, 不入门)
        unfilled = missing_verdict_frontmatter(verdict_fm)
        if unfilled:
            return {
                "task_id": task_id,
                "derivable": False,
                "current_node": "audit_verdict",
                "current_state": "claimed",
                "triggered_by": "auditor",
                "command": "",
                "verb": "lybra_audit_verdict_dry_run",
                "missing_records": [f"审计报告 frontmatter 未填/仍为占位: {', '.join(unfilled)}"],
                "suggested_action": f"审计体在 {verdict_artifact} 填实 {', '.join(unfilled)}(声明 transitions artifact_ingest.verdict.readiness)",
                "notes": "N4: 审计报告已交但未完成(完成判据 = 必填 frontmatter 全部已填且非占位, AIPOS-F89 件③c)",
                "action": {"type": "artifact_invalid", "card": task_id, "path": str(verdict_artifact)},
            }
        agent_inst = actor
        
        # AIPOS-F73前置①: 从被审卡分支提取 artifact_subject (code 卡必填)
        reviewed_task_path, _ = _find_task_in_queue(workspace_root, reviewed_task_id)
        reviewed_fm = _read_frontmatter(reviewed_task_path) if reviewed_task_path else {}
        reviewed_task_mode = reviewed_fm.get("task_mode", "code")
        # AIPOS-F73D + F78C: 卡分支活在被审卡声明的产品仓(resolve_card_repo: lane.repo → repos 清单 → code_repo → 治理根)
        from tools.aipos_cli.workspace_config import CardRepoUnresolved

        try:
            reviewed_repo = _card_repo_root(workspace_root, reviewed_task_id, reviewed_fm) if reviewed_task_mode == "code" else workspace_root
        except CardRepoUnresolved as exc:
            return {
                "task_id": task_id,
                "derivable": False,
                "current_node": "audit_verdict",
                "current_state": "claimed",
                "triggered_by": "auditor",
                "command": "",
                "verb": "lybra_audit_verdict_dry_run",
                "missing_records": [f"被审卡 {reviewed_task_id} 产品仓不可解析: {exc}"],
                "suggested_action": exc.reason,
                "notes": f"N4: 被审卡产品仓解析失败({exc.code}), 裁决 artifact_subject 无据(AIPOS-F78C)",
                "action": {"type": "artifact_invalid", "card": reviewed_task_id, "path": ""},
            }
        artifact_subject = _extract_artifact_subject_from_branch(reviewed_repo, reviewed_task_id, reviewed_task_mode)
        # AIPOS-F78B 件③: 驱动方信封(覆盖被审卡, 与 loop 同一判据)在 → 一阶段 PreAuthorized; 否则 Supervised 形(执行时 exit 5)
        driver_policy = _driver_envelope_ref(workspace_root, reviewed_task_id, reviewed_fm, conn_arg)
        
        # AIPOS-F90 件②: 裁决证据 = 审计报告本身(「一句话结论」+ 报告相对路径), 产物入口读报告提交, 驱动方不手提
        try:
            report_text = verdict_artifact.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            import sys

            print(f"Warning: 审计报告不可读 {verdict_artifact}: {exc}", file=sys.stderr)
            report_text = ""
        try:
            report_ref = str(verdict_artifact.resolve().relative_to(Path(workspace_root).resolve()))
        except ValueError:
            report_ref = str(verdict_artifact)
        cmd = _build_verdict_submit_command(
            reviewed_task_id=reviewed_task_id,
            audit_task_id=task_id,
            actor=actor,
            agent_instance=agent_inst,
            owner_policy_ref=driver_policy,
            connection_json=conn_arg,
            verdict=verdict,
            artifact_subject=artifact_subject,
            autonomy_mode="PreAuthorized" if driver_policy else "Supervised",
            findings_summary=extract_return_summary_text(report_text) if report_text else None,
            evidence_refs=[report_ref],
        )
        return {
            "task_id": task_id,
            "derivable": True,
            "current_node": "audit_verdict",
            "current_state": "claimed",
            "triggered_by": "auditor",
            # AIPOS-F90 件②: 驱动方只见产物入口命令(读报告 frontmatter、绑被审分支 tip、产品填模型字段、门侧快照报告);
            # shell_command = 入口内部执行的同一条薄壳命令(唯一构建 _build_verdict_submit_command)
            "command": ingest_command(task_id, "verdict", workspace_root, conn_arg),
            "shell_command": cmd,
            "verb": "lybra_audit_verdict_dry_run",
            "missing_records": [],
            "suggested_action": "提交审计裁决",
            "notes": f"N4: 审计卡 claimed + VERDICT 报告存在,需提交裁决",
        }
    
    # 审计卡 claimed 但无 verdict 报告 → 不可推导
    if is_audit_card and queue_dir == "claimed" and not verdict_artifact:
        return {
            "task_id": task_id,
            "derivable": False,
            "current_node": "claim",
            "current_state": "claimed",
            "triggered_by": "auditor",
            "command": "",
            "verb": "",
            "missing_records": [f"VERDICT-{task_id}.md 审计报告"],
            "suggested_action": "等待 auditor 完成审计并生成 VERDICT 报告",
            "notes": "审计卡 claimed + 无 VERDICT 报告,等待审计员完成工作",
        }

    # -----------------------------------------------------------------------
    # 按 queue 位置 + 记录推导下一步
    # -----------------------------------------------------------------------

    # --- pending → N1: claim ---
    if queue_dir == "pending":
        # AIPOS-F90 件①: 认领实例 = 卡面 agent_instance(具体实例, 门按 actor==canonical agent_instance 判), 缺省 assigned_to;
        # 门派生的审计卡 assigned_to 是短名(audit_<project>), 用它认领会被裁决角色门拒(ROLE_VIOLATION)
        claimant = str(fm.get("agent_instance") or fm.get("assigned_to") or "").strip()
        if not claimant:
            return {
                "task_id": task_id,
                "derivable": False,
                "current_node": "publish",
                "current_state": "pending",
                "triggered_by": "advisor",
                "command": "",
                "verb": "lybra_queue_claim_dry_run",
                "missing_records": ["卡面 agent_instance / assigned_to(认领实例)"],
                "suggested_action": f"lybra queue amend --task-id {task_id} 补 assigned_to 后重推导",
                "notes": "N0→N1: 卡无认领实例声明, 认领命令 actor 无据(fail-closed)",
            }
        # AIPOS-F133 件③: 依赖未满足 = 不派生认领(判据唯一实现 task_complexity.unmet_dependencies, 与门 claim 校验同一判据;
        # 全部依赖满足才放行, 只读门生记录)。不可推导, 点名未满足依赖与出口; loop 照 not_derivable 停(exit 4), 不空等。
        from tools.aipos_cli.task_complexity import unmet_dependencies

        unmet = unmet_dependencies(fm, workspace_root)
        if unmet:
            return {
                "task_id": task_id,
                "derivable": False,
                "current_node": "publish",
                "current_state": "pending",
                "triggered_by": "none",
                "command": "",
                "verb": "lybra_queue_claim_dry_run",
                "missing_records": unmet,
                "suggested_action": "先推进被依赖卡至满足条件(card.schema dependency_gate), 本卡随后自动成为可认领",
                "notes": "N0→N1: 依赖未满足, 不派生认领(AIPOS-F133)",
                "action": {"type": "dependencies_unmet", "card": task_id,
                           "depends_on": [str(d) for d in (fm.get("depends_on") or [])] if isinstance(fm.get("depends_on"), list) else [str(fm.get("depends_on"))]},
            }
        # AIPOS-F90 件①: 认领一段式 = 驱动方 token + 覆盖驱动方与本卡的信封(与 return/verdict/close 同一判据 _driver_envelope_ref,
        # loop --envelope 经 driver_scope 贯穿); 信封在 → PreAuthorized + owner_policy_ref; 不在 → Supervised 形(执行时 exit 5 带申领出口)
        driver_policy = _driver_envelope_ref(workspace_root, task_id, fm, conn_arg)
        cmd = _build_copyable_command(
            verb_base="claim",
            task_id=task_id,
            actor=claimant,
            agent_instance=claimant,
            autonomy_mode="PreAuthorized" if driver_policy else "Supervised",
            owner_policy_ref=driver_policy,
            connection_json=conn_arg,
        )
        return {
            "task_id": task_id,
            "derivable": True,
            "current_node": "publish",
            "current_state": "pending",
            "triggered_by": "executor",
            "command": cmd,
            "verb": "lybra_queue_claim_dry_run",
            "missing_records": [],
            "suggested_action": "认领任务",
            "notes": f"N0→N1: 任务已发布,等待认领",
        }

    # --- claimed → N2 (return) 或等待执行 ---
    if queue_dir == "claimed":
        latest_claim = records.get("latest_claim")
        latest_return = records.get("latest_return")
        # AIPOS-F73E 件①: 账务动词 actor = claim 记录实例(唯一实现 _claimer_instance); 缺记录时各派生点 fail-closed
        claimer = _claimer_instance(records)

        # 已有 return 记录 → 检查后续节点 N3→N6 (AIPOS-F73B前置零)
        if latest_return:
            latest_audit_dispatch = records.get("latest_audit_dispatch")
            latest_verdict = records.get("latest_verdict")
            latest_closure = records.get("latest_closure")
            
            # 检查是否有 finalization 记录(AIPOS-F120: 唯一判据 finalization_record.existing_finalization_records, finalize 续跑共用)
            from tools.aipos_cli.finalization_record import existing_finalization_records

            finalization_files = existing_finalization_records(workspace_root, task_id)
            has_finalization = bool(finalization_files)
            
            # N5→N6: 有 finalization 但无 closure → close
            if has_finalization and not latest_closure:
                # AIPOS-F73C前置零之一 + F78 前置零③: closure_evidence 从记录自填三字段, 三字段齐才派生
                closure_evidence = {}

                # 1. finalize_commit_hash: 从 finalization 记录读取 merge_commit(F78 声明字段), 兼容 commit/commit_hash
                fin_fm: dict[str, Any] = {}
                if finalization_files:
                    fin_fm = _read_frontmatter(finalization_files[0])
                    merge_commit = str(fin_fm.get("merge_commit") or fin_fm.get("commit") or fin_fm.get("commit_hash") or "").strip()
                    if merge_commit:
                        closure_evidence["finalize_commit_hash"] = merge_commit

                # 2. finalize_return_ref: 从 returns 记录读取
                returns_dir = record_dir(workspace_root, "returns", task_id)
                if returns_dir.is_dir():
                    return_files = sorted(returns_dir.glob("return_*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
                    if return_files:
                        ret_fm = _read_frontmatter(return_files[0])
                        if ret_fm.get("return_id"):
                            closure_evidence["finalize_return_ref"] = ret_fm["return_id"]

                # 3. verdict_ref: 从 audit_verdicts 记录读取
                if latest_verdict and latest_verdict.get("verdict_id"):
                    closure_evidence["verdict_ref"] = latest_verdict["verdict_id"]

                missing_evidence = [
                    f"{key}({src})"
                    for key, src in (
                        ("finalize_commit_hash", "finalization 记录 merge_commit"),
                        ("finalize_return_ref", "return 记录 return_id"),
                        ("verdict_ref", "裁决记录 verdict_id"),
                    )
                    if not closure_evidence.get(key)
                ]
                if missing_evidence:
                    return {
                        "task_id": task_id,
                        "derivable": False,
                        "current_node": "finalize",
                        "current_state": "claimed",
                        "triggered_by": "advisor",
                        "command": "",
                        "verb": "lybra_queue_close_dry_run",
                        "missing_records": missing_evidence,
                        "suggested_action": "补齐记录后重推导(finalization 记录须含 merge_commit: transitions N5.record.merge_commit)",
                        "notes": "N5→N6: closure_evidence 三字段未齐, 不派生 close(AIPOS-F78 前置零③)",
                    }

                import json
                closure_evidence_json = json.dumps(closure_evidence)

                # AIPOS-F73E 件①(改写 F78 前置零②): actor = 该卡 claim 记录的执行实例(F73C 定案: token 归驱动方, actor 归认领实例);
                # 门按 actor==claimer 判(queue_mutation complete), 驱动方实例当 actor 必被拒(F78 活体实撞); 无 claim 记录不可推导
                if not claimer:
                    return _not_derivable_no_claim(task_id, node="finalize", state="claimed", verb="lybra_queue_close_dry_run",
                                                   triggered_by="advisor", notes="N5→N6: 已 finalize 但无 claim 记录, close actor 无据(AIPOS-F73E 件①)")

                driver_policy = _driver_envelope_ref(workspace_root, task_id, fm, conn_arg)  # AIPOS-F78B 件③
                cmd = _build_close_command(
                    task_id=task_id,
                    actor=claimer,
                    connection_json=conn_arg,
                    closure_evidence_json=closure_evidence_json,
                    autonomy_mode="PreAuthorized" if driver_policy else "Supervised",
                    owner_policy_ref=driver_policy,
                )
                return {
                    "task_id": task_id,
                    "derivable": True,
                    "current_node": "finalize",
                    "current_state": "claimed",
                    "triggered_by": "advisor",
                    "command": cmd,
                    "verb": "lybra_queue_close_dry_run",
                    "missing_records": [],
                    "suggested_action": "结案",
                    "notes": "N5→N6: 已 finalize,需 close",
                }
            
            # N6: 有 closure → 先看治理是否已落账(AIPOS-F94 件①), 未落账 = 派生 N6 落账步; 已落账 = 已完成
            if latest_closure:
                landing_step = _derive_n6_landing(workspace_root, task_id, "claimed", conn_arg)
                if landing_step is not None:
                    return landing_step
                return {
                    "task_id": task_id,
                    "derivable": False,
                    "current_node": "close",
                    "current_state": "claimed",
                    "triggered_by": "none",
                    "command": "",
                    "verb": "",
                    "missing_records": [],
                    "suggested_action": "无(任务已结案)",
                    "notes": "N6: 任务已有 closure 记录,无下一步",
                }
            
            # AIPOS-F114 件①: 本轮审计在途(已派审未出本轮裁决)时先核交回是否已过期(本轮所审交回绑定的 tip ≠ 卡分支 tip);
            # 过期 = return_stale → 重交回派下一轮(与 F112 verdict_stale 同一重交回构建), 不再等一轮注定被入口拒的旧 tip 审计
            if not has_finalization:
                rstale = return_staleness(workspace_root, task_id, fm, records)
                if rstale is not None:
                    return _derive_return_stale(workspace_root, task_id, fm, records, rstale, claimer=claimer, conn_arg=conn_arg)

            # AIPOS-F112 件②: 复审轮(R2/R3…)已派生且该轮尚无裁决 → 等最新一轮审计卡(loop 认它等待/拉起), 不再拿上一轮裁决派 finalize
            current_audit_id = str(records.get("audit_task_id") or f"{task_id}R")
            audit_rounds = list(records.get("audit_round_ids") or [])
            if (latest_verdict and not has_finalization and len(audit_rounds) >= 2
                    and str(latest_verdict.get("audit_task_id") or "") != current_audit_id):
                return {
                    "task_id": task_id,
                    "derivable": False,
                    "current_node": "audit_dispatch",
                    "current_state": "claimed",
                    "triggered_by": "auditor",
                    "command": "",
                    "verb": "",
                    "missing_records": [f"复审卡 {current_audit_id} 的审计报告与裁决"],
                    "suggested_action": f"等待审计体完成复审 {current_audit_id}(上一轮 {latest_verdict.get('audit_task_id') or '?'} 的裁决已被取代)",
                    "notes": f"N3: 复审轮 {current_audit_id} 在途(审计轮 {audit_rounds}), 等最新一轮裁决(AIPOS-F112)",
                    "action": {"type": "await_artifact", "card": current_audit_id},
                }

            # N4→N5: 有 verdict 但无 finalization → finalize
            if latest_verdict and not has_finalization:
                verdict_result = latest_verdict.get("verdict", "")
                if verdict_result in ("PASS", "PASS_WITH_NOTES"):
                    # AIPOS-F78B 件②: 产品仓不在本机(finalize_mode=external) → 不派生 lybra finalize, 等外部 FINALIZE 卡 Return 经 ingest 铸记录
                    if _finalize_mode(workspace_root) == "external":
                        return _derive_external_finalize(workspace_root, task_id, fm, claimer=claimer, verdict_result=verdict_result)
                    # AIPOS-F112 件①: 派 finalize 前核裁决是否仍覆盖卡分支 tip(与 finalize F70 同一判据); 过期 = verdict_stale, 不派 finalize
                    stale = verdict_staleness(workspace_root, task_id, fm)
                    if stale is not None:
                        return _derive_verdict_stale(workspace_root, task_id, fm, records, stale, claimer=claimer, conn_arg=conn_arg)
                    # AIPOS-F73D 前置一② + F78C: finalize 模板必带 --actor, --workspace-root=该卡声明的产品仓(resolve_card_repo), --governance-root=治理根
                    from tools.aipos_cli.workspace_config import CardRepoUnresolved

                    try:
                        code_repo_root = _card_repo_root(workspace_root, task_id, fm, allow_governance_root=False)
                    except CardRepoUnresolved as exc:
                        return {
                            "task_id": task_id,
                            "derivable": False,
                            "current_node": "audit_verdict",
                            "current_state": "claimed",
                            "triggered_by": "advisor",
                            "command": "",
                            "verb": "lybra_finalize",
                            "missing_records": [f"卡 {task_id} 产品仓声明(finalize --workspace-root 依据): {exc}"],
                            "suggested_action": exc.reason,
                            "notes": f"N4→N5: 裁决 {verdict_result}, 但产品仓不可解析({exc.code}), 不可派生 finalize 命令",
                        }
                    # AIPOS-F73E 件①(改写 F78 前置零②): finalize actor = 该卡 claim 记录的执行实例; 无 claim 记录不可推导
                    if not claimer:
                        return _not_derivable_no_claim(task_id, node="audit_verdict", state="claimed", verb="lybra_finalize",
                                                       triggered_by="advisor", notes=f"N4→N5: 裁决 {verdict_result}, 但无 claim 记录, finalize actor 无据(AIPOS-F73E 件①)")
                    cmd = _build_finalize_command(
                        task_id=task_id,
                        actor=claimer,
                        code_repo_root=code_repo_root,
                        governance_root=workspace_root,
                    )
                    return {
                        "task_id": task_id,
                        "derivable": True,
                        "current_node": "audit_verdict",
                        "current_state": "claimed",
                        "triggered_by": "advisor",
                        "command": cmd,
                        "verb": "lybra_finalize",
                        "missing_records": [],
                        "suggested_action": "finalize 并部署",
                        "notes": f"N4→N5: 裁决 {verdict_result},需 finalize",
                    }
                else:
                    # FAIL/BLOCK 等非 PASS 裁决
                    return {
                        "task_id": task_id,
                        "derivable": False,
                        "current_node": "audit_verdict",
                        "current_state": "claimed",
                        "triggered_by": "executor",
                        "command": "",
                        "verb": "",
                        "missing_records": [],
                        "suggested_action": f"处理裁决 {verdict_result} (返工或修复)",
                        "notes": f"N4: 裁决 {verdict_result},需人工处理",
                    }
            
            # N3→N4: 已派审但无 verdict → 等待审计体产物
            if latest_audit_dispatch and not latest_verdict:
                audit_id = current_audit_id  # AIPOS-F112: 当前一轮(原写死 <ID>R)
                return {
                    "task_id": task_id,
                    "derivable": False,
                    "current_node": "audit_dispatch",
                    "current_state": "claimed",
                    "triggered_by": "auditor",
                    "command": "",
                    "verb": "",
                    "missing_records": [f"审计卡 {audit_id} 的 VERDICT 报告"],
                    "suggested_action": f"等待审计体完成 {audit_id} 并生成 VERDICT",
                    "notes": "N3: 已派审,等待审计体产物",
                    "action": {"type": "await_artifact", "card": audit_id},
                }
            
            # N2→N3: 已 return + 未派审 → 派审。AIPOS-F73D: 审计卡由派审动作生成(transitions N3.automation.creates_audit_card),
            # 执行体零门(F73C)后不再"自产审计卡"; 审计卡已存在时派审幂等(AIPOS-C1 大项C②)。
            if not latest_audit_dispatch and (task_mode == "code" or audit_required):
                audit_id = f"{task_id}R"
                # AIPOS-F103 件④: 信封 = 覆盖驱动方与本卡的有效信封(唯一挑选 autonomy_policy.select_envelope, 经 _driver_envelope_ref)
                policy_ref = _driver_envelope_ref(workspace_root, task_id, fm, conn_arg)
                # AIPOS-F102 件①: 派审 actor = 驱动方实例(roles.schema driver.role_class 对应的驱动方, _driver_actor 唯一实现:
                # loop --actor → 治理根 .lybra/role instance → connection.json 驱动方 token 绑定实例), 原写死 lybra 身份退役;
                # 解析不到 = 不可推导(点名缺项), 禁回退任何项目字面
                dispatch_actor = _driver_actor(workspace_root, connection_json=conn_arg)
                if not dispatch_actor:
                    return {
                        "task_id": task_id,
                        "derivable": False,
                        "current_node": "return",
                        "current_state": "claimed",
                        "triggered_by": "advisor",
                        "command": "",
                        "verb": "lybra_audit_dispatch_dry_run",
                        "missing_records": [DRIVER_ACTOR_MISSING],
                        "suggested_action": "补驱动方身份(lybra loop --actor 或治理根 .lybra/role instance)后重推导",
                        "notes": "N2→N3: 派审 actor 无据(驱动方身份解析不到, AIPOS-F102 件①)",
                        "action": {"type": "record_missing", "card": task_id, "record": "driver_actor"},
                    }
                # AIPOS-F102 件①: 审计卡认领实例 = 被审卡 audit_by 声明, 缺则按项目推导(audit_derivation.resolve_audit_instance 唯一实现)
                from tools.aipos_cli.audit_derivation import resolve_audit_instance

                try:
                    audit_instance = resolve_audit_instance(fm, workspace_root)
                except ValueError as exc:
                    return {
                        "task_id": task_id,
                        "derivable": False,
                        "current_node": "return",
                        "current_state": "claimed",
                        "triggered_by": "advisor",
                        "command": "",
                        "verb": "lybra_audit_dispatch_dry_run",
                        "missing_records": [f"卡 {task_id} 审计实例声明(audit_by)或项目声明(project): {exc}"],
                        "suggested_action": f"lybra queue amend --task-id {task_id} 补 audit_by 后重推导",
                        "notes": "N2→N3: 审计卡认领实例无据(AIPOS-F102 件①)",
                        "action": {"type": "record_missing", "card": task_id, "record": "audit_by"},
                    }
                cmd = _build_audit_dispatch_command(
                    task_id=task_id,
                    actor=dispatch_actor,
                    agent_instance=dispatch_actor,
                    owner_policy_ref=policy_ref,
                    connection_json=conn_arg,
                    audit_task_id=audit_id,
                    audit_agent_instance=audit_instance,
                )
                return {
                    "task_id": task_id,
                    "derivable": True,
                    "current_node": "return",
                    "current_state": "claimed",
                    "triggered_by": "advisor",
                    "command": cmd,
                    "verb": "lybra_audit_dispatch_dry_run",
                    "missing_records": [],
                    "suggested_action": "派发审计",
                    "notes": "N2→N3: 已 return, 需派审" + ("(审计卡已存在, 派审幂等)" if has_audit_card else "(派审生成审计卡)"),
                }
            # 非代码卡,不需要独立审计
            if task_mode not in ("code",) and not audit_required:
                return {
                    "task_id": task_id,
                    "derivable": True,
                    "current_node": "return",
                    "current_state": "claimed",
                    "triggered_by": "advisor",
                    "command": f"# 非代码卡(task_mode={task_mode}):顾问审核后直接提交裁决",
                    "verb": "lybra_audit_verdict_dry_run",
                    "missing_records": [],
                    "suggested_action": "顾问审核并提交裁决",
                    "notes": "非代码卡快车道:顾问直接审",
                }
            return {
                "task_id": task_id,
                "derivable": False,
                "current_node": "return",
                "current_state": "claimed",
                "triggered_by": "advisor",
                "command": "",
                "verb": "",
                "missing_records": [],
                "suggested_action": "等待审计流程",
                "notes": "已 return,审计流程进行中",
            }

        # 有 RETURN.md 但还没 return 记录 → 交回工作(N2)
        if has_return_artifact:
            # 第4轮③: 从 RETURN.md 提取 result_summary
            result_summary = _extract_return_summary(workspace_root, task_id)
            if not result_summary:
                # RETURN.md 存在但无法提取一句话结论,fail-closed
                return {
                    "task_id": task_id,
                    "derivable": False,
                    "current_node": "claim",
                    "current_state": "claimed",
                    "triggered_by": "executor",
                    "command": "",
                    "verb": "",
                    "missing_records": ["RETURN.md 存在但无法提取一句话结论"],
                    "suggested_action": "检查 RETURN.md 是否包含『一句话结论』节",
                    "notes": "RETURN.md 格式不完整,推导不出",
                }

            # AIPOS-F78 件③: Return 必填 frontmatter(transitions artifact_ingest.return.required_frontmatter)缺 = 不可推导
            # (action=artifact_invalid, loop 据此 exit 4 而非空等), 齐则派生与 ingest 同一条 return 命令
            return_path = _return_artifact_path(workspace_root, task_id)
            missing_fm = missing_return_frontmatter(_read_frontmatter(return_path, allow_missing_block=True))
            if missing_fm:
                # AIPOS-F123 件①: 拒因原文带补法(逐键说明 = 报告必填契约单源 report_frontmatter_contract, 分支 = 本卡分支)——
                # 收编的人肉期手写 Return 常缺这三项, 出口须能照做
                how = {e["key"]: e["hint"] for e in report_frontmatter_contract("return", branch_task_id=task_id)}
                return {
                    "task_id": task_id,
                    "derivable": False,
                    "current_node": "claim",
                    "current_state": "claimed",
                    "triggered_by": "executor",
                    "command": "",
                    "verb": "lybra_queue_return_dry_run",
                    "missing_records": [f"Return frontmatter 缺 {k}" for k in missing_fm],
                    "suggested_action": (f"执行体在 {return_path} 的 frontmatter 补齐 {', '.join(missing_fm)}"
                                         f"(声明: transitions.schema artifact_ingest.return.required_frontmatter)。补法: "
                                         + "; ".join(f"{k} = {how.get(k, '按声明填写实值')}" for k in missing_fm)),
                    "notes": "Return 已落盘但必填 frontmatter 不齐, 产品无法铸交回记录(AIPOS-F78 件③ fail-closed)",
                    "action": {"type": "artifact_invalid", "card": task_id, "path": str(return_path)},
                }

            if not claimer:
                return _not_derivable_no_claim(task_id, node="claim", state="claimed", verb="lybra_queue_return_dry_run",
                                               triggered_by="executor", notes="N1→N2: Return 已落盘但无 claim 记录, return actor 无据(AIPOS-F73E 件①)")
            return _return_submission_step(
                workspace_root, task_id, fm, claimer=claimer, conn_arg=conn_arg, result_summary=result_summary,
                return_path=return_path, node="claim",
                suggested_action="交回工作(RETURN.md 已存在,执行 return)",
                notes="N1→N2: RETURN.md 已生成,需执行 return 动词",
            )

        # 无 return 记录也无 RETURN.md → 检查是否有返工节 (AIPOS-F75 件③)
        rework_rounds = fm.get("rework_rounds", [])
        if isinstance(rework_rounds, list) and rework_rounds:
            # 取最新且未销账的轮次
            uncleared_rounds = [r for r in rework_rounds if not r.get("cleared_at")]
            if uncleared_rounds:
                # 有未销账返工节,输出点杀清单
                latest_round = uncleared_rounds[-1]
                focus_items = latest_round.get("focus_items", [])
                round_num = latest_round.get("round", len(rework_rounds))
                verdict_ref = latest_round.get("verdict_ref", "")
                
                focus_text = "\n".join(f"  - {item}" for item in focus_items) if focus_items else "  (无具体项)"
                
                return {
                    "task_id": task_id,
                    "derivable": True,
                    "current_node": "claim",
                    "current_state": "claimed",
                    "triggered_by": "executor",
                    "command": "",
                    "verb": "",
                    "missing_records": [],
                    "suggested_action": f"执行第 {round_num} 轮返工，完成后生成 RETURN.md 并执行 return",
                    "notes": f"AIPOS-F75: 卡面有返工节 (第 {round_num} 轮，依据 {verdict_ref})，点杀清单:\n{focus_text}",
                    "rework_round": latest_round,
                }
            # 全部返工节已销账，走原逻辑
        
        # 检查 events 是否有失败信号
        events = records.get("events", [])
        has_blocked = any(
            str(e.get("event_type") or e.get("event_kind") or "") in ("blocked", "launch_failed")
            for e in events
        )
        if has_blocked:
            return {
                "task_id": task_id,
                "derivable": False,
                "current_node": "claim",
                "current_state": "claimed",
                "triggered_by": "unknown",
                "command": "",
                "verb": "",
                "missing_records": ["RETURN.md 工作产物", "return 记录"],
                "suggested_action": "执行体遇到阻塞,需人工检查或 resume 轮派工",
                "notes": "claimed + 有 blocked/launch_failed 事件,无 return 产物",
            }

        # 事实不足以推导 → fail-closed
        return {
            "task_id": task_id,
            "derivable": False,
            "current_node": "claim",
            "current_state": "claimed",
            "triggered_by": "executor",
            "command": "",
            "verb": "",
            "missing_records": ["RETURN.md 工作产物", "return 记录"],
            "suggested_action": "等待执行体完成工作并生成 RETURN.md,或检查执行状态",
            "notes": "claimed + 无 return 产物/记录,事实不足以推导下一步",
        }

    # --- completed → N6 落账(AIPOS-F94 件①) 或 done ---
    if queue_dir == "completed":
        landing_step = _derive_n6_landing(workspace_root, task_id, "completed", conn_arg)
        if landing_step is not None:
            return landing_step
        return {
            "task_id": task_id,
            "derivable": True,
            "current_node": "close",
            "current_state": "completed",
            "triggered_by": "none",
            "command": "# 任务已完成,无下一步",
            "verb": "",
            "missing_records": [],
            "suggested_action": "无(任务已结束)",
            "notes": "N6: 任务已结案",
        }

    # --- blocked → 需人工裁定 ---
    if queue_dir == "blocked":
        return {
            "task_id": task_id,
            "derivable": False,
            "current_node": "blocked",
            "current_state": "blocked",
            "triggered_by": "advisor",
            "command": "",
            "verb": "",
            "missing_records": ["blocked 恢复策略需人工裁定"],
            "suggested_action": "检查阻塞原因,决定 reopen 或释放",
            "notes": "任务被阻塞,需人工裁定恢复策略",
        }

    # --- 其余终态(AIPOS-F117 件③, gap #62: 终态集合读 enums queue_state terminal 声明; completed 已在上方走 N6 落账)→ 无下一步 ---
    from tools.aipos_cli.task_loader import QUEUE_TERMINAL_STATES

    if queue_dir in QUEUE_TERMINAL_STATES:
        return {
            "task_id": task_id,
            "derivable": True,
            "current_node": None,
            "current_state": queue_dir,
            "triggered_by": "none",
            "command": f"# 任务已处于终态 {queue_dir},无下一步",
            "verb": "",
            "missing_records": [],
            "suggested_action": "无(任务已结束)",
            "notes": f"终态 {queue_dir}(enums.schema queue_state terminal=true), 推导核不派生推进步",
        }

    # --- 未知 queue 位置 ---
    return {
        "task_id": task_id,
        "derivable": False,
        "current_node": None,
        "current_state": queue_dir or "unknown",
        "triggered_by": "unknown",
        "command": "",
        "verb": "",
        "missing_records": [f"无法识别的队列位置: {queue_dir}"],
        "suggested_action": "检查任务卡在 queue/ 中的位置是否正确",
        "notes": "",
    }


def legacy_frozen_stop(workspace_root: Path, task_id: str, queue_dir: str | None = None) -> dict[str, Any] | None:
    """AIPOS-F122 件③: 冻结卡 / 清单读不出 → 不可推导硬停项(带解冻 / 修清单出口); 未冻结 → None。"""
    from tools.aipos_cli.legacy_baseline import LegacyBaselineError, frozen_rejection

    try:
        rejection = frozen_rejection(Path(workspace_root), [task_id], action="推进(next/loop)")
    except LegacyBaselineError as exc:
        return {
            "task_id": task_id, "derivable": False, "current_node": None, "current_state": "legacy_baseline_invalid",
            "triggered_by": "advisor", "command": "", "verb": "", "missing_records": [str(exc)],
            "suggested_action": "修正存量冻结清单条目 / project.json legacy_baseline 后重推导(清单读不出 = 不放行)",
            "notes": "AIPOS-F122: 存量冻结清单 fail-closed", "action": {"type": "legacy_baseline_invalid", "card": task_id},
        }
    if rejection is None:
        return None
    return {
        "task_id": task_id, "derivable": False, "current_node": None, "current_state": "legacy_frozen",
        "queue_state": queue_dir, "triggered_by": "none", "command": "", "verb": "",
        "missing_records": [rejection["message"]], "suggested_action": rejection["unfreeze_command"],
        "notes": "AIPOS-F122: 存量冻结卡 = 历史, 推导核不派生推进步",
        "action": {"type": "legacy_frozen", "card": task_id, "unfreeze_command": rejection["unfreeze_command"]},
    }


def _priority_rank(value: Any) -> int:
    """卡 priority 的序(enums.schema priority 值序, 后者高; 缺/不在值域 = -1 最低)。"""
    from tools.schema_loader import load_schema

    values = [str(v.get("value")) for v in (((load_schema("enums").get("enums") or {}).get("priority") or {}).get("values") or [])
              if isinstance(v, dict)]
    text = str(value or "").strip().lower()
    return values.index(text) if text in values else -1


_SCAN_STATES = ("pending", "claimed", "blocked")
_SCAN_STATE_ORDER = {"legacy_baseline_invalid": -1, "pending": 0, "claimed": 1, "blocked": 2, "completed": 3}


def scan_project(workspace_root: Path, *, lane: str | None = None) -> list[dict[str, Any]]:
    """项目级扫描:返回所有活跃任务的最小待办清单。

    AIPOS-F133: 卡遍历只走 task_loader.iter_queue_task_paths(原自 glob 队列目录); 每行带 lane(machine_zone.lane_of_card,
    唯一派生)、priority 与 next_card; lane 给出 = 经 machine_zone.filter_rows_by_lane 过滤(四命令同一函数)。
    排序: 硬停项(存量冻结清单读不出)首行; 然后「下一张可推进卡」(pending 且可推导 = 依赖满足, 判据 task_complexity.
    dependencies_satisfied; 优先级最高者, 同级按 task_id); 其余按 pending > claimed > blocked、可推导优先、优先级高者先、task_id。
    AIPOS-F122 件③: 存量冻结卡(legacy_baseline.frozen_tasks 唯一判定)不列; 清单读不出 = 首行列硬停项且不隐藏任何卡。
    """
    workspace_root = Path(workspace_root)
    from tools.aipos_cli.frontmatter import FrontmatterReadError
    from tools.aipos_cli.legacy_baseline import LegacyBaselineError, frozen_tasks
    from tools.aipos_cli.machine_zone import filter_rows_by_lane, lane_of_card
    from tools.aipos_cli.task_loader import iter_queue_task_paths

    results: list[dict[str, Any]] = []
    try:
        frozen = frozen_tasks(workspace_root)
    except LegacyBaselineError as exc:
        frozen = {}
        results.append({
            "task_id": "(legacy_baseline)", "derivable": False, "current_node": None, "current_state": "legacy_baseline_invalid",
            "triggered_by": "advisor", "command": "", "verb": "", "missing_records": [str(exc)],
            "suggested_action": "修正存量冻结清单条目 / project.json legacy_baseline(冻结不生效, 不隐藏任何卡)", "notes": "",
            "lane": None, "lane_error": None,
        })

    rows: list[dict[str, Any]] = []
    for task_file in iter_queue_task_paths(workspace_root, states=_SCAN_STATES):
        status_dir = task_file.parent.name
        raw_id = task_file.stem
        try:
            fm = _read_frontmatter(task_file)
        except FrontmatterReadError as exc:
            if raw_id.upper() in frozen:
                continue  # AIPOS-F122: 卡面读不出的冻结卡(清单按文件名回落记卡号, state_lint.queue_task_index 同一列举)
            # AIPOS-F100 件②: 读不出的卡不按文件名猜 task_id 去推导; 原样列为硬停项(点名文件与出口)
            rows.append({**frontmatter_unreadable_stop(raw_id.upper(), exc), "current_state": status_dir,
                         **lane_of_card(None, workspace_root)})
            continue
        task_id = fm.get("task_id", raw_id.upper()) if fm else raw_id.upper()
        if str(task_id).strip().upper() in frozen:
            continue  # AIPOS-F122 件③: 存量冻结卡 = 历史, 不列为待推进
        try:
            result = derive_next_step(str(task_id), workspace_root)
        except Exception as e:  # 推导异常不吞: 列为不可推导行(带异常原文), 不隐藏该卡
            result = {
                "task_id": task_id,
                "derivable": False,
                "current_node": None,
                "current_state": status_dir,
                "triggered_by": "unknown",
                "command": "",
                "verb": "",
                "missing_records": [f"推导异常: {e}"],
                "suggested_action": "检查任务状态",
                "notes": str(e),
            }
        rows.append({**result, **lane_of_card(fm, workspace_root), "priority": fm.get("priority")})

    rows = filter_rows_by_lane(rows, lane)

    def order_key(r: dict[str, Any]) -> tuple[Any, ...]:
        return (_SCAN_STATE_ORDER.get(r.get("current_state", ""), 9), 0 if r.get("derivable") else 1,
                -_priority_rank(r.get("priority")), str(r.get("task_id") or ""))

    rows.sort(key=order_key)
    candidates = [r for r in rows if r.get("current_state") == "pending" and r.get("derivable")]
    if candidates:
        nxt = candidates[0]  # 已按 可推导 > 优先级 > task_id 排序
        rows.remove(nxt)
        rows.insert(0, {**nxt, "next_card": True})
    return results + rows


def pick_next_card(results: list[dict[str, Any]]) -> dict[str, Any] | None:
    """scan_project 结果中的「下一张可推进卡」(next_card 标记行; 无 = None)。"""
    return next((r for r in results if r.get("next_card")), None)


def format_output(result: dict[str, Any], *, json_mode: bool = False) -> str:
    """格式化输出。面向三类扣扳机者(Owner 人肉/agent 会话/未来 cron)同一份。"""
    if json_mode:
        import json
        return json.dumps(result, indent=2, ensure_ascii=False)

    lines: list[str] = []
    task_id = result.get("task_id", "?")
    lines.append(f"Task: {task_id}")
    lines.append(f"State: {result.get('current_state', '?')}")
    lines.append(f"Node: {result.get('current_node', '?')}")
    lines.append(f"Triggered by: {result.get('triggered_by', '?')}")

    if result.get("derivable"):
        lines.append(f"Verb: {result.get('verb', '')}")
        cmd = result.get("command", "")
        if cmd:
            lines.append(f"Command:")
            lines.append(f"  {cmd}")
    else:
        missing = result.get("missing_records", [])
        if missing:
            lines.append(f"Missing: {', '.join(missing)}")
        lines.append(f"Suggested: {result.get('suggested_action', '?')}")

    notes = result.get("notes", "")
    if notes:
        lines.append(f"Notes: {notes}")

    return "\n".join(lines)


def format_scan_output(results: list[dict[str, Any]], *, json_mode: bool = False, lane: str | None = None) -> str:
    """格式化项目级扫描输出。"""
    if json_mode:
        import json
        return json.dumps(results, indent=2, ensure_ascii=False)

    if not results:
        return "No active tasks found in queue."

    lines: list[str] = []
    scope = f"lane {lane}, " if lane else ""
    lines.append(f"=== lybra next — project scan ({scope}{len(results)} active tasks) ===")
    # AIPOS-F133 件③: 首位给出「下一张可推进卡」(结案后取下一张的唯一出口; 判据见 verbs.schema lane_view.next_card)
    nxt = pick_next_card(results)
    if nxt is not None:
        lane_txt = f" lane={nxt.get('lane')}" if nxt.get("lane") else ""
        lines.append(f"下一张可推进卡: {nxt.get('task_id')}{lane_txt} priority={nxt.get('priority') or '-'}")
        if nxt.get("command"):
            lines.append(f"  Command: {nxt.get('command')}")
    else:
        waiting = [r for r in results if r.get("current_state") == "pending" and not r.get("derivable")]
        why = f"; pending 卡 {len(waiting)} 张均不可推导(依赖未满足/缺认领实例等, 见下)" if waiting else "; 无 pending 卡"
        lines.append(f"下一张可推进卡: 无{why}")
    lines.append("")

    for r in results:
        task_id = r.get("task_id", "?")
        state = r.get("current_state", "?")
        node = r.get("current_node", "?")
        triggered = r.get("triggered_by", "?")
        if r.get("lane"):
            task_id = f"{task_id} [lane {r.get('lane')}]"

        if r.get("derivable"):
            cmd = r.get("command", "")
            lines.append(f"[{state}/{node}] {task_id} → {triggered}")
            if cmd:
                lines.append(f"  Command: {cmd}")
        else:
            missing = r.get("missing_records", [])
            suggested = r.get("suggested_action", "?")
            lines.append(f"[{state}/{node}] {task_id} → NOT DERIVABLE")
            if missing:
                lines.append(f"  Missing: {', '.join(missing)}")
            lines.append(f"  Suggested: {suggested}")
        if r.get("lane_error"):
            lines.append(f"  Lane: {r.get('lane_error')}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# AIPOS-F73件②③: next --run 机器扣扳机 — 推导后立即执行
# ---------------------------------------------------------------------------

def _check_branch_has_commits(workspace_root: Path, task_id: str) -> bool:
    """AIPOS-F73件③: 检查卡分支是否有提交（相对 main）。
    
    Args:
        workspace_root: 产品仓根目录
        task_id: 任务 ID
    
    Returns:
        True if branch has commits, False otherwise
    """
    import subprocess
    
    branch_name = card_branch_name(task_id)  # AIPOS-F108 件② / F112: 分支名 / 基线读 N5.branch_integration 声明(唯一读取口)
    base_branch = card_base_branch()
    
    try:
        # 检查分支是否存在
        result = subprocess.run(
            ["git", "rev-parse", "--verify", branch_name],
            cwd=workspace_root,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return False
        
        # 检查是否有相对基线分支的提交
        result = subprocess.run(
            ["git", "rev-list", "--count", f"{base_branch}..{branch_name}"],
            cwd=workspace_root,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return False
        
        commit_count = int(result.stdout.strip())
        return commit_count > 0
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        import sys

        print(f"Warning: branch commit check failed for {branch_name}: {exc}", file=sys.stderr)
        return False


def card_branch_name(task_id: str, branch_integration: dict[str, Any] | None = None) -> str:
    """AIPOS-F88 件①: 卡分支名读声明(transitions.schema N5.branch_integration.branch_pattern, 与 finalize / card render 同一声明)。
    声明缺 = SchemaLoadError(fail-closed, 不回落写死)。
    AIPOS-F108 件②: 全产品唯一的分支名派生(原 finalize._branch_name_for_task 第二实现已退役); 调用方已读声明时传入
    branch_integration(finalize 读一次声明贯穿整合), 缺省 = 读 Lybra 自身 schema。"""
    from tools.schema_loader import SchemaLoadError, get_branch_integration

    decl = get_branch_integration() if branch_integration is None else branch_integration
    pattern = str(decl.get("branch_pattern") or "").strip()
    if "{task_id}" not in pattern:
        raise SchemaLoadError("transitions.schema.json N5.branch_integration.branch_pattern 未声明或缺 {task_id} 占位")
    return pattern.replace("{task_id}", task_id)


def card_base_branch(branch_integration: dict[str, Any] | None = None) -> str:
    """AIPOS-F108 件②(M18): 卡分支的基线分支读声明(transitions.schema N5.branch_integration.base_branch)——建树起点 /
    交回判据对照基 / 车道改动集三点 diff 基 / finalize 整合目标与部署分支强制同一声明。全产品唯一读取口;
    branch_integration 同 card_branch_name。声明缺 = SchemaLoadError(fail-closed, 不回落写死)。"""
    from tools.schema_loader import SchemaLoadError, get_branch_integration

    decl = get_branch_integration() if branch_integration is None else branch_integration
    base = decl.get("base_branch")
    if not isinstance(base, str) or not base.strip():
        raise SchemaLoadError("transitions.schema.json N5.branch_integration.base_branch 未声明")
    return base.strip()


def existing_branch_tip(code_repo: Path, branch: str) -> str | None:
    """AIPOS-F123 件①: 产品仓 code_repo 内既有分支 branch 的 tip 完整 sha(本地 refs/heads/<branch> 优先, 其次远端跟踪
    refs/remotes/<branch>); 不存在 = None。只读 git rev-parse; git 不可执行 = OSError 上抛(调用方 fail-closed)。"""
    import subprocess

    name = str(branch or "").strip()
    if not name or name.startswith("-"):
        return None
    for ref in (f"refs/heads/{name}", f"refs/remotes/{name}"):
        proc = subprocess.run(["git", "-C", str(code_repo), "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
                              capture_output=True, text=True, timeout=10)
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
    return None


def _ensure_worktree(workspace_root: Path, task_id: str,
                     card_frontmatter: dict[str, Any] | None = None,
                     *, start_point: str | None = None) -> dict[str, Any]:
    """AIPOS-F73件② + F73D 前置三 + F88 件①: 确保卡工作树存在(建/复用卡分支)——全产品唯一建树实现。

    门认领(queue_mutation claim, 原 WorktreeManager 第二实现已退役为委托)与驱动方 next --run 认领后建树都经此函数。
    落点 = card_worktree_location(该卡产品仓 = workspace_config.resolve_card_repo: 卡 lane.repo → project.json repos 清单 →
    code_repo 别名; 工作树 = config.schema worktree_root 声明 / <task_id>), 禁在治理根下建工作树(F73B 原实现
    `workspace_root/card/<ID>` 之误); 分支名 = card_branch_name(N5 branch_pattern 声明)。仓解析不到/不是 git 仓/声明缺 → fail-closed。

    Args:
        workspace_root: 治理根(推导核工作区); 若其 project.json 无仓声明且自身是 git 仓, 视为产品仓(靶场单根)
        task_id: 任务 ID
        card_frontmatter: 卡 frontmatter(门认领时已在手, 免二次查卡); 缺则按 task_id 读卡
        start_point: AIPOS-F123 件①(收编): 卡分支须落在的既有提交(既有分支 tip 完整 sha)。卡分支不存在 = 建在该提交上;
            已存在 = 其 tip 须正好等于该提交, 否则拒(绑定不唯一); 工作树已在落点 = 须检出卡分支且 HEAD = 该提交, 否则拒。
            缺省 None = 原认领语义(新卡分支起于 base_branch 声明)。

    Returns:
        {"ok": bool, "worktree_path": str, "branch": str, "message": str}
    """
    import subprocess

    from tools.aipos_cli.workspace_config import CardRepoUnresolved
    from tools.schema_loader import SchemaLoadError

    # AIPOS-F89 件③a: 审计卡 = 被审分支 tip 的只读 detached 取证工作树(同一落点函数 card_worktree_location, 同一建树入口)
    from tools.aipos_cli.frontmatter import FrontmatterReadError

    fm_for_mode = card_frontmatter
    if fm_for_mode is None:
        found_path, _ = _find_task_in_queue(workspace_root, task_id)
        try:
            fm_for_mode = _read_frontmatter(found_path) if found_path else {}
        except FrontmatterReadError as exc:
            # AIPOS-F100 件②: 卡读不出 = 不建树(不按空卡面猜仓/模式)
            return {"ok": False, "worktree_path": "", "branch": "", "message": f"卡 {task_id} 不建 worktree: {exc}"}
    if forensic_subject(fm_for_mode) is not None:
        return _ensure_forensic_worktree(workspace_root, task_id, fm_for_mode)

    # AIPOS-F78C 件②: 落点仓 = 该卡声明的仓(resolve_card_repo), 多仓项目两张卡各落各仓 .worktrees/<ID>
    # AIPOS-F86 件①: 落点经 card_worktree_location(与 my-tasks 开工面 / card render 同一函数)
    try:
        code_repo, worktree_path = card_worktree_location(workspace_root, task_id, card_frontmatter)
        branch_name = card_branch_name(task_id)
        base_branch = card_base_branch()  # AIPOS-F108 件②: 新卡分支起点读 N5.branch_integration.base_branch 声明
    except CardRepoUnresolved as exc:
        return {"ok": False, "worktree_path": "", "branch": "", "message": f"无法定位卡 {task_id} 的产品仓建 worktree: {exc}"}
    except SchemaLoadError as exc:
        return {"ok": False, "worktree_path": "", "branch": "", "message": f"worktree 落点/分支声明读取失败: {exc}"}
    except (OSError, ValueError) as exc:
        return {"ok": False, "worktree_path": "", "branch": "", "message": f"worktree 落点推导失败(声明读取/卡查找): {exc}"}
    if not (code_repo / ".git").exists():
        return {
            "ok": False,
            "worktree_path": "",
            "branch": branch_name,
            "message": f"卡 {task_id} 解析到的产品仓 {code_repo} 不是 git 仓根(无 .git), 无法建 worktree; 出口: project.json repos/code_repo 指向真实产品仓",
        }
    excluded = _exclude_worktree_root(code_repo, worktree_path)
    if excluded is not None:
        return {"ok": False, "worktree_path": "", "branch": branch_name, "message": excluded}
    workspace_root = code_repo  # 以下 git 操作全部在产品仓

    # 检查 worktree 是否已存在
    if worktree_path.exists():
        if start_point:
            # AIPOS-F123 件①: 收编复用既有工作树须正好是卡分支且 HEAD = 绑定提交(否则绑定不唯一, 拒)
            head = subprocess.run(["git", "-C", str(worktree_path), "rev-parse", "--abbrev-ref", "HEAD"],
                                  capture_output=True, text=True, timeout=10)
            sha = subprocess.run(["git", "-C", str(worktree_path), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10)
            if head.returncode != 0 or head.stdout.strip() != branch_name or sha.stdout.strip() != start_point:
                return {
                    "ok": False,
                    "worktree_path": "",
                    "branch": branch_name,
                    "message": (f"工作树落点 {worktree_path} 已存在但不是卡分支 {branch_name}@{start_point}"
                                f"(当前 {head.stdout.strip() or head.stderr.strip()}@{sha.stdout.strip() or sha.stderr.strip()}); "
                                "出口: 核对该目录是否在途产物, 移走或切到卡分支后重试"),
                }
        return {
            "ok": True,
            "worktree_path": str(worktree_path),
            "branch": branch_name,
            "message": f"Worktree already exists: {worktree_path}",
        }

    # 创建 worktree 目录
    worktree_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        # 检查分支是否已存在
        result = subprocess.run(
            ["git", "rev-parse", "--verify", f"refs/heads/{branch_name}"],
            cwd=workspace_root,
            capture_output=True,
            text=True,
            timeout=5,
        )
        branch_exists = result.returncode == 0

        if branch_exists and start_point and result.stdout.strip() != start_point:
            # AIPOS-F123 件①: 卡分支已存在且 tip ≠ 绑定提交 = 绑定不唯一(不移动既有分支, 不覆盖产物)
            return {
                "ok": False,
                "worktree_path": "",
                "branch": branch_name,
                "message": f"卡分支 {branch_name} 已存在于 {workspace_root}, tip {result.stdout.strip()} ≠ 绑定提交 {start_point}",
            }
        if branch_exists:
            # 复用已有分支
            result = subprocess.run(
                ["git", "worktree", "add", str(worktree_path), branch_name],
                cwd=workspace_root,
                capture_output=True,
                text=True,
                timeout=30,
            )
        else:
            # 创建新分支
            result = subprocess.run(
                ["git", "worktree", "add", "-b", branch_name, str(worktree_path), start_point or base_branch],
                cwd=workspace_root,
                capture_output=True,
                text=True,
                timeout=30,
            )

        if result.returncode != 0:
            return {
                "ok": False,
                "worktree_path": "",
                "branch": branch_name,
                "message": f"Failed to create worktree: {result.stderr}",
            }

        return {
            "ok": True,
            "worktree_path": str(worktree_path),
            "branch": branch_name,
            "message": f"Worktree created: {worktree_path}",
        }
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "ok": False,
            "worktree_path": "",
            "branch": branch_name,
            "message": f"Exception creating worktree: {exc}",
        }


def _exclude_worktree_root(code_repo: Path, worktree_path: Path) -> str | None:
    """AIPOS-F92 件③: 工作树根(声明 worktree_root, 缺省 <产品仓>/.worktrees)在产品仓内时登记进该仓 .git/info/exclude
    (既有唯一实现 git_exclude.register_git_exclude, 幂等)。否则新项目产品仓的工作树目录是未跟踪文件, finalize 合并前
    「工作树不干净 ?? .worktrees/」必 BLOCK(靶场实撞; lybra 自身仓靠 .gitignore 掩盖)。工作树根在仓外 = 无需登记。
    返回 None = 已登记/无需; 字符串 = 登记失败原因(调用方拒建树, fail-closed)。"""
    from tools.aipos_cli.git_exclude import register_git_exclude

    root = Path(worktree_path).parent
    try:
        rel = root.resolve().relative_to(Path(code_repo).resolve())
    except ValueError:
        return None
    if not str(rel) or str(rel) == ".":
        return f"工作树根 {root} 即产品仓根, 拒建(声明 worktree_root 须为仓内子目录或仓外目录)"
    report = register_git_exclude(Path(code_repo), [f"/{rel.as_posix()}/"])
    if not report.get("ok"):
        return f"工作树根 {rel}/ 登记进 {code_repo} .git/info/exclude 失败: {report.get('error')}"
    return None


def _ensure_forensic_worktree(workspace_root: Path, audit_task_id: str, card_frontmatter: dict[str, Any]) -> dict[str, Any]:
    """AIPOS-F89 件③a(Owner 2026-10-03 裁定 A2): 审计卡认领时为被审分支 tip 建**只读 detached 取证工作树**。

    落点 = card_worktree_location(审计卡: 被审卡所在产品仓的 <worktree_root>/<审计卡ID>); 被审分支名 = card_branch_name(被审卡 ID)。
    只读 = detached HEAD(无分支可提交; 审计体对产品仓的写边界见 roles.schema write_boundary)。已存在: HEAD 已在 tip = 复用;
    detached 且无本地改动 = 重指到当前 tip(复审场景); 其余 = 拒。被审分支不存在 / 卡缺被审卡字段 / 非 git 仓 = 拒(fail-closed)。
    返回形同 _ensure_worktree, 另带 detached / commit / reviewed_task_id。"""
    import subprocess

    from tools.aipos_cli.workspace_config import CardRepoUnresolved
    from tools.schema_loader import SchemaLoadError

    reviewed = forensic_subject(card_frontmatter) or ""
    fail = {"ok": False, "worktree_path": "", "branch": "", "detached": True, "commit": "", "reviewed_task_id": reviewed}
    if not reviewed:
        return {**fail, "message": f"审计卡 {audit_task_id} 卡面缺 reviewed_task_id / derived_from, 无法确定被审分支建取证工作树"}
    try:
        code_repo, worktree_path = card_worktree_location(workspace_root, audit_task_id, card_frontmatter)
        branch = card_branch_name(reviewed)
    except CardRepoUnresolved as exc:
        return {**fail, "message": f"无法定位被审卡 {reviewed} 的产品仓建取证工作树: {exc}"}
    except (SchemaLoadError, OSError, ValueError) as exc:
        return {**fail, "message": f"取证工作树落点/分支声明读取失败: {exc}"}
    fail["branch"] = branch
    if not (code_repo / ".git").exists():
        return {**fail, "message": f"被审卡 {reviewed} 解析到的产品仓 {code_repo} 不是 git 仓根(无 .git), 无法建取证工作树"}
    excluded = _exclude_worktree_root(code_repo, worktree_path)
    if excluded is not None:
        return {**fail, "message": excluded}

    def git(cwd: Path, *argv: str, timeout: int = 30) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *argv], cwd=cwd, capture_output=True, text=True, timeout=timeout)

    try:
        tip_run = git(code_repo, "rev-parse", "--verify", f"refs/heads/{branch}^{{commit}}", timeout=5)
        if tip_run.returncode != 0:
            return {**fail, "message": f"被审分支 {branch} 在 {code_repo} 不存在, 无法建取证工作树: {tip_run.stderr.strip()}"}
        tip = tip_run.stdout.strip()
        done = {"ok": True, "worktree_path": str(worktree_path), "branch": branch, "detached": True, "commit": tip,
                "reviewed_task_id": reviewed}
        if worktree_path.exists():
            head = git(worktree_path, "rev-parse", "HEAD", timeout=5)
            attached = git(worktree_path, "symbolic-ref", "-q", "HEAD", timeout=5)
            if head.returncode != 0 or attached.returncode == 0:
                return {**fail, "message": f"取证工作树落点 {worktree_path} 已被占用且不是 detached 工作树(HEAD={head.stdout.strip() or '?'}"
                                           f"{', 在分支 ' + attached.stdout.strip() if attached.returncode == 0 else ''}), 拒绝复用"}
            if head.stdout.strip() == tip:
                return {**done, "message": f"Forensic worktree already at {branch} tip {tip[:12]}: {worktree_path}"}
            dirty = git(worktree_path, "status", "--porcelain", timeout=10)
            if dirty.returncode != 0 or dirty.stdout.strip():
                return {**fail, "message": f"取证工作树 {worktree_path} 有本地改动, 不能重指到被审分支新 tip {tip[:12]}: "
                                           f"{(dirty.stdout or dirty.stderr).strip()[:200]}"}
            moved = git(worktree_path, "checkout", "-q", "--detach", tip)
            if moved.returncode != 0:
                return {**fail, "message": f"取证工作树重指 {tip[:12]} 失败: {moved.stderr.strip()}"}
            return {**done, "message": f"Forensic worktree re-pointed to {branch} tip {tip[:12]}: {worktree_path}"}
        worktree_path.parent.mkdir(parents=True, exist_ok=True)
        added = git(code_repo, "worktree", "add", "--detach", str(worktree_path), tip)
        if added.returncode != 0:
            return {**fail, "message": f"Failed to create forensic worktree: {added.stderr.strip()}"}
        return {**done, "message": f"Forensic worktree created (detached at {branch} tip {tip[:12]}): {worktree_path}"}
    except (OSError, subprocess.SubprocessError) as exc:
        return {**fail, "message": f"Exception creating forensic worktree: {exc}"}


def loop_step_timeout_seconds() -> float:
    """AIPOS-F90 件①(缺陷③): 派生命令子进程的硬上限秒数——唯一声明 verbs.schema lybra_loop.step_timeout_seconds
    (须覆盖薄壳内 initialize + 各阶段门请求超时 + 超时回读; 夹具核不变量)。缺声明 = SchemaLoadError, 禁回落写死。"""
    from tools.schema_loader import SchemaLoadError, load_schema

    value = ((load_schema("verbs", REPO_ROOT).get("verbs") or {}).get("lybra_loop") or {}).get("step_timeout_seconds")
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        raise SchemaLoadError("verbs.schema.json verbs.lybra_loop.step_timeout_seconds 未声明或非正数")
    return float(value)


def _run_product_command(command: str, action_type: str) -> dict[str, Any]:
    """执行一条派生产品命令(`lybra ...`, 每步过门零旁路), 超时读声明。返回 execute_derived_action 响应形。"""
    import shlex
    import subprocess

    timeout = loop_step_timeout_seconds()
    try:
        result = subprocess.run(shlex.split(command), capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "action_type": action_type,
            "message": f"{action_type} 子进程超时 (>{timeout:g}s, 声明 verbs.schema lybra_loop.step_timeout_seconds)",
            "command": command,
            "exit_code": 124,
            "output": f"Command timed out after {timeout:g} seconds",
        }
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return {
            "ok": False,
            "action_type": action_type,
            "message": f"{action_type} 执行异常: {exc}",
            "command": command,
            "exit_code": 1,
            "output": str(exc),
        }
    success = result.returncode == 0
    # AIPOS-F120 件③: stderr 与 stdout 分段进 output(原两段首尾直接粘连, 末行与 traceback 首行混成一行); 失败时 stderr 关键行
    # (异常行 / Error: / ✗ / BLOCK, 无则末行)进 message——loop JSON 的 step.message / step.output 与 exit 2 原文都带上, 驱动方收尾不再丢
    stdout, stderr = result.stdout or "", result.stderr or ""
    key_lines = stderr_key_lines(stderr)
    output = stdout.rstrip("\n")
    if stderr.strip():
        output = (output + "\n" if output else "") + "[stderr]\n" + stderr.rstrip("\n")
    message = f"{action_type} {'成功' if success else '失败'}"
    if not success and key_lines:
        message += f": {key_lines[-1]}"
    return {
        "ok": success,
        "action_type": action_type,
        "message": message,
        "command": command,
        "exit_code": result.returncode,
        "output": output,
        "stderr_key_lines": key_lines,
    }


_STDERR_KEY_LINE = re.compile(r"^\s*(?:[A-Za-z_][\w.]*(?:Error|Exception|Blocked)\b|Error:|✗|BLOCK)")


def stderr_key_lines(stderr: str, limit: int = 5) -> list[str]:
    """AIPOS-F120 件③: 子进程 stderr 的关键行(异常行 / Error: / ✗ / BLOCK 开头, 取末 limit 条; 一条都不命中取末行非空行)。"""
    lines = [line.rstrip() for line in str(stderr or "").splitlines() if line.strip()]
    keys = [line.strip() for line in lines if _STDERR_KEY_LINE.match(line)]
    if not keys and lines:
        keys = [lines[-1].strip()]
    return keys[-limit:]


def _run_cli_in_process(command: str, action_type: str) -> dict[str, Any]:
    """AIPOS-F94 件①: 在本进程经 `lybra` CLI 入口(aipos_cli.main, argparse 与处理器同一份)执行派生命令并取 --json 结果。
    返回 execute_derived_action 响应形; 结果 JSON 解析不出 = 失败(fail-closed, 原文透传)。"""
    import contextlib
    import io
    import json
    import shlex

    from tools.aipos_cli.aipos_cli import main as cli_main

    argv = shlex.split(command)[1:]
    if "--json" not in argv:
        argv.append("--json")
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = cli_main(argv)
        except SystemExit as exc:  # argparse 拒(loop 已先过 parser 夹具, 此处兜住 next --run 单步入口)
            rc = exc.code if isinstance(exc.code, int) else 2
    text = out.getvalue().strip()
    try:
        payload = json.loads(text) if text else {}
    except ValueError:
        payload = {}
    message = str(payload.get("message") or "").strip() or (err.getvalue().strip().splitlines() or [f"{action_type} rc={rc}"])[-1]
    return {
        "ok": rc == 0 and bool(payload),
        "action_type": action_type,
        "message": message.split("\n", 1)[0] if rc == 0 else message,
        "command": command,
        "exit_code": int(rc) if rc else (0 if payload else 1),
        "output": "\n".join(x for x in (text, err.getvalue().strip()) if x),
        "result": payload,
    }


def _execute_claim_with_role_token(
    *,
    derivation: dict[str, Any],
    workspace_root: Path,
) -> dict[str, Any]:
    """AIPOS-F73B件① + F73C件⑥ + F90 件①: 认领一段式——执行推导核派生的同一条 `lybra queue claim --confirm`
    (驱动方 token, actor/agent_instance=卡面认领实例, PreAuthorized + owner_policy_ref=覆盖驱动方的信封; 禁本处另拼第二条命令)。

    建树在门认领同一步完成(queue_mutation 认领前经 _ensure_worktree 唯一实现预建, 建树失败=门拒认领, 不留 claimed 无工作树的卡);
    本处只读核验落点(card_workstation_view, 与 my-tasks 开工面同一函数), 禁第二建树实现。成功输出 spawn_worker action。
    """
    task_id = str(derivation.get("task_id") or "")
    command = str(derivation.get("command") or "")
    run = _run_product_command(command, "claim")
    if not run.get("ok"):
        run["message"] = f"claim 失败: {run.get('exit_code')}" if run.get("exit_code") not in (None, 124) else run["message"]
        return run

    task_path, _queue = _find_task_in_queue(workspace_root, task_id)
    fm = _read_frontmatter(task_path) if task_path else {}
    assigned_to = str(fm.get("assigned_to") or fm.get("agent_instance") or "")
    worktree_path = ""
    # AIPOS-F89 件③a: 认领须建树的判据与门侧同一函数 card_needs_worktree(代码卡 + 代码卡的审计卡)
    if card_needs_worktree(workspace_root, fm if fm else {"task_mode": "code"}):
        view = card_workstation_view(workspace_root, task_id, fm)
        if not view.get("worktree_exists"):
            refusal = view.get("worktree_refusal") or {}
            return {
                "ok": False,
                "action_type": "claim",
                "message": f"门报认领成功但卡工作树不在落点({refusal.get('code') or 'WORKTREE_UNRESOLVED'}): 门侧建树失败须拒认领, 此为门/部署不一致",
                "command": command,
                "exit_code": 1,
                "output": run.get("output", "") + "\n" + str(refusal.get("reason") or ""),
                "worktree_path": "",
            }
        worktree_path = str(view.get("worktree_path") or "")
    return {
        "ok": True,
        "action_type": "claim",
        "message": "claim 成功 + 工作树已在落点" if worktree_path else "claim 成功",
        "command": command,
        "exit_code": 0,
        "output": str(run.get("output") or "") + (f"\nWorktree: {worktree_path}" if worktree_path else ""),
        "worktree_path": worktree_path,
        "spawn_action": {"type": "spawn_worker", "card": task_id, "worktree": worktree_path, "instance": assigned_to},
    }

def execute_derived_action(
    derivation: dict[str, Any],
    workspace_root: Path,
    connection_json: str | None = None,
) -> dict[str, Any]:
    """执行推导出的下一步动作(见 _execute_derived_action)。AIPOS-F100 件②: 执行途中卡/记录 frontmatter 读不出 = 失败
    (exit 4 同不可推导, 原文点名文件), 不取缺省继续。"""
    from tools.aipos_cli.frontmatter import FrontmatterReadError

    try:
        return _execute_derived_action(derivation, workspace_root, connection_json)
    except FrontmatterReadError as exc:
        from tools.aipos_cli.loop_driver import exit_code_for, load_loop_contract

        return {
            "ok": False,
            "action_type": "none",
            "message": f"frontmatter 读不出, 拒绝执行: {exc}",
            "command": str(derivation.get("command") or ""),
            "exit_code": exit_code_for(load_loop_contract(), "not_derivable"),
            "output": str(exc),
        }


def _execute_derived_action(
    derivation: dict[str, Any],
    workspace_root: Path,
    connection_json: str | None = None,
) -> dict[str, Any]:
    """AIPOS-F73件②③ + F73B件①: 执行推导出的下一步动作。
    
    铁律三条:
    1. 单步即退禁循环 (连接器病根禁复刻)
    2. 每步过门零旁路 (全部通过薄壳 CLI 执行)
    3. fail-closed 非零退出带拒因
    
    推导结果 → 执行:
    - pending → claim (AIPOS-F73B件①: 按角色 token 认领) + 建/复用 worktree + 输出 spawn_worker action
    - claimed + RETURN.md + 分支有提交 → return (执行 lybra queue return --confirm)
    - claimed + VERDICT → verdict (执行 lybra audit verdict --confirm)
    - returned + audit card → dispatch (执行 lybra audit dispatch --confirm)
    - verdict PASS → finalize --push --deploy → close
    
    Args:
        derivation: derive_next_step() 的返回结果
        workspace_root: 产品仓根目录
        connection_json: connection.json 路径 (可选)
    
    Returns:
        {
            "ok": bool,
            "action_type": str,  # "claim" | "return" | "verdict" | "dispatch" | "finalize" | "close" | "none"
            "message": str,
            "command": str,  # 实际执行的命令
            "exit_code": int,
            "output": str,
            "worktree_path": str | None,  # claim 时返回 worktree 路径
            "spawn_action": dict | None,  # claim 时输出 spawn_worker action
        }
    """
    if not derivation.get("derivable"):
        return {
            "ok": False,
            "action_type": "none",
            "message": f"不可推导: {', '.join(derivation.get('missing_records', []))}",
            "command": "",
            "exit_code": 1,
            "output": "",
        }
    
    task_id = derivation.get("task_id", "")
    command = derivation.get("command", "").strip()
    
    # 如果推导出的命令是注释或空,说明需要人工介入
    if not command or command.startswith("#"):
        return {
            "ok": False,
            "action_type": "manual",
            "message": f"需要人工介入: {derivation.get('suggested_action', '?')}",
            "command": command,
            "exit_code": 0,
            "output": "",
        }
    
    # AIPOS-F73C前置零之一 + F73D: action_type 按命令真实动词, 唯一映射 _action_type_for_command(loop 共用)
    action_type = _action_type_for_command(command)

    # AIPOS-F90 件②: return/verdict 步的派生命令 = `lybra artifact ingest --kind <return|verdict>`(产物入口);
    # 进程内经 ingest_task_artifact(校验 → 产品填模型字段 → 同一薄壳命令经本函数执行), 禁第二 return/verdict 路径
    if "artifact ingest" in command and action_type in ("return", "verdict"):
        from tools.aipos_cli.artifact_ingest import ingest_task_artifact

        ingested = ingest_task_artifact(task_id, Path(workspace_root), connection_json=connection_json, kind=action_type)
        reasons = "\n".join(str(r) for r in (ingested.get("reasons") or []))
        return {
            "ok": bool(ingested.get("ok")),
            "action_type": action_type,
            "message": str(ingested.get("message") or ingested.get("category") or ""),
            "command": command,
            "exit_code": 0 if ingested.get("ok") else int(ingested.get("exit_code") or 1),
            "output": "\n".join(x for x in (str(ingested.get("output") or ""), reasons) if x),
            "shell_command": str(ingested.get("command") or ""),
            "agent_runtime": ingested.get("agent_runtime"),
        }

    # AIPOS-F73件② + F78C: return 前先检查分支提交(卡分支活在该卡声明的产品仓 resolve_card_repo)
    if action_type == "return":
        from tools.aipos_cli.workspace_config import CardRepoUnresolved

        try:
            has_commits = _check_branch_has_commits(_card_repo_root(workspace_root, task_id), task_id)
        except CardRepoUnresolved as exc:
            return {
                "ok": False,
                "action_type": "return",
                "message": f"return 阻塞: 卡产品仓不可解析({exc.code})",
                "command": command,
                "exit_code": 1,
                "output": str(exc),
            }
        if not has_commits:
            return {
                "ok": False,
                "action_type": "return",
                "message": "return 阻塞: 分支无提交",
                "command": command,
                "exit_code": 1,
                "output": f"Branch {card_branch_name(task_id)} has no commits relative to {card_base_branch()}. Cannot return without commits.",
            }

    # AIPOS-F78 件③: return/verdict 步经产物入口(artifact_ingest.ingest_task_artifact): 读项目声明落点找 Return/裁决报告,
    # 校验必填 frontmatter 与分支 tip==commit_sha, 通过后才执行同一条薄壳命令(单一入口, 禁第二 return/verdict 路径)
    if action_type in ("return", "verdict"):
        from tools.aipos_cli.artifact_ingest import validate_task_artifact

        check = validate_task_artifact(task_id, workspace_root)
        if not check.get("ok"):
            return {
                "ok": False,
                "action_type": action_type,
                "message": f"{action_type} 阻塞: 产物入口校验拒(AIPOS-F78 件③): {check.get('category')}",
                "command": command,
                "exit_code": int(check.get("exit_code") or 4),
                "output": "\n".join(check.get("reasons") or []),
            }

    # AIPOS-F78B 件③ + F90 件①: 账务动词(claim/return/verdict/close)一律驱动方经信封一阶段放行; 派生命令无 PreAuthorized 形
    # = 无覆盖驱动方的信封 → exit 5 带申领出口, 禁裸撞 Supervised 索 owner_confirm(驱动方 token 无此 scope)
    if action_type in ("claim", "return", "verdict", "close") and "--autonomy-mode PreAuthorized" not in command:
        from tools.aipos_cli.loop_driver import DRIVER_ROLE, exit_code_for, load_loop_contract, mint_hint

        task_path_for_hint, _q = _find_task_in_queue(workspace_root, task_id)
        hint = mint_hint(task_id=task_id, task_fm=_read_frontmatter(task_path_for_hint) if task_path_for_hint else {},
                         driver_actor=_driver_actor(workspace_root, fallback=DRIVER_ROLE, connection_json=connection_json),
                         governance_root=workspace_root)
        return {
            "ok": False,
            "action_type": action_type,
            "message": f"{action_type} 阻塞: 无覆盖驱动方的有效 autonomy 信封(AIPOS-F78B 件③), 拒绝裸跑 Supervised",
            "command": command,
            "exit_code": exit_code_for(load_loop_contract(), "no_envelope"),
            "output": f"申领出口(Owner 亲自敲):\n  {hint}",
        }

    # AIPOS-F94 件①: N6 落账步 = 同一条产品命令在本进程经 CLI 入口(aipos_cli.main)执行——非门动词, 落账代码与驱动它的 loop 同版本
    # (子进程按 PATH 找 lybra 可能落到另一份部署); 输出取 --json 结果原文, 拒因原样透传(他人暂存 / 护栏 / push 未完成)
    if action_type == "governance_commit":
        return _run_cli_in_process(command, action_type)

    # AIPOS-F73B件① + F90 件①: 认领 = 执行同一条派生命令 + 只读核验门已建的卡工作树(输出 spawn_worker)
    if action_type == "claim":
        return _execute_claim_with_role_token(derivation=derivation, workspace_root=Path(workspace_root))

    # AIPOS-F73件②③: 每步过门零旁路 — 执行产品 CLI(超时读声明 verbs.schema lybra_loop.step_timeout_seconds)
    return _run_product_command(command, action_type)
