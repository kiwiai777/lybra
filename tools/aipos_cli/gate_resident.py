"""AIPOS-F263 件②: 门常驻安装(Linux systemd --user / macOS launchd)——`lybra serve install|uninstall` 与 `serve status` 常驻一节。

声明唯一出处 config.schema gate_resident(单元名 / 管理标记 / 环境文件 / 各平台落点、模板、命令); 本模块只渲染与执行声明:
  - plan_install: 预演(零写入): 将写文件全文与路径、将执行命令、同名拒、手写单元提示
  - install: --confirm 写文件 → commands.install → 回读 commands.status
  - uninstall: 只删带 managed_marker 的本命令生成文件(先 commands.uninstall)
  - resident_status: 只读(setup check 门常驻项 / serve status 同读)
  - unit_environment: 只读解析已装单元的环境(systemd Environment= / launchd EnvironmentVariables; AIPOS-F268 件②)
单元目录从环境(HOME / XDG_CONFIG_HOME)推导, 不假设主机; 不提权、不写系统路径。
AIPOS-F268 件①: 单元带安装者当前 PATH(声明 environment.PATH = {install_path}); 预演逐项列出 PATH 目录并标出 pi / node / git
解析到的目录是否在内, 安装者 PATH 里找不到 = 提示; 已装单元 PATH 与当前不一致 = 给「重装以刷新」出口。
"""
from __future__ import annotations

import os
import plistlib
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

from tools.aipos_cli._cli_entry_guard import check_direct_invocation

check_direct_invocation(__name__)

_DECL_KEYS = ("unit_name", "managed_marker", "config_home", "environment_file", "environment", "module_argv",
              "handwritten_detect", "platforms", "path_check")
_PATH_CHECK_KEYS = ("tools", "missing_note", "empty_note", "drift_note", "refresh_exit")
_PLATFORM_KEYS = ("kind", "unit_dir", "file_name", "suffix", "commands", "status_active")


class GateResidentError(ValueError):
    """声明缺 / 形坏 / 平台不支持(fail-closed)。"""


def gate_resident_declaration() -> dict[str, Any]:
    """config.schema gate_resident(唯一读取口)。缺键 = GateResidentError。"""
    from tools.schema_loader import code_repo_schema_root, load_schema

    decl = load_schema("config", code_repo_schema_root()).get("gate_resident")
    if not isinstance(decl, dict) or any(k not in decl for k in _DECL_KEYS):
        raise GateResidentError(f"config.schema.json gate_resident 未声明齐 {list(_DECL_KEYS)}")
    if not isinstance(decl["path_check"], dict) or any(k not in decl["path_check"] for k in _PATH_CHECK_KEYS):
        raise GateResidentError(f"config.schema.json gate_resident.path_check 未声明齐 {list(_PATH_CHECK_KEYS)}")
    for name, plat in (decl.get("platforms") or {}).items():
        if not isinstance(plat, dict) or any(k not in plat for k in _PLATFORM_KEYS):
            raise GateResidentError(f"config.schema.json gate_resident.platforms.{name} 未声明齐 {list(_PLATFORM_KEYS)}")
    return decl


def current_platform() -> str:
    """本机平台键(gate_resident.platforms 的键): linux / darwin / 其余原样(= 不支持, 由调用方拒)。"""
    return "linux" if sys.platform.startswith("linux") else sys.platform


def _fill(template: str, values: Mapping[str, str]) -> str:
    out = str(template)
    for key, val in values.items():
        out = out.replace("{" + key + "}", str(val))
    return out


def _base_values(decl: dict[str, Any], env: Mapping[str, str]) -> dict[str, str]:
    home = str(env.get("HOME") or Path.home())
    cfg = decl["config_home"]
    config_home = str(env.get(str(cfg["env"])) or "").strip() or _fill(cfg["default"], {"home": home})
    # AIPOS-F268 件①: 安装者当前 PATH 原样(不写死目录); 调用方给的 env 无 PATH = 空(该环境项不写进单元)
    values = {"home": home, "config_home": config_home, "unit_name": str(decl["unit_name"]),
              "install_path": str(env.get("PATH") or "")}
    values["environment_file"] = _fill(decl["environment_file"], values)
    return values


