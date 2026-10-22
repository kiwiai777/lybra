"""AIPOS-F187 件③④ — 卡工作树与审计工作树清理。

一句话: 卡结案(closure 落盘后 / AIPOS-F209 顾问复核结案 mark-concluded 后)/ 撤回 / 撤换后, 该卡工作树与其审计轮工作树干净的自动 `git worktree remove`(分支保留),
脏的不碰只报告; 存量由 `lybra project cleanup-worktrees` 按同一判据批量处理(跨 project.json repos.items 全部仓)。

判据唯一声明 = transitions.schema queue_mutations.worktree_cleanup(终态 / 承继保护状态 / 事件类型 / 判定动作集);
文案唯一声明 = verbs.schema lybra_project_cleanup_worktrees.texts(自动清理与批量命令同读)。
判一棵树的唯一实现 = judge_worktree; 未提交改动计数唯一实现 = uncommitted_count(AIPOS-F186 承继判定同读)。
工作树落点只经 next_resolver.card_worktree_location(与认领建树同一推导), 工作树根只经 next_resolver._resolve_worktree_root。
清理失败绝不影响结案 / 撤回 / 撤换本身(cleanup_after 只把失败写进输出与记录, 不抛)。
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

CLEANUP_VERB = "lybra_project_cleanup_worktrees"
CLEANUP_TEXT_KEYS = (
    "header_preview", "header_confirm", "repo_line", "repo_unreadable", "remove", "removed", "dirty", "unreadable", "in_flight",
    "carried", "failed", "unknown", "summary", "confirm_hint", "auto_line", "auto_kept", "auto_kept_item", "auto_none", "auto_error",
    "reason_dirty", "reason_in_flight", "reason_carried", "reason_unreadable", "reason_failed", "reason_removable", "loop_residual",
)
#: 判为「保留」并须在结案输出 / loop status 报告的动作(声明 actions 的子集; 判据见 judge_worktree)
KEPT_ACTIONS = ("dirty", "in_flight", "carried", "unreadable", "failed")


#: AIPOS-F214 件③: 投影键(声明段不得另列; 值由 cleanup_declaration 从单源投影)
PROJECTED_STATE_KEYS = ("terminal_states", "carry_protect_states")


def cleanup_declaration() -> dict[str, Any]:
    """transitions.schema queue_mutations.worktree_cleanup 唯一读取口。缺键 = SchemaLoadError。
    AIPOS-F214 件③(F208 P2-2): 状态集合不另列, 投影——terminal_states = enums queue_state 终态(task_loader.QUEUE_TERMINAL_STATES),
    carry_protect_states = 撤换承继 successor_states(queue_mutation.supersede_carry_declaration); 声明段另写这两个键 = SchemaLoadError。"""
    from tools.aipos_cli.queue_mutation import supersede_carry_declaration
    from tools.aipos_cli.task_loader import QUEUE_TERMINAL_STATES
    from tools.schema_loader import SchemaLoadError, code_repo_schema_root, load_schema

    decl = ((load_schema("transitions", code_repo_schema_root()).get("queue_mutations") or {}).get("worktree_cleanup"))
    need = ("event_type", "actions", "triggers", "closing_triggers")
    if not isinstance(decl, dict) or any(not decl.get(k) for k in need):
        raise SchemaLoadError(f"transitions.schema.json queue_mutations.worktree_cleanup 未声明齐 {list(need)}")
    copies = [k for k in PROJECTED_STATE_KEYS if k in decl]
    if copies:
        raise SchemaLoadError(f"transitions.schema.json queue_mutations.worktree_cleanup 不得另列 {copies}(投影自 enums queue_state 终态 / "
                              "queue_mutations.transitions.supersede.carry_branch.successor_states, AIPOS-F214 件③)")
    if not isinstance(decl["triggers"], dict) or not set(decl["closing_triggers"]) <= set(decl["triggers"]):
        raise SchemaLoadError("transitions.schema.json queue_mutations.worktree_cleanup.closing_triggers 须为 triggers 键的子集")
    return {**decl, "terminal_states": tuple(QUEUE_TERMINAL_STATES),
            "carry_protect_states": tuple(supersede_carry_declaration()["successor_states"]),
            "actions": tuple(decl["actions"]), "closing_triggers": tuple(decl["closing_triggers"])}


def cleanup_texts() -> dict[str, str]:
    """verbs.schema lybra_project_cleanup_worktrees.texts 唯一读取口(缺键 = SchemaLoadError)。"""
    from tools.schema_loader import SchemaLoadError, code_repo_schema_root, load_schema

    texts = (((load_schema("verbs", code_repo_schema_root()).get("verbs") or {}).get(CLEANUP_VERB) or {}).get("texts"))
    if not isinstance(texts, dict) or any(not isinstance(texts.get(k), str) for k in CLEANUP_TEXT_KEYS):
        raise SchemaLoadError(f"verbs.schema.json verbs.{CLEANUP_VERB}.texts 未声明齐 {list(CLEANUP_TEXT_KEYS)}")
    return texts


def uncommitted_count(worktree: Path) -> int:
    """工作树未提交改动计数(`git status --porcelain --untracked-files=all` 非空行数, 含未跟踪, 不含已忽略)——唯一实现
    (AIPOS-F186 承继判定 queue_mutation.supersede_carry_plan 与本模块清理判据同读)。读不出 = ValueError(含 git 原文)。"""
    try:
        proc = subprocess.run(["git", "-C", str(worktree), "status", "--porcelain", "--untracked-files=all"],
                              capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError(f"git status 失败: {exc}") from exc
    if proc.returncode != 0:
        raise ValueError((proc.stderr or proc.stdout).strip() or f"git status 退出码 {proc.returncode}")
    return len([ln for ln in proc.stdout.splitlines() if ln.strip()])


def _checked_out_branch(worktree: Path) -> str:
    """工作树当前检出分支名; detached = "(detached)"。只读。"""
    try:
        proc = subprocess.run(["git", "-C", str(worktree), "symbolic-ref", "--short", "-q", "HEAD"], capture_output=True, text=True,
                              timeout=10)
    except (OSError, subprocess.SubprocessError):
        return "(未知)"
    return proc.stdout.strip() if proc.returncode == 0 and proc.stdout.strip() else "(detached)"


def _read_card(governance_root: Path, task_id: str) -> tuple[Path | None, str | None, dict[str, Any]]:
    """卡在队列中的 (路径, 队列状态, frontmatter); 不在队列 = (None, None, {})。卡面读不出 = ValueError(fail-closed)。"""
    from tools.aipos_cli.frontmatter import FrontmatterReadError, require_frontmatter
    from tools.aipos_cli.task_loader import find_task_card

    path, state = find_task_card(governance_root, task_id)
    if path is None:
        return None, None, {}
    try:
        fm, _body = require_frontmatter(path)
    except FrontmatterReadError as exc:
        raise ValueError(f"卡 {task_id} 卡面读不出: {exc}") from exc
    return path, state, {**fm, "task_id": str(fm.get("task_id") or task_id)}


def carry_referrers(governance_root: Path, decl: dict[str, Any] | None = None) -> dict[str, str]:
    """承继保护: {被承继原卡 task_id: 承继卡 task_id}——队列 carry_protect_states 目录里卡面 carried_from 所指原卡
    (形校验唯一 next_resolver.card_carried_from; 形不合的卡面不构成保护, 由认领时拒因暴露)。"""
    from tools.aipos_cli.frontmatter import FrontmatterReadError, require_frontmatter
    from tools.aipos_cli.next_resolver import card_carried_from
    from tools.aipos_cli.task_loader import iter_queue_task_paths

    decl = decl or cleanup_declaration()
    out: dict[str, str] = {}
    for path in iter_queue_task_paths(Path(governance_root), states=tuple(decl["carry_protect_states"])):  # 卡遍历唯一口
        try:
            fm, _body = require_frontmatter(path)
            carried = card_carried_from(fm)
        except (FrontmatterReadError, ValueError):
            continue
        if carried:
            out[carried["task_id"]] = str(fm.get("task_id") or path.stem)
    return out


def judge_worktree(governance_root: Path, *, task_id: str, worktree: Path, code_repo: Path, state: str | None,
                   card_frontmatter: dict[str, Any] | None, referrers: dict[str, str], decl: dict[str, Any]) -> dict[str, Any]:
    """判一棵卡工作树(唯一判据; 声明 transitions.schema queue_mutations.worktree_cleanup), 只读。

    依次: 不在盘 = absent; 所属卡(审计树 = 审计卡或其被审卡任一)不在终态 = in_flight; 被 carry_protect_states 卡的
    carried_from 引用 = carried; 未提交改动 > 0 = dirty; 读不出 = unreadable; 否则 = remove。"""
    from tools.aipos_cli.audit_derivation import audit_card_reviewed_id, is_audit_card

    fm = card_frontmatter if isinstance(card_frontmatter, dict) else {}
    item: dict[str, Any] = {"task_id": task_id, "worktree": str(worktree), "code_repo": str(code_repo), "state": state or "(不在队列)",
                            "branch": "", "action": "absent", "uncommitted": None, "error": None, "by": None}
    if not Path(worktree).is_dir():
        return item
    item["branch"] = _checked_out_branch(Path(worktree))
    terminal = state in decl["terminal_states"]
    owner = task_id
    if is_audit_card(task_id, fm):
        reviewed = audit_card_reviewed_id(task_id, fm)
        if reviewed:
            try:
                _path, reviewed_state, _fm = _read_card(Path(governance_root), reviewed)
            except ValueError as exc:
                item.update(action="unreadable", error=str(exc))
                return item
            terminal = terminal or reviewed_state in decl["terminal_states"]
            owner = reviewed
    if not terminal:
        item["action"] = "in_flight"
        return item
    if owner == task_id and task_id in referrers:
        item.update(action="carried", by=referrers[task_id])
        return item
    try:
        count = uncommitted_count(Path(worktree))
    except ValueError as exc:
        item.update(action="unreadable", error=str(exc))
        return item
    item["uncommitted"] = count
    item["action"] = "dirty" if count else "remove"
    return item


def remove_worktree(code_repo: Path, worktree: Path) -> str | None:
    """`git worktree remove <树>`(不加 --force; 分支保留)。成功 = None, 失败 = 原因原文。"""
    try:
        proc = subprocess.run(["git", "-C", str(code_repo), "worktree", "remove", str(worktree)], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"{type(exc).__name__}: {exc}"
    if proc.returncode != 0:
        return (proc.stderr or proc.stdout).strip() or f"git worktree remove 退出码 {proc.returncode}"
    return None


def _card_tree_targets(governance_root: Path, task_id: str) -> list[dict[str, Any]]:
    """一张卡的清理对象 = 本卡工作树 + 其审计轮工作树(audit_round_ids)+ 补审卡工作树(supplementary_audit_cards); 落点
    card_worktree_location(与认领建树同一推导)。卡不建树(只写治理根)= 不列。落点不可推导 = 项带 error(判为 unreadable, 不碰)。"""
    from tools.aipos_cli.audit_derivation import audit_round_ids, supplementary_audit_cards
    from tools.aipos_cli.next_resolver import card_needs_worktree, card_worktree_location
    from tools.aipos_cli.workspace_config import CardRepoUnresolved
    from tools.schema_loader import SchemaLoadError

    out: list[dict[str, Any]] = []
    audits = audit_round_ids(task_id, governance_root) + [c["audit_task_id"] for c in supplementary_audit_cards(governance_root, task_id)]
    for tid in [task_id] + [r for r in dict.fromkeys(audits) if r != task_id]:
        path, state, fm = _read_card(governance_root, tid)
        if path is None:
            continue
        try:
            if not card_needs_worktree(governance_root, fm):
                continue
            code_repo, worktree = card_worktree_location(governance_root, tid, fm)
        except CardRepoUnresolved as exc:
            out.append({"task_id": tid, "state": state, "fm": fm, "error": f"{exc.code}: {exc.reason}"})
            continue
        except (SchemaLoadError, OSError, ValueError) as exc:
            out.append({"task_id": tid, "state": state, "fm": fm, "error": str(exc)})
            continue
        out.append({"task_id": tid, "state": state, "fm": fm, "code_repo": code_repo, "worktree": worktree})
    return out


def _fill(template: str, **values: Any) -> str:
    return template.format(**{k: ("" if v is None else v) for k, v in values.items()})


def _kept_reason(item: dict[str, Any], texts: dict[str, str]) -> str:
    action = item["action"]
    if action == "dirty":
        return _fill(texts["reason_dirty"], uncommitted=item.get("uncommitted"))
    if action == "in_flight":
        return _fill(texts["reason_in_flight"], task_id=item["task_id"], state=item["state"])
    if action == "carried":
        return _fill(texts["reason_carried"], by=item.get("by"))
    if action == "unreadable":
        return _fill(texts["reason_unreadable"], error=item.get("error"))
    return _fill(texts["reason_failed"], error=item.get("error"))


def card_worktree_view(governance_root: Path, task_id: str, *, confirm: bool, include_carried_source: bool = False) -> dict[str, Any]:
    """一张卡(及其审计轮; include_carried_source = 另含卡面 carried_from 所指原卡)的工作树判定, confirm = 对判为 remove 的执行删除。
    返回 {task_id, items: [judge_worktree 结果(+ 删除后 action removed / failed)], removed, kept}。不写记录。"""
    from tools.aipos_cli.next_resolver import card_carried_from

    gov = Path(governance_root)
    decl = cleanup_declaration()
    referrers = carry_referrers(gov, decl)
    owners = [task_id]
    if include_carried_source:
        _path, _state, fm = _read_card(gov, task_id)
        try:
            carried = card_carried_from(fm)
        except ValueError:
            carried = None
        if carried and carried["task_id"] not in owners:
            owners.append(carried["task_id"])
    items: list[dict[str, Any]] = []
    for owner in owners:
        for target in _card_tree_targets(gov, owner):
            if target.get("error"):
                items.append({"task_id": target["task_id"], "worktree": "(落点不可推导)", "code_repo": "", "state": target.get("state"),
                              "branch": "", "action": "unreadable", "uncommitted": None, "error": target["error"], "by": None})
                continue
            item = judge_worktree(gov, task_id=target["task_id"], worktree=target["worktree"], code_repo=target["code_repo"],
                                  state=target["state"], card_frontmatter=target["fm"], referrers=referrers, decl=decl)
            if confirm and item["action"] == "remove":
                error = remove_worktree(Path(item["code_repo"]), Path(item["worktree"]))
                item.update({"action": "failed", "error": error} if error else {"action": "removed"})
            items.append(item)
    return {"task_id": task_id, "items": items,
            "removed": [i for i in items if i["action"] == "removed"],
            "kept": [i for i in items if i["action"] in KEPT_ACTIONS]}


def _summary_line(view: dict[str, Any], trigger: str, texts: dict[str, str]) -> str:
    present = [i for i in view["items"] if i["action"] != "absent"]
    if not present:
        return _fill(texts["auto_none"], trigger=trigger, task_id=view["task_id"])
    rest = ""
    if view["kept"]:
        listed = "; ".join(_fill(texts["auto_kept_item"], worktree=i["worktree"], reason=_kept_reason(i, texts)) for i in view["kept"])
        rest = _fill(texts["auto_kept"], kept=len(view["kept"]), items=listed)
    return _fill(texts["auto_line"], trigger=trigger, task_id=view["task_id"], removed=len(view["removed"]), rest=rest)


def _write_cleanup_record(governance_root: Path, view: dict[str, Any], *, trigger: str, actor: str, line: str) -> str:
    """清理结果落 records/events/<卡>/(event_type 读声明; 记录写入唯一口 record_writer.write_records_atomic)。返回治理根相对路径。"""
    from tools.aipos_cli.clock import file_slug, iso_z
    from tools.aipos_cli.record_writer import render_markdown, write_records_atomic
    from tools.schema_constants import RecordType

    decl = cleanup_declaration()
    texts = cleanup_texts()
    task_id = view["task_id"]
    timestamp = iso_z()
    record_id = f"{decl['event_type']}_{task_id}_{file_slug('compact', timestamp)}"
    metadata = {
        "record_type": RecordType.TASK_PROGRESS_EVENT,
        "event_type": str(decl["event_type"]),
        "task_id": task_id,
        "actor": actor or "(unknown)",
        "timestamp": timestamp,
        "trigger": trigger,
        "removed": [i["worktree"] for i in view["removed"]],
        "kept": [f"{i['worktree']} — {_kept_reason(i, texts)}" for i in view["kept"]],
    }
    body = (f"# Worktree Cleanup: {task_id}\n\n{line}\n\n"
            "判据 transitions.schema queue_mutations.worktree_cleanup; 实现 tools/aipos_cli/worktree_cleanup.cleanup_after"
            "(分支保留, 脏树不碰)。\n")
    written = write_records_atomic(Path(governance_root), [("event", record_id, render_markdown(metadata, body, list(metadata)), task_id)])
    return str(written["paths"][0])


def cleanup_after(governance_root: Path, task_id: str, *, trigger: str, actor: str) -> dict[str, Any]:
    """结案 / 撤回 / 撤换之后的自动清理(声明 worktree_cleanup.triggers; close_task / mark_concluded_task(AIPOS-F209 conclude)/
    withdraw_task / supersede_task 调用)。trigger 不在声明 triggers = 不清理, 原因进 error(fail-closed)。closing_triggers(结案类)
    一并清卡面 carried_from 所指原卡的树。
    绝不抛: 声明 / git / 记录写入失败只进返回的 line 与 error(调用方照常完成本动作)。有任一树在盘才写清理记录。
    返回 {ok, task_id, trigger, line, removed: [路径], kept: [{worktree, action, reason}], record, error}。"""
    from tools.aipos_cli.frontmatter import FrontmatterReadError
    from tools.schema_loader import SchemaLoadError

    out: dict[str, Any] = {"ok": False, "task_id": task_id, "trigger": trigger, "line": "", "removed": [], "kept": [], "record": None,
                           "error": None}
    try:
        texts = cleanup_texts()
    except SchemaLoadError as exc:
        out.update(error=str(exc), line=f"工作树清理({trigger} {task_id})未完成: {exc}")
        return out
    try:
        decl = cleanup_declaration()
        if trigger not in decl["triggers"]:
            raise ValueError(f"trigger {trigger!r} 不在 transitions.schema queue_mutations.worktree_cleanup.triggers {sorted(decl['triggers'])}")
        view = card_worktree_view(Path(governance_root), task_id, confirm=True, include_carried_source=trigger in decl["closing_triggers"])
        line = _summary_line(view, trigger, texts)
        out.update(line=line, removed=[i["worktree"] for i in view["removed"]],
                   kept=[{"worktree": i["worktree"], "action": i["action"], "reason": _kept_reason(i, texts)} for i in view["kept"]])
        if any(i["action"] != "absent" for i in view["items"]):
            out["record"] = _write_cleanup_record(Path(governance_root), view, trigger=trigger, actor=actor, line=line)
        out["ok"] = not any(i["action"] == "failed" for i in view["items"])
    except (SchemaLoadError, FrontmatterReadError, OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        out.update(error=f"{type(exc).__name__}: {exc}", line=_fill(texts["auto_error"], trigger=trigger, task_id=task_id,
                                                                   error=f"{type(exc).__name__}: {exc}"))
    return out


def residual_line(governance_root: Path, task_id: str) -> str | None:
    """loop status(结案后取下一张)用: 本卡(含审计轮)仍在盘且被保留的工作树一句(只读, 不删); 无保留 = None。
    判不出 = 原因原文(不吞)。"""
    from tools.aipos_cli.frontmatter import FrontmatterReadError
    from tools.schema_loader import SchemaLoadError

    try:
        texts = cleanup_texts()
        view = card_worktree_view(Path(governance_root), task_id, confirm=False)
    except (SchemaLoadError, FrontmatterReadError, OSError, ValueError, subprocess.SubprocessError) as exc:
        return f"工作树清理状态判不出: {type(exc).__name__}: {exc}"
    pending = [i for i in view["items"] if i["action"] in KEPT_ACTIONS or i["action"] == "remove"]
    if not pending:
        return None
    listed = "; ".join(_fill(texts["auto_kept_item"], worktree=i["worktree"],
                             reason=_kept_reason(i, texts) if i["action"] != "remove" else texts["reason_removable"]) for i in pending)
    return _fill(texts["loop_residual"], items=listed)


# ---------------------------------------------------------------------------
# 件④ 存量批量: lybra project cleanup-worktrees
# ---------------------------------------------------------------------------

def _listed_worktrees(code_repo: Path) -> list[Path]:
    """`git worktree list --porcelain` 的工作树路径(含主检出; 调用方按工作树根过滤)。失败 = ValueError(原文)。"""
    try:
        proc = subprocess.run(["git", "-C", str(code_repo), "worktree", "list", "--porcelain"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError(f"git worktree list 失败: {exc}") from exc
    if proc.returncode != 0:
        raise ValueError((proc.stderr or proc.stdout).strip() or f"git worktree list 退出码 {proc.returncode}")
    return [Path(line[len("worktree "):].strip()) for line in proc.stdout.splitlines() if line.startswith("worktree ")]


def cleanup_project_worktrees(governance_root: Path, *, confirm: bool) -> dict[str, Any]:
    """件④: 本项目全部产品仓(project.json repos.items, 无 repos 段 = code_repo)工作树根下的卡工作树, 按 judge_worktree 同一判据
    预演 / 清理。目录名 = 卡 ID(认领建树 <工作树根>/<卡ID>); 查不到卡 = unknown(留)。主检出与工作树根外的树不列。
    返回 {project, governance_root, confirm, repos: [{repo, path, root, items, error}], counts: {动作: 数}, ok}。"""
    from tools.aipos_cli.next_resolver import _resolve_worktree_root
    from tools.aipos_cli.workspace_config import project_repos, read_project_json

    gov = Path(governance_root).resolve()
    decl = cleanup_declaration()
    referrers = carry_referrers(gov, decl)
    repos = project_repos(gov)
    if repos["declared"]:
        pairs = sorted((str(name), Path(path)) for name, path in repos["items"].items())
    else:
        pairs = [(Path(repos["code_repo"]).name, Path(repos["code_repo"]))] if repos["code_repo"] else []
    result: dict[str, Any] = {"project": str(read_project_json(gov).get("project") or gov.name), "governance_root": str(gov),
                              "confirm": bool(confirm), "repos": [], "counts": {}, "ok": True}
    for name, repo_path in pairs:
        entry: dict[str, Any] = {"repo": name, "path": str(repo_path), "root": "", "items": [], "error": None}
        result["repos"].append(entry)
        try:
            root = _resolve_worktree_root(gov, repo_path).resolve()
            entry["root"] = str(root)
            listed = _listed_worktrees(repo_path)
        except (OSError, ValueError) as exc:
            entry["error"] = str(exc)
            result["ok"] = False
            continue
        for worktree in listed:
            if worktree.resolve().parent != root:
                continue  # 主检出 / 工作树根外(如 finalize 回归自身临时树)不在清单
            task_id = worktree.name
            try:
                path, state, fm = _read_card(gov, task_id)
            except ValueError as exc:
                item = {"task_id": task_id, "worktree": str(worktree), "code_repo": str(repo_path), "state": "(卡面读不出)",
                        "branch": _checked_out_branch(worktree), "action": "unreadable", "uncommitted": None, "error": str(exc), "by": None}
                entry["items"].append(item)
                result["counts"]["unreadable"] = result["counts"].get("unreadable", 0) + 1
                continue
            if path is None:
                item = {"task_id": task_id, "worktree": str(worktree), "code_repo": str(repo_path), "state": "(不在队列)",
                        "branch": _checked_out_branch(worktree), "action": "unknown", "uncommitted": None, "error": None, "by": None}
            else:
                item = judge_worktree(gov, task_id=str(fm.get("task_id") or task_id), worktree=worktree, code_repo=repo_path,
                                      state=state, card_frontmatter=fm, referrers=referrers, decl=decl)
            if confirm and item["action"] == "remove":
                error = remove_worktree(repo_path, worktree)
                item.update({"action": "failed", "error": error} if error else {"action": "removed"})
                if error:
                    result["ok"] = False
            entry["items"].append(item)
            result["counts"][item["action"]] = result["counts"].get(item["action"], 0) + 1
    return result


def render_project_cleanup(result: dict[str, Any]) -> list[str]:
    """批量清理人读输出(文案 verbs.schema lybra_project_cleanup_worktrees.texts)。"""
    texts = cleanup_texts()
    lines = [_fill(texts["header_confirm" if result["confirm"] else "header_preview"], project=result["project"])]
    for entry in result["repos"]:
        if entry["error"]:
            lines.append(_fill(texts["repo_unreadable"], repo=entry["repo"], error=entry["error"]))
            continue
        lines.append(_fill(texts["repo_line"], repo=entry["repo"], root=entry["root"], count=len(entry["items"])))
        for item in entry["items"]:
            lines.append(_fill(texts[item["action"]], worktree=item["worktree"], task_id=item["task_id"], state=item["state"],
                               branch=item["branch"], uncommitted=item["uncommitted"], error=item["error"], by=item["by"]))
    counts = result["counts"]
    lines.append(_fill(texts["summary"], **{k: counts.get(k, 0) + (counts.get("removed", 0) if k == "remove" else 0)
                                            for k in ("remove", "dirty", "in_flight", "carried", "unknown", "unreadable", "failed")}))
    if not result["confirm"] and counts.get("remove"):
        lines.append(_fill(texts["confirm_hint"], governance_root=result["governance_root"]))
    return lines


def run_cleanup_cli(args: Any) -> int:
    """`lybra project cleanup-worktrees` 薄壳: 退出码读 verbs.schema lybra_project_cleanup_worktrees.exit_codes。"""
    import json
    import sys

    from tools.aipos_cli.aipos_cli import _find_repo_root_for_args
    from tools.aipos_cli.verb_contract import declared_exit_code
    from tools.schema_loader import SchemaLoadError

    if getattr(args, "dry_run", False) and getattr(args, "confirm", False):
        print("lybra project cleanup-worktrees: --dry-run 与 --confirm 不能同给", file=sys.stderr)
        return declared_exit_code(CLEANUP_VERB, "usage")
    try:
        gov = Path(_find_repo_root_for_args(args))
        result = cleanup_project_worktrees(gov, confirm=bool(getattr(args, "confirm", False)))
        lines = render_project_cleanup(result)
    except (FileNotFoundError, SchemaLoadError, OSError, ValueError) as exc:
        print(f"lybra project cleanup-worktrees: {type(exc).__name__}: {exc}", file=sys.stderr)
        return declared_exit_code(CLEANUP_VERB, "unreadable")
    if getattr(args, "json", False):
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("\n".join(lines))
    return declared_exit_code(CLEANUP_VERB, "ok" if result["ok"] else "failed")


# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation  # noqa: E402
check_direct_invocation(__name__)
