"""AIPOS-R7A2 靶②: lybra governance-commit — 顾问收口一条命令

N6 收账清单校验四件齐全:
1. 本卡台账条目 (task_cards/<ID>/)
2. decision_log 指针 (如适用)
3. 阶段快照 (如阶段收口)
4. task_cards 归档

校验通过 → commit(过治理仓 pre-commit 四检) → push
缺件明确报哪一件并拒绝;失败不静默。

命令须可由顾问工位 advisor token 使用(无 finalize 权限)。

AIPOS-F79 精确批次提交(chris 总顾问实撞):
- 件①: ``--paths``/``--paths-file`` 显式白名单 → 只 ``git add -- <paths>``, 绝不 add -A;
  路径越出治理根或指向他项目 = 拒(fail-closed)。
- 件②: ``--dry-run`` 输出将提交文件清单(modified/added/deleted/untracked_selected),
  只读 ``git status --porcelain`` + ``git diff --name-status``, 不 add/不 reset/不 stash。
- 件③: 正式提交后 ``git show --name-only HEAD`` 与清单逐条比对, 不同即 FAIL 出声;
  预暂存文件在 --paths 内 = 纳入清单, 在 --paths 外 = 拒并列出。
- 无 --paths 的整根 ``git add -A -- .`` 仅保留给 lybra 自身工作区; 他项目一律 --paths。

AIPOS-F79C push 假阴性热修(chris 总顾问阻塞, 2026-09-21 --json 取证):
- 件①: 旧判据 ``merge-base(local, remote) != local`` 在本地刚提交后恒真 → 永远 rebase → F69-R2 越界脏树
  检查把共享仓里他项目的未提交修改也算进 → pushed=False。改为 fetch 后双向 ``rev-list --count``:
  远端独有=0 → 直接 fast-forward push, 不 rebase、不做脏树检查。
- 件②: 远端独有>0 → 在仓外临时 linked worktree(``git worktree add --detach``)上 cherry-pick 本地独有
  提交并在那里 push; 共享检出/索引/他项目未提交文件全程不动; 冲突 → 清理临时 worktree、精确列冲突文件、
  BLOCK, 本地 commit 保留。成功后本地分支用 ``git reset --keep origin/<branch>`` 跟到远端(只改远端提交
  改过的文件, 有本地改动即整体中止只报告)。
- 件③: pushed=False 一律 verdict != PASS(CLI 非零退出), operations 末条 = ``PUSH NOT DONE: <原因>``;
  F79 的 manifest 核对保留。
- 件④: 目录 pathspec 内的删除用 ``git add -A -- <paths>`` 暂存(范围仍限 --paths); 暂存集/预暂存集一律
  ``--no-renames`` 与清单同口径(病根: ``diff --cached --name-only`` 把「删旧路径+加新路径」的队列搬动折叠成
  一条重命名, 删除项就「missing」); mismatch 时 ``git reset -- <paths>`` 清掉本次暂存再拒, 不留半成品。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from tools.schema_constants import Verdict


# ---------------------------------------------------------------------------
# AIPOS-F79: 显式路径白名单 + 只读清单
# ---------------------------------------------------------------------------

# 清单分类(件②): 与 git status/diff 状态码的映射
MANIFEST_MODIFIED = "modified"
MANIFEST_ADDED = "added"
MANIFEST_DELETED = "deleted"
MANIFEST_UNTRACKED = "untracked_selected"

_GIT_QUOTEPATH_OFF = ["git", "-c", "core.quotepath=false"]


def _git_readonly(args: list[str], cwd: Path, *, timeout: int = 30) -> str:
    """只读 git 调用(status/diff/rev-parse/show); 失败直接抛 CalledProcessError, 禁静默。"""
    result = subprocess.run(
        _GIT_QUOTEPATH_OFF + args,
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return result.stdout


def _split_nul(raw: str) -> list[str]:
    return [t for t in raw.split("\0") if t != ""]


def _porcelain_entries(raw: str) -> list[tuple[str, str]]:
    """``git status --porcelain -z`` → [(XY, 仓根相对路径)]; 重命名 / 复制条目的源路径 token 跳过。"""
    tokens = _split_nul(raw)
    out: list[tuple[str, str]] = []
    i = 0
    while i < len(tokens):
        entry = tokens[i]
        i += 1
        if entry[:1] in ("R", "C"):
            i += 1
        out.append((entry[:2], entry[3:]))
    return out


def _strip_ws_prefix(prefix: str, repo_path: str) -> str:
    """仓根相对 → 治理根相对(prefix = _git_ws_prefix; 治理根即仓根时原样)。"""
    return repo_path[len(prefix):] if prefix and repo_path.startswith(prefix) else repo_path


def resolve_commit_paths(
    governance_root: Path,
    paths: list[str] | None,
    paths_file: Path | str | None,
) -> dict[str, Any]:
    """AIPOS-F79 件①: 把 --paths / --paths-file 归一为治理根相对路径白名单。

    二者同一实现: --paths-file 每行一路径(空行与 # 行忽略), 与 --paths 二选一。
    fail-closed 规则(任一命中即整体拒绝, 不做部分接受):
      - 两种给法同时出现;
      - --paths-file 不存在/不可读;
      - 路径为空 / 指向治理根本身(等价整根 add -A);
      - 路径(含 ``..``/绝对路径/符号链接解析后)落在治理根之外 —— 含指向他项目。

    Returns:
        {"selected": bool, "paths": [rel...], "source": "paths"|"paths_file"|None,
         "rejected": [{"path": str, "reason": str}], "message": str|None}
    """
    if not paths and paths_file is None:
        return {"selected": False, "paths": [], "source": None, "rejected": [], "message": None}

    if paths and paths_file is not None:
        return {
            "selected": True,
            "paths": [],
            "source": None,
            "rejected": [{"path": str(paths_file), "reason": "--paths 与 --paths-file 二选一, 不可同时给"}],
            "message": "--paths 与 --paths-file 二选一, 不可同时给(fail-closed)",
        }

    raw_entries: list[str]
    source: str
    if paths_file is not None:
        source = "paths_file"
        pf = Path(paths_file).expanduser()
        try:
            content = pf.read_text(encoding="utf-8")
        except (FileNotFoundError, PermissionError, IsADirectoryError, UnicodeDecodeError) as exc:
            return {
                "selected": True,
                "paths": [],
                "source": source,
                "rejected": [{"path": str(pf), "reason": f"--paths-file 不可读: {exc.__class__.__name__}: {exc}"}],
                "message": f"--paths-file 不可读: {pf} ({exc.__class__.__name__})",
            }
        raw_entries = []
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            raw_entries.append(stripped)
        if not raw_entries:
            return {
                "selected": True,
                "paths": [],
                "source": source,
                "rejected": [{"path": str(pf), "reason": "--paths-file 为空(无有效路径行)"}],
                "message": f"--paths-file 为空, 无可提交路径: {pf}",
            }
    else:
        source = "paths"
        raw_entries = [str(p) for p in (paths or [])]

    root_resolved = governance_root.resolve()
    accepted: list[str] = []
    rejected: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in raw_entries:
        candidate = raw.strip()
        if not candidate:
            rejected.append({"path": raw, "reason": "空路径"})
            continue
        abs_candidate = Path(candidate) if os.path.isabs(candidate) else governance_root / candidate
        # resolve(): 消掉 ../ 并解析符号链接; 不存在的路径(如已删除文件)也可解析(strict=False)
        resolved = abs_candidate.resolve()
        if resolved == root_resolved:
            rejected.append({"path": raw, "reason": "指向治理根本身 = 整根提交; 请列具体文件/目录, 或去掉 --paths(仅 lybra 自身工作区允许)"})
            continue
        if not resolved.is_relative_to(root_resolved):
            rejected.append({"path": raw, "reason": f"越出治理根 {root_resolved}(他项目或仓外路径, 拒)"})
            continue
        rel = resolved.relative_to(root_resolved).as_posix()
        if rel in seen:
            continue
        seen.add(rel)
        accepted.append(rel)

    if rejected:
        listing = "\n  - ".join(f"{r['path']}: {r['reason']}" for r in rejected)
        return {
            "selected": True,
            "paths": [],
            "source": source,
            "rejected": rejected,
            "message": (
                f"--paths 白名单含非法路径, 整体拒绝(fail-closed, 不做部分提交):\n  - {listing}\n\n"
                f"可执行出口:\n"
                f"1. 只列治理根 {root_resolved} 内的相对路径(文件或目录)\n"
                f"2. 他项目内容须在其自己的治理根下用各自的 lybra governance-commit --paths 提交"
            ),
        }

    return {"selected": True, "paths": accepted, "source": source, "rejected": [], "message": None}


VANISHED_UNTRACKED_LINE = "已跳过 {count} 个从未入库且已不存在的路径(无可落内容, 不进暂存与提交): {paths}"


def classify_missing_paths(governance_root: Path, selected_paths: list[str]) -> dict[str, list[str]]:
    """AIPOS-F193: 白名单中工作树已不存在的条目分两类(只读 git ls-files, 治理根相对):
    - vanished_untracked: 索引与 HEAD 均无(从未入库, 如人肉期接入项目发布 / 认领后未提交、随后被队列写命令挪走的原卡路径)
      = 无可落内容, 从暂存与提交 pathspec 剔除;
    - staged_deleted: HEAD 有、索引无(已 git rm 的删除)= 删除已在索引, 只不再 git add(同样会报 pathspec 不匹配), 照常作删除提交。
    已跟踪且在工作树被删除(索引仍有)的条目不在此列, 由 git add -A 暂存删除。git 读失败抛 CalledProcessError(调用方 fail-closed)。"""
    missing = [p for p in selected_paths if not os.path.lexists(Path(governance_root) / p)]
    if not missing:
        return {"vanished_untracked": [], "staged_deleted": []}
    literal = [f":(literal){p}" for p in missing]
    in_index = _split_nul(_git_readonly(["ls-files", "-z", "--"] + literal, governance_root))
    has_head = subprocess.run(["git", "rev-parse", "--verify", "-q", "HEAD^{commit}"], cwd=str(governance_root),
                              capture_output=True, text=True).returncode == 0
    in_index_or_head = (_split_nul(_git_readonly(["ls-files", "-z", "--with-tree=HEAD", "--"] + literal, governance_root))
                        if has_head else in_index)

    def matched(path: str, entries: list[str]) -> bool:
        prefix = path.rstrip("/") + "/"
        return any(e == path or e.startswith(prefix) for e in entries)

    return {"vanished_untracked": [p for p in missing if not matched(p, in_index_or_head)],
            "staged_deleted": [p for p in missing if matched(p, in_index_or_head) and not matched(p, in_index)]}


def _git_ws_prefix(governance_root: Path) -> str:
    """治理根在 git 仓内的相对前缀(如 ``2_projects/chris-huibojin/``; 仓根即治理根时为 ``""``)。"""
    return _git_readonly(["rev-parse", "--show-prefix"], governance_root).strip()


def collect_commit_manifest(governance_root: Path, selected_paths: list[str] | None) -> dict[str, Any]:
    """AIPOS-F79 件②: 只读取得「将提交的具体文件」清单。

    只用 ``git status --porcelain -uall -z -- <pathspec>``(取未跟踪文件)与
    ``git diff --name-status --no-renames -z HEAD -- <pathspec>``(取已跟踪的 M/A/D, 即
    ``git add <pathspec>`` + commit 后相对 HEAD 的净差异)。不 add/不 reset/不 stash。

    selected_paths=None → pathspec ``.``(整根, 即既有 ``git add -A -- .`` 会吞入的范围),
    并对未跟踪文件数出 warning。

    Returns:
        {"mode": "paths"|"whole_root", "pathspec": [...], "ws_prefix": str,
         "files": [{"path": 治理根相对, "repo_path": 仓根相对, "status": 分类}],
         "counts": {分类: n, "total": n}, "warnings": [str]}
    """
    ws_prefix = _git_ws_prefix(governance_root)
    mode = "paths" if selected_paths else "whole_root"
    pathspec = list(selected_paths) if selected_paths else ["."]

    def to_gov_rel(repo_path: str) -> str:
        if ws_prefix and repo_path.startswith(ws_prefix):
            return repo_path[len(ws_prefix):]
        return repo_path

    files: dict[str, dict[str, str]] = {}

    # ① 已跟踪文件: HEAD vs 工作树(含已暂存), 即提交后的净差异
    diff_raw = _git_readonly(
        ["diff", "--name-status", "--no-renames", "-z", "HEAD", "--"] + pathspec, governance_root
    )
    tokens = _split_nul(diff_raw)
    i = 0
    while i + 1 < len(tokens):
        code, repo_path = tokens[i], tokens[i + 1]
        i += 2
        letter = code[:1]
        if letter == "D":
            status = MANIFEST_DELETED
        elif letter == "A":
            status = MANIFEST_ADDED
        else:  # M / T(类型变更) 均视为 modified
            status = MANIFEST_MODIFIED
        files[repo_path] = {"path": to_gov_rel(repo_path), "repo_path": repo_path, "status": status}

    # ② 未跟踪文件: 只取 ?? 行(-uall 展开目录到文件级, 与 git add 后的粒度一致)
    status_raw = _git_readonly(
        ["status", "--porcelain", "--untracked-files=all", "-z", "--"] + pathspec, governance_root
    )
    tokens = _split_nul(status_raw)
    i = 0
    while i < len(tokens):
        entry = tokens[i]
        i += 1
        xy, repo_path = entry[:2], entry[3:]
        if xy[0] in ("R", "C"):
            i += 1  # 重命名/复制条目带一个源路径 token, 跳过
        if xy == "??" and repo_path not in files:
            files[repo_path] = {"path": to_gov_rel(repo_path), "repo_path": repo_path, "status": MANIFEST_UNTRACKED}

    ordered = sorted(files.values(), key=lambda f: f["repo_path"])
    counts = {MANIFEST_MODIFIED: 0, MANIFEST_ADDED: 0, MANIFEST_DELETED: 0, MANIFEST_UNTRACKED: 0}
    for f in ordered:
        counts[f["status"]] += 1
    counts["total"] = len(ordered)

    warnings_out: list[str] = []
    if mode == "whole_root":
        warnings_out.append(
            f"无 --paths: 将对治理根整根 git add -A -- . (仅限 lybra 自身工作区; 他项目一律 --paths)"
        )
        if counts[MANIFEST_UNTRACKED]:
            warnings_out.append(
                f"WARNING: 整根提交会吞入 {counts[MANIFEST_UNTRACKED]} 个未跟踪文件(历史材料会被夹带); "
                f"请改用 --paths 精确选定"
            )
    return {
        "mode": mode,
        "pathspec": pathspec,
        "ws_prefix": ws_prefix,
        "files": ordered,
        "counts": counts,
        "warnings": warnings_out,
    }


def _manifest_repo_paths(manifest: dict[str, Any]) -> list[str]:
    return sorted(f["repo_path"] for f in manifest["files"])


# ---------------------------------------------------------------------------
# AIPOS-F94 件①③: 卡范围落账——task 范围路径推导 + 落账判据(唯一实现)
#
# 病根(第一次接入验收第五节): 新项目三张卡全部结案, 治理根却整体未跟踪——loop 结案即停, 无治理落账步。
# 本节 = 「一张卡的治理真相在哪些路径」与「这些路径是否已在治理仓提交且推送」的唯一推导;
# governance-commit --task-id(无 --paths)、推导核 N6 落账步(next_resolver)、state lint GOVERNANCE_UNCOMMITTED 三方同读。
# 路径全部由卡与声明推导(队列根 / 记录根 / 草稿目录 = 声明; 台账 / 交回 / 裁决落点 = project.json paths; 卡编年史 =
# project.json paths.foundation_backlog), 禁整根; 新记录类型(records/<新类型>/<卡ID>/)自动纳入。
# ---------------------------------------------------------------------------

TASK_SCOPE_SOURCE = "task_scope"
N6_LANDING_ACTOR_PLACEHOLDER = "<驱动方实例>"


def n6_landing_declaration() -> dict[str, Any]:
    """N6 落账步的唯一声明 transitions.schema nodes.N6.landing(动作类型 / 门外动词名 / 命令模板 / 非 git 出口)。
    缺声明或缺键 = SchemaLoadError(fail-closed, 禁回落写死)。"""
    from tools.aipos_cli.next_resolver import _transition_node
    from tools.schema_loader import SchemaLoadError

    decl = _transition_node("N6").get("landing")
    if not isinstance(decl, dict):
        raise SchemaLoadError("transitions.schema.json nodes.N6.landing 未声明")
    for key in ("action_type", "verb", "command_template"):
        if not str(decl.get(key) or "").strip():
            raise SchemaLoadError(f"transitions.schema.json nodes.N6.landing.{key} 未声明")
    if not str((decl.get("non_git_exit") or {}).get("command_template") or "").strip():
        raise SchemaLoadError("transitions.schema.json nodes.N6.landing.non_git_exit.command_template 未声明")
    return decl


def governance_commit_cli() -> str:
    """治理提交命令名 = verbs.schema lybra_governance_commit.cli_command(唯一声明)。"""
    from tools.aipos_cli.next_resolver import REPO_ROOT
    from tools.schema_loader import SchemaLoadError, load_schema

    cli = str(((load_schema("verbs", REPO_ROOT).get("verbs") or {}).get("lybra_governance_commit") or {}).get("cli_command") or "").strip()
    if not cli:
        raise SchemaLoadError("verbs.schema.json verbs.lybra_governance_commit.cli_command 未声明")
    return cli


def _render_template(template: str, **values: Any) -> str:
    import shlex

    return template.format(**{k: shlex.quote(str(v)) for k, v in values.items()})


def audit_card_ids(governance_root: Path, task_id: str) -> list[str]:
    """本卡的全部审计轮卡号(AIPOS-F112: 首轮 <ID>R 及复审轮 R2/R3…, audit_derivation.audit_round_ids 唯一实现;
    一张都没有 = [首轮号])。原 audit_card_id 写死 <ID>R, 复审轮的队列文件/记录/报告不入落账范围。"""
    from tools.aipos_cli.audit_derivation import audit_round_ids, current_audit_task_id

    return audit_round_ids(task_id, Path(governance_root)) or [current_audit_task_id(task_id, Path(governance_root))]


def governance_commit_command(task_id: str, actor: str, governance_root: Path | str) -> str:
    """N6 落账命令(推导核 N6 落账步 / close next_step / state lint 出口同此): 渲染 nodes.N6.landing.command_template。"""
    return _render_template(n6_landing_declaration()["command_template"], task_id=task_id, actor=actor,
                            governance_root=governance_root)


def non_git_exit_command(governance_root: Path | str) -> str:
    """治理根不在 git 工作树内时的出口(nodes.N6.landing.non_git_exit; home 根 = 治理根所在目录, 单门 home 根约定)。"""
    return _render_template(n6_landing_declaration()["non_git_exit"]["command_template"],
                            home_root=Path(governance_root).expanduser().parent)


def governance_commit_paths_command(paths: list[str], actor: str, governance_root: Path | str) -> str:
    """非卡改动(声明 / 治理文档 / 信封与决策记录 / 接入向导各步产物)精确提交命令的唯一渲染: --paths 逐条(治理根相对)。"""
    import shlex

    if not paths:
        raise ValueError("governance_commit_paths_command: paths 为空(禁整根提交)")
    return (f"{governance_commit_cli()} --governance-root {shlex.quote(str(governance_root))} --actor {shlex.quote(str(actor))} "
            + " ".join(f"--paths {shlex.quote(str(p))}" for p in paths))


def _gov_rel(governance_root: Path, path: Path) -> str | None:
    """治理根内的相对 posix 路径; 声明落在治理根外(chris 形绝对路径)= None(不属本治理根, 不入本根提交)。"""
    try:
        return Path(path).resolve().relative_to(Path(governance_root).resolve()).as_posix()
    except ValueError:
        return None


def card_own_paths(governance_root: Path, task_id: str, *, card_path: Path | None = None) -> list[str]:
    """一张卡自身的治理路径候选(治理根相对; 不判存在, 不判跟踪):

    - 队列文件: <queue_root>/<每个队列状态>/<卡文件名>(卡在队列间搬动, 旧位的删除也属本卡);
    - 草稿: <drafts>/<卡文件名>;
    - 记录: <records>/<每个记录类型>/<卡ID | 卡文件名 stem>/(门按 task_id 或 draft_id 分目录; 新类型自动纳入;
      loop 运行记录目录 paths.loop_runs_root 不按目录整入, 见下);
    - AIPOS-F147 件①: 本卡的部署记录(平铺无卡目录; 归属 = authorization_ref 是本卡裁决, card_deployment_records);
    - AIPOS-F147 件①: 本卡已结束的 loop 运行记录 .md(<loop_runs_root>/<卡ID>/looprun_*.md 中 status≠running 者, loop_run_files);
      运行中的那份是运行期观测(loop 每轮都写), 不入判据, 只随提交带走(card_trailing_paths); .log 一律不入库;
    - 台账 / 交回 / 裁决落点: project.json paths.{task_cards_root, return_root, verdict_root}/<卡ID>/。
    卡文件名 = 队列中按 frontmatter task_id 查到的那份(task_loader.find_task_card 唯一查找), 另含 <卡ID 小写>.md 缺省名。
    """
    from tools.aipos_cli.next_resolver import _resolve_governance_path_with_relative
    from tools.aipos_cli.task_loader import QUEUE_STATES, find_task_card, queue_root_for
    from tools.aipos_cli.workspace_config import project_paths

    root = Path(governance_root)
    if card_path is None:
        card_path, _state = find_task_card(root, task_id)
    names = [f"{task_id.lower()}.md"]
    if card_path is not None and card_path.name not in names:
        names.insert(0, card_path.name)
    record_keys = list(dict.fromkeys([task_id, task_id.lower()] + [Path(n).stem for n in names]))

    out: list[str] = []

    def add(path: Path) -> None:
        rel = _gov_rel(root, path)
        if rel and rel not in out:
            out.append(rel)

    queue_root = queue_root_for(root)
    for state in QUEUE_STATES:
        for name in names:
            add(queue_root / state / name)
    drafts = _resolve_governance_path_with_relative("drafts", root)
    for name in names:
        add(drafts / name)
    records = _resolve_governance_path_with_relative("records", root)
    declared = project_paths(root)
    # AIPOS-F131: loop 运行记录目录不按目录整入(loop 每轮都写本次运行的 .md, 整目录入判据则落账判据永不过);
    # AIPOS-F147 件①: 改为逐文件——已结束的运行 .md 入判据(loop_run_files), 运行中的只随提交带走(card_trailing_paths)
    loop_runs = Path(declared["loop_runs_root"]).resolve()
    if records.is_dir():
        for kind_dir in sorted(p for p in records.iterdir() if p.is_dir()):
            if kind_dir.resolve() == loop_runs:
                continue
            for key in record_keys:
                add(kind_dir / key)
    for path in card_deployment_records(root, task_id):
        add(path)
    for path in loop_run_files(root, task_id)["ended"]:
        add(path)
    for key in ("task_cards_root", "return_root", "verdict_root"):
        add(Path(declared[key]) / task_id)
    return out


# ---------------------------------------------------------------------------
# AIPOS-F147 件①(gap #104): 卡外 / 异步门记录归属——部署记录(平铺)、loop 运行记录(运行中 / 已结束)
# ---------------------------------------------------------------------------

#: 部署记录归属索引缓存: {部署记录目录: (目录项签名, {authorization_ref: [记录路径]}, [读不出的记录])}
_DEPLOYMENT_INDEX: dict[str, tuple[tuple[tuple[str, int], ...], dict[str, list[Path]], list[tuple[Path, str]]]] = {}


def deployment_attribution(governance_root: Path) -> dict[str, Any]:
    """部署记录(record_locations.kinds.deployments, 平铺; 存量旧名含 <commit8>/ 子目录)按 authorization_ref 建索引。

    返回 {by_ref: {裁决 id: [记录路径]}, unreadable: [(记录路径, 拒因)](读不出 frontmatter 的记录, 存量旧格式, 无从归属)}。读经唯一入口
    frontmatter.require_frontmatter; 读不出的记录不猜归属, 原样列出由调用方出声(N6 输出 / --json task_scope.unattributed_deployments)。
    缓存键 = 部署目录直接项的 (名, mtime_ns)(记录只追加; 新增必改目录项)。"""
    from tools.aipos_cli.frontmatter import FrontmatterReadError, require_frontmatter
    from tools.aipos_cli.record_writer import record_dir

    directory = record_dir(Path(governance_root), "deployments")
    if not directory.is_dir():
        return {"by_ref": {}, "unreadable": []}
    signature = tuple(sorted((p.name, p.stat().st_mtime_ns) for p in directory.iterdir()))
    cache_key = str(directory.resolve())
    hit = _DEPLOYMENT_INDEX.get(cache_key)
    if hit is None or hit[0] != signature:
        by_ref: dict[str, list[Path]] = {}
        unreadable: list[tuple[Path, str]] = []
        for record in sorted(p for p in directory.rglob("deployment_*.md") if p.is_file()):
            try:
                meta, _body = require_frontmatter(record)
            except FrontmatterReadError as exc:
                unreadable.append((record, str(exc)))
                continue
            ref = str(meta.get("authorization_ref") or "").strip()
            if ref and str(meta.get("authorization_type") or "") == "verdict_ref":
                by_ref.setdefault(ref, []).append(record)
        hit = (signature, by_ref, unreadable)
        _DEPLOYMENT_INDEX[cache_key] = hit
    return {"by_ref": hit[1], "unreadable": list(hit[2])}


def card_deployment_records(governance_root: Path, task_id: str) -> list[Path]:
    """本卡的部署记录 = authorization_ref ∈ 本卡裁决 id(<records>/audit_verdicts/<卡ID>/ 下门生裁决记录文件名; 门生判据
    record_writer.gate_record_files)。dev_override 部署无裁决, 不属任何卡。声明 transitions nodes.N5.deployment_record.card_attribution。"""
    from tools.aipos_cli.record_writer import gate_record_files, record_dir

    verdict_ids = {p.stem for p in gate_record_files(record_dir(Path(governance_root), "audit_verdicts", task_id), "audit_verdicts")}
    if not verdict_ids:
        return []
    by_ref = deployment_attribution(governance_root)["by_ref"]
    return sorted({path for vid in verdict_ids for path in by_ref.get(vid, [])})


def loop_run_files(governance_root: Path, task_id: str) -> dict[str, list[Path]]:
    """本卡 loop 运行记录 .md(<loop_runs_root>/<卡ID>/looprun_*.md)按记录 status 分 {ended, running}(.log 不在内, 日志不入库)。
    status 读经唯一读法 loop_run_record.read_run(record_type 须为声明值; 读不出 = LoopRunRecordError, fail-closed 原样抛)。
    running = loop 进程仍在写(或已被杀未写结束)——运行期观测, 只随提交带走、不入落账判据; 其余(ended)入判据。"""
    from tools.aipos_cli.loop_driver import load_loop_contract
    from tools.aipos_cli.loop_run_record import _run_files, loop_runs_dir, read_run, run_record_declaration

    directory = loop_runs_dir(Path(governance_root), task_id)
    files = _run_files(directory)
    out: dict[str, list[Path]] = {"ended": [], "running": []}
    if not files:
        return out
    from tools.aipos_cli.loop_run_record import LoopRunRecordError

    decl = run_record_declaration(load_loop_contract())
    for path in files:
        try:
            status = str(read_run(path, decl).get("status") or "")
        except LoopRunRecordError as exc:
            # 判据调用方(N6 推导 / loop 出口 / 落账提交)按 ValueError 走「落账判据不可读」fail-closed 出口
            raise ValueError(f"loop 运行记录读不出, 落账范围不可推导: {exc}") from exc
        out["running" if status == "running" else "ended"].append(path)
    return out


def card_trailing_paths(governance_root: Path, task_id: str) -> list[str]:
    """随本卡落账提交带走、但不入落账判据的路径(治理根相对): 本卡运行中的 loop 运行记录 .md(AIPOS-F147 件①)。"""
    root = Path(governance_root)
    return [rel for rel in (_gov_rel(root, p) for p in loop_run_files(root, task_id)["running"]) if rel]


def task_scope_candidates(governance_root: Path, task_id: str) -> dict[str, Any]:
    """一张卡的落账范围候选 = 本卡自身路径 + 其审计卡自身路径 + 卡编年史(项目声明了 paths.foundation_backlog 时;
    close 自动生成本卡条目, 属本卡落账)。返回 {paths, own, audit, chronicle, card_found}。"""
    from tools.aipos_cli.task_loader import find_task_card
    from tools.aipos_cli.workspace_config import project_paths

    root = Path(governance_root)
    card_path, _state = find_task_card(root, task_id)
    own = card_own_paths(root, task_id, card_path=card_path)
    audit = list(dict.fromkeys(p for audit_id in audit_card_ids(root, task_id) for p in card_own_paths(root, audit_id)))
    chronicle_path = project_paths(root).get("foundation_backlog")
    chronicle = _gov_rel(root, Path(chronicle_path)) if chronicle_path else None
    paths = list(dict.fromkeys(own + audit + ([chronicle] if chronicle else [])))
    # AIPOS-F147 件①: 随提交带走、不入判据的路径(本卡运行中的 loop 运行记录); 读不出、无从归属的部署记录(出声, 不猜)
    trailing = [p for p in card_trailing_paths(root, task_id) if p not in paths]
    unattributed = [rel for rel in (_gov_rel(root, p) for p, _why in deployment_attribution(root)["unreadable"]) if rel]
    return {"paths": paths, "own": own, "audit": audit, "chronicle": chronicle, "card_found": card_path is not None,
            "trailing": trailing, "unattributed_deployments": unattributed}


def _under(path: str, scope_path: str) -> bool:
    return path == scope_path or path.startswith(scope_path.rstrip("/") + "/")


def resolve_task_scope(governance_root: Path, task_id: str) -> dict[str, Any]:
    """件① task 范围精确提交的路径白名单: 候选中「盘上存在或已被跟踪(含待暂存的删除)」且未被 .gitignore 排除者。

    被忽略的候选(如项目把台账目录 task_cards/ 列入 .gitignore)原样报出 ignored, 不提交、不报错(仓的既定排除)。
    git 只读调用失败直接抛 CalledProcessError(调用方 fail-closed)。返回 {paths, ignored, absent, candidates, card_found, chronicle}。
    """
    root = Path(governance_root)
    scope = task_scope_candidates(root, task_id)
    # AIPOS-F147 件①: 提交白名单 = 判据范围 + 随提交带走者(trailing); 判据(task_landing)仍只看 scope["paths"]
    candidates = list(dict.fromkeys(scope["paths"] + scope["trailing"]))
    tracked_files = _split_nul(_git_readonly(["ls-files", "-z", "--"] + candidates, root)) if candidates else []
    prefix = _git_ws_prefix(root)
    tracked_rel = [f[len(prefix):] if prefix and f.startswith(prefix) else f for f in tracked_files]
    present: list[str] = []
    absent: list[str] = []
    for cand in candidates:
        if (root / cand).exists() or any(_under(t, cand) for t in tracked_rel):
            present.append(cand)
        else:
            absent.append(cand)
    ignored: list[str] = []
    untracked_present = [p for p in present if not any(_under(t, p) for t in tracked_rel)]
    if untracked_present:
        proc = subprocess.run(_GIT_QUOTEPATH_OFF + ["check-ignore", "-z", "--stdin"], input="\0".join(untracked_present) + "\0",
                              cwd=str(root), capture_output=True, text=True, timeout=30)
        if proc.returncode not in (0, 1):  # 0 = 有被忽略者, 1 = 无; 其余 = git 失败(fail-closed)
            raise subprocess.CalledProcessError(proc.returncode, proc.args, proc.stdout, proc.stderr)
        ignored_set = set(_split_nul(proc.stdout))
        ignored = [p for p in untracked_present if p in ignored_set or p.rstrip("/") in ignored_set]
    paths = [p for p in present if p not in ignored]
    # AIPOS-F147 件①: 读不出、无从归属的部署记录只报未入库者(已跟踪的存量旧格式已在仓里, 不丢, 不再逐次出声)
    unattributed = scope["unattributed_deployments"]
    if unattributed:
        tracked_unattr = set(_split_nul(_git_readonly(["ls-files", "-z", "--"] + unattributed, root)))
        unattributed = [u for u in unattributed if f"{prefix}{u}" not in tracked_unattr and u not in tracked_unattr]
    return {"paths": paths, "ignored": ignored, "absent": absent, "candidates": candidates,
            "card_found": scope["card_found"], "chronicle": scope["chronicle"], "trailing": scope["trailing"],
            "unattributed_deployments": unattributed}


def governance_landing(governance_root: Path, scopes: dict[str, list[str]]) -> dict[str, dict[str, Any]]:
    """件①③ 落账判据(唯一实现; 推导核 N6 落账步、loop 出口 0、state lint 同读): 每张卡的范围路径
    ① 无未跟踪 / 未提交 / 已暂存未提交(`git status --porcelain -uall`, .gitignore 排除者不算) 且
    ② 无未推送(`git log <上游>..HEAD` 触及范围路径; 上游 = 当前分支 @{u}, 读上次 fetch 结果, 只读不联网)。

    scopes = {卡ID: [治理根相对路径, ...]}。只读: 两次 git 调用覆盖全部卡。治理根不在 git 工作树内 / 当前分支无上游 = 一律
    landed=False 并给出原因(fail-closed: 无仓即无可追溯真相, 无上游即无从证明已推送)。
    返回 {卡ID: {landed, git, upstream, uncommitted: [..], unpushed: [..], reason}}。
    """
    root = Path(governance_root)

    def verdict(**kw: Any) -> dict[str, dict[str, Any]]:
        return {tid: dict(kw) for tid in scopes}

    try:
        inside = _git_readonly(["rev-parse", "--is-inside-work-tree"], root).strip()
    except (subprocess.CalledProcessError, FileNotFoundError, NotADirectoryError) as exc:
        detail = (getattr(exc, "stderr", "") or str(exc)).strip().splitlines()
        return verdict(landed=False, git=False, upstream=None, uncommitted=[], unpushed=[],
                       reason=f"治理根 {root} 不在 git 工作树内({detail[-1] if detail else exc}): 卡与记录只在盘上, 未成为可追溯真相; "
                              f"出口: {non_git_exit_command(root)}(Owner 按其输出配 origin 并首推)")
    if inside != "true":
        return verdict(landed=False, git=False, upstream=None, uncommitted=[], unpushed=[],
                       reason=f"治理根 {root} 不在 git 工作树内(rev-parse --is-inside-work-tree = {inside}); 出口: {non_git_exit_command(root)}")
    prefix = _git_ws_prefix(root)

    def gov_rel(repo_path: str) -> str:
        return repo_path[len(prefix):] if prefix and repo_path.startswith(prefix) else repo_path

    dirty: list[str] = []
    untracked: list[str] = []
    for xy, repo_path in _porcelain_entries(_git_readonly(["status", "--porcelain", "--untracked-files=all", "-z", "--", "."], root)):
        dirty.append(gov_rel(repo_path))
        if xy == "??":
            untracked.append(gov_rel(repo_path))
    # AIPOS-F195 件②: 已记入同槽清单且 sha256 未变的产物槽代码文件 = N6 已按声明排除(原文件留盘), 不算未落账;
    # 处置声明读不出 = 不剔除(仍算未落账, fail-closed)并在原因里写明
    settle_note = ""
    try:
        settled = recorded_slot_code_files(root, untracked) if untracked else set()
    except (ValueError, OSError, RuntimeError) as exc:  # RuntimeError ⊇ GuardrailDeclarationError
        settled, settle_note = set(), f"产物槽代码文件排除清单不可判({exc})"
    dirty = [d for d in dirty if d not in settled]
    upstream: str | None
    try:
        upstream = _git_readonly(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], root).strip() or None
    except subprocess.CalledProcessError:
        upstream = None
    unpushed: list[str] = []
    if upstream:
        unpushed = [gov_rel(p) for p in _split_nul(_git_readonly(
            ["log", "--format=", "--name-only", "--no-renames", "-z", f"{upstream}..HEAD", "--", "."], root)) if p.strip()]
    out: dict[str, dict[str, Any]] = {}
    for tid, paths in scopes.items():
        unc = sorted({d for d in dirty if any(_under(d, p) for p in paths)})
        unp = sorted({u.strip() for u in unpushed if any(_under(u.strip(), p) for p in paths)})
        reasons: list[str] = []
        if not paths:
            reasons.append("卡范围推导为空(队列无卡文件、无记录、无台账)")
        if unc:
            reasons.append(f"未跟踪/未提交 {len(unc)} 个: " + ", ".join(unc[:5]) + (f" …(+{len(unc) - 5})" if len(unc) > 5 else "")
                           + (f"; {settle_note}" if settle_note else ""))
        if upstream is None:
            reasons.append("当前分支无上游(@{u}), 无从证明已推送")
        elif unp:
            reasons.append(f"已提交未推送({upstream}..HEAD) {len(unp)} 个: " + ", ".join(unp[:5]) + (f" …(+{len(unp) - 5})" if len(unp) > 5 else ""))
        out[tid] = {"landed": not reasons, "git": True, "upstream": upstream, "uncommitted": unc, "unpushed": unp,
                    "reason": "; ".join(reasons)}
    return out


def task_landing(governance_root: Path, task_id: str) -> dict[str, Any]:
    """单卡落账判据(推导核 N6 落账步与 loop 出口用): 范围 = task_scope_candidates(含审计卡与卡编年史)。"""
    scope = task_scope_candidates(Path(governance_root), task_id)
    status = governance_landing(Path(governance_root), {task_id: scope["paths"]})[task_id]
    status["scope"] = scope["paths"]
    return status


#: AIPOS-F147 件①: 补落账结果行的行首(唯一出处; async 回归后台日志与 loop 输出同用)
RELAND_MARKER = "补落账:"


def reland_commit_message(task_id: str, actor: str, what: str) -> str:
    """AIPOS-F184 件①: 补落账提交信息 = transitions nodes.N6.landing.reland_message_template(与首次「N6 收账 <卡>」区分:
    10-17 F183 三条同名提交 = 首次 N6 + loop 运行记录终态补落账 + 合并后回归 async 记录补落账, 内容各不相同, 仅提交名相同难辨)。"""
    from tools.schema_loader import SchemaLoadError

    template = str(n6_landing_declaration().get("reland_message_template") or "")
    if "{task_id}" not in template or "{actor}" not in template:
        raise SchemaLoadError("transitions.schema.json nodes.N6.landing.reland_message_template 未声明(须含 {task_id} 与 {actor})")
    return template.format(task_id=task_id, actor=actor, what=what)


def reland_after_write(governance_root: Path, task_id: str, actor: str, *, what: str, push: bool = True) -> dict[str, Any]:
    """AIPOS-F147 件①: 卡结案(N6)之后才落盘的门记录(合并后回归 async 事件记录 / loop 结束时的运行记录终态)写完后补一次落账——
    复用 N6 同一提交口 governance_commit(task 范围精确提交 + 推送; 范围已落账 = no-op), 不另写提交路径。

    卡尚未结案(无 closure 记录)= 不提交: 该记录在本卡落账范围内(events/<卡ID>/ 与已结束的 loop 运行记录入判据), 由本卡 N6 带上。
    补落账失败(并发提交撞锁 / 推送被拒等)不吞: 结果行如实带拒因; 记录仍在本卡判据范围内 → state lint GOVERNANCE_UNCOMMITTED
    点名并给 `governance-commit --task-id` 出口, loop 再推该卡时推导核派生 N6 落账步——不丢。
    返回 {relanded, verdict, committed, pushed, line}(line 以 RELAND_MARKER 起头, 调用方原样输出)。"""
    from tools.aipos_cli.next_resolver import _read_task_records

    root = Path(governance_root)
    if not _read_task_records(root, task_id).get("latest_closure"):
        return {"relanded": False, "verdict": None, "committed": False, "pushed": False,
                "line": f"{RELAND_MARKER} {task_id} 未结案, {what}由本卡 N6 落账带上(在本卡落账范围内)"}
    result = governance_commit(root, task_id, actor, push=push, message=reland_commit_message(task_id, actor, what))
    first = next((ln.strip() for ln in str(result.get("message") or "").splitlines() if ln.strip()), "")
    return {"relanded": bool(result.get("committed")), "verdict": result.get("verdict"), "committed": bool(result.get("committed")),
            "pushed": bool(result.get("pushed")),
            "line": (f"{RELAND_MARKER} {task_id} {what}: verdict={result.get('verdict')} committed={bool(result.get('committed'))} "
                     f"pushed={bool(result.get('pushed'))} — {first[:300]}"
                     + ("" if result.get("verdict") == Verdict.PASS else
                        f"; 出口: {governance_commit_command(task_id, actor, root)}"))}


# ---------------------------------------------------------------------------
# AIPOS-F158(gap #130): 写后落账——非卡写命令(信封签发 / 记录类型补齐迁移)写完即经本模块同一提交口精确提交并推送。
# 声明 = verbs.schema verbs.lybra_governance_commit.post_write_landing(提交信息模板 / 落账失败退出码); 提交本体 = governance_commit
# (--paths 白名单 = 本次写出的文件, 护栏 / 清单核对 / 推送判据与 N6 落账同一实现), 不另写 git 提交逻辑。
# ---------------------------------------------------------------------------

def post_write_landing_declaration() -> dict[str, Any]:
    """verbs.schema verbs.lybra_governance_commit.post_write_landing(唯一声明)。缺键 / 形不合 = SchemaLoadError(fail-closed)。"""
    from tools.aipos_cli.next_resolver import REPO_ROOT
    from tools.schema_loader import SchemaLoadError, load_schema

    decl = (((load_schema("verbs", REPO_ROOT).get("verbs") or {}).get("lybra_governance_commit") or {}).get("post_write_landing"))
    commands = decl.get("commands") if isinstance(decl, dict) else None
    if (not isinstance(decl, dict) or not isinstance(decl.get("landing_failed_exit_code"), int)
            or isinstance(decl.get("landing_failed_exit_code"), bool) or decl["landing_failed_exit_code"] in (0, 1, 2)
            or not isinstance(commands, dict) or not commands
            or not all(isinstance(c, dict) and "{actor}" in str(c.get("message_template") or "") for c in commands.values())):
        raise SchemaLoadError("verbs.schema.json verbs.lybra_governance_commit.post_write_landing(landing_failed_exit_code 非 0/1/2 的整数 / "
                              "commands.<命令>.message_template 含 {actor})未声明齐")
    return decl


def land_written_paths(governance_root: Path | str, paths: list[str], actor: str, *, command: str, push: bool = True,
                       **fields: Any) -> dict[str, Any]:
    """AIPOS-F158: 写后落账唯一实现——paths(治理根相对, 本次写出的文件)经 governance_commit 精确提交并推送(推送策略同 N6 落账)。
    范围已落账 = governance_commit PASS no-op(幂等)。落账失败不回滚已写内容(写入已生效), 结果带拒因原文与补账命令。
    返回 {landed, verdict, committed, pushed, commit_hash, reason, remedy, exit_code, operations, skipped_vanished_untracked, line};
    line 供调用方原样输出(AIPOS-F193: 有从未入库且已不存在而被剔除的路径时行尾列出)。"""
    root = Path(governance_root)
    decl = post_write_landing_declaration()
    spec = decl["commands"].get(command)
    if not isinstance(spec, dict):
        from tools.schema_loader import SchemaLoadError

        raise SchemaLoadError(f"verbs.schema.json verbs.lybra_governance_commit.post_write_landing.commands 未登记 {command}")
    selected = sorted({str(p) for p in paths if str(p).strip()})
    remedy = governance_commit_paths_command(selected, actor, root)  # 空 paths = ValueError(禁整根)
    message = str(spec["message_template"]).format(actor=actor, **fields)
    result = governance_commit(root, None, actor, push=push, message=message, paths=selected)
    landed = result.get("verdict") == Verdict.PASS
    first = next((ln.strip() for ln in str(result.get("message") or "").splitlines() if ln.strip()), "")
    push_reason = (result.get("push_result") or {}).get("reason")
    reason = None if landed else (push_reason or first or str(result.get("verdict")))
    commit_hash = result.get("commit_hash")
    skipped = list(result.get("skipped_vanished_untracked") or [])
    skipped_note = f"; 已跳过 {len(skipped)} 个从未入库且已不存在的路径: {', '.join(skipped)}" if skipped else ""  # AIPOS-F193 件①
    if landed:
        line = (f"落账: verdict={result.get('verdict')} committed={bool(result.get('committed'))} pushed={bool(result.get('pushed'))}"
                + (f" commit={str(commit_hash)[:12]}" if commit_hash else f" — {first[:200]}") + f"({len(selected) - len(skipped)} 个文件)" + skipped_note)
    else:
        line = (f"⚠ 落账未完成(写入已生效, 勿重复执行写命令): verdict={result.get('verdict')} committed={bool(result.get('committed'))} "
                f"pushed={bool(result.get('pushed'))} — {str(reason)[:500]}\n  补账: {remedy}")
    return {"landed": landed, "verdict": result.get("verdict"), "committed": bool(result.get("committed")),
            "pushed": bool(result.get("pushed")), "commit_hash": commit_hash, "paths": selected, "reason": reason,
            "remedy": None if landed else remedy, "exit_code": 0 if landed else int(decl["landing_failed_exit_code"]),
            "operations": list(result.get("operations") or []), "skipped_vanished_untracked": skipped, "line": line}


def check_governance_completeness(
    governance_root: Path,
    task_id: str | None,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """校验 N6 收账清单四件齐全。
    
    AIPOS-R7A2 FIX-1: 所有治理路径从 config.schema 解析,零写死。
    参考 finalize.py::check_stage_archive_gate 的正确模式。
    
    AIPOS-F69 大项①: task_id 可选 — 无卡时跳过本卡台账条目检查(治理批次语义),
    仍走同一校验链与 pre-commit 四检。
    
    Args:
        governance_root: 治理工作区根
        task_id: 任务 ID (可选;无卡时跳过本卡台账检查)
        repo_root: 产品仓根 (用于定位 schema/config.schema.json)
    
    Returns:
        {
            "complete": bool,
            "missing": list[str],  # 缺件列表
            "details": dict,       # 各项详情
        }
    """
    from tools.schema_loader import SchemaLoadError, resolve_governance_path

    missing = []
    details = {}
    
    # AIPOS-R7A2 FIX-2: 四件解析统一为"解析失败即显式报错/BLOCK",禁静默降级到硬编码
    
    # AIPOS-F69 大项①: 无 task_id 时跳过本卡台账条目检查(治理批次语义)
    if task_id is None:
        details["task_cards"] = {"exists": None, "note": "Skipped (no task_id provided)"}
        details["archive_files"] = {"exists": None, "note": "Skipped (no task_id provided)"}
        details["decision_log"] = {"applicable": False, "note": "Skipped (no task_id provided)"}
        details["stage_snapshots"] = {"applicable": True, "note": "Stage archive check still applies"}
        # 无卡时只检查 stage_archive (阶段粒度治理更新仍需快照)
    else:
        # ① 本卡台账条目 (<paths.task_cards_root>/<ID>/) + ④ 归档文件检查
        # AIPOS-F89 件① H9: 台账根只读项目声明 project.json paths.task_cards_root(唯一读取口 workspace_config.project_paths);
        # 原读 config.schema governance_structure.paths.task_cards(第二份声明, 已删)。解析失败即 BLOCK(R7A2 FIX-2 语义不变)。
        from tools.aipos_cli.workspace_config import project_paths

        try:
            task_cards_root = Path(project_paths(governance_root)["task_cards_root"])
            task_cards_dir = task_cards_root / task_id
            try:
                ledger_ref = task_cards_dir.relative_to(governance_root).as_posix()
            except ValueError:
                ledger_ref = str(task_cards_dir)
        
            if task_cards_dir.is_dir():
                details["task_cards"] = {
                    "exists": True,
                    "path": str(task_cards_dir),
                    "files": [f.name for f in task_cards_dir.iterdir()],
                }
                
                # ④ task_cards 归档 (RETURN.md/AUDIT-REPORT.md/CLOSURE.md)
                archive_files = [
                    f.name for f in task_cards_dir.iterdir()
                    if f.name in ["RETURN.md", "AUDIT-REPORT.md", "CLOSURE.md"]
                ]
                if not archive_files:
                    # AIPOS-F147 件③(gap #115): 台账根缺省随交回根(task_cards_root default_from return_root)时, 台账目录 = 交回目录;
                    # 交回文件名按项目候选(transitions artifact_ingest.return.return_file_candidates)认, 经唯一 Return 查找
                    # next_resolver.find_return_artifact——不另写候选匹配。
                    from tools.aipos_cli.next_resolver import find_return_artifact

                    found_return = find_return_artifact(governance_root, task_id)
                    if found_return is not None and found_return.parent.resolve() == task_cards_dir.resolve():
                        archive_files = [found_return.name]

                if not archive_files:
                    missing.append(f"{ledger_ref}/ 缺少归档文件 (RETURN.md/AUDIT-REPORT.md/CLOSURE.md)")
                
                details["archive_files"] = {
                    "exists": len(archive_files) > 0,
                    "files": archive_files,
                }
            else:
                missing.append(f"{ledger_ref}/ (台账条目不存在)")
                details["task_cards"] = {"exists": False}
                details["archive_files"] = {"exists": False, "files": []}
        except (SchemaLoadError, OSError, ValueError) as exc:
            # AIPOS-R7A2 FIX-2: 移除 fallback,解析失败显式报错
            missing.append(f"<paths.task_cards_root>/ (路径解析失败: {exc})")
            details["task_cards"] = {"exists": False, "error": str(exc)}
            details["archive_files"] = {"exists": False, "error": str(exc)}
        
        # ② decision_log 指针 (如适用)
        # AIPOS-R7A2 FIX-2: decision_log 路径从 schema 解析,失败显式报错
        try:
            decision_log_dir = resolve_governance_path("decision_log_dir", governance_root, repo_root)
            # 在 decision_log/ 下查找与本任务相关的条目 (按 YYYY-MM/YYYY-MM-DD-<slug>.md 结构)
            # 这里简化为检查整个目录树中包含 task_id 的 .md 文件
            decision_files = []
            if decision_log_dir.is_dir():
                for md_file in decision_log_dir.rglob("*.md"):
                    if task_id.lower() in md_file.stem.lower():
                        decision_files.append(str(md_file.relative_to(decision_log_dir)))
            
            details["decision_log"] = {
                "applicable": len(decision_files) > 0,
                "files": decision_files,
                "path": str(decision_log_dir),
            }
        except Exception as exc:
            # AIPOS-R7A2 FIX-2: decision_log 解析失败也显式报错 (BLOCK)
            missing.append(f"decision_log_dir/ (路径解析失败: {exc})")
            details["decision_log"] = {
                "applicable": False,
                "error": str(exc),
            }
    
    # ③ 阶段快照
    # AIPOS-R7A2 FIX-2: 路径解析失败显式报错 (同 finalize.py 模式)
    # AIPOS-F145 件①: 落点经唯一读取口 stage_archive_root(project.json paths.stage_archive_root 声明, 缺省 = governance_structure)
    try:
        from tools.aipos_cli.workspace_config import stage_archive_root

        stage_archive_dir = stage_archive_root(governance_root)
        
        stage_snapshots = []
        if stage_archive_dir.is_dir():
            # 阶段快照 = *.md 文件 (排除 README/index)
            for snapshot_file in stage_archive_dir.glob("*.md"):
                if snapshot_file.name.lower() not in {"readme.md", "index.md"}:
                    stage_snapshots.append(snapshot_file.name)
        
        details["stage_snapshots"] = {
            "applicable": True,
            "snapshots": stage_snapshots,
            "path": str(stage_archive_dir),
        }
        
        # 阶段快照为空时不强制 BLOCK (允许非阶段收口卡无快照)
        if not stage_snapshots and stage_archive_dir.is_dir():
            details["stage_snapshots"]["note"] = "Stage archive directory exists but no snapshots found (OK for non-stage-closure tasks)"
    
    except Exception as exc:
        # AIPOS-R7A2 FIX-2: stage_archive 解析失败显式报错 (BLOCK)
        missing.append(f"stage_archive/ (路径解析失败: {exc})")
        details["stage_snapshots"] = {
            "applicable": False,
            "error": str(exc),
        }
    
    return {
        "complete": len(missing) == 0,
        "missing": missing,
        "details": details,
    }


# ---------------------------------------------------------------------------
# AIPOS-F195 件②: 产物槽内代码文件的 N6 处置(唯一落账实现在此一处; 落账判据 governance_landing 同读)
#
# 病根(10-20 chris 岚图 LANTU-SW13F1): 已审结卡报告落点 artifacts/ 里留着执行体临时写的 run.py, N6 落账整批被提交门 B① 拒,
# loop landing_rejected 停在 owner_needed, 无正式出口。处置 = 产物槽(record_file.required_frontmatter_exemptions.artifact_slot 所列
# project.json 落点 <落点>/<ID>/**)内新增(未跟踪 / 已暂存新增)代码文件不入库(暂存 pathspec 排除), 同槽写清单
# (config.schema governance_worktree.path_constraints.artifact_slot_code_files.excluded_manifest_name: 路径 / sha256 / 大小 / 原因,
# 只追加)一并落账, 原文件留盘不删。槽外代码文件与已跟踪代码文件的修改照旧被 B① 拒(提交门护栏不放宽, 治理仓仍零代码文件)。
# ---------------------------------------------------------------------------

def _slot_code_declarations(repo_root: Path | None):
    """产物槽代码文件处置的声明(governance_guardrails.load_guardrail_declarations 唯一读取口; 与四检同一 schema 目录序)。"""
    from tools.aipos_cli.governance_guardrails import load_guardrail_declarations, resolve_schema_dir

    return load_guardrail_declarations(resolve_schema_dir(repo_root))


def _read_exclusion_manifest(path: Path) -> dict[str, Any]:
    """读同槽已有清单; 不存在 = 空。读不出 / 形不合 = ValueError(fail-closed: 不覆盖他人写坏的清单)。"""
    import json

    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"代码文件排除清单读不出 {path}: {exc.__class__.__name__}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("files"), list):
        raise ValueError(f"代码文件排除清单形不合 {path}: 须为含 files 列表的对象")
    return data


def slot_code_candidates(governance_root: Path, gov_rel_paths: list[str], *, decls: Any) -> list[dict[str, Any]]:
    """gov_rel_paths(治理根相对)中落在产物槽 <槽根>/<ID>/** 内的代码文件(判据 governance_guardrails.is_code_file, 槽根
    governance_guardrails.artifact_slot_roots, 均唯一实现)。返回 [{path, slot_dir, slot_key, task_id, rel, sha256, size, recorded,
    manifest}](recorded = 同槽清单已记且 sha256 未变)。声明缺(decls.slot_code is None)= 空列表(不处置)。"""
    from tools.aipos_cli.governance_guardrails import artifact_slot_roots, file_sha256, is_code_file

    if decls.slot_code is None:
        return []
    code_paths = [p for p in gov_rel_paths if is_code_file(p, decls)]
    if not code_paths:
        return []
    root = Path(governance_root)
    slots = [(key, rel) for key, slot_root, _origin in artifact_slot_roots(root, decls, what="产物槽代码文件处置")
             for rel in [_gov_rel(root, slot_root)] if rel]
    manifests: dict[str, dict[str, Any]] = {}
    out: list[dict[str, Any]] = []
    for path in code_paths:
        hit = next(((key, slot_rel) for key, slot_rel in slots
                    if path.startswith(f"{slot_rel}/") and len(path[len(slot_rel) + 1:].split("/")) >= 2), None)
        full = root / path
        if hit is None or not full.is_file():
            continue
        key, slot_rel = hit
        task_id = path[len(slot_rel) + 1:].split("/")[0]
        slot_dir = f"{slot_rel}/{task_id}"
        manifest_rel = f"{slot_dir}/{decls.slot_code.manifest_name}"
        if manifest_rel not in manifests:
            manifests[manifest_rel] = _read_exclusion_manifest(root / manifest_rel)
        rel = path[len(slot_dir) + 1:]
        sha = file_sha256(full)
        recorded = any(isinstance(e, dict) and e.get("path") == rel and e.get("sha256") == sha
                       for e in manifests[manifest_rel].get("files") or [])
        out.append({"path": path, "slot_dir": slot_dir, "slot_key": key, "task_id": task_id, "rel": rel, "sha256": sha,
                    "size": full.stat().st_size, "recorded": recorded, "manifest": manifest_rel})
    return out


def recorded_slot_code_files(governance_root: Path, gov_rel_paths: list[str], *, repo_root: Path | None = None) -> set[str]:
    """已记入同槽清单且 sha256 未变的产物槽代码文件(治理根相对)——落账判据与「有无待收内容」判定不算它们未落账。"""
    candidates = [p for p in gov_rel_paths if p]
    if not candidates:
        return set()
    decls = _slot_code_declarations(repo_root)
    return {item["path"] for item in slot_code_candidates(Path(governance_root), candidates, decls=decls) if item["recorded"]}


def _write_exclusion_manifests(governance_root: Path, items: list[dict[str, Any]], actor: str, *, decls: Any) -> list[str]:
    """未记的排除项追加进各槽清单(原子替换写)。返回写过的清单路径(治理根相对)。"""
    import json

    from tools.aipos_cli.clock import iso_z

    root = Path(governance_root)
    written: list[str] = []
    now = iso_z()
    for manifest_rel in sorted({i["manifest"] for i in items if not i["recorded"]}):
        path = root / manifest_rel
        data = _read_exclusion_manifest(path)
        group = [i for i in items if i["manifest"] == manifest_rel and not i["recorded"]]
        files = list(data.get("files") or [])
        files += [{"path": i["rel"], "sha256": i["sha256"], "size": i["size"], "reason": decls.slot_code.text("manifest_reason"),
                   "excluded_at": now, "excluded_by": actor} for i in group]
        first = group[0]
        payload = {"kind": decls.slot_code.manifest_kind, "task_id": data.get("task_id") or first["task_id"],
                   "slot": first["slot_dir"], "slot_key": first["slot_key"],
                   "basis": "config.schema governance_worktree.path_constraints.artifact_slot_code_files (AIPOS-F195)",
                   "files": files}
        tmp = path.with_name(f".{path.name}.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)
        written.append(manifest_rel)
    return written


def _drop_from_manifest(manifest: dict[str, Any], repo_paths: set[str]) -> dict[str, Any]:
    files = [f for f in manifest["files"] if f["repo_path"] not in repo_paths]
    counts = {MANIFEST_MODIFIED: 0, MANIFEST_ADDED: 0, MANIFEST_DELETED: 0, MANIFEST_UNTRACKED: 0}
    for f in files:
        counts[f["status"]] += 1
    counts["total"] = len(files)
    return {**manifest, "files": files, "counts": counts}


def _exclude_spec(repo_path: str) -> str:
    """暂存 / 核对 pathspec 的排除项(仓根相对字面路径)。"""
    return f":(top,exclude,literal){repo_path}"


def _apply_slot_code_exclusion(governance_root: Path, manifest: dict[str, Any], selected_paths: list[str] | None,
                               repo_root: Path | None, actor: str, *, dry_run: bool) -> dict[str, Any]:
    """清单中产物槽内新增代码文件 → 不入库: 从清单剔除; 非预演时把未记者追加进同槽清单并把清单纳入本次提交。
    返回 {excluded: [项], manifests: [清单路径], written: [本次写的], manifest: 新清单, selected_paths, exclude_specs, line}。
    声明 / 清单读不出 = GuardrailDeclarationError / ValueError 原样抛(调用方 fail-closed)。"""
    decls = _slot_code_declarations(repo_root)
    added = [f for f in manifest["files"] if f["status"] in (MANIFEST_UNTRACKED, MANIFEST_ADDED)]
    items = slot_code_candidates(Path(governance_root), [f["path"] for f in added], decls=decls)
    none = {"excluded": [], "manifests": [], "written": [], "manifest": manifest, "selected_paths": selected_paths,
            "exclude_specs": [], "line": ""}
    if not items:
        return none
    by_gov = {f["path"]: f["repo_path"] for f in added}
    excluded_repo = {by_gov[i["path"]] for i in items}
    manifests = sorted({i["manifest"] for i in items})
    files_text = ", ".join(f"{i['path']}(sha256 {i['sha256'][:12]}…)" for i in items)
    written: list[str] = []
    if dry_run:
        line = decls.slot_code.text("n6_dry_run_line", count=len(items), manifests=", ".join(manifests), files=files_text)
    else:
        written = _write_exclusion_manifests(Path(governance_root), items, actor, decls=decls)
        if selected_paths is not None:
            selected_paths = list(selected_paths) + [m for m in manifests if not any(_under(m, p) for p in selected_paths)]
        manifest = collect_commit_manifest(governance_root, selected_paths)
        line = decls.slot_code.text("n6_excluded_line", count=len(items), manifests=", ".join(manifests))
    return {"excluded": items, "manifests": manifests, "written": written, "manifest": _drop_from_manifest(manifest, excluded_repo),
            "selected_paths": selected_paths, "exclude_specs": [_exclude_spec(p) for p in sorted(excluded_repo)], "line": line}


# ---------------------------------------------------------------------------
# AIPOS-F79C: push 判据(双向 rev-list)+ 临时 linked worktree 整合 + pushed=False 出声
# ---------------------------------------------------------------------------

_TMP_WORKTREE_PREFIX = "lybra-governance-commit-"
PUSH_NOT_DONE_PREFIX = "PUSH NOT DONE: "


def _commit_hook_env() -> dict[str, str]:
    """AIPOS-F93 件④: 正式提交子进程的环境——补 LYBRA_SCHEMA_DIR, 治理仓 pre-commit 钩子据此定位 schema。

    病根(chris 实撞): 钩子在 LYBRA_SCHEMA_DIR 未设时靠 PATH 上的 `lybra workspace roots --field schema_dir`; 非登录 shell
    (ssh 调用 / PATH 无 lybra)以 CLI 绝对路径调 governance-commit 即被钩子 BLOCK「无法定位 schema 目录」。
    值 = governance_guardrails.default_schema_dir()(与钩子同序的唯一实现: 调用方已设 LYBRA_SCHEMA_DIR 则原样尊重, 否则
    schema_loader.code_repo_schema_root()/schema = 本进程运行的 Lybra 代码所在仓)。钩子行为不变; dry-run 判据路径不经此处。"""
    from tools.aipos_cli.governance_guardrails import default_schema_dir

    return {**os.environ, "LYBRA_SCHEMA_DIR": str(default_schema_dir())}


def _git_run(args: list[str], cwd: Path, *, timeout: int = 60) -> subprocess.CompletedProcess:
    """写操作 git 调用(fetch/push/add/reset/worktree/cherry-pick); 失败抛 CalledProcessError, 禁静默。"""
    return subprocess.run(
        _GIT_QUOTEPATH_OFF + args,
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _divergence(governance_root: Path, upstream: str) -> tuple[int, int]:
    """件① 判据: (远端独有提交数, 本地独有提交数) = rev-list --count HEAD..upstream / upstream..HEAD。"""
    remote_only = int(_git_readonly(["rev-list", "--count", f"HEAD..{upstream}"], governance_root).strip())
    local_only = int(_git_readonly(["rev-list", "--count", f"{upstream}..HEAD"], governance_root).strip())
    return remote_only, local_only


def _local_commits_not_upstream(governance_root: Path, upstream: str) -> list[str]:
    """本地独有且其补丁(patch-id)尚未在远端的提交, 旧→新(即 rebase 会重放的集合)。"""
    raw = _git_readonly(
        ["rev-list", "--reverse", "--right-only", "--cherry-pick", f"{upstream}...HEAD"], governance_root
    )
    return [line for line in raw.split("\n") if line]


def _status_paths(governance_root: Path) -> set[str]:
    """共享检出里所有未提交状态的仓根相对路径(含已暂存/未暂存/未跟踪), 只读。"""
    tokens = _split_nul(
        _git_readonly(["status", "--porcelain", "--untracked-files=all", "-z"], governance_root)
    )
    paths: set[str] = set()
    i = 0
    while i < len(tokens):
        entry = tokens[i]
        i += 1
        xy, repo_path = entry[:2], entry[3:]
        paths.add(repo_path)
        if xy[0] in ("R", "C") and i < len(tokens):
            paths.add(tokens[i])  # 重命名/复制的源路径
            i += 1
    return paths


def _dirty_overlap_with(governance_root: Path, upstream: str) -> list[str]:
    """把本地分支移到 upstream 会改写的文件(HEAD vs upstream)中, 本地有未提交状态的那些(fail-closed 前置守卫)。"""
    changed = set(_split_nul(_git_readonly(["diff", "--name-only", "-z", "HEAD", upstream], governance_root)))
    return sorted(changed & _status_paths(governance_root))


def integrate_in_temp_worktree(
    governance_root: Path,
    branch: str,
    commits: list[str],
    actor: str,
    operations: list[str],
) -> dict[str, Any]:
    """AIPOS-F79C 件②: 在 <repo>/.git 之外的临时 linked worktree 上 cherry-pick 本地独有提交并 push。

    共享检出的工作树、索引、他项目未提交文件全程不动;禁 stash/禁整根 add -A/禁 pull --rebase。
    cherry-pick 冲突 → --abort, 精确取 unmerged 文件清单, 清理临时 worktree, 返回 ok=False(调用方 BLOCK,
    本地 commit 保留)。临时 worktree 在 finally 里无条件清理(remove --force + rmtree + prune)。

    Returns:
        {"ok": bool, "pushed_tip": str|None, "conflict_files": [repo_path...], "reason": str|None,
         "temp_worktree": str, "cleaned": bool}
    """
    tmp_root = Path(tempfile.mkdtemp(prefix=_TMP_WORKTREE_PREFIX))
    wt = tmp_root / "wt"
    upstream = f"origin/{branch}"
    outcome: dict[str, Any] = {
        "ok": False, "pushed_tip": None, "conflict_files": [], "reason": None,
        "temp_worktree": str(wt), "cleaned": False,
    }
    try:
        _git_run(["worktree", "add", "--detach", str(wt), upstream], governance_root, timeout=120)
        operations.append(f"Temp worktree added (detached at {upstream}): {wt}")
        try:
            _git_run(
                ["-c", f"user.name={actor}", "-c", f"user.email={actor}@lybra.local", "cherry-pick", *commits],
                wt, timeout=120,
            )
        except subprocess.CalledProcessError as exc:
            conflict_files = sorted(
                _split_nul(_git_readonly(["diff", "--name-only", "--diff-filter=U", "-z"], wt))
            )
            try:
                _git_run(["cherry-pick", "--abort"], wt, timeout=60)
            except subprocess.CalledProcessError as abort_exc:
                operations.append(
                    f"WARNING: git cherry-pick --abort in temp worktree failed: {(abort_exc.stderr or '').strip()[:200]}"
                    f" (worktree is removed anyway)"
                )
            stderr_tail = (exc.stderr or "").strip().replace("\n", " | ")[:400]
            outcome["conflict_files"] = conflict_files
            outcome["reason"] = (
                f"cherry-pick conflict in temp worktree: {len(conflict_files)} file(s): {', '.join(conflict_files)}"
                if conflict_files
                else f"cherry-pick failed in temp worktree (no unmerged files): {stderr_tail}"
            )
            operations.append(
                f"Cherry-pick failed in temp worktree ({len(commits)} commit(s)); conflict files: "
                f"{conflict_files if conflict_files else '(none) ' + stderr_tail}; aborted"
            )
            return outcome
        tip = _git_readonly(["rev-parse", "HEAD"], wt).strip()
        operations.append(f"Cherry-picked {len(commits)} commit(s) onto {upstream} in temp worktree -> {tip[:8]}")
        _git_run(["push", "origin", f"HEAD:refs/heads/{branch}"], wt, timeout=30)
        outcome["ok"] = True
        outcome["pushed_tip"] = tip
        return outcome
    finally:
        if wt.exists():
            try:
                _git_run(["worktree", "remove", "--force", str(wt)], governance_root, timeout=60)
            except subprocess.CalledProcessError as rm_exc:
                operations.append(
                    f"WARNING: git worktree remove failed: {(rm_exc.stderr or '').strip()[:200]}; falling back to rmtree + prune"
                )
        try:
            shutil.rmtree(tmp_root)
        except OSError as os_exc:
            operations.append(f"WARNING: could not delete temp dir {tmp_root}: {os_exc.__class__.__name__}: {os_exc}")
        try:
            _git_run(["worktree", "prune"], governance_root, timeout=60)
        except subprocess.CalledProcessError as prune_exc:
            operations.append(f"WARNING: git worktree prune failed: {(prune_exc.stderr or '').strip()[:200]}")
        outcome["cleaned"] = not wt.exists() and not tmp_root.exists()
        operations.append(f"Temp worktree cleaned: {outcome['cleaned']}")


def _push_not_done(
    base: dict[str, Any],
    operations: list[str],
    *,
    reason: str,
    message: str,
    verdict: str,
) -> dict[str, Any]:
    """件③: pushed=False 的统一出口 — verdict 永不为 PASS, operations 末条 = PUSH NOT DONE: <原因>。"""
    assert verdict != Verdict.PASS
    operations.append(PUSH_NOT_DONE_PREFIX + reason)
    base.update({
        "verdict": verdict,
        "pushed": False,
        "message": message,
        "operations": operations,
        "push_result": {"pushed": False, "reason": reason},
    })
    return base


def _seal_push_outcome(result: dict[str, Any], *, push_requested: bool) -> dict[str, Any]:
    """件③ 总闸(唯一提交路径末端): 只要请求了 push 而 pushed=False, verdict 不得为 PASS,
    operations 末条必为 "PUSH NOT DONE: <原因>"。唯一例外: 无待收内容且本地无未推提交(真无事可推, EXIT=0)。
    """
    result["push_requested"] = push_requested
    if not push_requested:
        result.setdefault("push_result", None)
        return result
    ops = result.setdefault("operations", [])
    if result.get("pushed"):
        result["push_result"] = {"pushed": True, "reason": None}
        if not ops or not ops[-1].startswith("Push result:"):
            ops.append(f"Push result: pushed {str(result.get('commit_hash') or '')[:8]}")
        return result
    if result.get("severity") == "info" and not result.get("committed") and not result.get("unpushed_local_commits"):
        result["push_result"] = {"pushed": False, "reason": "nothing to push (no changes, no unpushed local commits)"}
        return result
    existing = result.get("push_result") or {}
    reason = existing.get("reason")
    if not reason:
        first_line = (result.get("message") or "").strip().split("\n", 1)[0]
        reason = (
            f"not committed ({result.get('verdict')}): {first_line}"
            if not result.get("committed")
            else f"committed {str(result.get('commit_hash') or '')[:8]} but not pushed ({result.get('verdict')}): {first_line}"
        )
    if result.get("verdict") == Verdict.PASS:
        result["verdict"] = Verdict.FAIL
    result["push_result"] = {"pushed": False, "reason": reason}
    if not ops or not ops[-1].startswith(PUSH_NOT_DONE_PREFIX):
        ops.append(PUSH_NOT_DONE_PREFIX + reason)
    return result


def _unpushed_local_count(governance_root: Path) -> int | None:
    """无待收内容路径用: 本地相对「上次 fetch 到的」origin/<branch> 的未推提交数(不联网); 无上游/detached → None。"""
    try:
        branch = _git_readonly(["rev-parse", "--abbrev-ref", "HEAD"], governance_root).strip()
        if branch == "HEAD":
            return None
        return int(_git_readonly(["rev-list", "--count", f"origin/{branch}..HEAD"], governance_root).strip())
    except (subprocess.CalledProcessError, ValueError):
        return None


def _push_failed(base: dict[str, Any], operations: list[str], *, reason: str, message: str, verdict: str) -> dict[str, Any]:
    """推送未完成(_push_local_head 失败出口): 结果骨架经 _push_not_done 封口(verdict != PASS, 末条 PUSH NOT DONE)。"""
    return {"ok": False, "result": _push_not_done(base, operations, reason=reason, message=message, verdict=verdict)}


def _push_local_head(
    governance_root: Path,
    commit_hash: str,
    actor: str,
    operations: list[str],
    base_factory: Any,
) -> dict[str, Any]:
    """N6 推送唯一实现(AIPOS-F184 件①: 自 _governance_commit_impl 提出, 新提交推送与「无新改动、补推本地未推提交」同用)。

    AIPOS-F79C 件①: fetch 后双向 rev-list 判分歧 — 远端独有=0 → 直接 fast-forward push, 不 rebase、不做脏树检查;
    件②: 远端独有>0 → 在仓外临时 linked worktree 上 cherry-pick 本地独有提交并在该 worktree push; 共享检出的工作树/索引/
    他项目未提交文件全程不动(禁 stash/禁整根 add -A/禁 pull --rebase); 冲突 → 清理临时 worktree, 精确列出冲突文件, BLOCK,
    本地 commit 保留。件③: 任何 pushed=False 都出声(_push_failed → _push_not_done)。
    base_factory(commit_hash) = 失败时的结果骨架(调用方给 committed 等字段)。
    返回 {"ok": True, pushed_commit_hash, push_mode, commit_hash(reset --keep 后可能更新)} 或 {"ok": False, "result": 封口结果}。
    """
    try:
        # ① Fetch 远端状态
        _git_run(["fetch", "origin"], governance_root, timeout=30)
        operations.append("Fetched from remote")

        current_branch = _git_readonly(["rev-parse", "--abbrev-ref", "HEAD"], governance_root).strip()
        if current_branch == "HEAD":
            return _push_failed(
                base_factory(commit_hash),
                operations,
                reason="detached HEAD: no branch to push",
                message=f"已提交 {commit_hash[:8]}, 但治理仓处于 detached HEAD, 无分支可推。\n可执行出口: cd {governance_root} && git switch <branch> 后重试",
                verdict=Verdict.BLOCK,
            )
        upstream = f"origin/{current_branch}"
        try:
            _git_readonly(["rev-parse", "--verify", "--quiet", f"{upstream}^{{commit}}"], governance_root)
        except subprocess.CalledProcessError:
            return _push_failed(
                base_factory(commit_hash),
                operations,
                reason=f"no remote-tracking branch {upstream} after fetch",
                message=(
                    f"已提交 {commit_hash[:8]}, 但 fetch 后不存在 {upstream}(远端无此分支, 或 remote 未配置)。\n"
                    f"可执行出口: cd {governance_root} && git push -u origin {current_branch}"
                ),
                verdict=Verdict.FAIL,
            )

        # ② 件①判据: 双向 rev-list(远端独有 / 本地独有)
        remote_only, local_only = _divergence(governance_root, upstream)
        operations.append(
            f"Divergence vs {upstream}: remote-only={remote_only} local-only={local_only} "
            f"(git rev-list --count HEAD..{upstream} / {upstream}..HEAD)"
        )

        if remote_only == 0:
            # 远端未前进 → 直接 fast-forward push; 不 rebase、不做脏树检查、不碰工作树
            _git_run(["push", "origin", f"HEAD:refs/heads/{current_branch}"], governance_root, timeout=30)
            pushed_commit_hash = commit_hash
            push_mode = "fast-forward"
            operations.append(
                f"Pushed to remote (fast-forward: {upstream} had not advanced; no rebase, no dirty-tree check, "
                f"shared checkout untouched)"
            )
        else:
            # 件②: 远端已前进 → 临时 linked worktree 上 cherry-pick + push
            operations.append(
                f"Remote has advanced ({remote_only} commit(s) on {upstream} not in local); integrating in a "
                f"temporary linked worktree outside the shared checkout (no stash / no in-place rebase)"
            )
            commits = _local_commits_not_upstream(governance_root, upstream)
            if commit_hash not in commits:
                return _push_failed(
                    base_factory(commit_hash),
                    operations,
                    reason=f"commit {commit_hash[:8]} not found among local-only commits vs {upstream}",
                    message=(
                        f"已提交 {commit_hash[:8]}, 但 git rev-list --right-only --cherry-pick {upstream}...HEAD "
                        f"未列出它(现场在提交与 push 之间被改动?), 拒绝整合(fail-closed)。\n"
                        f"可执行出口: cd {governance_root} && git log --oneline {upstream}...HEAD"
                    ),
                    verdict=Verdict.FAIL,
                )
            if len(commits) > 1:
                operations.append(
                    f"Local branch also carries {len(commits) - 1} earlier unpushed commit(s) not on {upstream}; "
                    f"they are integrated together in order (a fast-forward push would carry them too)"
                )
            outcome = integrate_in_temp_worktree(
                governance_root, current_branch, commits, actor, operations
            )
            if not outcome["ok"]:
                conflict_files = outcome["conflict_files"]
                listing = "\n  - ".join(conflict_files) if conflict_files else "(无 unmerged 文件; 见 cherry-pick 输出)"
                base = base_factory(commit_hash)
                base["conflict_files"] = conflict_files
                base["temp_worktree_cleaned"] = outcome["cleaned"]
                return _push_failed(
                    base,
                    operations,
                    reason=outcome["reason"],
                    message=(
                        f"已提交 {commit_hash[:8]}(本地保留), 但与 {upstream} 整合冲突, 未 push(fail-closed)。\n\n"
                        f"冲突文件({len(conflict_files)}):\n  - {listing}\n\n"
                        f"临时 worktree 已清理: {outcome['cleaned']}; 共享检出/索引/他项目未提交文件未动。\n\n"
                        f"可执行出口(先落库冲突文件; 产品尚无 --resolve 出口, 以下为同款临时 worktree 手法, 不碰共享检出):\n"
                        f"1. 看远端版本: cd {governance_root} && git diff HEAD {upstream} -- <冲突文件>\n"
                        f"2. 在仓外临时 worktree 解决并推: git worktree add --detach /tmp/gc-resolve {upstream} && cd /tmp/gc-resolve && "
                        f"git cherry-pick {commit_hash[:8]} → 手工合并冲突文件 → git add -- <冲突文件> && git cherry-pick --continue && "
                        f"git push origin HEAD:refs/heads/{current_branch}\n"
                        f"3. 回治理根跟进并清理: cd {governance_root} && git fetch origin && git reset --keep {upstream} && "
                        f"git worktree remove /tmp/gc-resolve\n"
                        f"4. 禁 stash/禁整根 add -A/禁 pull --rebase(F69 铁律); 不要在共享检出里改冲突文件后重跑(原 commit 会再次冲突)"
                    ),
                    verdict=Verdict.BLOCK,
                )
            pushed_commit_hash = outcome["pushed_tip"]
            push_mode = f"cherry-picked in temp worktree, {len(commits)} commit(s) rewritten"
            operations.append(
                f"Pushed to remote from temp worktree ({commit_hash[:8]} -> {pushed_commit_hash[:8]}; "
                f"temp worktree cleaned={outcome['cleaned']})"
            )

            # 成功后把本地分支 fast-forward 到远端: fetch → 若本地所有提交都已(按 patch)在远端 → 移动分支.
            # 因 cherry-pick 改写了哈希, 字面 `git merge --ff-only` 不可能成立(本地有原 commit 对象);
            # 语义等价物 = `git reset --keep origin/<branch>`: 只更新「远端提交改动过」的文件, 遇到本地
            # 有改动的同名文件即整体中止(fail-closed); 他项目未提交文件与未跟踪文件全程不动。
            _git_run(["fetch", "origin"], governance_root, timeout=30)
            remaining = _local_commits_not_upstream(governance_root, upstream)
            if remaining:
                operations.append(
                    f"Local branch NOT moved: {len(remaining)} local commit(s) still not on {upstream} "
                    f"({', '.join(c[:8] for c in remaining)}); reported only, nothing touched"
                )
            else:
                overlap = _dirty_overlap_with(governance_root, upstream)
                if overlap:
                    operations.append(
                        f"Local branch NOT moved: {len(overlap)} locally modified/untracked file(s) would be "
                        f"overwritten by {upstream}: {', '.join(overlap[:10])}"
                        + (f" (+{len(overlap) - 10} more)" if len(overlap) > 10 else "")
                        + f"; reported only. Exit: save them, then cd {governance_root} && git reset --keep {upstream}"
                    )
                else:
                    try:
                        _git_run(["reset", "--keep", upstream], governance_root, timeout=60)
                    except subprocess.CalledProcessError as exc:
                        operations.append(
                            f"WARNING: Local branch NOT moved: git reset --keep {upstream} refused: "
                            f"{(exc.stderr or '').strip()[:300]}; nothing overwritten, reported only"
                        )
                    else:
                        commit_hash = _git_readonly(["rev-parse", "HEAD"], governance_root).strip()
                        operations.append(
                            f"Local branch fast-forwarded to {upstream} ({commit_hash[:8]}) via git reset --keep "
                            f"(only files changed by remote commits updated; other projects' uncommitted files untouched)"
                        )

        # AIPOS-F69 大项③: push 后校验远端确实包含本次 commit(禁「push 返回 0 即报成功」)
        remote_branches = _git_readonly(
            ["branch", "-r", "--contains", pushed_commit_hash], governance_root
        ).strip()
        if not remote_branches:
            return _push_failed(
                base_factory(commit_hash),
                operations,
                reason=f"push returned 0 but {pushed_commit_hash[:8]} not found in any remote branch",
                message=(
                    f"Push 命令返回成功,但远端不包含 commit {pushed_commit_hash[:8]}\n\n"
                    f"可执行出口:\n"
                    f"1. 手动检查: git branch -r --contains {pushed_commit_hash[:8]}\n"
                    f"2. 重试: cd {governance_root} && lybra governance-commit ...(本次 commit 保留)"
                ),
                verdict=Verdict.FAIL,
            )
        operations.append(
            f"Verified commit {pushed_commit_hash[:8]} exists in remote: {remote_branches.split()[0]}"
        )
        # 件③: --json operations 末条 = push 结果
        operations.append(f"Push result: pushed {pushed_commit_hash[:8]} to {upstream} ({push_mode})")
        return {"ok": True, "pushed_commit_hash": pushed_commit_hash, "push_mode": push_mode, "commit_hash": commit_hash}

    except subprocess.CalledProcessError as e:
        return _push_failed(
            base_factory(commit_hash),
            operations,
            reason=f"git {' '.join(e.cmd[3:5]) if isinstance(e.cmd, list) and len(e.cmd) > 4 else 'command'} failed: {(e.stderr or '').strip()[:300]}",
            message=f"Committed {commit_hash[:8]} but push failed: {e.stderr}",
            verdict=Verdict.FAIL,
        )
    except subprocess.TimeoutExpired as e:
        return _push_failed(
            base_factory(commit_hash),
            operations,
            reason=f"git command timed out after {e.timeout}s",
            message=f"Committed {commit_hash[:8]} but push step timed out: {e.cmd}",
            verdict=Verdict.FAIL,
        )



def _unpushed_scope_commits(governance_root: Path, selected_paths: list[str] | None) -> list[str]:
    """本地相对上次 fetch 的 origin/<分支> 未推、且触及 selected_paths 的提交(新→旧); 无范围 / 无上游 / detached = []。只读不联网。"""
    if not selected_paths:
        return []
    try:
        branch = _git_readonly(["rev-parse", "--abbrev-ref", "HEAD"], governance_root).strip()
        if branch == "HEAD":
            return []
        raw = _git_readonly(["rev-list", f"origin/{branch}..HEAD", "--"] + list(selected_paths), governance_root)
    except subprocess.CalledProcessError:
        return []
    return [line for line in raw.split("\n") if line.strip()]


def _retry_unpushed_push(governance_root: Path, task_id: str | None, actor: str, selected_paths: list[str] | None,
                         operations: list[str], *, dry_run: bool) -> dict[str, Any] | None:
    """AIPOS-F184 件①: 无新改动但范围内有本地未推提交 → 经 _push_local_head 补推送(无新提交, 幂等)。
    不触及范围 = None(调用方维持原拒)。补推送成功 = PASS(committed=False, pushed=True); 失败 = 封口结果原样(带重试命令)。"""
    mine = _unpushed_scope_commits(governance_root, selected_paths)
    if not mine:
        return None
    head = _git_readonly(["rev-parse", "HEAD"], governance_root).strip()
    operations.append(f"Retry push: {len(mine)} unpushed local commit(s) touch this scope ({', '.join(c[:8] for c in mine)}); "
                      f"no new commit, pushing local HEAD {head[:8]} with the same push implementation")

    def base(h: str) -> dict[str, Any]:
        return {"verdict": Verdict.FAIL, "task_id": task_id, "actor": actor, "dry_run": dry_run, "completeness_check": None,
                "committed": False, "pushed": False, "commit_hash": h, "message": "", "operations": operations,
                "selected_paths": selected_paths, "commit_manifest": None, "unpushed_scope_commits": mine,
                "unpushed_local_commits": _unpushed_local_count(governance_root)}

    outcome = _push_local_head(governance_root, head, actor, operations, base)
    if not outcome["ok"]:
        return outcome["result"]
    result = base(outcome["commit_hash"])
    result.update({"verdict": Verdict.PASS, "pushed": True, "retried_push": True,
                   "message": f"无新改动; 补推送本地未推提交 {len(mine)} 条(范围内)(pushed {str(outcome['pushed_commit_hash'])[:8]})"})
    return result


def governance_commit(
    governance_root: Path,
    task_id: str | None,
    actor: str,
    *,
    repo_root: Path | None = None,
    dry_run: bool = False,
    push: bool = True,
    message: str | None = None,
    paths: list[str] | None = None,
    paths_file: Path | str | None = None,
) -> dict[str, Any]:
    """N6 收账提交:校验四件 → commit → push(唯一提交口; AIPOS-F79C 件③ 总闸在此封口)。

    AIPOS-F94 件①: 给 task_id 而不给 --paths/--paths-file = task 范围精确提交——白名单由 resolve_task_scope 按卡与声明推导
    (本卡与其审计卡的队列文件 / 草稿 / 各类记录 / 台账落点 + 卡编年史), 之后与 --paths 走同一条实现(他人暂存在范围外 = 拒,
    护栏 / 清单核对 / push 判据不变), 禁整根。范围已落账(task_landing: 已提交且已推送)= PASS no-op(幂等, 不碰 git 写)。
    """
    task_scope: dict[str, Any] | None = None
    if task_id and not paths and paths_file is None:
        early = _task_scope_selection(governance_root, task_id, actor, dry_run=dry_run, push=push)
        if "result" in early:
            return _seal_push_outcome(early["result"], push_requested=bool(push) and not dry_run)
        task_scope = early["task_scope"]
        paths = list(task_scope["paths"])
    guardrail_sink: dict[str, Any] = {}
    skipped: list[str] = []
    exclusion: dict[str, Any] = {}
    result = _governance_commit_impl(
        governance_root, task_id, actor,
        repo_root=repo_root, dry_run=dry_run, push=push, message=message, paths=paths, paths_file=paths_file,
        guardrail_sink=guardrail_sink, skipped_sink=skipped, exclusion_sink=exclusion,
    )
    # AIPOS-F195 件②: 产物槽代码文件排除——结果字段 + 消息首行尾出声(预演 = 将排除; 已提交 = 已排除, 清单已入库)
    result["excluded_code_files"] = [{k: i[k] for k in ("path", "sha256", "size", "manifest", "recorded")}
                                     for i in exclusion.get("excluded") or []]
    result["code_files_excluded_manifests"] = list(exclusion.get("manifests") or [])
    if exclusion.get("excluded") and (dry_run or result.get("committed")):
        head, _sep, rest = str(result.get("message") or "").partition("\n")
        result["message"] = f"{head}; {exclusion['line']}" + (f"\n{rest}" if rest else "")
    # AIPOS-F193 件①: 从未入库且已不存在的路径已从暂存与提交剔除——结果字段 + 消息尾行出声(不报错)
    result["skipped_vanished_untracked"] = sorted(skipped)
    if skipped:
        result["message"] = (str(result.get("message") or "") + "\n"
                             + VANISHED_UNTRACKED_LINE.format(count=len(skipped), paths=", ".join(sorted(skipped)))).lstrip("\n")
    # AIPOS-F128 件③: 四检放行时 --json 同步带报告(含 b4_exemptions 计数与逐文件依据); 拒时 BLOCK 结果已带
    if guardrail_sink.get("report") is not None:
        result.setdefault("guardrail_report", guardrail_sink["report"])
    if task_scope is not None:
        result["task_scope"] = task_scope
        ops = result.setdefault("operations", [])
        ops.insert(0, _task_scope_operation(Path(governance_root), task_id, task_scope))
    return _attach_push_hint(_seal_push_outcome(result, push_requested=bool(push) and not dry_run), Path(governance_root),
                             task_id, actor, task_scoped=task_scope is not None)


def retry_command(governance_root: Path, task_id: str | None, actor: str, selected_paths: list[str] | None, *,
                  task_scoped: bool) -> str:
    """推送未完成时的重试命令(同一提交口): 卡范围 = N6 落账命令; --paths = 精确路径命令; 整根 = 治理根 + 身份。"""
    if task_scoped and task_id:
        return governance_commit_command(task_id, actor, governance_root)
    if selected_paths:
        return governance_commit_paths_command(list(selected_paths), actor, governance_root)
    import shlex

    return (f"{governance_commit_cli()} --governance-root {shlex.quote(str(governance_root))} --actor {shlex.quote(str(actor))}"
            + (f" --task-id {shlex.quote(str(task_id))}" if task_id else ""))


def _attach_push_hint(result: dict[str, Any], governance_root: Path, task_id: str | None, actor: str, *,
                      task_scoped: bool) -> dict[str, Any]:
    """AIPOS-F184 件①(gap #164): 已在本地提交(或补推送)而未推上 = 人话提示 + 重试命令, 置于 message 首行(CLI / 写后落账 /
    补落账行同读首行), 并出 push_hint / retry_command 字段。重跑同一命令: 无新改动时只补推送(_retry_unpushed_push), 不产生新提交。"""
    if not result.get("push_requested") or result.get("pushed"):
        return result
    if not (result.get("committed") or result.get("unpushed_scope_commits")):
        return result
    command = retry_command(governance_root, task_id, actor, result.get("selected_paths"), task_scoped=task_scoped)
    raw_reason = (result.get("push_result") or {}).get("reason") or str(result.get("verdict"))
    reason = " | ".join(ln.strip() for ln in str(raw_reason).splitlines() if ln.strip())  # 首行一行说完(拒因多行 stderr 折成一行)
    sha = str(result.get("commit_hash") or "")[:8]
    hint = (f"推送未完成: 本地已有提交 {sha}, 但还没推到远端(原因: {reason})。本地提交已保留, 不要重复执行写命令、不要手工 reset;"
            f" 按原因处理(网络 / 远端权限 / 远端冲突)后重跑: {command}(无新改动时只补推送, 不再产生新提交)")
    result["push_hint"] = hint
    result["retry_command"] = command
    result["message"] = hint + ("\n" + str(result.get("message") or "") if result.get("message") else "")
    return result


def _task_scope_operation(governance_root: Path, task_id: str, task_scope: dict[str, Any]) -> str:
    ignored = task_scope.get("ignored") or []
    return (f"Task scope ({TASK_SCOPE_SOURCE}, AIPOS-F94): {len(task_scope['paths'])} path(s) derived for {task_id} "
            f"(+{'+'.join(audit_card_ids(governance_root, task_id))}" + (f", chronicle {task_scope['chronicle']}" if task_scope.get("chronicle") else "")
            + ")" + (f"; ignored by .gitignore (not committed): {', '.join(ignored)}" if ignored else "")
            + (f"; trailing (committed, not in landing criterion): {', '.join(task_scope['trailing'])}" if task_scope.get("trailing") else "")
            + (f"; ⚠️ {len(task_scope['unattributed_deployments'])} deployment record(s) unreadable, not attributable to any card "
               f"(not in scope; commit with --paths after review): "
               + "; ".join(task_scope["unattributed_deployments"][:3])
               + (" …" if len(task_scope["unattributed_deployments"]) > 3 else "")
               if task_scope.get("unattributed_deployments") else ""))


def _task_scope_selection(governance_root: Path, task_id: str, actor: str, *, dry_run: bool, push: bool) -> dict[str, Any]:
    """件①: 推导 task 范围白名单。返回 {"task_scope": …}(继续走 --paths 同一实现)或 {"result": …}(范围不可推导 = BLOCK /
    已落账 = PASS no-op)。不吞异常: git 只读失败与卡多义一律变成带拒因的 BLOCK。"""
    from tools.aipos_cli.task_loader import AmbiguousTaskCard

    def stop(verdict: str, message: str, operations: list[str], **extra: Any) -> dict[str, Any]:
        return {"result": {"verdict": verdict, "task_id": task_id, "actor": actor, "dry_run": dry_run,
                           "completeness_check": None, "committed": False, "pushed": False, "commit_hash": None,
                           "message": message, "operations": operations, "selected_paths": None, "commit_manifest": None,
                           **extra}}

    root = Path(governance_root)
    if not root.is_dir():
        return stop(Verdict.BLOCK, f"Governance root does not exist: {root}", [])
    try:
        scope = resolve_task_scope(root, task_id)
    except AmbiguousTaskCard as exc:
        return stop(Verdict.BLOCK, f"卡范围不可推导: {exc}(同一 task_id 只能有一份卡文件)", ["Blocked: task scope underivable"])
    except ValueError as exc:
        # AIPOS-F147 件①: 范围内门记录读不出(loop 运行记录等)= 范围不可推导, BLOCK 点名文件(不跳过)
        return stop(Verdict.BLOCK, f"卡 {task_id} 落账范围不可推导: {exc}", ["Blocked: task scope underivable (record unreadable)"])
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        detail = (getattr(exc, "stderr", "") or str(exc)).strip()
        return stop(Verdict.BLOCK,
                    f"卡 {task_id} 落账范围推导失败(治理根须在 git 工作树内): {detail}\n"
                    f"可执行出口: {non_git_exit_command(root)}(治理根不在 git 仓时; Owner 按其输出配 origin 并首推), 之后重跑同一命令",
                    ["Blocked: task scope underivable (git read failed)"])
    if not scope["paths"]:
        return stop(Verdict.BLOCK,
                    f"卡 {task_id} 落账范围为空: 队列中无此卡文件、无记录、无台账"
                    + (f"(被 .gitignore 排除: {', '.join(scope['ignored'])})" if scope["ignored"] else "")
                    + "。核对卡号; 非卡改动用 --paths 精确提交",
                    ["Blocked: task scope empty"], task_scope=scope)
    if push and not dry_run:
        landing = governance_landing(root, {task_id: scope["candidates"]})[task_id]
        if landing["landed"]:
            return stop(Verdict.PASS, f"卡 {task_id} 已落账(范围内无未提交 / 未推送, 上游 {landing['upstream']}), 无待收内容",
                        [_task_scope_operation(root, task_id, scope), "No changes to commit (task scope already committed and pushed)"],
                        severity="info", task_scope=scope, landing=landing)
    return {"task_scope": scope}


def _run_guardrails_on_manifest(
    governance_root: Path,
    manifest: dict[str, Any],
    repo_root: Path | None,
    operations: list[str],
    sink: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """AIPOS-F79D 件①: 对清单跑 governance_guardrails(B①–B⑤), 与 pre-commit hook 同模块同文案。

    通过 → None(operations 记一行); 拒/声明不可用 → 返回 BLOCK/FAIL 结果骨架(调用方补上下文字段)。
    读工作树文件内容判 B②/B④, 与 hook 一致(hook 提交的也是工作树状态: add 后 commit)。
    """
    from tools.aipos_cli.governance_guardrails import (
        GuardrailDeclarationError,
        format_exemptions,
        git_current_branch,
        git_toplevel,
        manifest_entries,
        resolve_schema_dir,
        run_guardrails,
    )

    try:
        git_root = git_toplevel(governance_root)
        report, decls, text = run_guardrails(
            git_root,
            manifest_entries(manifest),
            schema_dir=resolve_schema_dir(repo_root),
            current_branch=git_current_branch(git_root),
        )
    except (GuardrailDeclarationError, ValueError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        operations.append(f"Guardrails (governance_guardrails, same module as pre-commit hook): FAIL — {exc}")
        return {
            "verdict": Verdict.FAIL,
            "message": f"治理仓四检模块不可用, 拒绝放行(fail-closed): {exc}",
            "operations": operations,
            "guardrail_report": None,
        }
    if sink is not None:
        sink["report"] = report.to_dict()
    if report.ok:
        operations.append(
            f"Guardrails (governance_guardrails, same module as pre-commit hook): PASS — {report.checked} file(s) checked"
        )
        # AIPOS-F128 件③: B④ 豁免计数与依据出声(与 hook 同一 format_exemptions 文案)
        operations.extend(format_exemptions(report, decls))
        return None
    operations.append(
        f"Guardrails (governance_guardrails, same module as pre-commit hook): BLOCK — "
        f"{len(report.violations)} violation(s)" + ("" if report.branch_ok else f", branch={report.branch} != main")
    )
    return {
        "verdict": Verdict.BLOCK,
        "message": text,
        "operations": operations,
        "guardrail_report": report.to_dict(),
        "rejected_files": report.to_dict()["rejected_files"],
    }


def _governance_commit_impl(
    governance_root: Path,
    task_id: str | None,
    actor: str,
    *,
    repo_root: Path | None = None,
    dry_run: bool = False,
    push: bool = True,
    message: str | None = None,
    paths: list[str] | None = None,
    paths_file: Path | str | None = None,
    guardrail_sink: dict[str, Any] | None = None,
    skipped_sink: list[str] | None = None,
    exclusion_sink: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """N6 收账提交:校验四件 → commit → push。

    AIPOS-F69 大项①: task_id 可选 — 无卡时走治理批次语义(台账追加/裁定入档/契约修正),
    仍走同一校验链与同一 pre-commit 四检,仅跳过本卡台账条目检查。
    
    Args:
        governance_root: 治理仓根目录
        task_id: 任务 ID (可选;无卡时走治理批次语义)
        actor: 执行者
        repo_root: 产品仓根 (用于定位 schema/config.schema.json)
        dry_run: 只校验不提交
        push: 是否 push 到远程
        message: commit message (默认自动生成)
        paths: AIPOS-F79 件① 显式白名单(治理根相对路径, 文件或目录); 给了即只 git add -- <paths>
        paths_file: 同上, 每行一路径的清单文件(与 paths 二选一)
    
    Returns:
        {
            "verdict": "PASS" | "BLOCK" | "FAIL",
            "task_id": str | None,
            "actor": str,
            "dry_run": bool,
            "completeness_check": dict,
            "committed": bool,
            "pushed": bool,
            "commit_hash": str | None,
            "message": str,
            "operations": list[str],
            "selected_paths": list[str] | None,   # F79: 白名单(无 --paths 为 None)
            "commit_manifest": dict | None,       # F79: 将提交/已提交文件清单(见 collect_commit_manifest)
        }
    """
    operations = []
    
    # AIPOS-F79 件①: 先归一白名单, 非法即整体拒绝(fail-closed), 不碰 git
    path_selection = resolve_commit_paths(governance_root, paths, paths_file)
    if path_selection["rejected"]:
        return {
            "verdict": Verdict.BLOCK,
            "task_id": task_id,
            "actor": actor,
            "dry_run": dry_run,
            "completeness_check": None,
            "committed": False,
            "pushed": False,
            "commit_hash": None,
            "message": path_selection["message"],
            "operations": operations + ["Blocked: --paths whitelist rejected"],
            "selected_paths": None,
            "rejected_paths": path_selection["rejected"],
            "commit_manifest": None,
        }
    selected_paths: list[str] | None = path_selection["paths"] if path_selection["selected"] else None
    if selected_paths is not None:
        operations.append(f"Path whitelist ({path_selection['source']}): {len(selected_paths)} path(s) — git add限定, 绝不 add -A")
    
    # 校验治理仓目录
    if not governance_root.is_dir():
        return {
            "verdict": Verdict.BLOCK,
            "task_id": task_id,
            "actor": actor,
            "dry_run": dry_run,
            "completeness_check": None,
            "committed": False,
            "pushed": False,
            "commit_hash": None,
            "message": f"Governance root does not exist: {governance_root}",
            "operations": operations,
        }
    
    operations.append(f"Governance root: {governance_root}")
    
    # AIPOS-F7 大项B①: 先查 git status — 无待收内容 → info "无待收内容, 治理仓已最新" + EXIT=0
    # (F4 no-op 档)。放在完整性校验之前:仓已干净则无需校验,避免无变更场景误触 BLOCK。
    # AIPOS-F79: 有 --paths 时 status 限定到白名单 pathspec(选定路径外的变更与本次无关)
    add_skip: set[str] = set()
    try:
        # AIPOS-F193 件①: 白名单中「工作树不存在且 git 从未跟踪」的条目 = 无可落内容, 从暂存与提交 pathspec 剔除并出声(不报错);
        # 剔除后为空 = no-op PASS。已 git rm 的删除(HEAD 有、索引无)不再 git add(删除已在索引), 照常作删除提交。
        # 唯一落账实现在此一处剔除(N6 / 写后落账 / --paths 手工补账同口径受益), 禁各调用方各自过滤。
        if selected_paths:
            missing = classify_missing_paths(governance_root, selected_paths)
            vanished = missing["vanished_untracked"]
            add_skip = set(missing["staged_deleted"])
            if vanished:
                selected_paths = [p for p in selected_paths if p not in set(vanished)]
                operations.append(VANISHED_UNTRACKED_LINE.format(count=len(vanished), paths=", ".join(vanished)))
                if skipped_sink is not None:
                    skipped_sink.extend(vanished)
                if not selected_paths:
                    operations.append("No changes to commit (all --paths entries never tracked and gone)")
                    return {
                        "verdict": Verdict.PASS,
                        "task_id": task_id,
                        "actor": actor,
                        "dry_run": dry_run,
                        "completeness_check": None,
                        "committed": False,
                        "pushed": False,
                        "commit_hash": None,
                        "message": "无待收内容, 选定路径均为从未入库且已不存在的路径",
                        "severity": "info",
                        "operations": operations,
                        "selected_paths": [],
                        "commit_manifest": None,
                    }
        status_result = subprocess.run(
            ["git", "-c", "core.quotepath=false", "status", "--porcelain", "--untracked-files=all", "-z", "--"]
            + (selected_paths if selected_paths else []),
            cwd=str(governance_root),
            check=True,
            capture_output=True,
            text=True,
        )
        # AIPOS-F195 件②: 已记入同槽清单且 sha256 未变的产物槽代码文件(N6 已排除、原文件留盘)不算待收内容
        changed = _porcelain_entries(status_result.stdout)
        ws_prefix = _git_ws_prefix(governance_root) if changed else ""
        untracked = [_strip_ws_prefix(ws_prefix, path) for xy, path in changed if xy == "??"]
        try:
            settled = recorded_slot_code_files(governance_root, untracked, repo_root=repo_root) if untracked else set()
        except (ValueError, OSError, RuntimeError) as exc:  # RuntimeError ⊇ GuardrailDeclarationError(声明不可用)
            operations.append(f"Slot code files: FAIL — {exc}")
            return {
                "verdict": Verdict.FAIL, "task_id": task_id, "actor": actor, "dry_run": dry_run, "completeness_check": None,
                "committed": False, "pushed": False, "commit_hash": None,
                "message": f"产物槽代码文件处置不可用, 拒绝放行(fail-closed): {exc}",
                "operations": operations, "selected_paths": selected_paths, "commit_manifest": None,
            }
        has_changes = any(not (xy == "??" and _strip_ws_prefix(ws_prefix, path) in settled) for xy, path in changed)
        if settled:
            operations.append(f"Slot code files already excluded and recorded (not pending): {len(settled)}")
        
        if not has_changes:
            operations.append("No changes to commit" + (" (within --paths)" if selected_paths else ""))
            # AIPOS-F79C 件③: 「治理仓已最新」不能在本地还有未推提交时说出口(chris 8 条未推提交静默的病根)
            unpushed = _unpushed_local_count(governance_root) if (push and not dry_run) else None
            if unpushed:
                operations.append(f"Unpushed local commits vs last-fetched origin: {unpushed}")
                # AIPOS-F184 件①: 本地未推提交触及本次范围(上次落账已提交、推送未完成)= 重跑同一命令即补推送(同一推送实现),
                # 不产生新提交; 未触及范围(他人未推提交)维持原拒, 不替人推送。
                retry = _retry_unpushed_push(governance_root, task_id, actor, selected_paths, operations, dry_run=dry_run)
                if retry is not None:
                    return retry
                return {
                    "verdict": Verdict.BLOCK,
                    "task_id": task_id,
                    "actor": actor,
                    "dry_run": dry_run,
                    "completeness_check": None,
                    "committed": False,
                    "pushed": False,
                    "commit_hash": None,
                    "message": (
                        f"无待收内容, 但本地有 {unpushed} 条提交尚未推到远端(治理仓并未「已最新」)。\n"
                        f"可执行出口: cd {governance_root} && git fetch origin && git log --oneline @{{u}}..HEAD; "
                        f"确认后 git push(远端未前进时为 fast-forward)"
                    ),
                    "operations": operations,
                    "selected_paths": selected_paths,
                    "commit_manifest": None,
                    "unpushed_local_commits": unpushed,
                    "push_result": {"pushed": False, "reason": f"nothing new committed; {unpushed} local commit(s) not on origin"},
                }
            return {
                "verdict": Verdict.PASS,
                "task_id": task_id,
                "actor": actor,
                "dry_run": dry_run,
                "completeness_check": None,
                "committed": False,
                "pushed": False,
                "commit_hash": None,
                "message": "无待收内容, 治理仓已最新" + ("(选定 --paths 内无变更)" if selected_paths else ""),
                "severity": "info",
                "operations": operations,
                "selected_paths": selected_paths,
                "commit_manifest": None,
            }
    except subprocess.CalledProcessError as e:
        return {
            "verdict": Verdict.FAIL,
            "task_id": task_id,
            "actor": actor,
            "dry_run": dry_run,
            "completeness_check": None,
            "committed": False,
            "pushed": False,
            "commit_hash": None,
            "message": f"Git status check failed: {e.stderr}\n可执行出口(治理根不在 git 仓时): {non_git_exit_command(governance_root)}"
                       f"(Owner 按其输出配 origin 并首推)后重跑同一命令",
            "operations": operations,
        }
    
    # ① 校验 N6 收账清单四件齐全(仅在有变更时校验)
    completeness = check_governance_completeness(governance_root, task_id, repo_root)
    operations.append(f"Completeness check: {'PASS' if completeness['complete'] else 'FAIL'}")
    
    if not completeness["complete"]:
        missing_items = "\n  - ".join(completeness["missing"])
        return {
            "verdict": Verdict.BLOCK,
            "task_id": task_id,
            "actor": actor,
            "dry_run": dry_run,
            "completeness_check": completeness,
            "committed": False,
            "pushed": False,
            "commit_hash": None,
            "message": f"N6 收账清单不完整,缺少:\n  - {missing_items}",
            "operations": operations,
        }
    
    # P0 修复(AIPOS-F69-R2): commit 前检查是否有范围外 staged 文件
    # 实锤(2026-09-05): 顾问治理仓有门刚落的账(staged 但未 commit),
    # governance-commit 会把这些也一起 commit,然后 rebase 可能导致问题
    # AIPOS-F79 件③: 检查提到 dry-run 之前(dry-run 必须如实预演 BLOCK), 语义改为:
    #   无 --paths: 任何预暂存 → BLOCK(原样);
    #   有 --paths: 预暂存在 --paths 内 = 纳入清单可接受; 在 --paths 外 = 拒并列出。
    # AIPOS-F79C 件④: 预暂存/暂存集一律 --no-renames, 与清单(diff --no-renames)及 git show --no-renames 同口径
    pre_staged_inside: set[str] = set()
    try:
        pre_staged_result = subprocess.run(
            ["git", "-c", "core.quotepath=false", "diff", "--cached", "--name-only", "--no-renames"],
            cwd=str(governance_root),
            check=True,
            capture_output=True,
            text=True,
        )
        pre_staged_files = [f.strip() for f in pre_staged_result.stdout.split('\n') if f.strip()]

        if pre_staged_files and selected_paths:
            inside_result = subprocess.run(
                ["git", "-c", "core.quotepath=false", "diff", "--cached", "--name-only", "--no-renames", "--"] + selected_paths,
                cwd=str(governance_root),
                check=True,
                capture_output=True,
                text=True,
            )
            inside = {f.strip() for f in inside_result.stdout.split('\n') if f.strip()}
            outside = [f for f in pre_staged_files if f not in inside]
            if inside:
                operations.append(f"Pre-staged within --paths: {len(inside)} file(s) accepted into manifest")
            pre_staged_inside = inside
            pre_staged_files = outside
        
        if pre_staged_files:
            # 有 staged 文件 → BLOCK(这些可能是门刚落的账)
            operations.append(f"Blocked: {len(pre_staged_files)} pre-staged files detected" + (" outside --paths" if selected_paths else ""))
            file_list = '\n  - '.join(pre_staged_files[:10])
            if len(pre_staged_files) > 10:
                file_list += f'\n  - ... and {len(pre_staged_files) - 10} more'
            return {
                "verdict": Verdict.BLOCK,
                "task_id": task_id,
                "actor": actor,
                "dry_run": dry_run,
                "completeness_check": None,
                "committed": False,
                "pushed": False,
                "commit_hash": None,
                "message": (
                    f"治理仓有已 staged 但未 committed 的文件"
                    + ("(在 --paths 白名单之外)" if selected_paths else "")
                    + f",必须先处理(fail-closed,禁混入本次提交)。\n\n"
                    f"检测到 {len(pre_staged_files)} 个 staged 文件:\n  - {file_list}\n\n"
                    f"可执行出口:\n"
                    f"1. 先提交这些文件: cd {governance_root} && git commit -m '...'\n"
                    f"2. 或取消 stage: cd {governance_root} && git reset HEAD -- <文件>\n"
                    + ("3. 或把它们加入 --paths 白名单(若确属本批次)\n" if selected_paths else "")
                    + f"{'4' if selected_paths else '3'}. 禁止混入本次提交 — 这些可能是门刚落的账,必须独立提交以保持原子性"
                ),
                "operations": operations,
                "selected_paths": selected_paths,
                "pre_staged_outside": pre_staged_files,
                "commit_manifest": None,
            }
        
        # AIPOS-F79 件②: 只读取得将提交文件清单(不 add/不 reset/不 stash)
        manifest = collect_commit_manifest(governance_root, selected_paths)
        # AIPOS-F195 件②: 产物槽内新增代码文件不入库(同槽清单记 sha256 并一并落账, 原文件留盘); 槽外代码文件照旧交四检 B① 拒
        try:
            exclusion = _apply_slot_code_exclusion(governance_root, manifest, selected_paths, repo_root, actor, dry_run=dry_run)
        except (ValueError, OSError, RuntimeError) as exc:  # RuntimeError ⊇ GuardrailDeclarationError(声明不可用)
            operations.append(f"Slot code files: FAIL — {exc}")
            return {
                "verdict": Verdict.FAIL, "task_id": task_id, "actor": actor, "dry_run": dry_run, "completeness_check": completeness,
                "committed": False, "pushed": False, "commit_hash": None,
                "message": f"产物槽代码文件处置不可用, 拒绝放行(fail-closed): {exc}",
                "operations": operations, "selected_paths": selected_paths, "commit_manifest": manifest,
            }
        manifest, selected_paths = exclusion["manifest"], exclusion["selected_paths"]
        exclude_specs = list(exclusion["exclude_specs"])
        if exclusion["excluded"]:
            operations.append(
                f"Slot code files excluded (not committed, kept on disk): {len(exclusion['excluded'])} — "
                + ", ".join(i["path"] for i in exclusion["excluded"])
                + f"; manifest(s): {', '.join(exclusion['manifests'])}" + (" (dry-run: not written)" if dry_run else "")
            )
            if exclusion_sink is not None:
                exclusion_sink.update(exclusion)
        for w in manifest["warnings"]:
            operations.append(w)
        operations.append(
            f"Manifest ({manifest['mode']}): {manifest['counts']['total']} file(s) — "
            f"modified={manifest['counts'][MANIFEST_MODIFIED]} added={manifest['counts'][MANIFEST_ADDED]} "
            f"deleted={manifest['counts'][MANIFEST_DELETED]} untracked_selected={manifest['counts'][MANIFEST_UNTRACKED]}"
        )
        
        if not manifest["files"]:
            operations.append("No net changes in manifest (nothing would be committed)")
            return {
                "verdict": Verdict.PASS,
                "task_id": task_id,
                "actor": actor,
                "dry_run": dry_run,
                "completeness_check": completeness,
                "committed": False,
                "pushed": False,
                "commit_hash": None,
                "message": "无待收内容, 清单为空(选定路径相对 HEAD 无净变更)",
                "severity": "info",
                "operations": operations,
                "selected_paths": selected_paths,
                "commit_manifest": manifest,
            }
    except subprocess.CalledProcessError as e:
        operations.append(f"Git read-only inspection failed: {e.stderr}")
        return {
            "verdict": Verdict.FAIL,
            "task_id": task_id,
            "actor": actor,
            "dry_run": dry_run,
            "completeness_check": completeness,
            "committed": False,
            "pushed": False,
            "commit_hash": None,
            "message": f"Git inspection failed (status/diff): {e.stderr}",
            "operations": operations,
            "selected_paths": selected_paths,
            "commit_manifest": None,
        }
    except subprocess.TimeoutExpired:
        operations.append("Git read-only inspection timed out")
        return {
            "verdict": Verdict.FAIL,
            "task_id": task_id,
            "actor": actor,
            "dry_run": dry_run,
            "completeness_check": completeness,
            "committed": False,
            "pushed": False,
            "commit_hash": None,
            "message": "Git inspection timed out (status/diff)",
            "operations": operations,
            "selected_paths": selected_paths,
            "commit_manifest": None,
        }
    
    # AIPOS-F79D 件①: dry-run 与正式提交前对同一「将提交清单」跑同一四检模块(hook 也调它), 拒因文案同源。
    # 病根: 2026-09-22 chris dry-run PASS 而正式提交被 hook exit 1 —— 预演从不跑 B①–B④。
    guardrail_outcome = _run_guardrails_on_manifest(governance_root, manifest, repo_root, operations, guardrail_sink)
    if guardrail_outcome is not None:
        guardrail_outcome.update({
            "task_id": task_id,
            "actor": actor,
            "dry_run": dry_run,
            "completeness_check": completeness,
            "committed": False,
            "pushed": False,
            "commit_hash": None,
            "selected_paths": selected_paths,
            "commit_manifest": manifest,
        })
        return guardrail_outcome

    if dry_run:
        operations.append("DRY-RUN: Would commit and push the manifest above (scene untouched: no add/reset/stash)")
        listing = "\n".join(f"  {f['status']:<18} {f['path']}" for f in manifest["files"])
        return {
            "verdict": Verdict.PASS,
            "task_id": task_id,
            "actor": actor,
            "dry_run": True,
            "completeness_check": completeness,
            "committed": False,
            "pushed": False,
            "commit_hash": None,
            "message": (
                ("DRY-RUN: N6 收账清单完整,可以提交" if task_id else "DRY-RUN: 治理批次更新检查通过,可以提交")
                + f"\n将提交 {manifest['counts']['total']} 个文件"
                + (f"(--paths 白名单)" if selected_paths else "(整根 add -A 范围)")
                + f":\n{listing}"
                + ("".join(f"\n{w}" for w in manifest["warnings"]) if manifest["warnings"] else "")
            ),
            "operations": operations,
            "selected_paths": selected_paths,
            "commit_manifest": manifest,
        }
    
    # ③ Commit (会触发 pre-commit 四检)
    if task_id:
        commit_msg = message or f"chore(governance): N6 收账 {task_id}\n\nActor: {actor}\nType: governance_commit"
    else:
        commit_msg = message or f"chore(governance): 治理批次更新\n\nActor: {actor}\nType: governance_commit"

    def _result_base(*, committed: bool, commit_hash: str | None) -> dict[str, Any]:
        return {
            "verdict": Verdict.FAIL,
            "task_id": task_id,
            "actor": actor,
            "dry_run": False,
            "completeness_check": completeness,
            "committed": committed,
            "pushed": False,
            "commit_hash": commit_hash,
            "message": "",
            "operations": operations,
            "selected_paths": selected_paths,
            "commit_manifest": manifest,
        }

    stage_pathspec = (list(selected_paths) if selected_paths else ["."]) + exclude_specs  # AIPOS-F195: 槽内代码文件排除

    def _unstage_this_run(saved_index_tree: str | None) -> None:
        """AIPOS-F79C 件④: 拒提交时清掉本次暂存(git reset -- <pathspec>, 范围仍限 --paths), 不留半成品;
        --paths 内原有的预暂存条目从暂存前的索引快照精确还原(不动工作树)。"""
        _git_run(["reset", "-q", "--"] + stage_pathspec, governance_root)
        operations.append(f"Unstaged this run's staging (git reset -- {' '.join(stage_pathspec)})")
        if pre_staged_inside and saved_index_tree:
            # 预暂存路径来自 diff --cached(仓根相对), 而 cwd 是治理根 → 用 :/ 顶层 pathspec 魔法
            _git_run(
                ["restore", "--staged", f"--source={saved_index_tree}", "--"]
                + [f":/{p}" for p in sorted(pre_staged_inside)],
                governance_root,
            )
            operations.append(f"Restored {len(pre_staged_inside)} pre-staged entr(y/ies) within --paths from index snapshot")

    try:
        # 件④: 暂存前对索引拍快照(write-tree), 仅用于 mismatch 时精确还原 --paths 内的预暂存条目
        saved_index_tree = _git_readonly(["write-tree"], governance_root).strip() if pre_staged_inside else None
        if selected_paths:
            # AIPOS-F79 件①: 只 stage 白名单 pathspec, 绝不整根 add -A
            # AIPOS-F79C 件④: -A 限定在 <paths> 内 — 目录 pathspec 内的删除也必须暂存(这不是整根 add -A)
            # AIPOS-F193 件①: 已 git rm 的删除(索引与工作树均无)不再 add(pathspec 不匹配), 删除已在索引, 由 --only 照常提交
            add_paths = [p for p in selected_paths if p not in add_skip]
            if add_paths:
                subprocess.run(
                    ["git", "add", "-A", "--"] + add_paths + exclude_specs,
                    cwd=str(governance_root),
                    check=True,
                    capture_output=True,
                    text=True,
                )
            operations.append(f"Staged whitelist only (git add -A -- {' '.join(add_paths)} in {governance_root}; deletions within these paths included)"
                              + (f"; already-staged deletions not re-added: {', '.join(sorted(add_skip))}" if add_skip else ""))
        else:
            # AIPOS-R8B 大项A: Stage all changes in governance repo with pathspec限定到 governance_root
            # 防止 git add -A 越界 stage 其他项目(如 kiwiaiagency)的文件
            # AIPOS-F79: 整根模式仅限 lybra 自身工作区; 他项目一律 --paths
            subprocess.run(
                ["git", "add", "-A", "--", "."] + exclude_specs,
                cwd=str(governance_root),
                check=True,
                capture_output=True,
                text=True,
            )
            operations.append(f"Staged all governance changes (git add -A -- . in {governance_root})")
        
        # AIPOS-R8B 大项A②: 断言 staged 文件全部落在 governance_root 内,越界即 BLOCK
        staged_files_result = subprocess.run(
            ["git", "-c", "core.quotepath=false", "diff", "--cached", "--name-only", "--no-renames"],
            cwd=str(governance_root),
            check=True,
            capture_output=True,
            text=True,
        )
        staged_files = [f.strip() for f in staged_files_result.stdout.split('\n') if f.strip()]

        # 检查 staged 文件是否全部在 governance_root 内(相对路径不应以 ../ 开头)
        out_of_scope = []
        for staged_file in staged_files:
            # 相对路径以 ../ 开头或包含 ../ 说明越界
            if staged_file.startswith('../') or '/../' in staged_file:
                out_of_scope.append(staged_file)

        if out_of_scope:
            operations.append(f"SCOPE VIOLATION: {len(out_of_scope)} staged files outside governance_root")
            _unstage_this_run(saved_index_tree)
            out_of_scope_list = '\n  - '.join(out_of_scope[:10])  # 最多列10个
            if len(out_of_scope) > 10:
                out_of_scope_list += f'\n  - ... and {len(out_of_scope) - 10} more'
            return {
                "verdict": Verdict.BLOCK,
                "task_id": task_id,
                "actor": actor,
                "dry_run": False,
                "completeness_check": completeness,
                "committed": False,
                "pushed": False,
                "commit_hash": None,
                "message": f"SCOPE VIOLATION: Staged files outside governance root (G3 铁律):\n  - {out_of_scope_list}",
                "operations": operations,
                "index_restored": True,
            }
        
        # AIPOS-F79 件③: 暂存集必须与清单逐条相同, 否则拒提交并出声
        # AIPOS-F79C 件④: 同口径 --no-renames; mismatch 时清掉本次暂存(出声记录, 非静默)再拒, 不留半成品
        # AIPOS-F184 件①: 核对限本次 pathspec(提交是 --only 清单, 范围外他人此刻暂存的文件不进提交, 也不拦本卡落账;
        # 预暂存于范围外的既有拒仍在上方 pre-staged 检查)
        staged_set = set(_git_readonly(["diff", "--cached", "--name-only", "--no-renames", "--"] + stage_pathspec,
                                       governance_root).split("\n")) - {""}
        manifest_set = set(_manifest_repo_paths(manifest))
        if staged_set != manifest_set:
            extra = sorted(staged_set - manifest_set)
            missing_from_stage = sorted(manifest_set - staged_set)
            operations.append(f"MANIFEST MISMATCH before commit: +{len(extra)} unexpected staged, -{len(missing_from_stage)} missing")
            _unstage_this_run(saved_index_tree)
            return {
                "verdict": Verdict.BLOCK,
                "task_id": task_id,
                "actor": actor,
                "dry_run": False,
                "completeness_check": completeness,
                "committed": False,
                "pushed": False,
                "commit_hash": None,
                "message": (
                    "暂存集与 dry-run 清单不一致, 拒绝提交(现场在检查与暂存之间被改动?)。本次暂存已清掉(git reset -- <paths>), 暂存区不留半成品。\n"
                    + (f"多出: {extra}\n" if extra else "")
                    + (f"缺少: {missing_from_stage}\n" if missing_from_stage else "")
                    + f"可执行出口: cd {governance_root} && git status; 然后重跑 --dry-run 核对清单后再提交"
                ),
                "operations": operations,
                "selected_paths": selected_paths,
                "commit_manifest": manifest,
                "staged_files": sorted(staged_set),
                "index_restored": True,
            }
        
        # Commit with explicit identity
        # AIPOS-F79D 件③: commit 子进程非零退出(pre-commit hook 拒)也走 _unstage_this_run —— 还原预暂存快照,
        # 范围仍限 --paths; 结果带 index_restored=true 与被拒清单。病根: 24 条留 index 反过来挡他项目提交。
        # AIPOS-F184 件①(gap #164): 只提交清单内的文件——`git commit --only -- <清单逐个文件>`(顶层字面 pathspec), 不提交整个索引。
        # 病根: 10-16 F173 loop 结束补落账在「范围外预暂存检查」与 commit 之间, 顾问并发 `git add governance/LEDGER.md`,
        # 旧的无 pathspec commit 把整个索引(含 LEDGER)提交进「N6 收账 AIPOS-F173」, 提交后核对 FAIL → 未推送, ahead 1。
        # --only: 只取这些路径的工作树内容建临时索引提交(钩子看到的暂存集 = 清单), 他人此刻暂存的文件原样留在索引里。
        only_pathspec = [f":(top,literal){p}" for p in sorted(manifest_set)]
        try:
            subprocess.run(
                [
                    "git",
                    "-c", f"user.name={actor}",
                    "-c", f"user.email={actor}@lybra.local",
                    "commit",
                    "-m", commit_msg,
                    "--only", "--",
                    *only_pathspec,
                ],
                cwd=str(governance_root),
                check=True,
                capture_output=True,
                text=True,
                env=_commit_hook_env(),
            )
        except subprocess.CalledProcessError as exc:
            hook_output = ((exc.stdout or "") + (exc.stderr or "")).strip()
            operations.append(f"Commit rejected by git/pre-commit hook (exit {exc.returncode})")
            _unstage_this_run(saved_index_tree)
            rejected_files = sorted(manifest_set)
            result = _result_base(committed=False, commit_hash=None)
            result["verdict"] = Verdict.BLOCK
            result["message"] = (
                f"治理仓 pre-commit 拒绝提交(exit {exc.returncode}); 本次暂存已清掉(git reset -- {' '.join(stage_pathspec)}), "
                f"预暂存条目已按快照还原, 暂存区不留半成品。\n\n"
                f"被拒清单({len(rejected_files)}):\n  - " + "\n  - ".join(rejected_files) + "\n\n"
                f"hook 输出:\n{hook_output}\n\n"
                f"可执行出口: 按 hook 输出修正文件后重跑同一命令(先 --dry-run: 同一四检模块, 拒因逐字相同)"
            )
            result["index_restored"] = True
            result["rejected_files"] = rejected_files
            result["hook_output"] = hook_output
            return result
        
        # Get commit hash
        commit_hash_result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(governance_root),
            check=True,
            capture_output=True,
            text=True,
        )
        commit_hash = commit_hash_result.stdout.strip()
        operations.append(f"Committed: {commit_hash[:8]}")
        
        # AIPOS-F79 件③: 提交后 git show --name-only HEAD 与清单逐条相同, 不同即 FAIL 出声
        shown_set = set(_git_readonly(["show", "--name-only", "--no-renames", "--format=", "HEAD"], governance_root).split("\n")) - {""}
        if shown_set != manifest_set:
            operations.append("POST-COMMIT VERIFICATION FAILED: git show --name-only HEAD != manifest")
            return {
                "verdict": Verdict.FAIL,
                "task_id": task_id,
                "actor": actor,
                "dry_run": False,
                "completeness_check": completeness,
                "committed": True,
                "pushed": False,
                "commit_hash": commit_hash,
                "message": (
                    f"提交 {commit_hash[:8]} 的文件集与清单不一致, 未 push(fail-closed)。\n"
                    f"提交内: {sorted(shown_set)}\n清单: {sorted(manifest_set)}\n"
                    f"可执行出口: cd {governance_root} && git show --name-only HEAD; 核对后决定是否 git reset --soft HEAD~1"
                ),
                "operations": operations,
                "selected_paths": selected_paths,
                "commit_manifest": manifest,
                "committed_files": sorted(shown_set),
            }
        operations.append(f"Verified: git show --name-only HEAD == manifest ({len(shown_set)} files)")
        
        # ④ Push (N6 语义「漏 push 即未收口」)
        # AIPOS-F79C 件①: fetch 后双向 rev-list 判分歧 — 远端独有=0 → 直接 fast-forward push,
        #   不 rebase、不做脏树检查(F69-R2 的越界脏树检查只对「真需整合」有意义, 且它把共享仓
        #   里他项目的未提交修改也算进去 → 恒 BLOCK = 2026-09-21 chris 阻塞的病根之一;
        #   另一病根: 旧判据 merge-base(local, remote) != local 在本地刚提交后恒真 → 永远走 rebase)。
        # AIPOS-F79C 件②: 远端独有>0 → 在仓外临时 linked worktree 上 cherry-pick 本地独有提交并
        #   在该 worktree push; 共享检出的工作树/索引/他项目未提交文件全程不动(禁 stash/禁整根
        #   add -A/禁 pull --rebase); 冲突 → 清理临时 worktree, 精确列出冲突文件, BLOCK, 本地 commit 保留。
        # AIPOS-F79C 件③: 任何 pushed=False 都出声 — 由 governance_commit() 的 _seal_push_outcome 统一
        #   保证 verdict != PASS 且 operations 末条 = "PUSH NOT DONE: <原因>"。
        pushed = False
        pushed_commit_hash: str | None = None
        if push:
            outcome_push = _push_local_head(governance_root, commit_hash, actor, operations,
                                            lambda h: _result_base(committed=True, commit_hash=h))
            if not outcome_push["ok"]:
                return outcome_push["result"]
            pushed = True
            pushed_commit_hash = outcome_push["pushed_commit_hash"]
            commit_hash = outcome_push["commit_hash"]

        return {
            "verdict": Verdict.PASS,
            "task_id": task_id,
            "actor": actor,
            "dry_run": False,
            "completeness_check": completeness,
            "committed": True,
            "pushed": pushed,
            "commit_hash": commit_hash,
            "message": (
                f"N6 收账完成: {commit_hash[:8]}" if task_id
                else f"治理批次更新完成: {commit_hash[:8]}"
            ) + (" (pushed)" if pushed else ""),
            "operations": operations,
            "selected_paths": selected_paths,
            "commit_manifest": manifest,
        }
        
    except subprocess.CalledProcessError as e:
        operations.append(f"Git operation failed: {e.stderr}")
        return {
            "verdict": Verdict.FAIL,
            "task_id": task_id,
            "actor": actor,
            "dry_run": False,
            "completeness_check": completeness,
            "committed": False,
            "pushed": False,
            "commit_hash": None,
            "message": f"Commit failed: {e.stderr}",
            "operations": operations,
            "selected_paths": selected_paths,
            "commit_manifest": manifest,
        }
    except subprocess.TimeoutExpired as e:
        operations.append(f"Git operation timed out: {e.cmd}")
        return {
            "verdict": Verdict.FAIL,
            "task_id": task_id,
            "actor": actor,
            "dry_run": False,
            "completeness_check": completeness,
            "committed": False,
            "pushed": False,
            "commit_hash": None,
            "message": f"Git operation timed out: {e.cmd}",
            "operations": operations,
            "selected_paths": selected_paths,
            "commit_manifest": manifest,
        }


# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation
check_direct_invocation(__name__)
