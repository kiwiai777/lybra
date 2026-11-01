"""AIPOS-F269 — 看板写例外④「新建项目」(产品化 P3 第二张): Owner 会话在接入页预演 → 确认执行 `lybra project new`(+ 可选产品仓声明)。

一句话: 装好 Lybra 的 Owner 不开终端也能建项目——接入页填项目名(和可选产品仓), 先看将建什么, 确认后建成并落账, 接入清单即列出下一步。

防碎片化(卡面硬约束), 本模块只给 F172 同一安全框架的三个回调 + 声明读取 + 下一步命令, 不另写任何一步的实现:
- 安全判定 = envelope_board.handle_owner_write(Owner 会话 / Origin / JSON / stage / 一次性预演令牌六类 / 看板审计日志), workspace 解析方式
  new = envelope_board.resolve_new_workspace(待建项目名; 名字不合 / 已存在在此拒)。
- 建项目 = workspace_config.scaffold_project(`lybra project new` 唯一实现); 产品仓 = workspace_config.set_project_repos(`lybra project
  set-repos` 唯一实现, 校验 = 其读取口 project_repos)。预演 = 同一组实现在一次性临时 home 根里演练(得出将建目录树与 project.json 全文),
  真 home 根零写入。
- 写出清单 = onboarding.project_new_products(向导第 1 步同一推导); 落账 = board_adapter.land_after_write(写后落账唯一薄壳,
  声明 post_write_landing.commands.<board.landing>); 治理仓事实 = onboarding.governance_repo_status; 非 git 出口 =
  governance_commit.non_git_exit_command; 产品仓 git 事实 = home_git.git_repo_ancestor。
- 下一步 = 接入清单 board_pages.onboarding_rows 同一推导(看板接入页渲染时取首个未完成项, 本模块不另算); 给顾问的命令 =
  board_pages 接入命令声明(onboarding_commands)+ card_policy_loader.set_task_id_pattern_command(project new CLI 同一渲染)。
任何 home 根 / 任何项目名同一组声明, 不写死项目名、主机或路径。界面文字在看板 i18n 单通道(web/board/app.py, 前缀 board.text_prefix)。
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

VERB = "lybra_project_new"
#: 声明 verbs.lybra_project_new.board 必有的键
_BOARD_KEYS = ("page", "write_path", "script_path", "action", "landing", "created_param", "text_prefix", "errors")


def board_declaration() -> dict[str, Any]:
    """verbs.schema verbs.lybra_project_new.board(fail-closed): 键齐; 路径以 / 起首; page ∈ 看板只读页; landing ∈ 写后落账命令;
    errors 每码有 int status 与非空 message。"""
    from tools.aipos_cli.board_pages import PAGES
    from tools.aipos_cli.governance_commit import post_write_landing_declaration
    from tools.schema_loader import SchemaLoadError, code_repo_schema_root, load_schema

    decl = ((load_schema("verbs", code_repo_schema_root()).get("verbs") or {}).get(VERB) or {}).get("board")
    where = f"verbs.schema.json verbs.{VERB}.board"
    if not isinstance(decl, dict) or any(k not in decl for k in _BOARD_KEYS):
        raise SchemaLoadError(f"{where} 未声明齐 {list(_BOARD_KEYS)}")
    if (any(not str(decl[k] or "").startswith("/") for k in ("write_path", "script_path")) or decl["page"] not in PAGES
            or not all(isinstance(decl[k], str) and decl[k] for k in ("action", "created_param", "text_prefix"))):
        raise SchemaLoadError(f"{where}: write_path / script_path 须以 / 起首, page ∈ {list(PAGES)}, action / created_param / text_prefix 非空")
    if decl["landing"] not in post_write_landing_declaration()["commands"]:
        raise SchemaLoadError(f"{where}.landing={decl['landing']!r} 未登记在 verbs.lybra_governance_commit.post_write_landing.commands")
    for code, spec in (decl["errors"] or {}).items():
        if not isinstance(spec, dict) or not isinstance(spec.get("status"), int) or not str(spec.get("message") or "").strip():
            raise SchemaLoadError(f"{where}.errors.{code} 须有 int status 与非空 message")
    return decl


def created_url(project: str) -> str:
    """建成后的落点: 接入页 ?<created_param>=<项目名>#<项目名>(页面路径读 board_readonly_pages.pages.<board.page>)。"""
    from urllib.parse import quote, urlencode

    from tools.aipos_cli.board_pages import pages_declaration

    decl = board_declaration()
    path = str(pages_declaration()["pages"][decl["page"]])
    return f"{path}?{urlencode({decl['created_param']: project})}#{quote(project)}"


