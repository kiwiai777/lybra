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


def _skeleton_prefix() -> str:
    """认领骨架占位前缀(record_writer.build_<return|verdict>_skeleton_markdown; N2.artifact.readiness)。AIPOS-F215 件①: 原模块常量
    收进 transitions.schema artifact_ingest.report_placeholder.skeleton_prefix, 与报告正文占位判据同一声明(唯一读取口
    validation_common.report_skeleton_prefix)。"""
    from tools.aipos_cli.validation_common import report_skeleton_prefix

    return report_skeleton_prefix()


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


def repo_turn_watch(workspace_root: Path, blocker_task_id: str) -> tuple[Path, list[str]]:
    """AIPOS-F135 件①: loop 等同仓先合入卡合入完成的 (watch 根, 相对 glob 列表) = 该卡 finalization 记录落点
    (record_dir(finalizations), 与 existing_finalization_records 读侧同一 glob)。"""
    directory = record_dir(Path(workspace_root), "finalizations", blocker_task_id)
    try:
        rel = directory.resolve().relative_to(Path(workspace_root).resolve())
        return Path(workspace_root), [str(rel / "finalization_*.md")]
    except ValueError:
        return directory.parent, [f"{directory.name}/finalization_*.md"]


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
    if not task_mode_is_audit(fm.get("task_mode")):
        return None
    return str(fm.get("reviewed_task_id") or fm.get("derived_from") or "").strip()


def task_mode_writes_product_repo(task_mode: str) -> bool:
    """AIPOS-F178 件①: 该 task_mode 缺省是否写产品仓(唯一读取口: enums.schema task_mode.values[].writes_product_repo)。
    AIPOS-F211 件②: 条目经 _task_mode_entry 唯一取(缺值 = missing_default 条目 false; 未知 = None → False)。声明缺 / 形变 = SchemaLoadError。"""
    from tools.schema_loader import SchemaLoadError, load_schema

    decl = ((load_schema("enums", REPO_ROOT).get("enums") or {}).get("task_mode") or {})
    if not isinstance(decl.get("product_repo_write"), dict):
        raise SchemaLoadError("enums.schema.json enums.task_mode.product_repo_write 未声明(AIPOS-F178)")
    entry = _task_mode_entry(task_mode)
    if entry is None:
        return False
    flag = entry.get("writes_product_repo")
    if not isinstance(flag, bool):
        raise SchemaLoadError(f"enums.schema.json task_mode={task_mode!r} 缺 writes_product_repo(布尔, AIPOS-F178)")
    return flag


def task_mode_missing_default() -> dict[str, Any]:
    """AIPOS-F211 件②: enums.schema task_mode.missing_default(缺 task_mode 卡的缺省条目; key 与三个布尔必声明)。缺 / 形变 = SchemaLoadError。"""
    from tools.schema_loader import SchemaLoadError, load_schema

    decl = (((load_schema("enums", REPO_ROOT).get("enums") or {}).get("task_mode") or {}).get("missing_default"))
    if (not isinstance(decl, dict) or not isinstance(decl.get("key"), str) or not decl["key"].strip()
            or not all(isinstance(decl.get(k), bool) for k in ("writes_product_repo", "independent_audit", "requires_tests"))):
        raise SchemaLoadError("enums.schema.json enums.task_mode.missing_default(key / writes_product_repo / independent_audit / "
                              "requires_tests)未声明齐(AIPOS-F211)")
    return decl


def _task_mode_entry(task_mode: str) -> dict[str, Any] | None:
    """enums.schema task_mode.values 中该值的条目; 未知(非空且不在值域)= None。声明缺 = SchemaLoadError(fail-closed)。
    AIPOS-F211 件②(Owner 10-22 裁定: 缺 task_mode 的缺省以 schema 为准): 缺值(空 / 缺)= missing_default 条目
    {value: <missing_default.key>, writes_product_repo: false, independent_audit: false, requires_tests: false, missing: True}——
    缺值兜底只此一处, 调用方禁再补 or "code"(棘轮 tests/test_aipos_f211_task_mode_single_judgment.py)。"""
    from tools.schema_loader import SchemaLoadError, load_schema

    decl = ((load_schema("enums", REPO_ROOT).get("enums") or {}).get("task_mode") or {})
    values = decl.get("values")
    if not isinstance(values, list):
        raise SchemaLoadError("enums.schema.json enums.task_mode.values 未声明")
    mode = str(task_mode or "").strip().lower()
    if not mode:
        default = task_mode_missing_default()
        return {"value": default["key"], "missing": True,
                **{k: default[k] for k in ("writes_product_repo", "independent_audit", "requires_tests")}}
    return next((item for item in values if isinstance(item, dict) and str(item.get("value") or "").strip().lower() == mode), None)


def task_mode_independent_audit(task_mode: str) -> bool:
    """AIPOS-F196 件①: 该 task_mode 交回后是否走独立审计(唯一读取口: enums.schema task_mode.values[].independent_audit)。
    缺值 = missing_default(false, AIPOS-F211); 未知 task_mode = False(行为同 F196 前: 只有 code 走独立审计——code 条目声明 true)。声明形变 = SchemaLoadError。"""
    from tools.schema_loader import SchemaLoadError, load_schema

    decl = ((load_schema("enums", REPO_ROOT).get("enums") or {}).get("task_mode") or {})
    if not isinstance(decl.get("independent_audit"), dict):
        raise SchemaLoadError("enums.schema.json enums.task_mode.independent_audit 未声明(AIPOS-F196)")
    entry = _task_mode_entry(task_mode)
    if entry is None:
        return False
    flag = entry.get("independent_audit", False)
    if not isinstance(flag, bool):
        raise SchemaLoadError(f"enums.schema.json task_mode={task_mode!r} independent_audit 须为布尔(AIPOS-F196)")
    return flag


def card_independent_audit(card_frontmatter: dict[str, Any] | None) -> bool:
    """AIPOS-F211 件④: 卡级是否走独立审计(唯一判定, 声明 enums.schema task_mode.independent_audit.card_rule)= 模式声明
    (task_mode_independent_audit)或卡面 audit: required。推导核派审 / 快车道分流、Owner 收件箱授权建议审计阶段、flow_description 同读。"""
    fm = card_frontmatter if isinstance(card_frontmatter, dict) else {}
    return task_mode_independent_audit(str(fm.get("task_mode") or "")) or str(fm.get("audit") or "").strip() == "required"


def task_mode_requires_tests(task_mode: str) -> bool:
    """AIPOS-F211 件①: 交回自检「有测试」判据是否适用该模式(唯一读取口: enums.schema task_mode.values[].requires_tests; 缺键 = false;
    缺值 = missing_default.requires_tests; 未知 = False)。取代 board_adapter 原 task_mode == "code" 字面。声明缺 / 形变 = SchemaLoadError。"""
    from tools.schema_loader import SchemaLoadError, load_schema

    decl = ((load_schema("enums", REPO_ROOT).get("enums") or {}).get("task_mode") or {})
    if not isinstance(decl.get("requires_tests"), dict):
        raise SchemaLoadError("enums.schema.json enums.task_mode.requires_tests 未声明(AIPOS-F211)")
    entry = _task_mode_entry(task_mode)
    if entry is None:
        return False
    flag = entry.get("requires_tests", False)
    if not isinstance(flag, bool):
        raise SchemaLoadError(f"enums.schema.json task_mode={task_mode!r} requires_tests 须为布尔(AIPOS-F211)")
    return flag


def task_mode_is_audit(task_mode: Any) -> bool:
    """AIPOS-F216 件④: 卡面 task_mode 是否审计模式 = 审计卡识别的卡面层唯一判定(唯一读取口: enums.schema task_mode.audit_card 声明, 每值带
    audit_card 布尔; 缺键 = false; 缺值 = missing_default 非审计; 未知 = 非审计)。取代原 7 模块 11 处 task_mode == "audit" 字面(F211 基线);
    卡级识别 audit_derivation.is_audit_card 的卡面层同读本函数。与 task_mode_* 族同经 _task_mode_entry 取条目(放本模块而非 audit_derivation:
    低层模块 autonomy_policy 与 finalize 合并后预载闭包只可依赖本模块, 不得牵入 audit_derivation 的整条 CLI 导入链)。声明缺 / 形变 = SchemaLoadError。"""
    from tools.schema_loader import SchemaLoadError, load_schema

    decl = ((load_schema("enums", REPO_ROOT).get("enums") or {}).get("task_mode") or {})
    if not isinstance(decl.get("audit_card"), dict):
        raise SchemaLoadError("enums.schema.json enums.task_mode.audit_card 未声明(AIPOS-F216)")
    entry = _task_mode_entry(str(task_mode or ""))
    if entry is None:
        return False
    flag = entry.get("audit_card", False)
    if not isinstance(flag, bool):
        raise SchemaLoadError(f"enums.schema.json task_mode={task_mode!r} audit_card 须为布尔(AIPOS-F216)")
    return flag


class CandidateSubjectError(ValueError):
    """AIPOS-F196: subject=candidate 模式卡的来源卡 / 候选声明不合(code ∈ enums.schema task_mode.candidate_subject.reject_codes)。"""

    def __init__(self, code: str, reason: str):
        super().__init__(f"{code}: {reason}")
        self.code = code
        self.reason = reason


def candidate_subject_declaration() -> dict[str, Any]:
    """enums.schema task_mode.candidate_subject(字段名 / 拒因码)。缺 = SchemaLoadError(fail-closed)。"""
    from tools.schema_loader import SchemaLoadError, load_schema

    decl = (((load_schema("enums", REPO_ROOT).get("enums") or {}).get("task_mode") or {}).get("candidate_subject"))
    fields = decl.get("fields") if isinstance(decl, dict) else None
    codes = decl.get("reject_codes") if isinstance(decl, dict) else None
    if (not isinstance(fields, dict) or not all(isinstance(fields.get(k), str) and fields[k] for k in ("source", "candidate"))
            or not isinstance(codes, dict) or not all(codes.get(k) for k in (
                "VALIDATION_SUBJECT_MISSING", "VALIDATION_CANDIDATE_INVALID", "VALIDATION_SOURCE_UNKNOWN"))):
        raise SchemaLoadError("enums.schema.json enums.task_mode.candidate_subject(fields.source / fields.candidate / reject_codes)未声明齐(AIPOS-F196)")
    return decl


def task_mode_subject_is_candidate(task_mode: str) -> bool:
    """AIPOS-F196 件①: 该模式的交付主体是否为固定候选(enums.schema task_mode.values[].subject == candidate; 缺 = 卡分支)。"""
    entry = _task_mode_entry(task_mode)
    return bool(entry) and str(entry.get("subject") or "").strip() == "candidate"


_FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def card_candidate_subject(card_frontmatter: dict[str, Any] | None) -> dict[str, str] | None:
    """AIPOS-F196 件①: 卡的固定候选主体唯一读取口。非 subject=candidate 模式 = None; 是 = {source, candidate}(卡面字段名读
    enums.schema task_mode.candidate_subject.fields)。缺字段 / 候选非完整 40 位小写 sha = CandidateSubjectError(fail-closed, 不当缺省放行)。
    只做形校验; 候选是否为仓中已有提交由 candidate_subject_values / 起草发卡核验(要读 git)。"""
    fm = card_frontmatter if isinstance(card_frontmatter, dict) else {}
    if not task_mode_subject_is_candidate(str(fm.get("task_mode") or "")):
        return None
    decl = candidate_subject_declaration()
    source_key, candidate_key = decl["fields"]["source"], decl["fields"]["candidate"]
    source = str(fm.get(source_key) or "").strip()
    candidate = str(fm.get(candidate_key) or "").strip()
    if not source or not candidate:
        missing = [k for k, v in ((source_key, source), (candidate_key, candidate)) if not v]
        raise CandidateSubjectError("VALIDATION_SUBJECT_MISSING", f"task_mode={fm.get('task_mode')} 卡缺 {', '.join(missing)}"
                                    f"(来源卡号 + 固定候选 40 位 sha 必填; 声明 enums.schema task_mode.candidate_subject)")
    if not _FULL_SHA_RE.match(candidate):
        raise CandidateSubjectError("VALIDATION_CANDIDATE_INVALID", f"{candidate_key}={candidate!r} 须为完整 40 位小写 sha")
    return {"source": source, "candidate": candidate}


def candidate_subject_values(workspace_root: Path, task_id: str, card_frontmatter: dict[str, Any] | None) -> dict[str, str] | None:
    """AIPOS-F196 件①: 固定候选主体的实值 {repository, commit_sha, tree_hash, branch, source, repo_path}(交回 / 审计报告契约给值、入口核对、
    裁决 artifact_subject 同读)。commit_sha = 卡面候选; tree_hash = 候选 tree(卡 lane.repo 解析仓 git rev-parse); branch = 来源卡卡分支名
    (card_branch_name, 说明候选出处, 入口只核等于本值)。非 candidate 模式 = None。候选不在仓 / 仓不可解析 = CandidateSubjectError。"""
    subject = card_candidate_subject(card_frontmatter)
    if subject is None:
        return None
    from tools.aipos_cli.workspace_config import CardRepoUnresolved

    try:
        repo = _card_repo_root(workspace_root, task_id, card_frontmatter or {}, allow_governance_root=False)
    except CardRepoUnresolved as exc:
        raise CandidateSubjectError("VALIDATION_CANDIDATE_INVALID", f"卡 {task_id} 的候选所在仓(lane.repo)不可解析: {exc}") from exc
    tree = _candidate_tree(repo, subject["candidate"], f"卡 {task_id} 候选所在仓 {repo}")
    return {"repository": Path(repo).name, "commit_sha": subject["candidate"], "tree_hash": tree,
            "branch": card_branch_name(subject["source"]), "source": subject["source"], "repo_path": str(repo)}


def _candidate_tree(repo: Path, candidate: str, where: str) -> str:
    """候选在仓中的 tree(git rev-parse <候选>^{tree}); 不是已有提交 / 读失败 = CandidateSubjectError(VALIDATION_CANDIDATE_INVALID)。
    固定候选主体(candidate_subject_values)与环境卡候选绑定(environment_binding_values)同一核对。"""
    import subprocess

    try:
        proc = subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", f"{candidate}^{{tree}}"],
                              capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        raise CandidateSubjectError("VALIDATION_CANDIDATE_INVALID", f"读候选 {candidate} 失败({repo}): {exc}") from exc
    tree = proc.stdout.strip()
    if proc.returncode != 0 or not _FULL_SHA_RE.match(tree):
        raise CandidateSubjectError("VALIDATION_CANDIDATE_INVALID", f"候选 {candidate} 不是{where} 中已有提交")
    return tree


def candidate_binding_declaration() -> dict[str, Any]:
    """AIPOS-F205: enums.schema task_mode.candidate_binding(适用模式 task_modes / 拒因码), 字段名经 fields_from 取
    candidate_subject.fields(不另立第二份)。缺 / 形变 / 适用模式不是已声明的不写仓非固定候选模式 = SchemaLoadError(fail-closed)。"""
    from tools.schema_loader import SchemaLoadError, load_schema

    decl = (((load_schema("enums", REPO_ROOT).get("enums") or {}).get("task_mode") or {}).get("candidate_binding"))
    modes = decl.get("task_modes") if isinstance(decl, dict) else None
    codes = decl.get("reject_codes") if isinstance(decl, dict) else None
    if (not isinstance(modes, list) or not modes or not all(isinstance(m, str) and m for m in modes) or not isinstance(codes, dict)
            or not codes.get("VALIDATION_BINDING_MODE") or decl.get("fields_from") != "candidate_subject.fields"):
        raise SchemaLoadError("enums.schema.json enums.task_mode.candidate_binding(task_modes / reject_codes.VALIDATION_BINDING_MODE / "
                              "fields_from=candidate_subject.fields)未声明齐(AIPOS-F205)")
    for mode in modes:
        entry = _task_mode_entry(mode)
        if entry is None or entry.get("writes_product_repo") is not False or task_mode_subject_is_candidate(mode) or task_mode_is_audit(mode):
            raise SchemaLoadError(f"enums.schema.json task_mode.candidate_binding.task_modes 含 {mode!r}: 须为已声明、writes_product_repo=false、"
                                  "非固定候选、非 audit 的模式(AIPOS-F205)")
    return {**decl, "fields": candidate_subject_declaration()["fields"]}


def card_environment_binding(card_frontmatter: dict[str, Any] | None) -> dict[str, str] | None:
    """AIPOS-F205 件①: 环境卡(enums.schema task_mode.candidate_binding.task_modes; config / docs / governance)可选候选绑定的唯一读取口。
    固定候选模式(validation)= None(其候选是交付主体, 走 card_candidate_subject); 两字段皆缺 = None(缺省 = 行为不变)。
    声明了 = {source, candidate}; 模式不适用 = VALIDATION_BINDING_MODE; 只写其一 = VALIDATION_SUBJECT_MISSING; 候选非完整 40 位小写 sha =
    VALIDATION_CANDIDATE_INVALID(CandidateSubjectError, fail-closed)。只做形校验; 来源卡在队列 / 候选是来源卡仓已有提交由 environment_binding_values 核。"""
    fm = card_frontmatter if isinstance(card_frontmatter, dict) else {}
    mode = str(fm.get("task_mode") or "").strip()
    if task_mode_subject_is_candidate(mode):
        return None
    decl = candidate_binding_declaration()
    source_key, candidate_key = decl["fields"]["source"], decl["fields"]["candidate"]
    source = str(fm.get(source_key) or "").strip()
    candidate = str(fm.get(candidate_key) or "").strip()
    if not source and not candidate:
        return None
    if mode.lower() not in {m.lower() for m in decl["task_modes"]}:
        raise CandidateSubjectError("VALIDATION_BINDING_MODE", f"task_mode={mode or '(缺)'} 卡不得声明 {source_key} / {candidate_key}"
                                    f"(可选候选绑定只限 {decl['task_modes']}; 声明 enums.schema task_mode.candidate_binding)")
    if not source or not candidate:
        missing = [k for k, v in ((source_key, source), (candidate_key, candidate)) if not v]
        raise CandidateSubjectError("VALIDATION_SUBJECT_MISSING", f"task_mode={mode} 卡的候选绑定缺 {', '.join(missing)}"
                                    "(来源卡号 + 固定候选 40 位 sha 须成对声明; 声明 enums.schema task_mode.candidate_binding)")
    if not _FULL_SHA_RE.match(candidate):
        raise CandidateSubjectError("VALIDATION_CANDIDATE_INVALID", f"{candidate_key}={candidate!r} 须为完整 40 位小写 sha")
    return {"source": source, "candidate": candidate}


def card_bound_candidate(card_frontmatter: dict[str, Any] | None) -> dict[str, str] | None:
    """AIPOS-F205: 卡面固定候选的统一读取口(验证根 run-start 候选须 = 本值)。固定候选主体卡 = card_candidate_subject + kind=subject;
    环境卡候选绑定 = card_environment_binding + kind=environment; 都无 = None。形不合 = CandidateSubjectError。"""
    subject = card_candidate_subject(card_frontmatter)
    if subject is not None:
        return {**subject, "kind": "subject"}
    binding = card_environment_binding(card_frontmatter)
    return {**binding, "kind": "environment"} if binding is not None else None


def card_candidate_repo(workspace_root: Path, task_id: str, card_frontmatter: dict[str, Any] | None, *,
                        allow_governance_root: bool = True) -> dict[str, Any]:
    """AIPOS-F205 件②: 卡的候选所在仓唯一解析(验证根 run-start 候选核对 / 源码送达 / 起草发卡绑定核验同读)。
    环境卡候选绑定(card_environment_binding 非 None)= 来源卡 lane.repo 经 workspace_config.resolve_card_repo 唯一解析(产品仓, 不接受治理根;
    来源卡不在队列 = CandidateSubjectError VALIDATION_SOURCE_UNKNOWN); 其余 = 本卡 lane.repo(行为不变, allow_governance_root 由调用方定)。
    返回 {repo: Path, card: 解析所依卡号, source: 来源卡号 | None}。仓解析不到 = CardRepoUnresolved(原样抛, 调用方转拒因)。"""
    fm = card_frontmatter if isinstance(card_frontmatter, dict) else {}
    binding = card_environment_binding(fm)
    if binding is None:
        return {"repo": Path(_card_repo_root(workspace_root, task_id, fm, allow_governance_root=allow_governance_root)), "card": task_id,
                "source": None}
    source = binding["source"]
    try:
        source_path, _q = _find_task_in_queue(workspace_root, source)
    except ValueError as exc:
        raise CandidateSubjectError("VALIDATION_SOURCE_UNKNOWN", f"卡 {task_id} 的来源卡 {source} 查找失败: {exc}") from exc
    if source_path is None:
        raise CandidateSubjectError("VALIDATION_SOURCE_UNKNOWN", f"卡 {task_id} 的来源卡 {source} 不在任何队列目录(须为本项目已发布的卡), "
                                    "无从定位候选所在仓")
    source_fm = _read_frontmatter(source_path)
    repo = _card_repo_root(workspace_root, source, {**source_fm, "task_id": source}, allow_governance_root=False)
    return {"repo": Path(repo), "card": source, "source": source}


def environment_binding_values(workspace_root: Path, task_id: str, card_frontmatter: dict[str, Any] | None) -> dict[str, str] | None:
    """AIPOS-F205 件①: 环境卡候选绑定的实值 {source, candidate, tree, repo_path, repository}(起草 / 发卡 / amend 核验、开工提示同读)。
    未声明绑定 = None。来源卡不在队列 = VALIDATION_SOURCE_UNKNOWN; 来源卡仓不可解析 / 候选不是来源卡仓已有提交 = VALIDATION_CANDIDATE_INVALID。
    不改交付主体: 交回 / 裁决契约仍按 task_mode 与 lane 判(只写治理根卡 = outputs)。"""
    from tools.aipos_cli.workspace_config import CardRepoUnresolved

    binding = card_environment_binding(card_frontmatter)
    if binding is None:
        return None
    try:
        located = card_candidate_repo(workspace_root, task_id, card_frontmatter)
    except CardRepoUnresolved as exc:
        raise CandidateSubjectError("VALIDATION_CANDIDATE_INVALID", f"卡 {task_id} 的来源卡 {binding['source']} 的产品仓(lane.repo)不可解析: "
                                    f"{exc}") from exc
    repo = located["repo"]
    tree = _candidate_tree(repo, binding["candidate"], f"卡 {task_id} 来源卡 {binding['source']} 的产品仓 {repo}")
    return {"source": binding["source"], "candidate": binding["candidate"], "tree": tree, "repo_path": str(repo),
            "repository": Path(repo).name}


def card_writes_product_repo(workspace_root: Path, card_frontmatter: dict[str, Any] | None) -> bool:
    """AIPOS-F178 件①: 非审计卡是否写产品仓 = task_mode 缺省(enums.schema writes_product_repo)或卡面 lane.repo 显式出现且
    解析到的仓不是治理根自身(workspace_config.resolve_card_repo 唯一解析); lane.repo 解析失败 = 按写仓(建树时给出拒因, fail-closed)。
    AIPOS-F196: 交付主体为固定候选的模式(subject=candidate)lane.repo = 候选所在仓(只读取证), 恒不写仓。"""
    from tools.aipos_cli.workspace_config import CardRepoUnresolved

    fm = card_frontmatter if isinstance(card_frontmatter, dict) else {}
    if task_mode_writes_product_repo(str(fm.get("task_mode") or "")):
        return True
    if task_mode_subject_is_candidate(str(fm.get("task_mode") or "")):
        return False
    lane = fm.get("lane") if isinstance(fm.get("lane"), dict) else {}
    if not str(lane.get("repo") or "").strip():
        return False
    try:
        repo = _card_repo_root(workspace_root, str(fm.get("task_id") or ""), fm)
    except CardRepoUnresolved:
        return True
    return Path(repo).resolve() != Path(workspace_root).resolve()


def card_needs_worktree(workspace_root: Path, card_frontmatter: dict[str, Any] | None) -> bool:
    """是否建卡工作树的唯一判据(AIPOS-F178: 声明 enums.schema task_mode.product_repo_write)——门认领 queue_mutation、loop 认领后核验与
    自动补建、`lybra queue repair` 缺树修复、my-tasks 开工面、card render、write_boundary、finalize 缺分支判定、审计裁决绑 tip 同读。
    执行卡 = card_writes_product_repo(写产品仓 = 卡分支工作树; 只写治理根 = 不建树);
    AIPOS-F89 件③a: 审计卡跟随被审卡(被审卡写仓 = 被审分支 tip 的只读 detached 取证工作树; 被审卡查不到 / 审计卡缺被审卡字段
    = fail-closed 按需建, 建树时给出拒因)。"""
    fm = card_frontmatter if isinstance(card_frontmatter, dict) else {}
    reviewed = forensic_subject(fm)
    if reviewed is None:
        return card_writes_product_repo(workspace_root, fm)
    if not reviewed:
        # 审计卡缺被审卡字段 → 需要(建树时给出拒因)
        return True
    task_path, _ = _find_task_in_queue(workspace_root, reviewed)
    if task_path is None:
        return True
    reviewed_fm = _read_frontmatter(task_path)
    # AIPOS-F211 件②: reviewed card without task_mode = schema missing_default (no product repo write), single fallback _task_mode_entry
    return card_writes_product_repo(workspace_root, {**reviewed_fm, "task_id": str(reviewed_fm.get("task_id") or reviewed)})


def card_follows_candidate_subject(workspace_root: Path, card_frontmatter: dict[str, Any] | None) -> bool:
    """AIPOS-F196 件①(F197 自 card_kickoff_without_worktree 更名; 开工面是否放行不建树卡改由 card_workstation_view 按
    本函数 / card_governance_outputs 判, 原函数无调用方后删除): 交付主体为固定候选的卡(enums.schema task_mode.candidate_subject;
    validation)及其审计卡(跟随被审卡)。用于不建树卡的开工放行与说明措辞分流(card render / write_boundary / 开工面); 是否建树仍只问
    card_needs_worktree。被审卡查不到 = False。"""
    fm = card_frontmatter if isinstance(card_frontmatter, dict) else {}
    reviewed = forensic_subject(fm)
    if reviewed is None:
        return task_mode_subject_is_candidate(str(fm.get("task_mode") or ""))
    if not reviewed:
        return False
    task_path, _ = _find_task_in_queue(workspace_root, reviewed)
    if task_path is None:
        return False
    return task_mode_subject_is_candidate(str(_read_frontmatter(task_path).get("task_mode") or ""))


class GovernanceOutputsError(ValueError):
    """AIPOS-F197 件②: 只写治理根卡的产出清单不合(code ∈ transitions.schema artifact_ingest.return.governance_root.reject_codes)。"""

    def __init__(self, code: str, reason: str):
        super().__init__(f"{code}: {reason}")
        self.code = code
        self.reason = reason


def governance_outputs_declaration() -> dict[str, Any]:
    """transitions.schema artifact_ingest.return.governance_root(只写治理根卡的交回契约)。缺 / 形不合 = SchemaLoadError(fail-closed)。"""
    from tools.schema_loader import SchemaLoadError

    decl = (_artifact_ingest_declaration().get("return") or {}).get("governance_root")
    keys = decl.get("required_frontmatter") if isinstance(decl, dict) else None
    codes = decl.get("reject_codes") if isinstance(decl, dict) else None
    if (not isinstance(keys, list) or not keys or not isinstance(decl.get("field_hints"), dict) or not isinstance(codes, dict)
            or not all(codes.get(k) for k in ("INGEST_OUTPUTS_INVALID", "INGEST_OUTPUT_MISSING", "INGEST_OUTPUT_OUT_OF_BOUNDARY",
                                              "INGEST_OUTPUTS_CHANGED"))):
        raise SchemaLoadError("transitions.schema.json artifact_ingest.return.governance_root(required_frontmatter / field_hints / "
                              "reject_codes)未声明齐(AIPOS-F197)")
    return decl


def card_governance_outputs(workspace_root: Path, card_frontmatter: dict[str, Any] | None) -> bool:
    """AIPOS-F197: 卡的交付主体是否为治理根产出清单(enums.schema task_mode.governance_outputs_subject 唯一读取口)= 显式声明只写治理根:
    task_mode 为已声明的不写仓模式(writes_product_repo=false, 非固定候选, 非 audit)且卡面 lane.repo 非空、经唯一解析落到治理根自身
    (保留值 card.schema intent_face.lane.governance_root_ref, 或无仓声明项目里派生的治理根路径)——即不建卡工作树(card_needs_worktree 的否)。
    缺 lane.repo / 缺 task_mode 的存量卡不算(行为同 F178: 不建树、不入选开工)。审计卡跟随被审卡(被审卡缺 task_mode = 按代码卡读)。"""
    fm = card_frontmatter if isinstance(card_frontmatter, dict) else {}
    reviewed = forensic_subject(fm)
    if reviewed is not None:
        if not reviewed:
            return False
        task_path, _ = _find_task_in_queue(workspace_root, reviewed)
        if task_path is None:
            return False
        reviewed_fm = _read_frontmatter(task_path)
        fm = {**reviewed_fm, "task_id": str(reviewed_fm.get("task_id") or reviewed)}
    mode = str(fm.get("task_mode") or "").strip()
    # AIPOS-F211 件②: 缺 task_mode = missing_default 条目(唯一兜底 _task_mode_entry); 存量缺 task_mode 卡不算治理根产出清单(声明 governance_outputs_subject)
    entry = _task_mode_entry(mode)
    if (entry is None or entry.get("missing") or task_mode_is_audit(mode) or task_mode_writes_product_repo(mode)
            or task_mode_subject_is_candidate(mode)):
        return False
    lane = fm.get("lane") if isinstance(fm.get("lane"), dict) else {}
    if not str(lane.get("repo") or "").strip():
        return False
    return not card_needs_worktree(workspace_root, fm)


