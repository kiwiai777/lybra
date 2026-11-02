"""AIPOS-F57 / AIPOS-F92 / AIPOS-F262 — 从 0 接新项目全流程固化(onboarding guide generator)。

把"从项目注册到首卡结案"做成产品命令序列, 全程零手工编辑。AIPOS-F92 起按单门 home 根约定(AIPOS-226 §1.3)排序;
AIPOS-F262 起对齐现行流程(项目常设授权取代三信封、执行分工成步、按需声明成节、凭据库与工位目录去本机假设):
  ① 建项目(顾问): `lybra project new` 落在 resolve_home_root 解析出的 home 根下(打印结果与来源; 门只扫描 home 根),
     同时写首份阶段快照「项目创建」(首次 finalize 不被阶段门拦); 之后 `lybra governance-commit --paths <本步产物>` 落账(AIPOS-F94)
  ② 声明产品仓(顾问): `lybra project set-repos`(project.json repos/code_repo, 经声明校验); --confirm 写后即自动落账 project.json(AIPOS-F159)
  ③ Owner 一次性动作 ①: `lybra project grant sign`(AIPOS-F224 项目常设授权; 先预演再 --confirm)一份授权覆盖本项目全部车道 / 任务类型 /
     驱动方 / 主机 / 拉起方式 / 总卡数 + 到期; 已有授权或旧信封的项目 = 本步给出提示(grant show / 迁移预演), 旧信封面兼容不教学
  ④ Owner 一次性动作 ②: `lybra roles enroll-deliver`(AIPOS-F151: 先 --dry-run 预演再 --confirm)用中央凭据库的 Owner 凭据为新项目
     签码并把顾问接入治理根(得 advisor 凭据, 项目范围 = 新项目; 注册码只在进程内, 不打印不落盘不进 argv), 产品经 distribution 声明把
     顾问件(技能 .claude/skills + 章程; AIPOS-F136)交付到顾问会话目录
     (AIPOS-F129: --advisor-harness codex = 顾问是 Codex 会话(可在他机, --advisor-host): 接入写 --harness codex, 声明无 codex 件
     = 不生成技能分发步骤; 取值读 distribution.schema harness_semantics.kinds.<kind>.advisor_session)
  ⑤ 顾问就位复核(前置自检 + 顾问件稳态 / 他机会话开局取章程)
  ⑥ 接入审计工位(pi, 工位模式)  ⑦ 接入 pi 执行工位(pi, 工位模式)  ⑧ 接入子 agent 执行者(--executor-mode subagent, 凭据落治理根)
     ——执行分工(AIPOS-F143 / F256): 代码卡 = 子 agent 执行者, 非代码卡一律 pi 执行工位, 审计恒为独立 pi 审计工位; 每条 enroll 命令显式带
     --executor-mode, 不依赖缺省; 授权引用一律 = 第 ③ 步项目授权 ID
  ⑨ `lybra project set-execution`(三者齐)+ `roles list` 复核执行分工
  ⑩ 工位 sync 分发 + 稳态复核 + 工位自检
  ⑪ 首卡: 发卡 → `lybra loop` 推进到结案(代码卡: 顾问按 `lybra kickoff` 开工稿派子 agent; 非代码卡: loop 按授权在 pi 执行工位拉起 pi,
     未放行拉起 = Owner 在工位 /go; 结案后 loop 自动 N6 落账)
  附: 按需声明(card-policy / test-contract / release-target / pi 模型 / headline, 只列不强制)、接入既有人肉项目。
Owner 动作只有 ③④ 两条(每条在 guide 中以 owner_action 标出); 其余由新顾问执行。Owner 凭据(门的中央凭据库)只从门注册表的已声明来源推导
(见 resolve_owner_credential), 推不出 = 明说并请用户给出, 不按项目名猜; 工位目录读声明 project.json paths.workstations_root(缺省见 config.schema)。注册码不经手任何人(AIPOS-F151 gap #92: 向导不再示例
把注册码写进命令行的兑换写法): 签码与兑换在 enroll-deliver 同一进程内完成。顾问会话目录可与治理根不同:
所有命令显式带治理根(--workspace-root / --home-root / --governance-root), 不依赖 cwd。

每步失败都报错带路且给出可执行出口。禁任何项目名/路径硬编码(项目无关性)。

单源纪律:
  - home 根 = workspace_config.resolve_home_root_with_source(与 project new / 门同一优先级梯)
  - 实例名 = naming_profile.default_instance_name(roles.schema naming.template + 角色前缀)
  - 可启动最小集清单 = distribution.schema#minimum_bootable_set(单源)
  - harness 维度 = distribution.schema#harness_semantics(--harness 取值由声明推导)

AIPOS-F85 件①(F84 G1)不变量 = guide 产出的每条 `lybra ...` 命令占位换合法值后过 aipos_cli.build_parser(),
pi 内斜杠命令只许 distribution 声明中扩展所注册者(夹具 tests/test_aipos_f85_onboarding_guide_generic.py 守)。
"""
from __future__ import annotations

import json
import os
import shlex
import socket
import sys
from datetime import timedelta
from tools.aipos_cli.clock import utc_now
from pathlib import Path
from typing import Any

#: guide 中由上一步输出填入的占位(F85 夹具登记同一组; 新占位须同步登记)。AIPOS-F151: 注册码占位(<ADVISOR_CODE> 等)退役——
#: 向导以 enroll-deliver 签码 + 接入一体, 码不出进程, 不再有「上一步输出的码」要填
OWNER_WORKSPACE_PLACEHOLDER = "<OWNER_WORKSPACE>"
#: AIPOS-F262 件③: Owner 凭据推不出时的填空(向导头部与第 3 / 4 步明说原因并请用户给 --owner-connection-json, 不以推导值冒充)
OWNER_CONNECTION_PLACEHOLDER = "<OWNER_CONNECTION_JSON>"
REPO_NAME_PLACEHOLDER = "<REPO_NAME>"
REPO_PLACEHOLDER = "<REPO_NAME>=<REPO_ABS_PATH>"
#: AIPOS-F262 件④: 「按需声明」一节的填空(值由 Owner / 顾问按项目实况定, 产品无内置默认)
OWNER_REF_PLACEHOLDER = "<OWNER_REF>"
SECONDS_PLACEHOLDER = "<SECONDS>"
TARGET_ID_PLACEHOLDER = "<TARGET_ID>"
HOST_PLACEHOLDER = "<HOST>"
ALLOW_PLACEHOLDER = "<ALLOW>"
RELEASE_ROOT_PLACEHOLDER = "<RELEASE_ROOT>"
ENTRY_PLACEHOLDER = "<ENTRY>"
MODEL_PLACEHOLDER = "<PROVIDER/MODEL>"
TITLE_PLACEHOLDER = "<TITLE>"
NOW_PLACEHOLDER = "<NOW>"
DONE_PLACEHOLDER = "<DONE>"
#: AIPOS-F262: 步号唯一声明(guide 各步 step_number 与 `lybra onboarding check --step` 前置判定同读; 改步序只改这里)
STEP = {
    "preflight": 0,  # AIPOS-F263 件③: 前置检查(lybra setup check 摘要; 不占业务步号, `onboarding check --step 0`)
    "project": 1, "repos": 2, "grant": 3, "advisor": 4, "advisor_check": 5,
    "auditor": 6, "pi_executor": 7, "subagent_executor": 8, "execution": 9, "sync": 10, "first_card": 11,
}
#: AIPOS-F262 件③: 工位缺省目录的项目声明键(config.schema project_json.paths.workstations_root; 缺省模板读同处 default_user_home)
WORKSTATIONS_ROOT_KEY = "workstations_root"
#: Owner 凭据所持角色(roles.schema owner, project_scope=cross_project; 向导签授权 / 签顾问码都用它)
OWNER_ROLE = "owner"
DRAFT_JSON_PLACEHOLDER = "<CARD_DRAFT_JSON>"
DRAFT_PATH_PLACEHOLDER = "<DRAFT_PATH>"
TASK_ID_PLACEHOLDER = "<TASK_ID>"
#: AIPOS-F123 件③ 接入既有人肉项目一节的占位(由顾问按项目实况填)
LEGACY_BRANCH_PLACEHOLDER = "<LEGACY_BRANCH>"
PATHS_KEY_PLACEHOLDER = "<PATHS_KEY>"
PATHS_VALUE_PLACEHOLDER = "<PATHS_VALUE>"
META_TEXT_PLACEHOLDER = "<META_TEXT>"  # AIPOS-F127 件④: set-meta 的说明文本(phase / note)
FREEZE_REASON_PLACEHOLDER = "<FREEZE_REASON>"
#: 顾问会话 harness 缺省(distribution.schema harness_semantics.kinds 的键; 缺省 = 既有行为 Claude Code)。
#: AIPOS-F129 件③: 可选值 = distribution_sync.advisor_harness_kinds()(kinds.<kind>.advisor_session = true), 本常量只定缺省
ADVISOR_HARNESS = "claude-code"


# ---------------------------------------------------------------------------
# 引号工具
# ---------------------------------------------------------------------------

def _shell_quote(s: str) -> str:
    """Shell-quote a string for copy-paste commands."""
    return shlex.quote(s)


def _shell_path(s: str) -> str:
    """Shell-quote a path but keep a leading ``~/`` unquoted so the shell still expands it.

    AIPOS-F85: 缺省工位 ``~/<项目>-workstation`` 经 shlex.quote 会变成 ``'~/...'``(引号内 ``~`` 不展开 → cd 落到字面目录)。
    占位(``<...>``)原样保留, 由上一步输出替换。
    """
    if s.startswith("<") and s.endswith(">"):
        return s
    if s == "~":
        return s
    if s.startswith("~/"):
        rest = s[2:]
        return "~/" + shlex.quote(rest) if rest else "~/"
    return shlex.quote(s)


def _cmd(*parts: str) -> str:
    return " ".join(parts)


def _t(key: str, /, **values: Any) -> str:
    """AIPOS-F277: 向导文案 = config.schema owner_texts.onboarding_guide 双语块的一条 → 双语渲染值(i18n.Localized; str 本身 = 缺省语言,
    改前原文逐字不变); 生成结果经 i18n.materialize 按用户语言落成普通 str。"""
    from tools.aipos_cli.i18n import owner_text

    return owner_text("onboarding_guide", key, **values)


def _join(sep: Any, parts: list[Any]) -> str:
    from tools.aipos_cli.i18n import join

    return join(sep, parts)


def _session_dir_label(source: str, *, occupant: str | None = None) -> str:
    """顾问会话目录来源人读一句: 缺省语言 = enrollment.session_dir_source_label(唯一出处, 车道外); 其余语言 = 同一声明
    (distribution.schema harness_semantics.session_dir)的译文覆盖层 translations.<语言>.source_labels(i18n.overlay_localize)。"""
    from tools.aipos_cli.enrollment import session_dir_declaration, session_dir_source_label
    from tools.aipos_cli.i18n import overlay_localize, pick

    decl = session_dir_declaration()
    label = overlay_localize(decl, f"source_labels.{source}", decl["source_labels"][source], {"occupant": occupant or "-"},
                             where="distribution.schema.json harness_semantics.session_dir.source_labels")
    if pick(label) != session_dir_source_label(source, occupant=occupant):  # 缺省语言与唯一出处逐字一致(fail-closed)
        raise ValueError(f"session_dir.source_labels.{source} 渲染与 enrollment.session_dir_source_label 不一致")
    return label


# ---------------------------------------------------------------------------
# AIPOS-F93 件②: 注册码交付文案唯一渲染(发码 paste_text 与门 enroll_code 动词说明 / next_step、CLI enroll-code
# 打印同此; 原「会话斜杠 enroll 命令」随 lybra-loop 扩展退役, 禁再出现)。项目无关: 只拼产品命令形。
# ---------------------------------------------------------------------------

ENROLL_WORKSPACE_PLACEHOLDER = "<工位目录或治理根>"
ENROLL_DELIVERY_INSTRUCTION = (
    "把注册码交给接收方, 由其在任一 shell 执行下面这条(--workspace = 其工位目录; 顾问凭据落治理根; "
    "完整接入步骤见 lybra onboarding guide):"
)
ENROLL_NEXT_STEP = (
    "接收方执行 paste_text 那条 lybra roles enroll 命令(替换 --workspace 占位)→ 产品完成兑换/落盘/连通验证(--verify)"
)


def render_enroll_command(code: str, workspace: str = ENROLL_WORKSPACE_PLACEHOLDER, *extra: str) -> str:
    """注册码兑换命令的唯一渲染: `lybra roles enroll --code <码> --workspace <工位或治理根> [extra…] --verify`。
    workspace 由调用方按需 shell 引用(向导传已引用路径; 发码侧传占位, 由接收方替换)。"""
    return _cmd("lybra", "roles", "enroll", "--code", code, "--workspace", workspace, *extra, "--verify")


