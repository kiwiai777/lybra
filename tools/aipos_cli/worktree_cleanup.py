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
KEPT_ACTIONS = ("dirty", "in_flight", "carried", "unreadable", "failed", "claim_residue")


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


# ---------------------------------------------------------------------------
# AIPOS-F285 件③: 认领半成品(门认领超时 / 建树中断留下的卡分支与半建工作树)唯一判定
# 声明 transitions.schema queue_mutations.worktree_cleanup.claim_residue; 文案 verbs.schema lybra_project_cleanup_worktrees.claim_residue_texts
# ---------------------------------------------------------------------------

RESIDUE_TEXTS_KEY = "claim_residue_texts"


def claim_residue_declaration() -> dict[str, Any]:
    """transitions.schema queue_mutations.worktree_cleanup.claim_residue 唯一读取口(缺键 = SchemaLoadError)。"""
    from tools.schema_loader import SchemaLoadError, code_repo_schema_root, load_schema

    decl = (((load_schema("transitions", code_repo_schema_root()).get("queue_mutations") or {}).get("worktree_cleanup") or {})
            .get("claim_residue"))
    if (not isinstance(decl, dict) or not str(decl.get("incomplete_marker") or "").strip()
            or not isinstance(decl.get("queue_states"), list) or not decl["queue_states"]):
        raise SchemaLoadError("transitions.schema.json queue_mutations.worktree_cleanup.claim_residue(incomplete_marker / queue_states)未声明齐")
    return decl


def residue_text(key: str, /, **values: Any) -> str:
    """认领残留文案(verbs.schema lybra_project_cleanup_worktrees.claim_residue_texts, 中英双语经 i18n 唯一读取器), 按用户语言填好。"""
    from tools.aipos_cli.i18n import fill, resolve_language, surface_texts
    from tools.schema_loader import SchemaLoadError, code_repo_schema_root, load_schema

    where = f"verbs.schema.json verbs.{CLEANUP_VERB}.{RESIDUE_TEXTS_KEY}"
    block = (((load_schema("verbs", code_repo_schema_root()).get("verbs") or {}).get(CLEANUP_VERB) or {}).get(RESIDUE_TEXTS_KEY))
    lang = resolve_language()
    table = surface_texts(block, lang, where=where)
    if not isinstance(table.get(key), str):
        raise SchemaLoadError(f"{where}.{lang}.{key} 未声明")
    return fill(table[key], values, where=f"{where}.{key}", lang=lang)