def platform_paths(*, platform: str | None = None, env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """该平台的单元目录 / 文件名 / 全路径 + 声明(只算路径, 不碰文件)。平台未声明 = GateResidentError。"""
    decl = gate_resident_declaration()
    plat_name = platform or current_platform()
    plat = (decl["platforms"] or {}).get(plat_name)
    if plat is None:
        raise GateResidentError(f"门常驻不支持本平台 {plat_name!r}(config.schema gate_resident.platforms 已声明: "
                                f"{sorted(decl['platforms'])}); 出口: 前台 `lybra serve start`")
    values = _base_values(decl, env if env is not None else os.environ)
    values["file_name"] = _fill(plat["file_name"], values)
    unit_dir = Path(_fill(plat["unit_dir"], values))
    values["unit_path"] = str(unit_dir / values["file_name"])
    values["uid"] = str(os.getuid()) if hasattr(os, "getuid") else "0"
    if plat.get("log_dir"):
        values["log_dir"] = _fill(plat["log_dir"], values)
    return {"platform": plat_name, "decl": decl, "plat": plat, "values": values, "unit_dir": unit_dir,
            "unit_path": unit_dir / values["file_name"]}


def install_command(workspace_root: str | Path, phase: str) -> str:
    """`lybra serve --workspace-root <根> install --dry-run|--confirm` 命令式样唯一渲染(向导起门步 / setup check 出口同读)。"""
    return shlex.join(["lybra", "serve", "--workspace-root", str(workspace_root), "install", f"--{phase}"])


def lybra_exec_argv(workspace_root: Path, *, connection_json: str | None = None, start_args: list[str] | None = None) -> list[str]:
    """ExecStart / ProgramArguments 指向的当前 lybra: 本进程解释器 + module_argv + serve … start。"""
    decl = gate_resident_declaration()
    argv = [sys.executable, *[str(a) for a in decl["module_argv"]], "serve", "--workspace-root", str(workspace_root)]
    if connection_json:
        argv += ["--connection-json", str(Path(connection_json).expanduser())]
    return argv + ["start", *(start_args or [])]


def current_code_root() -> Path:
    """单元里的代码根: 部署快照运行 = 部署目录的 current 链接(随部署切换, 不钉死某版本); 其余 = 运行代码根。"""
    from tools.aipos_cli.deploy_runtime import CURRENT_LINKNAME, running_code

    rc = running_code()
    if rc.mode == "deploy" and rc.deploy_dir is not None:
        return rc.deploy_dir / CURRENT_LINKNAME
    return Path(rc.code_root)


def _systemd_quote(arg: str, *, exec_line: bool = False) -> str:
    """systemd 单元值转义: % → %%; ExecStart 里 $ → $$(防变量展开); 含空白 / 引号 / 反斜杠 / ; = 双引号包裹并转义。"""
    s = str(arg).replace("%", "%%")
    if exec_line:
        s = s.replace("$", "$$")
    if s and not re.search(r'[\s"\'\\;]', s):
        return s
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_unit(*, workspace_root: Path, home_root: str, code_root: Path, exec_argv: list[str],
                platform: str | None = None, env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """渲染单元文件全文(纯函数: 不写文件、不跑命令)。返回 {platform, path, text, values}。"""
    pp = platform_paths(platform=platform, env=env)
    decl, plat, values = pp["decl"], pp["plat"], dict(pp["values"])
    values.update({"marker": str(decl["managed_marker"]), "workspace_root": str(workspace_root), "home_root": str(home_root),
                   "code_root": str(code_root)})
    rendered = {k: _fill(v, values) for k, v in decl["environment"].items()}
    env_map = {k: v for k, v in rendered.items() if v}
    skipped_env = [k for k, v in rendered.items() if not v]  # 渲染为空 = 不写(空 PATH 写进单元比服务管理器缺省更糟)
    if plat["kind"] == "systemd_user":
        sd = {k: v.replace("%", "%%") for k, v in values.items()}
        sd["environment_lines"] = "\n".join(f"Environment={_systemd_quote(f'{k}={v}')}" for k, v in env_map.items())
        sd["exec_start"] = " ".join(_systemd_quote(a, exec_line=True) for a in exec_argv)
        text = "\n".join(_fill(line, sd) for line in plat["template"]) + "\n"
    elif plat["kind"] == "launchd":
        values["exec_start"] = shlex.join(exec_argv)
        wrap = _fill(plat["env_file_wrapper"], {"environment_file": shlex.quote(values["environment_file"]),
                                                "exec_start": values["exec_start"]})
        values["wrapped_exec"] = wrap

        def fill_obj(obj: Any) -> Any:
            if isinstance(obj, str):
                return env_map if obj == "{environment}" else _fill(obj, values)
            if isinstance(obj, list):
                return [fill_obj(x) for x in obj]
            if isinstance(obj, dict):
                return {k: fill_obj(v) for k, v in obj.items()}
            return obj

        body = plistlib.dumps(fill_obj(plat["plist"]), sort_keys=False).decode("utf-8")
        if "--" in values["marker"]:
            raise GateResidentError("config.schema gate_resident.managed_marker 不得含 \"--\"(XML 注释禁用)")
        marker = "<!-- " + values["marker"] + " -->"
        head, sep, rest = body.partition("<plist")
        text = f"{head}{marker}\n{sep}{rest}"
    else:
        raise GateResidentError(f"gate_resident.platforms.{pp['platform']}.kind {plat['kind']!r} 未实现")
    return {"platform": pp["platform"], "path": str(pp["unit_path"]), "text": text, "values": values, "environment": env_map,
            "skipped_env": skipped_env}


def _exec_line_of(path: Path, kind: str) -> str | None:
    """他名单元文件的启动命令一行(systemd ExecStart 续行合并; launchd ProgramArguments 连成一行); 读不出 = None。"""
    try:
        if kind == "launchd":
            data = plistlib.loads(path.read_bytes())
            args = data.get("ProgramArguments") if isinstance(data, dict) else None
            return " ".join(str(a) for a in args) if isinstance(args, list) else None
        text = path.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError, plistlib.InvalidFileException):
        return None
    joined = re.sub(r"\\\n", " ", text)
    lines = [ln.split("=", 1)[1] for ln in joined.splitlines() if ln.strip().startswith("ExecStart=")]
    return " ".join(lines) if lines else None


def unit_environment(path: Path, kind: str) -> dict[str, str] | None:
    """AIPOS-F268 件②: 已装单元文件里写的环境(只读解析; setup check / install 预演同读)。
    systemd: Environment= 行(续行合并, 按 systemd 引号规则拆 KEY=VALUE, %% → %); launchd: plist EnvironmentVariables。
    文件读不出 / 形坏 = None(调用方按「读不出」报, 不当作空环境)。"""
    try:
        if kind == "launchd":
            data = plistlib.loads(path.read_bytes())
            env = data.get("EnvironmentVariables") if isinstance(data, dict) else None
            if env is None:
                return {}
            return {str(k): str(v) for k, v in env.items()} if isinstance(env, dict) else None
        text = path.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError, plistlib.InvalidFileException):
        return None
    out: dict[str, str] = {}
    for line in re.sub(r"\\\n", " ", text).splitlines():
        stripped = line.strip()
        if not stripped.startswith("Environment="):
            continue
        try:
            tokens = shlex.split(stripped.split("=", 1)[1], comments=False, posix=True)
        except ValueError:
            return None
        for token in tokens:
            key, sep, val = token.partition("=")
            if sep and key:
                out[key] = val.replace("%%", "%")
    return out