def render_enroll_deliver_command(roles_prefix: list[str] | tuple[str, ...], *, role: str, instance: str, governance_root: str,
                                  workspace: str, extra: list[str] | tuple[str, ...] = (), phase: str = "--confirm") -> str:
    """AIPOS-F151 件②: 签码 + 接入一体命令的唯一渲染(向导 Step 4 / 6 同用): `<roles_prefix> enroll-deliver --role … --instance …
    --governance-root <治理根> --workspace <工位或治理根> [extra…] <--dry-run | --confirm>`。参数由调用方按需 shell 引用。
    roles_prefix = `lybra roles [--workspace-root <门客户端凭据所在> --connection-json <…>]`。"""
    if phase not in ("--dry-run", "--confirm"):
        raise ValueError(f"phase 须为 --dry-run / --confirm(verbs.schema two_phase_protocol.roles_writers.flags), 得到 {phase!r}")
    return _cmd(*roles_prefix, "enroll-deliver", "--role", role, "--instance", instance, "--governance-root", governance_root,
                "--workspace", workspace, *extra, phase)


def enroll_delivery(code: str) -> dict[str, str]:
    """发码后的交付文案(门 lybra_enroll_code_confirm / lybra_roles_enroll_code 与 CLI roles enroll-code 共用一份):
    paste_text = 兑换命令(工位占位), paste_instruction = 说明 + 命令, next_step。"""
    paste_text = render_enroll_command(code)
    return {
        "paste_text": paste_text,
        "paste_instruction": f"{ENROLL_DELIVERY_INSTRUCTION}\n{paste_text}",
        "next_step": ENROLL_NEXT_STEP,
    }


# ---------------------------------------------------------------------------
# AIPOS-F94 件②: 向导第 1 步之后的治理落账(产物路径由向导按单源推导, 命令唯一渲染 governance_commit_paths_command);
# 第 2 步 set-repos --confirm 写后自动落账(AIPOS-F159), 其手跑落账行随 AIPOS-F160 件① 退役
# ---------------------------------------------------------------------------

def project_new_products(governance_root: Path) -> list[str]:
    """`lybra project new` 的治理产物(治理根相对): project.json + 治理文档(decision_log 桩) + 阶段快照目录。
    单源: workspace_config.project_json_path / governance_paths(scaffold_project 同读); 空目录骨架(队列 / 记录)无文件, 不入。"""
    from tools.aipos_cli.workspace_config import governance_paths, project_json_path, stage_archive_root

    root = Path(governance_root)
    paths = governance_paths(root)
    return [Path(p).relative_to(root).as_posix() for p in (project_json_path(root), paths["decision_log"], stage_archive_root(root))]


def governance_repo_status(home_root: Path) -> dict[str, Any]:
    """AIPOS-F94 N5: 治理根所在(home 根)是否已在 git 仓内——判据 = home_git.git_repo_ancestor(与 `lybra home git-init`
    拒绝嵌套同一实现; 向导生成时治理根尚未建, 故自 home 根向上找)。不在 = 向导第 1 步先给初始化出口。"""
    from tools.aipos_cli.home_git import git_repo_ancestor

    repo = git_repo_ancestor(home_root)
    return {"git": repo is not None, "repo_root": str(repo) if repo else None}


def render_landing_command(governance_root: Path, actor: str, paths: list[str]) -> str:
    from tools.aipos_cli.governance_commit import governance_commit_paths_command

    return governance_commit_paths_command(paths, actor, governance_root)


def landing_on_fail() -> dict[str, str]:
    """落账失败出口(各写治理步共用; 文案 owner_texts.onboarding_guide.landing, AIPOS-F277 双语)。"""
    return {
        "Git status check failed / not a git repository": _t("landing.git"),
        "PUSH NOT DONE": _t("landing.push"),
        _t("landing.staged_key"): _t("landing.staged"),
    }


# ---------------------------------------------------------------------------
# 推导(全部经既有单源)
# ---------------------------------------------------------------------------

def _workspace_of_connection(conn: Path) -> Path:
    """connection.json → 其所在工作区(`<工作区>/.lybra/connection.json` 的 <工作区>; 定位约定 service_mode.connection_path)。"""
    from tools.aipos_cli.service_mode import connection_path

    for ws in (conn.parent.parent, conn.parent):
        if connection_path(ws) == conn:
            return ws
    return conn.parent


def _holds_owner_credential(conn: Path) -> tuple[bool, str | None]:
    """该 connection.json 是否持 Owner 凭据条目: 判定 = 门自己的入表校验 load_service_role_registry(只看条目 role, 令牌值不读出不打印;
    门不认的残缺条目同样不算)。读不出 = (False, 原因)——原因写进推导说明, 不吞。"""
    import io

    from tools.mcp_server.http_sse import load_service_role_registry

    try:
        registry = load_service_role_registry(conn, error_stream=io.StringIO())
    except (OSError, ValueError) as exc:  # json.JSONDecodeError ⊂ ValueError
        return False, _t("cred.unreadable_item", path=conn, error=type(exc).__name__)
    return any(str(entry.get("role") or "") == OWNER_ROLE for entry in registry.values()), None


def resolve_owner_credential(home: Path, *, owner_workspace: str | None = None, owner_connection_json: str | None = None,
                             env: dict[str, str] | None = None) -> dict[str, Any]:
    """AIPOS-F262 件③: Owner 凭据(门的中央凭据库)位置的唯一推导——不按项目名猜(原「home 根 + 活动项目」梯在只有一个项目时回落到它,
    home 根里有名为 lybra 的项目就把别的项目的凭据库推成它)。

    次序: 显式 --owner-connection-json > 显式 --owner-workspace(其 .lybra/connection.json)> 门注册表的已声明来源(门 serve 读取的同一组:
    home 根自身 + home 根下各已建项目的 .lybra/connection.json, 见 mcp_server.http_sse.load_unified_service_role_registry)中**持 Owner 凭据
    条目**的那一份——多份时取已声明活动项目(环境变量 / ~/.lybra/config.json active_project; 不含「只有一个项目」的兜底)那份;
    零份或多份且无声明 = 推不出: path=None, reason 说明并请用户给 --owner-connection-json(向导不写占位冒充推导值)。
    返回 {path, workspace, source, resolved, reason, holders, unreadable}; 只读, 令牌值永不读出。"""
    from tools.aipos_cli.service_mode import connection_path
    from tools.aipos_cli.workspace_config import (ACTIVE_PROJECT_ENV, _project_candidates, global_config_active_project,
                                                  load_global_config)

    if str(owner_connection_json or "").strip():
        conn = Path(str(owner_connection_json)).expanduser()
        ws = Path(str(owner_workspace)).expanduser() if str(owner_workspace or "").strip() else _workspace_of_connection(conn)
        return {"path": str(conn), "workspace": str(ws), "source": _t("cred.source_explicit_json"), "resolved": True, "reason": None,
                "holders": [], "unreadable": []}
    if str(owner_workspace or "").strip():
        ws = Path(str(owner_workspace)).expanduser()
        return {"path": str(connection_path(ws)), "workspace": str(ws), "source": _t("cred.source_explicit_ws"), "resolved": True,
                "reason": None, "holders": [], "unreadable": []}
    home = Path(home)
    sources = [(_t("cred.label_home"), home)] + [(_t("cred.label_project", name=name), home / name) for name in _project_candidates(home)]
    holders: list[tuple[str, Path]] = []
    unreadable: list[str] = []
    for label, ws in sources:
        conn = connection_path(ws)
        if not conn.is_file():
            continue
        held, problem = _holds_owner_credential(conn)
        if problem:
            unreadable.append(problem)
        elif held:
            holders.append((label, ws))
    source_env = env if env is not None else os.environ
    declared = str(source_env.get(ACTIVE_PROJECT_ENV) or "").strip() or global_config_active_project(load_global_config(env))
    picked: tuple[str, Path] | None = None
    source = ""
    if declared and any(ws == home / declared for _l, ws in holders):
        picked = next(h for h in holders if h[1] == home / declared)
        source = _t("cred.source_active", holder=picked[0], declared=declared)
    elif len(holders) == 1:
        picked = holders[0]
        source = _t("cred.source_single", holder=picked[0])
    info = {"holders": [str(connection_path(ws)) for _l, ws in holders], "unreadable": unreadable}
    if picked is not None:
        return {"path": str(connection_path(picked[1])), "workspace": str(picked[1]), "source": source, "resolved": True,
                "reason": None, **info}
    if holders:
        why = _t("cred.why_many", home=home, count=len(holders), holders=", ".join(info["holders"]))
    else:
        why = _t("cred.why_none", home=home)
    if unreadable:
        why = _join("", [why, _t("cred.unreadable_part", items=_join("; ", unreadable))])
    return {"path": None, "workspace": None, "source": _t("cred.source_unresolved"), "resolved": False,
            "reason": _t("cred.reason", why=why, conn=OWNER_CONNECTION_PLACEHOLDER, ws=OWNER_WORKSPACE_PLACEHOLDER), **info}


def workstation_dirs(governance_root: Path, project: str, home_root: Path) -> dict[str, Any]:
    """AIPOS-F262 件③: 新接入工位的缺省目录 = <工位根>/<角色类>(pi 执行工位 executor / 审计工位 auditor)。工位根 = project.json
    paths.workstations_root(项目声明; 唯一读取口 workspace_config.project_paths)> config.schema 同键 default_user_home 模板(相对用户家目录,
    {project} = 项目名)。原缺省 `~/<项目>-executor` 直放家目录退役; 已接入项目不迁移(只影响向导给新接入的目录)。
    工位根落在 home 根 / 治理根内 = ValueError(治理仓会收进工位凭据), 出口 = 显式 --workspace-dir / --auditor-dir 或改声明。
    返回 {root(展示形, 缺省保留 ~/ 前缀便于 shell 展开), executor, auditor, source}。"""
    from tools.aipos_cli.workspace_config import _project_paths_declaration, project_json_path, project_paths
    from tools.schema_loader import SchemaLoadError

    gov = Path(governance_root)
    declared = project_paths(gov).get(WORKSTATIONS_ROOT_KEY) if project_json_path(gov).is_file() else None
    if declared is not None:
        shown, absolute = str(declared), Path(declared).expanduser()
        source = _t("ws.source_declared", key=WORKSTATIONS_ROOT_KEY)
    else:
        template = (_project_paths_declaration().get(WORKSTATIONS_ROOT_KEY) or {}).get("default_user_home")
        if not isinstance(template, str) or "{project}" not in template or template.startswith(("/", "~")):
            raise SchemaLoadError(f"config.schema.json project_json.paths.{WORKSTATIONS_ROOT_KEY}.default_user_home 未声明"
                                  "(须为相对用户家目录的模板, 含 {project})")
        rel = template.replace("{project}", project)
        shown, absolute = f"~/{rel}", Path.home() / rel
        source = _t("ws.source_default", key=WORKSTATIONS_ROOT_KEY, template=template)
    resolved = absolute.resolve()
    for label, inner in ((_t("ws.label_gov"), gov.resolve()), (_t("ws.label_home"), Path(home_root).resolve())):
        if resolved == inner or inner in resolved.parents:
            raise ValueError(_t("ws.inside", absolute=absolute, source=source, label=label, inner=inner, project=shlex.quote(project),
                                key=WORKSTATIONS_ROOT_KEY))
    base = shown.rstrip("/")
    return {"root": base, "executor": f"{base}/executor", "auditor": f"{base}/auditor", "source": source}


def _repo_names(repos: list[str] | None, code_repo: str | None, project: str, governance_root: Path | None = None) -> list[str]:
    """授权车道仓名 = 第 2 步声明的仓名(`<仓名>=<路径>` 的仓名; 只给 --code-repo = 项目名作单仓名, 与 set-repos 缺省同形); 都没给而项目已建且
    已声明仓清单 = 声明里的仓名(唯一读取口 workspace_config.project_repos; 已接入项目跑向导); 仍无 = 仓名占位。"""
    items = list(repos or ([f"{project}={code_repo}"] if code_repo else []))
    names = [str(i).split("=", 1)[0] for i in items if "=" in str(i) and not str(i).startswith("<")]
    if not names and governance_root is not None:
        from tools.aipos_cli.workspace_config import CardRepoUnresolved, project_json_path, project_repos

        if project_json_path(Path(governance_root)).is_file():
            try:
                names = sorted(project_repos(Path(governance_root))["items"])
            except (CardRepoUnresolved, OSError, ValueError) as exc:  # 清单坏 = 不猜, 给占位并说明(第 2 步重声明)
                print(f"Warning: {governance_root} 的仓清单读不出({type(exc).__name__}: {exc}); 授权车道写占位", file=sys.stderr)
    return names or [REPO_NAME_PLACEHOLDER]