def next_commands(project: str, governance_root: str | Path, home_root: str | Path) -> list[str]:
    """建成后给顾问的命令(折叠在页面「给顾问的命令」里): 接入向导(onboarding_commands.advisor, 接入清单同一声明)、确认看板 / 门发现了
    本项目(project list, 门只扫描 home 根)、卡号形状声明(project new CLI 同一渲染)。"""
    import shlex

    from tools.aipos_cli.board_pages import _command, pages_declaration
    from tools.card_policy_loader import set_task_id_pattern_command

    decl = pages_declaration()
    guide = _command(decl, "advisor", project=shlex.quote(str(project)), home_root=str(home_root), governance_root=str(governance_root))
    return [f"{guide} --home-root {shlex.quote(str(home_root))}",
            f"lybra project list --home-root {shlex.quote(str(home_root))}",
            set_task_id_pattern_command(governance_root)]


def _tree(root: Path) -> list[str]:
    """root 下的目录与文件(相对路径, 目录以 / 结尾, 排序)。"""
    return sorted(p.relative_to(root).as_posix() + ("/" if p.is_dir() else "") for p in root.rglob("*"))


def _repo_facts(code_repo: str | None) -> dict[str, Any] | None:
    from tools.aipos_cli.home_git import git_repo_ancestor

    if not code_repo:
        return None
    path = Path(code_repo)
    exists = path.is_dir()
    git_root = git_repo_ancestor(path) if exists else None
    return {"path": code_repo, "exists": exists, "git_root": str(git_root) if git_root else None,
            "is_git_root": bool(git_root and git_root == path.resolve())}


def _scaffold(home: Path, project: str, *, actor: str, code_repo: str | None, repo_name: str) -> Path:
    """`lybra project new`(+ 有产品仓时 `lybra project set-repos --confirm`)的同一实现序列; 预演(临时 home 根)与确认(真 home 根)同调。"""
    from tools.aipos_cli.workspace_config import scaffold_project, set_project_repos

    root = scaffold_project(home, project, registered_by=actor)  # CLI 非交互同参: collaboration_profile 缺省 None
    if code_repo:
        set_project_repos(home, project, {repo_name: code_repo}, default=repo_name, dry_run=False)
    return root


class RehearsalRefused(ValueError):
    """预演演练被同一实现拒(kind = repo: 产品仓声明不合 CardRepoUnresolved / name: 建项目拒因); 文中临时 home 根已换回真 home 根。"""

    def __init__(self, kind: str, text: str) -> None:
        super().__init__(text)
        self.kind = kind


def _rehearse(project: str, *, home: Path, actor: str, code_repo: str | None, repo_name: str) -> dict[str, Any]:
    """预演 = 同一实现在一次性临时 home 根里演练: 将建目录树(治理根相对)+ project.json 全文 + 将落账的写出清单(治理根相对)。
    名字 / 产品仓声明不合 = 同一实现的拒因 → RehearsalRefused(拒因原文, 临时 home 根路径换回真 home 根 home)。"""
    from tools.aipos_cli.onboarding import project_new_products
    from tools.aipos_cli.workspace_config import CardRepoUnresolved, project_json_path

    with tempfile.TemporaryDirectory(prefix="lybra-project-new-rehearsal-") as tmp:
        try:
            root = _scaffold(Path(tmp), project, actor=actor, code_repo=code_repo, repo_name=repo_name)
        except (ValueError, FileExistsError) as exc:  # CardRepoUnresolved ⊂ ValueError
            kind = "repo" if isinstance(exc, CardRepoUnresolved) else "name"
            raise RehearsalRefused(kind, str(exc).replace(str(Path(tmp).resolve()), str(home)).replace(tmp, str(home))) from exc
        return {"tree": _tree(root), "project_json": project_json_path(root).read_text(encoding="utf-8"),
                "products": project_new_products(root)}