def _path_entries(value: str) -> list[str]:
    return [p for p in str(value or "").split(os.pathsep) if p]


def path_contains(path_value: str | None, directory: str) -> bool:
    """directory 是否在 PATH 值的目录里(按规范化路径比, 符号链接解析后相同也算)。"""
    want = {os.path.normpath(directory), os.path.realpath(directory)}
    return any(os.path.normpath(e) in want or os.path.realpath(e) in want for e in _path_entries(path_value or ""))


def path_tool_report(install_path: str, unit_path_value: str | None = None) -> list[dict[str, Any]]:
    """path_check.tools 各自在安装者 PATH 中解析到的路径 / 目录, 及该目录是否在单元 PATH(缺省 = 将写入的 install_path)内。"""
    from tools.aipos_cli.setup_check import setup_check_declaration, tool_executable

    decl = gate_resident_declaration()
    sc_decl = setup_check_declaration()
    target = install_path if unit_path_value is None else unit_path_value
    rows = []
    for tool in decl["path_check"]["tools"]:
        exe = tool_executable(sc_decl, str(tool))
        found = shutil.which(exe, path=install_path) if install_path else None
        directory = os.path.dirname(found) if found else None
        rows.append({"tool": str(tool), "executable": exe, "resolved": found, "dir": directory,
                     "in_unit_path": bool(directory) and path_contains(target, directory)})
    return rows