def grant_task_modes() -> list[str]:
    """项目授权覆盖的任务类型 = enums.schema task_mode 值域中非审计者(审计卡按被审卡判定覆盖, autonomy_policy.envelope_subject;
    审计判定唯一读取口 next_resolver.task_mode_is_audit)。"""
    from tools.aipos_cli.next_resolver import task_mode_is_audit
    from tools.schema_loader import SchemaLoadError, load_schema

    values = (((load_schema("enums").get("enums") or {}).get("task_mode") or {}).get("values"))
    if not isinstance(values, list) or not values:
        raise SchemaLoadError("enums.schema.json enums.task_mode.values 未声明")
    return [str(v["value"]) for v in values if isinstance(v, dict) and v.get("value") and not task_mode_is_audit(v["value"])]


def existing_authorization(governance_root: Path) -> dict[str, Any]:
    """AIPOS-F262 件①: 已建项目的现有授权(验收②: 旧项目跑向导提示已有授权)——项目授权(autonomy_policy.authorization_overview,
    看板「项目授权」同读)与未迁入授权的旧信封(policy_ids − authorization_superseded_policies)。项目未建 = 空; 读不出 = error 原文(不吞)。"""
    from tools.aipos_cli.workspace_config import project_json_path

    gov = Path(governance_root)
    out: dict[str, Any] = {"authorizations": [], "envelopes": [], "error": None}
    if not project_json_path(gov).is_file():
        return out
    from tools.aipos_cli.autonomy_policy import authorization_overview, authorization_superseded_policies, policy_ids

    try:
        rows = authorization_overview(gov)
        migrated = authorization_superseded_policies(gov)
        out["envelopes"] = [pid for pid in policy_ids(gov) if pid not in migrated]
    except (OSError, ValueError) as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
        return out
    out["authorizations"] = [{k: row.get(k) for k in ("authorization_id", "revision", "state", "used", "max_tasks", "expires_at")}
                             for row in rows]
    return out


def grant_plan(governance_root: Path, project: str, *, inst: dict[str, str], repo_names: list[str], max_tasks: int,
               expires_at: str) -> dict[str, Any]:
    """AIPOS-F262 件①: 第 3 步项目常设授权的取值(一份授权覆盖接入的全部实例)。授权 ID = autonomy_policy.authorization_id_for(声明 id_template);
    车道 = 第 2 步仓名; 任务类型 = grant_task_modes; 驱动方 = 顾问实例; 主机 = 各实例名主机段(naming_profile.parse_instance_name; 授权按卡的
    assigned_to 主机段与工位实例主机段判覆盖, autonomy_policy.card_host / judge_project_authorization); 拉起 = pi 工位 harness(distribution 声明
    缺省 kind, 授权 loop 在工位拉起 pi; Owner 删去 = 只手工 /go)。existing = existing_authorization(已有授权 / 旧信封时第 3 步提示)。"""
    from tools.aipos_cli.autonomy_policy import authorization_id_for
    from tools.aipos_cli.distribution_sync import default_harness_kind
    from tools.aipos_cli.naming_profile import parse_instance_name

    hosts: list[str] = []
    for name in inst.values():
        h = str((parse_instance_name(name) or {}).get("host") or "").strip()
        if h and h not in hosts:
            hosts.append(h)
    return {"authorization_id": authorization_id_for(project), "lanes": list(repo_names), "task_modes": grant_task_modes(),
            "drivers": [inst["advisor"]], "hosts": hosts, "launch_harnesses": [default_harness_kind()], "max_tasks": int(max_tasks),
            "expires_at": expires_at, "existing": existing_authorization(governance_root)}


def _grant_existing_note(grant: dict[str, Any], project: str, hq: str) -> str | None:
    """已有授权 / 旧信封的第 3 步提示(验收②)。无 = None。"""
    ex = grant["existing"]
    pq = _shell_quote(project)
    if ex["error"]:
        return _t("grant.existing_error", error=ex["error"], pq=pq, hq=hq)
    live = [a for a in ex["authorizations"] if a.get("state") not in (None, "missing")]
    if live:
        rows = _join("; ", [_t("grant.existing_row", id=a["authorization_id"], revision=a["revision"], state=a["state"], used=a["used"],
                               max_tasks=a["max_tasks"], expires=a["expires_at"]) for a in live])
        return _t("grant.existing_live", rows=rows, pq=pq, hq=hq)
    if ex["envelopes"]:
        return _t("grant.existing_envelopes", count=len(ex["envelopes"]), ids=", ".join(ex["envelopes"]), pq=pq, hq=hq)
    return None


def on_demand_declarations(project: str, governance_root: str, *, home_root: str, actor: str, advisor: str,
                           repo_names: list[str]) -> list[dict[str, Any]]:
    """AIPOS-F262 件④: 「按需声明」一节(只列, 不强制): 每项 = 用途一句 + 何时需要 + 预演命令(零写入; 确认 = 同参改 --confirm)。
    命令形与 QUICKSTART「按需声明」节同一组产品写入口; 值由 Owner / 顾问按项目实况填(占位), 产品无内置默认。"""
    from tools.aipos_cli.workspace_config import lybra_command_prefix  # AIPOS-F215 件③: 治理根命令前缀唯一渲染
    from tools.card_policy_loader import show_command

    pq, hq = _shell_quote(project), _shell_path(home_root)
    repo = repo_names[0] if repo_names else REPO_NAME_PLACEHOLDER
    tail = ["--home-root", hq]
    return [
        {"key": "card_policy", "title": _t("on_demand.card_policy.title"),
         "purpose": _t("on_demand.card_policy.purpose"),
         "when": _t("on_demand.card_policy.when"),
         "commands": [show_command(governance_root), _join("", [pattern_command(governance_root), f" --actor {_shell_quote(actor)} --dry-run"])]},
        {"key": "test_contract", "title": _t("on_demand.test_contract.title"),
         "purpose": _t("on_demand.test_contract.purpose"),
         "when": _t("on_demand.test_contract.when"),
         "commands": [_cmd("lybra", "project", "set-test-contract", pq, *tail, "--timeout-seconds", SECONDS_PLACEHOLDER,
                           "--owner-ref", OWNER_REF_PLACEHOLDER, "--dry-run")]},
        {"key": "release_target", "title": _t("on_demand.release_target.title"),
         "purpose": _t("on_demand.release_target.purpose"),
         "when": _t("on_demand.release_target.when"),
         "commands": [_cmd("lybra", "project", "release-target", "declare", pq, *tail, "--id", TARGET_ID_PLACEHOLDER, "--host", HOST_PLACEHOLDER,
                           "--repo", repo if repo.startswith("<") else _shell_quote(repo), "--allow", ALLOW_PLACEHOLDER,
                           "--release-root", RELEASE_ROOT_PLACEHOLDER, "--entry", ENTRY_PLACEHOLDER, "--owner-ref", OWNER_REF_PLACEHOLDER,
                           "--dry-run"),
                      _cmd("lybra", "project", "release-target", "list", pq, *tail)]},
        {"key": "pi_model", "title": _t("on_demand.pi_model.title"),
         "purpose": _t("on_demand.pi_model.purpose"),
         "when": _t("on_demand.pi_model.when"),
         "commands": [_cmd("lybra", "project", "set-model", pq, *tail, "--model", MODEL_PLACEHOLDER, "--owner-ref", OWNER_REF_PLACEHOLDER,
                           "--dry-run")]},
        {"key": "headline", "title": _t("on_demand.headline.title"),
         "purpose": _t("on_demand.headline.purpose"),
         "when": _t("on_demand.headline.when"),
         "commands": [_cmd(lybra_command_prefix(governance_root), "project", "headline", "--instance", _shell_quote(advisor),
                           "--title", TITLE_PLACEHOLDER, "--now", NOW_PLACEHOLDER, "--done", DONE_PLACEHOLDER, "--dry-run")]},
    ]


def _distinct_workstation_targets(*, gov: str, advisor_dir: str | None, exec_ws: str, audit_ws: str) -> None:
    """AIPOS-F262 件②(11-01 实撞: 一个目录被两个实例登记, 先登记者章程落点失效): 向导给的 pi 执行工位 / 审计工位目录须互不相同,
    且不等于治理根(子 agent 执行者与顾问凭据落点)与顾问会话目录。相同 = ValueError(给出口)。"""
    def norm(p: str) -> Path:
        return Path(p).expanduser().resolve()

    gov_label, adv_label = _t("ws.label_gov"), _t("ws.label_advisor")
    named = {_t("ws.label_exec"): exec_ws, _t("ws.label_audit"): audit_ws, gov_label: gov}
    if advisor_dir:
        named[adv_label] = advisor_dir
    seen: dict[Path, str] = {}
    for label, path in named.items():
        key = norm(path)
        if key in seen and {label, seen[key]} != {gov_label, adv_label}:  # 顾问会话缺省即治理根(AIPOS-F253)
            raise ValueError(_t("ws.same_dir", label=label, other=seen[key], path=key))
        seen.setdefault(key, label)


def _gate_url(explicit: str | None, owner_connection: str) -> tuple[str, str]:
    """码内门地址(门基址) + 来源标注。AIPOS-F106 件①: 一律经 confirm_client.resolve_gate_base_url(唯一推导口, 委托
    loop_context.ConnectionResolver.resolve_gate_url), 优先级按 config.schema identity_resolution.keys.gate_url 声明序:
    显式 --gate-url > Owner 中央凭据库 connection.json 的 mcp.rpc_url(显式凭据文件层, 非秘密字段) > env LYBRA_GATE_URL(仅兜底)
    > urls.gate_local。原「env 高于凭据文件」与声明序相反, 已改正。凭据文件坏 = 出声 warning 后按未声明处理(guide 照常生成)。
    AIPOS-F153 件②(gap #93): 本 guide 各码都在治理根所在机(= 门机)兑换(顾问自接入落治理根、工位在本机; 跨机工位另走
    enroll_deliver --ssh 显式对外地址), 故非显式时按 config.schema lybra_dir_authority.same_host_rule 取 loopback
    (enroll_client.loopback_gate_url 唯一渲染, 端口取上述来源的端口)——与签码侧缺省推导 enrollment.resolve_gate_url_default 同一规则。"""
    from tools.aipos_cli.confirm_client import GateAddressError, declared_rpc_url, resolve_gate_base_url
    from tools.aipos_cli.enroll_client import loopback_gate_url

    if explicit:
        return resolve_gate_base_url(explicit_url=explicit), _t("gate.source_explicit")
    same_host = _t("gate.same_host")
    if not owner_connection.startswith("<") and Path(owner_connection).is_file():
        try:
            if declared_rpc_url(owner_connection):
                base = resolve_gate_base_url(connection_json=owner_connection, require_declared=True)
                return loopback_gate_url(base), _t("gate.source_connection", conn=owner_connection, same_host=same_host)
        except GateAddressError as exc:
            print(f"Warning: {exc}; 门地址按未声明处理", file=sys.stderr)
    from tools.loop_context import ConnectionResolver

    provenance = ConnectionResolver.resolve_identity()["gate_url"]  # 来源自曝(同一解析器): env 兜底 / schema 缺省
    return (loopback_gate_url(resolve_gate_base_url()),
            _t("gate.source_env" if provenance["via_env"] else "gate.source_default", same_host=same_host))


def _instances(project: str, host: str, advisor_host: str | None = None, *, executor_host: str | None = None) -> dict[str, str]:
    """实例名 = roles.schema naming.template({prefix}.{project}.{host}); AIPOS-F129: 顾问 host 段可与工位不同(他机顾问会话)。
    AIPOS-F143 件④: 子 agent 执行者的 host 段 = 顾问会话所在机(executor_host)。AIPOS-F256 件④: 子 agent 模式另给 pi_executor(pi 执行工位)。"""
    from tools.aipos_cli.naming_profile import _registry_prefix_mapping, default_instance_name

    prefixes = _registry_prefix_mapping()
    hosts = {"advisor": advisor_host or host, "executor": executor_host or host, "auditor": host}
    out = {role: default_instance_name(prefixes[role], project=project, host=hosts[role]) for role in ("advisor", "executor", "auditor")}
    if executor_host:
        # AIPOS-F256 件④: 子 agent 模式另需 pi 执行工位(非代码卡一律 pi; 与审计工位同级必备)——主机段 = 工位所在机; 与子 agent 执行者同名
        # (顾问会话与工位同机)= 主机段加 -pi 区分(模板三段不变)
        name = default_instance_name(prefixes["executor"], project=project, host=host)
        out["pi_executor"] = name if name != out["executor"] else default_instance_name(prefixes["executor"], project=project, host=f"{host}-pi")
    return out


