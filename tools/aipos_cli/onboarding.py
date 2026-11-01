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


LANDING_ON_FAIL = {
    "Git status check failed / not a git repository": (
        "治理根须在治理仓(git)内: lybra home git-init --home-root <home 根>(一次性, 不配远端), Owner 按其输出配 origin 并首推后重跑本条"
    ),
    "PUSH NOT DONE": "已提交未推送: 按输出原因处理(无 origin = Owner 配远端; 远端冲突按输出出口)后重跑同一条",
    "BLOCK(staged 在 --paths 外)": "治理仓有他人已暂存的文件, 本命令不动; 请其所有者先提交或取消暂存后重跑",
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
        return False, f"{conn} 读不出({type(exc).__name__})"
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
        return {"path": str(conn), "workspace": str(ws), "source": "显式 --owner-connection-json", "resolved": True, "reason": None,
                "holders": [], "unreadable": []}
    if str(owner_workspace or "").strip():
        ws = Path(str(owner_workspace)).expanduser()
        return {"path": str(connection_path(ws)), "workspace": str(ws), "source": "显式 --owner-workspace", "resolved": True,
                "reason": None, "holders": [], "unreadable": []}
    home = Path(home)
    sources = [("home 根自身", home)] + [(f"项目 {name}", home / name) for name in _project_candidates(home)]
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
        source = f"门注册表中持 Owner 凭据的{picked[0]}(已声明活动项目 {declared})"
    elif len(holders) == 1:
        picked = holders[0]
        source = f"门注册表中唯一持 Owner 凭据的{picked[0]}"
    info = {"holders": [str(connection_path(ws)) for _l, ws in holders], "unreadable": unreadable}
    if picked is not None:
        return {"path": str(connection_path(picked[1])), "workspace": str(picked[1]), "source": source, "resolved": True,
                "reason": None, **info}
    if holders:
        why = (f"home 根 {home} 下有 {len(holders)} 份持 Owner 凭据的 connection.json({', '.join(info['holders'])}), "
               "且未声明活动项目可据以选择")
    else:
        why = (f"home 根 {home} 的门注册表来源(home 根自身与各已建项目的 .lybra/connection.json)里没有持 Owner 凭据的文件"
               "(门尚未以本 home 根起过: lybra serve start; 或 Owner 凭据放在别处)")
    if unreadable:
        why += f"; 读不出: {'; '.join(unreadable)}"
    return {"path": None, "workspace": None, "source": "未推导出", "resolved": False,
            "reason": why + "。请用 --owner-connection-json <门 serve 读取的、持 Owner 凭据的 connection.json> 重新生成本向导"
                            f"(或在第 3 / 4 步命令里把 {OWNER_CONNECTION_PLACEHOLDER} / {OWNER_WORKSPACE_PLACEHOLDER} 换成它与其工作区)", **info}


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
        source = f"project.json paths.{WORKSTATIONS_ROOT_KEY}(项目声明)"
    else:
        template = (_project_paths_declaration().get(WORKSTATIONS_ROOT_KEY) or {}).get("default_user_home")
        if not isinstance(template, str) or "{project}" not in template or template.startswith(("/", "~")):
            raise SchemaLoadError(f"config.schema.json project_json.paths.{WORKSTATIONS_ROOT_KEY}.default_user_home 未声明"
                                  "(须为相对用户家目录的模板, 含 {project})")
        rel = template.replace("{project}", project)
        shown, absolute = f"~/{rel}", Path.home() / rel
        source = f"config.schema 缺省 project_json.paths.{WORKSTATIONS_ROOT_KEY}.default_user_home({template}; 项目可用 lybra project set-paths 声明)"
    resolved = absolute.resolve()
    for label, inner in (("治理根", gov.resolve()), ("home 根", Path(home_root).resolve())):
        if resolved == inner or inner in resolved.parents:
            raise ValueError(f"工位根 {absolute}({source})落在{label} {inner} 内: 工位目录含工位凭据, 不得进治理仓。"
                             f"改用 --workspace-dir / --auditor-dir 给治理仓外目录, 或 lybra project set-paths {shlex.quote(project)} "
                             f"--key {WORKSTATIONS_ROOT_KEY} --value <治理仓外绝对路径> 改声明")
    base = shown.rstrip("/")
    return {"root": base, "executor": f"{base}/executor", "auditor": f"{base}/auditor", "source": source}


def _repo_names(repos: list[str] | None, code_repo: str | None, project: str) -> list[str]:
    """授权车道仓名 = 第 2 步声明的仓名(`<仓名>=<路径>` 的仓名; 只给 --code-repo = 项目名作单仓名, 与 set-repos 缺省同形); 都缺 = 仓名占位。"""
    items = list(repos or ([f"{project}={code_repo}"] if code_repo else []))
    names = [str(i).split("=", 1)[0] for i in items if "=" in str(i) and not str(i).startswith("<")]
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
        return f"读不出本项目既有授权({ex['error']}): 先 lybra project grant show {pq} --home-root {hq} 查明再决定是否签"
    live = [a for a in ex["authorizations"] if a.get("state") not in (None, "missing")]
    if live:
        rows = "; ".join(f"{a['authorization_id']} r{a['revision']}(状态 {a['state']}, 已放行 {a['used']}/{a['max_tasks']}, 到期 {a['expires_at']})"
                         for a in live)
        return (f"本项目已有项目授权: {rows}——不必重签本步(sign 会以本次范围为全部范围出新修订); 车道 / 任务类型 / 驱动方 / 主机不够时"
                f"用 lybra project grant extend {pq} --home-root {hq} … 增补(先预演), 现状看 lybra project grant show {pq} --home-root {hq}")
    if ex["envelopes"]:
        return (f"本项目已有 {len(ex['envelopes'])} 张旧信封({', '.join(ex['envelopes'])}; 兼容仍生效, 不再新签): 建议合并为项目授权——"
                f"lybra project grant draft {pq} --home-root {hq} --from-envelopes(预演; Owner 加 --confirm 签), 或按本步命令签新授权")
    return None


def on_demand_declarations(project: str, governance_root: str, *, home_root: str, actor: str, advisor: str,
                           repo_names: list[str]) -> list[dict[str, Any]]:
    """AIPOS-F262 件④: 「按需声明」一节(只列, 不强制): 每项 = 用途一句 + 何时需要 + 预演命令(零写入; 确认 = 同参改 --confirm)。
    命令形与 QUICKSTART「按需声明」节同一组产品写入口; 值由 Owner / 顾问按项目实况填(占位), 产品无内置默认。"""
    from tools.aipos_cli.workspace_config import lybra_command_prefix  # AIPOS-F215 件③: 治理根命令前缀唯一渲染
    from tools.card_policy_loader import set_task_id_pattern_command, show_command

    pq, hq = _shell_quote(project), _shell_path(home_root)
    repo = repo_names[0] if repo_names else REPO_NAME_PLACEHOLDER
    tail = ["--home-root", hq]
    return [
        {"key": "card_policy", "title": "卡号形状(card-policy task_id_pattern)",
         "purpose": "受控发布 / finalize / lybra-deploy 的卡归属解析只读它, 产品无内置默认",
         "when": "首卡前(第 11 步已带); 卡号规则变化时再改",
         "commands": [show_command(governance_root), f"{set_task_id_pattern_command(governance_root)} --actor {_shell_quote(actor)} --dry-run"]},
        {"key": "test_contract", "title": "合并后回归契约(set-test-contract)",
         "purpose": "全量回归的时限与执行方式; lybra regression baseline 据此记录 main 侧基线, 多数卡不必各自重跑 main 全量",
         "when": "产品仓有全量测试清单、要让卡分支与 main 两边基线可复用时",
         "commands": [_cmd("lybra", "project", "set-test-contract", pq, *tail, "--timeout-seconds", SECONDS_PLACEHOLDER,
                           "--owner-ref", OWNER_REF_PLACEHOLDER, "--dry-run")]},
        {"key": "release_target", "title": "生产发布目标(release-target)",
         "purpose": "声明生产主机 + 白名单 + 发布根; 之后卡的生产发布只经 lybra release plan → apply → verify(回退 release rollback), 禁裸 ssh/scp/rsync",
         "when": "项目有生产部署时(授权的主机含目标主机时 --owner-ref 可省)",
         "commands": [_cmd("lybra", "project", "release-target", "declare", pq, *tail, "--id", TARGET_ID_PLACEHOLDER, "--host", HOST_PLACEHOLDER,
                           "--repo", repo if repo.startswith("<") else _shell_quote(repo), "--allow", ALLOW_PLACEHOLDER,
                           "--release-root", RELEASE_ROOT_PLACEHOLDER, "--entry", ENTRY_PLACEHOLDER, "--owner-ref", OWNER_REF_PLACEHOLDER,
                           "--dry-run"),
                      _cmd("lybra", "project", "release-target", "list", pq, *tail)]},
        {"key": "pi_model", "title": "pi 模型(set-model)",
         "purpose": "loop 拉起的 pi 执行体 / 审计体用哪个模型(须是本机 pi 已配置的模型, pi --list-models 可查)",
         "when": "不想沿用 pi 自身缺省模型时(可按角色 --role executor / auditor 分别声明)",
         "commands": [_cmd("lybra", "project", "set-model", pq, *tail, "--model", MODEL_PLACEHOLDER, "--owner-ref", OWNER_REF_PLACEHOLDER,
                           "--dry-run")]},
        {"key": "headline", "title": "看板顾问卡(project headline)",
         "purpose": "顾问在看板首页的摘要卡: 标题 / 正在推进 / 最近完成",
         "when": "接入完成后, 以及项目阶段变化时",
         "commands": [_cmd(lybra_command_prefix(governance_root), "project", "headline", "--instance", _shell_quote(advisor),
                           "--title", TITLE_PLACEHOLDER, "--now", NOW_PLACEHOLDER, "--done", DONE_PLACEHOLDER, "--dry-run")]},
    ]


def _distinct_workstation_targets(*, gov: str, advisor_dir: str | None, exec_ws: str, audit_ws: str) -> None:
    """AIPOS-F262 件②(11-01 实撞: 一个目录被两个实例登记, 先登记者章程落点失效): 向导给的 pi 执行工位 / 审计工位目录须互不相同,
    且不等于治理根(子 agent 执行者与顾问凭据落点)与顾问会话目录。相同 = ValueError(给出口)。"""
    def norm(p: str) -> Path:
        return Path(p).expanduser().resolve()

    named = {"pi 执行工位(--workspace-dir)": exec_ws, "审计工位(--auditor-dir)": audit_ws, "治理根": gov}
    if advisor_dir:
        named["顾问会话目录(--advisor-dir)"] = advisor_dir
    seen: dict[Path, str] = {}
    for label, path in named.items():
        key = norm(path)
        if key in seen and {label, seen[key]} != {"治理根", "顾问会话目录(--advisor-dir)"}:  # 顾问会话缺省即治理根(AIPOS-F253)
            raise ValueError(f"{label} 与{seen[key]}是同一目录 {key}: 一个目录只能登记给一个实例(否则先登记者的章程落点失效)。"
                             "给各工位不同目录后重跑向导(--workspace-dir / --auditor-dir)")
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
        return resolve_gate_base_url(explicit_url=explicit), "显式 --gate-url"
    same_host = "; 同机按 same_host_rule 取 loopback, 端口取自该来源"
    if not owner_connection.startswith("<") and Path(owner_connection).is_file():
        try:
            if declared_rpc_url(owner_connection):
                base = resolve_gate_base_url(connection_json=owner_connection, require_declared=True)
                return loopback_gate_url(base), f"{owner_connection} mcp.rpc_url{same_host}"
        except GateAddressError as exc:
            print(f"Warning: {exc}; 门地址按未声明处理", file=sys.stderr)
    from tools.loop_context import ConnectionResolver

    provenance = ConnectionResolver.resolve_identity()["gate_url"]  # 来源自曝(同一解析器): env 兜底 / schema 缺省
    return (loopback_gate_url(resolve_gate_base_url()),
            ("环境变量 LYBRA_GATE_URL" if provenance["via_env"] else "config.schema 缺省") + same_host)


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
        raise ValueError(f"--advisor-harness {kind!r} 不是声明的顾问会话 harness; 合法值 {list(allowed)}"
                         "(distribution.schema harness_semantics.kinds.<kind>.advisor_session)")
    decl = harness_kind_declaration(kind)
    host = str(advisor_host or "").strip() or None
    if host and decl["harness_host"] == "forbidden":
        raise ValueError(f"--advisor-host 不适用于 --advisor-harness {kind}(声明 harness_host=forbidden: 该会话与治理根同机, "
                         "技能交付到本机会话目录); 他机顾问会话用 --advisor-harness 中 harness_host=optional 的值")
    if host and any(ch.isspace() for ch in host):
        raise ValueError(f"--advisor-host {host!r} 含空白(主机名不得含空白)")
    if host:
        instance_host = host.split(".")[0]
    elif str(host_segment or "").strip():
        instance_host = str(host_segment).strip()
    elif decl["harness_host"] == "optional" and decl["harness_dir"] != "required":
        raise ValueError(f"--advisor-harness {kind} 的会话可在他机: 须给 --advisor-host <会话所在机>(或 --host-segment), "
                         "顾问实例名 host 段不默认成治理根所在机")
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
) -> dict[str, Any]:
    """生成从 0 接新项目全流程的分步指南(结构化: steps[] + metadata)。步号读 STEP(唯一声明)。

    AIPOS-F262: 接入流程恒按执行分工(AIPOS-F143 / F256)——代码卡 = 子 agent 执行者(凭据落治理根, 实例名主机段 = 顾问会话所在机,
    `enroll-deliver … --workspace <治理根> --executor-mode subagent --harness <顾问会话 kind>`), 非代码卡 = pi 执行工位, 审计 = 独立 pi 审计工位;
    三者接入后 `lybra project set-execution` 一次声明。executor_mode 只认凭据落治理根的模式(roles.schema executor_modes; 缺省即它), 给工位模式
    = ValueError(单 pi 执行工位包办的旧流程不再生成)。授权 = 第 3 步项目常设授权(AIPOS-F224), 之后各 enroll 命令的授权引用都是它。

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
        raise ValueError(f"--executor-mode {asked!r} 不在 {list(mode_decl['values'])}(roles.schema executor_modes)")
    if asked and asked not in seat_modes:
        raise ValueError(f"--executor-mode {asked}: 接入流程恒按执行分工(AIPOS-F256 / F262)——代码卡 = 子 agent 执行者(--executor-mode {exec_mode}), "
                         f"非代码卡 = pi 执行工位({asked} 模式), 审计 = pi 审计工位, 向导三者都接入; 单 pi 执行工位包办的旧流程不再生成。"
                         "去掉 --executor-mode 重跑")
    inst = _instances(project_name, host, adv_h["instance_host"], executor_host=adv_h["instance_host"])
    owner_cred = resolve_owner_credential(home, owner_workspace=owner_workspace, owner_connection_json=owner_connection_json)
    owner_ws = owner_cred["workspace"] or OWNER_WORKSPACE_PLACEHOLDER
    owner_conn = owner_cred["path"] or OWNER_CONNECTION_PLACEHOLDER
    owner_note = ("" if owner_cred["resolved"] else
                  f"⚠ Owner 凭据未推导出: {owner_cred['reason']}")
    gate, gate_source = _gate_url(gate_url, owner_conn)
    # AIPOS-F129: 会话目录必给(harness_dir=required, Claude Code)才落缺省; 可空(codex)= 只用显式值, 不猜他机路径
    # AIPOS-F243 件④: 他机会话(--advisor-host)的会话目录不猜(缺省只给本机会话); 该 kind 必给目录 = 拒并给出口
    if adv_h["host"] and adv_h["declaration"]["harness_dir"] == "required" and not str(advisor_dir or "").strip():
        raise ValueError(f"--advisor-harness {adv_h['kind']} 的会话在他机({adv_h['host']}): 须给 --advisor-dir <该机上的会话目录绝对路径>"
                         "(他机目录如实登记, 不猜)")
    # AIPOS-F253 件②: 本机会话缺省 = 治理根(上方经 home 根解析梯拼出, 不写死路径); 同机治理根已被别的顾问实例登记 = 治理根内声明子路径
    # (唯一实现 enrollment.default_session_dir); 显式 --advisor-dir 优先。件①: 本机会话目录与同机已接入顾问相撞 = 拒并给换目录的命令原文
    from tools.aipos_cli.enrollment import default_session_dir, session_dir_refusal, session_dir_source_label

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
    advisor_dir_label = (session_dir_source_label(advisor_dir_source["source"], occupant=advisor_dir_source["occupant"])
                         if advisor_dir_source else None)
    # AIPOS-F262 件③: 工位缺省目录读声明(project.json paths.workstations_root > config.schema 缺省模板), 显式参数优先; 件②: 各目录互不相同
    ws_dirs = workstation_dirs(gov, project_name, home) if not (workspace_dir and auditor_dir) else None
    exec_ws = workspace_dir or ws_dirs["executor"]
    audit_ws = auditor_dir or ws_dirs["auditor"]
    ws_source = ("显式 --workspace-dir / --auditor-dir" if ws_dirs is None else
                 f"工位根 {ws_dirs['root']}(来源: {ws_dirs['source']})" + ("; 部分为显式参数" if (workspace_dir or auditor_dir) else ""))
    _distinct_workstation_targets(gov=gov_s, advisor_dir=advisor_ws if not adv_h["host"] else None, exec_ws=exec_ws, audit_ws=audit_ws)
    _actor = actor or inst["advisor"]
    now = utc_now()
    expires = (now + timedelta(days=int(grant_days))).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    grant = grant_plan(gov, project_name, inst=inst, repo_names=_repo_names(repos, code_repo, project_name), max_tasks=max_tasks,
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
    step1 = [] if repo_status["git"] else [
        f"# 治理仓未就绪: home 根 {home} 不在任何 git 仓内, 本步落账与之后每张卡的落账都会 fail-closed。先一次性初始化"
        "(本地 git init, 不配远端、不推送), Owner 按其输出配 origin 并首推:",
        non_git_exit_command(gov),
    ]
    step1 += [
        _cmd("lybra", "project", "new", _shell_quote(project_name), "--home-root", hq, "--actor", _shell_quote(_actor)),
        "# 落账: 本步产物精确提交并推送治理仓(AIPOS-F94; 只提交下列路径, 不碰治理仓其他改动)",
        render_landing_command(gov, _actor, project_new_products(gov)),
    ]
    steps.append({
        "step_number": STEP["project"],
        "actor": "advisor",
        "owner_action": None,
        "title": "建项目(home 根下的治理根 + project.json + 首份阶段快照)并落账",
        "command": "\n".join(step1),
        "purpose": (
            f"在 home 根 {home}(来源: {home_source})下建 {project_name} 的治理根 {gov_s}: 队列 / 记录 / 治理文档 / project.json, "
            "并经 governance add stage 写首份阶段快照「项目创建」。门只扫描 home 根发现项目; 要放别处只能改 home 根本身"
            "(--home-root > LYBRA_HOME_ROOT > ~/.lybra/config.json home_root > 缺省 ~/.lybra/projects), 不登记 home 根外的治理根"
        ),
        "check": ("输出 'Created project root:' 与 'stage snapshot:' 行; 验证: lybra project list --home-root <home 根> 出现项目名; "
                  "落账输出 Verdict: PASS 且 '✓ Pushed to remote'"),
        "on_fail": {
            "PROJECT_EXISTS": "项目根已存在且非空; lybra project list 确认后跳到 Step 2",
            "PROJECT_NAME_EMPTY": "项目名不能为空; 给一个非空项目名重跑本步",
            "home 根不对": "home 根只经优先级梯解析(见目的); 换 --home-root 重跑本步",
            **LANDING_ON_FAIL,
        },
        "creates": f"{gov_s}/(project.json, 5_tasks/, governance/, stage_archive/<日期>_项目创建.md)"
                   + ("" if repo_status["git"] else f"; {home} 成为治理仓(git, 待 Owner 配 origin)"),
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
        "# 先预演(打印将写的 project.json diff 与校验结果, 零写入)给 Owner 看, 再 --confirm 写入",
        _cmd(*repo_parts, "--dry-run"),
        # AIPOS-F159 件①: --confirm 写后即经同一提交口落账 project.json(提交身份 --actor)。AIPOS-F160 件①(gap #145): 原随后的
        # 「核账/补账」governance-commit 行退役(已落账 = 多余空操作, 新项目照做困惑); 落账失败时 --confirm 自己打印补账命令并 exit 3
        "# --confirm 写入后即自动提交并推送 project.json(输出 '落账: verdict=PASS …'), 不必再跑 governance-commit; 落账失败(exit 3)时照其输出的补账命令跑一次",
        _cmd(*repo_parts, "--actor", _shell_quote(_actor), "--confirm"),
    ]
    steps.append({
        "step_number": STEP["repos"],
        "actor": "advisor",
        "owner_action": None,
        "title": "声明产品仓(project.json repos / code_repo)并落账",
        "command": "\n".join(step2),
        "purpose": ("把产品仓清单写进 project.json(repos {default, items} + code_repo 别名), 经 config.schema project_json.repos 声明校验; "
                    "卡 lane.repo 写仓名, 建工作树 / finalize 按此解析。之后各卡由 lybra loop 结案后自动落账; 写治理真相的命令(project 族 / "
                    "project grant / migrate)--confirm 写后自动落账; 其余非卡改动(手改治理文档等)用 governance-commit --paths"),
        "check": ("--dry-run 输出 'project set-repos <项目>: 预览(未写; 加 --confirm 写入)' + 每个仓一行 + project.json diff(零写入); "
                  "--confirm 输出 '已写入' 与 '落账: verdict=PASS committed=True pushed=True'(AIPOS-F159: 写后即落账); 多于一个仓须 --default"),
        "on_fail": {
            "REPOS_CONFLICT": "路径须为绝对路径、default 须在仓名内; 改参数重跑(预演即报错, project.json 未改动)",
            "PROJECT_NOT_ESTABLISHED": "Step 1 未完成; 先跑 Step 1",
            **LANDING_ON_FAIL,
        },
        "creates": f"{gov_s}/project.json#repos",
    })

    # ── Step 3: Owner ① 签项目常设授权(AIPOS-F224; 取代三张信封) ────────────
    # 授权先于接入: 工位类接入预检(workstation_wiring.enroll_envelope_requirement → select_envelope, 项目授权优先)须有覆盖它的授权,
    # 顾问 role 的 owner_policy_ref 亦由此推导; 之后各 enroll 命令的 --owner-authorization-ref 一律 = 本授权 ID
    pq = _shell_quote(project_name)
    grant_parts = ["lybra", "project", "grant", "sign", pq, "--home-root", hq]
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
        "# 先预演(零写入: 打印授权修订全文与校验)给 Owner 看, 再 --confirm 经门签发并落账; 一份授权覆盖本项目全部车道 / 任务类型 / 驱动方 / 主机,"
        " 不再逐卡、逐实例签信封",
        *([f"# ⚠ {existing_note}"] if existing_note else []),
        *([f"# ⚠ {owner_note}"] if owner_note else []),
        *step3_cmds,
    ]
    steps.append({
        "step_number": STEP["grant"],
        "actor": "owner",
        "owner_action": 1,
        "title": f"【Owner 动作 ①】签项目常设授权 {auth_ref}(一份覆盖全项目, 取代逐实例信封)",
        "command": "\n".join(step3),
        "commands": step3_cmds,
        "purpose": (
            f"Owner 用门的中央凭据库({owner_conn}; 来源: {owner_cred['source']})里的 Owner 凭据经门 owner_decision_record 签项目常设授权 "
            f"{auth_ref}: 车道 {', '.join(grant['lanes'])} × 任务类型 {', '.join(grant['task_modes'])}(审计卡按被审卡判定) × 驱动方 "
            f"{', '.join(grant['drivers'])}; 主机 {', '.join(grant['hosts'])}(本向导接入的各实例名主机段); 拉起 {', '.join(grant['launch_harnesses'])}"
            "(授权 lybra loop 在工位自动拉起 pi; Owner 删去 --launch-harness = 只手工 /go); 总卡数 + 到期双上限。"
            "授权先于接入: 工位接入预检须有覆盖它的生效授权(无则拒, 不签码 / 不改凭据); 之后 lybra loop 自动找到本授权, 不再逐卡签信封"
        ),
        "check": (f"预演输出 'lybra project grant sign 预演(零写入 …): verdict=PASS' 与授权修订全文; --confirm 输出 'signed {auth_ref}' 与 "
                  f"'wrote …/{auth_ref}/r<修订号>.md'(以门生记录为准); 之后 lybra project grant show {pq} --home-root {hq} 列出该授权, 状态 active"),
        "on_fail": {
            "PROJECT_SCOPE_DENIED / OWNER_CONFIRMATION_REQUIRED": "签授权须 Owner 凭据(owner_confirm); --connection-json 指向门的中央凭据库",
            "凭据文件不存在": f"--connection-json 须指向门 serve 读取的、持 Owner 凭据的 connection.json; 推不出时给 --owner-connection-json 重新生成本向导",
            "gate call failed": "门未运行或地址不通: lybra serve status; 此为门侧故障, 报 Owner",
            "✗ 拒因": "按预演列出的拒因改参数(车道须为第 2 步仓名、任务类型须在值域)后重跑预演; 预演零写入",
            **LANDING_ON_FAIL,
        },
        "creates": f"{gov_s}/<authorizations_root>/{auth_ref}/r<修订号>.md + owner_decisions 记录(落账进治理仓)",
        "note": " ".join(n for n in (existing_note, owner_note) if n) or None,
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
    enroll_extra += ["--token-role", "owner", "--gate-url", _shell_quote(gate), "--ttl", "86400",
                     "--owner-authorization-ref", _shell_quote(auth_ref),
                     "--reason", _shell_quote(f"Onboarding {project_name} advisor")]
    step4_deliver = [render_enroll_deliver_command(owner_roles, role="advisor", instance=_shell_quote(inst["advisor"]),
                                                   governance_root=gq, workspace=gq, extra=enroll_extra, phase=phase)
                     for phase in ("--dry-run", "--confirm")]
    session_at = f"{adv_h['host']}:{advisor_ws or '<会话目录>'}" if adv_h["host"] else (advisor_ws or "<会话目录>")
    label = str(adv_h["declaration"].get("label") or adv_kind)
    # AIPOS-F136 件②: 顾问件 = 技能 + 章程(advisor-charter-<harness>, 母本 agents/roles/advisor/AGENTS.md 含持续推进守则);
    # 只在会话目录在本机(无 --advisor-host 且有会话目录)时可交付; 他机会话本机无落点(不猜落点): 章程件 = 拉取出口(AIPOS-F138), 其余列 undelivered
    deliverable = bool(adv_h["deliveries"]) and not adv_h["host"] and bool(advisor_ws)
    session_start: dict[str, Any] | None = None  # AIPOS-F138 件②: 他机会话开局取章程命令(仅本机无落点时)
    if deliverable:
        landings = [f"{advisor_ws}/{d.get('target_path') or ''}" for d in adv_h["deliveries"]]
        step4 = [
            "# 先预演(显示将签的角色 / 实例 / 治理根 / 有效期与接入落点, 不签码、零写入)给 Owner 看, 再 --confirm: 签码 + 接入一体,"
            " 注册码只在本进程内(不打印、不落盘、不进 argv)。凭据落治理根 .lybra/(loop 按治理根取驱动方凭据), 顾问件(技能 / 章程)"
            "交付到会话目录: " + ", ".join(landings),
            *step4_deliver,
        ]
        title4 = f"【Owner 动作 ②】签码接入顾问到治理根(enroll-deliver, 码不出进程)+ 顾问件交付到 {label} 会话目录"
        purpose4 = (
            f"Owner 用门的中央凭据库({owner_conn})里的 Owner 凭据, 在治理根所在机一条命令为 {project_name} 签顾问码并就地兑换: "
            f"码内嵌门地址 {gate}(来源: {gate_source})与治理根, 只在本进程内传给 enroll(不打印、不落盘、不进 argv), 兑换出的 advisor 凭据"
            f"(projects=[{project_name}])落 {gov_s}/.lybra/(connection.json + role, owner_policy_ref 由第 {STEP['grant']} 步项目授权 {auth_ref} 推导); "
            f"产品按 distribution 声明({', '.join(d['distribution_id'] for d in adv_h['deliveries'])}, harness={adv_kind})"
            f"把顾问件经同一分发引擎交付到 {', '.join(landings)}(章程 = 顾问章程母本的渲染物, 含持续推进守则)"
        )
        check4 = (f"--dry-run 输出 'roles enroll-deliver advisor: 预览(未执行; 加 --confirm 执行)'(未签码、零写入); --confirm 输出 '已执行'、"
                  f"注册码 code_id 与指纹(无码原文)与 '{adv_kind} 件 → …' 行")
        creates4 = f"{gov_s}/.lybra/connection.json, {gov_s}/.lybra/role, " + ", ".join(landings)
        note4 = (f"顾问会话目录 {advisor_ws}({advisor_dir_label}; 缺省 = 治理根, 同机治理根已被别的顾问登记 = 治理根内声明子路径, "
                 f"--advisor-dir 显式优先; 同机两个顾问不能共用一个会话目录): 本 guide 每条命令都显式带治理根, 不依赖 cwd; "
                 f"顾问件交付后在会话目录重启 {label} 会话即加载")
    else:
        undelivered = ", ".join(d["distribution_id"] for d in adv_h["deliveries"])
        why = (f"声明给 {adv_kind} 的顾问件({undelivered})需本机会话目录, 会话在 {session_at} = 本机无落点不落盘(章程 / 技能件由会话开局拉取, 见 Step 5; 其余件 sync 列 undelivered)"
               if adv_h["deliveries"] else f"声明里无给 {adv_kind} 的顾问件 = 无分发步骤")
        step4 = [
            f"# 先预演再 --confirm(在治理根所在机执行; 签码 + 接入一体, 注册码只在本进程内): 凭据与身份落治理根 .lybra/,"
            f" role 如实记 harness={adv_kind} 与会话所在(--harness-host / --harness-dir); {why}",
            *step4_deliver,
        ]
        title4 = f"【Owner 动作 ②】签码接入顾问到治理根(enroll-deliver, 码不出进程; {label} 会话, 如实登记会话所在)"
        purpose4 = (
            f"Owner 用门的中央凭据库({owner_conn})里的 Owner 凭据, 在治理根所在机一条命令为 {project_name} 签顾问码并就地兑换"
            f"(码内嵌门地址 {gate}, 来源: {gate_source}; 码只在本进程内): 顾问({label} 会话, 位于 {session_at})的 advisor 凭据落 "
            f"{gov_s}/.lybra/(connection.json + role); role 的 harness 记 {{kind: {adv_kind}, dir: {advisor_ws or '空'}, host: {adv_h['host'] or '空'}}}"
            f"(他机目录如实记、不校验本机存在); 不落 .pi 接线; {why}"
        )
        check4 = ("--dry-run 输出 '预览(未执行; 加 --confirm 执行)'(未签码、零写入); --confirm 输出 '已执行' 与 'harness …' 已记入 .lybra/role; "
                  "治理根下无 .pi/, 本机不新增顾问件")
        creates4 = f"{gov_s}/.lybra/connection.json, {gov_s}/.lybra/role(harness={adv_kind})"
        # AIPOS-F244 件②: 他机会话经 ssh 跑本 guide 的命令时 lybra 用治理根所在机稳定入口(唯一拼装 charter_render.remote_cli)
        from tools.aipos_cli.charter_render import remote_cli

        via_ssh = remote_cli("lybra")
        note4 = (f"顾问会话({label})与治理根可不同机: 产品命令在治理根所在机运行(他机会话经 ssh), 本 guide 每条命令都显式带治理根, "
                 "不依赖 cwd; " + (f"经 ssh 运行时 lybra 写治理根所在机入口绝对路径 {via_ssh['cli']}(非登录 ssh 不加载 ~/.local/bin 进 PATH)"
                                    if via_ssh["entry"] else via_ssh["hint"]))
    steps.append({
        "step_number": STEP["advisor"],
        "actor": "owner",
        "owner_action": 2,
        "title": title4,
        "command": "\n".join(step4),
        "commands": step4_deliver,
        "purpose": purpose4,
        "check": check4,
        "on_fail": {
            "ENROLL_ENVELOPE_MISSING / 推导 owner_policy_ref 失败": (f"第 {STEP['grant']} 步项目授权未签或不覆盖本实例(驱动方 / 主机); 核对 "
                                                              f"lybra project grant show {pq} --home-root {hq}(预检拒 = 未签码、未改凭据)"),
            "UNKNOWN_GOVERNANCE_ROOT / PROJECT_NOT_ESTABLISHED": "门或本机在 home 根下找不到该项目(第 1 步未完成或门的 home 根不同); 确认门与第 1 步用同一 home 根",
            "no usable role token": "--connection-json 须指向持 Owner 凭据的中央凭据库(门 serve 所读的 connection.json)",
            "gate call failed": "门未运行或地址不通: lybra serve status; 此为门侧故障, 报 Owner",
            "不在 distribution.schema harness_semantics.kinds 声明内": "--harness 取值按输出列出的合法值改正后重跑(预检拒, 未签码)",
            "须为已存在的绝对目录": "会话在他机时加 --harness-host <会话所在机>(他机目录如实记, 不校验本机存在); 同机则先建目录",
            "件交付失败": "凭据已落, 重跑 lybra sync --harness-root <治理根>(勿重跑接入)",
            "已回滚 / 回滚失败": "接入失败时注册码已经门吊销(输出 '已回滚'); 回滚失败按输出出口手工吊销后重跑本步",
        },
        "creates": creates4,
        "note": note4,
    })

    # ── Step 5: 顾问就位复核(前置自检 + 顾问件稳态 / 开局取章程) ────────────
    step5 = [
        "# 前置自检(只读): 项目 / 产品仓 / 项目授权 / 顾问凭据齐",
        _cmd("lybra", "onboarding", "check", _shell_quote(project_name), "--step", str(STEP["advisor_check"]), "--home-root", hq),
    ]
    if deliverable:
        step5 += [
            f"# 顾问件稳态复核: plan 为空 = 顾问件已齐(第 {STEP['advisor']} 步已交付; 母本变更后由 lybra sync 重渲染)",
            f"{_cmd('lybra', 'sync', '--harness-root', gq, '--workspace-root', gq)} --dry-run",
        ]
        title5 = f"顾问就位复核({label} 会话目录顾问件稳态)"
        check5 = f"onboarding check 输出 '✓ Step {STEP['advisor_check']} prerequisites satisfied'; sync --dry-run 输出 'up-to-date: 0 file(s) to fetch/render'"
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
                f"# 开局取章程(在 {session_at} 每次开 {label} 会话先运行, 输出 = 渲染后顾问章程全文, 含持续推进守则):",
                f"#   {session_start['command']}",
            ] + ([f"#   ({session_start['host_hint']})"] if session_start["host_hint"] else [])
        elif charter_dists or any(d.get("kind") == "skills" for d in adv_h["deliveries"]):
            # AIPOS-F243 件②④: 落盘型他机会话(claude-code): 开局在会话目录取章程与技能并落到声明的相对落点(与 lybra sync 的 pull 行同一拼装)
            target = str(charter_dists[0].get("target_path") or "").strip("/") if charter_dists else None
            session_start = charter_pull_command(gov, instance=inst["advisor"], role_class=adv_h["role_class"], target_path=target)
            lines = [f"# 开局取章程与技能(在 {session_at} 每次开 {label} 会话先在会话目录运行; 由会话自己落盘, 产品不推送):"]
            if charter_dists:
                lines.append(f"#   {session_start['land_command']}    # 章程 → {target}")
            for d in adv_h["deliveries"]:
                if d.get("kind") == "skills":
                    sk = skills_pull_command(gov, instance=inst["advisor"], role_class=adv_h["role_class"])
                    session_start["skills_command"] = sk["command"]
                    lines.append(f"#   {sk['command']}    # 技能 → {str(d.get('target_path') or '').strip('/')}/")
            step5 += lines + ([f"#   ({session_start['host_hint']})"] if session_start["host_hint"] else [])
        title5 = f"顾问就位复核({label} 会话: 前置自检 + 开局取章程)"
        check5 = f"onboarding check 输出 '✓ Step {STEP['advisor_check']} prerequisites satisfied'; 治理根下无 .pi/, 本机不新增顾问件"
    steps.append({
        "step_number": STEP["advisor_check"],
        "actor": "advisor",
        "owner_action": None,
        "title": title5,
        "command": "\n".join(step5),
        "purpose": (f"新顾问({label} 会话, 位于 {session_at})确认第 1–{STEP['advisor']} 步的产物齐: 治理根 {gov_s}/.lybra/ 已有自己的 advisor 凭据与 role"
                    f"(owner_policy_ref = 项目授权 {auth_ref}), 顾问件到位(本机会话)或会话开局取章程(他机会话)"),
        "check": check5,
        "on_fail": {
            f"Step {STEP['advisor']} 未完成 / 治理根无顾问凭据": f"请 Owner 重跑第 {STEP['advisor']} 步(先 --dry-run 再 --confirm)",
            f"Step {STEP['grant']} 未完成 / 无项目授权": f"请 Owner 跑第 {STEP['grant']} 步签项目授权后再跑第 {STEP['advisor']} 步",
            "--dry-run 的 plan 非空": "再跑一次不带 --dry-run 的 lybra sync --harness-root <治理根> 后复核; 仍非空 = 报 bug, 附 --dry-run --json 输出",
            "章程落点已有非 Lybra 渲染物": "会话目录里已有同名文件(非 Lybra 渲染): 移走它或换会话目录后重跑 lybra sync(不覆盖用户文件)",
        },
        "creates": "无(只读复核)",
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
    common = ["--gate-url", _shell_quote(gate), "--ttl", "86400", "--actor", _shell_quote(inst["advisor"])]
    enroll_on_fail = {
        "no usable role token": f"第 {STEP['advisor']} 步未完成(治理根无顾问凭据); 请 Owner 先跑第 {STEP['advisor']} 步",
        "UNKNOWN_GOVERNANCE_ROOT": f"门找不到项目; 核对门与第 {STEP['project']} 步同一 home 根",
        "ENROLL_ENVELOPE_MISSING / 推导 owner_policy_ref 失败": (f"第 {STEP['grant']} 步项目授权未签或不覆盖该实例(实例名主机段须在授权主机内); "
                                                          f"核对 lybra project grant show {pq} --home-root {hq}(预检拒 = 未签码)"),
        "401 Unauthorized": "门拒绝顾问凭据或码(发码门 ≠ 本门); 核对 connection.json mcp.rpc_url 后重跑本步(失败时码已吊销)",
        "已回滚 / 回滚失败": "接入失败时注册码已经门吊销(输出 '已回滚'); 回滚失败按输出出口手工吊销后重跑",
    }

    def _enroll_pair(role: str, instance: str, target: str, extra: list[str]) -> list[str]:
        auth = ["--owner-authorization-ref", _shell_quote(auth_ref), "--reason", _shell_quote(f"Onboarding {project_name} {instance}")]
        return [render_enroll_deliver_command(adv_roles, role=role, instance=_shell_quote(instance), governance_root=gq,
                                              workspace=target, extra=[*extra, *common, *auth], phase=phase) for phase in ("--dry-run", "--confirm")]

    def _workstation_step(number: int, role: str, instance: str, ws: str, label_ws: str, duty: str) -> None:
        pair = _enroll_pair(role, instance, _shell_path(ws), ["--executor-mode", station_mode])
        steps.append({
            "step_number": number,
            "actor": "advisor",
            "owner_action": None,
            "title": f"接入{' ' if label_ws[:1].isascii() else ''}{label_ws}(pi, {station_mode} 模式): {instance} @ {ws}",
            "commands": pair,
            "command": "\n".join([f"# {label_ws}: 先 --dry-run 预演(角色 / 实例 / 治理根 / 落点 / 覆盖授权, 不签码零写入)再 --confirm;"
                                  f" 注册码只在本进程内; 凭据落工位 .lybra + .pi 接线(owner_policy_ref 由项目授权 {auth_ref} 推导)", *pair]),
            "purpose": (f"{duty}。顾问用自己的凭据(治理根 .lybra/connection.json, projects=[{project_name}])在治理根所在机一条命令签码并就地兑换;"
                        f" 工位目录 {ws}(来源: {ws_source}), 一个目录只登记给一个实例"),
            "check": ("--dry-run 输出 '预览(未执行; 加 --confirm 执行)' 与覆盖授权; --confirm 输出 '已执行'、code_id 与指纹(无码原文)与 "
                      "'role(含 owner_policy_ref)'; 工位 .lybra/connection.json 含 lybra_bin 且 workspace_root == governance_root"),
            "on_fail": {
                **enroll_on_fail,
                ".pi 接线缺失": f"接入落 .pi 接线, 第 {STEP['sync']} 步 sync 补挂声明内缺失挂载; 仍缺 = 报 bug(附 sync --json 输出)",
                "connection.json 缺 lybra_bin": "接入按运行中的 lybra 部署位写入; 用部署的 lybra 命令重跑本步",
                "workspace_root 写成 harness root": "workspace_root 须等于码内治理根; 重跑本步校正",
            },
            "creates": f"{ws}/.lybra + .pi + AGENTS.md; 治理根接入登记 land 事件(workstation={ws})",
        })

    # ── Step 6: 审计工位(独立 pi; 审计恒不随执行方式变化) ──
    _workstation_step(STEP["auditor"], audit_role, inst["auditor"], audit_ws, "审计工位",
                      f"审计恒为独立 pi 审计工位 {inst['auditor']}(不审自己做的卡)")
    # ── Step 7: pi 执行工位(非代码卡一律 pi, AIPOS-F256) ──
    _workstation_step(STEP["pi_executor"], exec_role, inst["pi_executor"], exec_ws, "pi 执行工位",
                      f"非代码卡(docs / governance / config / validation)一律由 pi 执行工位 {inst['pi_executor']} 做(loop 按授权拉起 pi, 或 Owner 在工位 /go)")

    # ── Step 8: 子 agent 执行者(代码卡; 顾问会话派生, 凭据落治理根, 无工位) ──
    host_args = ["--harness-host", _shell_quote(adv_h["host"])] if adv_h["host"] else []
    pair8 = _enroll_pair(exec_role, inst["executor"], gq, ["--executor-mode", exec_mode, "--harness", adv_h["kind"], *host_args])
    steps.append({
        "step_number": STEP["subagent_executor"],
        "actor": "advisor",
        "owner_action": None,
        "title": f"接入子 agent 执行者({exec_mode} 模式, 顾问会话 {adv_h['kind']} 派生): {inst['executor']} @ 治理根",
        "commands": pair8,
        "command": "\n".join([f"# 子 agent 执行者: 凭据落治理根 .lybra/connection.json(按实例条目), harness = 顾问会话 {adv_h['kind']}; "
                              "无工位、不 sync、不起 pi(代码卡由顾问按 lybra kickoff 开工稿派子 agent 以此身份交付)", *pair8]),
        "purpose": (f"代码卡的执行身份 {inst['executor']}(实例名主机段 = 顾问会话所在机): 凭据与治理席位同落治理根 {gov_s}, land 事件记 "
                    f"mode={exec_mode} harness={adv_h['kind']}(无 workstation= 段 = 无工位位置, 不可派 pi); 与 pi 执行工位 {inst['pi_executor']} 不同名、不同目录"),
        "check": "--dry-run 输出 '预览(未执行; 加 --confirm 执行)'; --confirm 输出 '已执行' 与 land 事件(mode=subagent); 治理根下不新增 .pi/",
        "on_fail": {
            **enroll_on_fail,
            "EXECUTOR_MODE_HARNESS": f"子 agent 模式须给 --harness <顾问会话 kind>(本向导 = {adv_h['kind']})且不给 --harness-dir; 按本步命令原样重跑",
            "EXECUTOR_MODE_TARGET": f"子 agent 模式凭据须落治理根: --workspace 须为 {gov_s}",
        },
        "creates": f"{gov_s}/.lybra/connection.json 子 agent 执行者条目 + land 事件(mode={exec_mode}, harness={adv_h['kind']})",
    })

    # ── Step 9: 声明执行分工(三者齐)+ 复核 ──
    set_exec = [_cmd("lybra", "project", "set-execution", "--workspace-root", gq, "--subagent-executor", _shell_quote(inst["executor"]),
                     "--pi-executor", _shell_quote(inst["pi_executor"]), "--auditor", _shell_quote(inst["auditor"]), *phase)
                for phase in (("--dry-run",), ("--actor", _shell_quote(_actor), "--confirm"))]
    division = {"code": inst["executor"], "noncode": inst["pi_executor"], "audit": inst["auditor"]}
    step9 = [
        "# 声明执行分工(AIPOS-F143 / F256, 产品按卡类型强制): 代码卡 = 子 agent 执行者, 非代码卡一律 pi 执行工位, 审计 = 独立 pi 审计工位;"
        " 起草缺省与发卡核身份同读此声明(先预演再写入并落账)",
        *set_exec,
        "# 复核: 三实例都已接入(✓)且「执行分工」一段显示 pi 执行工位",
        _cmd("lybra", "roles", "--workspace-root", gq, "list"),
    ]
    steps.append({
        "step_number": STEP["execution"],
        "actor": "advisor",
        "owner_action": None,
        "title": "声明执行分工(project set-execution, 三者齐)并复核",
        "command": "\n".join(step9),
        "purpose": (f"一次声明本项目谁执行什么: 代码卡 → 子 agent 执行者 {division['code']}; 非代码卡 → pi 执行工位 {division['noncode']}; "
                    f"审计 → pi 审计工位 {division['audit']}。写前核身份(各实例须为本项目已接入实例, 审计者与 pi 执行者须有工位位置)"),
        "check": ("set-execution --dry-run 给 project.json diff(零写入), --confirm 写入并落账('落账: verdict=PASS'); roles list 列出三实例 ✓ 且"
                  "「执行分工」行 = 'pi 执行工位 = <pi 执行工位实例>'"),
        "on_fail": {
            "实例未接入 / 无工位位置": f"回第 {STEP['auditor']}–{STEP['subagent_executor']} 步接入对应实例后重跑本步",
            "auditor 与执行者相同": "审计者须是独立 pi 审计工位实例, 不得等于任一执行者",
            **LANDING_ON_FAIL,
        },
        "creates": f"{gov_s}/project.json#execution",
        "division": division,
    })

    # ── Step 10: 工位 sync 分发 + 稳态复核 + 工位自检 ──
    step10: list[str] = []
    for label10, ws in (("pi 执行", exec_ws), ("审计", audit_ws)):
        wq = _shell_path(ws)
        sync_ws = f"lybra sync --harness-root {wq} --workspace-root {gq}"
        step10 += [f"# {label10}工位: 按分发声明落齐工位件 → 稳态复核 → 工位自检(只读, 缺项逐项点名)", sync_ws, f"{sync_ws} --dry-run",
                   _cmd("lybra", "onboarding", "check", pq, "--step", str(STEP["sync"]), "--home-root", hq, "--workspace-dir", wq)]
    step10 += ["# 工位设置现状(只读; 三实例登记、工位章程新鲜度与执行分工)", _cmd("lybra", "project", "workstation-plan", pq, "--home-root", hq)]
    steps.append({
        "step_number": STEP["sync"],
        "actor": "advisor",
        "owner_action": None,
        "title": "工位 sync 分发 + 稳态复核 + 工位自检",
        "command": "\n".join(step10),
        "purpose": (f"pi 执行工位 {exec_ws} 与审计工位 {audit_ws} 按分发声明落齐技能 / 扩展 / 章程, 复核稳态并自检可启动最小集"
                    "(子 agent 执行者无工位, 不 sync); 工位只同步分发, 不敲门动词"),
        "check": (f"sync 工位行 synced(首个工位会补挂 .pi/extensions/go.ts); --dry-run 输出 'up-to-date: 0 file(s) to fetch/render'、无 would-prune 行; "
                  f"onboarding check 输出 '✓ Step {STEP['sync']} prerequisites satisfied'; workstation-plan 现状三实例已接入、工位章程最新"),
        "on_fail": {
            "sync 把工位记为 skipped": f"--workspace-root 须为本项目治理根 {gov_s}",
            "--dry-run 的 plan 或 prune 非空": "再跑一次不带 --dry-run 的 sync 后复核; 仍非空 = 报 bug, 附 --dry-run --json 输出",
            "未完成 enroll, 拒绝写入": f"该工位未接入: 回第 {STEP['auditor']} / {STEP['pi_executor']} 步为它 enroll-deliver",
            "自检缺项": (f"按输出缺项逐项修复(lybra_bin 悬空 → 重跑对应接入步; owner_policy_ref 缺失 → 核对第 {STEP['grant']} 步授权覆盖本工位后重跑 sync)"),
        },
        "creates": f"{exec_ws}, {audit_ws} 的工位件, 工位父根 _distributed/",
    })

    # ── Step 11: 首卡(顾问, 产品命令; 执行按分工) ──
    step11 = [
        *card_policy_lines(gov_s, _actor),  # AIPOS-F252 件①: 首卡前声明卡号形状(值 = Owner 确认的本项目卡号正则, 无内置默认)
        "# 发卡: 卡稿 JSON 写 project / task_mode / lane.repo(第 2 步仓名) / audit 等(执行者 / harness / 审计者按第 9 步执行分工缺省); 校验后发布到 pending",
        _cmd(lybra_command_prefix(gov_s), "draft", "create", "--from-json", DRAFT_JSON_PLACEHOLDER),
        _cmd(lybra_command_prefix(gov_s), "draft", "publish", "--path", DRAFT_PATH_PLACEHOLDER),
        f"# 推进: lybra loop 自动找到项目授权 {auth_ref}(不带信封)一段式认领(建工作树)→ 等交回 → 派审 → 等审计报告 → 裁决 → finalize → 结案 → N6 落账",
        _cmd("lybra", "loop", "--task-id", TASK_ID_PLACEHOLDER, "--workspace-root", gq),
        f"# 代码卡: loop 提示派子 agent 时, 顾问取自包含开工稿, 在顾问会话里派生子 agent(以 {inst['executor']} 身份)照稿执行; 子 agent 写完 RETURN 即停",
        _cmd(lybra_command_prefix(gov_s), "kickoff", "--task-id", TASK_ID_PLACEHOLDER),
        f"# 非代码卡(及审计卡): loop 按授权在 pi 执行工位 / 审计工位自动拉起 pi; 授权未放行拉起时, Owner 在该工位手工开工(审计工位同法, cd {_shell_path(audit_ws)}):",
        f"cd {_shell_path(exec_ws)}",
        "pi",
        "/go",
    ]
    steps.append({
        "step_number": STEP["first_card"],
        "actor": "advisor",
        "owner_action": None,
        "title": "首卡: 发卡 → lybra loop 推进到结案(代码卡派子 agent / 非代码卡 pi 执行工位)",
        "command": "\n".join(step11),
        "purpose": (f"顾问只用产品命令推进: lybra loop 以项目授权 {auth_ref} 驱动整张卡; 执行按第 {STEP['execution']} 步分工——代码卡由顾问 lybra kickoff "
                    f"派子 agent, 非代码卡由 loop 在 pi 执行工位 {exec_ws} 拉起 pi(或 Owner 在工位 /go); 审计由 pi 审计工位 {audit_ws} 做。"
                    "执行体 / 审计体只写产物(RETURN / 审计报告), 门动作全由产品做"),
        "check": "lybra loop 退出码 0 且输出 'done: <卡ID> 已结案(closure 记录存在), 治理已落账'(结案后 loop 自动执行 N6 落账: lybra governance-commit --task-id)",
        "on_fail": {
            "exit 5(无有效授权)": f"第 {STEP['grant']} 步授权未签或不覆盖本卡(车道 / 任务类型 / 主机); 按输出的扩大授权命令请 Owner 签",
            "exit 3(等待超时)": "执行体 / 审计体尚未交回产物: 代码卡确认子 agent 已派出, 非代码卡确认 pi 已拉起或已 /go, 之后重跑同一条 lybra loop",
            "看进度(loop 跑在后台 / 久无动静)": f"lybra loop status --task-id {TASK_ID_PLACEHOLDER} --workspace-root {gq}"
                                                "(读运行记录 + 探活 + 判停滞: running/stalled/launch_dead/loop_dead/ended, 含日志路径); "
                                                "禁 tail/grep 原始日志自判",
            "exit 2(门拒)": "照输出原文处理; loop 已回读门生记录, 不会重复执行已落步骤",
            "exit 2(落账拒)": "N6 落账被拒(他人暂存 / 护栏 / 推送未完成): 照输出原文处理后重跑同一条 lybra loop(落账幂等)",
            "/go 报 工作树尚未建立": "认领与建树由驱动方完成, 工位不自领; 等 lybra loop 认领后再 /go",
        },
        "creates": "卡在 completed 队列 + claim/return/dispatch/verdict/finalization/closure 记录, 且已提交并推送治理仓",
    })

    owner_steps = [s["step_number"] for s in steps if s.get("owner_action")]
    repo_names = _repo_names(repos, code_repo, project_name)
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
        "summary": (
            f"从 0 接新项目 {project_name} 全流程({len(steps)} 步, 零手工编辑; Owner 一次性动作 = Step {' / '.join(map(str, owner_steps))}):\n"
            + "\n".join(f"  Step {s['step_number']}: {s['title']}" for s in steps)
        ),
    }


def card_policy_lines(governance_root: str, actor: str) -> list[str]:
    """AIPOS-F252 件①: 接入流程的卡号形状声明行(向导第 9 步首卡前 / 人肉期接入第 1 步同一渲染): 说明 + show(看建议值) + 预演 + 确认。
    命令原文唯一渲染 card_policy_loader.set_task_id_pattern_command / show_command(缺声明报错 / project upgrade 待补项同一出处); 值不内置默认。"""
    from tools.card_policy_loader import set_task_id_pattern_command, show_command

    base = set_task_id_pattern_command(governance_root)
    return [
        "# 卡号形状(task_id_pattern, AIPOS-F252): 受控发布 / finalize / lybra-deploy 的归属解析只读它, 产品无内置默认; "
        "show 给出按现有卡号前缀推出的建议值(仅建议), 值经 Owner 确认后先预演再 --confirm(写治理根 card_policy.json 并落账)",
        show_command(governance_root),
        f"{base} --actor {_shell_quote(actor)} --dry-run",
        f"{base} --actor {_shell_quote(actor)} --confirm",
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
        meta_lines = ["# project.json 说明键(" + " / ".join(f.lstrip("-") for f in meta_flags) + ")同样经产品写入口更新(人肉期旧文免手改), 先预演再 --confirm"]
        meta_args = [part for flag in meta_flags for part in (flag, META_TEXT_PLACEHOLDER)]
        meta_lines += [_cmd("lybra", "project", "set-meta", pq, "--home-root", hq, *meta_args, phase) for phase in ("--dry-run", "--confirm")]
    else:
        meta_lines = ["# project.json 说明键写入口 lybra project set-meta: 待 config.schema project_json 声明这些键后可用(未声明 = 拒 META_KEY_UNDECLARED)"]
    return [
        {
            "order": 1,
            "title": "落点声明: project.json paths 经产品写入口声明(免手改)",
            "actor": "advisor",
            "command": "\n".join([
                "# 键名与取值按 config.schema project_json.paths 校验(未知键拒 / finalize_mode 枚举); 先 --dry-run 看 diff, 再 --confirm",
                _cmd("lybra", "project", "set-paths", pq, "--home-root", hq, "--key", PATHS_KEY_PLACEHOLDER, "--value",
                     PATHS_VALUE_PLACEHOLDER, "--dry-run"),
                _cmd("lybra", "project", "set-paths", pq, "--home-root", hq, "--key", PATHS_KEY_PLACEHOLDER, "--value",
                     PATHS_VALUE_PLACEHOLDER, "--confirm"),
                # AIPOS-F145 件③: 既有项目声明阶段档案位置(finalize 阶段闸门按此落点核阶段快照)
                f"# 阶段档案(阶段快照)不在缺省位置时同样声明: --key {STAGE_ARCHIVE_PATH_KEY} --value <相对治理根路径>"
                "(未声明 = 缺省落点; 落点下无快照 = finalize 阶段闸门拒)",
                *meta_lines,
                *card_policy_lines(governance_root, actor),
            ]),
            "purpose": ("为什么先做: 后两步都按声明落点工作——冻结按 queue_root 找卡, 收编按 return_root 写报告骨架、loop 按声明找产物; "
                        "落点未声明时取缺省值, 人肉期项目的真实落点未必是缺省, 骨架会落错位置。"
                        "人肉期项目的 Return/裁决/队列/授权落点逐项声明(可多对 --key/--value; 与缺省相同也显式声明: "
                        "收编要求 config.schema project_json.paths.adoption.required_declared_keys 已显式声明, 否则拒 ADOPT_PATHS_UNDECLARED)。"
                        f"阶段档案(阶段快照)若不在缺省位置, 一并声明 {STAGE_ARCHIVE_PATH_KEY}(相对治理根): Lybra 内部 finalize 的阶段闸门按此落点核"
                        "阶段快照, 未声明而缺省位置无快照 = 拒(AIPOS-F145)。"
                        "预演首行标明目标项目与 project.json 绝对路径, 核对无误再 --confirm; 在目标项目治理根下运行可省项目名"
                        "(目标 = 该根 project.json#project, 解析不出即拒, 绝不回落 home 级活动项目; 显式项目名与所在治理根不一致 = 拒)"),
        },
        {
            "order": 2,
            "title": "存量冻结: 人肉期已结束/不再推进的卡一次声明为历史(迁移基线)",
            "actor": "advisor",
            "command": "\n".join([
                "# 先 --dry-run 核对将冻结的卡与各卡 lint 问题计数, 无误后改 --confirm(清单只追加; 解冻 = --unfreeze)",
                _cmd("lybra", "project", "freeze-legacy", "--queue", "claimed,completed,blocked", "--exclude", TASK_ID_PLACEHOLDER,
                     "--reason", FREEZE_REASON_PLACEHOLDER, "--actor", actor, "--workspace-root", gq, "--dry-run"),
            ]),
            "purpose": ("为什么在收编之前: 收编认冻结(冻结卡拒 ADOPT_LEGACY_FROZEN), 先把不再推进的卡一次声明为历史, "
                        "仍在途、要继续推进的卡用 --exclude 留出(交给下一步收编); 冻结卡不被 state lint 报 ERROR、不被 next 当待推进, 门拒对其写动作"),
        },
        {
            "order": 3,
            "title": "在途卡收编: 门补铸 claim 记录、绑定既有分支、建卡工作树",
            "actor": "advisor",
            "command": "\n".join([
                "# 每张在途卡: 先预览(零写入, 含授权判定与全部拒因), 无拒因后改 --confirm; 收编后与新卡同样只用 lybra loop 推进",
                _cmd(lybra_command_prefix(governance_root), "queue", "adopt", "--task-id", TASK_ID_PLACEHOLDER, "--branch",
                     LEGACY_BRANCH_PLACEHOLDER, "--actor", actor, "--owner-policy-ref", authorization_ref, "--dry-run"),
                _cmd(lybra_command_prefix(governance_root), "queue", "adopt", "--task-id", TASK_ID_PLACEHOLDER, "--branch",
                     LEGACY_BRANCH_PLACEHOLDER, "--actor", actor, "--owner-policy-ref", authorization_ref, "--confirm"),
                _cmd("lybra", "loop", "--task-id", TASK_ID_PLACEHOLDER, "--workspace-root", gq),
            ]),
            "purpose": ("为什么最后: 收编写报告骨架与 claim 记录, 依赖前两步(落点已声明、历史卡已冻结)。"
                        "--branch = 该卡产物所在的既有分支(卡 lane.repo 仓内); 卡分支名与之不同时卡分支建在其 tip 上。"
                        "拒因(声明 transitions nodes.N1.adoption.guards + config.schema project_json.paths.adoption.guards)一次列全: "
                        "卡面意图必填缺项 / 分支不存在 / 已有门生 claim / 已冻结 / 落点未声明 / 卡分支分叉 / 建树失败 / 授权不覆盖。"
                        "已有手写 Return 不被覆盖: loop 按交回判据要求补 commit_sha / tree_hash / branch, 拒因原文给出补法"),
        },
    ]


def format_guide_text(guide: dict[str, Any]) -> str:
    """将结构化 guide 格式化为可读文本(终端输出用)。"""
    lines: list[str] = []
    lines.append(f"═══ 从 0 接新项目: {guide['project_name']} ═══")
    lines.append(f"home 根: {guide['home_root']}(来源: {guide.get('home_root_source', '?')})")
    lines.append(f"治理根: {guide.get('governance_root', '')}")
    lines.append(f"门地址: {guide['gate_url']}(来源: {guide.get('gate_url_source', '?')})")
    cred = guide.get("owner_credential") or {}
    lines.append(f"Owner 凭据: {guide.get('owner_connection_json', '')}(来源: {cred.get('source', '?')})")
    if cred and not cred.get("resolved"):
        lines.append(f"  ⚠ Owner 凭据未推导出: {cred.get('reason')}")
    auth = guide.get("authorization") or {}
    if auth:
        lines.append(f"项目常设授权: {auth.get('authorization_id')}(第 {STEP['grant']} 步签; 之后各接入命令的授权引用都是它)")
    ws = guide.get("workstations") or {}
    lines.append(f"工位目录: pi 执行工位 {ws.get('pi_executor')} · 审计工位 {ws.get('auditor')}(来源: {guide.get('workstations_source', '?')})")
    div = guide.get("division") or {}
    if div:
        lines.append(f"执行分工: 代码卡 → 子 agent 执行者 {div.get('code')} · 非代码卡 → pi 执行工位 {div.get('noncode')} · "
                     f"审计 → pi 审计工位 {div.get('audit')}")
    lines.append(f"生成时间: {guide['generated_at']}")
    owner = guide.get("owner_actions") or []
    lines.append(f"共 {guide['total_steps']} 步, 全程零手工编辑; Owner 一次性动作 {len(owner)} 条: " + ", ".join(f"Step {n}" for n in owner))
    lines.append("")

    for step in guide["steps"]:
        who = {"owner": "Owner", "advisor": "顾问", "workstation": "工位"}.get(step.get("actor", ""), step.get("actor", ""))
        lines.append(f"── Step {step['step_number']}: {step['title']} [执行方: {who}] ──")
        lines.append(f"目的: {step['purpose']}")
        lines.append("")

        if "commands" in step and len(step["commands"]) > 1:
            lines.append("命令(逐条执行):")
            for i, cmd in enumerate(step["commands"], 1):
                lines.append(f"  {i}. {cmd}")
        else:
            lines.append("命令:")
            for cmd_line in step["command"].split("\n"):
                lines.append(f"  {cmd_line}")

        lines.append("")
        lines.append(f"验证: {step['check']}")

        if step.get("on_fail"):
            lines.append("失败出口:")
            for err, fix in step["on_fail"].items():
                lines.append(f"  [{err}] → {fix}")

        if step.get("note"):
            lines.append(f"注: {step['note']}")

        lines.append(f"产物: {step['creates']}")
        lines.append("")

    lines.append("═══ 完成: 从项目注册到首卡结案, 零手工编辑 ═══")
    on_demand = guide.get("on_demand") or []
    if on_demand:
        lines.append("")
        lines.append("═══ 按需声明(只列, 不强制; 每条先预演, 确认 = 同参改 --confirm) ═══")
        for item in on_demand:
            lines.append(f"── {item['title']} ──")
            lines.append(f"用途: {item['purpose']}")
            lines.append(f"何时需要: {item['when']}")
            lines.append("命令:")
            for cmd_line in item["commands"]:
                lines.append(f"  {cmd_line}")
            lines.append("")
    legacy = guide.get("legacy_onboarding") or []
    if legacy:
        lines.append("")
        lines.append("═══ 附: 接入既有人肉项目(人肉期已有卡与记录; 顺序 = 落点声明 → 冻结 → 收编) ═══")
        for item in legacy:
            lines.append(f"── {item['order']}. {item['title']} ──")
            lines.append(f"目的: {item['purpose']}")
            lines.append("命令:")
            for cmd_line in str(item["command"]).split("\n"):
                lines.append(f"  {cmd_line}")
            lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 前置自检(lybra onboarding check; 步号与上面 guide 同一编号)
# ---------------------------------------------------------------------------

def validate_step_prerequisites(
    step_number: int,
    *,
    project_name: str,
    home_root: str | None = None,
    workspace_dir: str | None = None,
) -> dict[str, Any]:
    """验证进入某一步之前的产物是否齐(负夹具 / 运行时前置检查)。步号 = generate_onboarding_guide 的步号(唯一声明 STEP)。

    ≥项目: 项目根 + project.json; ≥授权步: 产品仓声明; ≥顾问接入步: 项目常设授权(或兼容旧信封); ≥顾问复核步: 顾问凭据(治理根 .lybra/connection.json);
    ≥工位 sync 步: 工位 connection.json(lybra_bin / workspace_root==governance_root)+ .pi 接线 + role#owner_policy_ref(工位接入步)。
    AIPOS-F262: 授权先于接入(项目常设授权, AIPOS-F224), 顾问 / 工位接入 = enroll-deliver。工位目录缺省 = workstation_dirs 的 pi 执行工位。
    返回 {ok: bool, missing: [str], guidance: [str]}。
    """
    from tools.aipos_cli.workspace_config import resolve_home_root

    home = resolve_home_root(explicit_root=home_root)
    project_root = home / project_name

    missing: list[str] = []
    guidance: list[str] = []

    if step_number >= STEP["project"]:
        if not project_root.is_dir():
            missing.append("project_root")
            guidance.append(f"Step {STEP['project']} 未完成: 项目目录不存在 {project_root}; 先跑 lybra project new")
        elif not (project_root / "project.json").is_file():
            missing.append("project.json")
            guidance.append(f"project.json 缺失; 重跑 Step {STEP['project']}")

    if step_number >= STEP["grant"] and (project_root / "project.json").is_file():
        from tools.aipos_cli.workspace_config import CardRepoUnresolved, project_repos

        try:
            repos = project_repos(project_root)
            if not repos["declared"] and repos["code_repo"] is None:
                missing.append("repos")
                guidance.append(f"Step {STEP['repos']} 未完成: project.json 无产品仓声明; 跑 lybra project set-repos")
        except CardRepoUnresolved as exc:
            missing.append("repos")
            guidance.append(f"产品仓声明不合规: {exc}; 重跑 Step {STEP['repos']}")

    if step_number >= STEP["advisor"] and (project_root / "project.json").is_file():
        # AIPOS-F262 件①: 授权 = 项目常设授权(autonomy_policy.authorization_ids, 声明 authorizations_root); 兼容: 仍用旧信封的项目(policy_ids)不报缺
        from tools.aipos_cli.autonomy_policy import authorization_ids, policy_ids

        if not authorization_ids(project_root) and not policy_ids(project_root):
            missing.append("authorization")
            guidance.append(f"Step {STEP['grant']} 未完成: 无项目常设授权; Owner 跑 Step {STEP['grant']} 的 lybra project grant sign(先预演再 --confirm)")

    # AIPOS-F106 件④: connection.json 定位经 service_mode.connection_path(既有唯一定位); role 只经 charter_render.workstation_identity 读
    from tools.aipos_cli.service_mode import connection_path as _connection_path

    if step_number >= STEP["advisor_check"] and project_root.is_dir():
        if not _connection_path(project_root).is_file():
            missing.append("advisor_credential")
            guidance.append(f"Step {STEP['advisor']} 未完成: 治理根无 .lybra/connection.json(顾问凭据); Owner 跑 Step {STEP['advisor']} 的 "
                            "lybra roles enroll-deliver(先 --dry-run 再 --confirm)")

    if step_number >= STEP["sync"]:
        if workspace_dir:
            ws = Path(workspace_dir).expanduser()
        else:
            try:
                ws = Path(workstation_dirs(project_root, project_name, home)["executor"]).expanduser()
            except ValueError as exc:
                return {"ok": False, "missing": [*missing, "workspace_dir"], "guidance": [*guidance, f"{exc}; 用 --workspace-dir 指明工位目录"]}
        conn_json = _connection_path(ws)
        if not conn_json.is_file():
            missing.append("connection.json")
            guidance.append(f"工位未接入: {conn_json} 不存在; 跑工位接入步(Step {STEP['auditor']} / {STEP['pi_executor']})的 lybra roles enroll-deliver(先 --dry-run 再 --confirm)")
        else:
            try:
                data = json.loads(conn_json.read_text(encoding="utf-8"))
                if not data.get("lybra_bin"):
                    missing.append("lybra_bin")
                    guidance.append(f"connection.json 缺 lybra_bin; 重跑工位接入步(Step {STEP['auditor']} / {STEP['pi_executor']}) enroll-deliver")
                ws_root = data.get("workspace_root", "")
                gov_root = data.get("governance_root", "")
                if ws_root and gov_root and Path(ws_root).resolve() != Path(gov_root).resolve():
                    missing.append("workspace_root_mismatch")
                    guidance.append(f"workspace_root != governance_root; 重跑工位接入步(Step {STEP['auditor']} / {STEP['pi_executor']}) enroll-deliver")
            except (OSError, json.JSONDecodeError):
                missing.append("connection.json_invalid")
                guidance.append(f"connection.json 格式错误; 重跑工位接入步(Step {STEP['auditor']} / {STEP['pi_executor']})")
        if not (ws / ".pi" / "settings.json").is_file():
            missing.append(".pi/settings.json")
            guidance.append(f".pi 接线缺失; 工位接入应自动落, 重跑 Step {STEP['sync']} 的 sync(仍缺再重跑工位接入步(Step {STEP['auditor']} / {STEP['pi_executor']}))")
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
                guidance.append(f"{exc}; 重跑工位接入步(Step {STEP['auditor']} / {STEP['pi_executor']})")
            else:
                if ident["role"]["source"] != ".lybra/role":
                    missing.append("role_invalid")
                    guidance.append(f"role 文件格式错误; 重跑工位接入步(Step {STEP['auditor']} / {STEP['pi_executor']})")
                elif ident["owner_policy_ref"]["source"] != ".lybra/role":
                    missing.append("owner_policy_ref")
                    guidance.append(f"role 文件缺 owner_policy_ref; 核对 Step {STEP['grant']} 项目授权是否覆盖本工位实例(主机段)后重跑 lybra sync")

    return {
        "ok": not missing,
        "missing": missing,
        "guidance": guidance,
    }