def handwritten_units(*, platform: str | None = None, env: Mapping[str, str] | None = None) -> list[dict[str, str]]:
    """单元目录下其他名、启动命令含 `serve … start` 的手写常驻单元(只读)。"""
    pp = platform_paths(platform=platform, env=env)
    pattern = re.compile(str(pp["decl"]["handwritten_detect"]["pattern"]))
    unit_dir: Path = pp["unit_dir"]
    if not unit_dir.is_dir():
        return []
    found: list[dict[str, str]] = []
    for path in sorted(unit_dir.glob("*" + str(pp["plat"]["suffix"]))):
        if path.name == pp["values"]["file_name"] or not path.is_file():
            continue
        line = _exec_line_of(path, pp["plat"]["kind"])
        if line and pattern.search(line):
            found.append({"name": path.name, "path": str(path), "exec": " ".join(line.split())[:300]})
    return found


def _run(argv: list[str], values: Mapping[str, str], env: Mapping[str, str] | None) -> dict[str, Any]:
    cmd = [_fill(a, values) for a in argv]
    try:
        proc = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60,
                              env=dict(env) if env is not None else None)
    except FileNotFoundError as exc:
        return {"argv": cmd, "returncode": 127, "stdout": "", "stderr": f"{cmd[0]} 不在 PATH: {exc}"}
    except subprocess.TimeoutExpired:
        return {"argv": cmd, "returncode": 124, "stdout": "", "stderr": "超时(60s)"}
    return {"argv": cmd, "returncode": proc.returncode, "stdout": (proc.stdout or "").strip()[-2000:],
            "stderr": (proc.stderr or "").strip()[-2000:]}


def _is_managed(path: Path, marker: str) -> bool:
    try:
        return marker in path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


def _active(result: dict[str, Any], plat: dict[str, Any]) -> bool:
    rule = plat["status_active"]
    out = str(result.get("stdout") or "")
    if "stdout_equals" in rule:
        return result.get("returncode") == 0 and out.strip() == str(rule["stdout_equals"])
    return result.get("returncode") == 0 and str(rule.get("stdout_contains") or "") in out