def resolve_advisor_harness(advisor_harness: str | None, advisor_host: str | None, host_segment: str | None) -> dict[str, Any]:
    """AIPOS-F129 件③: 顾问会话 harness(取值读声明 distribution_sync.advisor_harness_kinds; 缺省 ADVISOR_HARNESS = 既有行为)。

    返回 {kind, host, instance_host, deliveries}: host = --advisor-host(会话所在机, 原样; 声明 harness_host=forbidden 的 kind 给了 = 拒);
    instance_host = 顾问实例名 host 段 = --advisor-host 短名 > --host-segment > (harness_host=forbidden, 或 harness_dir=required 的 kind 不给
    --advisor-host = 本机会话: 本机短名; AIPOS-F243: claude-code 会话可在他机, 不给 --advisor-host 仍是本机会话, 既有行为)
    > (harness_host=optional 且 harness_dir 非 required, 会话可在他机: 拒, 不默认成治理根所在机); deliveries = 声明给 advisor 角色、该 harness 的分发条目
    (workstation_wiring.declared_role_distributions + distribution_sync.harness_distributions, 与 enroll / sync 同一构建器)。"""
    from tools.aipos_cli.distribution_sync import advisor_harness_kinds, harness_distributions, harness_kind_declaration
    from tools.aipos_cli.workstation_wiring import declared_role_distributions

    kind = str(advisor_harness or ADVISOR_HARNESS).strip()
    allowed = advisor_harness_kinds()
    if kind not in allowed:
        raise ValueError(_t("harness.not_declared", kind=repr(kind), allowed=list(allowed)))
    decl = harness_kind_declaration(kind)
    host = str(advisor_host or "").strip() or None
    if host and decl["harness_host"] == "forbidden":
        raise ValueError(_t("harness.host_forbidden", kind=kind))
    if host and any(ch.isspace() for ch in host):
        raise ValueError(_t("harness.host_blank", host=repr(host)))
    if host:
        instance_host = host.split(".")[0]
    elif str(host_segment or "").strip():
        instance_host = str(host_segment).strip()
    elif decl["harness_host"] == "optional" and decl["harness_dir"] != "required":
        raise ValueError(_t("harness.host_required", kind=kind))
    else:
        instance_host = socket.gethostname().split(".")[0].strip()
    from tools.aipos_cli.custom_roles import resolve_role_to_class

    role = "advisor"  # 向导第 4 步 enroll-deliver 接入的角色; 类经唯一 role→class 实现解析
    role_class = str(resolve_role_to_class(role, required=True))
    deliveries = harness_distributions(declared_role_distributions(role, role_class), kind)
    return {"kind": kind, "host": host, "instance_host": instance_host, "deliveries": deliveries, "declaration": decl,
            "role": role, "role_class": role_class}


# ---------------------------------------------------------------------------
# guide 生成
# ---------------------------------------------------------------------------