def handle_project_new_request(
    body: dict[str, Any],
    *,
    session: dict[str, Any] | None,
    home_root: str | Path | None,
    preview_store: Any,
    audit_log_path: Path | None,
    origin: str | None = None,
    host: str | None = None,
    content_type: str | None = None,
) -> tuple[int, dict[str, Any]]:
    """写例外④(唯一写路由): 安全判定全在 envelope_board.handle_owner_write(workspace 解析方式 new); 本函数只给三个回调。
    请求体: workspace = 项目名, code_repo = 可选产品仓绝对路径, stage, preview_token。参数摘要 = {action, project, home_root, governance_root,
    code_repo, repo_name, actor}: 预演后改项目名 / 产品仓 = 确认拒(PREVIEW_PARAMS_MISMATCH)。actor = 会话 Owner 的 captured_by(F172 同一模板,
    来源 = 看板), 即 project.json registered_by 与落账提交身份。"""
    from tools.aipos_cli.envelope_board import MintRequestError, board_envelopes_declaration, handle_owner_write, session_provenance

    board = board_declaration()
    frame = board_envelopes_declaration()

    def prepare(body: dict[str, Any], project: str, root: Path, now: Any, session: dict[str, Any]) -> tuple[dict, dict, dict]:
        from tools.aipos_cli.onboarding import governance_repo_status

        raw_repo = body.get("code_repo")
        if raw_repo is not None and not isinstance(raw_repo, str):
            raise MintRequestError("PARAMS_INVALID", detail="code_repo 须为字符串(产品仓绝对路径)或留空")
        code_repo = str(raw_repo or "").strip() or None
        provenance = session_provenance(frame, session)
        actor = provenance["captured_by"]
        params = {"action": str(board["action"]), "project": project, "home_root": str(root.parent), "governance_root": str(root),
                  "code_repo": code_repo, "repo_name": project, "actor": actor}
        try:
            rehearsal = _rehearse(project, home=root.parent, actor=actor, code_repo=code_repo, repo_name=project)
        except RehearsalRefused as exc:  # 产品仓 = set-repos 同一校验拒因; 名字 = scaffold_project 拒因(PROJECT_NAME_EMPTY 等)原样
            raise MintRequestError("CODE_REPO_INVALID" if exc.kind == "repo" else "PARAMS_INVALID", detail=str(exc)) from exc
        repo = _repo_facts(code_repo)
        if repo is not None and not repo["exists"]:
            raise MintRequestError("CODE_REPO_NOT_FOUND", code_repo=code_repo)
        audit_fields = {"project": project, "code_repo": code_repo, "actor": actor, "capture_surface": provenance["capture_surface"]}
        context = {"rehearsal": rehearsal, "repo": repo, "governance_repo": governance_repo_status(root.parent), "provenance": provenance}
        return params, audit_fields, context

    def preview(params: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        from tools.aipos_cli.governance_commit import non_git_exit_command

        gov_repo = context["governance_repo"]
        plan = {**context["rehearsal"], "repo": context["repo"], "governance_repo": gov_repo, "registered_by": params["actor"],
                "capture_surface": context["provenance"]["capture_surface"],
                "non_git_exit": None if gov_repo["git"] else non_git_exit_command(params["governance_root"])}
        return {"params": params, "plan": plan}

    def confirm(params: dict[str, Any], context: dict[str, Any]) -> tuple[dict, dict, Any]:
        from tools.aipos_cli.board_adapter import land_after_write, landing_view
        from tools.aipos_cli.onboarding import project_new_products
        from tools.aipos_cli.workspace_config import project_json_path

        actor, project, home = params["actor"], params["project"], Path(params["home_root"])
        fields = {"actor": actor, "capture_surface": context["provenance"]["capture_surface"]}
        try:
            root = _scaffold(home, project, actor=actor, code_repo=params["code_repo"], repo_name=params["repo_name"])
        except (OSError, ValueError, RuntimeError) as exc:  # 预演后目录被占 / 写盘失败 / 产品仓声明写后复核不过: 拒因原文, 不猜
            detail = f"{type(exc).__name__}: {exc}"
            built = project_json_path(params["governance_root"]).is_file()  # 已建出 project.json(如产品仓声明一步失败)= 如实点名
            return {**fields, "outcome": "project_new_failed", "error": detail, "project_json_written": built}, {}, MintRequestError(
                "PROJECT_NEW_FAILED", detail=detail + (f"; project.json 已写出({params['governance_root']}), 核对后由顾问处理" if built else ""))
        landing = land_after_write(root, project_new_products(root), actor=actor, command=str(board["landing"]), project=project)
        created = {"governance_root": str(root), "tree": _tree(root), "project_json": project_json_path(root).read_text(encoding="utf-8")}
        result = {**fields, "outcome": "created", "governance_root": str(root), "landed": bool(landing and landing.get("landed")),
                  "commit": (landing or {}).get("commit_hash")}
        response = {"ok": True, "stage": "confirm", "params": params, "actor": actor, "captured_by": actor,
                    "capture_surface": fields["capture_surface"], "created": created, "landing": landing_view(landing),
                    "landing_line": (landing or {}).get("line"), "redirect": created_url(project),
                    "next_commands": next_commands(project, root, home)}
        return result, response, None

    return handle_owner_write(body, decl=board, action=str(board["action"]), session=session, home_root=home_root,
                              preview_store=preview_store, audit_log_path=audit_log_path, prepare=prepare, preview=preview,
                              confirm=confirm, origin=origin, host=host, content_type=content_type, workspace="new")


# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation  # noqa: E402

check_direct_invocation(__name__)