def resident_status(*, platform: str | None = None, env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """门常驻现状(只读): 本命令单元是否在、是否本命令生成、是否在跑(仅单元在时跑 commands.status); 手写单元清单。
    平台不支持 = supported False(不抛)。"""
    try:
        pp = platform_paths(platform=platform, env=env)
    except GateResidentError as exc:
        return {"supported": False, "platform": platform or current_platform(), "installed": False, "managed": False,
                "active": None, "unit_path": None, "handwritten": [], "detail": str(exc)}
    unit_path: Path = pp["unit_path"]
    installed = unit_path.is_file()
    managed = installed and _is_managed(unit_path, str(pp["decl"]["managed_marker"]))
    status = _run(pp["plat"]["commands"]["status"], pp["values"], env) if installed else None
    return {"supported": True, "platform": pp["platform"], "kind": pp["plat"]["kind"], "unit_path": str(unit_path),
            "installed": installed, "managed": managed, "active": _active(status, pp["plat"]) if status else None,
            "status_output": status, "handwritten": handwritten_units(platform=pp["platform"], env=env),
            "unit_environment": unit_environment(unit_path, str(pp["plat"]["kind"])) if installed else None}


def plan_install(workspace_root: Path, *, connection_json: str | None = None, start_args: list[str] | None = None,
                 platform: str | None = None, env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """预演(零写入): 将写的文件与全文、将执行的命令、拒因(同名 / 手写单元)与提示。"""
    from tools.aipos_cli.workspace_config import resolve_home_root_with_source

    ws = Path(workspace_root).expanduser().resolve()
    home_root, home_source = resolve_home_root_with_source()
    code_root = current_code_root()
    unit = render_unit(workspace_root=ws, home_root=str(home_root), code_root=code_root,
                       exec_argv=lybra_exec_argv(ws, connection_json=connection_json, start_args=start_args),
                       platform=platform, env=env)
    pp = platform_paths(platform=unit["platform"], env=env)
    path = Path(unit["path"])
    refusals: list[dict[str, str]] = []
    notes: list[str] = []
    pc = pp["decl"]["path_check"]
    install_path = unit["values"]["install_path"]
    for key in unit["skipped_env"]:
        notes.append(_fill(pc["empty_note"], {"key": key}))
    path_tools = path_tool_report(install_path)
    for row in path_tools:
        if not row["resolved"]:
            notes.append(_fill(pc["missing_note"], {"tool": row["executable"]}))
    refresh: dict[str, Any] | None = None
    if path.exists():
        if _is_managed(path, str(pp["decl"]["managed_marker"])):
            installed_env = unit_environment(path, str(pp["plat"]["kind"]))
            unit_value = (installed_env or {}).get("PATH")
            exit_line = f"要改参数: 先 `lybra serve uninstall --confirm` 再重装; 只看现状: `lybra serve status`"
            if installed_env is None or unit_value != unit["environment"].get("PATH"):
                shown = "读不出" if installed_env is None else (unit_value if unit_value is not None else "未写 PATH")
                refresh = {"unit_value": unit_value, "current_value": unit["environment"].get("PATH"),
                           "exit": _fill(pc["refresh_exit"], {"install_confirm": install_command(ws, "confirm")}),
                           "unit_tools": path_tool_report(install_path, unit_value or "")}
                notes.append(_fill(pc["drift_note"], {"unit_path": str(path), "unit_value": shown,
                                                      "current_value": unit["environment"].get("PATH") or "(空)"}))
                exit_line = refresh["exit"]
            refusals.append({"code": "UNIT_EXISTS_MANAGED",
                             "reason": f"已存在同名单元 {path}(本命令先前生成), 不覆盖",
                             "exit": exit_line})
        else:
            refusals.append({"code": "UNIT_EXISTS_FOREIGN",
                             "reason": f"已存在同名文件 {path}, 不是本命令生成(无管理标记), 不覆盖、不接管",
                             "exit": "保留它(手写常驻继续用), 或自行停用并移走该文件后再装"})
    hand = handwritten_units(platform=unit["platform"], env=env)
    for h in hand:
        notes.append(f"检测到已有手写常驻单元 {h['name']}({h['path']}), 本命令不接管")
    if hand:
        refusals.append({"code": "HANDWRITTEN_UNIT_PRESENT",
                         "reason": "单元目录已有手写常驻门单元: " + ", ".join(h["name"] for h in hand)
                                   + "; 再装一个会与其争同一端口, --confirm 拒",
                         "exit": "保留手写单元(门已常驻, 无需本命令), 或先停用并移走它后再 install --confirm"})
    commands = [[_fill(a, unit["values"]) for a in argv] for argv in pp["plat"]["commands"]["install"]]
    status_cmd = [_fill(a, unit["values"]) for a in pp["plat"]["commands"]["status"]]
    return {"operation": "serve_install", "platform": unit["platform"], "kind": pp["plat"]["kind"], "unit_path": unit["path"],
            "unit_text": unit["text"], "workspace_root": str(ws), "home_root": str(home_root), "home_root_source": home_source,
            "code_root": str(code_root), "environment_file": unit["values"]["environment_file"],
            "commands": commands, "status_command": status_cmd, "refusals": refusals, "notes": notes, "handwritten": hand,
            "log_dir": unit["values"].get("log_dir"), "unit_env_path": unit["environment"].get("PATH"),
            "path_entries": _path_entries(unit["environment"].get("PATH") or ""), "path_tools": path_tools, "path_refresh": refresh}


def install(workspace_root: Path, *, confirm: bool, connection_json: str | None = None, start_args: list[str] | None = None,
            platform: str | None = None, env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """dry-run(缺省)= plan_install; --confirm = 无拒因才写文件 → commands.install → 回读 commands.status。"""
    plan = plan_install(workspace_root, connection_json=connection_json, start_args=start_args, platform=platform, env=env)
    plan["phase"] = "confirm" if confirm else "dry_run"
    if not confirm:
        plan["ok"] = not plan["refusals"]
        return plan
    if plan["refusals"]:
        plan["ok"] = False
        return plan
    path = Path(plan["unit_path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    if plan.get("log_dir"):
        Path(plan["log_dir"]).mkdir(parents=True, exist_ok=True)
    with open(path, "x", encoding="utf-8") as fh:  # 'x' = 期间被他人建同名 = FileExistsError(不覆盖)
        fh.write(plan["unit_text"])
    os.chmod(path, 0o644)
    plan["written"] = str(path)
    pp = platform_paths(platform=plan["platform"], env=env)
    ran = []
    for argv in pp["plat"]["commands"]["install"]:
        res = _run(argv, pp["values"], env)
        ran.append(res)
        if res["returncode"] != 0:
            break
    plan["ran"] = ran
    failed = next((r for r in ran if r["returncode"] != 0), None)
    status = _run(pp["plat"]["commands"]["status"], pp["values"], env)
    plan["status_output"] = status
    plan["active"] = _active(status, pp["plat"])
    if failed:
        plan["ok"] = False
        plan["refusals"].append({"code": "SERVICE_COMMAND_FAILED",
                                 "reason": f"`{shlex.join(failed['argv'])}` exit {failed['returncode']}: {failed['stderr'] or failed['stdout']}",
                                 "exit": f"单元文件已写 {path}; 按上文报错修好后 `lybra serve uninstall --confirm` 清理再重装"})
    else:
        plan["ok"] = True
    return plan


def uninstall(*, confirm: bool, platform: str | None = None, env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """只删本命令生成(带管理标记)的单元; 先 commands.uninstall 停用, 删文件后 after_remove。"""
    pp = platform_paths(platform=platform, env=env)
    path: Path = pp["unit_path"]
    commands = [[_fill(a, pp["values"]) for a in argv] for argv in pp["plat"]["commands"]["uninstall"]]
    after = [[_fill(a, pp["values"]) for a in argv] for argv in pp["plat"]["commands"]["after_remove"]]
    result: dict[str, Any] = {"operation": "serve_uninstall", "phase": "confirm" if confirm else "dry_run", "platform": pp["platform"],
                              "unit_path": str(path), "commands": commands + after, "refusals": [], "notes": []}
    if not path.exists():
        result.update(ok=True, installed=False)
        result["notes"].append(f"未安装: {path} 不存在, 无事可做")
        return result
    result["installed"] = True
    if not _is_managed(path, str(pp["decl"]["managed_marker"])):
        result["ok"] = False
        result["refusals"].append({"code": "UNIT_NOT_MANAGED", "reason": f"{path} 不是本命令生成(无管理标记), 不删",
                                   "exit": "手写单元请自行停用与移除"})
        return result
    if not confirm:
        result["ok"] = True
        return result
    ran = []
    for argv in pp["plat"]["commands"]["uninstall"]:
        ran.append(_run(argv, pp["values"], env))
    path.unlink()
    for argv in pp["plat"]["commands"]["after_remove"]:
        ran.append(_run(argv, pp["values"], env))
    result["ran"] = ran
    result["removed"] = str(path)
    bad = [r for r in ran if r["returncode"] != 0]
    if bad:
        result["notes"].append("停用命令有非零退出(文件已删): " + "; ".join(f"`{shlex.join(r['argv'])}` exit {r['returncode']}" for r in bad))
    result["ok"] = True
    return result


def render_resident_report(report: dict[str, Any]) -> str:
    """install / uninstall 人读唯一出口。"""
    op, phase = report.get("operation"), report.get("phase")
    lines = [f"[{op} · {'预演(零写入)' if phase == 'dry_run' else '确认'}] 平台 {report.get('platform')}  单元 {report.get('unit_path')}"]
    if op == "serve_install":
        lines.append(f"工作区(门 --workspace-root): {report.get('workspace_root')}")
        lines.append(f"home 根(LYBRA_HOME_ROOT): {report.get('home_root')}(来源: {report.get('home_root_source')})")
        lines.append(f"代码根: {report.get('code_root')}")
        lines.append(f"环境文件(可选, 只引用不创建): {report.get('environment_file')}")
        if phase == "dry_run":
            lines.extend(_render_path_section(report))
            lines.append(f"── 将写入 {report.get('unit_path')} 全文 ──")
            lines.extend(str(report.get("unit_text") or "").rstrip("\n").splitlines())
            lines.append("── 全文完 ──")
    cmds = report.get("commands") or []
    if cmds and phase == "dry_run":
        lines.append("将执行的命令:")
        lines.extend(f"  {shlex.join(c)}" for c in cmds)
    if op == "serve_install" and phase == "dry_run" and report.get("status_command"):
        lines.append(f"确认后回读状态: {shlex.join(report['status_command'])}")
    for note in report.get("notes") or []:
        lines.append(f"⚠ {note}")
    for item in report.get("ran") or []:
        lines.append(f"  $ {shlex.join(item['argv'])} → exit {item['returncode']}" + (f": {item['stderr']}" if item["stderr"] else ""))
    if op == "serve_install" and phase == "confirm" and report.get("status_output"):
        st = report["status_output"]
        lines.append(f"回读状态: `{shlex.join(st['argv'])}` → exit {st['returncode']} {st['stdout'][:200]}"
                     + ("(在跑)" if report.get("active") else "(未在跑)"))
    for ref in report.get("refusals") or []:
        lines.append(f"✗ 拒 {ref['code']}: {ref['reason']}")
        lines.append(f"  出口: {ref['exit']}")
    if not report.get("refusals"):
        if op == "serve_install" and phase == "dry_run":
            lines.append("预演无拒因; 确认写入并启用: 同一命令改 --confirm")
        elif op == "serve_uninstall" and phase == "dry_run" and report.get("installed"):
            lines.append("预演无拒因; 确认停用并删除: 同一命令改 --confirm")
    return "\n".join(lines)


def _render_path_section(report: dict[str, Any]) -> list[str]:
    """AIPOS-F268 件①: 预演里的单元 PATH 一节(逐项目录 + pi / node / git 解析到的目录是否在内; 已装单元 PATH 不一致另列)。"""
    entries = report.get("path_entries") or []
    if report.get("unit_env_path"):
        lines = [f"单元 PATH(取自安装者当前 PATH, 共 {len(entries)} 项, 原样写入):"]
        lines.extend(f"  {i}. {entry}" for i, entry in enumerate(entries, 1))
    else:
        lines = ["单元 PATH: 不写(安装者 PATH 为空, 门进程沿用服务管理器缺省)"]
    for row in report.get("path_tools") or []:
        if row["resolved"]:
            mark = "在单元 PATH 内 ✓" if row["in_unit_path"] else "不在单元 PATH 内 ⚠"
            lines.append(f"  {row['executable']} → {row['resolved']}(目录 {row['dir']}: {mark})")
        else:
            lines.append(f"  {row['executable']} → 安装者 PATH 里找不到 ⚠")
    refresh = report.get("path_refresh")
    if refresh:
        lines.append(f"已装单元 PATH: {refresh['unit_value'] if refresh['unit_value'] is not None else '(未写 PATH)'}")
        for row in refresh.get("unit_tools") or []:
            if row["resolved"]:
                lines.append(f"  {row['executable']} 目录 {row['dir']}: "
                             + ("在已装单元 PATH 内 ✓" if row["in_unit_path"] else "不在已装单元 PATH 内 ⚠"))
        lines.append(f"出口(重装以刷新): {refresh['exit']}")
    return lines


def render_resident_status_line(status: dict[str, Any]) -> str:
    """serve status 常驻一节(一行 + 手写单元)。"""
    if not status.get("supported"):
        return f"门常驻: 本平台不支持({status.get('detail')})"
    if status.get("installed"):
        state = "在跑" if status.get("active") else "未在跑"
        owner = "本命令生成" if status.get("managed") else "同名但非本命令生成"
        line = f"门常驻: 已安装 {status['unit_path']}({owner}; {state})"
    else:
        line = f"门常驻: 未安装({status['unit_path']} 不存在)"
    extra = [f"  已有手写常驻单元 {h['name']}({h['path']}), 本命令不接管" for h in status.get("handwritten") or []]
    return "\n".join([line, *extra])