def generate_onboarding_guide(
    project_name: str,
    *,
    home_root: str | None = None,
    gate_url: str | None = None,
    code_repo: str | None = None,
    actor: str | None = None,
    workspace_dir: str | None = None,
    repos: list[str] | None = None,
    default_repo: str | None = None,
    advisor_dir: str | None = None,
    auditor_dir: str | None = None,
    host_segment: str | None = None,
    owner_workspace: str | None = None,
    owner_connection_json: str | None = None,
    grant_days: int = 30,
    max_tasks: int = 50,
    advisor_harness: str | None = None,
    advisor_host: str | None = None,
    executor_mode: str | None = None,
    run_preflight: bool = False,
) -> dict[str, Any]:
    """生成从 0 接新项目全流程的分步指南(结构化: steps[] + metadata)。步号读 STEP(唯一声明)。

    AIPOS-F262: 接入流程恒按执行分工(AIPOS-F143 / F256)——代码卡 = 子 agent 执行者(凭据落治理根, 实例名主机段 = 顾问会话所在机,
    `enroll-deliver … --workspace <治理根> --executor-mode subagent --harness <顾问会话 kind>`), 非代码卡 = pi 执行工位, 审计 = 独立 pi 审计工位;
    三者接入后 `lybra project set-execution` 一次声明。executor_mode 只认凭据落治理根的模式(roles.schema executor_modes; 缺省即它), 给工位模式
    = ValueError(单 pi 执行工位包办的旧流程不再生成)。授权 = 第 3 步项目常设授权(AIPOS-F224), 之后各 enroll 命令的授权引用都是它。

    AIPOS-F263 件③: 开头「前置检查」(preflight: run_preflight=True 时跑 setup_check.run_setup_check, 门探测用本指南的门地址;
    缺省 False = 只给命令不跑, 结构不变)——✗ 项阻断后续(blocking=True, 人读输出在 Step 1 前标阻断并给出口; `onboarding check
    --step 0` 同判); 「起门」(gate_start: 前台 serve start 或常驻 serve install 二选一)排在首个 Owner 动作步之前(签授权 / 签码要连门)。
    前置检查与起门不占业务步号: 前置检查 = STEP["preflight"](0, `onboarding check --step 0`), 起门排在 STEP["grant"](首个 Owner 动作步)之前。

    每步: step_number, title, actor(owner|advisor|workstation), owner_action(Owner 一次性动作序号或 None), command/commands,
    purpose, check, on_fail, creates。参数:
      - workspace_dir: pi 执行工位目录(兼容旧参数名); auditor_dir: 审计工位目录; 缺省见 workstation_dirs(project.json paths.workstations_root >
        config.schema 缺省模板), 两者须不同且不在治理根 / home 根内
      - advisor_dir: 顾问 Claude Code 会话目录(顾问技能交付落点基准; AIPOS-F253 缺省 = 治理根, 同机治理根已被别的顾问登记 = 治理根内声明子路径);
        codex 顾问 = 会话目录(可空, 如实记)
      - advisor_harness / advisor_host: AIPOS-F129 件③ 顾问会话 harness(取值读声明, 缺省 claude-code)与会话所在机(见 resolve_advisor_harness)
      - repos: ["<仓名>=<绝对路径>", ...](缺省: code_repo 给了 = 单仓 <项目>=<code_repo>; 都缺 = 占位)
      - owner_workspace / owner_connection_json: Owner 凭据所在(门的中央凭据库; 缺省见 resolve_owner_credential, 推不出 = 明说并请用户给出)
      - grant_days / max_tasks: 项目常设授权的有效天数与总卡数(双上限, 第 3 步)
    """
    from tools.aipos_cli.workspace_config import resolve_home_root_with_source

    home, home_source = resolve_home_root_with_source(explicit_root=home_root)
    gov = home / project_name
    gov_s = str(gov)
    host = (host_segment or socket.gethostname().split(".")[0]).strip()
    adv_h = resolve_advisor_harness(advisor_harness, advisor_host, host_segment)
    from tools.aipos_cli.enrollment import executor_mode_declaration, governance_root_executor_modes

    mode_decl = executor_mode_declaration()
    seat_modes = [m for m in mode_decl["values"] if m in governance_root_executor_modes()]
    station_modes = [m for m in mode_decl["values"] if m not in seat_modes]
    if not seat_modes or not station_modes:
        raise ValueError("roles.schema executor_modes 须同时声明凭据落治理根(子 agent)与落工位的模式(AIPOS-F143)")
    exec_mode = seat_modes[0]  # 子 agent 执行者的接入模式(凭据落治理根)
    station_mode = str(mode_decl["default"]) if str(mode_decl["default"]) in station_modes else station_modes[0]
    asked = str(executor_mode or "").strip()
    if asked and asked not in mode_decl["values"]:
        raise ValueError(_t("mode.not_in", asked=repr(asked), values=list(mode_decl["values"])))
    if asked and asked not in seat_modes:
        raise ValueError(_t("mode.station_refused", asked=asked, exec_mode=exec_mode))
    inst = _instances(project_name, host, adv_h["instance_host"], executor_host=adv_h["instance_host"])
    owner_cred = resolve_owner_credential(home, owner_workspace=owner_workspace, owner_connection_json=owner_connection_json)
    owner_ws = owner_cred["workspace"] or OWNER_WORKSPACE_PLACEHOLDER
    owner_conn = owner_cred["path"] or OWNER_CONNECTION_PLACEHOLDER
    owner_note = ("" if owner_cred["resolved"] else _t("cred.unresolved_note", reason=owner_cred["reason"]))
    gate, gate_source = _gate_url(gate_url, owner_conn)
    # AIPOS-F129: 会话目录必给(harness_dir=required, Claude Code)才落缺省; 可空(codex)= 只用显式值, 不猜他机路径
    # AIPOS-F243 件④: 他机会话(--advisor-host)的会话目录不猜(缺省只给本机会话); 该 kind 必给目录 = 拒并给出口
    if adv_h["host"] and adv_h["declaration"]["harness_dir"] == "required" and not str(advisor_dir or "").strip():
        raise ValueError(_t("harness.dir_required", kind=adv_h["kind"], host=adv_h["host"]))
    # AIPOS-F253 件②: 本机会话缺省 = 治理根(上方经 home 根解析梯拼出, 不写死路径); 同机治理根已被别的顾问实例登记 = 治理根内声明子路径
    # (唯一实现 enrollment.default_session_dir); 显式 --advisor-dir 优先。件①: 本机会话目录与同机已接入顾问相撞 = 拒并给换目录的命令原文
    from tools.aipos_cli.enrollment import default_session_dir, session_dir_refusal

    advisor_ws: str | None = advisor_dir or None
    advisor_dir_source = {"source": "explicit", "occupant": None} if advisor_ws else None
    if not advisor_ws and adv_h["declaration"]["harness_dir"] == "required" and not adv_h["host"]:
        # AIPOS-F254 件②: 向导接入的是项目顾问(adv_h["role"], 层级判据 enrollment.session_tier: 内建角色 = 项目级)= 缺省治理根;
        # 治理根被同机子项目顾问占着 = 不降级, 下方相撞拦截给换位命令原文
        dflt = default_session_dir(gov, inst["advisor"], role=adv_h["role"])
        advisor_ws, advisor_dir_source = dflt["path"], {"source": dflt["source"], "occupant": dflt["occupant"]}
    if advisor_ws and not adv_h["host"]:
        retry_argv = ["lybra", "onboarding", "guide", project_name, "--home-root", str(home), "--advisor-harness", adv_h["kind"],
                      "--advisor-dir", "DIR_SLOT"]
        for flag, value in (("--host-segment", host_segment), ("--executor-mode", executor_mode), ("--code-repo", code_repo),
                            ("--workspace-dir", workspace_dir), ("--auditor-dir", auditor_dir), ("--default-repo", default_repo)):
            retry_argv += [flag, str(value)] if value else []
        for item in repos or []:
            retry_argv += ["--repo", str(item)]
        refusal = session_dir_refusal(gov, instance=inst["advisor"], kind=adv_h["kind"], host=None, dir=str(Path(advisor_ws).expanduser()),
                                      role=adv_h["role"], retry=shlex.join(retry_argv).replace("DIR_SLOT", "{dir}"))
        if refusal:
            raise ValueError(refusal)
    advisor_dir_label = (_session_dir_label(advisor_dir_source["source"], occupant=advisor_dir_source["occupant"])
                         if advisor_dir_source else None)
    # AIPOS-F262 件③: 工位缺省目录读声明(project.json paths.workstations_root > config.schema 缺省模板), 显式参数优先; 件②: 各目录互不相同
    ws_dirs = workstation_dirs(gov, project_name, home) if not (workspace_dir and auditor_dir) else None
    exec_ws = workspace_dir or ws_dirs["executor"]
    audit_ws = auditor_dir or ws_dirs["auditor"]
    ws_source = (_t("ws.source_explicit") if ws_dirs is None else
                 _join("", [_t("ws.source_root", root=ws_dirs["root"], source=ws_dirs["source"]),
                            _t("ws.source_partial") if (workspace_dir or auditor_dir) else ""]))
    _distinct_workstation_targets(gov=gov_s, advisor_dir=advisor_ws if not adv_h["host"] else None, exec_ws=exec_ws, audit_ws=audit_ws)
    _actor = actor or inst["advisor"]
    now = utc_now()
    expires = (now + timedelta(days=int(grant_days))).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    grant = grant_plan(gov, project_name, inst=inst, repo_names=_repo_names(repos, code_repo, project_name, gov), max_tasks=max_tasks,
                       expires_at=expires)
    auth_ref = grant["authorization_id"]
    repo_items = list(repos or ([f"{project_name}={code_repo}"] if code_repo else [REPO_PLACEHOLDER]))
    repo_default = default_repo or (repo_items[0].split("=", 1)[0] if len(repo_items) > 1 and "=" in repo_items[0] and not repo_items[0].startswith("<") else None)

    from tools.aipos_cli.workspace_config import lybra_command_prefix  # AIPOS-F215 件③: 治理根命令前缀唯一渲染

    steps: list[dict[str, Any]] = []
    gq, hq = _shell_quote(gov_s), _shell_path(str(home))

    # ── Step 1: 建项目(顾问) ─────────────────────────────────────────
    # AIPOS-F94 N5: 治理根所在不是 git 仓 = 落账无处可落(fail-closed), 第 1 步先初始化治理仓(出口 = transitions nodes.N6.landing.non_git_exit)
    from tools.aipos_cli.governance_commit import non_git_exit_command

    repo_status = governance_repo_status(home)
    step1 = [] if repo_status["git"] else [_t("step1.not_git", home=home), non_git_exit_command(gov)]
    step1 += [
        _cmd("lybra", "project", "new", _shell_quote(project_name), "--home-root", hq, "--actor", _shell_quote(_actor)),
        _t("step1.landing"),
        render_landing_command(gov, _actor, project_new_products(gov)),
    ]
    steps.append({
        "step_number": STEP["project"],
        "actor": "advisor",
        "owner_action": None,
        "title": _t("step1.title"),
        "command": _join("\n", step1),
        "purpose": _t("step1.purpose", home=home, home_source=home_source, project=project_name, gov=gov_s),
        "check": _t("step1.check"),
        "on_fail": {
            "PROJECT_EXISTS": _t("step1.fail_exists"),
            "PROJECT_NAME_EMPTY": _t("step1.fail_empty"),
            _t("step1.fail_home_key"): _t("step1.fail_home"),
            **landing_on_fail(),
        },
        "creates": _join("", [_t("step1.creates", gov=gov_s), "" if repo_status["git"] else _t("step1.creates_git", home=home)]),
        "governance_repo": repo_status,
    })

    # ── Step 2: 声明产品仓(顾问) ─────────────────────────────────────
    repo_parts = ["lybra", "project", "set-repos", _shell_quote(project_name), "--home-root", hq]
    for item in repo_items:
        repo_parts += ["--repo", item if item.startswith("<") else _shell_quote(item)]
    if repo_default:
        repo_parts += ["--default", _shell_quote(repo_default)]
    step2 = [
        # AIPOS-F125: 写 project.json 的命令同一两阶段(verbs.schema two_phase_protocol.project_json_writers): 缺省预演, --confirm 才写
        _t("step2.preview"),
        _cmd(*repo_parts, "--dry-run"),
        # AIPOS-F159 件①: --confirm 写后即经同一提交口落账 project.json(提交身份 --actor)。AIPOS-F160 件①(gap #145): 原随后的
        # 「核账/补账」governance-commit 行退役(已落账 = 多余空操作, 新项目照做困惑); 落账失败时 --confirm 自己打印补账命令并 exit 3
        _t("step2.confirm"),
        _cmd(*repo_parts, "--actor", _shell_quote(_actor), "--confirm"),
    ]
    steps.append({
        "step_number": STEP["repos"],
        "actor": "advisor",
        "owner_action": None,
        "title": _t("step2.title"),
        "command": _join("\n", step2),
        "purpose": _t("step2.purpose"),
        "check": _t("step2.check"),
        "on_fail": {
            "REPOS_CONFLICT": _t("step2.fail_conflict"),
            "PROJECT_NOT_ESTABLISHED": _t("step2.fail_not_established"),
            **landing_on_fail(),
        },
        "creates": f"{gov_s}/project.json#repos",
    })

    # ── Step 3: Owner ① 签项目常设授权(AIPOS-F224; 取代三张信封) ────────────
    # 授权先于接入: 工位类接入预检(workstation_wiring.enroll_envelope_requirement → select_envelope, 项目授权优先)须有覆盖它的授权,
    # 顾问 role 的 owner_policy_ref 亦由此推导; 之后各 enroll 命令的 --owner-authorization-ref 一律 = 本授权 ID
    pq = _shell_quote(project_name)
    # AIPOS-F280 件②: --project = 门调用的显式目标项目(Owner 多项目无缺省 / "*" 凭据也能签; 与看板 F274 同一字段)
    grant_parts = ["lybra", "project", "grant", "sign", pq, "--home-root", hq, "--project", pq]
    for lane in grant["lanes"]:
        grant_parts += ["--grant-lane", lane if lane.startswith("<") else _shell_quote(lane)]
    for mode in grant["task_modes"]:
        grant_parts += ["--task-mode", mode]
    for drv in grant["drivers"]:
        grant_parts += ["--driver", _shell_quote(drv)]
    for h in grant["hosts"]:
        grant_parts += ["--host", _shell_quote(h)]
    for kind in grant["launch_harnesses"]:
        grant_parts += ["--launch-harness", kind]
    grant_parts += ["--max-tasks", str(grant["max_tasks"]), "--expires-at", _shell_quote(grant["expires_at"]),
                    "--decision-summary", _shell_quote(f"Onboarding {project_name}: project standing grant"),
                    "--connection-json", _shell_path(owner_conn)]
    step3_cmds = [_cmd(*grant_parts), _cmd(*grant_parts, "--confirm")]
    existing_note = _grant_existing_note(grant, project_name, hq)
    step3 = [
        _t("step3.intro"),
        *([_t("step3.warn", note=existing_note)] if existing_note else []),
        *([_t("step3.warn", note=owner_note)] if owner_note else []),
        *step3_cmds,
    ]
    steps.append({
        "step_number": STEP["grant"],
        "actor": "owner",
        "owner_action": 1,
        "title": _t("step3.title", auth=auth_ref),
        "command": _join("\n", step3),
        "commands": step3_cmds,
        "purpose": _t("step3.purpose", conn=owner_conn, source=owner_cred["source"], auth=auth_ref, lanes=", ".join(grant["lanes"]),
                      modes=", ".join(grant["task_modes"]), drivers=", ".join(grant["drivers"]), hosts=", ".join(grant["hosts"]),
                      launch=", ".join(grant["launch_harnesses"])),
        "check": _t("step3.check", auth=auth_ref, pq=pq, hq=hq),
        "on_fail": {
            "PROJECT_SCOPE_DENIED / OWNER_CONFIRMATION_REQUIRED": _t("step3.fail_scope"),
            "PROJECT_AMBIGUOUS": _t("step3.fail_ambiguous", pq=pq),  # AIPOS-F280 件②
            _t("step3.fail_no_file_key"): _t("step3.fail_no_file"),
            "gate call failed": _t("step3.fail_gate"),
            _t("step3.fail_refused_key"): _t("step3.fail_refused"),
            **landing_on_fail(),
        },
        "creates": _t("step3.creates", gov=gov_s, auth=auth_ref),
        "note": _join(" ", [n for n in (existing_note, owner_note) if n]) or None,
        "authorization": {k: v for k, v in grant.items() if k != "existing"},
    })

    # ── Step 4: Owner ② 签码接入顾问(enroll-deliver: 码不出进程)+ 顾问件交付 ─────
    # AIPOS-F129 件③: 顾问 harness 读声明(resolve_advisor_harness); 技能分发只在声明给该 harness 的顾问件非空且会话目录在本机时发生
    adv_kind = adv_h["kind"]
    owner_roles = ["lybra", "roles", "--workspace-root", _shell_path(owner_ws), "--connection-json", _shell_path(owner_conn)]
    enroll_extra = ["--harness", adv_kind]
    if advisor_ws:
        enroll_extra += ["--harness-dir", _shell_path(advisor_ws)]
    if adv_h["host"]:
        enroll_extra += ["--harness-host", _shell_quote(adv_h["host"])]
    # AIPOS-F282 件②: --project = 门调用的显式目标项目(Owner 多项目无缺省 / "*" 凭据也能签; 与第 3 步 grant sign 同一字段)
    enroll_extra += ["--token-role", "owner", "--gate-url", _shell_quote(gate), "--ttl", "86400", "--project", pq,
                     "--owner-authorization-ref", _shell_quote(auth_ref),
                     "--reason", _shell_quote(f"Onboarding {project_name} advisor")]
    step4_deliver = [render_enroll_deliver_command(owner_roles, role="advisor", instance=_shell_quote(inst["advisor"]),
                                                   governance_root=gq, workspace=gq, extra=enroll_extra, phase=phase)
                     for phase in ("--dry-run", "--confirm")]
    no_dir = _t("step4.session_dir_placeholder")
    session_at = _join("", [f"{adv_h['host']}:", advisor_ws or no_dir]) if adv_h["host"] else (advisor_ws or no_dir)
    label = str(adv_h["declaration"].get("label") or adv_kind)
    # AIPOS-F136 件②: 顾问件 = 技能 + 章程(advisor-charter-<harness>, 母本 agents/roles/advisor/AGENTS.md 含持续推进守则);
    # 只在会话目录在本机(无 --advisor-host 且有会话目录)时可交付; 他机会话本机无落点(不猜落点): 章程件 = 拉取出口(AIPOS-F138), 其余列 undelivered
    deliverable = bool(adv_h["deliveries"]) and not adv_h["host"] and bool(advisor_ws)
    session_start: dict[str, Any] | None = None  # AIPOS-F138 件②: 他机会话开局取章程命令(仅本机无落点时)
    if deliverable:
        landings = [f"{advisor_ws}/{d.get('target_path') or ''}" for d in adv_h["deliveries"]]
        step4 = [_t("step4.intro_local", landings=", ".join(landings)), *step4_deliver]
        title4 = _t("step4.title_local", label=label)
        purpose4 = _t("step4.purpose_local", conn=owner_conn, project=project_name, gate=gate, gate_source=gate_source, gov=gov_s,
                      grant_step=STEP["grant"], auth=auth_ref, dists=", ".join(d["distribution_id"] for d in adv_h["deliveries"]),
                      kind=adv_kind, landings=", ".join(landings))
        check4 = _t("step4.check_local", kind=adv_kind)
        creates4 = f"{gov_s}/.lybra/connection.json, {gov_s}/.lybra/role, " + ", ".join(landings)
        note4 = _t("step4.note_local", dir=advisor_ws, dir_source=advisor_dir_label, label=label)
    else:
        undelivered = ", ".join(d["distribution_id"] for d in adv_h["deliveries"])
        why = (_t("step4.why_pull", kind=adv_kind, undelivered=undelivered, session_at=session_at)
               if adv_h["deliveries"] else _t("step4.why_none", kind=adv_kind))
        step4 = [_t("step4.intro_remote", kind=adv_kind, why=why), *step4_deliver]
        title4 = _t("step4.title_remote", label=label)
        empty = _t("step4.empty")
        purpose4 = _t("step4.purpose_remote", conn=owner_conn, project=project_name, gate=gate, gate_source=gate_source, label=label,
                      session_at=session_at, gov=gov_s, kind=adv_kind, dir=advisor_ws or empty, host=adv_h["host"] or empty, why=why)
        check4 = _t("step4.check_remote")
        creates4 = _t("step4.creates_remote", gov=gov_s, kind=adv_kind)
        # AIPOS-F244 件②: 他机会话经 ssh 跑本 guide 的命令时 lybra 用治理根所在机稳定入口(唯一拼装 charter_render.remote_cli)
        from tools.aipos_cli.charter_render import remote_cli

        via_ssh = remote_cli("lybra")
        # 入口不可用时的 hint 原文来自 charter_render.remote_cli(车道外, 单语; AIPOS-F277 已知局限)
        note4 = _t("step4.note_remote", label=label, ssh=_t("step4.ssh_entry", cli=via_ssh["cli"]) if via_ssh["entry"] else via_ssh["hint"])
    steps.append({
        "step_number": STEP["advisor"],
        "actor": "owner",
        "owner_action": 2,
        "title": title4,
        "command": _join("\n", step4),
        "commands": step4_deliver,
        "purpose": purpose4,
        "check": check4,
        "on_fail": {
            _t("step4.fail_envelope_key"): _t("step4.fail_envelope", grant_step=STEP["grant"], pq=pq, hq=hq),
            "UNKNOWN_GOVERNANCE_ROOT / PROJECT_NOT_ESTABLISHED": _t("step4.fail_unknown_root"),
            "no usable role token": _t("step4.fail_no_role_cred"),
            "gate call failed": _t("step3.fail_gate"),
            "PROJECT_AMBIGUOUS": _t("enroll.fail_ambiguous", pq=pq),  # AIPOS-F282 件②
            _t("step4.fail_harness_key"): _t("step4.fail_harness"),
            _t("step4.fail_dir_key"): _t("step4.fail_dir"),
            _t("step4.fail_delivery_key"): _t("step4.fail_delivery"),
            _t("step4.fail_rollback_key"): _t("step4.fail_rollback"),
        },
        "creates": creates4,
        "note": note4,
    })

    # ── Step 5: 顾问就位复核(前置自检 + 顾问件稳态 / 开局取章程) ────────────
    step5 = [
        _t("step5.precheck"),
        _cmd("lybra", "onboarding", "check", _shell_quote(project_name), "--step", str(STEP["advisor_check"]), "--home-root", hq),
    ]
    if deliverable:
        step5 += [
            _t("step5.steady", advisor_step=STEP["advisor"]),
            f"{_cmd('lybra', 'sync', '--harness-root', gq, '--workspace-root', gq)} --dry-run",
        ]
        title5 = _t("step5.title_local", label=label)
        check5 = _t("step5.check_local", step=STEP["advisor_check"])
    else:
        # AIPOS-F138 件②: 本机无落点的顾问章程 = 拉取出口(会话开局经 ssh 取渲染后章程, 含持续推进守则; 产品不推送)。
        # 命令唯一拼装 charter_render.charter_pull_command; 在会话所在机每次开局跑, 不是本步现在跑的命令, 故以注释行给出
        from tools.aipos_cli.charter_render import charter_pull_command, remote_session_delivery, skills_pull_command
        from tools.aipos_cli.distribution_sync import _is_charter

        charter_dists = [d for d in adv_h["deliveries"] if _is_charter(d)]
        land = adv_kind in (remote_session_delivery()["land"]["harnesses"] or [])
        if charter_dists and not land:
            session_start = charter_pull_command(gov, instance=inst["advisor"], role_class=adv_h["role_class"])
            step5 += [
                _t("step5.pull_head", session_at=session_at, label=label),
                f"#   {session_start['command']}",
            ] + ([f"#   ({session_start['host_hint']})"] if session_start["host_hint"] else [])
        elif charter_dists or any(d.get("kind") == "skills" for d in adv_h["deliveries"]):
            # AIPOS-F243 件②④: 落盘型他机会话(claude-code): 开局在会话目录取章程与技能并落到声明的相对落点(与 lybra sync 的 pull 行同一拼装)
            target = str(charter_dists[0].get("target_path") or "").strip("/") if charter_dists else None
            session_start = charter_pull_command(gov, instance=inst["advisor"], role_class=adv_h["role_class"], target_path=target)
            lines = [_t("step5.land_head", session_at=session_at, label=label)]
            if charter_dists:
                lines.append(_t("step5.land_charter", command=session_start["land_command"], target=target))
            for d in adv_h["deliveries"]:
                if d.get("kind") == "skills":
                    sk = skills_pull_command(gov, instance=inst["advisor"], role_class=adv_h["role_class"])
                    session_start["skills_command"] = sk["command"]
                    lines.append(_t("step5.land_skills", command=sk["command"], target=str(d.get("target_path") or "").strip("/")))
            step5 += lines + ([f"#   ({session_start['host_hint']})"] if session_start["host_hint"] else [])
        title5 = _t("step5.title_remote", label=label)
        check5 = _t("step5.check_remote", step=STEP["advisor_check"])
    steps.append({
        "step_number": STEP["advisor_check"],
        "actor": "advisor",
        "owner_action": None,
        "title": title5,
        "command": _join("\n", step5),
        "purpose": _t("step5.purpose", label=label, session_at=session_at, advisor_step=STEP["advisor"], gov=gov_s, auth=auth_ref),
        "check": check5,
        "on_fail": {
            _t("step5.fail_advisor_key", advisor_step=STEP["advisor"]): _t("step5.fail_advisor", advisor_step=STEP["advisor"]),
            _t("step5.fail_grant_key", grant_step=STEP["grant"]): _t("step5.fail_grant", grant_step=STEP["grant"], advisor_step=STEP["advisor"]),
            _t("step5.fail_plan_key"): _t("step5.fail_plan"),
            _t("step5.fail_foreign_key"): _t("step5.fail_foreign"),
        },
        "creates": _t("step5.creates"),
        "note": note4,
        "session_start": session_start,
    })

    # ── Step 6–8: 按执行分工接入三实例(enroll-deliver: 码不出进程; 每条显式带 --executor-mode, 授权引用 = 第 3 步项目授权) ──────
    from tools.aipos_cli.custom_roles import role_classes_in_group

    # 角色名读声明: 子 agent 模式允许的角色类 = 执行者; 工位类分组(roles.schema class_groups.workstation)里另一个 = 审计者
    exec_role = str((mode_decl["values"][exec_mode].get("allowed_role_classes") or ["executor"])[0])
    audit_role = next((r for r in role_classes_in_group("workstation") if r != exec_role), None)
    if audit_role is None:
        raise ValueError("roles.schema class_groups.workstation 未声明审计类工位角色(AIPOS-F102)")
    adv_roles = ["lybra", "roles", "--workspace-root", gq]
    # AIPOS-F157 件①: --actor = 签发实例(顾问本人), 按实例取凭据(F134/F154 唯一口; 同角色多顾问实例时缺之即拒)
    # AIPOS-F282 件②: --project 同第 4 步(门的显式目标项目)
    common = ["--gate-url", _shell_quote(gate), "--ttl", "86400", "--actor", _shell_quote(inst["advisor"]), "--project", pq]
    enroll_on_fail = {
        "no usable role token": _t("enroll.fail_no_role_cred", advisor_step=STEP["advisor"]),
        "UNKNOWN_GOVERNANCE_ROOT": _t("enroll.fail_unknown_root", project_step=STEP["project"]),
        _t("step4.fail_envelope_key"): _t("enroll.fail_envelope", grant_step=STEP["grant"], pq=pq, hq=hq),
        "401 Unauthorized": _t("enroll.fail_401"),
        "PROJECT_AMBIGUOUS": _t("enroll.fail_ambiguous", pq=pq),  # AIPOS-F282 件②
        _t("step4.fail_rollback_key"): _t("enroll.fail_rollback"),
    }

    def _enroll_pair(role: str, instance: str, target: str, extra: list[str]) -> list[str]:
        auth = ["--owner-authorization-ref", _shell_quote(auth_ref), "--reason", _shell_quote(f"Onboarding {project_name} {instance}")]
        return [render_enroll_deliver_command(adv_roles, role=role, instance=_shell_quote(instance), governance_root=gq,
                                              workspace=target, extra=[*extra, *common, *auth], phase=phase) for phase in ("--dry-run", "--confirm")]

    def _workstation_step(number: int, role: str, instance: str, ws: str, label_ws: str, duty: str) -> None:
        from tools.aipos_cli.i18n import Localized, localize

        pair = _enroll_pair(role, instance, _shell_path(ws), ["--executor-mode", station_mode])
        # 标签以 ASCII 起头(如「pi 执行工位」)时与「接入」间空一格; 各语言按各自标签判定
        by_lang = label_ws.by_lang if isinstance(label_ws, Localized) else {"zh": str(label_ws)}
        sep = localize({code: (" " if text[:1].isascii() else "") for code, text in by_lang.items()}, None,
                       where="onboarding_guide.station.title")
        steps.append({
            "step_number": number,
            "actor": "advisor",
            "owner_action": None,
            "title": _t("station.title", sep=sep, label=label_ws, mode=station_mode, instance=instance, ws=ws),
            "commands": pair,
            "command": _join("\n", [_t("station.comment", label=label_ws, auth=auth_ref), *pair]),
            "purpose": _t("station.purpose", duty=duty, project=project_name, ws=ws, ws_source=ws_source),
            "check": _t("station.check"),
            "on_fail": {
                **enroll_on_fail,
                _t("station.fail_pi_key"): _t("station.fail_pi", sync_step=STEP["sync"]),
                _t("station.fail_bin_key"): _t("station.fail_bin"),
                _t("station.fail_root_key"): _t("station.fail_root"),
            },
            "creates": _t("station.creates", ws=ws),
        })

    # ── Step 6: 审计工位(独立 pi; 审计恒不随执行方式变化) ──
    _workstation_step(STEP["auditor"], audit_role, inst["auditor"], audit_ws, _t("station.label_audit"),
                      _t("station.duty_audit", instance=inst["auditor"]))
    # ── Step 7: pi 执行工位(非代码卡一律 pi, AIPOS-F256) ──
    _workstation_step(STEP["pi_executor"], exec_role, inst["pi_executor"], exec_ws, _t("station.label_exec"),
                      _t("station.duty_exec", instance=inst["pi_executor"]))

    # ── Step 8: 子 agent 执行者(代码卡; 顾问会话派生, 凭据落治理根, 无工位) ──
    host_args = ["--harness-host", _shell_quote(adv_h["host"])] if adv_h["host"] else []
    pair8 = _enroll_pair(exec_role, inst["executor"], gq, ["--executor-mode", exec_mode, "--harness", adv_h["kind"], *host_args])
    steps.append({
        "step_number": STEP["subagent_executor"],
        "actor": "advisor",
        "owner_action": None,
        "title": _t("step8.title", mode=exec_mode, kind=adv_h["kind"], instance=inst["executor"]),
        "commands": pair8,
        "command": _join("\n", [_t("step8.comment", kind=adv_h["kind"]), *pair8]),
        "purpose": _t("step8.purpose", instance=inst["executor"], gov=gov_s, mode=exec_mode, kind=adv_h["kind"], pi_instance=inst["pi_executor"]),
        "check": _t("step8.check"),
        "on_fail": {
            **enroll_on_fail,
            "EXECUTOR_MODE_HARNESS": _t("step8.fail_harness", kind=adv_h["kind"]),
            "EXECUTOR_MODE_TARGET": _t("step8.fail_target", gov=gov_s),
        },
        "creates": _t("step8.creates", gov=gov_s, mode=exec_mode, kind=adv_h["kind"]),
    })

    # ── Step 9: 声明执行分工(三者齐)+ 复核 ──
    set_exec = [_cmd("lybra", "project", "set-execution", "--workspace-root", gq, "--subagent-executor", _shell_quote(inst["executor"]),
                     "--pi-executor", _shell_quote(inst["pi_executor"]), "--auditor", _shell_quote(inst["auditor"]), *phase)
                for phase in (("--dry-run",), ("--actor", _shell_quote(_actor), "--confirm"))]
    division = {"code": inst["executor"], "noncode": inst["pi_executor"], "audit": inst["auditor"]}
    step9 = [
        _t("step9.declare"),
        *set_exec,
        _t("step9.review"),
        _cmd("lybra", "roles", "--workspace-root", gq, "list"),
    ]
    steps.append({
        "step_number": STEP["execution"],
        "actor": "advisor",
        "owner_action": None,
        "title": _t("step9.title"),
        "command": _join("\n", step9),
        "purpose": _t("step9.purpose", code=division["code"], noncode=division["noncode"], audit=division["audit"]),
        "check": _t("step9.check"),
        "on_fail": {
            _t("step9.fail_missing_key"): _t("step9.fail_missing", first=STEP["auditor"], last=STEP["subagent_executor"]),
            _t("step9.fail_same_key"): _t("step9.fail_same"),
            **landing_on_fail(),
        },
        "creates": f"{gov_s}/project.json#execution",
        "division": division,
    })

    # ── Step 10: 工位 sync 分发 + 稳态复核 + 工位自检 ──
    step10: list[str] = []
    for label10, ws in ((_t("step10.label_exec"), exec_ws), (_t("step10.label_audit"), audit_ws)):
        wq = _shell_path(ws)
        sync_ws = f"lybra sync --harness-root {wq} --workspace-root {gq}"
        step10 += [_t("step10.station", label=label10), sync_ws, f"{sync_ws} --dry-run",
                   _cmd("lybra", "onboarding", "check", pq, "--step", str(STEP["sync"]), "--home-root", hq, "--workspace-dir", wq)]
    step10 += [_t("step10.plan"), _cmd("lybra", "project", "workstation-plan", pq, "--home-root", hq)]
    steps.append({
        "step_number": STEP["sync"],
        "actor": "advisor",
        "owner_action": None,
        "title": _t("step10.title"),
        "command": _join("\n", step10),
        "purpose": _t("step10.purpose", exec_ws=exec_ws, audit_ws=audit_ws),
        "check": _t("step10.check", step=STEP["sync"]),
        "on_fail": {
            _t("step10.fail_skipped_key"): _t("step10.fail_skipped", gov=gov_s),
            _t("step10.fail_plan_key"): _t("step10.fail_plan"),
            _t("step10.fail_not_enrolled_key"): _t("step10.fail_not_enrolled", audit_step=STEP["auditor"], exec_step=STEP["pi_executor"]),
            _t("step10.fail_selfcheck_key"): _t("step10.fail_selfcheck", grant_step=STEP["grant"]),
        },
        "creates": _t("step10.creates", exec_ws=exec_ws, audit_ws=audit_ws),
    })

    # ── Step 11: 首卡(顾问, 产品命令; 执行按分工) ──
    step11 = [
        *card_policy_lines(gov_s, _actor),  # AIPOS-F252 件①: 首卡前声明卡号形状(值 = Owner 确认的本项目卡号正则, 无内置默认)
        _t("step11.publish"),
        _cmd(lybra_command_prefix(gov_s), "draft", "create", "--from-json", DRAFT_JSON_PLACEHOLDER),
        _cmd(lybra_command_prefix(gov_s), "draft", "publish", "--path", DRAFT_PATH_PLACEHOLDER),
        _t("step11.drive", auth=auth_ref),
        _cmd("lybra", "loop", "--task-id", TASK_ID_PLACEHOLDER, "--workspace-root", gq),
        _t("step11.code", instance=inst["executor"]),
        _cmd(lybra_command_prefix(gov_s), "kickoff", "--task-id", TASK_ID_PLACEHOLDER),
        _t("step11.noncode", audit_ws=_shell_path(audit_ws)),
        f"cd {_shell_path(exec_ws)}",
        "pi",
        "/go",
    ]
    steps.append({
        "step_number": STEP["first_card"],
        "actor": "advisor",
        "owner_action": None,
        "title": _t("step11.title"),
        "command": _join("\n", step11),
        "purpose": _t("step11.purpose", auth=auth_ref, execution_step=STEP["execution"], exec_ws=exec_ws, audit_ws=audit_ws),
        "check": _t("step11.check"),
        "on_fail": {
            _t("step11.fail_5_key"): _t("step11.fail_5", grant_step=STEP["grant"]),
            _t("step11.fail_3_key"): _t("step11.fail_3"),
            _t("step11.fail_status_key"): _t("step11.fail_status", task=TASK_ID_PLACEHOLDER, gov=gq),
            _t("step11.fail_gate_key"): _t("step11.fail_gate"),
            _t("step11.fail_landing_key"): _t("step11.fail_landing"),
            _t("step11.fail_go_key"): _t("step11.fail_go"),
        },
        "creates": _t("step11.creates"),
    })

    owner_steps = [s["step_number"] for s in steps if s.get("owner_action")]
    repo_names = grant["lanes"]
    # AIPOS-F263 件③: 前置检查(setup check 摘要)+ 起门步(首个 Owner 动作步之前)
    from tools.aipos_cli.setup_check import preflight_step, run_setup_check

    preflight = preflight_step(run_setup_check(gate_url=gate) if run_preflight else None)
    gate_start = gate_start_step(owner_ws if not owner_ws.startswith("<") else gov_s, gate,
                                 before_step=STEP["grant"], preflight=preflight)
    return {
        "project_name": project_name,
        "home_root": str(home),
        "home_root_source": home_source,
        "governance_root": gov_s,
        "governance_repo": repo_status,
        "gate_url": gate,
        "gate_url_source": gate_source,
        "owner_workspace": owner_ws,
        "owner_connection_json": owner_conn,
        "owner_credential": {k: owner_cred[k] for k in ("resolved", "source", "reason", "holders", "unreadable")},  # AIPOS-F262 件③
        "instances": inst,
        "authorization": {k: v for k, v in grant.items()},  # AIPOS-F262 件①(取代原三信封 policies)
        "advisor_dir": advisor_ws,
        "advisor_dir_source": advisor_dir_label,  # AIPOS-F253
        "advisor_harness": {"kind": adv_h["kind"], "host": adv_h["host"], "dir": advisor_ws},
        "workstations": {"executor": None, "auditor": audit_ws, "pi_executor": exec_ws},  # AIPOS-F256 件④
        "workstations_source": ws_source,  # AIPOS-F262 件③
        "executor_mode": exec_mode,  # AIPOS-F143 件④ / F262: 子 agent 执行者接入模式(恒按分工)
        "division": division,  # AIPOS-F262 件②
        "preflight": preflight,  # AIPOS-F263 件③
        "gate_start": gate_start,  # AIPOS-F263 件③
        "generated_at": now.isoformat(),
        "total_steps": len(steps),
        "owner_actions": owner_steps,
        "steps": steps,
        # AIPOS-F262 件④: 按需声明(只列不强制)
        "on_demand": on_demand_declarations(project_name, gov_s, home_root=str(home), actor=_actor, advisor=inst["advisor"],
                                            repo_names=repo_names),
        # AIPOS-F123 件③ / AIPOS-F124 件②: 接入既有人肉项目(落点声明 → 冻结 → 收编), 新项目可略过
        "legacy_onboarding": legacy_onboarding_steps(project_name, gov_s, home_root=str(home), actor=_actor,
                                                     authorization_ref=auth_ref),
        "summary": _join("\n", [_t("summary.head", project=project_name, count=len(steps), owner_steps=" / ".join(map(str, owner_steps))),
                                *(_t("summary.row", n=s["step_number"], title=s["title"]) for s in steps)]),
    }


