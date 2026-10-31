"""AIPOS-F257(产品化 P1 发布面)— npm 安装包: 版本单源 + 打包清单校验 + 干净 HOME 装包冒烟 + 发布前自检(唯一实现)。

声明唯一来源 = schema/distribution.schema.json npm_package(本模块 package_declaration 唯一读取; 缺键 / 形坏 = PackageCheckError):
- 件② 版本单源: version_source(package.json version)是唯一版本源; mirrors(pyproject.toml / package-lock.json)只校验一致
  (declared_version / version_mirror_problems); `lybra --version` 经 deploy_runtime.version_line 读 declared_version。
- 件① 清单校验: check_files(包内文件清单 vs 源文件清单 vs 声明)判 required(paths / trees / distributions 源)缺件与
  forbidden(测试文件 = workspace_config.is_test_file 同一判定 + globs + 非 git 跟踪件)禁入件; check_pack 对真实 npm pack 产物
  (tarball 解包)跑 check_files 并按 transitions artifact_ingest.credential_guard 同一判定(artifact_ingest.credential_hits)扫凭据。
- 件③ 冒烟: smoke_install 把 tarball 装进临时 prefix, 以全新环境(临时 HOME, 无 LYBRA_* / AIPOS_*)逐步跑声明的 lybra 命令。
- 件⑤ 自检: preflight 依次跑上述三项 + 全量测试, 末行一行结论; 不执行 npm publish、不登录 registry。

入口: `python3 -m tools.aipos_cli.package_check {version|manifest|smoke|preflight}`(package.json scripts.preflight 指向 preflight)。
本模块只用标准库 + 既有唯一实现(schema_loader / workspace_config / artifact_ingest), 零新增运行时依赖。
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path
from typing import Any

DECLARATION_KEY = "npm_package"
TARBALL_PREFIX = "package/"


class PackageCheckError(RuntimeError):
    """声明缺 / 形坏、npm 不可用、打包或安装失败(fail-closed: 调用方如实报 FAIL, 不当通过)。"""


# ---------------------------------------------------------------------------
# 声明
# ---------------------------------------------------------------------------

def package_declaration(repo_root: Path | None = None) -> dict[str, Any]:
    """distribution.schema npm_package(唯一读取口)。缺节 / 缺必需键 = PackageCheckError(点名缺什么)。"""
    from tools.schema_loader import SchemaLoadError, load_schema

    try:
        schema = load_schema("distribution", repo_root)
    except SchemaLoadError as exc:
        raise PackageCheckError(f"distribution.schema 读不出: {exc}") from exc
    decl = schema.get(DECLARATION_KEY)
    where = f"distribution.schema.json {DECLARATION_KEY}"
    if not isinstance(decl, dict):
        raise PackageCheckError(f"{where} 未声明")
    need = {"version_source": ("file", "key_path", "semver_pattern", "mirrors"),
            "required": ("paths", "trees", "from_distributions", "npm_omitted_basenames"),
            "forbidden": ("globs", "untracked_is_forbidden", "credential_scan", "credential_scan_skips_code_files"),
            "smoke": ("project", "actor", "steps", "step_timeout_seconds", "install_timeout_seconds"),
            "preflight": ("suite_command", "pass_line", "suite_timeout_seconds")}
    missing = [f"{sec}.{key}" for sec, keys in need.items() for key in keys if not isinstance(decl.get(sec), dict) or key not in decl[sec]]
    if missing:
        raise PackageCheckError(f"{where} 缺键: {', '.join(missing)}")
    return decl


def _code_root() -> Path:
    from tools.schema_loader import code_repo_schema_root

    return Path(code_repo_schema_root())


# ---------------------------------------------------------------------------
# 件② 版本单源
# ---------------------------------------------------------------------------

def _json_key(path: Path, key_path: list[str]) -> Any:
    try:
        node: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PackageCheckError(f"{path} 读不出: {exc}") from exc
    for key in key_path:
        if not isinstance(node, dict) or key not in node:
            raise PackageCheckError(f"{path} 无键 {'.'.join(repr(k) if k == '' else k for k in key_path)}")
        node = node[key]
    return node


def _toml_project_version(path: Path) -> str:
    """pyproject.toml [project] 表里的 version = "…"(只认这一种写法; Python 3.10 无 tomllib, 不引依赖)。"""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise PackageCheckError(f"{path} 读不出: {exc}") from exc
    table = ""
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            table = stripped.strip("[]").strip()
            continue
        match = re.match(r'version\s*=\s*"([^"]*)"\s*(#.*)?$', stripped)
        if table == "project" and match:
            return match.group(1)
    raise PackageCheckError(f"{path} [project] 表无 version = \"…\"")


def declared_version(repo_root: Path | None = None, decl: dict[str, Any] | None = None) -> str:
    """包版本(唯一版本源 version_source; 非 semver = PackageCheckError)。"""
    root = Path(repo_root) if repo_root is not None else _code_root()
    src = (decl or package_declaration())["version_source"]
    value = _json_key(root / str(src["file"]), [str(k) for k in src["key_path"]])
    if not isinstance(value, str) or not re.fullmatch(str(src["semver_pattern"]), value):
        raise PackageCheckError(f"{src['file']} 版本 {value!r} 不是 semver(version_source.semver_pattern)")
    return value


def version_mirror_problems(repo_root: Path | None = None, decl: dict[str, Any] | None = None) -> list[str]:
    """各 mirror 与版本源不一致 / 读不出 = 一条问题(点名文件与键); 全一致 = []。"""
    root = Path(repo_root) if repo_root is not None else _code_root()
    d = decl or package_declaration()
    version = declared_version(root, d)
    problems: list[str] = []
    for mirror in d["version_source"]["mirrors"]:
        path, keys = root / str(mirror["file"]), [str(k) for k in mirror["key_path"]]
        try:
            got = _toml_project_version(path) if mirror.get("format") == "toml_project_version" else _json_key(path, keys)
        except PackageCheckError as exc:
            problems.append(str(exc))
            continue
        if got != version:
            problems.append(f"{mirror['file']} {'.'.join(repr(k) if k == '' else k for k in keys)} = {got!r} ≠ 版本源 "
                            f"{d['version_source']['file']} {version!r}")
    return problems


# ---------------------------------------------------------------------------
# 件① 打包清单校验
# ---------------------------------------------------------------------------

def source_files(repo_root: Path, decl: dict[str, Any]) -> tuple[list[str], str]:
    """源文件清单(相对路径)与依据: git 仓 = git ls-files(git 跟踪); 否则(如部署快照)= 磁盘上全部文件(禁入件除外)。"""
    root = Path(repo_root)
    proc = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True, text=True, timeout=60)
    if proc.returncode == 0 and (root / ".git").exists():
        return sorted(p for p in proc.stdout.split("\0") if p), "git"
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root)
        dirnames[:] = [d for d in dirnames if not _forbidden_glob((rel_dir / d).as_posix() + "/x", decl)]
        found += [(rel_dir / f).as_posix() for f in filenames]
    return sorted(found), "filesystem"


def _forbidden_glob(rel: str, decl: dict[str, Any]) -> str | None:
    for pattern in decl["forbidden"]["globs"]:
        if fnmatch.fnmatchcase(rel, str(pattern)):
            return str(pattern)
    return None


def forbidden_reason(rel: str, decl: dict[str, Any]) -> str | None:
    """包内路径是否禁入: 测试文件(workspace_config.is_test_file 缺省约定, 与 run-all 发现同一判定)/ 命中 forbidden.globs; 否 = None。"""
    from tools.aipos_cli.workspace_config import default_test_contract, is_test_file

    if is_test_file(rel, default_test_contract()):
        return "测试文件(test_file_globs)"
    pattern = _forbidden_glob(rel, decl)
    return f"禁入式样 {pattern}" if pattern else None


def _distribution_sources(decl_root: Path | None) -> list[str]:
    from tools.schema_loader import load_schema

    return sorted({str(d["source"]["path"]).rstrip("/") for d in load_schema("distribution", decl_root).get("distributions", [])
                   if isinstance(d, dict) and isinstance(d.get("source"), dict) and d["source"].get("path")})


def check_files(packed: list[str], sources: list[str], decl: dict[str, Any], *, source_basis: str = "git",
                distribution_sources: list[str] | None = None) -> dict[str, Any]:
    """件① 判定本体(纯函数): packed = 包内相对路径, sources = 源文件清单。返回 {ok, missing, forbidden, untracked, tops, files}。"""
    req = decl["required"]
    pack_set, src_set = set(packed), set(sources)
    omitted = {str(n) for n in req["npm_omitted_basenames"]}
    missing: list[dict[str, str]] = [{"path": p, "why": "required.paths"} for p in req["paths"] if p not in pack_set]
    for tree in req["trees"]:
        prefix = str(tree).rstrip("/") + "/"
        for rel in sorted(s for s in src_set if s.startswith(prefix)):
            if (rel not in pack_set and rel.rsplit("/", 1)[-1] not in omitted and forbidden_reason(rel, decl) is None
                    and all(m["path"] != rel for m in missing)):
                missing.append({"path": rel, "why": f"required.trees {tree}"})
    if req["from_distributions"]:
        for src in distribution_sources if distribution_sources is not None else _distribution_sources(None):
            if src not in pack_set and not any(p.startswith(src + "/") for p in pack_set):
                missing.append({"path": src, "why": "distributions[].source.path"})
    forbidden = [{"path": p, "why": why} for p in sorted(pack_set) if (why := forbidden_reason(p, decl))]
    untracked = (sorted(p for p in pack_set if p not in src_set)
                 if decl["forbidden"]["untracked_is_forbidden"] and source_basis == "git" else [])
    tops: dict[str, int] = {}
    for p in packed:
        tops[p.split("/", 1)[0]] = tops.get(p.split("/", 1)[0], 0) + 1
    return {"ok": not (missing or forbidden or untracked), "missing": missing, "forbidden": forbidden, "untracked": untracked,
            "tops": dict(sorted(tops.items())), "files": len(pack_set), "source_basis": source_basis}


def _npm_env(home: Path, extra_path: list[str] | None = None) -> dict[str, str]:
    """npm 子进程环境: 只给 PATH(声明式最小集)与临时 HOME(npm 缓存 / 日志落临时目录, 不碰真实 ~/.npm), 不带 LYBRA_* / AIPOS_*。"""
    return {"PATH": os.pathsep.join([*(extra_path or []), "/usr/bin", "/bin"]), "HOME": str(home), "LANG": "C.UTF-8",
            "npm_config_update_notifier": "false", "npm_config_fund": "false", "npm_config_audit": "false"}


def _tool_bin(work: Path) -> Path:
    """临时目录里 node / npm / python3 的符号链接(干净 PATH 只经它们找到工具, 不把真实 ~/.local/bin 等整目录放进 PATH)。"""
    tb = work / "toolbin"
    tb.mkdir(parents=True, exist_ok=True)
    found = {"node": shutil.which("node"), "npm": shutil.which("npm"), "python3": sys.executable or shutil.which("python3")}
    lacking = [name for name, path in found.items() if not path]
    if lacking:
        raise PackageCheckError(f"本机缺 {', '.join(lacking)}(装包冒烟需要 node / npm / python3)")
    for name, path in found.items():
        link = tb / name
        if not link.exists():
            link.symlink_to(os.path.realpath(str(path)))
    return tb


def pack(repo_root: Path, dest: Path, work: Path) -> Path:
    """npm pack 到 dest(只在本地打包, 不发布), 返回 tarball 路径。失败 = PackageCheckError(带 npm 输出尾部)。"""
    dest.mkdir(parents=True, exist_ok=True)
    home = work / "npm-home"
    home.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(["npm", "pack", "--pack-destination", str(dest), "--json", "--silent"], cwd=str(repo_root),
                          env=_npm_env(home, [str(_tool_bin(work))]), capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        raise PackageCheckError(f"npm pack 失败(exit {proc.returncode}): {(proc.stdout + proc.stderr)[-1500:]}")
    try:
        name = json.loads(proc.stdout)[0]["filename"]
    except (ValueError, LookupError, TypeError) as exc:
        raise PackageCheckError(f"npm pack --json 输出读不出: {proc.stdout[-500:]}") from exc
    tarball = dest / str(name).replace("@", "").replace("/", "-")
    if not tarball.is_file():
        raise PackageCheckError(f"npm pack 报告的产物 {tarball} 不存在")
    return tarball


def tarball_files(tarball: Path) -> list[str]:
    with tarfile.open(tarball, "r:gz") as tf:
        names = [m.name for m in tf.getmembers() if m.isfile()]
    bad = [n for n in names if not n.startswith(TARBALL_PREFIX)]
    if bad:
        raise PackageCheckError(f"{tarball} 含 {TARBALL_PREFIX} 之外的条目: {bad[:5]}")
    return sorted(n[len(TARBALL_PREFIX):] for n in names)


def _credential_scan_tree(tree: Path, files: list[str], *, skip_code_files: bool) -> tuple[list[dict[str, Any]], int]:
    """包内文本按 transitions artifact_ingest.credential_guard 同一判定扫描(credential_hits; 只报位置与种类, 不报值)。
    skip_code_files = 跳过代码文件(governance_guardrails.is_code_file 唯一判据)。返回 (命中, 扫描文件数)。"""
    from tools.aipos_cli.artifact_ingest import _credential_text, credential_guard_declaration, credential_hits
    from tools.aipos_cli.governance_guardrails import is_code_file, load_guardrail_declarations

    decl = credential_guard_declaration()
    if decl is None:
        raise PackageCheckError("transitions artifact_ingest.credential_guard 未声明, 包内凭据扫描无判据(fail-closed)")
    code_decls = load_guardrail_declarations() if skip_code_files else None
    hits: list[dict[str, Any]] = []
    scanned = 0
    for rel in files:
        if code_decls is not None and is_code_file(rel, code_decls):
            continue
        text = _credential_text(tree / rel, decl["scan"])
        if text is None:
            continue
        scanned += 1
        hits += [{"path": rel, **h} for h in credential_hits(text, decl)]
    return hits, scanned


def check_pack(repo_root: Path, tarball: Path, work: Path, decl: dict[str, Any] | None = None) -> dict[str, Any]:
    """件①: 真实 npm pack 产物 → check_files + 凭据扫描。返回 check_files 结果 + {credential_hits, files_scanned, tarball}。"""
    d = decl or package_declaration()
    packed = tarball_files(tarball)
    sources, basis = source_files(repo_root, d)
    result = check_files(packed, sources, d, source_basis=basis, distribution_sources=_distribution_sources(Path(repo_root)))
    hits: list[dict[str, Any]] = []
    scanned = 0
    if d["forbidden"]["credential_scan"]:
        unpacked = work / "unpacked"
        with tarfile.open(tarball, "r:gz") as tf:
            # 解包只为扫文本; data 过滤(拒绝越界路径 / 设备文件)在支持的解释器上启用
            tf.extractall(unpacked, **({"filter": "data"} if hasattr(tarfile, "data_filter") else {}))
        hits, scanned = _credential_scan_tree(unpacked / TARBALL_PREFIX.rstrip("/"), packed,
                                              skip_code_files=bool(d["forbidden"]["credential_scan_skips_code_files"]))
    result.update(credential_hits=hits, credential_scanned=scanned, tarball=str(tarball), ok=result["ok"] and not hits)
    return result


def manifest_summary(result: dict[str, Any]) -> str:
    """件① 一段人读结论(清单校验与自检共用)。"""
    head = (f"包内 {result['files']} 个文件; 顶层 " + ", ".join(f"{k} {v}" for k, v in result["tops"].items())
            + f"; 源清单依据 {result['source_basis']}")
    if "credential_scanned" in result:
        head += f"; 凭据扫描 {result['credential_scanned']} 个文本文件(代码文件除外), 命中 {len(result['credential_hits'])}"
    lines = [("✓ " if result["ok"] else "✗ ") + "打包清单校验: " + head]
    lines += [f"  缺件 {m['path']}({m['why']})" for m in result["missing"]]
    lines += [f"  禁入件 {f['path']}({f['why']})" for f in result["forbidden"]]
    lines += [f"  非 git 跟踪件 {u}" for u in result["untracked"]]
    lines += [f"  凭据命中 {h['path']}:{h['line']}({h['kind']})" for h in result.get("credential_hits", [])]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 件③ 干净 HOME 装包冒烟
# ---------------------------------------------------------------------------

def _fill(text: str, values: dict[str, str]) -> str:
    for key, value in values.items():
        text = text.replace("{" + key + "}", value)
    return text


def smoke_install(tarball: Path, work: Path, decl: dict[str, Any] | None = None, *, version: str | None = None,
                  log_dir: Path | None = None) -> dict[str, Any]:
    """件③: tarball 装进 <work>/prefix(npm install -g --offline), 全新环境逐步跑声明 smoke.steps。
    返回 {ok, version, install_seconds, seconds, steps: [{name, argv, rc, ok, seconds, problems, output}], env_keys, prefix}。
    日志(每步原文)写 log_dir(缺省 <work>/smoke-logs)。"""
    d = decl or package_declaration()
    smoke = d["smoke"]
    started = time.monotonic()
    work = Path(work)
    home, prefix, cwd = work / "home", work / "prefix", work / "cwd"
    home_root = work / "home-root"
    for p in (home, prefix, cwd, home_root):
        p.mkdir(parents=True, exist_ok=True)
    logs = Path(log_dir) if log_dir is not None else work / "smoke-logs"
    logs.mkdir(parents=True, exist_ok=True)
    tool_bin = _tool_bin(work)
    env = _npm_env(home, [str(prefix / "bin"), str(tool_bin)])
    t0 = time.monotonic()
    inst = subprocess.run(["npm", "install", "-g", "--prefix", str(prefix), "--offline", "--no-audit", "--no-fund", str(tarball)],
                          cwd=str(cwd), env=env, capture_output=True, text=True, timeout=int(smoke["install_timeout_seconds"]))
    install_seconds = round(time.monotonic() - t0, 1)
    (logs / "00-install.log").write_text(f"$ npm install -g --prefix {prefix} --offline {tarball}\n{inst.stdout}{inst.stderr}"
                                         f"[exit {inst.returncode}]\n", encoding="utf-8")
    entry = prefix / "bin" / "lybra"
    if inst.returncode != 0 or not entry.exists():
        raise PackageCheckError(f"装包失败(exit {inst.returncode}; 入口 {entry} {'在' if entry.exists() else '不在'}): "
                                f"{(inst.stdout + inst.stderr)[-1500:]}")
    values = {"version": version or "", "project": str(smoke["project"]), "home_root": str(home_root),
              "governance_root": str(home_root / str(smoke["project"])), "actor": str(smoke["actor"])}
    steps: list[dict[str, Any]] = []
    for index, step in enumerate(smoke["steps"], start=1):
        argv = [_fill(str(a), values) for a in step["argv"]]
        t1 = time.monotonic()
        proc = subprocess.run(["lybra", *argv], cwd=str(cwd), env=env, capture_output=True, text=True,
                              timeout=int(smoke["step_timeout_seconds"]))
        seconds = round(time.monotonic() - t1, 1)
        problems = []
        if proc.returncode not in [int(c) for c in step["expect_exit"]]:
            problems.append(f"退出码 {proc.returncode} 不在 {step['expect_exit']}")
        problems += [f"stdout 缺 {_fill(s, values)!r}" for s in step.get("expect_stdout", []) if _fill(s, values) not in proc.stdout]
        problems += [f"输出缺 {_fill(s, values)!r}" for s in step.get("expect_output", [])
                     if _fill(s, values) not in proc.stdout + proc.stderr]
        if "Traceback (most recent call last)" in proc.stderr:
            problems.append("stderr 含 Python Traceback(未按声明出口拒绝)")
        for key, key_path in (step.get("capture") or {}).items():
            try:
                node: Any = json.loads(proc.stdout)
                for k in key_path:
                    node = node[k]
                values[str(key)] = str(node)
            except (ValueError, LookupError, TypeError):
                problems.append(f"stdout(JSON)取不到 {'.'.join(map(str, key_path))}")
        (logs / f"{index:02d}-{step['name']}.log").write_text(
            f"$ lybra {' '.join(argv)}\n--- stdout ---\n{proc.stdout}--- stderr ---\n{proc.stderr}[exit {proc.returncode}] "
            f"{seconds}s\n", encoding="utf-8")
        steps.append({"name": step["name"], "argv": argv, "rc": proc.returncode, "ok": not problems, "seconds": seconds,
                      "problems": problems, "output": (proc.stdout + proc.stderr)})
    return {"ok": all(s["ok"] for s in steps), "version": version, "install_seconds": install_seconds, "steps": steps,
            "seconds": round(time.monotonic() - started, 1), "env_keys": sorted(env), "prefix": str(prefix),
            "home": str(home), "home_root": str(home_root), "logs": str(logs)}


def smoke_summary(result: dict[str, Any]) -> str:
    lines = [("✓ " if result["ok"] else "✗ ") + f"干净 HOME 装包冒烟: {len(result['steps'])} 步, 装包 {result['install_seconds']}s, "
             f"共 {result['seconds']}s; 环境键 {', '.join(result['env_keys'])}; 日志 {result['logs']}"]
    for s in result["steps"]:
        lines.append(f"  {'✓' if s['ok'] else '✗'} lybra {' '.join(s['argv'])} → exit {s['rc']} ({s['seconds']}s)"
                     + (f": {'; '.join(s['problems'])}" if s["problems"] else ""))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 件⑤ 发布前自检
# ---------------------------------------------------------------------------

def preflight(repo_root: Path | None = None, *, work: Path | None = None, suite_command: list[str] | None = None,
              say: Any = print) -> dict[str, Any]:
    """件⑤: 版本单源 → 打包清单校验 → 干净 HOME 冒烟 → 全量测试(声明 preflight.suite_command; suite_command 参数只供靶场替换)。
    返回 {ok, line, items: {version, manifest, smoke, suite}}; line = 末行一行结论。任一项抛错 = 该项 FAIL(不中断其余项)。"""
    root = Path(repo_root) if repo_root is not None else _code_root()
    work = Path(work) if work is not None else Path(tempfile.mkdtemp(prefix="lybra-preflight-"))
    decl = package_declaration(root)
    items: dict[str, dict[str, Any]] = {}
    version = ""
    try:
        version = declared_version(root, decl)
        problems = version_mirror_problems(root, decl)
        items["version"] = {"ok": not problems, "detail": version if not problems else "; ".join(problems)}
    except PackageCheckError as exc:
        items["version"] = {"ok": False, "detail": str(exc)}
    say(("✓ " if items["version"]["ok"] else "✗ ") + f"版本单源: {items['version']['detail']}")
    tarball = None
    try:
        tarball = pack(root, work / "dist", work)
        manifest = check_pack(root, tarball, work, decl)
        items["manifest"] = {"ok": manifest["ok"], "detail": f"{manifest['files']} 件",
                             "problems": len(manifest["missing"]) + len(manifest["forbidden"]) + len(manifest["untracked"])
                             + len(manifest["credential_hits"])}
        say(manifest_summary(manifest))
    except PackageCheckError as exc:
        items["manifest"] = {"ok": False, "detail": str(exc)}
        say(f"✗ 打包清单校验: {exc}")
    if tarball is not None:
        try:
            smoke = smoke_install(tarball, work / "smoke", decl, version=version)
            items["smoke"] = {"ok": smoke["ok"], "detail": f"{sum(s['ok'] for s in smoke['steps'])}/{len(smoke['steps'])} 步"}
            say(smoke_summary(smoke))
        except (PackageCheckError, subprocess.TimeoutExpired) as exc:
            items["smoke"] = {"ok": False, "detail": str(exc)}
            say(f"✗ 干净 HOME 装包冒烟: {exc}")
    else:
        items["smoke"] = {"ok": False, "detail": "无 tarball(打包失败)"}
    argv = list(suite_command) if suite_command is not None else [str(a) for a in decl["preflight"]["suite_command"]]
    log = work / "suite.log"
    try:
        t0 = time.monotonic()
        with log.open("w", encoding="utf-8") as fh:
            proc = subprocess.run(argv, cwd=str(root), stdout=fh, stderr=subprocess.STDOUT,
                                  timeout=int(decl["preflight"]["suite_timeout_seconds"]))
        tail = [l for l in log.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()][-5:]
        passed = proc.returncode == 0 and any(str(decl["preflight"]["pass_line"]) in l for l in tail)
        items["suite"] = {"ok": passed, "detail": f"exit {proc.returncode}, {round(time.monotonic() - t0)}s, 日志 {log}"}
    except (OSError, subprocess.TimeoutExpired) as exc:
        items["suite"] = {"ok": False, "detail": f"{type(exc).__name__}: {exc}; 日志 {log}"}
    say(("✓ " if items["suite"]["ok"] else "✗ ") + f"全量测试 {' '.join(argv)}: {items['suite']['detail']}")
    ok = all(i["ok"] for i in items.values())
    labels = {"version": "版本单源", "manifest": "打包清单", "smoke": "干净 HOME 冒烟", "suite": "全量测试"}
    line = (f"lybra 发布前自检 {version or '(版本不明)'}: {'PASS' if ok else 'FAIL'}("
            + " / ".join(f"{labels[k]} {'✓' if v['ok'] else '✗'}" for k, v in items.items())
            + f"; 工作目录 {work}; 未执行 npm publish)")
    return {"ok": ok, "line": line, "items": items, "work": str(work)}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m tools.aipos_cli.package_check",
                                     description="AIPOS-F257: npm 安装包版本单源 / 打包清单校验 / 干净 HOME 装包冒烟 / 发布前自检(不发布)")
    parser.add_argument("--repo-root", default=None, help="产品仓根(缺省 = 本代码所在仓根)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("version", help="打印包版本(版本源)并校验各 mirror 一致")
    for name, text in (("manifest", "npm pack 到临时目录并校验打包清单(必需件 / 禁入件 / 凭据)"),
                       ("smoke", "npm pack 后装进临时 prefix, 以干净 HOME 跑声明的冒烟步"),
                       ("preflight", "发布前自检: 版本单源 + 打包清单 + 冒烟 + 全量测试, 末行一行结论")):
        p = sub.add_parser(name, help=text)
        p.add_argument("--work-dir", default=None, help="工作目录(缺省 = 新建临时目录; 产物与日志落此, 不入库)")
    args = parser.parse_args(argv)
    root = Path(args.repo_root).resolve() if args.repo_root else _code_root()
    try:
        decl = package_declaration(root)
        if args.command == "version":
            version, problems = declared_version(root, decl), version_mirror_problems(root, decl)
            print(version if not problems else f"{version}(✗ 不一致: {'; '.join(problems)})")
            return 0 if not problems else 1
        work = Path(args.work_dir).resolve() if args.work_dir else Path(tempfile.mkdtemp(prefix="lybra-package-"))
        if args.command == "preflight":
            result = preflight(root, work=work)
            print(result["line"])
            return 0 if result["ok"] else 1
        tarball = pack(root, work / "dist", work)
        if args.command == "manifest":
            result = check_pack(root, tarball, work, decl)
            print(manifest_summary(result))
            return 0 if result["ok"] else 1
        result = smoke_install(tarball, work / "smoke", decl, version=declared_version(root, decl))
        print(smoke_summary(result))
        return 0 if result["ok"] else 1
    except PackageCheckError as exc:
        print(f"package_check: ✗ {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(_cli())