def governance_outputs_manifest(workspace_root: Path, task_id: str, card_frontmatter: dict[str, Any] | None = None,
                                return_frontmatter: dict[str, Any] | None = None) -> dict[str, Any]:
    """AIPOS-F197 件②: 只写治理根卡的产出清单与指纹(唯一实现; 产物入口核对、门交回绑定、顾问复核结案 / 裁决 / finalize 复核同读)。

    清单来源 = 本卡 Return frontmatter outputs(return_frontmatter 缺 = 读声明落点的 Return)。逐项核对(transitions.schema
    artifact_ingest.return.governance_root.checks): 无 `..` 段、存在、普通文件、自身非符号链接、真实路径在治理根内且 = 字面、
    write_boundary.check_access(executor, mutate, 本卡)放行。返回 {kind, repository, outputs_digest, files[{path, sha256, size}], outputs};
    不合 = GovernanceOutputsError(code 读声明 reject_codes)。只读。"""
    import hashlib

    decl = governance_outputs_declaration()
    gov = Path(workspace_root).expanduser().resolve()
    if return_frontmatter is None:
        found = find_return_artifact(gov, task_id)
        if found is None:
            raise GovernanceOutputsError("INGEST_OUTPUTS_INVALID", f"卡 {task_id} 的 Return 不在声明落点 {return_artifact_dir(gov, task_id)}")
        return_frontmatter = _read_frontmatter(found, allow_missing_block=True)
    key = str(decl["required_frontmatter"][0])
    raw = return_frontmatter.get(key) if isinstance(return_frontmatter, dict) else None
    items = [raw] if isinstance(raw, str) else raw
    if (not isinstance(items, list) or not items
            or any(not isinstance(i, str) or not i.strip() or _is_placeholder_value(i) for i in items)):
        raise GovernanceOutputsError("INGEST_OUTPUTS_INVALID", f"Return frontmatter {key} 须为非空串列表(产出文件路径), 现为 {raw!r}")
    from tools.aipos_cli.write_boundary import WriteBoundaryError, check_access

    files: dict[str, dict[str, Any]] = {}
    for item in items:
        text = item.strip()
        if ".." in Path(text).parts:
            raise GovernanceOutputsError("INGEST_OUTPUTS_INVALID", f"{key} 项 {text!r} 含 `..` 段")
        literal = Path(text).expanduser()
        literal = literal if literal.is_absolute() else gov / literal
        if literal.is_symlink() or not literal.is_file():
            what = "是符号链接" if literal.is_symlink() else ("不是普通文件" if literal.exists() else "不存在")
            raise GovernanceOutputsError("INGEST_OUTPUT_MISSING", f"{key} 项 {text!r} → {literal} {what}")
        real = literal.resolve()
        try:
            rel = real.relative_to(gov).as_posix()
        except ValueError:
            raise GovernanceOutputsError("INGEST_OUTPUT_OUT_OF_BOUNDARY", f"{key} 项 {text!r} 真实路径 {real} 不在治理根 {gov} 内") from None
        if str(real) != os.path.abspath(str(literal)):
            raise GovernanceOutputsError("INGEST_OUTPUT_OUT_OF_BOUNDARY", f"{key} 项 {text!r} 经符号链接解析到 {real}(字面 {literal}), 不放行")
        try:
            verdict = check_access(gov, role="executor", path=real, level="mutate", task_id=task_id)
        except (WriteBoundaryError, OSError, ValueError) as exc:
            raise GovernanceOutputsError("INGEST_OUTPUT_OUT_OF_BOUNDARY", f"{key} 项 {rel} 写权限边界不可判定(fail-closed): {exc}") from exc
        if not verdict.get("allowed"):
            raise GovernanceOutputsError("INGEST_OUTPUT_OUT_OF_BOUNDARY",
                                         f"{key} 项 {rel} 写权限边界不放行本卡执行角色写该处: [{verdict.get('code')}] {verdict.get('reason')}")
        data = real.read_bytes()
        files[rel] = {"path": rel, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
    ordered = [files[k] for k in sorted(files)]
    lines = [f"{f['sha256']}  {f['path']}" for f in ordered]
    digest = hashlib.sha256("".join(line + "\n" for line in lines).encode("utf-8")).hexdigest()
    return {"kind": governance_outputs_kind(), "repository": gov.name, "outputs_digest": digest, "files": ordered, "outputs": lines}


def governance_outputs_return_binding(workspace_root: Path, task_id: str) -> dict[str, Any] | None:
    """AIPOS-F197: 本卡最新门交回记录绑定的产出清单主体(artifact_subject.kind = governance_outputs); 无记录 / 无绑定 = None。"""
    records = _read_task_records(workspace_root, task_id)
    latest = records.get("latest_return") or {}
    subject = latest.get("artifact_subject") if isinstance(latest.get("artifact_subject"), dict) else None
    if not subject or str(subject.get("kind") or "") != governance_outputs_kind() or not str(subject.get("outputs_digest") or "").strip():
        return None
    return dict(subject)


def governance_outputs_recheck(workspace_root: Path, task_id: str, card_frontmatter: dict[str, Any] | None = None, *,
                               allow_legacy: bool = False) -> dict[str, Any]:
    """AIPOS-F197 件②: 复核结案 / 裁决 / finalize 前的「此刻清单指纹 = 交回绑定指纹」核对(唯一实现)。
    返回 {ok, code, legacy, bound, current, reason}: 绑定在且此刻清单合规且指纹相等 = ok; 不等 / 此刻清单不合 = ok False
    (码 INGEST_OUTPUTS_CHANGED 或清单拒因码)。最新交回记录无产出清单绑定(F197 前存量交回 / 未经产物入口)= allow_legacy 时 ok + legacy
    (沿 AIPOS-F70 legacy 裁决口径: 调用方出警告放行), 否则 ok False(产物入口 / 审计报告入口要求绑定)。"""
    bound = governance_outputs_return_binding(workspace_root, task_id)
    try:
        current = governance_outputs_manifest(workspace_root, task_id, card_frontmatter)
        current_problem = None
    except GovernanceOutputsError as exc:
        current, current_problem = None, exc
    if bound is None:
        reason = (f"卡 {task_id} 最新交回记录无产出清单绑定(artifact_subject.kind=governance_outputs; F197 前存量交回或未经产物入口); "
                  f"出口: 经产物入口重交回(lybra artifact ingest --task-id {task_id} --kind return)")
        if allow_legacy:
            return {"ok": True, "code": "LEGACY_NO_BINDING", "legacy": True, "bound": None, "current": current,
                    "reason": f"警告(legacy, 沿 AIPOS-F70 口径放行): {reason}"}
        return {"ok": False, "code": "INGEST_OUTPUTS_CHANGED", "legacy": True, "bound": None, "current": current, "reason": reason}
    if current_problem is not None:
        return {"ok": False, "code": current_problem.code, "legacy": False, "bound": bound, "current": None,
                "reason": f"此刻产出清单不合: {current_problem}"}
    if current["outputs_digest"] != bound["outputs_digest"]:
        return {"ok": False, "code": "INGEST_OUTPUTS_CHANGED", "legacy": False, "bound": bound, "current": current,
                "reason": (f"卡 {task_id} 交回后产出被改: 交回绑定指纹 {bound['outputs_digest']} ≠ 此刻 {current['outputs_digest']}"
                           f"(交回 {bound.get('outputs')}; 此刻 {current['outputs']}); 出口: 执行体按改后产出重写 Return 后重交回")}
    return {"ok": True, "code": "OK", "legacy": False, "bound": bound, "current": current,
            "reason": f"产出清单指纹 {current['outputs_digest']} = 交回绑定"}


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
    if task_mode_is_audit(fm.get("task_mode")):
        return audit_report_artifact_path(workspace_root, task_id)
    return _return_artifact_path(workspace_root, task_id)


def delivery_subject_kinds() -> tuple[str, ...]:
    """AIPOS-F214 件⑤: 交付主体三态值域唯一读取口 enums.schema task_mode.delivery_subject.kinds(原手写常量 DELIVERY_SUBJECT_KINDS 退役)。
    缺 / 形不合 = SchemaLoadError。"""
    from tools.schema_loader import SchemaLoadError, load_schema

    kinds = ((((load_schema("enums", REPO_ROOT).get("enums") or {}).get("task_mode") or {}).get("delivery_subject") or {}).get("kinds"))
    if not isinstance(kinds, list) or not kinds or not all(isinstance(k, str) and k for k in kinds) or len(set(kinds)) != len(kinds):
        raise SchemaLoadError("enums.schema.json task_mode.delivery_subject.kinds 须为非空无重复串列表(AIPOS-F210)")
    return tuple(kinds)


def governance_outputs_kind() -> str:
    """AIPOS-F214 件⑤(F208 P2-4): 治理根产出清单交付主体的 kind 值唯一读取口 enums.schema task_mode.governance_outputs_subject.subject_kind
    (交回绑定 artifact_subject.kind / 交付主体 kind 同用; 原各处字面量 "governance_outputs" 退役); 须 ∈ delivery_subject_kinds。
    缺 / 不在三态 = SchemaLoadError。"""
    from tools.schema_loader import SchemaLoadError, load_schema

    kind = ((((load_schema("enums", REPO_ROOT).get("enums") or {}).get("task_mode") or {}).get("governance_outputs_subject") or {})
            .get("subject_kind"))
    if not isinstance(kind, str) or kind not in delivery_subject_kinds():
        raise SchemaLoadError(f"enums.schema.json task_mode.governance_outputs_subject.subject_kind 须 ∈ delivery_subject.kinds(得 {kind!r})")
    return kind


def branch_delivery_subject(task_id: str | None, *, audit: bool = False, branch_pattern: str | None = None) -> dict[str, Any]:
    """AIPOS-F210: 卡分支态交付主体 {kind: branch, task_id, audit, branch, base}——分支名 / 基线只经 card_branch_name / card_base_branch
    (声明 transitions.schema N5.branch_integration); branch_pattern = 调用方已读的分支式样(machine_zone 按 product_root 读)。
    task_id 缺 = 分支声明套占位卡号(与 report_frontmatter_contract 同口径)。"""
    subject_id = str(task_id or "").strip()
    placeholder = subject_id or ("<被审卡ID>" if audit else "<卡ID>")
    branch = card_branch_name(placeholder, {"branch_pattern": branch_pattern} if branch_pattern else None)
    return {"kind": "branch", "task_id": subject_id or None, "audit": audit, "branch": branch, "base": card_base_branch()}


def card_delivery_subject(workspace_root: Path | None, task_id: str, card_frontmatter: dict[str, Any] | None, *,
                          branch_pattern: str | None = None) -> dict[str, Any]:
    """AIPOS-F210(F208 P0-2, gap #218): 卡的交付主体三态唯一判定——卡正文工作纪律(machine_zone)、审计取证锚点
    (audit_derivation.render_forensic_anchor)、报告必填句与交回 / 裁决骨架(card_report_contract)、开工面(card_workstation_view)同读;
    声明 enums.schema task_mode.delivery_subject。判定顺序 = 报告契约分形顺序(先固定候选, 再治理根产出清单, 其余卡分支):

    - candidate: 主体卡(执行卡本卡 / 审计卡的被审卡)task_mode 的交付主体为固定候选(task_mode_subject_is_candidate)——
      {source, candidate}(card_candidate_subject 形校验; 卡面不合 = problem 原文, source / candidate 为 None, 不当卡分支放行);
    - governance_outputs: card_governance_outputs(显式声明只写治理根; 审计卡跟随被审卡)——附主体卡最新交回绑定的 outputs_digest
      (governance_outputs_return_binding; 未交回 / 无绑定 = None; 审计卡报告契约与取证锚点据此给值);
    - branch: 其余(branch_delivery_subject: 分支名 + 基线)。缺 task_mode 的存量不建树卡仍落此态(与报告契约同口径; 缺省值归 F208 卡组 C)。

    审计卡的被审卡按 reviewed_task_id 从队列读; workspace_root 缺(调用方未给治理根)= 只按执行卡卡面判固定候选, 其余按卡分支
    (治理根产出清单须经治理根解析 lane.repo, 不猜)。声明缺 = SchemaLoadError 上抛(fail-closed)。"""
    fm = card_frontmatter if isinstance(card_frontmatter, dict) else {}
    reviewed = forensic_subject(fm)
    audit = reviewed is not None
    subject_id = str(task_id or "").strip() if reviewed is None else reviewed
    subject_fm: dict[str, Any] | None = fm if reviewed is None else None
    if reviewed and workspace_root is not None:
        reviewed_path, _q = _find_task_in_queue(workspace_root, reviewed)
        subject_fm = _read_frontmatter(reviewed_path) if reviewed_path else {}
    if subject_fm is not None and task_mode_subject_is_candidate(str(subject_fm.get("task_mode") or "")):
        try:
            candidate, problem = card_candidate_subject(subject_fm), None
        except CandidateSubjectError as exc:
            candidate, problem = None, str(exc)
        return {"kind": "candidate", "task_id": subject_id or None, "audit": audit, "source": (candidate or {}).get("source"),
                "candidate": (candidate or {}).get("candidate"), "problem": problem}
    if workspace_root is not None and card_governance_outputs(workspace_root, {**fm, "task_id": str(task_id or "")}):
        bound = governance_outputs_return_binding(workspace_root, subject_id) if subject_id else None
        return {"kind": governance_outputs_kind(), "task_id": subject_id or None, "audit": audit,
                "outputs_digest": str((bound or {}).get("outputs_digest") or "") or None}
    return branch_delivery_subject(subject_id, audit=audit, branch_pattern=branch_pattern)


def card_workstation_view(workspace_root: Path, task_id: str, card_frontmatter: dict[str, Any] | None, *,
                          branch_pattern: str | None = None) -> dict[str, Any]:
    """`lybra my-tasks --json` 每张 claimed 卡的开工面字段(工位 /go 只读这些字段, 不自行推导)。

    - worktree_required: 是否建卡工作树(AIPOS-F178 唯一判据 card_needs_worktree); False = 只写治理根, worktree_path 恒 None,
      worktree_refusal = WORKTREE_NOT_REQUIRED(点名治理根报告落点)
    - worktree_path: 推导出的工作树路径(不可推导 / 不建树 = None)
    - worktree_exists: 该路径是否已在盘上(认领时由驱动方建)
    - worktree_refusal: None | {code, reason} —— 不可推导(CardRepoUnresolved.code / WORKTREE_ROOT_UNDECLARED)
      或尚未建立(WORKTREE_NOT_CREATED); 从不输出空串
    - report_path / report_refusal: 同构(REPORT_LOCATION_UNDECLARED)
    - report_required_frontmatter: 报告必填字段 [{key, hint, value}](AIPOS-F93 件①, card_report_contract); 不可推导 =
      report_refusal REPORT_CONTRACT_UNRESOLVED 且 report_path 置空
    - carried_from / carried_line: AIPOS-F186 撤换承继来源(card_carried_from; 不承继 = None)与开工提示一句(含先合入当前 base_branch
      的 base_sync 命令, carried_kickoff_line); 卡面形不合 = carried_line 为拒因原文
    - delivery_subject / branch: AIPOS-F210 交付主体三态(card_delivery_subject 唯一判定: branch / candidate / governance_outputs)与
      卡分支名(仅卡分支态, 其余 None); 判定失败 = delivery_subject None + delivery_subject_refusal DELIVERY_SUBJECT_UNRESOLVED。
      卡正文工作纪律(machine_zone)按本视图渲染; branch_pattern = 调用方已读的分支式样(缺省 = 声明)
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
    # AIPOS-F186 件②: 承继来源(执行体开工材料显示; 提示先把当前 base_branch 合入)
    try:
        view["carried_from"] = card_carried_from(fm)
        view["carried_line"] = carried_kickoff_line(fm, task_id)
    except (ValueError, SchemaLoadError) as exc:
        view["carried_from"] = None
        view["carried_line"] = f"CARRIED_FROM_INVALID: {exc}"
    # AIPOS-F178 件②: 是否建树只问唯一判据 card_needs_worktree; 不建树(只写治理根)= 不给工作树路径, 拒因点名治理根报告落点
    try:
        needs_worktree = card_needs_worktree(workspace_root, {**fm, "task_id": task_id})
    except (SchemaLoadError, OSError, ValueError) as exc:
        needs_worktree = True
        view["worktree_refusal"] = {"code": "WORKTREE_ROOT_UNDECLARED", "reason": f"是否建卡工作树不可判定(AIPOS-F178 判据): {exc}"}
    view["worktree_required"] = needs_worktree
    # AIPOS-F197 件①: 不建树卡中固定候选卡(F196)与显式声明只写治理根的卡(card_governance_outputs)及其审计卡照常开工; 措辞分流:
    # 固定候选卡 / 只写治理根的执行卡(outputs_note → template_governance_root)/ 只写治理根卡的审计卡 / 其余存量不建树卡(不入选, F178)
    # AIPOS-F210: 交付主体三态唯一判定 card_delivery_subject(卡正文 / 审计锚点 / 报告契约同读); 分支名只在卡分支态给出
    view["delivery_subject"] = None
    view["branch"] = None
    subject_problem = ""
    try:
        subject = card_delivery_subject(workspace_root, task_id, {**fm, "task_id": task_id}, branch_pattern=branch_pattern)
    except (SchemaLoadError, OSError, ValueError) as exc:
        subject, subject_problem = None, str(exc)
        view["delivery_subject_refusal"] = {"code": "DELIVERY_SUBJECT_UNRESOLVED", "reason": f"交付主体判定失败: {exc}"}
    else:
        view["delivery_subject"] = subject
        view["branch"] = subject.get("branch") if subject["kind"] == "branch" else None
    candidate_card = governance_card = False
    if view["worktree_refusal"] is None and not needs_worktree:
        if subject is None:
            view["worktree_refusal"] = {"code": "VALIDATION_SUBJECT_UNRESOLVED", "reason": f"不建树卡的交付主体判定失败: {subject_problem}"}
        else:
            candidate_card = subject["kind"] == "candidate"
            governance_card = subject["kind"] == governance_outputs_kind()
    view["kickoff_without_worktree"] = view["worktree_refusal"] is None and (candidate_card or governance_card)
    if view["worktree_refusal"] is None and not needs_worktree:
        try:
            where = str(card_report_path(workspace_root, task_id, fm))
        except (SchemaLoadError, OSError, ValueError) as exc:
            where = f"(报告落点不可推导: {exc})"
        if candidate_card:
            view["worktree_refusal"] = {
                "code": "WORKTREE_NOT_REQUIRED",
                "reason": (f"不建(交付主体 = 来源卡的固定候选, 不写产品仓源码; 判据 enums.schema task_mode.candidate_subject): "
                           f"产物只落本卡验证根(本卡本活动 run 目录)与报告落点 {where}"),
            }
        elif governance_card and forensic_subject(fm) is not None:
            view["worktree_refusal"] = {
                "code": "WORKTREE_NOT_REQUIRED",
                "reason": (f"不建(被审卡只写治理根, 审计对象 = 被审卡交回时产品绑定的治理根产出清单; 判据 enums.schema "
                           f"task_mode.governance_outputs_subject): 审计报告写 {where}"),
            }
        elif not governance_card:
            # AIPOS-F178 件②: 缺 lane.repo / 缺 task_mode 的存量不建树卡不入选开工, 拒因点名治理根报告落点与 F197 显式声明出口
            view["worktree_refusal"] = {
                "code": "WORKTREE_NOT_REQUIRED",
                "reason": (f"本卡只写治理根(不写产品仓, 不建卡工作树/卡分支; 判据 enums.schema task_mode.product_repo_write): "
                           f"产物写治理根报告落点 {where}; 工位开工提示只给建树卡与显式声明只写治理根的卡(AIPOS-F197: 卡面 task_mode "
                           f"docs / governance / config + lane.repo: governance_root), 本卡开工说明见卡渲染物(card render)"),
            }
        else:
            slot = str(Path(where).parent)
            view["worktree_refusal"] = {
                "code": "WORKTREE_NOT_REQUIRED",
                "reason": (f"本卡只写治理根(不写产品仓, 不建卡工作树/卡分支; 判据 enums.schema task_mode.product_repo_write): "
                           f"产物写治理根报告落点 {where}"),
            }
            view["outputs_note"] = (f"只写治理根——产物写本卡报告落点目录 {slot}/ 及其子目录(如 {slot}/artifacts/; 写权限边界判定, "
                                    f"其余治理根路径与产品仓一律不写); 报告 frontmatter outputs 逐项列出产出文件, 产品逐个计算 sha256 "
                                    f"并核对, 交回后不再改动(判据 enums.schema task_mode.governance_outputs_subject)")
    elif view["worktree_refusal"] is None:
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
    # AIPOS-F196 件③: 本卡验证根节(validation_root.render_section_lines; AIPOS-F206: 明细只在开工提示, 章程只留通用段; 实例 = 认领实例, 缺 = assigned_to)。
    # 声明读不出 = 开工面拒因(VALIDATION_ROOT_UNRESOLVED, select_next_card 不入选; fail-closed, 不出缺节的开工提示)
    # AIPOS-F212 件①: 本节只此一处算(工位 /go 与 lybra kickoff 同读本视图的 validation_root_lines / validation_root_refusal, 拒因同码);
    # 沙箱列举按卡 harness 唯一推导 loop_driver.card_harness(卡面值, 缺省由本卡执行方式推出), 不再读卡面原值
    view["validation_root_lines"] = []
    view.update(_card_harness_view(workspace_root, task_id, fm))
    instance = str(fm.get("claimed_by") or fm.get("assigned_to") or "").strip()
    if instance:
        from tools.aipos_cli.validation_root import ValidationRootError, render_section_lines

        try:
            lines = render_section_lines(workspace_root, instance=instance, task_id=task_id, harness=view["harness"],
                                         include_inactive=True)
        except (ValidationRootError, SchemaLoadError, OSError, ValueError) as exc:
            view["validation_root_refusal"] = {"code": "VALIDATION_ROOT_UNRESOLVED", "reason": f"本卡验证根节不可渲染: {exc}"}
        else:
            if lines and view["harness"] is None:
                # 本卡有验证根节而 harness 不可推导 = 沙箱可写根无从按卡渲染(不退回列全部沙箱), 同码拒
                view["validation_root_refusal"] = {
                    "code": "VALIDATION_ROOT_UNRESOLVED",
                    "reason": f"本卡验证根节不可渲染: 沙箱按卡 harness 渲染, harness 不可推导({view['harness_refusal']['reason']})"}
            else:
                view["validation_root_lines"] = lines
    try:
        view["report_path"] = str(card_report_path(workspace_root, task_id, fm))
    except (SchemaLoadError, OSError, ValueError) as exc:
        view["report_refusal"] = {"code": "REPORT_LOCATION_UNDECLARED", "reason": f"报告落点声明读取失败: {exc}"}
        return view
    # AIPOS-F93 件①: 报告必填字段(声明单源 report_frontmatter_contract); 审计卡附被审 tip/tree 实值(取证工作树 HEAD)。
    # 不可推导 = 报告契约拒因(report_path 置空, select_next_card 按报告不可推导排除), 不出无清单的开工提示。
    try:
        view["report_required_frontmatter"] = card_report_contract(
            workspace_root, task_id, fm, worktree=Path(view["worktree_path"]) if view["worktree_exists"] else None,
            branch_pattern=branch_pattern)
    except (SchemaLoadError, OSError, ValueError) as exc:
        view["report_path"] = None
        view["report_refusal"] = {"code": "REPORT_CONTRACT_UNRESOLVED", "reason": f"报告必填字段不可推导: {exc}"}
    return view


def card_harness_mismatch(workspace_root: Path, task_id: str, fm: dict[str, Any] | None, *, claimed: bool) -> dict[str, Any] | None:
    """AIPOS-F235 件③: 子 agent 卡卡面 harness 与派发顾问现登记 harness 不符(唯一判定; next 未认领卡不派生认领、kickoff KICKOFF_HARNESS_MISMATCH 同读)。
    只判: 卡面有 harness、本卡执行方式不是 pi(workspace_config.card_execution_mode; 审计卡恒 pi)、派发顾问(enrollment.card_dispatch_advisor)
    有登记 harness 且为顾问会话 kind(enrollment.dispatch_harness source=advisor)。不符 = {face, registered, advisor, source, reason, amend_command}
    (声明 verbs.lybra_roles_redeclare.card_harness_mismatch; 已认领卡 = --restricted, card.schema restricted_amend 含 harness); 其余 = None。
    执行方式声明形坏 / 连接文件读不出 = None(该口径由 harness 推导 / 发卡核身份各自拒, 本判定不重复拒)。"""
    from tools.aipos_cli.audit_derivation import is_audit_card
    from tools.aipos_cli.enrollment import card_dispatch_advisor, dispatch_harness, redeclaration_declaration
    from tools.aipos_cli.workspace_config import EXECUTION_MODE_PI, card_execution_mode, project_execution

    card = fm or {}
    face = str(card.get("harness") or "").strip()
    advisor = card_dispatch_advisor(card)
    if not face or not advisor:
        return None
    try:
        execution = project_execution(Path(workspace_root))
        mode = card_execution_mode(execution, card, audit_card=is_audit_card(task_id, card))
        if mode is None or mode == EXECUTION_MODE_PI:
            return None
        view = dispatch_harness(Path(workspace_root), advisor=advisor, executor=None)
    except ValueError:
        return None
    registered = view["harness"]
    if view["source"] != "advisor" or not registered or registered == face:
        return None
    reg = view["advisor_registration"] or {}
    texts = redeclaration_declaration()["card_harness_mismatch"]
    fill = {"task_id": task_id, "face": face, "registered": registered, "advisor": advisor, "governance_root": str(workspace_root),
            "source": reg.get("source_label") or reg.get("source") or "-", "restricted": " --restricted" if claimed else ""}
    return {"face": face, "registered": registered, "advisor": advisor, "source": fill["source"],
            "reason": str(texts["reason"]).format(**fill), "amend_command": str(texts["amend_command"]).format(**fill)}


def _card_harness_view(workspace_root: Path, task_id: str, fm: dict[str, Any]) -> dict[str, Any]:
    """AIPOS-F212 件①: 开工面的卡 harness(唯一推导 loop_driver.card_harness; /go 验证根沙箱列举与 lybra kickoff 时限句同读)。
    返回 {harness, harness_refusal}: 推导失败(子 agent 执行者无接入登记 / 执行方式声明形坏 / 缺省表缺条目)= harness None +
    harness_refusal {code HARNESS_UNRESOLVED, reason 原文(含出口)}; 不猜、不退回列全部沙箱。"""
    from tools.aipos_cli.loop_driver import card_harness
    from tools.schema_loader import SchemaLoadError

    try:
        return {"harness": card_harness(fm, workspace_root, task_id=task_id), "harness_refusal": None,
                "harness_mismatch": card_harness_mismatch(workspace_root, task_id, fm, claimed=True)}  # AIPOS-F235 件③: 开工面(已认领卡)
    except (SchemaLoadError, OSError, ValueError) as exc:
        return {"harness": None, "harness_refusal": {"code": "HARNESS_UNRESOLVED", "reason": str(exc)}, "harness_mismatch": None}


def card_report_contract(workspace_root: Path, task_id: str, card_frontmatter: dict[str, Any] | None,
                         *, worktree: Path | None = None, branch_pattern: str | None = None) -> list[dict[str, Any]]:
    """AIPOS-F93 件①: 一张卡的报告必填 frontmatter 契约(my-tasks 开工面 / 认领模板 / 卡正文 / 审计卡必填句 / 交回与裁决骨架同读)。

    AIPOS-F210 件③: 分形只按交付主体三态(card_delivery_subject 唯一判定)——
    固定候选(validation 卡及其审计卡)= 三键实值 = 候选(candidate_subject_values 唯一实值), 不读卡分支 / 取证树;
    治理根产出清单(只写治理根的执行卡 = outputs 清单, 无分支三键; 其审计卡 = verdict + 交回绑定的 outputs_digest, 产品给值);
    卡分支 = 执行卡 return 契约(分支 = 本卡分支) / 审计卡 verdict 契约(分支 = 被审卡分支), 取证工作树已建时附被审 tip/tree 实值
    (= 取证工作树 HEAD, 认领时由门建于被审分支 tip)。取证工作树在盘但读不出 HEAD = ValueError(fail-closed, 不给空值);
    候选不合 = CandidateSubjectError(fail-closed)。"""
    fm = card_frontmatter if isinstance(card_frontmatter, dict) else {}
    reviewed = forensic_subject(fm)
    kind = "return" if reviewed is None else "verdict"
    subject = card_delivery_subject(workspace_root, task_id, fm, branch_pattern=branch_pattern)
    if subject["kind"] == "candidate":
        subject_fm = fm
        if reviewed:
            reviewed_path, _q = _find_task_in_queue(workspace_root, reviewed)
            subject_fm = _read_frontmatter(reviewed_path) if reviewed_path else {}
        candidate = candidate_subject_values(workspace_root, str(subject["task_id"] or ""), subject_fm)
        if candidate is None:  # 判定与取值同读 task_mode_subject_is_candidate, 不会走到; 防御: 不当卡分支放行
            raise CandidateSubjectError("VALIDATION_SUBJECT_MISSING", f"卡 {subject['task_id']} 交付主体判为固定候选却取不出候选值")
        return report_frontmatter_contract(kind, branch_task_id=candidate["source"], subject=candidate, branch_pattern=branch_pattern,
                                           candidate=True)
    if subject["kind"] == governance_outputs_kind():
        if reviewed is None:
            return report_frontmatter_contract("return", governance=True, report_dir=_report_dir_hint(workspace_root, task_id))
        return report_frontmatter_contract("verdict", branch_task_id=reviewed or None, governance=True,
                                           subject={"outputs_digest": str(subject.get("outputs_digest") or "")})
    if reviewed is None:
        return report_frontmatter_contract("return", branch_task_id=task_id, branch_pattern=branch_pattern)
    values = forensic_worktree_subject(worktree) if worktree is not None else None
    return report_frontmatter_contract("verdict", branch_task_id=reviewed or None, subject=values, branch_pattern=branch_pattern)


def _report_dir_hint(workspace_root: Path, task_id: str) -> str:
    """本卡报告落点目录(治理根相对, 带尾 /; 落点根在治理根外 = 绝对路径)——契约提示占位 {report_dir} 的值。"""
    directory = return_artifact_dir(workspace_root, task_id)
    try:
        return directory.resolve().relative_to(Path(workspace_root).resolve()).as_posix() + "/"
    except ValueError:
        return str(directory) + "/"


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
        "worktree_exists == true(工作树由驱动方认领时建立; AIPOS-F89 件③a: 审计卡 = 被审分支 tip 的只读 detached 取证工作树; "
        "AIPOS-F178/F196/F197: worktree_required == false(判据唯一 card_needs_worktree)且 kickoff_without_worktree(固定候选卡 / 显式声明只写治理根的卡"
        "及其审计卡)= 不要求工作树, 照常入选, 开工提示按 template_governance_root / template_no_worktree 渲染; 其余不建树存量卡不入选, 拒因 "
        "WORKTREE_NOT_REQUIRED 点名治理根报告落点)",
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

    事实只读: 结案口径唯一判定 task_complexity.closure_state(AIPOS-F209) + 记录(claim 记录实例) + 推导核结论(triggered_by / derivable), 禁另判。
    返回 None(可开工) 或 {code, reason}(code ∈ KICKOFF_REFUSAL_CODES; 文案零门动词, 工位原样转述)。
    2026-10-02 事故: 审计会话被贴错卡号, 对已结案卡重审并覆盖原报告 → 已结案/产物已交一律拒。"""
    task_id = str(task_id or "").strip()
    fm = card_frontmatter if isinstance(card_frontmatter, dict) else {}

    def refuse(code: str, detail: str) -> dict[str, str]:
        return {"task_id": task_id, "code": code, "reason": f"{KICKOFF_REFUSAL_CODES[code]}: {detail}"}

    if not queue_state:
        return refuse("NOT_FOUND", f"{task_id} 不在任何队列目录")
    from tools.aipos_cli.frontmatter import FrontmatterReadError
    from tools.aipos_cli.task_complexity import closure_state

    try:
        records = _read_task_records(workspace_root, task_id)
        # AIPOS-F209: 是否结案读结案口径唯一判定 closure_state(门生 closure / 顾问复核结案 / 卡在 completed/), 不另判
        closure = closure_state(workspace_root, task_id)
    except (FrontmatterReadError, ValueError) as exc:  # 卡面 / 记录读不出, 或结论值不在声明(fail-closed, 不当作可开工)
        return refuse("FRONTMATTER_UNREADABLE", str(exc))
    if closure["concluded"]:
        return refuse("CONCLUDED", f"{task_id} queue_state={queue_state}"
                      + (f"(结案口径 {closure['kind']}{'; 结论 ' + closure['outcome'] if closure['outcome'] else ''})" if closure["kind"] else ""))
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
    field_lines = render_report_field_lines(next_card.get("report_required_frontmatter"), decl=decl)
    vr_lines = next_card.get("validation_root_lines") or []
    if not isinstance(vr_lines, list) or not all(isinstance(x, str) for x in vr_lines):
        raise KickoffRenderError(f"validation_root_lines 须为串列表: {vr_lines!r}")
    values = {
        "task_id": next_card.get("task_id"),
        "worktree_path": next_card.get("worktree_path"),
        "report_path": next_card.get("report_path"),
        "card_path": next_card.get("card_path"),
        "report_field_lines": "\n".join(field_lines),
        "refusal_clause": report_refusal_clause(),  # AIPOS-F215 件③: 拒收条款唯一声明
        "remote_material": "",
        # AIPOS-F196 件③: 本卡验证根节(章程同源); 无声明 = 空串(与 F196 前逐字节相同)
        "validation_root": ("\n\n" + "\n".join(vr_lines)) if vr_lines else "",
    }
    template = decl["template"]
    if "outputs_note" in next_card:
        # AIPOS-F197 件①: 只写治理根的执行卡 = template_governance_root
        template = decl.get("template_governance_root")
        if not isinstance(template, str) or not template:
            raise KickoffRenderError("verbs.schema.json verbs.lybra_my_tasks.kickoff.template_governance_root 未声明")
        values["outputs_note"] = next_card.get("outputs_note")
    elif "worktree_note" in next_card:
        # AIPOS-F196 件①: 不建树的固定候选卡(validation 及其审计卡)= template_no_worktree
        template = decl.get("template_no_worktree")
        if not isinstance(template, str) or not template:
            raise KickoffRenderError("verbs.schema.json verbs.lybra_my_tasks.kickoff.template_no_worktree 未声明")
        values["worktree_note"] = next_card.get("worktree_note")
    if remote is not None:
        material_template = decl.get("remote_material")
        if not isinstance(material_template, str) or not material_template:
            raise KickoffRenderError("verbs.schema.json verbs.lybra_my_tasks.kickoff.remote_material 未声明")
        if not isinstance(remote, dict):
            raise KickoffRenderError(f"remote 上下文须为对象: {remote!r}")
        values["remote_material"] = _KICKOFF_PLACEHOLDER_RE.sub(lambda m: _kickoff_value(remote, m.group(1)), material_template)
    return _KICKOFF_PLACEHOLDER_RE.sub(lambda m: _kickoff_value(values, m.group(1)), template)


def render_report_field_lines(contract: Any, *, decl: dict[str, Any] | None = None) -> list[str]:
    """报告必填字段逐项成行(行式 = verbs.schema lybra_my_tasks.kickoff.report_field_line; 工位 /go 开工提示与 AIPOS-F187 子 agent 开工稿
    同一渲染)。每项须为 {key: 非空串, hint: 串, value: 串|None}; 列表空 / 项形变 / 行式缺 = KickoffRenderError(fail-closed)。"""
    line_decl = (decl or kickoff_declaration())["report_field_line"]
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
        field_lines.append(_KICKOFF_PLACEHOLDER_RE.sub(lambda m, entry=entry: _kickoff_value(entry, m.group(1)), line_template))
    # AIPOS-F233 件⑥: 报告头 YAML 写法预防(值含冒号须加引号; 声明 transitions artifact_ingest.report_header_yaml.quote_rule)
    field_lines.append(report_header_quote_rule())
    return field_lines


def report_header_quote_rule() -> str:
    """AIPOS-F233 件⑥: 报告必填行末尾的 YAML 引号规则一行(声明 transitions artifact_ingest.report_header_yaml; 缺 = SchemaLoadError)。"""
    from tools.schema_loader import SchemaLoadError

    decl = _artifact_ingest_declaration().get("report_header_yaml")
    if not isinstance(decl, dict) or not str(decl.get("quote_rule") or "").strip() or not isinstance(decl.get("free_text_keys"), list):
        raise SchemaLoadError("transitions.schema.json artifact_ingest.report_header_yaml(quote_rule / free_text_keys)未声明(AIPOS-F233)")
    return str(decl["quote_rule"]).replace("{keys}", " / ".join(str(k) for k in decl["free_text_keys"]))


def remote_kickoff_context(workspace_root: Path, instance: str) -> dict[str, str]:
    """AIPOS-F110 件②: 跨机工位开工提示的门机材料上下文(render_kickoff remote=)。门机读不到远端工位目录, 身份 = land 事件实例:
    位置 = enrollment.workstation_location(该实例最新 land 事件, 须 transport=remote; AIPOS-F146 经 instance_enrollment, 子 agent 执行者 = 拒带出口),
    材料 = workspace_config.project_workstation
    (project.json workstations.<实例>)。不可得 = ValueError(含 WORKSTATION_MATERIAL_* 拒因码 / 位置拒因; fail-closed)。"""
    import socket

    from tools.aipos_cli.enrollment import instance_enrollment
    from tools.aipos_cli.workspace_config import project_workstation
    from tools.schema_loader import SchemaLoadError

    try:
        view = instance_enrollment(workspace_root, instance)  # AIPOS-F146: 身份与模式一处判定; 位置 = 其 workstation(workstation_location)
    except SchemaLoadError as exc:
        raise ValueError(f"工位位置声明读取失败: {exc}") from exc
    if view["governance_root_mode"]:
        raise ValueError(f"实例 {instance} 为子 agent 执行者(接入模式 {view['mode']}, 无工位), 不适用 --remote-workstation; "
                         f"出口: lybra my-tasks --actor {instance} --task-id <卡号>(不带 --workstation / --remote-workstation)")
    loc = view["workstation"]
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
    if not isinstance(value, str) or (not value and name not in ("value", "hint", "remote_material", "validation_root")):
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
        no_tree_kickoff = card.get("worktree_required") is False and card.get("kickoff_without_worktree") is True
        if card.get("worktree_required") is False and not no_tree_kickoff:
            # AIPOS-F178 件②: 存量不建树卡(未显式声明只写治理根)不入选, 拒因原样点名治理根报告落点; 判定失败同此转述
            excluded.append({"task_id": task_id, "code": str(refusal.get("code") or "WORKTREE_NOT_REQUIRED"),
                             "reason": str(refusal.get("reason") or "本卡只写治理根, 不建卡工作树")})
            continue
        if no_tree_kickoff:
            # AIPOS-F196 件① + F197 件①: 不建树卡(固定候选卡、只写治理根的卡及其审计卡; 唯一判据 card_needs_worktree)照常开工
            pass
        elif not card.get("worktree_path"):
            excluded.append({
                "task_id": task_id,
                "code": str(refusal.get("code") or "WORKTREE_UNRESOLVED"),
                "reason": f"工作树不可推导: {refusal.get('reason') or '产品未给出拒因'}; 按 block-and-report 上报",
            })
            continue
        elif card.get("worktree_exists") is not True:
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
        vr_refusal = card.get("validation_root_refusal") if isinstance(card.get("validation_root_refusal"), dict) else None
        if vr_refusal:
            excluded.append({"task_id": task_id, "code": str(vr_refusal.get("code") or "VALIDATION_ROOT_UNRESOLVED"),
                             "reason": f"{vr_refusal.get('reason')}; 按 block-and-report 上报"})
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
        if chosen.get("kickoff_without_worktree") is True and chosen.get("worktree_required") is False:
            if chosen.get("outputs_note"):
                # AIPOS-F197 件①: 只写治理根的执行卡 = template_governance_root(产物落点说明, 不提分支 / 工作树)
                next_card["outputs_note"] = str(chosen["outputs_note"])
            else:
                # AIPOS-F196 件①: 不建树的验证卡 / 审计卡: 开工提示写明不建树原因 + 本卡验证根节(card_workstation_view 给出, 章程同源)
                next_card["worktree_note"] = str((chosen.get("worktree_refusal") or {}).get("reason") or "")
        if chosen.get("validation_root_lines"):
            next_card["validation_root_lines"] = list(chosen["validation_root_lines"])
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
        if _is_placeholder_value(frontmatter.get(key)):  # AIPOS-F215: 与报告必填值同一占位判据(原此处复制一份)
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


_PASS_VERDICTS = ("PASS", "PASS_WITH_NOTES")


def _external_repo_turn_blocker(workspace_root: Path, task_id: str, fm: dict[str, Any]) -> dict[str, Any] | None:
    """AIPOS-F135 件①: external 模式同仓合入串行的唯一判定(声明 transitions N5.finalize_mode.repo_serial)。

    本卡 FINALIZE Return 未落时调用: 同一 lane 仓(仓键唯一派生 machine_zone.card_lane_key)另有「最新裁决 PASS/PASS_WITH_NOTES 且无
    finalization 记录」的 claimed 卡, 且按先后序(FINALIZE Return 已落者优先 → 裁决时间 verdict_at → 卡号)排在本卡之前 = 返回
    {blocker, lane, reason}(推导返回 await_repo_turn, 不派 FINALIZE); 无 = None。卡遍历只走 task_loader.iter_queue_task_paths;
    finalization 判据只走 finalization_record.existing_finalization_records; 审计卡与存量冻结卡不计。
    本卡 lane 不可解析 = CardRepoUnresolved 原样抛(调用方不可推导, 无法判同仓即不派)。他卡卡面读不出 / lane 不可解析 = 不计入并在
    reason 点名(其自身推导已硬停, finalize 同一仓判据亦拒)。"""
    from tools.aipos_cli.audit_derivation import is_audit_card
    from tools.aipos_cli.finalization_record import existing_finalization_records
    from tools.aipos_cli.frontmatter import FrontmatterReadError
    from tools.aipos_cli.legacy_baseline import frozen_tasks
    from tools.aipos_cli.machine_zone import card_lane_key
    from tools.aipos_cli.record_writer import record_file_prefix
    from tools.aipos_cli.task_loader import iter_queue_task_paths
    from tools.aipos_cli.workspace_config import CardRepoUnresolved

    root = Path(workspace_root)
    lane = card_lane_key(fm, root)

    def _latest_verdict(card_id: str) -> dict[str, Any] | None:
        return _find_latest_record(record_dir(root, "audit_verdicts", card_id), record_file_prefix("audit_verdicts"))

    def _verdict_time(verdict: dict[str, Any]) -> str:
        # 裁决时间同一口径(YAML 可能解析为 datetime 或字符串): 规范为 UTC ISO 秒级; 读不出 = 原文(排序退到卡号)
        raw = verdict.get("verdict_at")
        try:
            moment = raw if isinstance(raw, datetime) else datetime.fromisoformat(str(raw or "").strip().replace("Z", "+00:00"))
        except ValueError:
            return str(raw or "")
        moment = moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    own = _latest_verdict(task_id) or {}
    own_key = (1, _verdict_time(own), task_id)
    frozen = frozen_tasks(root)
    skipped: list[str] = []
    ahead: list[tuple[tuple[int, str, str], str]] = []
    for path in iter_queue_task_paths(root, states=("claimed",)):
        try:
            other = _read_frontmatter(path)
        except FrontmatterReadError as exc:
            skipped.append(f"{path.name}(卡面读不出: {exc})")
            continue
        other_id = str(other.get("task_id") or "").strip()
        if not other_id or other_id == task_id or other_id.upper() in frozen or is_audit_card(other_id, other):
            continue
        verdict = _latest_verdict(other_id)
        if not verdict or str(verdict.get("verdict") or "") not in _PASS_VERDICTS or existing_finalization_records(root, other_id):
            continue
        try:
            other_lane = card_lane_key(other, root)
        except (CardRepoUnresolved, OSError, ValueError) as exc:
            skipped.append(f"{other_id}(lane 不可解析: {exc})")
            continue
        if other_lane != lane:
            continue
        returned = find_return_artifact(root, finalize_task_id_for(other_id, other)) is not None
        key = (0 if returned else 1, _verdict_time(verdict), other_id)
        if key < own_key:
            ahead.append((key, other_id))
    if not ahead:
        return None
    key, blocker = min(ahead)
    why = "其 FINALIZE Return 已落(合入在外部进行/已完成)" if key[0] == 0 else f"其裁决({key[1] or '无时间'})早于本卡({own_key[1] or '无时间'})"
    reason = (f"同仓 lane {lane} 的卡 {blocker} 裁决已 PASS、finalization 记录未落, {why}, 先合入; "
              f"本卡 {task_id} 待其 finalization 记录落盘后再派 FINALIZE(同仓同时只合一张, AIPOS-F135 件①)")
    if skipped:
        reason += f"; 未计入: {', '.join(skipped)}"
    return {"blocker": blocker, "lane": lane, "reason": reason, "ahead": [card for _k, card in sorted(ahead)]}


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
        # AIPOS-F135 件①: 同仓合入串行——同 lane 仓另有先合入的 PASS 待合入卡 = await_repo_turn, 不派 FINALIZE
        from tools.aipos_cli.workspace_config import CardRepoUnresolved

        try:
            turn = _external_repo_turn_blocker(workspace_root, task_id, fm)
        except CardRepoUnresolved as exc:
            return {
                **base,
                "derivable": False,
                "triggered_by": "advisor",
                "missing_records": [f"卡 {task_id} lane 仓不可解析, 无法判同仓合入串行: {exc}"],
                "suggested_action": exc.reason,
                "notes": f"N4→N5(external): 裁决 {verdict_result}, 但 lane 仓不可解析({exc.code}), 不派 FINALIZE(fail-closed)",
            }
        if turn is not None:
            return {
                **base,
                "derivable": False,
                "triggered_by": "none",
                "missing_records": [f"同仓先合入卡 {turn['blocker']} 的 finalization 记录"],
                "suggested_action": f"等同仓卡 {turn['blocker']} 合入完成(其 finalization 记录落盘)后本卡再派 FINALIZE {fin_id}",
                "notes": f"N4→N5(external): 裁决 {verdict_result}; {turn['reason']}",
                "repo_turn": turn,
                "action": {"type": "await_repo_turn", "card": turn["blocker"], "lane": turn["lane"]},
            }
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
    """驱动方 token 绑定的实例(connection.json 中 role_class==roles.schema driver.role_class 的条目)。

    AIPOS-F134 件①: loop --actor 在 scope 内 = 只认该实例自己的条目(two_phase_shell_factory.driver_token_entry → token_resolver
    .select_token_entry(agent_instance=…), 无即 "" 并出声, 禁回落首个); 无 scope 时连接文件绑定 ≥2 个驱动方实例 = ""(多顾问实例不取首个,
    调用方 fail-closed 要求显式 --actor); 否则既有行为(首个驱动方角色类条目的实例)。"""
    import json
    import sys

    from tools.aipos_cli.two_phase_shell_factory import driver_role_class, driver_token_entry, driver_token_instances

    conn = connection_json or _find_connection_json(workspace_root)
    if not conn or not Path(conn).is_file():
        return ""
    scoped_actor = _scoped_driver().get("actor")
    try:
        if scoped_actor:
            return str(driver_token_entry(connection_json_path=conn, agent_instance=scoped_actor).get("agent_instance") or "").strip()
        bound = driver_token_instances(connection_json_path=conn)
    except ValueError as exc:  # DriverTokenError / 连接文件读不出: 出声, 按「解析不到」交调用方 fail-closed
        print(f"Warning: {conn}: driver instance unresolved: {exc}", file=sys.stderr)
        return ""
    if len(bound) > 1:
        print(f"Warning: {conn} 绑定了多个驱动方实例 {bound}, 不取首个; 须显式 lybra loop --actor <实例>", file=sys.stderr)
        return ""
    try:
        tokens = json.loads(Path(conn).read_text(encoding="utf-8")).get("tokens") or []
    except (OSError, ValueError) as exc:
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


def _bound_driver_instances(workspace_root: Path, connection_json: str | None = None) -> list[str]:
    """AIPOS-F134: 连接文件里可用驱动方条目绑定的实例(two_phase_shell_factory.driver_token_instances 唯一判定); 无连接文件/读不出 = []。"""
    from tools.aipos_cli.two_phase_shell_factory import driver_token_instances

    conn = connection_json or _find_connection_json(workspace_root)
    if not conn or not Path(conn).is_file():
        return []
    try:
        return driver_token_instances(connection_json_path=conn)
    except ValueError:
        return []


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
    # AIPOS-F134 件①②: 连接文件绑定 ≥2 个驱动方实例(同一治理根多顾问)= 驱动方不定——.lybra/role 顶层只是最近接入的实例,
    # 不能代表本次驱动方, 禁取顶层/首个冒名 → 调用方显式 fallback(靶场/申领提示) 或 ""(调用方 fail-closed: 须显式 --actor)
    if len(_bound_driver_instances(workspace_root, connection_json)) > 1:
        return str(fallback or "")
    # AIPOS-F106 件④: 治理根 .lybra/role 只经 ConnectionResolver.resolve_identity 读(唯一实现; env 不参与 = 只认工位声明层)
    from tools.loop_context import ConnectionResolver

    declared = ConnectionResolver.resolve_identity(workspace_root=workspace_root, env={})["agent_instance"]
    if declared["source"] == ".lybra/role" and str(declared["value"] or "").strip():
        return str(declared["value"]).strip()
    instance = _driver_token_instance(workspace_root, connection_json)
    if instance:
        return instance
    return str(fallback or "")


DRIVER_ACTOR_MISSING = "驱动方身份(治理根 .lybra/role 的 instance, 或 connection.json 驱动方 token 的 agent_instance; 同一治理根多顾问实例时须显式 --actor)"


def _driver_role_name(workspace_root: Path, connection_json: str | None = None) -> str:
    """AIPOS-F78B 件③: 驱动方的角色名(自定义角色如 chris 的 hbj-advisor)——工位声明 .lybra/role 的 role, 其次 connection.json
    驱动方 token(role_class==driver.role_class)的 role; 解析不到返回 ""(调用方回退角色类 advisor)。信封 agent_or_role 可写角色名。

    AIPOS-F134 件①: loop --actor 在 scope 内 = 该实例自己凭据条目的 role(driver_token_entry, 与门侧按请求 token 取角色名同口径);
    该实例无凭据 = ""(禁回落首个驱动方或另一实例的角色; loop 启动已按 driver_token_refusal 拒)。未给 --actor 时 .lybra/role 读顶层
    (ConnectionResolver.resolve_role, 顶层 = 最近接入实例; 多顾问实例时驱动方身份以 --actor + 其凭据条目为准); 给了 --actor 而无连接
    文件时按实例经分槽读口 enroll_client.read_role_record 取(AIPOS-F140)。"""
    import json

    from tools.aipos_cli.two_phase_shell_factory import driver_role_class, driver_token_entry
    from tools.loop_context import ConnectionResolver

    conn = connection_json or _find_connection_json(workspace_root)
    scoped_actor = _scoped_driver().get("actor")
    if scoped_actor:
        if conn and Path(conn).is_file():
            try:
                return str(driver_token_entry(connection_json_path=conn, agent_instance=scoped_actor).get("role") or "").strip()
            except ValueError:
                return ""
        # 无连接文件(靶场): 治理根 .lybra/role 中该实例的记录(AIPOS-F140 件②: 唯一分槽读口 read_role_record——顶层或
        # instances.<实例> 槽)的 role; 未登记该实例 = ""(不借别的实例); 文件坏 = RoleRecordError 原样抛(fail-closed)
        from tools.aipos_cli.enroll_client import RoleInstanceNotRegistered, read_role_record

        lybra_dir = ConnectionResolver.discover_lybra_dir(Path(workspace_root))
        try:
            record = read_role_record(lybra_dir, scoped_actor) if lybra_dir is not None else None
        except RoleInstanceNotRegistered:
            return ""
        return str((record or {}).get("role") or "").strip()
    if len(_bound_driver_instances(workspace_root, conn)) > 1:
        return ""  # AIPOS-F134: 多驱动方实例未指明 = 不取 role 顶层(最近接入者)或首个驱动方的角色
    # AIPOS-F106 件④: 治理根 .lybra/role 只经 ConnectionResolver.resolve_role 读(唯一实现; env={} = 只认工位声明层)
    role = str(ConnectionResolver.resolve_role(workspace_root=workspace_root, env={}) or "").strip()
    if role:
        return role
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


def driver_token_refusal(workspace_root: Path, actor: str, connection_json: str | None = None) -> str:
    """AIPOS-F134 件①: loop 启动前核驱动方实例有自己的凭据——连接文件在而该实例无可用驱动方条目 = 拒因原文(禁回落首个);
    连接文件不在 = ""(派生命令不带凭据文件, 既有行为)。判定唯一实现 two_phase_shell_factory.driver_token_entry。"""
    from tools.aipos_cli.two_phase_shell_factory import driver_token_entry

    conn = connection_json or _find_connection_json(workspace_root)
    if not conn or not Path(conn).is_file():
        return ""
    try:
        driver_token_entry(connection_json_path=conn, agent_instance=actor)
    except ValueError as exc:
        return str(exc)
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


def _evidence_declaration_invalid(task_id: str, evidence: dict[str, Any], *, node: str, path: str) -> dict[str, Any]:
    """AIPOS-F233 件③: 审计卡生效的运行证据声明本身有问题(artifact_ingest.evidence_declaration_problem 唯一判据)→ 不可推导,
    action.type = transitions runtime_evidence.loop.declaration_invalid_action(triggered_by=advisor; end_reason 读声明), loop 不拉起 / 不再拉起
    审计体, 立即以该结束原因结束(advisor_fix: 顾问受限 amend 修正声明后续跑)。"""
    from tools.aipos_cli.artifact_ingest import runtime_evidence_declaration

    loop = runtime_evidence_declaration()["loop"]
    reasons = [r for r in evidence.get("reasons") or [] if r]
    return {
        "task_id": task_id,
        "derivable": False,
        "current_node": node,
        "current_state": "claimed",
        "triggered_by": "advisor",
        "command": "",
        "verb": "lybra_audit_verdict_dry_run",
        "missing_records": reasons,
        "suggested_action": f"卡面运行证据声明本身有问题({evidence.get('category')}), 顾问修正声明(不拉起审计体): " + "; ".join(reasons),
        "notes": "N3/N4: 运行证据声明渲染不了 = 声明错归顾问(AIPOS-F233 件③), 不按 relaunch_cap 让审计体补做",
        "action": {"type": str(loop["declaration_invalid_action"]), "card": task_id, "path": path, "category": evidence.get("category"),
                   "end_reason": str(loop["declaration_invalid_end_reason"])},
    }


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
    # AIPOS-F162 件②: 补审裁决(记录带 supplementary_of)不是本卡审计链的裁决——latest_verdict 只认常规审计裁决(原裁决语义不变),
    # 补审裁决另列 latest_supplementary_verdict
    from tools.aipos_cli.record_writer import record_files_with_prefix

    result["latest_supplementary_verdict"] = None
    filled: set[str] = set()
    for vfile in sorted(record_files_with_prefix(verdicts_dir, record_file_prefix("audit_verdicts")),
                        key=lambda p: p.stat().st_mtime, reverse=True):
        vfm = _read_frontmatter(vfile) or None
        slot = "latest_supplementary_verdict" if vfm and str(vfm.get("supplementary_of") or "").strip() else "latest_verdict"
        if slot not in filled:
            filled.add(slot)
            result[slot] = vfm
        if len(filled) == 2:
            break

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
    """执行体产物就绪判据(唯一): 路径读 transitions N2.artifact.location; 存在且摘要可取
    (AIPOS-F148 件②: 摘要来源读声明 transitions artifact_ingest.return.summary_source + 项目覆盖, 缺省 =「一句话结论」节非
    `(待填写` 占位)才算。骨架由 claim 创建(N1.return_skeleton), 不是交回。"""
    path = _return_artifact_path(workspace_root, task_id)
    if not path.is_file():
        return False
    return _extract_return_summary(workspace_root, task_id) is not None


def return_summary_status(workspace_root: Path, task_id: str) -> dict[str, Any]:
    """AIPOS-F148 件②: 执行体 Return 的摘要就绪视图(推导核「不就绪说清缺什么」与 artifact ingest 拒因同源)。

    返回 {path, exists, summary, source, submitted, missing, error}:
      summary   = extract_return_summary_text(内容, 本项目摘要来源)(None = 取不到);
      submitted = 必填 frontmatter(required_frontmatter)均已填实值(= 执行体已自认交回, 骨架不算);
      missing   = 摘要取不到时的原文(所查 frontmatter 键 + 正文节标记 + 补法); error = 摘要来源声明不合 / 文件读不出的原文。"""
    from tools.aipos_cli.workspace_config import ReturnSummarySourceError, return_summary_source

    path = _return_artifact_path(workspace_root, task_id)
    out: dict[str, Any] = {"path": path, "exists": path.is_file(), "summary": None, "source": None, "submitted": False,
                           "missing": "", "error": ""}
    if not out["exists"]:
        return out
    try:
        source = return_summary_source(workspace_root)
    except (ReturnSummarySourceError, OSError, ValueError) as exc:
        out["error"] = (f"本项目 Return 摘要来源声明不可用(fail-closed): {exc}。出口: lybra project set-return-summary "
                        "--frontmatter-key <键> / --section-marker <标记> 重新声明(config.schema project_json.return_summary_source)")
        return out
    out["source"] = source
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        out["error"] = f"{path} 读不出: {exc}"
        return out
    out["summary"] = extract_return_summary_text(content, source)
    if out["summary"] is None:
        out["missing"] = return_summary_missing_text(path, source)
        from tools.aipos_cli.frontmatter import FrontmatterReadError

        try:
            out["submitted"] = not missing_return_frontmatter(_read_frontmatter(path, allow_missing_block=True),
                                                              governance=card_report_is_governance(workspace_root, task_id))
        except FrontmatterReadError:
            out["submitted"] = False  # frontmatter 读不出 = 不能断定已交(推导核交回步另按 F100 件② 判 frontmatter_unreadable)
    return out


def return_summary_missing_text(path: Path, source: dict[str, Any]) -> str:
    """AIPOS-F148 件②: 摘要取不到时的拒因原文(推导核 missing_records 与 artifact ingest INGEST_SUMMARY_MISSING 同一文案)。"""
    keys, markers = list(source.get("frontmatter_keys") or []), list(source.get("section_markers") or [])
    parts = []
    if keys:
        parts.append(f"frontmatter 键 {keys} 均缺或为占位")
    if markers:
        parts.append(f"正文无含 {markers} 任一标记的摘要节(或其内容仍为 `{_skeleton_prefix()}` 骨架占位)")
    how = []
    if keys:
        how.append(f"frontmatter 写 {keys[0]}: <一句话结论>")
    if markers:
        how.append(f"正文加「## {markers[0].lstrip('#').strip()}」节并在其下写一句话结论")
    return (f"{path}: Return 摘要缺——" + "; ".join(parts) + "(摘要来源声明: transitions.schema artifact_ingest.return.summary_source"
            + (" + 本项目 project.json return_summary_source" if source.get("declared") else "") + ")。补法(任一): " + " / ".join(how))


def _check_verdict_artifact(workspace_root: Path, task_id: str, card_frontmatter: dict[str, Any] | None = None) -> Path | None:
    """审计体产物就绪判据(唯一): 落点根读项目声明(verdict_root), 文件候选读 transitions artifact_ingest.verdict
    (RETURN.md 优先, 回退 audit_report.md), 取首个「已交」的文件(AIPOS-F89 件③c: verdict_report_submitted = 声明必填键
    至少一个已填实值; 认领时生成的同名同位空模板全为占位 = 未交); 完成与否另由 missing_verdict_frontmatter 判(推导核对已交未完成
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
        if verdict_report_submitted(_read_frontmatter(cand, allow_missing_block=True),
                                    governance=card_report_is_governance(workspace_root, task_id, card_frontmatter)):
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
    """从 Return 提取一句话结论(推导核交回步的 result_summary)。

    第4轮③: result_summary 必须从 Return 摘要来源提取,不存在→该步不该是return。
    AIPOS-F73D: 占位符(骨架 `(待填写`)不算结论 → None(= 产物未就绪)。路径读 N2.artifact.location。
    AIPOS-F148 件②: 摘要来源读声明(return_summary_status 唯一视图); 来源声明不合 / 文件读不出 = None 并出声(推导核另判 artifact_invalid)。
    """
    status = return_summary_status(workspace_root, task_id)
    if status["error"]:
        import sys

        print(f"Warning: {task_id}: {status['error']}", file=sys.stderr)
    return status["summary"]


def extract_return_summary_text(content: str, source: dict[str, Any] | None = None) -> str | None:
    """从 Return 提取一句话结论(唯一解析; 推导核就绪判据 / 交回步 / artifact ingest / 审计报告 findings_summary 共用)。骨架占位 → None。

    AIPOS-F148 件②: source = 摘要来源 {frontmatter_keys, section_markers}(项目的经 workspace_config.return_summary_source 取);
    缺省 None = transitions artifact_ingest.return.summary_source 缺省(=「一句话结论」节, 与 F148 前写死判据逐字等价)。
    序: frontmatter_keys 按列序取首个非占位值 → section_markers 正文节首个内容行。"""
    if source is None:
        from tools.aipos_cli.workspace_config import return_summary_source_default

        source = return_summary_source_default()
    keys = [str(k) for k in (source.get("frontmatter_keys") or [])]
    if keys:
        from tools.aipos_cli.frontmatter import parse_markdown_frontmatter

        meta, _body, _warnings = parse_markdown_frontmatter(content)
        for key in keys:
            value = (meta or {}).get(key)
            if isinstance(value, str) and not _is_placeholder_value(value):
                return value.strip().rstrip("。")
    markers = [str(m) for m in (source.get("section_markers") or [])]
    if not markers:
        return None
    lines = content.split("\n")
    in_summary_section = False
    for line in lines:
        if any(marker in line for marker in markers):
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
                if summary.startswith(_skeleton_prefix()):
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
) -> dict[str, str] | None:
    """AIPOS-F73前置①: 从卡分支 tip 提取 artifact_subject (repository/commit_sha/tree_hash)。

    AIPOS-F211 件①: 原 task_mode 参数(task_mode != "code" = None, 第二判据)删除——是否有卡分支由调用方先问唯一判据
    card_writes_product_repo, 本函数只读 tip。

    Args:
        workspace_root: 产品仓根目录
        reviewed_task_id: 被审任务 ID

    Returns:
        artifact_subject dict 或 None（无分支 / 提取失败）
    """
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
    # AIPOS-F178: card branch exists iff the card writes a product repo (single criterion card_writes_product_repo;
    # AIPOS-F211: card without task_mode = schema missing_default via _task_mode_entry, no second fallback here)
    if not card_writes_product_repo(workspace_root, {**fm, "task_id": task_id}):
        return None
    from tools.aipos_cli.workspace_config import CardRepoUnresolved

    try:
        code_repo = _card_repo_root(workspace_root, task_id, fm, allow_governance_root=False)
    except CardRepoUnresolved:
        return None  # 产品仓不可解析: 交给既有 finalize 派生点出声(不可推导点名), 本判据不另判
    subject = _extract_artifact_subject_from_branch(code_repo, task_id)
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


def uncleared_rework_rounds(card_frontmatter: dict[str, Any] | None) -> list[dict[str, Any]]:
    """AIPOS-F75 件③ 返工识别唯一实现: 卡面 rework_rounds(只经 queue rework 门动词追加)中未销账(无 cleared_at)的轮次, 按登记序。
    推导核「claimed 且无交回」分支(F75 原逻辑)与 AIPOS-F155 件② N4 非 PASS 裁决分支(rework_round_for_verdict)同读, 禁第二份。"""
    rounds = (card_frontmatter or {}).get("rework_rounds") if isinstance(card_frontmatter, dict) else None
    if not isinstance(rounds, list):
        return []
    return [r for r in rounds if isinstance(r, dict) and not r.get("cleared_at")]


def rework_round_for_verdict(card_frontmatter: dict[str, Any] | None, verdict: dict[str, Any] | None) -> dict[str, Any] | None:
    """AIPOS-F155 件②(gap #135): 针对该门生裁决登记的未销账返工轮(最新一轮; 识别 = uncleared_rework_rounds 同一实现)。
    「针对」= 返工节 verdict_ref 指向该裁决(含其 verdict_id 或审计卡号, 门 queue rework 不校验写法, 两种写法都认)或登记时刻不早于裁决时刻
    (verdict_at; 轮次销账字段 cleared_at 尚无写入方, 故用时刻区分上一轮 FAIL 的旧返工节)。无 = None。"""
    if not isinstance(verdict, dict):
        return None
    verdict_id = str(verdict.get("verdict_id") or "").strip()
    audit_id = str(verdict.get("audit_task_id") or "").strip()
    verdict_at = str(verdict.get("verdict_at") or "").strip()
    for rnd in reversed(uncleared_rework_rounds(card_frontmatter)):
        ref = str(rnd.get("verdict_ref") or "").strip()
        if verdict_id and verdict_id in ref:
            return rnd
        if audit_id and ref and re.search(rf"(?<![A-Za-z0-9_-]){re.escape(audit_id)}(?![A-Za-z0-9])", ref):
            return rnd
        created = str(rnd.get("created_at") or "").strip()
        if verdict_at and created and _moment_not_before(created, verdict_at):
            return rnd
    return None


def _moment_not_before(left: str, right: str) -> bool:
    """AIPOS-F232 件⑤: 两个 ISO 时刻 left ≥ right(解析比较: 毫秒级 `…49.200Z` 与秒级存量 `…49Z` 混比按时刻, 秒级视同 .000);
    任一解析不了 = 回落原字符串比较(存量口径不变)。"""
    from tools.aipos_cli.owner_inbox import _parse_utc

    a, b = _parse_utc(left), _parse_utc(right)
    return a >= b if a is not None and b is not None else left >= right


def verdict_staleness(workspace_root: Path, task_id: str, card_frontmatter: dict[str, Any] | None) -> dict[str, Any] | None:
    """AIPOS-F112 件①: 「裁决已过期」判据唯一实现(推导核 audit_verdict 判定前 / 门侧交回自动派审 同读, 禁第二份)。

    比较对象与 finalize 的 F70 核对同源, 推导核与 finalize 不会各说各话:
      - 卡分支 tip: 分支名 = card_branch_name(声明), 读取口 = _extract_artifact_subject_from_branch(裁决提交绑 tip 的同一函数);
      - 最新门生裁决: deployment_authorization.find_gate_pass_verdict_for_task(required_commit_sha=tip)(finalize 同一判定)。
    过期 = 最新门生裁决属 PASS 族(N5.guards.has_pass_verdict 声明)且带 artifact_subject, 但其 commit_sha ≠ 卡分支 tip。
    不过期(None): 非代码卡 / 产品仓不可解析 / 无卡分支 / 卡分支已并入 main(finalize 不再要求精确覆盖)/ legacy 裁决 / 非 PASS 族 / 已覆盖。
    返回 {verdict_id, verdict, verdict_at, verdict_commit_sha, audit_task_id, branch, tip, tree_hash, reason}。
    AIPOS-F218 件①: 交付主体非卡分支的卡(non_branch_rework_subject: 固定候选 / 治理根产出清单, F210 唯一判定)无卡分支, 改按
    _non_branch_verdict_staleness 判(非 PASS 裁决 + 已登记返工轮 + 晚于裁决的新交回 = 过期, 声明 transitions nodes.N4.non_branch_rework)。
    AIPOS-F219 件①: 「晚于裁决」一律 = 晚于 rework_return_bound 时间界(max(裁决时刻, 该裁决最新返工轮 created_at)); 卡分支返工另须 tip 提交时刻
    不早于该界(同一裁决第 2 轮登记后, 第 1 轮期间的分支提交不算)。"""
    non_branch = non_branch_rework_subject(workspace_root, task_id, card_frontmatter)
    if non_branch is not None:
        return _non_branch_verdict_staleness(workspace_root, task_id, card_frontmatter, non_branch)
    live = _live_card_branch(workspace_root, task_id, card_frontmatter)
    if live is None:
        return None
    _code_repo, subject, branch = live
    from tools.aipos_cli.deployment_authorization import find_gate_pass_verdict_for_task

    check = find_gate_pass_verdict_for_task(task_id, Path(workspace_root), required_commit_sha=subject["commit_sha"])
    allowed = [str(v) for v in (_transition_node("N5").get("guards", {}).get("has_pass_verdict", {}).get("allowed_verdict_values") or [])]
    covered = check.get("artifact_subject") if isinstance(check.get("artifact_subject"), dict) else None
    if check.get("found") or covered is None:
        return None
    verdict_file = str(check.get("verdict_file") or "")
    verdict_fm = _read_frontmatter(Path(verdict_file)) if verdict_file else {}
    rework = None
    if str(check.get("verdict") or "") not in allowed:
        # AIPOS-F155 件②(gap #135): 非 PASS 裁决本身不「过期」; 但已针对它登记返工轮(F75 返工识别同一实现)且卡分支已前进(返工修复提交)
        # = 该裁决审的产物已被返工取代, 与 PASS 过期同一出口(重交回 → 门侧按卡号演进派下一轮审计)
        rework = rework_round_for_verdict(card_frontmatter, {**verdict_fm, "verdict_id": check.get("verdict_id"),
                                                             "verdict_at": check.get("verdict_at")})
        if rework is None or str(covered.get("commit_sha") or "").strip().lower() == str(subject["commit_sha"]).lower():
            return None
        # AIPOS-F219 件①: 分支前进须晚于「该裁决最新返工轮登记」(rework_return_bound 唯一时间界)——同一裁决第 2 轮登记后, 第 1 轮期间的
        # 分支提交不算第 2 轮返工; tip 提交时刻读不出 = 不算(fail-closed, 等新提交)
        bound = rework_return_bound(Path(verdict_file), {**verdict_fm, "verdict_at": verdict_fm.get("verdict_at") or check.get("verdict_at")},
                                    rework)
        committed = _commit_moment(_code_repo, str(subject["commit_sha"]))
        if committed is None or committed < bound["at"].replace(microsecond=0):
            return None
    return {
        "rework_round": rework,
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


_REWORK_BOUND_TEXTS = ("registered", "fallback", "earlier_ignored")


def rework_return_bound_declaration() -> dict[str, Any]:
    """AIPOS-F219 件①: transitions nodes.N4.rework_return_bound(唯一读取口); 措辞缺 = SchemaLoadError(fail-closed)。"""
    from tools.schema_loader import SchemaLoadError

    decl = _transition_node("N4").get("rework_return_bound")
    texts = decl.get("texts") if isinstance(decl, dict) else None
    if not isinstance(texts, dict) or not all(isinstance(texts.get(k), str) and texts[k].strip() for k in _REWORK_BOUND_TEXTS):
        raise SchemaLoadError(f"transitions.schema.json nodes.N4.rework_return_bound.texts.{list(_REWORK_BOUND_TEXTS)} 未声明齐(AIPOS-F219)")
    return decl


def rework_return_bound(verdict_path: Path, verdict_fm: dict[str, Any], rework: dict[str, Any] | None) -> dict[str, Any]:
    """AIPOS-F219 件①: 返工重交回时间界唯一实现(声明 transitions nodes.N4.rework_return_bound)= max(裁决时刻, 该裁决最新返工轮 created_at)。
    裁决时刻 = _verdict_moment(verdict_at; 读不出 = 裁决记录 mtime); rework = rework_round_for_verdict 返回的那一轮(None = 仅裁决时刻, F218 口径);
    该轮 created_at 缺 / 读不出 = 回落裁决时刻(fallback=True, 等待提示点名)。non_branch_re_return(门交回记录 / 报告文件两路)、
    verdict_staleness 卡分支分支(tip 提交时刻)、等待提示(_derive_rework_wait)同读, 禁第二份。
    返回 {at, verdict_moment, round, round_at(datetime|None), raw(created_at 原值), fallback}。"""
    from tools.aipos_cli.owner_inbox import _parse_utc

    moment = _verdict_moment(Path(verdict_path), verdict_fm)
    raw = str((rework or {}).get("created_at") or "").strip()
    round_at = _parse_utc(raw)
    return {"at": max(moment, round_at) if round_at is not None else moment, "verdict_moment": moment,
            "round": (rework or {}).get("round"), "round_at": round_at, "raw": raw,
            "fallback": rework is not None and round_at is None}


def rework_bound_note(bound: dict[str, Any]) -> str:
    """AIPOS-F219 件①: 等待提示里的时间界一句(texts.registered / texts.fallback)。"""
    texts = rework_return_bound_declaration()["texts"]
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    if bound.get("fallback"):
        return texts["fallback"].format(round=bound.get("round") or "?", raw=bound.get("raw") or "",
                                        verdict_at=bound["verdict_moment"].strftime(fmt))
    return texts["registered"].format(round=bound.get("round") or "?", round_at=bound["at"].strftime(fmt))


def _commit_moment(code_repo: Path, sha: str) -> datetime | None:
    """AIPOS-F219 件①: 提交的 committer 时刻(UTC, 秒); git 读不出 = None(调用方 fail-closed: 不算返工提交)并出声。"""
    import subprocess
    import sys

    try:
        out = subprocess.run(["git", "-C", str(code_repo), "show", "-s", "--format=%ct", sha], capture_output=True, text=True,
                             check=True, timeout=30).stdout.strip()
        return datetime.fromtimestamp(int(out), tz=timezone.utc)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        print(f"Warning: 卡分支提交 {sha[:12]} 提交时刻读不出(不算返工提交, AIPOS-F219): {exc}", file=sys.stderr)
        return None


_NON_BRANCH_COMMON_TEXTS = ("await_progress", "criteria", "evidence_record", "evidence_file", "re_return_note", "without_round_note",
                            "without_round_suggested", "reviewed_what")
_NON_BRANCH_SUBJECT_TEXTS = ("await_missing", "await_suggested", "stale_head", "invalid_fix", "not_updated_note", "re_return_suggested",
                             "reaudit_ref", "reaudit_note")


def non_branch_rework_declaration() -> dict[str, Any]:
    """AIPOS-F218 件①: transitions nodes.N4.non_branch_rework(唯一读取口)。by_subject 键须 ∈ 交付主体三态且不含 branch;
    缺键 / 措辞缺 = SchemaLoadError(fail-closed)。"""
    from tools.schema_loader import SchemaLoadError

    decl = _transition_node("N4").get("non_branch_rework")
    texts = decl.get("texts") if isinstance(decl, dict) else None
    by_subject = decl.get("by_subject") if isinstance(decl, dict) else None
    kinds = set(delivery_subject_kinds())
    if (not isinstance(decl, dict) or not str(decl.get("return_fingerprint_key") or "").strip() or not isinstance(texts, dict)
            or not all(isinstance(texts.get(k), str) and texts[k].strip() for k in _NON_BRANCH_COMMON_TEXTS)
            or not isinstance(by_subject, dict) or not by_subject or "branch" in by_subject or not set(by_subject) <= kinds
            or not all(isinstance(v, dict) and all(isinstance(v.get(k), str) and v[k].strip() for k in _NON_BRANCH_SUBJECT_TEXTS)
                       for v in by_subject.values())):
        raise SchemaLoadError("transitions.schema.json nodes.N4.non_branch_rework(return_fingerprint_key / texts."
                              f"{list(_NON_BRANCH_COMMON_TEXTS)} / by_subject.<交付主体非 branch>.{list(_NON_BRANCH_SUBJECT_TEXTS)})未声明齐(AIPOS-F218)")
    return decl


def non_branch_rework_subject(workspace_root: Path, task_id: str, card_frontmatter: dict[str, Any] | None) -> dict[str, Any] | None:
    """AIPOS-F218 件①: 本卡交付主体(card_delivery_subject, F210 唯一判定)在 non_branch_rework.by_subject 内 = 该主体视图
    (含 kind, candidate 另带 candidate); 卡分支态 / 未声明主体 = None(返工判据走卡分支原逻辑, 行为不变)。"""
    subject = card_delivery_subject(workspace_root, task_id, card_frontmatter)
    return subject if str(subject.get("kind") or "") in non_branch_rework_declaration()["by_subject"] else None


def _file_sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _return_record_binding(workspace_root: Path, task_id: str, return_id: str) -> dict[str, Any]:
    """门交回记录的 artifact_subject 原样(任一交付主体; 记录不在 / 无绑定 = {})。读不出 = FrontmatterReadError 上抛(AIPOS-F100)。"""
    path = record_dir(workspace_root, "returns", task_id) / f"{str(return_id or '').strip()}.md"
    if not str(return_id or "").strip() or not path.is_file():
        return {}
    subject = _read_frontmatter(path).get("artifact_subject")
    return dict(subject) if isinstance(subject, dict) else {}


def non_branch_re_return(workspace_root: Path, task_id: str, card_frontmatter: dict[str, Any] | None, verdict_path: Path,
                         verdict_fm: dict[str, Any], rework: dict[str, Any] | None = None) -> dict[str, str] | None:
    """AIPOS-F218 件①: 交付主体非卡分支的卡(固定候选 / 治理根产出清单)「已按返工重写交回」判据唯一实现(声明 transitions
    nodes.N4.non_branch_rework)——晚于该裁决(verdict_at; 读不出 = 裁决记录 mtime, _verdict_moment 同口径)的新交回产物:
      - record: 门交回记录 return_id ≠ 裁决 reviewed_return_record_ref 且 returned_at 晚于裁决(门已落新交回);
      - file: 交回报告文件(find_return_artifact)晚于裁决改写且指纹不同于被裁决那份——被裁决那份交回记录绑定了报告指纹 = 报告指纹不同
        (治理根产出清单另: 此刻清单 outputs_digest 不同于绑定也算)且修改时刻(按秒)不早于裁决; 存量记录无报告指纹 = 清单指纹不同或报告修改时刻
        (按秒)严格晚于裁决(同一秒不算, 防把被裁决那份误当新交回)。不校验 commit_sha / 卡分支(契约由产物入口按主体校验)。
    AIPOS-F219 件①: 上文「裁决」时刻一律 = rework_return_bound 时间界(max(裁决时刻, 该裁决最新返工轮 rework 的 created_at)); rework=None =
    仅裁决时刻(F218 口径, 等待提示据此点名「晚于裁决但早于该轮登记」的旧改写)。
    返回 {source, at, ref, sha256?}; 无 = None。"""
    from tools.aipos_cli.owner_inbox import _parse_utc

    moment = rework_return_bound(Path(verdict_path), verdict_fm, rework)["at"]
    reviewed = str(verdict_fm.get("reviewed_return_record_ref") or "").strip()
    latest = _read_task_records(workspace_root, task_id).get("latest_return") or {}
    latest_id = str(latest.get("return_id") or "").strip()
    returned = _parse_utc(latest.get("returned_at"))
    if latest_id and latest_id != reviewed and returned is not None and returned > moment:
        return {"source": "record", "ref": latest_id, "at": returned.strftime("%Y-%m-%dT%H:%M:%SZ")}
    path = find_return_artifact(workspace_root, task_id)
    if path is None:
        return None
    modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).replace(microsecond=0)
    moment = moment.replace(microsecond=0)
    if modified < moment:
        return None  # 裁决前的报告 = 未在裁决后重写
    bound = _return_record_binding(workspace_root, task_id, reviewed)
    bound_report = str(bound.get(str(non_branch_rework_declaration()["return_fingerprint_key"])) or "")
    digest = _file_sha256(path)
    outputs_changed = False
    if str(bound.get("outputs_digest") or "") and card_governance_outputs(workspace_root, {**(card_frontmatter or {}), "task_id": task_id}):
        try:
            current = governance_outputs_manifest(workspace_root, task_id, card_frontmatter or {})["outputs_digest"]
        except GovernanceOutputsError:
            current = ""  # 此刻清单不合: 不以清单判(重交回时产物入口同一实现给拒因原文)
        outputs_changed = bool(current) and current != str(bound["outputs_digest"])
    if bound_report:
        changed = digest != bound_report or outputs_changed
    else:
        changed = outputs_changed or modified > moment
    if not changed:
        return None  # 被裁决那份原样(只动了修改时刻 / 同一秒)= 未重写交回
    return {"source": "file", "ref": str(path), "at": modified.strftime("%Y-%m-%dT%H:%M:%SZ"), "sha256": digest}


def _non_branch_verdict_staleness(workspace_root: Path, task_id: str, card_frontmatter: dict[str, Any] | None,
                                  subject: dict[str, Any]) -> dict[str, Any] | None:
    """AIPOS-F218 件①: verdict_staleness 的非卡分支主体分支(non_branch_rework_subject)——最新常规裁决(_regular_verdicts, 与 latest_verdict
    同一排序)非 PASS 族、已针对它登记返工轮(rework_round_for_verdict)且已重写交回(non_branch_re_return)= 过期(该裁决审的产物已被返工取代)。
    返回形同卡分支分支, 另带 subject(kind)与 re_execution; candidate = tip 取卡面固定候选(候选读不出 = 卡面原值、tree 空, 随后产物入口
    同一校验给 INGEST_CANDIDATE_INVALID 原文); governance_outputs = 无 tip / 分支(空串)。"""
    regular = _regular_verdicts(workspace_root, task_id)
    if not regular:
        return None
    verdict_path, verdict_fm = regular[0]
    allowed = [str(v) for v in (_transition_node("N5").get("guards", {}).get("has_pass_verdict", {}).get("allowed_verdict_values") or [])]
    if str(verdict_fm.get("verdict") or "") in allowed:
        return None
    verdict_id = str(verdict_fm.get("verdict_id") or verdict_path.stem)
    verdict_at = str(verdict_fm.get("verdict_at") or "")
    rework = rework_round_for_verdict(card_frontmatter, {**verdict_fm, "verdict_id": verdict_id, "verdict_at": verdict_at})
    if rework is None:
        return None
    evidence = non_branch_re_return(workspace_root, task_id, card_frontmatter, verdict_path, verdict_fm, rework)
    if evidence is None:
        return None
    values: dict[str, Any] = {}
    if subject.get("kind") == "candidate":
        try:
            values = candidate_subject_values(workspace_root, task_id, card_frontmatter) or {}
        except CandidateSubjectError:
            values = {"commit_sha": str(subject.get("candidate") or ""), "tree_hash": "", "branch": ""}
    covered = verdict_fm.get("artifact_subject") if isinstance(verdict_fm.get("artifact_subject"), dict) else {}
    return {
        "subject": str(subject.get("kind") or ""),
        "rework_round": rework,
        "re_execution": evidence,
        "verdict_id": verdict_id,
        "verdict": str(verdict_fm.get("verdict") or ""),
        "verdict_at": verdict_at,
        "verdict_commit_sha": str(covered.get("commit_sha") or values.get("commit_sha") or ""),
        "audit_task_id": str(verdict_fm.get("audit_task_id") or ""),
        "reviewed_return_record_ref": str(verdict_fm.get("reviewed_return_record_ref") or ""),
        "branch": str(values.get("branch") or ""),
        "tip": str(values.get("commit_sha") or ""),
        "tree_hash": str(values.get("tree_hash") or ""),
        "reason": "",
    }


# ---------------------------------------------------------------------------
# AIPOS-F221 件②: 非代码快车道(F197)顾问复核不通过 → 返工轮 → 再交回 → 再复核。出口 = mark-concluded --outcome rework
# (verbs.schema lybra_mark_concluded.review_rework, 写路径 board_adapter.mark_concluded_task); 推导沿用 F218/F219 返工判据
# (rework_round_for_verdict / rework_return_bound / non_branch_re_return), 锚 = 顾问复核记录(裁决形视图), 不另起第二套。
# ---------------------------------------------------------------------------

_REVIEW_REWORK_KEYS = ("outcome", "event_type", "record_fields", "command", "reject_codes", "audit_verdict_refusal", "escalate_hint")
_REVIEW_REWORK_CODES = ("REVIEW_REWORK_NOT_CLAIMED", "REVIEW_REWORK_NOT_FAST_LANE", "REVIEW_REWORK_NO_RETURN", "REVIEW_REWORK_FOCUS_REQUIRED",
                        "REVIEW_REWORK_ALREADY_PENDING", "REVIEW_REWORK_ROUND_REFUSED", "REVIEW_REWORK_ESCALATED")
_FAST_LANE_REWORK_TEXTS = ("await_missing", "await_suggested", "await_progress", "stale_head", "re_return_note", "re_return_suggested",
                           "invalid_fix", "not_updated_note", "reviewed_what", "without_round_note", "without_round_suggested", "back_to_review")


def review_rework_declaration() -> dict[str, Any]:
    """AIPOS-F221 件②: verbs.schema lybra_mark_concluded.review_rework(唯一读取口)。缺键 / 拒因缺 = SchemaLoadError(fail-closed)。"""
    from tools.schema_loader import SchemaLoadError, load_schema

    decl = ((load_schema("verbs").get("verbs") or {}).get("lybra_mark_concluded") or {}).get("review_rework")
    codes = decl.get("reject_codes") if isinstance(decl, dict) else None
    if (not isinstance(decl, dict) or not all(decl.get(k) for k in _REVIEW_REWORK_KEYS) or not isinstance(decl.get("record_fields"), list)
            or not isinstance(codes, dict) or not all(isinstance(codes.get(k), str) and codes[k].strip() for k in _REVIEW_REWORK_CODES)):
        raise SchemaLoadError(f"verbs.schema.json verbs.lybra_mark_concluded.review_rework({list(_REVIEW_REWORK_KEYS)} / reject_codes "
                              f"{list(_REVIEW_REWORK_CODES)})未声明齐(AIPOS-F221)")
    return decl


def fast_lane_rework_declaration() -> dict[str, Any]:
    """AIPOS-F221 件②: transitions nodes.N4.fast_lane_rework(唯一读取口)。state / 措辞缺 = SchemaLoadError(fail-closed)。"""
    from tools.schema_loader import SchemaLoadError

    decl = _transition_node("N4").get("fast_lane_rework")
    texts = decl.get("texts") if isinstance(decl, dict) else None
    if (not isinstance(decl, dict) or not str(decl.get("state") or "").strip() or not isinstance(texts, dict)
            or not all(isinstance(texts.get(k), str) and texts[k].strip() for k in _FAST_LANE_REWORK_TEXTS)):
        raise SchemaLoadError(f"transitions.schema.json nodes.N4.fast_lane_rework(state / texts.{list(_FAST_LANE_REWORK_TEXTS)})未声明齐(AIPOS-F221)")
    return decl


def fast_lane_review(workspace_root: Path, task_id: str, card_frontmatter: dict[str, Any] | None) -> bool:
    """AIPOS-F221 件②: 非代码快车道唯一判定(AIPOS-F197)= 交付主体为治理根产出清单(card_governance_outputs)且不声明独立审计
    (card_independent_audit)——交回后由顾问复核(mark-concluded 结案 / --outcome rework 打回返工), 不走审计裁决。
    推导核快车道分支、mark-concluded 复核打回前置、audit-verdict 拒(fast_lane_audit_refusal)同读。"""
    fm = card_frontmatter if isinstance(card_frontmatter, dict) else {}
    return card_governance_outputs(workspace_root, {**fm, "task_id": task_id}) and not card_independent_audit(fm)


def advisor_reviews(workspace_root: Path, task_id: str) -> list[tuple[Path, dict[str, Any]]]:
    """AIPOS-F221 件②: 本卡顾问复核记录(records/events/<卡>/<review_rework.event_type>_*.md; 写入 board_adapter.mark_concluded_task
    --outcome rework), 旧 → 新(timestamp, 同刻按文件名)。读不出 = FrontmatterReadError 上抛(AIPOS-F100, 推导核硬停)。"""
    from tools.aipos_cli.frontmatter import require_frontmatter
    from tools.aipos_cli.record_writer import record_files_with_prefix

    event_type = str(review_rework_declaration()["event_type"])
    out: list[tuple[Path, dict[str, Any]]] = []
    for path in record_files_with_prefix(record_dir(Path(workspace_root), "events", task_id), event_type):
        fm, _body = require_frontmatter(path)
        if str(fm.get("event_type") or "") == event_type:
            out.append((path, fm))
    from tools.aipos_cli.owner_inbox import _parse_utc

    floor = datetime.min.replace(tzinfo=timezone.utc)
    # AIPOS-F232 件⑤: 按解析后的时刻排序(毫秒级新记录与秒级存量混排不错序), 同刻按文件名
    out.sort(key=lambda item: (_parse_utc(item[1].get("timestamp")) or floor, item[0].name))
    return out


def advisor_review_anchor(workspace_root: Path, task_id: str) -> tuple[Path, dict[str, Any]] | None:
    """AIPOS-F221 件②: 返工判据的锚 = 最新一条顾问复核记录的裁决形视图 {verdict_id = review_id, verdict_at = 复核时刻, verdict = outcome,
    reviewed_return_record_ref = 所审交回, audit_task_id = ""}——交给 F218/F219 同一组函数(rework_round_for_verdict / rework_return_bound /
    non_branch_re_return)。无复核记录 = None。"""
    reviews = advisor_reviews(workspace_root, task_id)
    if not reviews:
        return None
    path, fm = reviews[-1]
    view = {**fm, "verdict_id": str(fm.get("review_id") or path.stem), "verdict_at": str(fm.get("timestamp") or ""),
            "verdict": str(fm.get("outcome") or ""), "reviewed_return_record_ref": str(fm.get("reviewed_return_record_ref") or ""),
            "audit_task_id": ""}
    return path, view


def fast_lane_rework_state(workspace_root: Path, task_id: str, card_frontmatter: dict[str, Any] | None) -> dict[str, Any] | None:
    """AIPOS-F221 件②: 快车道卡复核打回后的返工态(推导核与 mark-concluded 重复打回判定同读)。最新顾问复核记录在且已针对它登记未销账
    返工轮(rework_round_for_verdict)= {anchor_path, anchor, rework, bound, evidence, re_returned}; evidence = non_branch_re_return(晚于时间界
    的新交回: source=record 门已落新交回 / source=file 交回报告已重写 / None 未返工); re_returned = 已按本轮返工重交回 → 回到顾问复核
    (门交回证据, 或报告证据成立且门最新交回已不是被打回的那份——同秒落盘时门记录一路按严格晚于界不认, 报告一路已认, 两路合判, 不卡在重交回)。
    否则 None(未被打回 / 返工轮未登记)。"""
    anchor = advisor_review_anchor(workspace_root, task_id)
    if anchor is None:
        return None
    path, view = anchor
    rework = rework_round_for_verdict(card_frontmatter, view)
    if rework is None:
        return None
    evidence = non_branch_re_return(workspace_root, task_id, card_frontmatter, path, view, rework)
    latest_id = str((_read_task_records(Path(workspace_root), task_id).get("latest_return") or {}).get("return_id") or "").strip()
    reviewed = str(view.get("reviewed_return_record_ref") or "").strip()
    re_returned = evidence is not None and (evidence.get("source") == "record" or bool(latest_id and latest_id != reviewed))
    return {"anchor_path": path, "anchor": view, "rework": rework, "bound": rework_return_bound(path, view, rework),
            "evidence": evidence, "re_returned": re_returned}


def review_rework_command(workspace_root: Path, task_id: str, report_ref: str) -> str:
    """AIPOS-F221 件②: 复核不通过打回返工命令(review_rework.command; 推导核快车道提示 / audit-verdict 拒因同读)。"""
    from tools.aipos_cli.workspace_config import lybra_command_prefix

    return str(review_rework_declaration()["command"]).format(prefix=lybra_command_prefix(workspace_root), task_id=task_id, report=report_ref)


def fast_lane_audit_refusal(workspace_root: Path, task_id: str) -> str | None:
    """AIPOS-F221 件②⑤: 对在途(claimed)快车道卡提交审计裁决 = 拒因原文(review_rework.audit_verdict_refusal, 点名复核结案与复核打回两条正式出口);
    非快车道 / 已派审(派审记录在, 如补充审计)/ 卡不在 claimed / 找不到卡 = None(照常走审计裁决)。CLI audit-verdict 在取凭据前判,
    门侧裁决(board_adapter.audit_verdict_task)同判。"""
    from tools.aipos_cli.workspace_config import lybra_command_prefix

    task_path, queue_dir = _find_task_in_queue(Path(workspace_root), task_id)
    if task_path is None or queue_dir != "claimed":
        return None
    fm = _read_frontmatter(task_path)
    if not fast_lane_review(workspace_root, task_id, fm):
        return None
    if _read_task_records(Path(workspace_root), task_id).get("latest_audit_dispatch"):
        return None
    try:
        report_ref = str(_return_artifact_path(workspace_root, task_id).resolve().relative_to(Path(workspace_root).resolve()))
    except ValueError:
        report_ref = str(_return_artifact_path(workspace_root, task_id))
    pass_command = (f"{lybra_command_prefix(workspace_root)} mark-concluded --task-id {task_id} --actor <你的顾问实例> "
                    f"--report-path {report_ref} --conclusion-note \"顾问复核: <结论>\"")
    return str(review_rework_declaration()["audit_verdict_refusal"]).format(
        task_id=task_id, pass_command=pass_command, rework_command=review_rework_command(workspace_root, task_id, report_ref))


def return_binding_subject(workspace_root: Path, task_id: str, card_frontmatter: dict[str, Any] | None) -> dict[str, str] | None:
    """AIPOS-F114 件①: 门 queue_return 落交回记录时绑定的被交回产物主体 = 卡分支此刻 tip {repository, commit_sha, tree_hash, branch}
    (AIPOS-F197: 只写治理根的卡 = 产出清单主体 {kind=governance_outputs, repository, outputs_digest, outputs})
    (_card_branch_tip 唯一读取, 与裁决绑 tip / 过期判据同一函数)。非代码卡 / 产品仓不可解析 / 无卡分支 = None
    (交回记录不带绑定, 与存量记录同形; 代码卡经产物入口交回时入口已核 Return commit_sha == 此 tip)。"""
    # AIPOS-F197 件②: 只写治理根的卡 = 绑定产出清单(governance_outputs_manifest 唯一实现; 门 queue_return 先以同一实现核清单, 不合即拒,
    # 故此处清单不合只在门外调用出现 = 不带绑定)
    fm = card_frontmatter if isinstance(card_frontmatter, dict) else {}
    if card_governance_outputs(workspace_root, {**fm, "task_id": task_id}):
        try:
            manifest = governance_outputs_manifest(workspace_root, task_id, fm)
        except GovernanceOutputsError:
            return None
        bound = {k: manifest[k] for k in ("kind", "repository", "outputs_digest", "outputs")}
        report = find_return_artifact(workspace_root, task_id)
        if report is not None:  # AIPOS-F218 件①: 附交回报告指纹(返工重交回判据 non_branch_re_return 读它)
            bound[str(non_branch_rework_declaration()["return_fingerprint_key"])] = _file_sha256(report)
        return bound
    if card_delivery_subject(workspace_root, task_id, fm).get("kind") == "candidate":
        # AIPOS-F218 件①: 固定候选卡 = 候选三键(candidate_subject_values 唯一实值)+ 交回报告指纹(返工重交回判据 non_branch_re_return 读它);
        # 候选读不出 = 不带绑定(门 queue_return 前产物入口已以同一实值核报告三键, 不合即拒)
        try:
            values = candidate_subject_values(workspace_root, task_id, fm)
        except CandidateSubjectError:
            return None
        if not values:
            return None
        report = find_return_artifact(workspace_root, task_id)
        bound = {"kind": "candidate", **{k: values[k] for k in ("repository", "commit_sha", "tree_hash", "branch")}}
        if report is not None:
            bound[str(non_branch_rework_declaration()["return_fingerprint_key"])] = _file_sha256(report)
        return bound
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
    return not text or text.startswith(_skeleton_prefix()) or text.startswith("<")


def _report_declaration(kind: str, governance: bool = False) -> dict[str, Any]:
    """报告契约声明节: transitions artifact_ingest.<return|verdict>; AIPOS-F197 governance=True = 其 governance_root 分形
    (只写治理根的执行卡 / 其审计卡)。缺 = SchemaLoadError(fail-closed)。"""
    from tools.schema_loader import SchemaLoadError

    decl = _artifact_ingest_declaration()[kind]
    if not governance:
        return decl
    if kind == "return":
        return governance_outputs_declaration()
    sub = decl.get("governance_root") if isinstance(decl, dict) else None
    if not isinstance(sub, dict) or not isinstance(sub.get("required_frontmatter"), list) or not isinstance(sub.get("field_hints"), dict):
        raise SchemaLoadError("transitions.schema.json artifact_ingest.verdict.governance_root(required_frontmatter / field_hints)未声明(AIPOS-F197)")
    return sub


def required_verdict_frontmatter(*, governance: bool = False) -> list[str]:
    """审计报告必填 frontmatter 键(唯一声明: transitions artifact_ingest.verdict.required_frontmatter; AIPOS-F197 governance=True =
    被审卡只写治理根时的分形 verdict.governance_root.required_frontmatter)。"""
    keys = list(_report_declaration("verdict", governance).get("required_frontmatter") or [])
    if not keys:
        from tools.schema_loader import SchemaLoadError

        raise SchemaLoadError("transitions.schema.json artifact_ingest.verdict.required_frontmatter 未声明")
    return [str(k) for k in keys]


def missing_verdict_frontmatter(frontmatter: dict[str, Any], *, governance: bool = False) -> list[str]:
    """审计报告缺失的必填 frontmatter 键(空值/占位视为缺)。AIPOS-F89 件③c: 报告完成判据(artifact_ingest.verdict.readiness)
    = 本函数返回空; 认领时生成的同名同位空模板(全占位)不算完成。AIPOS-F197: governance = 被审卡只写治理根(card_governance_outputs)。"""
    return [k for k in required_verdict_frontmatter(governance=governance) if _is_placeholder_value(frontmatter.get(k))]


def verdict_report_submitted(frontmatter: dict[str, Any], *, governance: bool = False) -> bool:
    """AIPOS-F89 件③c: 审计报告「已动笔」= 至少一个声明必填键已填实值(认领时的空模板全为占位 = 未交, 继续等待)。
    已交但未填全 → 推导核判 artifact_invalid(loop 硬停点名缺项), 不当完成也不空等。"""
    return any(not _is_placeholder_value(frontmatter.get(k)) for k in required_verdict_frontmatter(governance=governance))


def required_return_frontmatter(*, governance: bool = False) -> list[str]:
    """Return 必填 frontmatter 键(唯一声明: transitions artifact_ingest.return.required_frontmatter; AIPOS-F197 governance=True =
    只写治理根卡的分形 return.governance_root.required_frontmatter, 无分支三键)。"""
    keys = list(_report_declaration("return", governance).get("required_frontmatter") or [])
    if not keys:
        from tools.schema_loader import SchemaLoadError

        raise SchemaLoadError("transitions.schema.json artifact_ingest.return.required_frontmatter 未声明")
    return [str(k) for k in keys]


def missing_return_frontmatter(frontmatter: dict[str, Any], *, governance: bool = False) -> list[str]:
    """缺失的 Return 必填 frontmatter 键列表(空值/占位视为缺)。AIPOS-F197: governance = 本卡只写治理根(card_governance_outputs)。"""
    return [k for k in required_return_frontmatter(governance=governance) if _is_placeholder_value(frontmatter.get(k))]


def card_report_is_governance(workspace_root: Path, task_id: str, card_frontmatter: dict[str, Any] | None = None) -> bool:
    """AIPOS-F197: 该卡(执行卡或审计卡)的报告契约是否取 governance_root 分形(= card_governance_outputs; 卡不在队列 = False)。
    card_frontmatter 缺 = 按 task_id 读队列卡面。判定读不出(声明 / 卡面)= 异常上抛, 调用方 fail-closed。"""
    fm = card_frontmatter
    if fm is None:
        task_path, _q = _find_task_in_queue(workspace_root, task_id)
        if task_path is None:
            return False
        fm = _read_frontmatter(task_path)
    return card_governance_outputs(workspace_root, {**fm, "task_id": str(fm.get("task_id") or task_id)})


# ---------------------------------------------------------------------------
# AIPOS-F93 件①: 报告必填字段单源告知——声明 transitions artifact_ingest.<return|verdict>.required_frontmatter(+ field_hints)
# 的唯一渲染。派生审计卡落点句 / 执行卡落点句 / my-tasks next_card / 认领模板 / 章程报告节 全部经此二函数, 禁第二份清单/文案。
# ---------------------------------------------------------------------------

REPORT_KINDS = ("return", "verdict")


def report_frontmatter_contract(kind: str, *, branch_task_id: str | None = None,
                                subject: dict[str, str] | None = None,
                                branch_pattern: str | None = None, candidate: bool = False,
                                governance: bool = False, report_dir: str | None = None) -> list[dict[str, Any]]:
    """报告必填 frontmatter 契约(唯一渲染源): [{key, hint, value}], 键序 = 声明 required_frontmatter。

    - kind: return(执行卡 Return)/ verdict(审计报告)。
    - branch_task_id: 报告对应的卡分支所属卡(执行卡 = 本卡, 审计卡 = 被审卡); 缺 = 分支声明套占位卡号(card/<卡ID>)。
    - subject: 产品已知的实值(审计卡 = 取证工作树 HEAD 的 {commit_sha, tree_hash}); 有值的键 value=实值, 提示带 tree, agent 照抄。
    - branch_pattern: 调用方已读的分支声明(machine_zone 按 product_root 读 N5.branch_integration); 缺 = card_branch_name。
    - candidate: AIPOS-F196 交付主体为固定候选(validation 卡与其审计卡): subject = candidate_subject_values 三键实值, 提示写明是固定候选。
    - governance: AIPOS-F197 只写治理根的执行卡(return: outputs 产出清单, 无分支三键)/ 其审计卡(verdict: verdict + outputs_digest,
      subject = 交回绑定的 {outputs_digest}); report_dir = 本卡报告落点目录(治理根相对, 带尾 /; 缺 = `<报告落点>/`), 填提示占位 {report_dir}。
    键缺说明(field_hints 未声明该键)= 通用提示「按声明填写实值」(仍列出, 不漏键)。声明缺 = SchemaLoadError(fail-closed)。
    """
    if kind not in REPORT_KINDS:
        raise ValueError(f"report_frontmatter_contract: kind={kind!r} 不在 {REPORT_KINDS}")
    keys = required_return_frontmatter(governance=governance) if kind == "return" else required_verdict_frontmatter(governance=governance)
    hints = _report_declaration(kind, governance).get("field_hints") or {}
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
        hint = (template.replace("{branch}", branch).replace("{allowed_verdict_values}", verdict_values or "裁决值")
                .replace("{report_dir}", report_dir or "<报告落点>/"))
        value = str(values.get(key) or "").strip() or None
        if value and candidate:
            what = {"commit_sha": f"来源卡 {branch_task_id} 的固定候选", "tree_hash": "固定候选的 tree",
                    "branch": f"来源卡 {branch_task_id} 的卡分支名(说明候选出处)"}.get(key, "按声明")
            hint = f"{what}; 产品给出, 照抄(本卡交付主体 = 固定候选; 验证卡不建卡分支、不提交产品仓)"
        elif value and key == "commit_sha" and values.get("tree_hash"):
            hint = f"{hint}; 产品给出: {branch} tip = {value}, tree = {values['tree_hash']}(取证工作树 HEAD), 照抄"
        entries.append({"key": key, "hint": hint, "value": value})
    return entries


def render_report_frontmatter_clause(entries: list[dict[str, Any]]) -> str:
    """报告必填 frontmatter 的唯一一句话文案(落点句 / 章程 / card render 共用)。entries = report_frontmatter_contract 输出。"""
    parts = []
    for entry in entries:
        value = entry.get("value")
        parts.append(f"`{entry['key']}`({f'= {value}; ' if value else ''}{entry['hint']})")
    return "报告 frontmatter 必填: " + "; ".join(parts) + "。" + report_refusal_clause()


def report_refusal_clause() -> str:
    """AIPOS-F215 件③: 报告必填拒收条款(transitions.schema artifact_ingest.report_refusal_clause)唯一读取口——章程报告必填句、
    my-tasks 开工提示模板占位 {refusal_clause}、lybra kickoff 交回节同读。缺 / 空 = SchemaLoadError(fail-closed)。"""
    from tools.schema_loader import SchemaLoadError

    clause = _artifact_ingest_declaration().get("report_refusal_clause")
    if not isinstance(clause, str) or not clause.strip():
        raise SchemaLoadError("transitions.schema.json artifact_ingest.report_refusal_clause 未声明")
    return clause


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
    workspace_root: Path | str | None = None,
    dispatch_reason: str | None = None,
) -> str:
    """构建审计派发命令。AIPOS-F179 件③: workspace_root / dispatch_reason 给出 = 可照抄的完整命令(全局 --workspace-root + --dispatch-reason,
    重审命令 reaudit_command 同一构建); 缺省 = 原形(loop 执行时治理根经环境下传)。"""
    from tools.aipos_cli.workspace_config import lybra_command_prefix

    parts = ["lybra audit dispatch"] if workspace_root is None else [f"{lybra_command_prefix(workspace_root)} audit dispatch"]
    parts.append(f"--source-task-id {task_id}")
    parts.append(f"--actor {actor}")
    parts.append(f"--agent-instance {agent_instance}")
    if owner_policy_ref:
        parts.append(f"--owner-policy-ref {owner_policy_ref}")
    parts.append(f"--audit-task-id {audit_task_id}")
    parts.append(f"--audit-agent-instance {audit_agent_instance}")
    if dispatch_reason:
        parts.append(f"--dispatch-reason {_shlex_quote(dispatch_reason)}")
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
    shell_command = 入口内部执行的同一条薄壳命令(build_return_command_from_artifact 唯一构建), 禁第二条交回路径。
    AIPOS-F236 件②: 本卡交回入门被拒且自拒后报告与产物未变、此刻复核仍拒(唯一判定 artifact_ingest.return_rejection_state)= 不再给交回命令,
    改为不可推导 + 合法出口全集(return_rejected); 此刻复核已通过 = 照常给命令并附说明(门侧拒另附出口)。"""
    from tools.aipos_cli.artifact_ingest import return_rejected_declaration, return_rejection_state

    rejection = return_rejection_state(workspace_root, task_id)
    if rejection is not None and rejection["still_rejected"]:
        return _return_rejected_step(workspace_root, task_id, fm, rejection, conn_arg=conn_arg, return_path=return_path, node=node)
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
    step: dict[str, Any] = {
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
    if rejection is not None:  # 拒后未变但此刻复核通过(产品 / 仓已变, 或拒的是门侧): 照常交回 + 说明
        texts = return_rejected_declaration()["texts"]
        rec = rejection["record"]
        values = {"rejection_id": rec.get("rejection_id"), "category": rec.get("category")}
        step["notes"] = f"{notes}; " + texts["gate_side" if rejection["gate_side"] else "revalidated_ok"].format(**values)
    return step


def _return_rejected_step(workspace_root: Path, task_id: str, fm: dict[str, Any], rejection: dict[str, Any], *, conn_arg: str | None,
                          return_path: Path, node: str) -> dict[str, Any]:
    """AIPOS-F236 件②: 交回入门被拒且自拒后报告与产物未变、此刻复核仍拒 = 不可推导(action.type = rejection_record.action_type), 不给交回命令;
    suggested_action = 此刻拒因 + 合法出口全集(transitions nodes.N4.legal_exits.states.return_rejected; 配置卡交付程序按
    evidence_overrides.return_rejected_program 推荐换新主体)。loop 见此 action 按 lybra_loop.launch.report_fix.executor.artifact 续接或结束。"""
    from tools.aipos_cli.artifact_ingest import program_guard_declaration, return_rejected_declaration

    decl = return_rejected_declaration()
    texts = decl["texts"]
    rec, check = rejection["record"], rejection["check"]
    category = str(check.get("category") or "")
    rec_key, why = None, ""
    guard = program_guard_declaration() or {}
    if category and category == str(guard.get("category") or ""):
        rule = legal_exits_declaration()["evidence_overrides"].get("return_rejected_program") or {}
        rec_key, why = rule.get("exit"), str(rule.get("why") or "")
    exits = legal_exits(workspace_root, task_id, fm, str(decl["legal_exits_state"]),
                        actor=_driver_actor(workspace_root, connection_json=conn_arg),
                        policy_ref=_driver_envelope_ref(workspace_root, task_id, fm, conn_arg) or "", recommended=rec_key, why=why)
    try:
        report_ref = str(Path(return_path).resolve().relative_to(Path(workspace_root).resolve()))
    except ValueError:
        report_ref = str(return_path)
    reason = " ".join(str(r) for r in check.get("reasons") or []) or category
    values = {"rejection_id": rec.get("rejection_id"), "recorded_at": rec.get("recorded_at"), "category": category,
              "return_path": report_ref, "reason": reason, "exits": exits["text"], "path": rec.get("_path")}
    return _attach_legal_exits({
        "task_id": task_id,
        "derivable": False,
        "current_node": node,
        "current_state": "claimed",
        "triggered_by": "advisor",
        "command": "",
        "verb": "",
        "missing_records": [texts["missing"].format(**values)],
        "suggested_action": texts["suggested"].format(**values),
        "notes": texts["notes"].format(**values),
        "action": {"type": str(decl["action_type"]), "card": task_id, "path": str(return_path), "category": category,
                   "rejection_id": rec.get("rejection_id"), "end_reason": str(decl["action_type"])},
        "return_rejection": {"rejection_id": rec.get("rejection_id"), "category": category, "record_path": rec.get("_path")},
    }, exits)


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
    if str(stale.get("subject") or "") in non_branch_rework_declaration()["by_subject"]:
        # AIPOS-F218 件①: 交付主体非卡分支的卡 = 已按返工重写交回(non_branch_re_return); 说理 / 补法措辞读 transitions nodes.N4.non_branch_rework
        texts, values = non_branch_stale_texts(stale)
        head = texts["stale_head"].format(**values)
        return _derive_stale_re_return(
            workspace_root, task_id, fm, records, stale, claimer=claimer, conn_arg=conn_arg, state=state, stale_key="verdict_stale",
            head=head, reviewed_ref=str(stale.get("reviewed_return_record_ref") or ""), since=str(stale.get("verdict_at") or ""),
            reviewed_what=texts["reviewed_what"], without_round_suggested=texts["without_round_suggested"],
            without_round_note=texts["without_round_note"], not_updated_note=texts["not_updated_note"],
            re_return_notes=f"{state}: {head}; {texts['re_return_note']}", invalid_fix=texts["invalid_fix"].format(**values),
            re_return_suggested=texts["re_return_suggested"])
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


def non_branch_evidence_text(texts: dict[str, str], evidence: dict[str, Any]) -> str:
    """非卡分支主体返工交回证据一句(non_branch_rework.texts.evidence_record / evidence_file; non_branch_re_return 结果)。"""
    return texts["evidence_record" if evidence.get("source") == "record" else "evidence_file"].format(
        return_id=evidence.get("ref"), path=evidence.get("ref"), at=evidence.get("at"))


def non_branch_stale_texts(stale: dict[str, Any]) -> tuple[dict[str, str], dict[str, Any]]:
    """AIPOS-F219 件②: 非卡分支主体裁决过期(_non_branch_verdict_staleness 结果, 带 subject)的措辞(non_branch_rework texts 公共 + by_subject.<kind>)
    与占位值唯一构建——推导核 verdict_stale 说理(_derive_verdict_stale)与门侧派下一轮审计卡复审说理(audit_derivation.build_derived_audit_task)同读。"""
    decl = non_branch_rework_declaration()
    texts = {**decl["texts"], **decl["by_subject"][str(stale["subject"])]}
    values = {"verdict_id": stale.get("verdict_id"), "verdict": stale.get("verdict"), "audit_task_id": stale.get("audit_task_id") or "?",
              "reviewed": stale.get("reviewed_return_record_ref") or "?", "round": (stale.get("rework_round") or {}).get("round") or "?",
              "candidate": stale.get("tip") or "?", "tree_hash": stale.get("tree_hash") or "?", "branch": stale.get("branch") or "?",
              "evidence": non_branch_evidence_text(texts, stale.get("re_execution") or {})}
    return texts, values


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


def _derive_rework_wait(workspace_root: Path, task_id: str, fm: dict[str, Any], records: dict[str, Any], verdict: dict[str, Any],
                        rework: dict[str, Any], *, claimer: str, conn_arg: str | None) -> dict[str, Any]:
    """AIPOS-F155 件②(gap #135): N4 非 PASS 裁决且已针对它登记返工轮(F75 返工识别 rework_round_for_verdict)。
    执行体已按返工节修复(卡分支前进, verdict_staleness 判该裁决已被返工取代)且 Return frontmatter commit_sha 已是新 tip = 重交回,
    走 F112 verdict_stale 同一出口(_derive_verdict_stale → 重交回 → 门侧按卡号演进派下一轮审计 R(n+1)); Return 已写新 tip 但不合规 =
    该出口的 artifact_invalid(原文点名)。否则 = 等返工交回: 不可推导但 action await_rework(loop 按执行体产物等待, 与 N1 执行体在干活
    同一等待; 不以 not_derivable 结束, 故不入连败), 等待提示附返工节要点。
    AIPOS-F218 件①: 交付主体非卡分支的卡(non_branch_rework_subject: 固定候选 / 治理根产出清单, F210 唯一判定)无卡分支——verdict_staleness
    判已按返工重写交回(晚于裁决与该轮登记的新交回, AIPOS-F219 rework_return_bound)即走同一重交回出口(报告不合本主体契约 = artifact_invalid); 等待提示按主体措辞, 不提卡分支新提交
    (transitions nodes.N4.non_branch_rework.by_subject)。"""
    round_num = rework.get("round") or "?"
    focus = [str(x) for x in (rework.get("focus_items") or [])]
    criteria = "; ".join(ln.strip() for ln in str(rework.get("acceptance_criteria") or "").splitlines() if ln.strip())  # 一行(等待提示单行)
    stale = verdict_staleness(workspace_root, task_id, fm)
    points = "; ".join(focus) if focus else "(返工节无具体项)"
    subject = non_branch_rework_subject(workspace_root, task_id, fm)
    # AIPOS-F219 件①: 等待提示点名时间界(第 n 轮于 <时刻> 登记, 等其后的新交回; rework_return_bound 唯一实现, 与判据同一界)
    regular = _regular_verdicts(workspace_root, task_id)
    bound_note = rework_bound_note(rework_return_bound(regular[0][0], regular[0][1], rework)) if regular else ""
    earlier_note = ""
    if subject is not None:
        if stale is not None and stale.get("rework_round") is not None:
            return _derive_verdict_stale(workspace_root, task_id, fm, records, stale, claimer=claimer, conn_arg=conn_arg)
        decl = non_branch_rework_declaration()
        texts = {**decl["texts"], **decl["by_subject"][str(subject["kind"])]}
        values = {"round": round_num, "candidate": str(subject.get("candidate") or "?"), "points": points,
                  "criteria": texts["criteria"].format(criteria=criteria) if criteria else ""}
        missing = texts["await_missing"].format(**values)
        suggested = texts["await_suggested"].format(**values)
        progress = texts["await_progress"].format(**values)
        # 晚于裁决但早于该轮登记的旧改写(F218 口径会误当本轮交回)= 点名不算(同一判定 non_branch_re_return, 仅以裁决时刻为界)
        earlier = non_branch_re_return(workspace_root, task_id, fm, regular[0][0], regular[0][1]) if regular else None
        if earlier is not None:
            earlier_note = rework_return_bound_declaration()["texts"]["earlier_ignored"].format(
                evidence=non_branch_evidence_text(texts, earlier), round=round_num)
    else:
        return_path = find_return_artifact(workspace_root, task_id)
        if stale is not None and stale.get("rework_round") is not None and return_path is not None:
            ret_fm = _read_frontmatter(return_path, allow_missing_block=True)
            if str(ret_fm.get("commit_sha") or "").strip().lower() == str(stale.get("tip") or "").lower():
                return _derive_verdict_stale(workspace_root, task_id, fm, records, stale, claimer=claimer, conn_arg=conn_arg)
        branch = card_branch_name(task_id)
        progress = (f"卡分支 {branch} 已有返工提交(tip {str(stale.get('tip'))[:12]}), 等 Return frontmatter commit_sha / tree_hash 更新为该 tip"
                    if stale is not None else f"等卡分支 {branch} 出现返工提交并更新 Return")
        missing = f"第 {round_num} 轮返工交回(卡分支 {branch} 新提交 + Return commit_sha/tree_hash 更新为新 tip)"
        suggested = (f"执行体按第 {round_num} 轮返工节修复后重新交回(返工要点: {points}"
                     + (f"; 验收: {criteria}" if criteria else "")
                     + f"): 在卡工作树提交修复 → 重跑定向测试 → 把 Return frontmatter commit_sha / tree_hash 改为卡分支新 tip、"
                       "正文补返工证据; loop 见新交回即重交并派下一轮审计")
    if bound_note:
        missing, suggested, progress = f"{missing}; {bound_note}", f"{suggested}; {bound_note}", f"{progress}; {bound_note}"
    if earlier_note:
        progress = f"{progress}; {earlier_note}"
    return {
        "task_id": task_id,
        "derivable": False,
        "current_node": "audit_verdict",
        "current_state": "claimed",
        "triggered_by": "executor",
        "command": "",
        "verb": "lybra_queue_return_dry_run",
        "missing_records": [missing],
        "suggested_action": suggested,
        "notes": (f"N4: 裁决 {verdict.get('verdict')}({verdict.get('verdict_id') or '?'}), 已登记第 {round_num} 轮返工"
                  f"(依据 {rework.get('verdict_ref') or '?'}); {progress}(AIPOS-F155 件②, F75 返工节)"),
        "rework_round": rework,
        "action": {"type": "await_rework", "card": task_id, "round": round_num},
    }


def _derive_fast_lane_rework(workspace_root: Path, task_id: str, fm: dict[str, Any], records: dict[str, Any],
                             state: dict[str, Any], *, claimer: str, conn_arg: str | None) -> dict[str, Any]:
    """AIPOS-F221 件②: 快车道卡顾问复核打回、已登记返工轮(fast_lane_rework_state)且门尚未落晚于时间界的新交回时的推导(声明 transitions
    nodes.N4.fast_lane_rework)。交回报告已按返工重写(non_branch_re_return source=file)= 重交回(_derive_stale_re_return 同一构建:
    产物入口同一校验, 不合 = artifact_invalid); 否则 = 等返工(action await_rework, loop 拉起执行体返工, 与 _derive_rework_wait 非卡分支
    同一等待路径; 等待提示点名时间界 rework_bound_note, 晚于复核但早于该轮登记的旧改写点名不算)。"""
    decl = fast_lane_rework_declaration()
    texts = decl["texts"]
    state_name = str(decl["state"])
    anchor, rework, evidence = state["anchor"], state["rework"], state["evidence"]
    round_num = rework.get("round") or "?"
    focus = [str(x) for x in (rework.get("focus_items") or [])]
    criteria = "; ".join(ln.strip() for ln in str(rework.get("acceptance_criteria") or "").splitlines() if ln.strip())
    nb_texts = non_branch_rework_declaration()["texts"]
    values = {"round": round_num, "review_id": anchor["verdict_id"], "reviewed": anchor["reviewed_return_record_ref"] or "?",
              "points": "; ".join(focus) if focus else "(返工节无具体项)",
              "criteria": nb_texts["criteria"].format(criteria=criteria) if criteria else ""}
    if evidence is not None:
        head = texts["stale_head"].format(**values, evidence=non_branch_evidence_text(nb_texts, evidence))
        stale = {"subject": governance_outputs_kind(), "rework_round": rework, "re_execution": evidence, "verdict_id": anchor["verdict_id"],
                 "verdict": anchor.get("verdict"), "verdict_at": anchor.get("verdict_at"), "audit_task_id": "",
                 "reviewed_return_record_ref": anchor["reviewed_return_record_ref"], "branch": "", "tip": "", "tree_hash": "", "reason": ""}
        return _derive_stale_re_return(
            workspace_root, task_id, fm, records, stale, claimer=claimer, conn_arg=conn_arg, state=state_name, stale_key=state_name,
            head=head, reviewed_ref=str(anchor["reviewed_return_record_ref"] or ""), since=str(anchor.get("verdict_at") or ""),
            reviewed_what=texts["reviewed_what"], without_round_suggested=texts["without_round_suggested"],
            without_round_note=texts["without_round_note"], not_updated_note=texts["not_updated_note"],
            re_return_notes=f"{state_name}: {head}; {texts['re_return_note']}", invalid_fix=texts["invalid_fix"],
            re_return_suggested=texts["re_return_suggested"])
    bound_note = rework_bound_note(state["bound"])
    missing = f"{texts['await_missing'].format(**values)}; {bound_note}"
    suggested = f"{texts['await_suggested'].format(**values)}; {bound_note}"
    progress = f"{texts['await_progress']}; {bound_note}"
    earlier = non_branch_re_return(workspace_root, task_id, fm, state["anchor_path"], anchor)  # 仅以复核时刻为界(同 F219 earlier_ignored)
    if earlier is not None:
        progress = f"{progress}; " + rework_return_bound_declaration()["texts"]["earlier_ignored"].format(
            evidence=non_branch_evidence_text(nb_texts, earlier), round=round_num)
    return {
        "task_id": task_id,
        "derivable": False,
        "current_node": state_name,
        "current_state": "claimed",
        "triggered_by": "executor",
        "command": "",
        "verb": "lybra_queue_return_dry_run",
        "missing_records": [missing],
        "suggested_action": suggested,
        "notes": (f"N2: 非代码快车道, 顾问复核 {anchor['verdict_id']} 打回交回 {values['reviewed']}, 已登记第 {round_num} 轮返工"
                  f"(依据 {rework.get('verdict_ref') or '?'}); {progress}(AIPOS-F221 件②, 声明 transitions nodes.N4.fast_lane_rework)"),
        "rework_round": rework,
        "action": {"type": "await_rework", "card": task_id, "round": round_num},
    }


def _derive_stale_re_return(workspace_root: Path, task_id: str, fm: dict[str, Any], records: dict[str, Any],
                            stale: dict[str, Any], *, claimer: str, conn_arg: str | None, state: str, stale_key: str, head: str,
                            reviewed_ref: str, since: str | None, reviewed_what: str, without_round_suggested: str,
                            without_round_note: str, not_updated_note: str, re_return_notes: str,
                            invalid_fix: str | None = None, re_return_suggested: str | None = None) -> dict[str, Any]:
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
                "suggested_action": invalid_fix or (f"执行体把 Return frontmatter 更新为卡分支当前 tip(commit_sha={tip}, tree_hash={stale.get('tree_hash')}), "
                                                    "产品随后重交回并派复审"),
                "notes": f"{state}: {head}; {not_updated_note}",
                "action": {"type": "artifact_invalid", "card": task_id, "path": str(check.get("path") or "")}}
    if not claimer:
        return _not_derivable_no_claim(task_id, node=state, state="claimed", verb="lybra_queue_return_dry_run",
                                       triggered_by="executor", notes=f"{state}: {head}; 无 claim 记录, 重交回 actor 无据(AIPOS-F73E 件①)")
    return_path = Path(str(check["path"]))
    summary = _extract_return_summary(workspace_root, task_id) or ""  # AIPOS-F148 件②: 摘要来源读声明(同一解析)
    step = _return_submission_step(
        workspace_root, task_id, fm, claimer=claimer, conn_arg=conn_arg, result_summary=summary, return_path=return_path,
        node=state,
        suggested_action=re_return_suggested or "重交回(Return 已是卡分支当前 tip): 门落新 return 记录并按卡号演进声明派下一轮审计",
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


# ---------------------------------------------------------------------------
# AIPOS-F179 item 3: audit verdict BLOCK = audit could not complete (environment / resource / duration).
# Declaration transitions.schema nodes.N4.audit_blocked; judged here in the N4 non-PASS branch (next to F155 rework).
# ---------------------------------------------------------------------------

_AUDIT_BLOCKED_KEYS = ("verdicts", "action_type", "end_reason", "repeated_end_reason", "owner_needed_after", "reason_excerpt_chars",
                       "dispatch_reason", "owner_reaudit")


def audit_blocked_declaration() -> dict[str, Any]:
    """transitions nodes.N4.audit_blocked (single reader). Missing keys = SchemaLoadError (fail-closed)."""
    from tools.schema_loader import SchemaLoadError

    decl = _transition_node("N4").get("audit_blocked")
    if not isinstance(decl, dict) or any(k not in decl for k in _AUDIT_BLOCKED_KEYS):
        raise SchemaLoadError(f"transitions.schema.json nodes.N4.audit_blocked 未声明齐 {list(_AUDIT_BLOCKED_KEYS)}")
    if not isinstance(decl["owner_needed_after"], int) or isinstance(decl["owner_needed_after"], bool) or decl["owner_needed_after"] < 1:
        raise SchemaLoadError("transitions.schema.json nodes.N4.audit_blocked.owner_needed_after 须为 ≥1 整数")
    reaudit = decl["owner_reaudit"]
    if (not isinstance(reaudit, dict) or not isinstance(reaudit.get("choices"), list) or not reaudit["choices"]
            or not all(isinstance(c, str) and c.strip() for c in reaudit["choices"])
            or not isinstance(reaudit.get("dispatch_reason"), str) or not reaudit["dispatch_reason"].strip()):
        raise SchemaLoadError("transitions.schema.json nodes.N4.audit_blocked.owner_reaudit 须有非空 choices(选项键)与 dispatch_reason")
    # AIPOS-F213 件③(F208 P1-8 选项): choices 须为收件箱对连续 BLOCK 事由恒给的选项(pinned_options_by_reason[repeated_end_reason]);
    # 一侧改名 = 声明加载即红(原只校验非空, 收件箱改名后 Owner 裁决不再命中、重新派审静默失效)。校验唯一实现 queue_mutation.require_inbox_choices
    from tools.aipos_cli.queue_mutation import require_inbox_choices

    require_inbox_choices(reaudit["choices"], "transitions nodes.N4.audit_blocked.owner_reaudit.choices",
                          pinned=("loop_owner_needed", str(decl["repeated_end_reason"])))
    return decl


def owner_reaudit_ruling(workspace_root: Path, task_id: str) -> dict[str, Any] | None:
    """AIPOS-F202: the card's latest Owner inbox ruling whose choice is in nodes.N4.audit_blocked.owner_reaudit.choices
    (owner_inbox.rulings_for_task, the inbox's own reader; ordered by decided_at) or None. A record that cannot be read
    = FrontmatterReadError upward (fail-closed); a picked ruling without a readable decided_at = FrontmatterReadError naming it.
    AIPOS-F225 item 2: a non-revoked tier-2 auto re-audit record (owner_inbox.auto_release_records, kind = nodes.N4.audit_blocked.auto_reaudit.kind)
    is a boundary of the same kind; the later of the two wins (auto = True, choice = auto_reaudit.choice)."""
    from tools.aipos_cli.frontmatter import FrontmatterReadError
    from tools.aipos_cli.owner_inbox import _parse_utc, auto_release_records, revoked_auto_release_ids, rulings_for_task

    choices = {str(c) for c in audit_blocked_declaration()["owner_reaudit"]["choices"]}
    picked = [r for r in rulings_for_task(Path(workspace_root), task_id) if str(r.get("allowed_next_action") or "") in choices]
    candidates: list[dict[str, Any]] = []
    if picked:
        ruling = picked[-1]
        decided = _parse_utc(ruling.get("decided_at"))
        if decided is None:
            raise FrontmatterReadError(ruling.get("_path") or ruling.get("decision_id"), "收件箱裁决 decided_at 读不出(连续 BLOCK 计数的界, AIPOS-F202)")
        candidates.append({"decision_id": str(ruling.get("decision_id") or ""), "choice": str(ruling.get("allowed_next_action") or ""),
                           "decided_at": str(ruling.get("decided_at") or ""), "decided": decided, "record": ruling.get("_path"),
                           "owner_words": ruling.get("owner_words"), "relayed_by": ruling.get("relayed_by"), "auto": False})
    auto_decl = auto_reaudit_declaration()
    autos = [r for r in auto_release_records(Path(workspace_root), task_id) if str(r.get("kind") or "") == str(auto_decl["kind"])]
    if autos:
        revoked = revoked_auto_release_ids(Path(workspace_root), task_id)
        live = [r for r in autos if str(r.get("auto_release_id") or "") not in revoked]
        if live:
            rec = live[-1]
            decided = _parse_utc(rec.get("released_at"))
            if decided is None:
                raise FrontmatterReadError(rec.get("_path"), "自动放行记录 released_at 读不出(连续 BLOCK 计数的界, AIPOS-F225)")
            candidates.append({"decision_id": str(rec.get("auto_release_id") or ""), "choice": str(auto_decl["choice"]),
                               "decided_at": str(rec.get("released_at") or ""), "decided": decided, "record": rec.get("_path"),
                               "owner_words": None, "relayed_by": None, "auto": True})
    if not candidates:
        return None
    return max(candidates, key=lambda c: c["decided"])


_AUTO_REAUDIT_KEYS = ("kind", "choice", "dispatch_reason", "texts")


def auto_reaudit_declaration() -> dict[str, Any]:
    """transitions nodes.N4.audit_blocked.auto_reaudit(AIPOS-F225 件②, 唯一读取口)。缺键 / kind 不在 tiers.tier2.kinds = SchemaLoadError。"""
    from tools.aipos_cli.owner_inbox import tier_declaration
    from tools.schema_loader import SchemaLoadError

    decl = audit_blocked_declaration().get("auto_reaudit")
    texts = decl.get("texts") if isinstance(decl, dict) else None
    if (not isinstance(decl, dict) or any(k not in decl for k in _AUTO_REAUDIT_KEYS) or not isinstance(texts, dict)
            or any(not str(texts.get(k) or "").strip() for k in ("basis", "no_baseline", "not_comparable", "already", "head", "unchanged"))):
        raise SchemaLoadError(f"transitions.schema.json nodes.N4.audit_blocked.auto_reaudit 未声明齐 {list(_AUTO_REAUDIT_KEYS)}(AIPOS-F225)")
    if str(decl["kind"]) not in tier_declaration()["tier2"]["kinds"]:
        raise SchemaLoadError(f"nodes.N4.audit_blocked.auto_reaudit.kind {decl['kind']!r} 不在 verbs.schema lybra_owner_inbox.tiers.tier2.kinds")
    return decl


def validation_roots_digest(workspace_root: Path) -> str:
    """AIPOS-F225 件②: 项目验证根声明(validation_root.project_validation_roots 唯一读取口)规范 JSON 的 sha256 前 16 位。读不出 = 向上。"""
    import hashlib
    import json

    from tools.aipos_cli.validation_root import project_validation_roots

    roots = project_validation_roots(Path(workspace_root))
    return hashlib.sha256(json.dumps(roots, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]


def current_env(workspace_root: Path, deploy: dict[str, Any] | None = None) -> dict[str, Any]:
    """AIPOS-F225 件②: 当前环境指纹 {deploy_commit, validation_roots_digest}(部署 = 本进程运行代码 deploy_runtime.DeployGuard 同一描述;
    验证根 = validation_roots_digest)。读不出的一项 = None(比对时不可比, 不猜)。"""
    if deploy is None:
        from tools.aipos_cli.deploy_runtime import DeployGuard

        deploy = DeployGuard.for_running_process().describe()
    try:
        digest: str | None = validation_roots_digest(Path(workspace_root))
    except (OSError, ValueError) as exc:  # 验证根声明读不出 = 该项不可比(原因随结果给出)
        digest = None
        deploy = {**deploy, "validation_roots_error": f"{type(exc).__name__}: {exc}"}
    return {"deploy_commit": str((deploy or {}).get("commit") or "") or None, "validation_roots_digest": digest}


def auto_reaudit_check(workspace_root: Path, task_id: str, *, env: dict[str, Any] | None = None) -> dict[str, Any]:
    """AIPOS-F225 件② 唯一判定(loop_driver 自动重新派审与 loop status 下一动作同读): 本卡审计连续 BLOCK(audit_block_state repeated)时,
    自最新 BLOCK 裁决起部署提交或验证根声明是否有变化。基线 = 该 BLOCK 裁决时刻之后最早结束的本卡 loop 运行记录(deploy.commit / env);
    现值 = env(缺省 = current_env 本进程)。返回 {eligible, revoked, verdict_id, audit_task_id, changes, why, baseline, current, state}。
    同一 BLOCK 裁决已有自动放行记录: 已撤回 = revoked(卡停住另判), 未撤回 = 不再自动(already)。记录读不出 = 向上。"""
    from tools.aipos_cli.loop_driver import load_loop_contract
    from tools.aipos_cli.loop_run_record import _run_files, loop_runs_dir, read_run, run_record_declaration
    from tools.aipos_cli.owner_inbox import _parse_utc, auto_release_records, revoked_auto_release_ids, tier_declaration

    decl = auto_reaudit_declaration()
    texts = decl["texts"]
    out: dict[str, Any] = {"eligible": False, "revoked": False, "verdict_id": None, "audit_task_id": None, "changes": "", "why": "",
                           "baseline": None, "current": None}
    state = audit_block_state(Path(workspace_root), task_id)
    if state is None or not state.get("repeated"):
        out["why"] = "not_repeated"
        return out
    out.update({"verdict_id": state["verdict_id"], "audit_task_id": state["audit_task_id"]})
    prior = [r for r in auto_release_records(Path(workspace_root), task_id)
             if str(r.get("kind") or "") == str(decl["kind"]) and str(r.get("verdict_ref") or "") == str(state["verdict_id"])]
    if prior:
        rid = str(prior[-1].get("auto_release_id") or "")
        out["revoked"] = rid in revoked_auto_release_ids(Path(workspace_root), task_id)
        out["why"] = str(texts["already"]).format(record_id=rid)
        return out
    regular = _regular_verdicts(Path(workspace_root), task_id)
    moment = _verdict_moment(*regular[0]) if regular else None
    rdecl = run_record_declaration(load_loop_contract())
    runs = [read_run(p, rdecl) for p in _run_files(loop_runs_dir(Path(workspace_root), task_id))]
    ended = [(_parse_utc(m.get("ended_at")), m) for m in runs if m.get("status") == "ended"]
    after = [m for at, m in sorted((e for e in ended if e[0] is not None), key=lambda e: e[0]) if moment is not None and at >= moment]
    if not after:
        out["why"] = str(texts["no_baseline"])
        return out
    base_run = after[0]
    baseline = {"deploy_commit": str((base_run.get("deploy") or {}).get("commit") or "") or None,
                "validation_roots_digest": str((base_run.get("env") or {}).get("validation_roots_digest") or "") or None,
                "run_id": base_run.get("run_id")}
    cur = current_env(Path(workspace_root)) if env is None else env
    out.update({"baseline": baseline, "current": cur})
    tt = tier_declaration()["texts"]
    changes: list[str] = []
    comparable = False
    for key, text_key in (("deploy_commit", "change_deploy"), ("validation_roots_digest", "change_validation_roots")):
        before, after_v = baseline.get(key), cur.get(key)
        if before and after_v:
            comparable = True
            if before != after_v:
                changes.append(str(tt[text_key]).format(before=str(before)[:12], after=str(after_v)[:12]))
    if not comparable:
        out["why"] = str(texts["not_comparable"])
        return out
    out["eligible"] = bool(changes)
    out["changes"] = "; ".join(changes)
    out["why"] = out["changes"] or str(texts["unchanged"]).format(run_id=baseline.get("run_id") or "?",
                                                                  deploy=str(cur.get("deploy_commit") or "-")[:12],
                                                                  digest=str(cur.get("validation_roots_digest") or "-"))
    out["state"] = state
    return out


def _verdict_moment(path: Path, fm: dict[str, Any]) -> datetime:
    """AIPOS-F202: a verdict record's moment for the owner-reaudit boundary = frontmatter verdict_at, else the record mtime (UTC)."""
    from tools.aipos_cli.owner_inbox import _parse_utc

    return _parse_utc(fm.get("verdict_at")) or datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def _regular_verdicts(workspace_root: Path, task_id: str) -> list[tuple[Path, dict[str, Any]]]:
    """本卡常规(非补审)裁决记录, 新 → 旧(记录 mtime, 与 _read_task_records latest_verdict 同一排序)。audit_block_state / candidate_fail_state 共用。"""
    from tools.aipos_cli.record_writer import record_file_prefix, record_files_with_prefix

    directory = record_dir(Path(workspace_root), "audit_verdicts", task_id)
    files = sorted(record_files_with_prefix(directory, record_file_prefix("audit_verdicts")), key=lambda p: p.stat().st_mtime, reverse=True)
    regular: list[tuple[Path, dict[str, Any]]] = []
    for path in files:
        fm = _read_frontmatter(path) or {}
        if not str(fm.get("supplementary_of") or "").strip():
            regular.append((path, fm))
    return regular


def _verdict_reason_excerpt(path: Path, fm: dict[str, Any], *, workspace_root: Path | None = None) -> str:
    """裁决记录原因摘录(Findings summary / 审计结论, owner_truth_view._extract_summary_field 真相视图同一函数), 截
    transitions nodes.N4.audit_blocked.reason_excerpt_chars 字。audit_block_state / candidate_fail_state 共用。
    AIPOS-F227 件②: 记录无 Findings summary(findings_summary_present=false 即正文 not provided, 或摘要行缺)= 改读该裁决的审计报告
    裁决依据(_verdict_report_reason, 声明 transitions nodes.N4.candidate_fail.report_reason); workspace_root 缺 = 不读报告(照旧)。"""
    from tools.aipos_cli.frontmatter import FrontmatterReadError, require_frontmatter
    from tools.aipos_cli.owner_truth_view import _extract_summary_field

    try:
        _meta, body = require_frontmatter(path)
    except FrontmatterReadError:
        body = ""
    reason = " ".join((_extract_summary_field(body, ("Findings summary", "审计结论")) or "").split())
    if workspace_root is not None and (not reason or fm.get("findings_summary_present") is False):
        reason = _verdict_report_reason(Path(workspace_root), fm) or ("" if fm.get("findings_summary_present") is False else reason)
    limit = int(audit_blocked_declaration()["reason_excerpt_chars"])
    return reason[:limit] + "…" if len(reason) > limit else reason


def verdict_report(workspace_root: Path, verdict_fm: dict[str, Any]) -> dict[str, Any] | None:
    """AIPOS-F227: 裁决所依据的审计报告 = 门快照(report_snapshot_ref, 裁决入门时冻结; 复原 = board_adapter.unwrap_report_snapshot 去记录头
    并核 sha256)优先, 快照缺 / 不存在退回 report_source_ref(相对治理根); 都没有 = None。返回 {path, meta, body, problem}: 快照校验不符 /
    frontmatter 解析告警 = problem 非空(meta / body 不可信, 调用方按未声明 / 无摘录处理, 不取其值)。verdict_fail_scope 与原因摘录兜底同读。"""
    from tools.aipos_cli.board_adapter import unwrap_report_snapshot
    from tools.aipos_cli.frontmatter import parse_markdown_frontmatter

    for key in ("report_snapshot_ref", "report_source_ref"):
        ref = str(verdict_fm.get(key) or "").strip()
        path = (Path(ref) if Path(ref).is_absolute() else Path(workspace_root) / ref) if ref else None
        if path is None or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
            if key == "report_snapshot_ref":
                text = unwrap_report_snapshot(text)
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            return {"path": path, "meta": {}, "body": "", "problem": f"{path}: {exc}"}
        meta, body, warnings = parse_markdown_frontmatter(text)
        if warnings:
            return {"path": path, "meta": {}, "body": "", "problem": f"{path}: frontmatter 解析告警 {warnings}"}
        return {"path": path, "meta": meta, "body": body, "problem": ""}
    return None


def _verdict_report_reason(workspace_root: Path, verdict_fm: dict[str, Any]) -> str:
    """AIPOS-F227 件②: 审计报告裁决依据摘录(声明 nodes.N4.candidate_fail.report_reason; 切节 = card_render._split_sections 同一实现)。
    报告缺 / 读不出 / 无匹配节 = 空串。"""
    from tools.aipos_cli.card_render import _split_sections

    report = verdict_report(workspace_root, verdict_fm)
    if report is None or report["problem"]:
        return ""
    decl = candidate_fail_declaration()["report_reason"]
    sections = _split_sections(report["body"])
    for heading in decl["headings"]:
        text = next((t for h, t in sections if h and str(heading) in h), None)
        if text is None:
            continue
        lines = [ln.strip().lstrip("-*>#` ").replace("**", "").replace("__", "").strip() for ln in text.splitlines()]
        marked = next((ln for ln in lines if ln and any(str(m) in ln for m in decl["verdict_line_markers"])), None)
        picked = marked if marked is not None else " ".join(ln for ln in lines if ln)
        if picked:
            return " ".join(picked.split())
    return ""


def audit_block_state(workspace_root: Path, task_id: str) -> dict[str, Any] | None:
    """Latest regular (non-supplementary) verdict of the card is a declared BLOCK value -> state; else None.
    Order = record mtime, same as _read_task_records latest_verdict. consecutive = trailing BLOCK verdicts.
    reason = the verdict record's findings summary (owner_truth_view._extract_summary_field, the truth view's verdict reader).
    next_round = next audit round id (audit_derivation.derive_audit_task_id); redispatched_to = a later round already exists."""
    from tools.aipos_cli.audit_derivation import audit_round_ids, derive_audit_task_id

    decl = audit_blocked_declaration()
    blocked = {str(v).upper() for v in decl["verdicts"]}
    regular = _regular_verdicts(workspace_root, task_id)
    if not regular or str(regular[0][1].get("verdict") or "").strip().upper() not in blocked:
        return None
    # AIPOS-F202: the card's latest Owner "re-audit" ruling bounds the count - only BLOCK verdicts after it are counted
    # (history is not rewritten; with no such ruling the count is unchanged)
    ruling = owner_reaudit_ruling(Path(workspace_root), task_id)
    consecutive = total = 0
    for path, fm in regular:
        if str(fm.get("verdict") or "").strip().upper() not in blocked:
            break
        total += 1
        if ruling is None or _verdict_moment(path, fm) > ruling["decided"]:
            consecutive += 1
    latest_path, latest = regular[0]
    reason = _verdict_reason_excerpt(latest_path, latest, workspace_root=Path(workspace_root))
    blocked_audit = str(latest.get("audit_task_id") or "")
    redispatched, next_round = _next_audit_round(workspace_root, task_id, blocked_audit)
    return {
        "verdict": str(latest.get("verdict") or ""),
        "verdict_id": str(latest.get("verdict_id") or latest_path.stem),
        "audit_task_id": blocked_audit,
        "consecutive": consecutive,
        "consecutive_total": total,
        "repeated": consecutive >= int(decl["owner_needed_after"]),
        "owner_needed_after": int(decl["owner_needed_after"]),
        "reason": reason,
        "redispatched_to": redispatched,
        "next_round": next_round,
        "owner_reaudit": None if ruling is None else {k: ruling[k] for k in ("decision_id", "choice", "decided_at", "record", "auto")},
    }


def _next_audit_round(workspace_root: Path, task_id: str, judged_audit: str) -> tuple[str | None, str | None]:
    """(redispatched_to, next_round): 被裁审计轮之后已有更晚一轮 = (最新一轮, None); 否则 (None, 下一轮号 derive_audit_task_id)。
    audit_block_state 与 AIPOS-F218 FAIL 仅重审(non_pass_recovery)同一实现。"""
    from tools.aipos_cli.audit_derivation import audit_round_ids, derive_audit_task_id

    rounds = audit_round_ids(task_id, Path(workspace_root))
    if judged_audit in rounds and rounds.index(judged_audit) < len(rounds) - 1:
        return rounds[-1], None
    return None, derive_audit_task_id(task_id, repo_root=Path(workspace_root))


def reaudit_command(workspace_root: Path, task_id: str, card_frontmatter: dict[str, Any] | None, state: dict[str, Any], *,
                    actor: str, policy_ref: str) -> str:
    """Single rendering of the re-audit command (existing verb `lybra audit dispatch`, next audit round, dispatcher /
    envelope = the driver). Later round already dispatched = a sentence saying so (no second dispatch)."""
    from tools.aipos_cli.audit_derivation import resolve_audit_instance

    if state.get("redispatched_to"):
        return f"已派下一轮审计 {state['redispatched_to']}(无需再派)"
    fm = card_frontmatter
    if fm is None:
        path, _q = _find_task_in_queue(Path(workspace_root), task_id)
        fm = _read_frontmatter(path) if path else {}
    try:
        auditor = resolve_audit_instance(fm, workspace_root)
    except ValueError as exc:
        auditor = f"<审计实例: {exc}>"
    reason = _reaudit_dispatch_reason(state)
    return _build_audit_dispatch_command(
        task_id=task_id, actor=actor or "<驱动方实例>", agent_instance=actor or "<驱动方实例>",
        owner_policy_ref=policy_ref or "<信封 policy_id>", connection_json=None, audit_task_id=str(state.get("next_round") or ""),
        audit_agent_instance=auditor, workspace_root=Path(workspace_root), dispatch_reason=reason)


def _reaudit_dispatch_reason(state: dict[str, Any]) -> str:
    """Dispatch reason of the next audit round: nodes.N4.audit_blocked.dispatch_reason; after an Owner re-audit ruling with no
    BLOCK since it (AIPOS-F202) = owner_reaudit.dispatch_reason (names the decision record)."""
    decl = audit_blocked_declaration()
    pending = state.get("owner_reaudit") if not state.get("consecutive") else None
    template = str(decl["owner_reaudit"]["dispatch_reason"] if pending else decl["dispatch_reason"])
    if pending and pending.get("auto"):  # AIPOS-F225 item 2: tier-2 auto re-audit boundary
        template = str(auto_reaudit_declaration()["dispatch_reason"])
    if state.get("dispatch_reason_template"):  # AIPOS-F218 件②: FAIL 仅重审 = nodes.N4.non_pass_recovery.fail_dispatch_reason
        template = str(state["dispatch_reason_template"])
    values = {"blocked_audit_task_id": str(state.get("audit_task_id") or "?"), "verdict_id": str(state.get("verdict_id") or "?"),
              "decision_id": str((pending or {}).get("decision_id") or "?")}
    for key, value in values.items():
        template = template.replace("{" + key + "}", value)
    return template


_NON_PASS_RECOVERY_TEXTS = ("paths", "recommend_rework", "recommend_reaudit", "recommend_fail", "why_return_key", "why_summary",
                            "why_audit_reason", "actor_placeholder")


def non_pass_recovery_declaration() -> dict[str, Any]:
    """AIPOS-F218 件②: transitions nodes.N4.non_pass_recovery(唯一读取口)。缺键 / 形不合 = SchemaLoadError(fail-closed)。"""
    from tools.schema_loader import SchemaLoadError

    decl = _transition_node("N4").get("non_pass_recovery")
    texts = decl.get("texts") if isinstance(decl, dict) else None
    lists = ("return_keys", "return_values", "summary_prefixes", "audit_reason_markers")
    if (not isinstance(decl, dict) or not isinstance(texts, dict)
            or not all(isinstance(decl.get(k), list) and decl[k] and all(isinstance(x, str) and x for x in decl[k]) for k in lists)
            or not all(isinstance(decl.get(k), str) and decl[k].strip() for k in ("rework_command", "fail_dispatch_reason"))
            or not all(isinstance(texts.get(k), str) and texts[k].strip() for k in _NON_PASS_RECOVERY_TEXTS)):
        raise SchemaLoadError(f"transitions.schema.json nodes.N4.non_pass_recovery({list(lists)} / rework_command / fail_dispatch_reason / "
                              f"texts.{list(_NON_PASS_RECOVERY_TEXTS)})未声明齐(AIPOS-F218)")
    return decl


def _executor_blocked_reason(workspace_root: Path, task_id: str, audit_reason: str, decl: dict[str, Any]) -> str:
    """AIPOS-F218 件②: 执行产物本身是 BLOCK 报告的依据句(空 = 不是): 交回报告 frontmatter 键 ∈ return_keys 的值 ∈ return_values →
    交回摘要(_extract_return_summary, 去掉强调符)以 summary_prefixes 开头 → 裁决原因摘录含 audit_reason_markers, 按序取第一条。
    报告 frontmatter 读不出 = FrontmatterReadError 上抛(AIPOS-F100, 不取缺省)。"""
    texts = decl["texts"]
    path = find_return_artifact(workspace_root, task_id)
    if path is not None:
        ret_fm = _read_frontmatter(path, allow_missing_block=True)
        wanted = {str(v).strip().upper() for v in decl["return_values"]}
        for key in decl["return_keys"]:
            value = str(ret_fm.get(key) or "").strip()
            if value.upper() in wanted:
                return texts["why_return_key"].format(key=key, value=value)
        summary = (_extract_return_summary(workspace_root, task_id) or "").strip().lstrip("*_` #>").strip().upper()
        for prefix in decl["summary_prefixes"]:
            if summary.startswith(str(prefix).upper()):
                return texts["why_summary"].format(prefix=prefix)
    for marker in decl["audit_reason_markers"]:
        if marker in (audit_reason or ""):
            return texts["why_audit_reason"].format(marker=marker)
    return ""


def rework_command_line(workspace_root: Path, task_id: str, *, actor: str, verdict_id: str) -> str:
    """登记返工轮命令唯一渲染(既有 queue rework 缺省预演形; 声明 transitions nodes.N4.non_pass_recovery.rework_command, AIPOS-F218 件②):
    non_pass_recovery 两条可选路与 AIPOS-F227 固定候选同候选补正出口同读。actor 缺 = texts.actor_placeholder。"""
    from tools.aipos_cli.workspace_config import lybra_command_prefix

    decl = non_pass_recovery_declaration()
    return decl["rework_command"].format(prefix=lybra_command_prefix(workspace_root), task_id=task_id,
                                         actor=actor or decl["texts"]["actor_placeholder"], verdict_id=verdict_id or "?")


def non_pass_recovery(workspace_root: Path, task_id: str, card_frontmatter: dict[str, Any] | None, state: dict[str, Any], *,
                      actor: str, policy_ref: str) -> dict[str, Any]:
    """AIPOS-F218 件②: N4 非 PASS(BLOCK = audit_block_state; FAIL = 本卡最新常规裁决)未登记返工轮时给顾问的两条可选路——判定与渲染唯一实现
    (推导核 _derive_audit_blocked / FAIL 分支与 loop status 例行修法占位 audit_blocked_fix_values 同读; 声明 transitions nodes.N4.non_pass_recovery):
    a) 登记返工轮(既有 queue rework 缺省预演形)后重新执行同一卡; b) 仅重审(reaudit_command)。
    返回 {recommended(rework|reaudit), executor_blocked, why, rework_command, reaudit_command, choice, paths}。"""
    decl = non_pass_recovery_declaration()
    texts = decl["texts"]
    blocked = {str(v).upper() for v in audit_blocked_declaration()["verdicts"]}
    verdict = str(state.get("verdict") or "")
    why = _executor_blocked_reason(Path(workspace_root), task_id, str(state.get("reason") or ""), decl)
    rework = rework_command_line(workspace_root, task_id, actor=actor, verdict_id=str(state.get("verdict_id") or ""))
    reaudit = reaudit_command(Path(workspace_root), task_id, card_frontmatter, state, actor=actor, policy_ref=policy_ref)
    if why:
        recommended, choice = "rework", texts["recommend_rework"].format(
            why=why, owner_needed_after=audit_blocked_declaration()["owner_needed_after"])
    elif verdict.upper() in blocked:
        recommended, choice = "reaudit", texts["recommend_reaudit"]
    else:
        recommended, choice = "rework", texts["recommend_fail"].format(verdict=verdict)
    return {"recommended": recommended, "executor_blocked": bool(why), "why": why, "rework_command": rework, "reaudit_command": reaudit,
            "choice": choice, "paths": texts["paths"].format(rework_command=rework, reaudit_command=reaudit)}


def _non_pass_fail_recovery(workspace_root: Path, task_id: str, fm: dict[str, Any], *, conn_arg: str | None) -> dict[str, Any] | None:
    """AIPOS-F218 件②: 推导核 N4 FAIL(未登记返工轮)分支的两条可选路(non_pass_recovery; 返工命令 / 重审命令的 --actor 与信封 = 驱动方,
    同 _derive_audit_blocked)。非 FAIL 类 = None。"""
    state = non_pass_fail_state(workspace_root, task_id)
    if state is None:
        return None
    return non_pass_recovery(workspace_root, task_id, fm, state, actor=_driver_actor(workspace_root, connection_json=conn_arg),
                             policy_ref=_driver_envelope_ref(workspace_root, task_id, fm, conn_arg) or "")


def non_pass_fail_state(workspace_root: Path, task_id: str) -> dict[str, Any] | None:
    """AIPOS-F218 件②: 本卡最新常规裁决非 PASS 族且非 BLOCK(audit_block_state 管)时的仅重审依据 {verdict, verdict_id, audit_task_id, reason,
    redispatched_to, next_round, dispatch_reason_template}(派审理由 = nodes.N4.non_pass_recovery.fail_dispatch_reason); 否则 None。"""
    regular = _regular_verdicts(workspace_root, task_id)
    if not regular:
        return None
    path, latest = regular[0]
    value = str(latest.get("verdict") or "").strip()
    blocked = {str(v).upper() for v in audit_blocked_declaration()["verdicts"]}
    if not value or value in _PASS_VERDICTS or value.upper() in blocked:
        return None
    audit_id = str(latest.get("audit_task_id") or "")
    redispatched, next_round = _next_audit_round(workspace_root, task_id, audit_id)
    return {"verdict": value, "verdict_id": str(latest.get("verdict_id") or path.stem), "audit_task_id": audit_id,
            "reason": _verdict_reason_excerpt(path, latest, workspace_root=Path(workspace_root)), "redispatched_to": redispatched,
            "next_round": next_round,
            "dispatch_reason_template": non_pass_recovery_declaration()["fail_dispatch_reason"]}


def audit_blocked_fix_values(workspace_root: Path, task_id: str, *, actor: str, policy_ref: str) -> dict[str, str]:
    """Placeholder values {block_reason, reaudit_command, recovery_choice, rework_command} for loop status routine fix of end reason
    audit_blocked (verbs.schema lybra_loop_status.next_action.routine_rejections.placeholders). AIPOS-F218 item 2: the two recovery
    paths come from next_resolver.non_pass_recovery (same judgement as `lybra next`)."""
    state = audit_block_state(Path(workspace_root), task_id)
    if state is None:
        gone = "(本卡最新裁决已不是 BLOCK)"
        hint = f"无需重审; 先看 lybra next --task-id {task_id} --workspace-root {workspace_root}"
        return {"block_reason": gone, "reaudit_command": hint, "recovery_choice": gone, "rework_command": hint}
    path, _q = _find_task_in_queue(Path(workspace_root), task_id)
    fm = _read_frontmatter(path) if path else {}
    recovery = non_pass_recovery(Path(workspace_root), task_id, fm, state, actor=actor, policy_ref=policy_ref)
    return {"block_reason": state["reason"] or "(裁决记录无 Findings summary, 见审计报告)", "reaudit_command": recovery["reaudit_command"],
            "recovery_choice": recovery["choice"], "rework_command": recovery["rework_command"]}


_FAIL_SCOPE_TEXTS = ("audit_rule", "criterion", "scope_declared", "scope_undeclared", "scope_invalid", "invalid_refusal", "rework_path",
                     "deliverable_alt", "candidate_declared_no_alt", "deliverable_suggested", "deliverable_notes")


def candidate_fail_declaration() -> dict[str, Any]:
    """AIPOS-F211 件④: transitions nodes.N4.candidate_fail(唯一读取口)。缺键 = SchemaLoadError(fail-closed)。
    AIPOS-F227: 另核 fail_scope(report_key / values 含 default / 补正出口 action_type 与 end_reason / texts)与 report_reason 声明齐。"""
    from tools.schema_loader import SchemaLoadError

    decl = _transition_node("N4").get("candidate_fail")
    if (not isinstance(decl, dict) or not isinstance(decl.get("verdicts"), list) or not decl["verdicts"]
            or not all(isinstance(decl.get(k), str) and decl[k].strip() for k in ("action_type", "end_reason"))):
        raise SchemaLoadError("transitions.schema.json nodes.N4.candidate_fail(verdicts / action_type / end_reason)未声明齐(AIPOS-F211)")
    scope = decl.get("fail_scope")
    texts = scope.get("texts") if isinstance(scope, dict) else None
    values = scope.get("values") if isinstance(scope, dict) else None
    reason = decl.get("report_reason")
    if (not isinstance(scope, dict) or not isinstance(texts, dict) or not isinstance(values, dict)
            or set(values) != {"candidate", "deliverable"} or not all(isinstance(v, str) and v.strip() for v in values.values())
            or scope.get("default") not in values
            or not all(isinstance(scope.get(k), str) and scope[k].strip()
                       for k in ("report_key", "deliverable_action_type", "deliverable_end_reason"))
            or scope["deliverable_end_reason"] == decl["end_reason"] or scope["deliverable_action_type"] == decl["action_type"]
            or not all(isinstance(texts.get(k), str) and texts[k].strip() for k in _FAIL_SCOPE_TEXTS)
            or not isinstance(reason, dict)
            or not all(isinstance(reason.get(k), list) and reason[k] and all(isinstance(x, str) and x for x in reason[k])
                       for k in ("headings", "verdict_line_markers"))):
        raise SchemaLoadError("transitions.schema.json nodes.N4.candidate_fail.fail_scope(report_key / values{candidate, deliverable} / "
                              f"default / deliverable_action_type / deliverable_end_reason / texts.{list(_FAIL_SCOPE_TEXTS)})与 "
                              "report_reason(headings / verdict_line_markers)未声明齐(AIPOS-F227)")
    return decl


def candidate_fail_action_types() -> tuple[str, str]:
    """AIPOS-F227: 固定候选 FAIL 两种出口的 action.type(candidate_fail / 同候选补正), loop 结束原因按 action.end_reason 记。"""
    decl = candidate_fail_declaration()
    return str(decl["action_type"]), str(decl["fail_scope"]["deliverable_action_type"])


def candidate_fail_end_reasons() -> tuple[str, str]:
    """AIPOS-F227: 固定候选 FAIL 两种出口的 loop 结束原因(candidate_fail / 同候选补正); loop status 例行修法占位同读。"""
    decl = candidate_fail_declaration()
    return str(decl["end_reason"]), str(decl["fail_scope"]["deliverable_end_reason"])


def fail_scope_audit_rule() -> str:
    """AIPOS-F227 件①: 审计侧「FAIL 时写 fail_scope」说明唯一渲染(texts.audit_rule, 值释义与判据读声明); 审计卡取证锚点
    (audit_derivation.build_forensic_anchor_section)与审计开工稿(subagent_kickoff)同读, 只对交付主体为固定候选的被审卡出。"""
    scope = candidate_fail_declaration()["fail_scope"]
    texts = scope["texts"]
    return texts["audit_rule"].format(candidate=scope["values"]["candidate"], deliverable=scope["values"]["deliverable"],
                                      criterion=texts["criterion"])


def fail_scope_problem(report_fm: dict[str, Any] | None, *, path: Path | str = "") -> str | None:
    """AIPOS-F227 件①: 审计报告 frontmatter 的 fail_scope 值核对(裁决入口唯一判定): 未写 = None; 值不在声明 values 内 = 拒因一句
    (texts.invalid_refusal, 点名声明值); 合法 = None。"""
    scope = candidate_fail_declaration()["fail_scope"]
    fm = report_fm if isinstance(report_fm, dict) else {}
    if scope["report_key"] not in fm or fm.get(scope["report_key"]) in (None, ""):
        return None
    value = fm.get(scope["report_key"])
    if isinstance(value, str) and value.strip() in scope["values"]:
        return None
    return scope["texts"]["invalid_refusal"].format(path=path or "审计报告", value=repr(value), allowed="/".join(scope["values"]))


def verdict_fail_scope(workspace_root: Path, verdict_fm: dict[str, Any]) -> dict[str, Any]:
    """AIPOS-F227 件①: 一条裁决的 FAIL 范围(唯一判定)= 裁决记录自带 report_key 优先, 否则读该裁决的门快照报告(verdict_report)。
    返回 {scope(candidate|deliverable), declared(bool), value, note}; 未声明 / 值不合 / 报告不可信 = default(candidate, 现行为)且
    declared=False, note 写明(fail-closed: 不据不可信报告放宽到同候选补正)。"""
    decl = candidate_fail_declaration()["fail_scope"]
    texts, key = decl["texts"], decl["report_key"]
    value: Any = verdict_fm.get(key)
    if value in (None, ""):
        report = verdict_report(Path(workspace_root), verdict_fm)
        if report is not None and report["problem"]:  # 报告不可信(快照校验不符 / 解析告警)= 不取其值, 按 default 并点名
            return {"scope": decl["default"], "declared": False, "value": None,
                    "note": texts["scope_invalid"].format(value=f"(报告读不出: {report['problem']})", allowed="/".join(decl["values"]))}
        value = report["meta"].get(key) if report is not None else None
    if value in (None, ""):
        return {"scope": decl["default"], "declared": False, "value": None, "note": texts["scope_undeclared"]}
    if not (isinstance(value, str) and value.strip() in decl["values"]):
        return {"scope": decl["default"], "declared": False, "value": value,
                "note": texts["scope_invalid"].format(value=repr(value), allowed="/".join(decl["values"]))}
    scope = value.strip()
    return {"scope": scope, "declared": True, "value": scope,
            "note": texts["scope_declared"].format(scope=scope, meaning=decl["values"][scope])}


def candidate_fail_state(workspace_root: Path, task_id: str, card_frontmatter: dict[str, Any] | None) -> dict[str, Any] | None:
    """AIPOS-F211 件④(gap #217): 交付主体为固定候选的卡(task_mode_subject_is_candidate)最新常规裁决值 ∈ nodes.N4.candidate_fail.verdicts
    = {verdict, verdict_id, audit_task_id, reason, source, candidate, fail_scope}; 否则 None。来源卡 / 候选读 card_candidate_subject(卡面不合 = 只给空串, 不拒)。
    AIPOS-F227 件①: fail_scope = verdict_fail_scope(该裁决); 原因摘录 = 记录 Findings summary, 缺则审计报告裁决依据。"""
    fm = card_frontmatter if isinstance(card_frontmatter, dict) else {}
    if not task_mode_subject_is_candidate(str(fm.get("task_mode") or "")):
        return None
    decl = candidate_fail_declaration()
    failing = {str(v).upper() for v in decl["verdicts"]}
    regular = _regular_verdicts(workspace_root, task_id)
    if not regular or str(regular[0][1].get("verdict") or "").strip().upper() not in failing:
        return None
    path, latest = regular[0]
    try:
        subject = card_candidate_subject(fm) or {}
    except CandidateSubjectError:
        subject = {}
    return {"verdict": str(latest.get("verdict") or ""), "verdict_id": str(latest.get("verdict_id") or path.stem),
            "audit_task_id": str(latest.get("audit_task_id") or ""),
            "reason": _verdict_reason_excerpt(path, latest, workspace_root=Path(workspace_root)),
            "source": str(subject.get("source") or ""), "candidate": str(subject.get("candidate") or ""),
            "fail_scope": verdict_fail_scope(Path(workspace_root), latest)}


def _candidate_rework_path(state: dict[str, Any], rework_command: str) -> str:
    """AIPOS-F227 件②: 同候选补正路径一句(texts.rework_path; 推导核与 loop status 例行修法同一渲染)。"""
    texts = candidate_fail_declaration()["fail_scope"]["texts"]
    return texts["rework_path"].format(rework_command=rework_command, candidate=state.get("candidate") or "?")


def candidate_deliverable_exit(state: dict[str, Any], rework_command: str) -> str:
    """AIPOS-F227 件①②: candidate_fail 出口附带的同候选补正说明——未声明 fail_scope = 另一出口并列(texts.deliverable_alt, 带判据);
    已声明 candidate = 一句「不适用」; deliverable = 补正路径本身。推导核与 loop status 例行修法同读。"""
    texts = candidate_fail_declaration()["fail_scope"]["texts"]
    scope = state.get("fail_scope") or {}
    if scope.get("scope") == "deliverable":
        return _candidate_rework_path(state, rework_command)
    if scope.get("declared"):
        return texts["candidate_declared_no_alt"]
    return texts["deliverable_alt"].format(criterion=texts["criterion"], rework_path=_candidate_rework_path(state, rework_command))


def candidate_fail_fix_values(workspace_root: Path, task_id: str, *, actor: str = "") -> dict[str, str]:
    """AIPOS-F211 件④: loop status 例行修法 end_reasons.candidate_fail 的占位实值 {fail_reason, source_task, supersede_command}
    (verbs.schema lybra_loop_status.next_action.routine_rejections.placeholders)。撤换命令 = 既有动词 `lybra queue supersede` 预演形。
    AIPOS-F227: 另给 {fail_scope_note, deliverable_exit, rework_command}(end_reasons.candidate_fail / candidate_deliverable_fail 同读;
    返工命令唯一渲染 rework_command_line, --actor = 运行记录 driver)。"""
    path, _q = _find_task_in_queue(Path(workspace_root), task_id)
    fm = _read_frontmatter(path) if path else {}
    state = candidate_fail_state(Path(workspace_root), task_id, fm)
    if state is None:
        note = f"(本卡此刻最新裁决已不是固定候选 FAIL, 无需撤换; 先看 lybra next --task-id {task_id} --workspace-root {workspace_root})"
        return {"fail_reason": note, "source_task": note, "supersede_command": note, "fail_scope_note": note, "deliverable_exit": note,
                "rework_command": note}
    reason = (f"裁决 {state['verdict']} {state['verdict_id']}(审计卡 {state['audit_task_id'] or '?'}): "
              f"{state['reason'] or '(裁决记录无 Findings summary, 见审计报告)'}")
    source = f"来源卡 {state['source'] or '(卡面缺 validation_source_task)'}(本卡 FAIL 的固定候选 {state['candidate'] or '?'})"
    from tools.aipos_cli.workspace_config import lybra_command_prefix

    supersede = (f"{lybra_command_prefix(workspace_root)} queue supersede --task-id {task_id} --by <新验证卡号> "
                 f"--actor <你的顾问实例> --owner-words \"<Owner 原话摘要>\" --dry-run")
    rework = rework_command_line(Path(workspace_root), task_id, actor=actor, verdict_id=state["verdict_id"])
    return {"fail_reason": reason, "source_task": source, "supersede_command": supersede, "fail_scope_note": state["fail_scope"]["note"],
            "deliverable_exit": candidate_deliverable_exit(state, rework), "rework_command": rework}


def _derive_candidate_fail(task_id: str, state: dict[str, Any], recovery: dict[str, Any] | None) -> dict[str, Any]:
    """AIPOS-F211 件④: N4 固定候选 FAIL → 不可推导, action candidate_fail(advisor_fix: 来源卡修复出新候选 → 另出验证卡 → queue supersede
    本卡); 不计连败, 不进 Owner 收件箱(loop 以 end_reason 结束, 修法读 verbs.schema routine_rejections.end_reasons.candidate_fail)。
    AIPOS-F227 件①②: fail_scope=deliverable = 同候选补正出口(_derive_candidate_deliverable_fail); 未声明 = candidate_fail 兼容 + 同候选补正
    另一出口并列(candidate_deliverable_exit); 返工命令 = recovery.rework_command(non_pass_recovery 同一渲染, --actor = 驱动方)。"""
    decl = candidate_fail_declaration()
    rework = str((recovery or {}).get("rework_command") or "")
    if state["fail_scope"]["scope"] == "deliverable":
        return _derive_candidate_deliverable_fail(task_id, state, rework)
    exit_note = candidate_deliverable_exit(state, rework)
    return {
        "task_id": task_id,
        "derivable": False,
        "current_node": "audit_verdict",
        "current_state": "claimed",
        "triggered_by": "advisor",
        "command": "",
        "verb": "",
        "missing_records": [],
        "suggested_action": (f"固定候选 {state['candidate'] or '?'} 审计 {state['verdict']}(裁决 {state['verdict_id']}), {state['fail_scope']['note']}: "
                             f"候选本身不合格, 本卡不返工 —— "
                             f"来源卡 {state['source'] or '?'} 修复出新候选 → 另出验证卡承接新候选 → lybra queue supersede 本卡(--by 新验证卡); "
                             f"原因: {state['reason'] or '(见审计报告)'}"
                             + ("" if state["fail_scope"]["declared"] else f"; {exit_note}")),
        "notes": (f"N4: 固定候选验证卡裁决 {state['verdict']} = 候选不合格, 出口 = 新候选另出验证卡 + 撤换本卡, 不计连败、不升 Owner"
                  "(AIPOS-F211 件④, 声明 transitions nodes.N4.candidate_fail)"),
        "candidate_fail": state,
        "action": {"type": str(decl["action_type"]), "card": task_id, "end_reason": str(decl["end_reason"]),
                   "verdict_id": state["verdict_id"], "audit_task_id": state["audit_task_id"],
                   "fail_scope": state["fail_scope"]["scope"], "fail_scope_declared": state["fail_scope"]["declared"],
                   "rework_command": rework},
    }


def _derive_candidate_deliverable_fail(task_id: str, state: dict[str, Any], rework: str) -> dict[str, Any]:
    """AIPOS-F227 件②: N4 固定候选 FAIL 且 fail_scope=deliverable → 不可推导, action candidate_deliverable_fail(advisor_fix: 登记返工轮同候选
    补交付物 → loop 拉起执行体重写交回 → 重交回派下一轮审计; 复用 F218 / F219 非卡分支返工路径); 不计连败, 不进 Owner 收件箱
    (修法读 verbs.schema routine_rejections.end_reasons.<deliverable_end_reason>)。"""
    scope = candidate_fail_declaration()["fail_scope"]
    texts = scope["texts"]
    return {
        "task_id": task_id,
        "derivable": False,
        "current_node": "audit_verdict",
        "current_state": "claimed",
        "triggered_by": "advisor",
        "command": "",
        "verb": "",
        "missing_records": [],
        "suggested_action": texts["deliverable_suggested"].format(
            candidate=state["candidate"] or "?", verdict=state["verdict"], verdict_id=state["verdict_id"],
            scope_note=state["fail_scope"]["note"], rework_path=_candidate_rework_path(state, rework),
            reason=state["reason"] or "(见审计报告)"),
        "notes": texts["deliverable_notes"].format(verdict=state["verdict"]),
        "candidate_fail": state,
        "action": {"type": str(scope["deliverable_action_type"]), "card": task_id, "end_reason": str(scope["deliverable_end_reason"]),
                   "verdict_id": state["verdict_id"], "audit_task_id": state["audit_task_id"], "fail_scope": "deliverable",
                   "fail_scope_declared": True, "rework_command": rework},
    }


# ---------------------------------------------------------------------------
# AIPOS-F230 件①: 门只守底线、不规定路线——每个非通过状态同时给出全部合法出口(声明 transitions nodes.N4.legal_exits;
# 命令全部复用既有唯一构造: rework_command_line / review_rework_command / reaudit_command / 声明模板)
# ---------------------------------------------------------------------------

_LEGAL_EXIT_TEXTS = ("line", "item", "owner_part", "dropped", "fast_lane_no_reaudit", "rework_limit_note", "advisor_placeholder",
                     "budget_unknown", "resubmit", "resubmit_who_pi", "resubmit_who_subagent", "resubmit_who_unknown", "withdraw_gate",
                     "withdraw_need_owner", "withdraw_no_owner", "gate_part")
#: 渲染器名全集(声明 exits.<出口>.renderer 只得取其一); 模板型读 command_templates 同名键
#: AIPOS-F236: resubmit = 交回入门被拒后修产物重交(产物入口命令 ingest_command 唯一构造 + 谁来修)
_EXIT_RENDERERS = ("rework", "same_round", "reaudit", "supersede", "amend", "withdraw", "owner_ask", "resubmit")
_EXIT_TEMPLATE_RENDERERS = ("same_round", "supersede", "amend", "withdraw", "owner_ask")
#: 同主体返工出口在返工额度用尽时改判 rework_limit 的状态(返工轮计入既有上限)
_REWORK_LIMITED_STATES = ("FAIL", "BLOCK", "candidate_fail", "candidate_deliverable_fail", "fast_lane_review_fail", "audit_blocked_repeated")


def legal_exits_declaration() -> dict[str, Any]:
    """AIPOS-F230 件①: transitions nodes.N4.legal_exits(唯一读取口)。核: 不变式 / 出口(label / invariants ⊆ 不变式 / owner_tier1 ∈
    verbs.schema 第一档事由或 null / renderer ∈ _EXIT_RENDERERS)/ 模板齐 / 状态表每行主体键 = 交付主体三态, 每格 = ≥2 个不重复已声明出口或
    {sole_reason | not_applicable: 非空说明} / recommend 每态一条且推荐出口在该态某格内 / 文案齐。不合 = SchemaLoadError(fail-closed)。"""
    from tools.aipos_cli.owner_inbox import tier_declaration
    from tools.schema_loader import SchemaLoadError

    decl = _transition_node("N4").get("legal_exits")
    where = "transitions.schema.json nodes.N4.legal_exits"
    if not isinstance(decl, dict):
        raise SchemaLoadError(f"{where} 未声明(AIPOS-F230 件①)")
    invariants, exits, states = decl.get("invariants"), decl.get("exits"), decl.get("states")
    templates, recommend, texts = decl.get("command_templates"), decl.get("recommend"), decl.get("texts")
    problems: list[str] = []
    if not isinstance(invariants, dict) or not invariants or not all(isinstance(v, str) and v.strip() for v in invariants.values()):
        problems.append("invariants 须为非空 {键: 说明}")
        invariants = {}
    tier1 = set(tier_declaration()["tier1"]["reasons"])
    if not isinstance(exits, dict) or not exits:
        problems.append("exits 须为非空对象")
        exits = {}
    for key, spec in exits.items():
        if (not isinstance(spec, dict) or not str(spec.get("label") or "").strip() or spec.get("renderer") not in _EXIT_RENDERERS
                or not isinstance(spec.get("invariants"), list) or not spec["invariants"] or not set(spec["invariants"]) <= set(invariants)
                or not (spec.get("owner_tier1") is None or spec.get("owner_tier1") in tier1)):
            problems.append(f"exits.{key} 须有 label / renderer ∈ {list(_EXIT_RENDERERS)} / invariants ⊆ invariants / owner_tier1 ∈ 第一档事由或 null")
    kinds = set(delivery_subject_kinds())
    if (not isinstance(templates, dict) or not all(isinstance(templates.get(k), str) and templates[k].strip() for k in _EXIT_TEMPLATE_RENDERERS)
            or not isinstance(templates.get("subject_hint"), dict) or set(templates["subject_hint"]) != kinds):
        problems.append(f"command_templates 须含 {list(_EXIT_TEMPLATE_RENDERERS)} 与 subject_hint(键 = 交付主体三态)")
    if not isinstance(states, dict) or not states:
        problems.append("states 须为非空对象")
        states = {}
    for state, row in states.items():
        if not isinstance(row, dict) or set(row) != kinds:
            problems.append(f"states.{state} 的主体键须恰为交付主体三态 {sorted(kinds)}")
            continue
        for subject, cell in row.items():
            if isinstance(cell, list):
                if len(cell) < 2 or len(set(cell)) != len(cell) or not set(cell) <= set(exits):
                    problems.append(f"states.{state}.{subject} 须为 ≥2 个不重复的已声明出口(或写明 sole_reason)")
            elif not (isinstance(cell, dict) and len(cell) == 1 and next(iter(cell)) in ("sole_reason", "not_applicable")
                      and isinstance(next(iter(cell.values())), str) and next(iter(cell.values())).strip()):
                problems.append(f"states.{state}.{subject} 须为出口列表或 {{sole_reason | not_applicable: 说明}}")
    if not isinstance(recommend, dict) or set(recommend) != set(states):
        problems.append("recommend 须对 states 每态各一条")
    else:
        for state, rec in recommend.items():
            cells = [c for c in (states.get(state) or {}).values() if isinstance(c, list)]
            if not isinstance(rec, dict) or not str(rec.get("why") or "").strip() or not any(rec.get("exit") in c for c in cells):
                problems.append(f"recommend.{state} 须为 {{exit: 该态某格内的出口, why}}")
    overrides = decl.get("evidence_overrides")
    if not isinstance(overrides, dict) or not all(isinstance(v, dict) and v.get("exit") in exits and str(v.get("why") or "").strip()
                                                  for v in overrides.values()):
        problems.append("evidence_overrides 须为 {名: {exit, why}}")
    if not isinstance(texts, dict) or not all(isinstance(texts.get(k), str) and texts[k].strip() for k in _LEGAL_EXIT_TEXTS):
        problems.append(f"texts 须含 {list(_LEGAL_EXIT_TEXTS)}")
    if problems:
        raise SchemaLoadError(f"{where} 未声明齐(AIPOS-F230 件①): " + "; ".join(problems))
    return decl


def rework_budget_exhausted(workspace_root: Path, task_id: str, card_frontmatter: dict[str, Any] | None) -> dict[str, Any]:
    """AIPOS-F230 件①: 下一轮返工是否会被门 queue rework 以额度拒(判据复用 queue_mutation.rework_round_budget + auto_extension_check,
    与 build_rework_round 同一组函数, 只读)。返回 {exhausted, why, error}: 额度内 / 有未消费 Owner 追加裁决 / 满足第二档自动追加 = 未尽;
    读不出 = error 非空(调用方照原状态列出口, 门照常核)。"""
    from tools.aipos_cli.frontmatter import FrontmatterReadError, require_frontmatter
    from tools.aipos_cli.queue_mutation import auto_extension_check, rework_round_budget

    fm = card_frontmatter if isinstance(card_frontmatter, dict) else {}
    rounds = fm.get("rework_rounds") if isinstance(fm.get("rework_rounds"), list) else []
    try:
        budget = rework_round_budget(Path(workspace_root), task_id, rounds)
        next_round = len(rounds) + 1
        if next_round <= budget["limit"] or budget["unconsumed"]:
            return {"exhausted": False, "why": "", "error": ""}
        path, _q = _find_task_in_queue(Path(workspace_root), task_id)
        if path is None:
            return {"exhausted": False, "why": "", "error": f"卡 {task_id} 不在队列"}
        meta, body = require_frontmatter(path)
        auto = auto_extension_check(Path(workspace_root), task_id, meta, body, next_round=next_round, max_rounds=budget["max_rounds"])
    except (OSError, ValueError, LookupError, FrontmatterReadError) as exc:
        return {"exhausted": False, "why": "", "error": f"{type(exc).__name__}: {exc}"}
    if auto.get("eligible"):
        return {"exhausted": False, "why": "", "error": ""}
    return {"exhausted": True, "why": f"已登记 {len(rounds)} 轮 ≥ 额度 {budget['limit']}; {auto.get('why') or '不满足第二档自动追加'}", "error": ""}


def _exit_state_for_reaudit(workspace_root: Path, task_id: str, verdict_state: dict[str, Any] | None) -> dict[str, Any] | None:
    """重新派审出口所需的裁决态(含 next_round / redispatched_to): 调用方已给(audit_block_state / non_pass_fail_state 形)则用之,
    否则按本卡最新常规裁决取(non_pass_fail_state, 再 audit_block_state)。"""
    st = verdict_state if isinstance(verdict_state, dict) else {}
    if "next_round" in st or "redispatched_to" in st:
        return st
    return non_pass_fail_state(Path(workspace_root), task_id) or audit_block_state(Path(workspace_root), task_id)


def _render_exit(workspace_root: Path, task_id: str, fm: dict[str, Any], renderer: str, *, subject: str, verdict_state: dict[str, Any] | None,
                 actor: str, policy_ref: str, owner_tier1: str | None, decl: dict[str, Any]) -> tuple[str | None, str]:
    """一个出口的命令(唯一渲染; 返回 (命令, 不适用原因))。命令 = 既有唯一构造(rework / reaudit)或声明模板(其余)。"""
    from tools.aipos_cli.workspace_config import lybra_command_prefix

    texts, templates = decl["texts"], decl["command_templates"]
    advisor = texts["advisor_placeholder"]
    prefix = lybra_command_prefix(Path(workspace_root))
    verdict_id = str((verdict_state or {}).get("verdict_id") or "")
    fast = fast_lane_review(workspace_root, task_id, fm)
    try:
        report_ref = str(_return_artifact_path(workspace_root, task_id).resolve().relative_to(Path(workspace_root).resolve()))
    except ValueError:
        report_ref = str(_return_artifact_path(workspace_root, task_id))
    if renderer == "rework":
        if fast:
            return review_rework_command(workspace_root, task_id, report_ref), ""
        return rework_command_line(workspace_root, task_id, actor=actor, verdict_id=verdict_id), ""
    if renderer == "resubmit":  # AIPOS-F236 件②: 修产物后重交(谁修按执行方式: pi = loop 续接, 子 agent = 顾问派)
        return texts["resubmit"].format(who=_resubmit_who(workspace_root, task_id, fm, texts),
                                        command=ingest_command(task_id, "return", Path(workspace_root), None)), ""
    if renderer == "reaudit":
        if fast:
            return None, texts["fast_lane_no_reaudit"]
        state = _exit_state_for_reaudit(workspace_root, task_id, verdict_state)
        if state is None:
            return None, "本卡无常规审计裁决, 无审计轮可重派"
        return reaudit_command(Path(workspace_root), task_id, fm, state, actor=actor, policy_ref=policy_ref), ""
    values = {"prefix": prefix, "task_id": task_id, "advisor": advisor, "report": report_ref, "owner_reason": owner_tier1 or "",
              "subject_hint": str(templates["subject_hint"].get(subject) or ""), "outcome": str(same_round_declaration()["outcome"]),
              "restricted": " --restricted" if (_find_task_in_queue(Path(workspace_root), task_id)[1] == "claimed") else ""}
    return str(templates[renderer]).format(**values), ""


def _resubmit_who(workspace_root: Path, task_id: str, fm: dict[str, Any], texts: dict[str, Any]) -> str:
    """AIPOS-F236: 修后重交出口的「谁来修」(卡 harness 唯一推导 loop_driver.card_harness: pi = loop 同会话续接执行体, 其余 = 顾问派执行子 agent)。"""
    from tools.aipos_cli.loop_driver import CardHarnessUnresolved, card_harness

    try:
        harness = card_harness(fm, Path(workspace_root), task_id=task_id, audit_card=False)
    except (CardHarnessUnresolved, ValueError, LookupError) as exc:
        return texts["resubmit_who_unknown"].format(error=f"{type(exc).__name__}: {exc}")
    if harness == "pi":
        return texts["resubmit_who_pi"]
    return texts["resubmit_who_subagent"].format(harness=harness)


def withdraw_gate_fact(workspace_root: Path, task_id: str, decl: dict[str, Any] | None = None) -> str:
    """AIPOS-F236 件②: 撤卡出口的门侧真实判定(与 queue withdraw 同源: 须带 Owner 裁决引用的源目录 queue_mutation.withdraw_owner_ruling_states,
    本卡队列状态 _find_task_in_queue)。卡不在队列 = 空串。"""
    from tools.aipos_cli.queue_mutation import withdraw_owner_ruling_states

    texts = (decl or legal_exits_declaration())["texts"]
    _path, queue = _find_task_in_queue(Path(workspace_root), task_id)
    if not queue:
        return ""
    states = withdraw_owner_ruling_states()
    need = texts["withdraw_need_owner"] if queue in states else texts["withdraw_no_owner"]
    return texts["withdraw_gate"].format(queue=queue, need=need, states="/".join(states) or "(无)")


def _exit_item(texts: dict[str, Any], e: dict[str, Any]) -> str:
    """一个出口的文本行(texts.item + 须 Owner 档位 + AIPOS-F236 门侧判定 gate_part); legal_exits.text 与 render_legal_exit_lines 同读。"""
    owner = texts["owner_part"].format(owner_tier1=e["owner_tier1"]) if e.get("owner_tier1") else ""
    line = texts["item"].format(key=e["key"], label=e["label"], owner=owner, command=e["command"])
    return line + (texts["gate_part"].format(gate=e["gate"]) if e.get("gate") else "")


def legal_exits(workspace_root: Path, task_id: str, card_frontmatter: dict[str, Any] | None, state_key: str, *,
                verdict_state: dict[str, Any] | None = None, actor: str = "", policy_ref: str = "",
                recommended: str | None = None, why: str = "") -> dict[str, Any]:
    """AIPOS-F230 件①: 本卡此刻非通过状态 state_key 的全部合法出口(唯一渲染; next 各非通过分支与 loop status 同读)。
    交付主体 = card_delivery_subject; 同主体返工所在状态若返工额度用尽(rework_budget_exhausted)= 改判 rework_limit。
    recommended / why = 调用方按证据给的推荐(审计 fail_scope / 执行产物 BLOCK 报告); 缺 = 声明 recommend.<状态>。推荐出口对本卡不适用 =
    取首个可用出口。返回 {state, subject, invariants, exits[{key, label, owner_tier1, invariants, command, note}], dropped, recommended, why,
    budget, text}; 声明格为 sole_reason / not_applicable = exits 空且 text 写明。"""
    decl = legal_exits_declaration()
    texts = decl["texts"]
    fm = card_frontmatter if isinstance(card_frontmatter, dict) else {}
    subject = str(card_delivery_subject(workspace_root, task_id, fm).get("kind") or "branch")
    state = state_key
    budget: dict[str, Any] = {}
    if state in _REWORK_LIMITED_STATES:
        budget = rework_budget_exhausted(Path(workspace_root), task_id, fm)
        if budget["exhausted"]:
            state, recommended, why = "rework_limit", None, ""
    cell = decl["states"][state][subject]
    out: dict[str, Any] = {"state": state, "requested_state": state_key, "subject": subject, "invariants": decl["invariants"],
                           "exits": [], "dropped": [], "recommended": "", "why": "", "budget": budget, "text": ""}
    if isinstance(cell, dict):
        kind, note = next(iter(cell.items()))
        out.update({kind: note, "text": f"{state} × {subject}: {note}"})
        return out
    for key in cell:
        spec = decl["exits"][key]
        command, drop = _render_exit(workspace_root, task_id, fm, str(spec["renderer"]), subject=subject, verdict_state=verdict_state,
                                     actor=actor, policy_ref=policy_ref, owner_tier1=spec.get("owner_tier1"), decl=decl)
        if command is None:
            out["dropped"].append({"key": key, "label": spec["label"], "why": drop})
            continue
        gate = withdraw_gate_fact(workspace_root, task_id, decl) if spec["renderer"] == "withdraw" else ""
        out["exits"].append({"key": key, "label": str(spec["label"]), "owner_tier1": spec.get("owner_tier1"),
                             "invariants": list(spec["invariants"]), "command": command, "note": str(spec.get("note") or ""),
                             **({"gate": gate} if gate else {})})
    keys = [e["key"] for e in out["exits"]]
    rec = decl["recommend"][state]
    pick, reason = (recommended, why) if recommended in keys else (str(rec["exit"]), str(rec["why"]))
    if pick not in keys and keys:
        pick, reason = keys[0], f"{reason}(推荐出口本卡不适用, 取首个可用出口)"
    if budget.get("exhausted"):
        reason = f"{reason}; " + texts["rework_limit_note"].format(why=budget["why"])
    elif budget.get("error"):
        reason = f"{reason}; " + texts["budget_unknown"].format(error=budget["error"])
    out["recommended"], out["why"] = pick, reason
    items = [_exit_item(texts, e) for e in out["exits"]]
    items += [texts["dropped"].format(label=d["label"], why=d["why"]) for d in out["dropped"]]
    out["text"] = texts["line"].format(state=state, subject=subject, invariants="/".join(decl["invariants"]), items=" | ".join(items),
                                       recommended=pick or "(无)", why=reason)
    return out


def recovery_exit_key(recovery: dict[str, Any] | None) -> tuple[str | None, str]:
    """non_pass_recovery 推荐(rework|reaudit)→ 出口键 + 依据(证据改判: 执行产物本身是 BLOCK 报告 = evidence_overrides.executor_blocked)。"""
    if not recovery:
        return None, ""
    if recovery.get("executor_blocked"):
        rule = legal_exits_declaration()["evidence_overrides"]["executor_blocked"]
        return str(rule["exit"]), str(rule["why"]).format(why=recovery.get("why") or "")
    return ("rework_same_subject" if recovery.get("recommended") == "rework" else "reaudit"), str(recovery.get("choice") or "")


def _attach_legal_exits(result: dict[str, Any], exits: dict[str, Any]) -> dict[str, Any]:
    """推导结果附合法出口(legal_exits 键; suggested_action 仍是推荐主路径不变——全集由 `lybra next` 文本 Exits 段(format_output)、
    --json 的 legal_exits 与 loop status next_action.legal_exits 同读本键)。"""
    result["legal_exits"] = exits
    return result


def render_legal_exit_lines(exits: dict[str, Any] | None) -> list[str]:
    """AIPOS-F230 件①: `lybra next` 文本输出的 Exits 段(每出口一行 + 推荐一行; 文案读 legal_exits.texts)。无 = []。"""
    if not isinstance(exits, dict) or not (exits.get("exits") or exits.get("dropped") or exits.get("text")):
        return []
    if not exits.get("exits"):
        return [f"Exits: {exits.get('text')}"]
    texts = legal_exits_declaration()["texts"]
    lines = [f"Exits({exits['state']} × {exits['subject']}, 门只守底线 {'/'.join(exits.get('invariants') or {})}; 顾问任选其一):"]
    for e in exits["exits"]:
        lines.append("  " + _exit_item(texts, e))
    lines += ["  " + texts["dropped"].format(label=d["label"], why=d["why"]) for d in exits.get("dropped") or []]
    lines.append(f"  推荐: {exits.get('recommended') or '(无)'}({exits.get('why') or ''})")
    return lines


def _non_pass_exits(workspace_root: Path, task_id: str, fm: dict[str, Any], state_key: str, verdict_state: dict[str, Any] | None,
                    recovery: dict[str, Any] | None, *, conn_arg: str | None) -> dict[str, Any]:
    """推导核非通过分支的合法出口(出口命令 --actor / 信封 = 驱动方, 与 non_pass_recovery 同口径; 推荐 = recovery_exit_key 证据改判)。"""
    rec, rec_why = recovery_exit_key(recovery)
    return legal_exits(workspace_root, task_id, fm, state_key, verdict_state=verdict_state,
                       actor=_driver_actor(workspace_root, connection_json=conn_arg),
                       policy_ref=_driver_envelope_ref(workspace_root, task_id, fm, conn_arg) or "", recommended=rec, why=rec_why)


def _candidate_fail_exits(workspace_root: Path, task_id: str, fm: dict[str, Any], state: dict[str, Any], derived: dict[str, Any], *,
                          conn_arg: str | None) -> dict[str, Any]:
    """固定候选 FAIL 两态的合法出口: deliverable = candidate_deliverable_fail; 未声明 fail_scope = evidence_overrides.fail_scope_undeclared 推荐。"""
    deliverable = derived["action"]["type"] == candidate_fail_action_types()[1]
    rec, why = None, ""
    if not deliverable and not state["fail_scope"]["declared"]:
        rule = legal_exits_declaration()["evidence_overrides"]["fail_scope_undeclared"]
        rec, why = str(rule["exit"]), str(rule["why"])
    return legal_exits(workspace_root, task_id, fm, "candidate_deliverable_fail" if deliverable else "candidate_fail",
                       verdict_state={"verdict_id": state["verdict_id"]}, actor=_driver_actor(workspace_root, connection_json=conn_arg),
                       policy_ref=_driver_envelope_ref(workspace_root, task_id, fm, conn_arg) or "", recommended=rec, why=why)


def legal_exits_for_card(workspace_root: Path, task_id: str) -> dict[str, Any] | None:
    """AIPOS-F230 件①: loop status 读口——本卡此刻推导(derive_next_step, 唯一推导核)若处非通过状态 = 其 legal_exits; 否则 None。
    推导出错 = {error, text}(出声, 不吞)。"""
    try:
        derivation = derive_next_step(task_id, Path(workspace_root))
    except (OSError, ValueError, LookupError) as exc:
        return {"error": f"{type(exc).__name__}: {exc}", "exits": [], "text": f"合法出口推导出错: {type(exc).__name__}: {exc}"}
    exits = derivation.get("legal_exits")
    return exits if isinstance(exits, dict) else None


# ---------------------------------------------------------------------------
# AIPOS-F230 件④: 同轮补正(声明 transitions nodes.N4.same_round + verbs.schema lybra_mark_concluded.same_round)
# ---------------------------------------------------------------------------

_SAME_ROUND_TEXTS = ("await_missing", "await_suggested", "after_audit", "after_first", "after_review", "stale_head", "re_return_note",
                     "re_return_suggested", "invalid_fix", "not_updated_note", "without_round_suggested", "without_round_note", "reviewed_what",
                     "reaudit_ref", "reaudit_note", "bound_note")
_SAME_ROUND_CODES = ("SAME_ROUND_NOT_CLAIMED", "SAME_ROUND_NO_RETURN", "SAME_ROUND_AUDIT_STARTED", "SAME_ROUND_ALREADY_PENDING",
                     "SAME_ROUND_FOCUS_REQUIRED", "SAME_ROUND_ROLE")


def same_round_declaration() -> dict[str, Any]:
    """AIPOS-F230 件④: transitions nodes.N4.same_round(推导)+ verbs.schema lybra_mark_concluded.same_round(写口)合并视图(唯一读取口)。
    outcome 不得与复核打回 outcome 相同; 缺键 = SchemaLoadError(fail-closed)。"""
    from tools.schema_loader import SchemaLoadError, load_schema

    node = _transition_node("N4").get("same_round")
    verb = ((load_schema("verbs").get("verbs") or {}).get("lybra_mark_concluded") or {}).get("same_round")
    texts = node.get("texts") if isinstance(node, dict) else None
    codes = verb.get("reject_codes") if isinstance(verb, dict) else None
    if (not isinstance(node, dict) or not str(node.get("state") or "").strip() or not str(node.get("kind") or "").strip()
            or not isinstance(texts, dict) or not all(isinstance(texts.get(k), str) and texts[k].strip() for k in _SAME_ROUND_TEXTS)
            or not isinstance(verb, dict) or not str(verb.get("outcome") or "").strip()
            or str(verb["outcome"]) == str(review_rework_declaration()["outcome"])
            or not isinstance(verb.get("record_fields"), list) or not isinstance(verb.get("audit_not_started_states"), list)
            or not isinstance(codes, dict) or not all(isinstance(codes.get(k), str) and codes[k].strip() for k in _SAME_ROUND_CODES)):
        raise SchemaLoadError(f"transitions nodes.N4.same_round(state / kind / texts.{list(_SAME_ROUND_TEXTS)})与 verbs.lybra_mark_concluded."
                              f"same_round(outcome ≠ review_rework.outcome / record_fields / audit_not_started_states / reject_codes "
                              f"{list(_SAME_ROUND_CODES)})未声明齐(AIPOS-F230 件④)")
    return {**node, **verb, "texts": texts, "reject_codes": codes}


def same_round_reviews(workspace_root: Path, task_id: str) -> list[tuple[Path, dict[str, Any]]]:
    """本卡同轮补正复核记录(advisor_reviews 同一读取, outcome = same_round), 旧 → 新。"""
    outcome = str(same_round_declaration()["outcome"])
    return [(p, fm) for p, fm in advisor_reviews(workspace_root, task_id) if str(fm.get("outcome") or "") == outcome]


def same_round_state(workspace_root: Path, task_id: str, card_frontmatter: dict[str, Any] | None,
                     records: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """AIPOS-F230 件④ 唯一判定: 最新同轮补正记录所打回的交回 = 门最新交回(补正未消费)= {review_path, review(裁决形视图), evidence,
    withdrawn_audit_task_id}; evidence = 晚于复核时刻的重写交回(non_branch_re_return; 时间界 = rework_return_bound 以复核记录为锚、无返工轮);
    已消费(门已落新交回)/ 无记录 = None。"""
    reviews = same_round_reviews(workspace_root, task_id)
    if not reviews:
        return None
    path, fm = reviews[-1]
    recs = records if isinstance(records, dict) else _read_task_records(Path(workspace_root), task_id)
    latest_id = str((recs.get("latest_return") or {}).get("return_id") or "").strip()
    reviewed = str(fm.get("reviewed_return_record_ref") or "").strip()
    if not latest_id or latest_id != reviewed:
        return None
    view = {**fm, "verdict_id": str(fm.get("review_id") or path.stem), "verdict_at": str(fm.get("timestamp") or ""),
            "verdict": str(fm.get("outcome") or ""), "reviewed_return_record_ref": reviewed, "audit_task_id": ""}
    evidence = non_branch_re_return(workspace_root, task_id, card_frontmatter, path, view, None)
    return {"review_path": path, "review": view, "evidence": evidence,
            "withdrawn_audit_task_id": str(fm.get("withdrawn_audit_task_id") or "").strip(),
            "bound": rework_return_bound(path, view, None)}


def _derive_same_round(workspace_root: Path, task_id: str, fm: dict[str, Any], records: dict[str, Any], state: dict[str, Any], *,
                       claimer: str, conn_arg: str | None) -> dict[str, Any]:
    """AIPOS-F230 件④: 同轮补正未消费时的推导(声明 nodes.N4.same_round): 已见补正 = 重交回(_derive_stale_re_return 同一构建, 产物入口同一
    校验); 否则 = 等执行体补正(action await_rework, loop 拉起执行体; 不占返工轮)。"""
    decl = same_round_declaration()
    texts = decl["texts"]
    name = str(decl["state"])
    review = state["review"]
    points = "; ".join(str(x) for x in (review.get("focus_items") or [])) or "(复核记录无具体项)"
    withdrawn = state["withdrawn_audit_task_id"]
    after = (texts["after_review"] if fast_lane_review(workspace_root, task_id, fm)
             else (texts["after_audit"].format(withdrawn=withdrawn) if withdrawn else texts["after_first"]))
    values = {"review_id": review["verdict_id"], "reviewed": review["reviewed_return_record_ref"] or "?", "points": points, "after": after}
    nb_texts = non_branch_rework_declaration()["texts"]
    if state["evidence"] is not None:
        head = texts["stale_head"].format(**values, evidence=non_branch_evidence_text(nb_texts, state["evidence"]))
        tip = _card_branch_tip(workspace_root, task_id, fm)
        stale = {"subject": str(card_delivery_subject(workspace_root, task_id, fm).get("kind") or ""), "rework_round": None,
                 "re_execution": state["evidence"], "verdict_id": review["verdict_id"], "verdict": review.get("verdict"),
                 "verdict_at": review.get("verdict_at"), "audit_task_id": withdrawn, "reviewed_return_record_ref": review["reviewed_return_record_ref"],
                 "branch": tip[2] if tip else "", "tip": tip[1]["commit_sha"] if tip else "", "tree_hash": tip[1]["tree_hash"] if tip else "",
                 "reason": ""}
        return _derive_stale_re_return(
            workspace_root, task_id, fm, records, stale, claimer=claimer, conn_arg=conn_arg, state=name, stale_key=name, head=head,
            reviewed_ref=str(review["reviewed_return_record_ref"] or ""), since=str(review.get("verdict_at") or ""),
            reviewed_what=texts["reviewed_what"], without_round_suggested=texts["without_round_suggested"],
            without_round_note=texts["without_round_note"], not_updated_note=texts["not_updated_note"],
            re_return_notes=f"{name}: {head}; {texts['re_return_note']}", invalid_fix=texts["invalid_fix"],
            re_return_suggested=texts["re_return_suggested"])
    bound_note = texts["bound_note"].format(at=state["bound"]["at"].strftime("%Y-%m-%dT%H:%M:%SZ"))  # 时间界 = 复核时刻(无返工轮)
    return {
        "task_id": task_id,
        "derivable": False,
        "current_node": name,
        "current_state": "claimed",
        "triggered_by": "executor",
        "command": "",
        "verb": "lybra_queue_return_dry_run",
        "missing_records": [f"{texts['await_missing'].format(**values)}; {bound_note}"],
        "suggested_action": f"{texts['await_suggested'].format(**values)}; {bound_note}",
        "notes": (f"N2: 顾问复核 {review['verdict_id']} 同轮打回交回 {values['reviewed']}(不占返工轮"
                  + (f", 已撤本轮未开始的审计卡 {withdrawn}" if withdrawn else "") + "; AIPOS-F230 件④, 声明 transitions nodes.N4.same_round)"),
        "same_round": {k: v for k, v in state.items() if k not in ("review_path", "bound")} | {"review_path": str(state["review_path"])},
        "action": {"type": "await_rework", "card": task_id, "round": 0, "same_round": review["verdict_id"]},
    }


# ---------------------------------------------------------------------------
# AIPOS-F230 件⑥: 审计运行证据复用(声明 transitions artifact_ingest.verdict.runtime_evidence.candidate_subject.reuse)
# ---------------------------------------------------------------------------

_REUSE_TEXTS = ("kickoff", "basis_scope", "basis_round", "not_earlier_round", "decl_mismatch", "instance_mismatch", "not_eligible")


def evidence_reuse_declaration() -> dict[str, Any]:
    """AIPOS-F230 件⑥: runtime_evidence.candidate_subject.reuse(唯一读取口); 缺键 = SchemaLoadError(fail-closed)。"""
    from tools.aipos_cli.artifact_ingest import runtime_evidence_candidate_declaration
    from tools.schema_loader import SchemaLoadError

    decl = runtime_evidence_candidate_declaration().get("reuse")
    texts = decl.get("texts") if isinstance(decl, dict) else None
    if (not isinstance(decl, dict) or not all(isinstance(decl.get(k), str) and decl[k].strip() for k in ("field", "rework_field", "rework_value"))
            or not isinstance(texts, dict) or not all(isinstance(texts.get(k), str) and texts[k].strip() for k in _REUSE_TEXTS)):
        raise SchemaLoadError("transitions.schema.json artifact_ingest.verdict.runtime_evidence.candidate_subject.reuse(field / rework_field / "
                              f"rework_value / texts.{list(_REUSE_TEXTS)})未声明齐(AIPOS-F230 件⑥)")
    return decl


def audit_evidence_reuse(workspace_root: Path, audit_task_id: str, *, reviewed: str = "", reviewed_fm: dict[str, Any] | None = None) -> dict[str, Any]:
    """AIPOS-F230 件⑥ 资格判定(唯一实现; 裁决入口 board_adapter._candidate_run_problem 与开工稿 / 取证锚点同读): 本审计卡之前的更早轮次
    (audit_derivation.audit_round_ids 中位于本卡之前者)+ 资格依据: 被审卡最新常规裁决(本轮之前那一轮)fail_scope = deliverable
    (verdict_fail_scope), 或针对它登记的返工轮写 rework_field = rework_value。返回 {eligible, earlier_rounds, previous, basis, verdict_id,
    scope_note}。"""
    from tools.aipos_cli.audit_derivation import audit_card_reviewed_id, audit_round_ids

    decl = evidence_reuse_declaration()
    texts = decl["texts"]
    root = Path(workspace_root)
    rid = str(reviewed or "").strip() or audit_card_reviewed_id(audit_task_id, None)
    rounds = audit_round_ids(rid, root) if rid else []
    earlier = rounds[: rounds.index(audit_task_id)] if audit_task_id in rounds else list(rounds)
    out: dict[str, Any] = {"eligible": False, "earlier_rounds": earlier, "previous": earlier[-1] if earlier else "", "basis": "",
                           "verdict_id": "", "scope_note": "", "reviewed": rid}
    if not earlier:
        return out
    regular = [(p, fm) for p, fm in _regular_verdicts(root, rid) if str(fm.get("audit_task_id") or "") in earlier]
    if not regular:
        return out
    _path, latest = regular[0]
    out["verdict_id"] = str(latest.get("verdict_id") or _path.stem)
    scope = verdict_fail_scope(root, latest)
    out["scope_note"] = scope["note"]
    if reviewed_fm is None:
        rpath, _q = _find_task_in_queue(root, rid)
        reviewed_fm = _read_frontmatter(rpath) if rpath else {}
    rework = rework_round_for_verdict(reviewed_fm, {**latest, "verdict_id": out["verdict_id"]})
    if scope["scope"] == "deliverable" and scope["declared"]:
        out.update({"eligible": True, "basis": texts["basis_scope"].format(verdict_id=out["verdict_id"])})
    elif rework is not None and str(rework.get(decl["rework_field"]) or "").strip() == str(decl["rework_value"]):
        out.update({"eligible": True, "basis": texts["basis_round"].format(round=rework.get("round") or "?", field=decl["rework_field"],
                                                                             value=decl["rework_value"])})
    return out


def audit_evidence_reuse_line(workspace_root: Path, audit_task_id: str, *, candidate: str = "", reviewed: str = "") -> str | None:
    """AIPOS-F230 件⑥: 开工稿 / 取证锚点的复用说明一行(texts.kickoff); 不可复用 = None。"""
    view = audit_evidence_reuse(workspace_root, audit_task_id, reviewed=reviewed)
    if not view["eligible"]:
        return None
    return evidence_reuse_declaration()["texts"]["kickoff"].format(basis=view["basis"], previous=view["previous"], candidate=candidate or "?")


def _derive_audit_blocked(workspace_root: Path, task_id: str, fm: dict[str, Any], state: dict[str, Any], *,
                          conn_arg: str | None) -> dict[str, Any]:
    """N4 BLOCK -> not derivable, action audit_blocked (advisor fixes environment then re-dispatches R(n+1));
    consecutive BLOCK >= owner_needed_after -> repeated end reason (owner_needed)."""
    decl = audit_blocked_declaration()
    actor = _driver_actor(workspace_root, connection_json=conn_arg)
    policy_ref = _driver_envelope_ref(workspace_root, task_id, fm, conn_arg) or ""
    if state.get("owner_reaudit") and not state["consecutive"] and not state.get("redispatched_to"):
        return _derive_owner_reaudit(workspace_root, task_id, fm, state, actor=actor, policy_ref=policy_ref, conn_arg=conn_arg)
    # AIPOS-F218 件②: 两条可选路(登记返工轮重新执行 / 仅重审)与推荐 = non_pass_recovery(loop status 例行修法同读)
    recovery = non_pass_recovery(workspace_root, task_id, fm, state, actor=actor, policy_ref=policy_ref)
    command = recovery["reaudit_command"]
    repeated = bool(state["repeated"])
    head = (f"审计未能完成(裁决 {state['verdict']} {state['verdict_id']}, 审计卡 {state['audit_task_id']}"
            f"{', 连续第 ' + str(state['consecutive']) + ' 次' if state['consecutive'] > 1 else ''})")
    if repeated:
        suggested = (f"{head}: 已按 BLOCK 原因修环境重审仍 BLOCK(连续 {state['consecutive']} 次 ≥ {state['owner_needed_after']}); "
                     "第二档(AIPOS-F225): 自该 BLOCK 起部署 / 验证根有变化 = 原命令续跑 loop 即自动重新派审, 无变化 = 顾问修环境 / 产品, "
                     f"要改卡面验收 / 换审计环境才带第一档事由升 Owner — 原因: {state['reason'] or '(见审计报告)'}; {recovery['choice']}; "
                     f"a) {recovery['rework_command']}")
    else:
        suggested = f"{head}: 原因: {state['reason'] or '(见审计报告)'}; {recovery['choice']}; {recovery['paths']}"
    result = {
        "task_id": task_id,
        "derivable": False,
        "current_node": "audit_verdict",
        "current_state": "claimed",
        "triggered_by": "advisor",
        "command": "",
        "verb": "",
        "missing_records": [],
        "suggested_action": suggested,
        "notes": (f"N4: 裁决 {state['verdict']} = 审计未能完成(环境 / 资源 / 时长), 不计连败; 连续 BLOCK {state['consecutive']}/"
                  f"{state['owner_needed_after']}(AIPOS-F179 件③, 声明 transitions nodes.N4.audit_blocked)"),
        "audit_blocked": state,
        "recovery": recovery,
        "action": {"type": str(decl["action_type"]), "card": task_id,
                   "end_reason": str(decl["repeated_end_reason"] if repeated else decl["end_reason"]),
                   "verdict_id": state["verdict_id"], "audit_task_id": state["audit_task_id"], "consecutive": state["consecutive"],
                   "reaudit_command": "" if repeated else command, "rework_command": recovery["rework_command"],
                   "recommended": recovery["recommended"]},
    }
    # AIPOS-F230 件①: BLOCK / 连续 BLOCK 也列全部合法出口(推荐 = non_pass_recovery 证据改判)
    rec, rec_why = recovery_exit_key(recovery)
    return _attach_legal_exits(result, legal_exits(workspace_root, task_id, fm, "audit_blocked_repeated" if repeated else "BLOCK",
                                                   verdict_state=state, actor=actor, policy_ref=policy_ref, recommended=rec, why=rec_why))


def _derive_owner_reaudit(workspace_root: Path, task_id: str, fm: dict[str, Any], state: dict[str, Any], *, actor: str,
                          policy_ref: str, conn_arg: str | None) -> dict[str, Any]:
    """AIPOS-F202: the Owner ruled "re-audit" after the latest BLOCK (no BLOCK since the ruling) -> derivable: dispatch the next
    audit round with the existing verb (same command shape as the N2->N3 dispatch the loop executes; round = derive_audit_task_id;
    dispatcher / envelope = the driver). The loop then waits for / launches the new round as usual (AIPOS-F112 in-flight round)."""
    from tools.aipos_cli.audit_derivation import resolve_audit_instance

    ruling = state["owner_reaudit"]
    head = (f"Owner 裁决 {ruling['decision_id']}(选 {ruling['choice']} @ {ruling['decided_at']})= 重新派审: 连续 BLOCK 自该裁决起重新计数"
            f"(此前 {state['consecutive_total']} 次, 历史裁决与审计卡不改)")
    if ruling.get("auto"):  # AIPOS-F225 item 2: tier-2 auto re-audit record as the boundary
        head = str(auto_reaudit_declaration()["texts"]["head"]).format(decision_id=ruling["decision_id"], decided_at=ruling["decided_at"],
                                                                        total=state["consecutive_total"])
    missing: list[str] = []
    auditor = ""
    if not actor:
        missing.append(DRIVER_ACTOR_MISSING)
    try:
        auditor = resolve_audit_instance(fm, workspace_root)
    except ValueError as exc:
        missing.append(f"卡 {task_id} 审计实例声明(audit_by)或项目声明(project): {exc}")
    if missing:
        return {"task_id": task_id, "derivable": False, "current_node": "audit_verdict", "current_state": "claimed", "triggered_by": "advisor",
                "command": "", "verb": "lybra_audit_dispatch_dry_run", "missing_records": missing,
                "suggested_action": f"{head}; 派审缺项: {'; '.join(missing)}", "notes": "N4: Owner 重新派审裁决在, 派审依据缺(AIPOS-F202)",
                "audit_blocked": state, "action": {"type": "record_missing", "card": task_id, "record": "owner_reaudit_dispatch"}}
    cmd = _build_audit_dispatch_command(
        task_id=task_id, actor=actor, agent_instance=actor, owner_policy_ref=policy_ref or None, connection_json=conn_arg,
        audit_task_id=str(state["next_round"]), audit_agent_instance=auditor, dispatch_reason=_reaudit_dispatch_reason(state))
    return {
        "task_id": task_id,
        "derivable": True,
        "current_node": "audit_verdict",
        "current_state": "claimed",
        "triggered_by": "advisor",
        "command": cmd,
        "verb": "lybra_audit_dispatch_dry_run",
        "missing_records": [],
        "suggested_action": f"{head}; 派下一轮审计 {state['next_round']}(执行产物不变)",
        "notes": (f"N4: 最新裁决 {state['verdict']} {state['verdict_id']}(审计卡 {state['audit_task_id']})在 Owner 重新派审裁决之前, "
                  f"界后 BLOCK 0/{state['owner_needed_after']}(AIPOS-F202, 声明 transitions nodes.N4.audit_blocked.owner_reaudit)"),
        "audit_blocked": state,
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
            result = _derive_next_step(task_id, workspace_root)
    except FrontmatterReadError as exc:
        return frontmatter_unreadable_stop(task_id, exc)
    return _attach_loop_end(result, Path(workspace_root), task_id)


def _attach_loop_end(result: dict[str, Any], workspace_root: Path, task_id: str) -> dict[str, Any]:
    """AIPOS-F240 件④: claimed 卡最近一次 loop 运行以上下文超限结束(launch.early_exit.run_overflow.next_end_reasons)= 附 loop_end
    (与 loop status 同一下一动作 loop_run_record.card_loop_end_action: 结束原因 / 出口全集 / 续跑命令); 不改推导结论。
    运行记录 / 声明读不出 = loop_end.error(出声, 不吞, 不阻断推导)。"""
    if not isinstance(result, dict) or str(result.get("current_state") or "") != "claimed":
        return result
    from tools.aipos_cli.loop_run_record import LoopRunRecordError, card_loop_end_action
    from tools.schema_loader import SchemaLoadError

    try:
        action = card_loop_end_action(workspace_root, task_id)
    except (LoopRunRecordError, SchemaLoadError, OSError, ValueError) as exc:
        result["loop_end"] = {"error": f"{type(exc).__name__}: {exc}"}
        return result
    if action is not None:
        result["loop_end"] = action
    return result


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
    revoked_stop = auto_release_revoked_stop(workspace_root, task_id)  # AIPOS-F225 件②: Owner 撤回自动放行 = 卡停住
    if revoked_stop is not None:
        return revoked_stop

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

    # AIPOS-F211 件②: 缺 task_mode 不再补 "code"(缺省 = schema missing_default, 唯一兜底 _task_mode_entry); 文案里如实显示「(缺)」
    task_mode = str(fm.get("task_mode") or "").strip()
    assigned_to = fm.get("assigned_to") or fm.get("agent_instance") or ""
    independent_audit = card_independent_audit(fm)  # AIPOS-F211 件④: 卡级独立审计唯一判定(模式声明或卡面 audit: required)
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
        # AIPOS-F234 件②: 孤儿审计卡(业务卡已撤回 / 撤换 / 结案; 唯一判定 queue_mutation.orphan_audit_state)= 不再审计、不补跑旧命令,
        # 出口 = 随业务卡撤回(命令原文); 先于轮次 / 报告判据(业务卡已不推进, 其报告缺项不再要审计体补做)
        from tools.aipos_cli.queue_mutation import orphan_audit_state

        orphan = orphan_audit_state(workspace_root, task_id, fm)
        if orphan is not None:
            return {
                "task_id": task_id, "derivable": False, "current_node": "orphan_audit", "current_state": queue_dir,
                "triggered_by": "advisor", "command": "", "verb": "", "missing_records": [],
                "suggested_action": orphan["suggested"],
                "notes": f"{orphan['note']}(AIPOS-F234 件②, 声明 transitions queue_mutations.in_transit_guard)",
                "orphan_audit": orphan,
                "action": {"type": "orphan_audit_withdraw", "card": task_id, "command": orphan["command"]},
            }
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
        unfilled = missing_verdict_frontmatter(verdict_fm, governance=card_report_is_governance(workspace_root, task_id, fm))
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
        # AIPOS-F162 件①: 运行证据硬门(门侧裁决写入唯一口同一判据 board_adapter.runtime_evidence_check, AIPOS-F163)先行——不合格 = 审计产物不合格,
        # 让审计体补做(action.type 读声明 runtime_evidence.loop.derivation_action; 非硬停、不升 Owner, loop 拉起审计体并附拒因)
        from tools.aipos_cli.artifact_ingest import runtime_evidence_declaration
        from tools.aipos_cli.board_adapter import runtime_evidence_check

        evidence = runtime_evidence_check(workspace_root, task_id, fm, report_path=verdict_artifact, report_fm=verdict_fm)
        if evidence is not None and evidence["category"] == str(runtime_evidence_declaration()["declaration_invalid_category"]):
            # AIPOS-F233 件③: 声明本身有问题(形不合 / 命令渲染不了)= 审计体补做也匹配不上 → 归顾问受限 amend, 不让审计体补做
            return _evidence_declaration_invalid(task_id, evidence, node="audit_verdict", path=str(verdict_artifact))
        if evidence is not None:
            rework_action = str(runtime_evidence_declaration()["loop"]["derivation_action"])
            return {
                "task_id": task_id,
                "derivable": False,
                "current_node": "audit_verdict",
                "current_state": "claimed",
                "triggered_by": "auditor",
                "command": "",
                "verb": "lybra_audit_verdict_dry_run",
                "missing_records": list(evidence["reasons"]),
                "suggested_action": f"审计报告被拒收({evidence['category']}), 审计体补做: " + "; ".join(evidence["reasons"]),
                "notes": "N4: 审计报告 PASS 类裁决缺卡面要求的独立运行证据(AIPOS-F162 件①), 裁决入口拒收; loop 让审计体补做",
                "action": {"type": rework_action, "card": task_id, "path": str(verdict_artifact), "category": evidence["category"]},
            }
        agent_inst = actor
        
        # AIPOS-F73前置①: 从被审卡分支提取 artifact_subject (code 卡必填)
        reviewed_task_path, _ = _find_task_in_queue(workspace_root, reviewed_task_id)
        reviewed_fm = _read_frontmatter(reviewed_task_path) if reviewed_task_path else {}
        # AIPOS-F178: 被审卡有卡分支 = 写产品仓(唯一判据 card_writes_product_repo; 写仓的 docs 等卡与代码卡同路径绑 tip;
        # AIPOS-F211: 缺 task_mode 按 schema missing_default, 唯一兜底 _task_mode_entry)
        reviewed_has_branch = card_writes_product_repo(workspace_root, {**reviewed_fm, "task_id": reviewed_task_id})
        # AIPOS-F73D + F78C: 卡分支活在被审卡声明的产品仓(resolve_card_repo: lane.repo → repos 清单 → code_repo → 治理根)
        from tools.aipos_cli.workspace_config import CardRepoUnresolved

        try:
            reviewed_repo = _card_repo_root(workspace_root, reviewed_task_id, reviewed_fm) if reviewed_has_branch else workspace_root
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
        artifact_subject = _extract_artifact_subject_from_branch(reviewed_repo, reviewed_task_id) if reviewed_has_branch else None
        # AIPOS-F196 件①: 交付主体为固定候选的被审卡(validation)= 裁决绑候选(candidate_subject_values 唯一实值, 门侧同核)
        try:
            candidate_values = candidate_subject_values(workspace_root, reviewed_task_id, reviewed_fm)
        except CandidateSubjectError as exc:
            return {
                "task_id": task_id,
                "derivable": False,
                "current_node": "audit_verdict",
                "current_state": "claimed",
                "triggered_by": "advisor",
                "command": "",
                "verb": "lybra_audit_verdict_dry_run",
                "missing_records": [f"被审卡 {reviewed_task_id} 固定候选不可用: {exc}"],
                "suggested_action": f"顾问 lybra queue amend 修正被审卡 {reviewed_task_id} 的来源卡 / 候选后重推导",
                "notes": f"N4: 被审卡固定候选不合({exc.code}), 裁决 artifact_subject 无据(AIPOS-F196)",
                "action": {"type": "artifact_invalid", "card": reviewed_task_id, "path": ""},
            }
        if candidate_values is not None:
            artifact_subject = {k: candidate_values[k] for k in ("repository", "commit_sha", "tree_hash")}
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
        # AIPOS-F233 件③: 卡面运行证据声明本身有问题(渲染不了)= 不拉起审计体(审计体实跑也匹配不上), 归顾问受限 amend
        from tools.aipos_cli.artifact_ingest import evidence_declaration_problem

        declaration_problem = evidence_declaration_problem(workspace_root, task_id, fm)  # 形不合等声明本身错归入问题(不上抛; 字面花括号不算)
        if declaration_problem is not None:
            return _evidence_declaration_invalid(task_id, {"category": declaration_problem["category"],
                                                           "reasons": [*declaration_problem["reasons"], declaration_problem["outlet"]]},
                                                 node="audit_dispatch", path="")
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
        # AIPOS-F235 件③: 子 agent 卡卡面 harness 与派发顾问现登记不符 = 不派生认领(认领后开工稿时限句 / 派发说明会按卡面错), 给 amend 出口原文
        mismatch = card_harness_mismatch(workspace_root, task_id, fm, claimed=False)
        if mismatch:
            return {
                "task_id": task_id,
                "derivable": False,
                "current_node": "publish",
                "current_state": "pending",
                "triggered_by": "advisor",
                "command": "",
                "verb": "lybra_queue_claim_dry_run",
                "missing_records": [f"卡面 harness = 派发顾问现登记 harness({mismatch['registered']})"],
                "suggested_action": mismatch["amend_command"],
                "notes": f"N0→N1: {mismatch['reason']}(AIPOS-F235 件③)",
                "action": {"type": "harness_mismatch", "card": task_id, **{k: mismatch[k] for k in ("face", "registered", "advisor", "source")}},
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
                # AIPOS-F230 件④: 顾问同轮打回了门最新交回(审计开始前, 不占返工轮)= 等执行体补正 / 已补正 = 重交回
                same_round = same_round_state(workspace_root, task_id, fm, records)
                if same_round is not None:
                    return _derive_same_round(workspace_root, task_id, fm, records, same_round, claimer=claimer, conn_arg=conn_arg)

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
                        # AIPOS-F197 件②: 只写治理根的卡 N5 无合入(finalize 不碰产品仓 git)= --workspace-root 取治理根, 不解析产品仓
                        code_repo_root = (Path(workspace_root) if card_governance_outputs(workspace_root, {**fm, "task_id": task_id})
                                          else _card_repo_root(workspace_root, task_id, fm, allow_governance_root=False))
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
                    # AIPOS-F155 件②(gap #135): 已针对本裁决登记返工轮 = 等执行体按返工节修复后重交(不再「需人工处理」)
                    rework = rework_round_for_verdict(fm, latest_verdict)
                    if rework is not None:
                        return _derive_rework_wait(workspace_root, task_id, fm, records, latest_verdict, rework,
                                                   claimer=claimer, conn_arg=conn_arg)
                    # AIPOS-F179 件③: BLOCK = 审计未能完成 → 修环境后重新派审计(不改执行产物, 不计连败; 连续 BLOCK 达声明次数升 Owner)
                    block = audit_block_state(workspace_root, task_id)
                    if block is not None:
                        return _derive_audit_blocked(workspace_root, task_id, fm, block, conn_arg=conn_arg)
                    # AIPOS-F218 件②: FAIL 等非 PASS 裁决(未登记返工轮)附两条可选路(non_pass_recovery, 与 BLOCK 同一实现)
                    # AIPOS-F211 件④(gap #217): 固定候选验证卡 FAIL = 候选不合格 → 新候选另出验证卡 + 撤换本卡(不计连败, 不升 Owner);
                    # AIPOS-F227: 审计声明 fail_scope=deliverable = 同候选补正出口(返工命令读上行同一 recovery), 未声明 = 两出口并列
                    recovery = _non_pass_fail_recovery(workspace_root, task_id, fm, conn_arg=conn_arg)
                    candidate_fail = candidate_fail_state(workspace_root, task_id, fm)
                    if candidate_fail is not None:
                        # AIPOS-F230 件①: 固定候选 FAIL 两态(candidate_fail / candidate_deliverable_fail)也列全部合法出口
                        derived = _derive_candidate_fail(task_id, candidate_fail, recovery)
                        return _attach_legal_exits(derived, _candidate_fail_exits(workspace_root, task_id, fm, candidate_fail, derived,
                                                                                  conn_arg=conn_arg))
                    return _attach_legal_exits({
                        "task_id": task_id,
                        "derivable": False,
                        "current_node": "audit_verdict",
                        "current_state": "claimed",
                        "triggered_by": "executor",
                        "command": "",
                        "verb": "",
                        "missing_records": [],
                        "suggested_action": (f"处理裁决 {verdict_result} (返工或修复)"
                                             + (f"; {recovery['choice']}; {recovery['paths']}" if recovery else "")),
                        "notes": f"N4: 裁决 {verdict_result},需人工处理",
                        **({"recovery": recovery} if recovery else {}),
                    }, _non_pass_exits(workspace_root, task_id, fm, "FAIL", non_pass_fail_state(workspace_root, task_id), recovery,
                                       conn_arg=conn_arg))
            
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
            if not latest_audit_dispatch and independent_audit:  # AIPOS-F196 / F211: 读声明(card_independent_audit)
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
            # 非代码卡,不需要独立审计(AIPOS-F196: 是否独立审计读声明 enums.schema task_mode.independent_audit)
            if not independent_audit:
                if card_governance_outputs(workspace_root, {**fm, "task_id": task_id}):
                    # AIPOS-F221 件②: 顾问复核打回后(复核记录 + 返工轮)沿用 F218/F219 返工判据——未重交回 = 等返工 / 报告已重写 = 重交回;
                    # 门已落晚于时间界的新交回 = 回到顾问复核(下方结案命令, 附 back_to_review)
                    rework_state = fast_lane_rework_state(workspace_root, task_id, fm)
                    back_note = ""
                    if rework_state is not None:
                        evidence = rework_state["evidence"]
                        if not rework_state["re_returned"]:
                            return _derive_fast_lane_rework(workspace_root, task_id, fm, records, rework_state, claimer=claimer,
                                                            conn_arg=conn_arg)
                        texts = fast_lane_rework_declaration()["texts"]
                        back_note = texts["back_to_review"].format(
                            round=rework_state["rework"].get("round") or "?", review_id=rework_state["anchor"]["verdict_id"],
                            reviewed=rework_state["anchor"]["reviewed_return_record_ref"] or "?",
                            evidence=non_branch_evidence_text(non_branch_rework_declaration()["texts"], evidence))
                    # AIPOS-F197 件②: 只写治理根的卡 = 顾问复核交回绑定的产出清单后结案(mark-concluded 结案前核此刻指纹 = 交回绑定)
                    bound = governance_outputs_return_binding(workspace_root, task_id) or {}
                    try:
                        report_ref = str(_return_artifact_path(workspace_root, task_id).resolve().relative_to(Path(workspace_root).resolve()))
                    except ValueError:
                        report_ref = str(_return_artifact_path(workspace_root, task_id))
                    from tools.aipos_cli.workspace_config import lybra_command_prefix

                    close_cmd = (f"{lybra_command_prefix(workspace_root)} mark-concluded --task-id {task_id} --actor <你的顾问实例> "
                                 f"--report-path {report_ref} --conclusion-note \"顾问复核: <结论>\"")
                    rework_cmd = review_rework_command(workspace_root, task_id, report_ref)  # AIPOS-F221 件②: 复核不通过的正式出口
                    review_step = {
                        "task_id": task_id,
                        "derivable": True,
                        "current_node": "return",
                        "current_state": "claimed",
                        "triggered_by": "advisor",
                        "command": (f"# 非代码卡(task_mode={task_mode or '(缺)'}, 只写治理根):顾问复核交回的产出清单"
                                    f"(指纹 {bound.get('outputs_digest') or '(交回记录无绑定)'})后结案: {close_cmd}"
                                    f"; 复核不通过打回原卡返工: {rework_cmd}"),
                        "verb": "lybra_mark_concluded",
                        "advisor_command": close_cmd,  # AIPOS-F207 件③: loop status advisor_review_close 的 hint 读此(不解析注释行)
                        "rework_command": rework_cmd,
                        "missing_records": [],
                        "suggested_action": (f"顾问复核产出清单 {bound.get('outputs') or []} 后结案(先加 --dry-run 预演); "
                                             f"复核不通过打回原卡返工(AIPOS-F221): {rework_cmd} --dry-run"
                                             + (f"; {back_note}" if back_note else "")),
                        "notes": ("非代码卡快车道(只写治理根, AIPOS-F197):顾问复核后 mark-concluded 结案(不通过 = --outcome rework 打回返工)"
                                  + (f"; {back_note}" if back_note else "")),
                    }
                    # AIPOS-F230 件①: 复核不通过时的全部合法出口(同主体返工 / 同轮补正 / 换新主体 / 改范围 / 撤卡)
                    return _attach_legal_exits(review_step, _non_pass_exits(workspace_root, task_id, fm, "fast_lane_review_fail", None, None,
                                                                            conn_arg=conn_arg))
                return {
                    "task_id": task_id,
                    "derivable": True,
                    "current_node": "return",
                    "current_state": "claimed",
                    "triggered_by": "advisor",
                    "command": f"# 非代码卡(task_mode={task_mode or '(缺)'}):顾问审核后直接提交裁决",
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
                    "missing_records": ["RETURN.md 存在但无法提取一句话结论(摘要来源: transitions artifact_ingest.return.summary_source)"],
                    "suggested_action": "检查 Return 是否含声明的摘要来源(缺省『一句话结论』节)",
                    "notes": "RETURN.md 格式不完整,推导不出",
                }

            # AIPOS-F78 件③: Return 必填 frontmatter(transitions artifact_ingest.return.required_frontmatter)缺 = 不可推导
            # (action=artifact_invalid, loop 据此 exit 4 而非空等), 齐则派生与 ingest 同一条 return 命令
            return_path = _return_artifact_path(workspace_root, task_id)
            governance_card = card_governance_outputs(workspace_root, {**fm, "task_id": task_id})  # AIPOS-F197 件②: 交回契约分形
            return_fm = _read_frontmatter(return_path, allow_missing_block=True)
            missing_fm = missing_return_frontmatter(return_fm, governance=governance_card)
            if missing_fm:
                # AIPOS-F123 件①: 拒因原文带补法(逐键说明 = 报告必填契约单源 report_frontmatter_contract)——收编的人肉期手写 Return
                # 常缺这三项, 出口须能照做。AIPOS-F204 件①: 补法 = 本卡开工提示同一契约(候选主体卡 = 产品给值照抄, 非「卡分支
                # card/<本卡> tip」; 误用另一契约另附说明), 判定与渲染唯一实现 artifact_ingest.return_header_problem(ingest 拒因同读)
                from tools.aipos_cli.artifact_ingest import return_header_problem

                problem = return_header_problem(workspace_root, task_id, fm, return_fm, missing=missing_fm)
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
                                         f"(声明: transitions.schema artifact_ingest.return.required_frontmatter)。补法: {problem['fix']}"
                                         + (f"。{problem['note']}" if problem["note"] else "")),
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

        # AIPOS-F148 件②(gap #112): Return 已落盘但摘要来源都取不到——必填 frontmatter 已填实值(执行体已自认交回)或摘要来源声明
        # 不可用 = artifact_invalid(loop exit 4, 原文列出所查来源与补法), 不当「无交回」空等; 必填 frontmatter 仍为占位 = 骨架, 照旧等待
        summary_status = return_summary_status(workspace_root, task_id)
        if summary_status["exists"] and summary_status["summary"] is None and (summary_status["error"] or summary_status["submitted"]):
            reason = summary_status["error"] or summary_status["missing"]
            return {
                "task_id": task_id,
                "derivable": False,
                "current_node": "claim",
                "current_state": "claimed",
                "triggered_by": "executor",
                "command": "",
                "verb": "lybra_queue_return_dry_run",
                "missing_records": [reason],
                "suggested_action": (f"执行体按拒因补齐 {summary_status['path']} 的摘要后产品自动重推导(声明: transitions.schema "
                                     "artifact_ingest.return.summary_source; 项目可经 lybra project set-return-summary 声明本项目的摘要来源)"),
                "notes": "Return 已落盘且必填 frontmatter 已填, 但摘要来源都取不到, 产品无法铸交回记录(AIPOS-F148 件② fail-closed, 不空等)",
                "action": {"type": "artifact_invalid", "card": task_id, "path": str(summary_status["path"])},
            }

        # 无 return 记录也无 RETURN.md → 检查是否有返工节 (AIPOS-F75 件③)
        rework_rounds = fm.get("rework_rounds", [])
        if isinstance(rework_rounds, list) and rework_rounds:
            # 取最新且未销账的轮次(识别唯一实现 uncleared_rework_rounds, AIPOS-F155 件② N4 分支同读)
            uncleared_rounds = uncleared_rework_rounds(fm)
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
        # AIPOS-F162 件②: 已结案卡的补充审计——在途 = 等补审卡(loop 走既有审计等待/拉起/裁决入口, 同 N3 await_artifact);
        # 已有补审 = 链路随推导结果给出(next 视图); 原 closure/原裁决不动
        from tools.aipos_cli.audit_derivation import render_supplementary_chain, supplementary_audit_chain, supplementary_in_flight

        sup_chain = [] if is_audit_card else supplementary_audit_chain(workspace_root, task_id)
        sup_line = render_supplementary_chain(task_id, sup_chain) if sup_chain else ""
        sup_card = supplementary_in_flight(workspace_root, task_id) if sup_chain else None
        if sup_card:
            return {
                "task_id": task_id,
                "derivable": False,
                "current_node": "supplementary_audit",
                "current_state": "completed",
                "triggered_by": "auditor",
                "command": "",
                "verb": "",
                "missing_records": [f"补充审计 {sup_card} 裁决"],
                "suggested_action": f"已结案卡补充审计在途: {sup_line}; lybra loop --task-id {task_id} 推进(认领补审卡/拉起审计体/裁决入门)",
                "notes": "AIPOS-F162 件②: 已结案卡有在途补充审计, 原 closure/原裁决不动",
                "action": {"type": "await_artifact", "card": sup_card},
                "supplementary_audit": sup_chain,
            }
        landing_step = _derive_n6_landing(workspace_root, task_id, "completed", conn_arg)
        if landing_step is not None:
            if sup_chain:
                landing_step["supplementary_audit"] = sup_chain
            return landing_step
        if sup_chain:
            return {
                "task_id": task_id,
                "derivable": True,
                "current_node": "close",
                "current_state": "completed",
                "triggered_by": "none",
                "command": "# 任务已完成,无下一步",
                "verb": "",
                "missing_records": [],
                "suggested_action": f"无(任务已结束); {sup_line}",
                "notes": "N6: 任务已结案(AIPOS-F162 件②: 附补充审计链路)",
                "supplementary_audit": sup_chain,
            }
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


def auto_release_revoked_stop(workspace_root: Path, task_id: str) -> dict[str, Any] | None:
    """AIPOS-F225 件②: Owner 撤回了本卡的第二档自动放行、之后无新裁决(唯一判定 owner_inbox.auto_release_stop)→ 不可推导硬停项
    (action.type / end_reason = tiers.tier2.auto_release.revoked_end_reason; loop 以该结束原因结束, 第一档); 否则 None。读不出 = 向上。"""
    from tools.aipos_cli.owner_inbox import auto_release_stop, inbox_declaration

    stop = auto_release_stop(Path(workspace_root), task_id)
    if stop is None:
        return None
    reason = str(stop["end_reason"])
    text = str((inbox_declaration()["options"].get("revoke_auto") or {}).get("advisor_next") or "").format(
        task_id=task_id, decision_id=stop["decision_id"])
    return {
        "task_id": task_id, "derivable": False, "current_node": None, "current_state": reason, "triggered_by": "owner", "command": "",
        "verb": "", "missing_records": [f"Owner 撤回自动放行 {stop['auto_release_id']}(裁决 {stop['decision_id']} @ {stop['decided_at']})"],
        "suggested_action": text, "notes": "AIPOS-F225 件②: 撤回自动放行之后本卡无 Owner 新裁决 = 卡停住(声明 verbs.schema lybra_owner_inbox.tiers.tier2.auto_release)",
        "auto_release_stop": stop, "action": {"type": reason, "card": task_id, "end_reason": reason},
    }


def _priority_rank(value: Any) -> int:
    """卡 priority 的序(enums.schema priority 值序, 后者高; 缺/不在值域 = -1 最低)。"""
    from tools.schema_loader import load_schema

    values = [str(v.get("value")) for v in (((load_schema("enums").get("enums") or {}).get("priority") or {}).get("values") or [])
              if isinstance(v, dict)]
    text = str(value or "").strip().lower()
    return values.index(text) if text in values else -1


_SCAN_STATES = ("pending", "claimed", "blocked")
# AIPOS-F142 件①: 「下一张可推进卡」只可能出自这些队列状态(verbs.schema lane_view.next_card: pending 且可推导)——scan_project_view
# 选 next_card 与结案取下一张(next_states_only)共用这一处, 两处不得各写一份
NEXT_CARD_STATES = ("pending",)
_SCAN_STATE_ORDER = {"legacy_baseline_invalid": -1, "pending": 0, "claimed": 1, "blocked": 2, "completed": 3}


def scan_project_view(workspace_root: Path, *, lane: str | list[str] | None = None, include_frozen: bool = False,
                      next_states_only: bool = False, viewer: dict[str, Any] | None = None) -> dict[str, Any]:
    """项目级扫描:返回所有活跃任务的最小待办清单 + 可见卡汇总。

    AIPOS-F133: 卡遍历只走 task_loader.iter_queue_task_paths(原自 glob 队列目录); 每行带 lane、priority 与 next_card。
    AIPOS-F141: 冻结与 lane 过滤只经四视图唯一可见卡入口 machine_zone.visible_cards(冻结卡 = F122 唯一判定 frozen_tasks, 缺省不列;
    --lane 只列所选, 未解析 lane 的卡单独成组), 在推导之前过滤(不为不列的卡做推导); include_frozen = 冻结卡照列(推导核给
    legacy_frozen 硬停, 行标 frozen)。返回 visible_cards 结果, rows 换成推导后的行(含首行硬停项)。
    排序: 硬停项(存量冻结清单读不出)首行; 然后「下一张可推进卡」(pending 且可推导 = 依赖满足, 判据 task_complexity.
    unmet_dependencies 为空; 优先级最高者, 同级按 task_id); 其余按 pending > claimed > blocked、可推导优先、优先级高者先、task_id。
    AIPOS-F142 件①: next_states_only=True(结案取下一张, 只要 pick_next_card)只遍历 NEXT_CARD_STATES —— 下一张只可能出自这些状态,
    所得 next_card 与全扫相同, 不为不可能当选的 claimed/blocked 卡做推导。
    AIPOS-F156 件②: viewer(machine_zone.owner_viewer 结果)给出 = 经 visible_cards 同一归属判定: 非总顾问视角下归属其他实例的卡
    不进 rows(不推导、不当选「下一张」), 列在返回的 owner_other(「其他子项目(不取)」); 行带 owner / owner_source。
    """
    workspace_root = Path(workspace_root)
    from tools.aipos_cli.task_loader import task_card_lookup_scope

    # AIPOS-F142 件①: 整趟扫描共用一个只读作用域——逐卡 derive_next_step 共用一趟队列索引(原每卡各建一趟 = O(卡数²)),
    # 冻结清单重放与信封已放行计数各只算一次(lookup_scope_memo); 判据与逐卡推导完全相同
    with task_card_lookup_scope(workspace_root):
        return _scan_project_view(workspace_root, lane=lane, include_frozen=include_frozen, next_states_only=next_states_only,
                                  viewer=viewer)


def _scan_project_view(workspace_root: Path, *, lane: str | list[str] | None, include_frozen: bool,
                       next_states_only: bool, viewer: dict[str, Any] | None = None) -> dict[str, Any]:
    from tools.aipos_cli.frontmatter import FrontmatterReadError
    from tools.aipos_cli.machine_zone import visible_cards
    from tools.aipos_cli.task_loader import iter_queue_task_paths

    entries: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
    task_files = (iter_queue_task_paths(workspace_root, states=NEXT_CARD_STATES) if next_states_only
                  else iter_queue_task_paths(workspace_root, states=_SCAN_STATES))
    for task_file in task_files:
        stub: dict[str, Any] = {"task_id": task_file.stem.upper(), "path": str(task_file), "current_state": task_file.parent.name}
        try:
            fm = _read_frontmatter(task_file)
        except FrontmatterReadError as exc:
            # AIPOS-F100 件②: 读不出的卡不按文件名猜 task_id 去推导; 原样列为硬停项(点名文件与出口); 冻结判定按文件名回落
            entries.append(({**stub, "_unreadable": exc}, None))
            continue
        fm = fm or {}
        entries.append(({**stub, "task_id": fm.get("task_id", stub["task_id"]), "priority": fm.get("priority")}, fm))
    view = visible_cards(workspace_root, entries, lane=lane, include_frozen=include_frozen, viewer=viewer)

    results: list[dict[str, Any]] = []
    if view["frozen_error"]:
        results.append({
            "task_id": "(legacy_baseline)", "derivable": False, "current_node": None, "current_state": "legacy_baseline_invalid",
            "triggered_by": "advisor", "command": "", "verb": "", "missing_records": [view["frozen_error"]],
            "suggested_action": "修正存量冻结清单条目 / project.json legacy_baseline(冻结不生效, 不隐藏任何卡)", "notes": "",
            "lane": None, "lane_error": None,
        })

    rows: list[dict[str, Any]] = []
    for card in view["rows"]:
        lane_part = {"lane": card.get("lane"), "lane_error": card.get("lane_error"), **({"frozen": True} if card.get("frozen") else {}),
                     **{k: card[k] for k in ("owner", "owner_source") if k in card}}  # AIPOS-F156 件②: 归属标注
        if card.get("_unreadable") is not None:
            rows.append({**frontmatter_unreadable_stop(card["task_id"], card["_unreadable"]), "current_state": card["current_state"],
                         **lane_part})
            continue
        task_id = card["task_id"]
        try:
            result = derive_next_step(str(task_id), workspace_root)
        except Exception as e:  # 推导异常不吞: 列为不可推导行(带异常原文), 不隐藏该卡
            result = {
                "task_id": task_id,
                "derivable": False,
                "current_node": None,
                "current_state": card["current_state"],
                "triggered_by": "unknown",
                "command": "",
                "verb": "",
                "missing_records": [f"推导异常: {e}"],
                "suggested_action": "检查任务状态",
                "notes": str(e),
            }
        rows.append({**result, **lane_part, "priority": card.get("priority")})

    def order_key(r: dict[str, Any]) -> tuple[Any, ...]:
        return (_SCAN_STATE_ORDER.get(r.get("current_state", ""), 9), 0 if r.get("derivable") else 1,
                -_priority_rank(r.get("priority")), str(r.get("task_id") or ""))

    rows.sort(key=order_key)
    candidates = [r for r in rows if r.get("current_state") in NEXT_CARD_STATES and r.get("derivable")]
    if candidates:
        nxt = candidates[0]  # 已按 可推导 > 优先级 > task_id 排序
        rows.remove(nxt)
        rows.insert(0, {**nxt, "next_card": True})
    return {**view, "rows": results + rows}


def pick_next_card(results: list[dict[str, Any]]) -> dict[str, Any] | None:
    """scan_project_view(...)["rows"] 中的「下一张可推进卡」(next_card 标记行; 无 = None)。"""
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
    if result.get("supplementary_audit"):  # AIPOS-F162 件②: 补充审计链路(唯一渲染 audit_derivation.render_supplementary_chain)
        from tools.aipos_cli.audit_derivation import render_supplementary_chain

        lines.append(f"Supplementary: {render_supplementary_chain(str(task_id), result['supplementary_audit'])}")
    for row in result.get("post_merge_regression_unfinished") or []:  # AIPOS-F166 件③(行文案唯一 post_merge_regression.unfinished_regressions)
        lines.append(f"Regression: {row['line']}")
    lines += render_legal_exit_lines(result.get("legal_exits"))  # AIPOS-F230 件①: 非通过状态的全部合法出口
    loop_end = result.get("loop_end")
    if isinstance(loop_end, dict):  # AIPOS-F240 件④: 最近一次 loop 运行以上下文超限结束 = loop status 同一下一动作(出口全集)
        if loop_end.get("error"):
            lines.append(f"Loop: 最近一次 loop 运行读不出: {loop_end['error']}")
        else:
            lines.append(f"Loop: 最近一次 loop 运行 {loop_end.get('run_id')} 于 {loop_end.get('ended_at')} 以 {loop_end.get('end_reason')} 结束"
                         f"(下一动作 {loop_end.get('action')}): {loop_end.get('detail')}")
            lines += [f"  {line}" for line in str(loop_end.get("hint") or "").splitlines()]

    return "\n".join(lines)


def format_scan_output(results: list[dict[str, Any]], *, json_mode: bool = False, lane: str | list[str] | None = None,
                       view: dict[str, Any] | None = None) -> str:
    """格式化项目级扫描输出。AIPOS-F141: view(scan_project_view 结果)给出 = 冻结行标 frozen_marker, 末尾附可见卡汇总行
    (machine_zone.render_visible_summary, 四视图同一渲染); JSON 仍为行列表(冻结行带 frozen: true)。"""
    if json_mode:
        import json
        # AIPOS-F156 件②: 归属其他实例的卡(不取)追加在行列表末尾, 标 owner_other(永不带 next_card)
        others = [{**o, "owner_other": True, "derivable": False} for o in ((view or {}).get("owner_other") or [])]
        return json.dumps(results + others, indent=2, ensure_ascii=False)

    from tools.aipos_cli.machine_zone import (lane_filter_label, lane_view_declaration, owner_tag,  # F139: 仓集合
                                              render_visible_summary)

    summary = render_visible_summary(view) if view else []
    if not results:
        return "\n".join(["No active tasks found in queue.", *summary])

    lines: list[str] = []
    marker = str(lane_view_declaration()["visible_cards"]["frozen_marker"])

    scope = f"lane {lane_filter_label(lane)}, " if lane else ""
    lines.append(f"=== lybra next — project scan ({scope}{len(results)} active tasks) ===")
    # AIPOS-F133 件③: 首位给出「下一张可推进卡」(结案后取下一张的唯一出口; 判据见 verbs.schema lane_view.next_card)
    nxt = pick_next_card(results)
    if nxt is not None:
        lane_txt = f" lane={nxt.get('lane')}" if nxt.get("lane") else ""
        tag = f" {owner_tag(nxt)}" if owner_tag(nxt) else ""
        lines.append(f"下一张可推进卡: {nxt.get('task_id')}{lane_txt} priority={nxt.get('priority') or '-'}{tag}")
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
        if r.get("frozen"):
            task_id = f"{task_id} {marker}"
        if owner_tag(r):  # AIPOS-F156 件②: 归属标注(自报另标)
            task_id = f"{task_id} {owner_tag(r)}"

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

    return "\n".join(lines + summary)


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


def card_expected_base(task_metadata: dict[str, Any] | None, branch_integration: dict[str, Any] | None = None) -> str:
    """AIPOS-F150: 卡分支的「期望基座」唯一判定——卡面 fix_base_branch(fix 卡声明的合法基座)优先, 缺省 = card_base_branch。
    交回检查⑤分支合规「基座合法」(board_adapter._check_branch_compliance)与例行门拒修法渲染(loop_run_record.next_action,
    BRANCH_WRONG_BASE 的 {base})共用, 禁第二实现。"""
    fix_base = str((task_metadata or {}).get("fix_base_branch") or "").strip()
    return fix_base or card_base_branch(branch_integration)


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


_FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def card_carried_from(card_frontmatter: dict[str, Any] | None) -> dict[str, str] | None:
    """AIPOS-F186: 卡面撤换承继(card.schema carried_from; 键名读 supersede.carry_branch.card_field)的唯一读取与形校验。
    None = 不承继(缺省, 行为不变); 形不合(非对象 / 缺 task_id·branch·tip / tip 非完整 40 位 sha)= ValueError(调用方 fail-closed)。
    认领建树 _ensure_worktree / my-tasks 开工面 / card render / queue_close 结案记录同读。"""
    from tools.aipos_cli.queue_mutation import supersede_carry_declaration

    raw = (card_frontmatter or {}).get(supersede_carry_declaration()["card_field"])
    if raw in (None, "", {}):
        return None
    if not isinstance(raw, dict):
        raise ValueError(f"carried_from 须为对象, 得到 {type(raw).__name__}")
    out = {k: str(raw.get(k) or "").strip() for k in ("task_id", "branch", "tip")}
    missing = [k for k, v in out.items() if not v]
    if missing:
        raise ValueError(f"carried_from 缺 {missing}")
    if not _FULL_SHA_RE.match(out["tip"]):
        raise ValueError(f"carried_from.tip {out['tip']!r} 不是完整 40 位 sha")
    if out["branch"].startswith("-"):
        raise ValueError(f"carried_from.branch {out['branch']!r} 非法")
    return out


def carried_start_point(code_repo: Path, card_frontmatter: dict[str, Any] | None) -> tuple[dict[str, str] | None, str | None]:
    """AIPOS-F186 件②: 承继卡认领建树的起点判定(唯一实现, _ensure_worktree 在卡分支尚不存在时调用)。
    返回 (carried, refusal): 不承继 = (None, None); 承继且原卡分支当前 tip = 记录 = (carried, None)(起点 = carried['tip']);
    卡面形不合 / 原卡分支已不在 / tip 与记录不符 = (carried 或 None, 拒因原文, 文案 verbs lybra_queue_supersede.carry_texts)。"""
    from tools.aipos_cli.queue_mutation import supersede_carry_texts

    texts = supersede_carry_texts()
    try:
        carried = card_carried_from(card_frontmatter)
    except ValueError as exc:
        return None, texts["claim_invalid"].format(reason=exc)
    if carried is None:
        return None, None
    current = existing_branch_tip(code_repo, carried["branch"])
    if current is None:
        return carried, texts["claim_branch_missing"].format(repo=code_repo, **carried)
    if current != carried["tip"]:
        return carried, texts["claim_tip_moved"].format(current=current, **carried)
    return carried, None


def carried_kickoff_line(card_frontmatter: dict[str, Any] | None, task_id: str) -> str | None:
    """AIPOS-F186 件②: 开工面(my-tasks / card render)的承继来源一句(含「先把当前 base_branch 合入」的 base_sync 命令); 不承继 = None。
    卡面形不合 = ValueError(调用方给拒因)。"""
    carried = card_carried_from(card_frontmatter)
    if carried is None:
        return None
    from tools.aipos_cli.board_adapter import base_sync_command
    from tools.aipos_cli.queue_mutation import supersede_carry_texts

    base = card_base_branch()
    return supersede_carry_texts()["kickoff_line"].format(base=base, sync=base_sync_command(card_branch_name(task_id), base), **carried)


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
            缺省 None = 原认领语义(新卡分支起于 base_branch 声明); AIPOS-F186: 缺省且卡面 carried_from(撤换承继)且卡分支尚不存在 =
            起点取承继记录 tip(carried_start_point 判定, 原卡分支 tip 不符 = 拒)。

    Returns:
        {"ok": bool, "worktree_path": str, "branch": str, "message": str[, "carried_from": {...}, "carried_line": str]}
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

        # AIPOS-F186 件②: 撤换承继卡(卡面 carried_from)——卡分支尚不存在时从原卡分支记录 tip 建(而非 base_branch), 原卡分支不动;
        # 原卡分支已不在 / 当前 tip ≠ 记录 / 卡面形不合 = 拒(门认领据此 BLOCK, 队列与记录零变更)。卡分支已存在 = 复用(认领时已承继)。
        carried: dict[str, str] | None = None
        if not branch_exists and not start_point:
            carried, carry_refusal = carried_start_point(workspace_root, fm_for_mode)
            if carry_refusal:
                return {"ok": False, "worktree_path": "", "branch": branch_name, "message": carry_refusal}
            if carried is not None:
                start_point = carried["tip"]

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

        built = {
            "ok": True,
            "worktree_path": str(worktree_path),
            "branch": branch_name,
            "message": f"Worktree created: {worktree_path}",
        }
        if carried is not None:
            from tools.aipos_cli.queue_mutation import supersede_carry_texts

            built["carried_from"] = dict(carried)
            built["carried_line"] = supersede_carry_texts()["claim_note"].format(**carried)
            built["message"] += f"; {built['carried_line']}"
        return built
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "ok": False,
            "worktree_path": "",
            "branch": branch_name,
            "message": f"Exception creating worktree: {exc}",
        }


def worktree_repair_command(task_id: str, actor: str, governance_root: str | Path | None = None, *, confirm: bool = False) -> str:
    """AIPOS-F178 件③: 缺树修复命令的唯一渲染(loop 停线提示 / my-tasks 出口同用)。"""
    root = f" --workspace-root {governance_root}" if governance_root else ""
    return f"lybra queue repair --task-id {task_id} --actor {actor or '<实例>'}{root}{' --confirm' if confirm else ''}"


def repair_missing_worktree(repo_root: Path, task_id: str, actor: str, *, confirm: bool = False) -> dict[str, Any]:
    """AIPOS-F178 件③: 「已认领但缺卡工作树」检测与修复——`lybra queue repair` 族(queue_mutation.repair_bad_frontmatter 委托)
    与 loop 认领后核验 / 等待前核验共用的唯一实现(与唯一建树入口 _ensure_worktree 同模块)。

    判据只读既有唯一实现: 是否须建树 = next_resolver.card_needs_worktree; 落点 = card_worktree_location; 建树 =
    next_resolver._ensure_worktree(门认领同一建树入口, 审计卡 = 被审分支 tip 的只读 detached 取证工作树; 卡分支已存在即复用)。
    适用 = 卡在 claimed 队列目录、须建树、落点目录不存在。confirm=False = 预演(零写); confirm=True = 建树并写
    records/events/<ID>/event_worktree_repair_*.md; 已在落点 = 幂等(不重建、不写记录)。
    返回 {operation, task_id, actor, confirm, applicable, needs_worktree, queue_state, code_repo, worktree_path, branch,
    action: none|would_create|created, message, repair_record, verdict: OK|BLOCK, blocking_reasons}; 失败 = BLOCK + 原文拒因。"""
    from tools.aipos_cli.clock import file_slug, iso_z
    from tools.aipos_cli.frontmatter import FrontmatterReadError, require_frontmatter
    from tools.aipos_cli.record_writer import render_markdown, write_records_atomic
    from tools.aipos_cli.task_loader import AmbiguousTaskCard, find_task_card
    from tools.aipos_cli.workspace_config import CardRepoUnresolved
    from tools.schema_constants import RecordType
    from tools.schema_loader import SchemaLoadError

    out: dict[str, Any] = {
        "operation": "repair_worktree", "task_id": task_id, "actor": actor, "confirm": bool(confirm), "applicable": False,
        "needs_worktree": None, "queue_state": None, "code_repo": None, "worktree_path": None, "branch": None,
        "action": "none", "message": "", "repair_record": None, "verdict": "OK", "blocking_reasons": [],
    }

    def block(reason: str) -> dict[str, Any]:
        out.update({"verdict": "BLOCK", "message": reason})
        out["blocking_reasons"].append(reason)
        return out

    try:
        card_path, queue_state = find_task_card(Path(repo_root), task_id)
    except AmbiguousTaskCard as exc:
        return block(str(exc))
    if card_path is None:
        return block(f"队列中找不到卡 {task_id}")
    out["queue_state"] = queue_state
    if queue_state != "claimed":
        out["message"] = f"卡 {task_id} 在 {queue_state} 队列, 不是「已认领」, 缺树修复不适用"
        return out
    try:
        fm, _body = require_frontmatter(card_path)
        fm = {**fm, "task_id": str(fm.get("task_id") or task_id)}
        needs = card_needs_worktree(Path(repo_root), fm)
    except (FrontmatterReadError, SchemaLoadError, OSError, ValueError) as exc:
        return block(f"卡 {task_id} 是否须建树不可判定(fail-closed): {exc}")
    out["needs_worktree"] = needs
    if not needs:
        try:
            where = str(card_report_path(Path(repo_root), task_id, fm))
        except (SchemaLoadError, OSError, ValueError) as exc:
            where = f"(报告落点不可推导: {exc})"
        out["message"] = f"卡 {task_id} 只写治理根(不建卡工作树; 判据 enums.schema task_mode.product_repo_write), 产物落 {where}; 缺树修复不适用"
        return out
    try:
        code_repo, worktree = card_worktree_location(Path(repo_root), task_id, fm)
        branch = card_branch_name(forensic_subject(fm) or task_id)
    except CardRepoUnresolved as exc:
        return block(f"{exc.code}: {exc.reason}")
    except (SchemaLoadError, OSError, ValueError) as exc:
        return block(f"卡 {task_id} 工作树落点不可推导: {exc}")
    out.update({"code_repo": str(code_repo), "worktree_path": str(worktree), "branch": branch})
    if worktree.is_dir():
        out["message"] = f"卡工作树已在落点 {worktree}(幂等: 不重建, 不写记录)"
        return out
    out["applicable"] = True
    if not confirm:
        out["action"] = "would_create"
        out["message"] = (f"预演: 卡 {task_id} 已认领但缺卡工作树, 将在 {code_repo} 建 {worktree}(分支 {branch}; "
                          f"经门认领同一建树入口 _ensure_worktree, 卡分支已存在即复用)。确认: {worktree_repair_command(task_id, actor, confirm=True)}")
        return out
    built = _ensure_worktree(Path(repo_root), task_id, card_frontmatter=fm)
    if not built.get("ok"):
        return block(f"建卡工作树失败: {built.get('message')}")
    if not Path(str(built.get("worktree_path") or worktree)).is_dir():
        return block(f"落点 {worktree} 已被非目录占用, 未建卡工作树({built.get('message')}); 出口: 核对该路径是否在途产物, 移走后重试")
    out["action"] = "created"
    out["worktree_path"] = str(built.get("worktree_path") or worktree)
    timestamp = iso_z()
    record_id = f"event_worktree_repair_{task_id}_{file_slug('compact', timestamp)}"
    metadata = {
        "record_type": RecordType.TASK_PROGRESS_EVENT,
        "event_type": "worktree_repair",
        "task_id": task_id,
        "actor": actor,
        "timestamp": timestamp,
        "repair": "AIPOS-F178 件③: 已认领但缺卡工作树, 经门认领同一建树入口补建(卡面与队列状态不变)",
        "code_repo": str(code_repo),
        "worktree_path": out["worktree_path"],
        "branch": branch,
        "ensure_message": str(built.get("message") or ""),
    }
    body = (f"# Worktree Repair: {task_id}\n\n卡 `{card_path}` 已认领但缺卡工作树; 补建于 `{out['worktree_path']}`(分支 `{branch}`)。\n\n"
            "由 `lybra queue repair --confirm`(或 loop 认领后核验)经 next_resolver._ensure_worktree 写入。\n")
    try:
        written = write_records_atomic(Path(repo_root), [("event", record_id, render_markdown(metadata, body, list(metadata)), task_id)])
    except (OSError, ValueError, RuntimeError) as exc:
        return block(f"卡工作树已建于 {out['worktree_path']}, 但修复记录写入失败: {exc}")
    out["repair_record"] = written["paths"][0]
    out["message"] = f"已补建卡工作树 {out['worktree_path']}(分支 {branch}); 修复记录 {out['repair_record']}"
    return out


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


def _run_product_command(command: str, action_type: str, governance_root: Path | str | None = None) -> dict[str, Any]:
    """执行一条派生产品命令(`lybra ...`, 每步过门零旁路), 超时读声明。返回 execute_derived_action 响应形。
    AIPOS-F144: governance_root(推导所在治理根)给出 = 经环境变量 LYBRA_WORKSPACE_ROOT 下传子进程(唯一治理根解析
    resolve_governance_root 的显式级来源), 派生命令不依赖 loop 的 cwd、不回落 home 级活动项目; 命令文本不变(可照抄)。"""
    import shlex
    import subprocess

    timeout = loop_step_timeout_seconds()
    # AIPOS-F134 件①: loop 已校验的驱动方实例随子进程环境下传(薄壳按实例取凭据, two_phase_shell_factory.driver_instance_hint 唯一读取)
    from tools.aipos_cli.two_phase_shell_factory import DRIVER_INSTANCE_ENV
    from tools.aipos_cli.workspace_config import LEGACY_WORKSPACE_ROOT_ENV, WORKSPACE_ROOT_ENV

    env = dict(os.environ)
    scoped_actor = _scoped_driver().get("actor")
    if scoped_actor:
        env[DRIVER_INSTANCE_ENV] = scoped_actor
    if governance_root is not None:
        env.pop(LEGACY_WORKSPACE_ROOT_ENV, None)
        env[WORKSPACE_ROOT_ENV] = str(Path(governance_root).expanduser().resolve())
    try:
        argv = shlex.split(command)
        if argv and argv[0] == "lybra":
            # AIPOS-F143 件①: 派生 lybra 子命令不按调用方 PATH 找(唯一构造 workstation_wiring.lybra_cli_invocation)
            from tools.aipos_cli.workstation_wiring import lybra_cli_invocation

            argv, env = lybra_cli_invocation(argv[1:], env)
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "action_type": action_type,
            "message": f"{action_type} 子进程超时 (>{timeout:g}s, 声明 verbs.schema lybra_loop.step_timeout_seconds)",
            "command": command,
            "exit_code": 124,
            "output": f"Command timed out after {timeout:g} seconds",
        }
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        # AIPOS-F143 件①: 异常原文(类名 + 原文, 如 FileNotFoundError: [Errno 2] …)进 message 首行与 cause, 调用方禁再吞成「失败: 1」
        cause = f"{type(exc).__name__}: {exc}"
        return {
            "ok": False,
            "action_type": action_type,
            "message": f"{action_type} 执行异常: {cause}",
            "command": command,
            "exit_code": 1,
            "output": cause,
            "cause": cause,
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
    # AIPOS-F143 件①: 失败真因 = stderr 关键行(无 stderr 取 stdout 末行非空行), 进 message 首行与 cause(运行记录 end_message 取首行)
    cause = ""
    if not success:
        cause = key_lines[-1] if key_lines else next((ln.strip() for ln in reversed(stdout.splitlines()) if ln.strip()), "")
    if cause:
        message += f": {cause}"
    return {
        "ok": success,
        "action_type": action_type,
        "message": message,
        "command": command,
        "exit_code": result.returncode,
        "output": output,
        "stderr_key_lines": key_lines,
        "cause": cause,
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
    run = _run_product_command(command, "claim", governance_root=workspace_root)
    if not run.get("ok"):
        # AIPOS-F143 件①: 原写「claim 失败: <退出码>」吞掉真因(接入项目首卡实撞: FileNotFoundError 只剩「claim 失败: 1」);
        # 现首行 = 退出码 + 真因原文(子进程异常 / stderr 关键行), 超时(124)沿用超时文案
        if run.get("exit_code") != 124:
            cause = str(run.get("cause") or "").strip() or "子进程无 stderr/stdout 输出"
            run["message"] = f"claim 失败(exit {run.get('exit_code')}): {cause}"
        return run

    task_path, _queue = _find_task_in_queue(workspace_root, task_id)
    if not task_path:
        # AIPOS-F211 件②: 门报认领成功却在队列找不到卡 = 停线点名(原按 {"task_mode": "code"} 猜卡面, 第二份缺省)
        return {"ok": False, "action_type": "claim",
                "message": f"门报认领成功但队列中找不到卡 {task_id}, 无法核验卡工作树; 出口: lybra next --task-id {task_id} 重推导",
                "command": command, "exit_code": 1, "output": str(run.get("output") or ""), "worktree_path": ""}
    fm = _read_frontmatter(task_path)
    assigned_to = str(fm.get("assigned_to") or fm.get("agent_instance") or "")
    worktree_path = ""
    # AIPOS-F89 件③a: 认领须建树的判据与门侧同一函数 card_needs_worktree(代码卡 + 代码卡的审计卡)
    repair_note = ""
    repair: dict[str, Any] = {}
    if card_needs_worktree(workspace_root, fm):
        view = card_workstation_view(workspace_root, task_id, fm)
        if not view.get("worktree_exists"):
            # AIPOS-F178 件③: 认领后核验缺树 = 自动走缺树修复唯一实现(queue repair 同一函数, 写修复记录); 修不了才停并给命令
            repair = repair_missing_worktree(workspace_root, task_id, assigned_to, confirm=True)
            if repair["action"] == "created":
                view = card_workstation_view(workspace_root, task_id, fm)
                repair_note = f"\nWorktree repair: {repair['message']}"
        if not view.get("worktree_exists"):
            refusal = view.get("worktree_refusal") or {}
            return {
                "ok": False,
                "action_type": "claim",
                "message": (f"门报认领成功但卡工作树不在落点({refusal.get('code') or 'WORKTREE_UNRESOLVED'}), 自动补建未成: "
                            f"{repair.get('message') or ''}; 出口: {worktree_repair_command(task_id, assigned_to, workspace_root)} 预演看拒因, 修好后加 --confirm"),
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
        "output": str(run.get("output") or "") + (f"\nWorktree: {worktree_path}" if worktree_path else "") + repair_note,
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
    # AIPOS-F196 件①: 不建卡分支的卡(card_needs_worktree 唯一判据; 固定候选验证卡)无分支可查, 交回产物由入口按候选核(validate_task_artifact)
    _return_task_path, _q = _find_task_in_queue(workspace_root, task_id) if action_type == "return" else (None, None)
    _return_needs_branch = (card_needs_worktree(workspace_root, {**_read_frontmatter(_return_task_path), "task_id": task_id})
                            if _return_task_path else True)
    if action_type == "return" and _return_needs_branch:
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
    return _run_product_command(command, action_type, governance_root=workspace_root)