def gate_start_step(gate_workspace: str, gate_url: str, *, before_step: int | None, preflight: dict[str, Any]) -> dict[str, Any]:
    """AIPOS-F263 件③: 向导「起门」步(唯一出处): 前台 `serve start` 或常驻 `serve install`(二选一); 排在 before_step(首个 Owner
    动作步, 签授权 / 签码要连门)之前。门工作区 = Owner 凭据所在(无既有项目 = 本项目治理根, Step 1 建出后可用)。
    前置检查已显示门在跑且可达 = 注明本步可略过。命令式样: serve install 经 gate_resident.install_command 唯一渲染。"""
    from tools.aipos_cli.gate_resident import install_command

    ws = _shell_quote(gate_workspace)
    reachable = bool(preflight.get("gate_reachable"))
    return {
        "title": _t("gate_start.title"),
        "before_step": before_step,
        "actor": "owner",
        "options": [
            {"name": _t("gate_start.foreground"), "command": _cmd("lybra", "serve", "--workspace-root", ws, "start"),
             "note": _t("gate_start.foreground_note")},
            {"name": _t("gate_start.resident"), "commands": [install_command(gate_workspace, "dry-run"), install_command(gate_workspace, "confirm")],
             "note": _t("gate_start.resident_note")},
        ],
        "check": _t("gate_start.check", gate=gate_url),
        "skip_note": _t("gate_start.skip") if reachable else None,
    }