def worktree_entries(code_repo: Path) -> list[dict[str, Any]]:
    """`git worktree list --porcelain` 解析(唯一实现): [{path, head, branch(短名; detached = ""), locked, prunable}]。失败 = ValueError(原文)。"""
    try:
        proc = subprocess.run(["git", "-C", str(code_repo), "worktree", "list", "--porcelain"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError(f"git worktree list 失败: {exc}") from exc
    if proc.returncode != 0:
        raise ValueError((proc.stderr or proc.stdout).strip() or f"git worktree list 退出码 {proc.returncode}")
    entries: list[dict[str, Any]] = []
    for line in proc.stdout.splitlines():
        if line.startswith("worktree "):
            entries.append({"path": Path(line[len("worktree "):].strip()), "head": "", "branch": "", "locked": False, "prunable": False})
        elif entries and line.startswith("HEAD "):
            entries[-1]["head"] = line[len("HEAD "):].strip()
        elif entries and line.startswith("branch "):
            ref = line[len("branch "):].strip()
            entries[-1]["branch"] = ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ref
        elif entries and (line == "locked" or line.startswith("locked ")):
            entries[-1]["locked"] = True
        elif entries and (line == "prunable" or line.startswith("prunable ")):
            entries[-1]["prunable"] = True
    return entries


def _same_path(a: Path | str, b: Path | str) -> bool:
    import os

    return os.path.realpath(str(a)) == os.path.realpath(str(b))


def _admin_dir(worktree: Path) -> Path | None:
    """链接工作树的管理目录(工作树根 .git 文件的 gitdir: 行); 读不出 = None。"""
    marker = Path(worktree) / ".git"
    try:
        text = marker.read_text(encoding="utf-8") if marker.is_file() else ""
    except OSError:
        return None
    for line in text.splitlines():
        if line.startswith("gitdir:"):
            path = Path(line[len("gitdir:"):].strip())
            return path if path.is_absolute() else (Path(worktree) / path)
    return None


def _git_out(code_repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(code_repo), *args], capture_output=True, text=True, timeout=30)


def claim_residue_base(card_frontmatter: dict[str, Any] | None) -> str | None:
    """认领残留的基线: 卡面 carried_from.tip(撤换承继)否则 N5.branch_integration.base_branch 声明。卡面 carried_from 形不合 = None
    (不判残留, 由认领建树原有的承继拒因暴露)。"""
    from tools.aipos_cli.next_resolver import card_base_branch, card_carried_from

    try:
        carried = card_carried_from(card_frontmatter)
    except ValueError:
        return None
    return carried["tip"] if carried else card_base_branch()


def gate_claimed_before(governance_root: Path, task_id: str) -> bool:
    """本卡是否有过门生认领记录(claims/<卡>/ 门生 claim 记录; 判定唯一 queue_mutation._gate_claim_records)。"""
    from tools.aipos_cli.queue_mutation import _gate_claim_records

    return bool(_gate_claim_records(Path(governance_root), task_id)[0])


def judge_claim_residue(governance_root: Path, task_id: str, *, code_repo: Path, worktree: Path, branch: str,
                        base_ref: str | None) -> dict[str, Any]:
    """AIPOS-F285 件③: 认领半成品唯一判定(只读)。门认领建树 next_resolver._ensure_worktree 与清理 judge_worktree 同读。

    返回 {kind: none|reuse|rebuild|refuse, reason: 文案键 | None, message, tree: absent|clean|incomplete|missing|dirty|foreign,
    branch_exists, commits, base, other, uncommitted, locked}。判据见声明 claim_residue.description; 读不出 = ValueError(调用方 fail-closed)。"""
    out: dict[str, Any] = {"kind": "none", "reason": None, "message": "", "tree": "absent", "branch_exists": False, "commits": None,
                           "base": base_ref, "other": None, "uncommitted": None, "locked": False}
    if base_ref is None or gate_claimed_before(governance_root, task_id):
        return out
    marker = str(claim_residue_declaration()["incomplete_marker"])
    values = {"task_id": task_id, "branch": branch, "base": base_ref, "worktree": str(worktree), "repo": str(code_repo)}

    def done(kind: str, reason: str | None, **extra: Any) -> dict[str, Any]:
        out.update(kind=kind, reason=reason, **{k: v for k, v in extra.items() if k in out})
        if reason:
            out["message"] = residue_text(reason, **{**values, **extra})
        return out

    verify = _git_out(code_repo, "rev-parse", "--verify", "-q", f"refs/heads/{branch}")
    out["branch_exists"] = verify.returncode == 0
    entries = worktree_entries(code_repo)
    at = next((e for e in entries if _same_path(e["path"], worktree)), None)
    if at is not None:
        out["locked"] = bool(at["locked"])
    present = Path(worktree).exists()
    if not out["branch_exists"] and not present and at is None:
        return out
    if out["branch_exists"]:
        counted = _git_out(code_repo, "rev-list", "--count", f"{base_ref}..refs/heads/{branch}")
        if counted.returncode != 0:
            raise ValueError(f"git rev-list {base_ref}..{branch} failed: {(counted.stderr or counted.stdout).strip()}")
        out["commits"] = int(counted.stdout.strip() or "0")
        if out["commits"] > 0:
            return done("refuse", "refuse_commits", commits=out["commits"])
        other = next((e for e in entries if e["branch"] == branch and not _same_path(e["path"], worktree)), None)
        if other is not None:
            return done("refuse", "refuse_occupied", other=str(other["path"]))
    if at is not None and not present:
        out["tree"] = "missing"
        return done("rebuild", "rebuild")
    if present:
        if at is None or at["branch"] != branch:
            out["tree"] = "foreign"
            current = (at["branch"] or "detached HEAD") if at is not None else "unregistered directory"
            return done("refuse", "refuse_foreign", current=current)
        admin = _admin_dir(Path(worktree))
        if admin is None or not (admin / marker).exists():
            out["tree"] = "incomplete"
            return done("rebuild", "rebuild")
        out["uncommitted"] = uncommitted_count(Path(worktree))
        if out["uncommitted"]:
            out["tree"] = "dirty"
            return done("refuse", "refuse_dirty", uncommitted=out["uncommitted"])
        out["tree"] = "clean"
        return done("reuse", "reuse_tree")
    return done("reuse", "reuse_branch")


def remove_claim_residue(code_repo: Path, worktree: Path, branch: str, *, base_ref: str, delete_branch: bool) -> str | None:
    """清掉认领残留: 带锁先 unlock → 在盘 = `git worktree remove --force`(只对判为残留的树) / 已不在 = `git worktree prune`;
    delete_branch = 删前复核卡分支仍无基线之外的提交再 `git branch -D`。成功 = None, 失败 = 原因原文。"""
    try:
        entries = worktree_entries(code_repo)
    except ValueError as exc:
        return str(exc)
    at = next((e for e in entries if _same_path(e["path"], worktree)), None)
    steps: list[list[str]] = []
    if at is not None and at["locked"]:
        steps.append(["worktree", "unlock", str(at["path"])])
    if at is not None:
        steps.append(["worktree", "remove", "--force", str(at["path"])] if Path(worktree).exists() else ["worktree", "prune"])
    for args in steps:
        proc = _git_out(code_repo, *args)
        if proc.returncode != 0:
            return f"git {' '.join(args[:2])}: {(proc.stderr or proc.stdout).strip()}"
    if delete_branch and _git_out(code_repo, "rev-parse", "--verify", "-q", f"refs/heads/{branch}").returncode == 0:
        counted = _git_out(code_repo, "rev-list", "--count", f"{base_ref}..refs/heads/{branch}")
        if counted.returncode != 0 or (counted.stdout.strip() or "0") != "0":
            return (f"branch {branch} not deleted: re-check found commits beyond {base_ref} or the comparison failed "
                    f"({(counted.stderr or counted.stdout).strip() or counted.stdout.strip()})")
        proc = _git_out(code_repo, "branch", "-D", branch)
        if proc.returncode != 0:
            return f"git branch -D: {(proc.stderr or proc.stdout).strip()}"
    return None


def _cleanup_residue(governance_root: Path, task_id: str, fm: dict[str, Any], *, state: str | None, worktree: Path,
                     code_repo: Path) -> dict[str, Any] | None:
    """清理判据里的认领残留判定: 只判队列状态 ∈ 声明 claim_residue.queue_states 的非审计卡(已认领 / 已结案的卡不判, 即使缺门生认领记录——
    人肉期卡与旧夹具形); 判为 reuse / rebuild = 残留(可清), 其余 = None(走原判据)。"""
    from tools.aipos_cli.audit_derivation import is_audit_card
    from tools.aipos_cli.next_resolver import card_branch_name

    if is_audit_card(task_id, fm) or state not in claim_residue_declaration()["queue_states"]:
        return None
    base = claim_residue_base(fm)
    residue = judge_claim_residue(governance_root, task_id, code_repo=Path(code_repo), worktree=Path(worktree),
                                  branch=card_branch_name(task_id), base_ref=base)
    return residue if residue["kind"] in ("reuse", "rebuild") else None


def _residue_view(residue: dict[str, Any]) -> dict[str, Any]:
    return {k: residue[k] for k in ("kind", "reason", "tree", "branch_exists", "commits", "base", "locked")}


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
    # AIPOS-F285 件③: 从未认领成功的卡(队列状态 ∈ claim_residue.queue_states)留下的认领半成品(0 提交卡分支 + 干净 / 未建完的树)= 可清;
    # 判在在途 / 脏之前, 承继引用之后(被承继原卡的树留到承继卡认领)
    def residue_item() -> dict[str, Any] | None:
        try:
            residue = _cleanup_residue(Path(governance_root), task_id, fm, state=state, worktree=Path(worktree), code_repo=Path(code_repo))
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            item.update(action="unreadable", error=str(exc))
            return item
        if residue is None:
            return None
        item.update(action="claim_residue", residue=_residue_view(residue))
        return item

    if not terminal:
        return residue_item() or {**item, "action": "in_flight"}
    if owner == task_id and task_id in referrers:
        item.update(action="carried", by=referrers[task_id])
        return item
    residue_hit = residue_item()
    if residue_hit is not None:
        return residue_hit
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
    if action == "claim_residue":
        return residue_text("kept_reason", task_id=item["task_id"])
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
    """`git worktree list --porcelain` 的工作树路径(含主检出; 调用方按工作树根过滤)。解析唯一 worktree_entries; 失败 = ValueError(原文)。"""
    return [Path(e["path"]) for e in worktree_entries(code_repo)]


def _apply_confirm(item: dict[str, Any], repo_path: Path, result: dict[str, Any]) -> None:
    """--confirm 执行: remove = git worktree remove(分支保留); claim_residue = 删残留树 + 删 0 提交卡分支(AIPOS-F285)。"""
    if item["action"] == "remove":
        error = remove_worktree(repo_path, Path(item["worktree"]))
        item.update({"action": "failed", "error": error} if error else {"action": "removed"})
    elif item["action"] == "claim_residue":
        from tools.aipos_cli.next_resolver import card_branch_name

        error = remove_claim_residue(repo_path, Path(item["worktree"]), card_branch_name(item["task_id"]),
                                     base_ref=str(item["residue"]["base"]), delete_branch=True)
        item.update({"action": "failed", "error": error} if error else {"action": "residue_removed"})
    else:
        return
    if item["action"] == "failed":
        result["ok"] = False


def _task_items(gov: Path, task_id: str, *, referrers: dict[str, str], decl: dict[str, Any]) -> list[dict[str, Any]]:
    """--task-id: 只看这一张卡(及其审计轮)的落点树(card_worktree_location, 与认领建树同一推导); 本卡落点不在盘而仅有 0 提交卡分支
    且从未认领成功 = 分支型认领残留(claim_residue, worktree_present=False)。"""
    from tools.aipos_cli.next_resolver import card_branch_name

    items: list[dict[str, Any]] = []
    path, _state, _fm = _read_card(gov, task_id)
    if path is None:
        raise ValueError(f"卡 {task_id} 不在队列(--task-id 须为本项目队列中的卡)")
    for target in _card_tree_targets(gov, task_id):
        if target.get("error"):
            items.append({"task_id": target["task_id"], "worktree": "(落点不可推导)", "code_repo": "", "state": target.get("state"),
                          "branch": "", "action": "unreadable", "uncommitted": None, "error": target["error"], "by": None})
            continue
        item = judge_worktree(gov, task_id=target["task_id"], worktree=target["worktree"], code_repo=target["code_repo"],
                              state=target["state"], card_frontmatter=target["fm"], referrers=referrers, decl=decl)
        item["worktree_present"] = item["action"] != "absent"
        if item["action"] == "absent" and target["task_id"] == task_id:
            try:
                residue = _cleanup_residue(gov, task_id, target["fm"], state=target["state"], worktree=Path(target["worktree"]),
                                           code_repo=Path(target["code_repo"]))
            except (ValueError, OSError, subprocess.SubprocessError) as exc:
                item.update(action="unreadable", error=str(exc))
                residue = None
            if residue is not None:
                item.update(action="claim_residue", residue=_residue_view(residue), branch=card_branch_name(task_id))
        if item["action"] != "absent":
            items.append(item)
    return items


def cleanup_project_worktrees(governance_root: Path, *, confirm: bool, task_id: str | None = None) -> dict[str, Any]:
    """件④: 本项目全部产品仓(project.json repos.items, 无 repos 段 = code_repo)工作树根下的卡工作树, 按 judge_worktree 同一判据
    预演 / 清理。目录名 = 卡 ID(认领建树 <工作树根>/<卡ID>); 查不到卡 = unknown(留)。主检出与工作树根外的树不列。
    AIPOS-F285: 认领残留(claim_residue)--confirm = 删残留树 + 删 0 提交卡分支; task_id = 只看 / 只清这一张卡(_task_items)。
    返回 {project, governance_root, confirm, task_id, repos: [{repo, path, root, items, error}], counts: {动作: 数}, ok}。"""
    from tools.aipos_cli.next_resolver import _resolve_worktree_root
    from tools.aipos_cli.workspace_config import project_repos, read_project_json

    gov = Path(governance_root).resolve()
    decl = cleanup_declaration()
    referrers = carry_referrers(gov, decl)
    result: dict[str, Any] = {"project": str(read_project_json(gov).get("project") or gov.name), "governance_root": str(gov),
                              "confirm": bool(confirm), "task_id": task_id or None, "repos": [], "counts": {}, "ok": True}
    if task_id:
        entry: dict[str, Any] = {"repo": task_id, "path": "", "root": "", "items": [], "error": None}
        result["repos"].append(entry)
        for item in _task_items(gov, task_id, referrers=referrers, decl=decl):
            if confirm:
                _apply_confirm(item, Path(item["code_repo"]), result)
            entry["items"].append(item)
            result["counts"][item["action"]] = result["counts"].get(item["action"], 0) + 1
        return result
    repos = project_repos(gov)
    if repos["declared"]:
        pairs = sorted((str(name), Path(path)) for name, path in repos["items"].items())
    else:
        pairs = [(Path(repos["code_repo"]).name, Path(repos["code_repo"]))] if repos["code_repo"] else []
    for name, repo_path in pairs:
        entry = {"repo": name, "path": str(repo_path), "root": "", "items": [], "error": None}
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
            tree_task = worktree.name
            try:
                path, state, fm = _read_card(gov, tree_task)
            except ValueError as exc:
                item = {"task_id": tree_task, "worktree": str(worktree), "code_repo": str(repo_path), "state": "(卡面读不出)",
                        "branch": _checked_out_branch(worktree), "action": "unreadable", "uncommitted": None, "error": str(exc), "by": None}
                entry["items"].append(item)
                result["counts"]["unreadable"] = result["counts"].get("unreadable", 0) + 1
                continue
            if path is None:
                item = {"task_id": tree_task, "worktree": str(worktree), "code_repo": str(repo_path), "state": "(不在队列)",
                        "branch": _checked_out_branch(worktree), "action": "unknown", "uncommitted": None, "error": None, "by": None}
            else:
                item = judge_worktree(gov, task_id=str(fm.get("task_id") or tree_task), worktree=worktree, code_repo=repo_path,
                                      state=state, card_frontmatter=fm, referrers=referrers, decl=decl)
            item.setdefault("worktree_present", True)
            if confirm:
                _apply_confirm(item, repo_path, result)
            entry["items"].append(item)
            result["counts"][item["action"]] = result["counts"].get(item["action"], 0) + 1
    return result


def _residue_line(item: dict[str, Any]) -> str:
    """认领残留 / 已清残留一行(双语文案)。"""
    if item["action"] == "residue_removed":
        return residue_text("cleanup_removed", worktree=item["worktree"], task_id=item["task_id"], branch=item["branch"])
    if not item.get("worktree_present", True):
        return residue_text("cleanup_branch_item", branch=item["branch"], task_id=item["task_id"], state=item["state"])
    kind = {"incomplete": "kind_incomplete", "missing": "kind_missing"}.get((item.get("residue") or {}).get("tree"), "kind_clean")
    return residue_text("cleanup_item", worktree=item["worktree"], task_id=item["task_id"], state=item["state"], branch=item["branch"],
                        kind=residue_text(kind))


def render_project_cleanup(result: dict[str, Any]) -> list[str]:
    """批量清理人读输出(文案 verbs.schema lybra_project_cleanup_worktrees.texts; 认领残留行 claim_residue_texts 双语)。"""
    texts = cleanup_texts()
    lines = [_fill(texts["header_confirm" if result["confirm"] else "header_preview"], project=result["project"])]
    for entry in result["repos"]:
        if entry["error"]:
            lines.append(_fill(texts["repo_unreadable"], repo=entry["repo"], error=entry["error"]))
            continue
        if not result.get("task_id"):
            lines.append(_fill(texts["repo_line"], repo=entry["repo"], root=entry["root"], count=len(entry["items"])))
        for item in entry["items"]:
            if item["action"] in ("claim_residue", "residue_removed"):
                lines.append(_residue_line(item))
                continue
            lines.append(_fill(texts[item["action"]], worktree=item["worktree"], task_id=item["task_id"], state=item["state"],
                               branch=item["branch"], uncommitted=item["uncommitted"], error=item["error"], by=item["by"]))
    counts = result["counts"]
    lines.append(_fill(texts["summary"], **{k: counts.get(k, 0) + (counts.get("removed", 0) if k == "remove" else 0)
                                            for k in ("remove", "dirty", "in_flight", "carried", "unknown", "unreadable", "failed")}))
    if counts.get("claim_residue") or counts.get("residue_removed"):
        lines.append(residue_text("summary", residue=counts.get("claim_residue", 0) + counts.get("residue_removed", 0),
                                  removed=counts.get("residue_removed", 0)))
    if not result["confirm"] and (counts.get("claim_residue") or (counts.get("remove") and result.get("task_id"))):
        flag = f" --task-id {result['task_id']}" if result.get("task_id") else ""
        lines.append(residue_text("confirm_hint", governance_root=result["governance_root"], task_flag=flag))
    elif not result["confirm"] and counts.get("remove"):
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
        result = cleanup_project_worktrees(gov, confirm=bool(getattr(args, "confirm", False)),
                                           task_id=str(getattr(args, "task_id", None) or "").strip() or None)
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