def pattern_command(governance_root: str | Path) -> str:
    """卡号形状声明命令(唯一渲染 card_policy_loader.set_task_id_pattern_command)按语言: 缺省语言 = 其缺省占位(card_policy 声明的
    pattern_placeholder, 门侧占位拒判同读); 其余语言 = 该语言占位(owner_texts.onboarding_guide.card_policy.pattern_placeholder)。"""
    from tools.aipos_cli.i18n import Localized, default_language, pick
    from tools.card_policy_loader import set_task_id_pattern_command

    placeholder = _t("card_policy.pattern_placeholder")
    if not isinstance(placeholder, Localized):
        return set_task_id_pattern_command(governance_root)
    return Localized({code: (set_task_id_pattern_command(governance_root) if code == default_language()
                             else set_task_id_pattern_command(governance_root, pick(placeholder, code))) for code in placeholder.by_lang},
                     default_language())


def card_policy_lines(governance_root: str, actor: str) -> list[str]:
    """AIPOS-F252 件①: 接入流程的卡号形状声明行(向导第 9 步首卡前 / 人肉期接入第 1 步同一渲染): 说明 + show(看建议值) + 预演 + 确认。
    命令原文唯一渲染 card_policy_loader.set_task_id_pattern_command / show_command(缺声明报错 / project upgrade 待补项同一出处); 值不内置默认。"""
    from tools.card_policy_loader import show_command

    base = pattern_command(governance_root)
    return [
        _t("card_policy.comment"),
        show_command(governance_root),
        _join("", [base, f" --actor {_shell_quote(actor)} --dry-run"]),
        _join("", [base, f" --actor {_shell_quote(actor)} --confirm"]),
    ]


def legacy_onboarding_steps(project_name: str, governance_root: str, *, home_root: str, actor: str, authorization_ref: str) -> list[dict[str, Any]]:
    """AIPOS-F123 件③ / AIPOS-F124 件②: 「接入既有人肉项目」一节(接入向导唯一出处; 顺序 = 落点声明 → 冻结 → 收编)。

    人肉期(无门记录)项目接入时: ① project.json 落点声明经产品写入口(set-paths, 免手改)——冻结按队列落点选卡、收编按 Return
    落点写报告骨架, 落点须先对; ② 存量冻结为历史(AIPOS-F122 迁移基线: lint/next 不再当待推进, 门拒对冻结卡写)——收编认冻结,
    先冻结才能把在途卡用 --exclude 留出、其余一次声明为历史; ③ 仍在途的卡逐张经门收编(AIPOS-F123: 补铸 claim 记录、绑定既有分支、
    建卡工作树), 之后与新卡一样只用 lybra loop 推进(收编前落点未声明 = 拒 ADOPT_PATHS_UNDECLARED, 冻结卡 = 拒 ADOPT_LEGACY_FROZEN)。
    命令全部显式带治理根/项目, 禁写死项目 ID/路径/卡号前缀。
    AIPOS-F127 件④: 第 1 步同时给出 project.json 说明键(phase / note)写入口 set-meta(人肉期旧文不再手改); 可写键 = 唯一判定
    workspace_config.declared_meta_keys(config.schema project_json 未声明 = 只给一行说明, 不给会被拒的命令)。
    AIPOS-F262 件①: 收编覆盖依据 = 项目常设授权 authorization_ref(收编 --owner-policy-ref 写授权 ID, 门按 select_envelope 显式授权判定); loop 不带信封。"""
    from tools.aipos_cli.workspace_config import STAGE_ARCHIVE_PATH_KEY, declared_meta_keys, project_meta_declaration

    from tools.aipos_cli.workspace_config import lybra_command_prefix  # AIPOS-F215 件③: 治理根命令前缀唯一渲染

    gq = _shell_quote(governance_root)
    pq = _shell_quote(project_name)
    hq = _shell_path(home_root)
    # AIPOS-F170 件②: 只给自由文本键(声明无 enum)的示例行——占位 <META_TEXT> 只对自由文本成立(status 等 enum 键不进向导)
    meta_flags = [project_meta_declaration()["keys"][key] for key, spec in declared_meta_keys().items() if not spec.get("enum")]
    if meta_flags:
        meta_lines = [_t("legacy.meta", keys=" / ".join(f.lstrip("-") for f in meta_flags))]
        meta_args = [part for flag in meta_flags for part in (flag, META_TEXT_PLACEHOLDER)]
        meta_lines += [_cmd("lybra", "project", "set-meta", pq, "--home-root", hq, *meta_args, phase) for phase in ("--dry-run", "--confirm")]
    else:
        meta_lines = [_t("legacy.meta_none")]
    return [
        {
            "order": 1,
            "title": _t("legacy.t1"),
            "actor": "advisor",
            "command": _join("\n", [
                _t("legacy.c1_head"),
                _cmd("lybra", "project", "set-paths", pq, "--home-root", hq, "--key", PATHS_KEY_PLACEHOLDER, "--value",
                     PATHS_VALUE_PLACEHOLDER, "--dry-run"),
                _cmd("lybra", "project", "set-paths", pq, "--home-root", hq, "--key", PATHS_KEY_PLACEHOLDER, "--value",
                     PATHS_VALUE_PLACEHOLDER, "--confirm"),
                # AIPOS-F145 件③: 既有项目声明阶段档案位置(finalize 阶段闸门按此落点核阶段快照)
                _t("legacy.c1_stage", key=STAGE_ARCHIVE_PATH_KEY),
                *meta_lines,
                *card_policy_lines(governance_root, actor),
            ]),
            "purpose": _t("legacy.p1", key=STAGE_ARCHIVE_PATH_KEY),
        },
        {
            "order": 2,
            "title": _t("legacy.t2"),
            "actor": "advisor",
            "command": _join("\n", [
                _t("legacy.c2_head"),
                _cmd("lybra", "project", "freeze-legacy", "--queue", "claimed,completed,blocked", "--exclude", TASK_ID_PLACEHOLDER,
                     "--reason", FREEZE_REASON_PLACEHOLDER, "--actor", actor, "--workspace-root", gq, "--dry-run"),
            ]),
            "purpose": _t("legacy.p2"),
        },
        {
            "order": 3,
            "title": _t("legacy.t3"),
            "actor": "advisor",
            "command": _join("\n", [
                _t("legacy.c3_head"),
                _cmd(lybra_command_prefix(governance_root), "queue", "adopt", "--task-id", TASK_ID_PLACEHOLDER, "--branch",
                     LEGACY_BRANCH_PLACEHOLDER, "--actor", actor, "--owner-policy-ref", authorization_ref, "--dry-run"),
                _cmd(lybra_command_prefix(governance_root), "queue", "adopt", "--task-id", TASK_ID_PLACEHOLDER, "--branch",
                     LEGACY_BRANCH_PLACEHOLDER, "--actor", actor, "--owner-policy-ref", authorization_ref, "--confirm"),
                _cmd("lybra", "loop", "--task-id", TASK_ID_PLACEHOLDER, "--workspace-root", gq),
            ]),
            "purpose": _t("legacy.p3"),
        },
    ]


def _format_gate_start(gate_start: dict[str, Any]) -> list[str]:
    """AIPOS-F263 件③: 起门步人读(format_guide_text 内, 首个 Owner 动作步之前)。"""
    out = [_t("text.gs_head", title=gate_start["title"])]
    for i, opt in enumerate(gate_start["options"], 1):
        out.append(_t("text.gs_option", n=i, name=opt["name"], note=opt["note"]))
        for cmd in opt.get("commands") or [opt["command"]]:
            out.append(f"     {cmd}")
    out.append(_t("text.check", check=gate_start["check"]))
    if gate_start.get("skip_note"):
        out.append(_t("text.note", note=gate_start["skip_note"]))
    out.append("")
    return out


def format_guide_text(guide: dict[str, Any], lang: str | None = None) -> str:
    """将结构化 guide 格式化为可读文本(终端输出用)。AIPOS-F277: lang = 用户语言(i18n.resolve_language; 缺省 = 缺省语言, 与改前逐字相同);
    guide 可为 generate_onboarding_guide 原样结果(含双语渲染值)或已按语言落成的结果。"""
    from tools.aipos_cli.i18n import lines_in

    unknown = _t("text.unknown")
    lines: list[Any] = []
    lines.append(_t("text.banner", project=guide["project_name"]))
    lines.append(_t("text.home", home=guide["home_root"], source=guide.get("home_root_source", unknown)))
    lines.append(_t("text.gov", gov=guide.get("governance_root", "")))
    lines.append(_t("text.gate", gate=guide["gate_url"], source=guide.get("gate_url_source", unknown)))
    cred = guide.get("owner_credential") or {}
    lines.append(_t("text.cred", conn=guide.get("owner_connection_json", ""), source=cred.get("source", unknown)))
    if cred and not cred.get("resolved"):
        lines.append(_t("text.cred_warn", reason=cred.get("reason")))
    auth = guide.get("authorization") or {}
    if auth:
        lines.append(_t("text.auth", auth=auth.get("authorization_id"), step=STEP["grant"]))
    ws = guide.get("workstations") or {}
    lines.append(_t("text.ws", exec_ws=ws.get("pi_executor"), audit_ws=ws.get("auditor"), source=guide.get("workstations_source", unknown)))
    div = guide.get("division") or {}
    if div:
        lines.append(_t("text.division", code=div.get("code"), noncode=div.get("noncode"), audit=div.get("audit")))
    lines.append(_t("text.generated", at=guide["generated_at"]))
    owner = guide.get("owner_actions") or []
    lines.append(_t("text.total", total=guide["total_steps"], count=len(owner), steps=", ".join(f"Step {n}" for n in owner)))
    lines.append("")
    pre = guide.get("preflight")  # AIPOS-F263 件③: 前置检查(lybra setup check 摘要)
    if pre:
        lines.append(_t("text.pre_head", title=pre["title"]))
        lines.append(_t("text.pre_command", command=pre["command"]))
        lines.extend(_join("", ["  ", ln]) for ln in pre["lines"])
        if pre.get("blocking"):
            lines.append(_t("text.pre_blocking", items=", ".join(pre["blocking_items"]), command=pre["command"], project=guide["project_name"]))
        lines.append("")
    gate_start = guide.get("gate_start")
    who_keys = {"owner": "text.actor_owner", "advisor": "text.actor_advisor", "workstation": "text.actor_workstation"}

    for step in guide["steps"]:
        if gate_start and step["step_number"] == gate_start.get("before_step"):
            lines.extend(_format_gate_start(gate_start))
        actor = step.get("actor", "")
        who = _t(who_keys[actor]) if actor in who_keys else actor
        lines.append(_t("text.step_head", n=step["step_number"], title=step["title"], who=who))
        lines.append(_t("text.purpose", purpose=step["purpose"]))
        lines.append("")

        if "commands" in step and len(step["commands"]) > 1:
            lines.append(_t("text.commands_each"))
            for i, cmd in enumerate(step["commands"], 1):
                lines.append(f"  {i}. {cmd}")
        else:
            lines.append(_t("text.commands"))
            for cmd_line in _split_lines(step["command"]):
                lines.append(_join("", ["  ", cmd_line]))

        lines.append("")
        lines.append(_t("text.check", check=step["check"]))

        if step.get("on_fail"):
            lines.append(_t("text.on_fail"))
            for err, fix in step["on_fail"].items():
                lines.append(_join("", ["  [", err, "] → ", fix]))

        if step.get("note"):
            lines.append(_t("text.note", note=step["note"]))

        lines.append(_t("text.creates", creates=step["creates"]))
        lines.append("")

    lines.append(_t("text.done"))
    on_demand = guide.get("on_demand") or []
    if on_demand:
        lines.append("")
        lines.append(_t("text.on_demand_head"))
        for item in on_demand:
            lines.append(_t("text.item_head", title=item["title"]))
            lines.append(_t("text.use", purpose=item["purpose"]))
            lines.append(_t("text.when", when=item["when"]))
            lines.append(_t("text.commands"))
            for cmd_line in item["commands"]:
                lines.append(_join("", ["  ", cmd_line]))
            lines.append("")
    legacy = guide.get("legacy_onboarding") or []
    if legacy:
        lines.append("")
        lines.append(_t("text.legacy_head"))
        for item in legacy:
            lines.append(_t("text.legacy_item_head", n=item["order"], title=item["title"]))
            lines.append(_t("text.purpose", purpose=item["purpose"]))
            lines.append(_t("text.commands"))
            for cmd_line in _split_lines(item["command"]):
                lines.append(_join("", ["  ", cmd_line]))
            lines.append("")
    return "\n".join(lines_in(lines, lang))


def _split_lines(text: Any) -> list[str]:
    """多行文案按行拆(双语渲染值逐语言拆, 行数不同 = 按缺省语言行数对齐, 各语言行一一对应)。"""
    from tools.aipos_cli.i18n import Localized, default_language

    if not isinstance(text, Localized):
        return str(text).split("\n")
    parts = {code: value.split("\n") for code, value in text.by_lang.items()}
    count = len(parts[default_language()])
    if any(len(v) != count for v in parts.values()):
        raise ValueError("onboarding guide: 双语命令块各语言行数不一致(声明与实现不符)")
    return [Localized({code: v[i] for code, v in parts.items()}, default_language()) for i in range(count)]


# ---------------------------------------------------------------------------
# 前置自检(lybra onboarding check; 步号与上面 guide 同一编号)
# ---------------------------------------------------------------------------

def validate_step_prerequisites(
    step_number: int,
    *,
    project_name: str,
    home_root: str | None = None,
    workspace_dir: str | None = None,
    preflight: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """验证进入某一步之前的产物是否齐(负夹具 / 运行时前置检查)。步号 = generate_onboarding_guide 的步号(唯一声明 STEP)。

    ≥项目: 项目根 + project.json; ≥授权步: 产品仓声明; ≥顾问接入步: 项目常设授权(或兼容旧信封); ≥顾问复核步: 顾问凭据(治理根 .lybra/connection.json);
    ≥工位 sync 步: 工位 connection.json(lybra_bin / workspace_root==governance_root)+ .pi 接线 + role#owner_policy_ref(工位接入步)。
    AIPOS-F262: 授权先于接入(项目常设授权, AIPOS-F224), 顾问 / 工位接入 = enroll-deliver。工位目录缺省 = workstation_dirs 的 pi 执行工位。
    AIPOS-F263 件③: preflight = setup_check.run_setup_check 结果(CLI `--step 0` = STEP["preflight"] = 向导前置检查时跑): 其 ✗ 项算缺项,
    指引 = 该项出口; 缺省 None = 不核(行为不变)。
    返回 {ok: bool, missing: [str], guidance: [str]}。
    """
    from tools.aipos_cli.workspace_config import resolve_home_root

    home = resolve_home_root(explicit_root=home_root)
    project_root = home / project_name

    missing: list[str] = []
    guidance: list[str] = []

    for item in (preflight or {}).get("items") or []:
        if item["status"] == "fail":
            missing.append(f"preflight:{item['id']}")
            guidance.append(_t("check.preflight", label=item["label"], detail=item["detail"], exit=item["exit"]))

    if step_number >= STEP["project"]:
        if not project_root.is_dir():
            missing.append("project_root")
            guidance.append(_t("check.no_project", step=STEP["project"], root=project_root))
        elif not (project_root / "project.json").is_file():
            missing.append("project.json")
            guidance.append(_t("check.no_project_json", step=STEP["project"]))

    if step_number >= STEP["grant"] and (project_root / "project.json").is_file():
        from tools.aipos_cli.workspace_config import CardRepoUnresolved, project_repos

        try:
            repos = project_repos(project_root)
            if not repos["declared"] and repos["code_repo"] is None:
                missing.append("repos")
                guidance.append(_t("check.no_repos", step=STEP["repos"]))
        except CardRepoUnresolved as exc:
            missing.append("repos")
            guidance.append(_t("check.bad_repos", error=exc, step=STEP["repos"]))

    if step_number >= STEP["advisor"] and (project_root / "project.json").is_file():
        # AIPOS-F262 件①: 授权 = 项目常设授权(autonomy_policy.authorization_ids, 声明 authorizations_root); 兼容: 仍用旧信封的项目(policy_ids)不报缺
        from tools.aipos_cli.autonomy_policy import authorization_ids, policy_ids

        if not authorization_ids(project_root) and not policy_ids(project_root):
            missing.append("authorization")
            guidance.append(_t("check.no_grant", step=STEP["grant"]))

    # AIPOS-F106 件④: connection.json 定位经 service_mode.connection_path(既有唯一定位); role 只经 charter_render.workstation_identity 读
    from tools.aipos_cli.service_mode import connection_path as _connection_path

    if step_number >= STEP["advisor_check"] and project_root.is_dir():
        if not _connection_path(project_root).is_file():
            missing.append("advisor_credential")
            guidance.append(_t("check.no_advisor", step=STEP["advisor"]))

    if step_number >= STEP["sync"]:
        steps = {"audit_step": STEP["auditor"], "exec_step": STEP["pi_executor"]}
        if workspace_dir:
            ws = Path(workspace_dir).expanduser()
        else:
            try:
                ws = Path(workstation_dirs(project_root, project_name, home)["executor"]).expanduser()
            except ValueError as exc:
                return {"ok": False, "missing": [*missing, "workspace_dir"],
                        "guidance": [*guidance, _t("check.ws_dir", error=exc.args[0] if exc.args else exc)]}
        conn_json = _connection_path(ws)
        if not conn_json.is_file():
            missing.append("connection.json")
            guidance.append(_t("check.no_conn", path=conn_json, **steps))
        else:
            try:
                data = json.loads(conn_json.read_text(encoding="utf-8"))
                if not data.get("lybra_bin"):
                    missing.append("lybra_bin")
                    guidance.append(_t("check.no_bin", **steps))
                ws_root = data.get("workspace_root", "")
                gov_root = data.get("governance_root", "")
                if ws_root and gov_root and Path(ws_root).resolve() != Path(gov_root).resolve():
                    missing.append("workspace_root_mismatch")
                    guidance.append(_t("check.root_mismatch", **steps))
            except (OSError, json.JSONDecodeError):
                missing.append("connection.json_invalid")
                guidance.append(_t("check.conn_invalid", **steps))
        if not (ws / ".pi" / "settings.json").is_file():
            missing.append(".pi/settings.json")
            guidance.append(_t("check.no_pi", sync_step=STEP["sync"], **steps))
        from tools.aipos_cli.charter_render import is_enrolled_workstation
        from tools.loop_context import ConnectionResolver

        if is_enrolled_workstation(ws):
            # role 文件只经 ConnectionResolver.resolve_identity 读(唯一实现之一; env={} = 只认工位声明层 .lybra/role)
            # AIPOS-F115 件②: 工位声明文件坏 = WorkstationFileError(原被吞成「未声明」), 此处转为缺项 + 带出口的指引
            from tools.loop_context import WorkstationFileError

            try:
                ident = ConnectionResolver.resolve_identity(workspace_root=ws, env={})
            except WorkstationFileError as exc:
                missing.append("role_invalid")
                guidance.append(_t("check.role_error", error=exc, **steps))
            else:
                if ident["role"]["source"] != ".lybra/role":
                    missing.append("role_invalid")
                    guidance.append(_t("check.role_invalid", **steps))
                elif ident["owner_policy_ref"]["source"] != ".lybra/role":
                    missing.append("owner_policy_ref")
                    guidance.append(_t("check.no_policy_ref", grant_step=STEP["grant"]))

    return {
        "ok": not missing,
        "missing": missing,
        "guidance": guidance,
    }
