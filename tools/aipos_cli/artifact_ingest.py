"""AIPOS-196a gate-side scratch artifact ingestion.

Layer 2 confined-worker boundary: a confined worker can write only to a scratch
directory, never to Lybra truth. Proposed outputs become workspace-controlled
artifacts only when the gate ingests them during an Owner-confirmed queue_return.

This module is pure ingestion planning plus a guarded copy. It does not launch a
worker, mount anything, run a scheduler, or define a new controlled-execute
operation class. Ingestion only ever copies FROM an approved scratch root INTO
``workspace_artifacts/<task_id>/<return_id>/`` under the repo, and refuses any
symlink / relative-path escape or confused-deputy scratch root.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat as stat_module
from tools.schema_constants import RecordType
from tools.aipos_cli.verb_contract import declared_exit_code
from pathlib import Path
from typing import Any




WORKSPACE_ARTIFACT_ROOT = Path("workspace_artifacts")
APPROVED_SCRATCH_ROOT_ENV = "LYBRA_APPROVED_SCRATCH_ROOT"
# Truth / control-plane prefixes that must never be a scratch source.
_TRUTH_PREFIXES = ("5_tasks", ".lybra")
_BLOCK_PREFIX = "ARTIFACT_INGEST_BLOCKED"
_MAX_INGEST_BYTES = 25 * 1024 * 1024


def has_scratch_request(scratch_dir: Any, scratch_artifact_refs: Any) -> bool:
    """True when the caller actually asked for scratch ingestion."""
    return bool(str(scratch_dir or "").strip()) or bool(_as_ref_list(scratch_artifact_refs))


def _as_ref_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def approved_scratch_root(env: dict[str, str] | None = None) -> Path | None:
    source = env if env is not None else os.environ
    raw = str(source.get(APPROVED_SCRATCH_ROOT_ENV) or "").strip()
    if not raw:
        return None
    return Path(raw).expanduser().resolve()


def _sanitize_component(name: str) -> str:
    cleaned = "".join(char if (char.isalnum() or char in "._-") else "-" for char in name).strip("-._")
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned or "artifact"


def _is_within(base: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(base)
        return True
    except ValueError:
        return False


def _within_truth(repo_root: Path, candidate: Path) -> bool:
    for prefix in _TRUTH_PREFIXES:
        if _is_within((repo_root / prefix).resolve(), candidate):
            return True
    return False


def plan_scratch_ingestion(
    *,
    repo_root: Path,
    task_id: str,
    return_id: str,
    scratch_dir: str | None,
    scratch_artifact_refs: Any,
    env: dict[str, str] | None = None,
    home_root: Path | None = None,
) -> dict[str, Any]:
    """Plan (and content-hash) a scratch->workspace ingestion without writing.

    Returns a dict with ``ingestions`` (each: scratch_resolved, scratch_ref,
    workspace_rel, content_sha256, size_bytes), ``workspace_refs``, a
    timestamp-independent ``digest`` (R-B snapshot integrity), and
    ``blocking_reasons``. Fails closed: any ambiguity is a blocking reason.

    AIPOS-227 — invariant: ``repo_root`` is ALREADY the resolved project truth root supplied by
    the single caller (board_adapter queue_return -> _resolve_repo_and_home). This function does
    not resolve; a future second caller feeding a raw/unresolved root is a contract violation and
    is fail-fast-guarded at the board boundary (R-2).

    ``home_root`` is the survivable truth home that contains all project subtrees, resolved by the
    home model. When supplied (production home path) a scratch source resolving anywhere inside
    ``<home>`` — i.e. into any project's truth, not just this project's — is refused. ``None``
    means the legacy/explicit/direct path (no home model in play), where the existing per-project
    truth guards already apply (R-1: ``None`` never stands in for an unresolved home).
    """
    refs = _as_ref_list(scratch_artifact_refs)
    blocking: list[str] = []
    result: dict[str, Any] = {
        "ingestions": [],
        "workspace_refs": [],
        "digest": "",
        "blocking_reasons": blocking,
    }
    if not has_scratch_request(scratch_dir, scratch_artifact_refs):
        return result

    repo_root = repo_root.resolve()

    # R-C: scratch_dir must live under an Owner/operator-approved root supplied
    # to the gate process out of band. The agent cannot widen this; without it
    # the gate refuses to act as a confused deputy.
    approved_root = approved_scratch_root(env)
    if approved_root is None:
        blocking.append(
            f"{_BLOCK_PREFIX}: no approved scratch root configured "
            f"({APPROVED_SCRATCH_ROOT_ENV} unset); refusing scratch ingestion"
        )
        return result

    scratch_raw = str(scratch_dir or "").strip()
    if not scratch_raw:
        blocking.append(f"{_BLOCK_PREFIX}: scratch_dir is required when scratch_artifact_refs are provided")
        return result
    if not refs:
        blocking.append(f"{_BLOCK_PREFIX}: scratch_artifact_refs is required when scratch_dir is provided")
        return result

    scratch_root = Path(scratch_raw).expanduser().resolve()
    if not _is_within(approved_root, scratch_root) and scratch_root != approved_root:
        blocking.append(f"{_BLOCK_PREFIX}: scratch_dir is outside the approved scratch root")
        return result
    if _within_truth(repo_root, scratch_root) or _is_within((repo_root / WORKSPACE_ARTIFACT_ROOT).resolve(), scratch_root):
        blocking.append(f"{_BLOCK_PREFIX}: scratch_dir resolves into a Lybra truth or workspace-artifact path")
        return result
    # AIPOS-227: extend-only — when the home model is in play, refuse a scratch source anywhere
    # inside the survivable truth home (any project's truth), not just this project's. Never
    # relaxes an existing guard; only widens the refused surface.
    if home_root is not None and _is_within(Path(home_root).expanduser().resolve(), scratch_root):
        blocking.append(f"{_BLOCK_PREFIX}: scratch_dir resolves into the Lybra truth home")
        return result
    if not scratch_root.exists() or not scratch_root.is_dir():
        blocking.append(f"{_BLOCK_PREFIX}: scratch_dir does not exist or is not a directory")
        return result

    dest_root = (repo_root / WORKSPACE_ARTIFACT_ROOT / _sanitize_component(task_id) / _sanitize_component(return_id)).resolve()
    workspace_artifact_root = (repo_root / WORKSPACE_ARTIFACT_ROOT).resolve()

    ingestions: list[dict[str, Any]] = []
    workspace_refs: list[str] = []
    seen_dest: set[str] = set()
    for ref in refs:
        if Path(ref).is_absolute():
            blocking.append(f"{_BLOCK_PREFIX}: scratch_artifact_ref must be relative to scratch_dir: {ref}")
            continue
        candidate = (scratch_root / ref).resolve()
        # Defeats symlink and ../ escape: the real path must stay in scratch.
        if not _is_within(scratch_root, candidate):
            blocking.append(f"{_BLOCK_PREFIX}: scratch_artifact_ref escapes scratch_dir: {ref}")
            continue
        if not candidate.exists() or not candidate.is_file():
            blocking.append(f"{_BLOCK_PREFIX}: scratch_artifact_ref is not a regular file: {ref}")
            continue
        size = candidate.stat().st_size
        if size > _MAX_INGEST_BYTES:
            blocking.append(f"{_BLOCK_PREFIX}: scratch_artifact_ref exceeds ingest size limit: {ref}")
            continue
        basename = _sanitize_component(Path(ref).name)
        dest_abs = (dest_root / basename).resolve()
        if not _is_within(workspace_artifact_root, dest_abs):
            blocking.append(f"{_BLOCK_PREFIX}: computed destination escapes workspace_artifacts: {ref}")
            continue
        dest_rel = dest_abs.relative_to(repo_root.resolve()).as_posix()  # AIPOS-240 r2: dest_abs is resolved (line above); symlink-safe render
        if dest_rel in seen_dest:
            blocking.append(f"{_BLOCK_PREFIX}: duplicate destination for ingested artifact: {dest_rel}")
            continue
        if dest_abs.exists():
            blocking.append(f"{_BLOCK_PREFIX}: workspace artifact already exists, refusing overwrite: {dest_rel}")
            continue
        seen_dest.add(dest_rel)
        content_sha256 = _hash_file(candidate)
        ingestions.append(
            {
                "scratch_resolved": str(candidate),
                "scratch_ref": ref,
                "workspace_rel": dest_rel,
                "content_sha256": content_sha256,
                "size_bytes": size,
            }
        )
        workspace_refs.append(dest_rel)

    if blocking:
        return result

    result["ingestions"] = ingestions
    result["workspace_refs"] = workspace_refs
    result["digest"] = ingestion_digest(ingestions)
    result["scratch_root"] = str(scratch_root)
    return result


def ingestion_digest(ingestions: list[dict[str, Any]]) -> str:
    """Timestamp-independent digest over ingested basenames + content hashes.

    Excludes return_id-bearing paths so the snapshot stays stable across the
    deterministic timestamp while still catching any content swap (R-B).
    """
    items = sorted(
        (Path(item["workspace_rel"]).name, str(item["content_sha256"]))
        for item in ingestions
    )
    encoded = "\n".join(f"{name}:{digest}" for name, digest in items)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def perform_scratch_ingestion(
    repo_root: Path,
    ingestions: list[dict[str, Any]],
    *,
    scratch_root: str | None = None,
    approved_root: Path | None = None,
) -> list[dict[str, Any]]:
    """Copy planned scratch artifacts into workspace_artifacts (Owner-confirmed).

    R-A: each artifact is re-resolved and re-hashed at copy time and opened with
    O_NOFOLLOW, so a symlink or content swap between plan and copy is rejected
    rather than trusted. Raises ValueError on any integrity failure so the
    caller blocks the whole return instead of writing partial truth.
    """
    repo_root = repo_root.resolve()
    workspace_artifact_root = (repo_root / WORKSPACE_ARTIFACT_ROOT).resolve()
    scratch_root_resolved = Path(scratch_root).resolve() if scratch_root else None
    performed: list[dict[str, Any]] = []
    for item in ingestions:
        scratch_resolved = Path(str(item["scratch_resolved"])).resolve()
        if scratch_root_resolved is not None and not _is_within(scratch_root_resolved, scratch_resolved):
            raise ValueError(f"{_BLOCK_PREFIX}: scratch artifact left the scratch root before copy")
        if approved_root is not None and not _is_within(approved_root, scratch_resolved):
            raise ValueError(f"{_BLOCK_PREFIX}: scratch artifact left the approved root before copy")
        dest_abs = (repo_root / str(item["workspace_rel"])).resolve()
        if not _is_within(workspace_artifact_root, dest_abs):
            raise ValueError(f"{_BLOCK_PREFIX}: ingest destination escaped workspace_artifacts before copy")
        if dest_abs.exists():
            raise ValueError(f"{_BLOCK_PREFIX}: ingest destination appeared before copy: {item['workspace_rel']}")
        payload = _read_nofollow(scratch_resolved)
        actual = hashlib.sha256(payload).hexdigest()
        if actual != str(item.get("content_sha256")):
            raise ValueError(f"{_BLOCK_PREFIX}: scratch artifact content changed before copy: {item['scratch_ref']}")
        dest_abs.parent.mkdir(parents=True, exist_ok=True)
        with open(dest_abs, "xb") as handle:
            handle.write(payload)
        performed.append(
            {
                "path": str(item["workspace_rel"]),
                "kind": "create",
                "type": RecordType.INGESTED_ARTIFACT,
                "record_type": RecordType.INGESTED_ARTIFACT,
                "content_sha256": actual,
                "wrote": True,
            }
        )
    return performed


def _read_nofollow(path: Path) -> bytes:
    flags = os.O_RDONLY
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(str(path), flags | nofollow)
    try:
        info = os.fstat(fd)
        if not stat_module.S_ISREG(info.st_mode):
            raise ValueError(f"{_BLOCK_PREFIX}: scratch artifact is not a regular file at copy time")
        data = b""
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            data += chunk
        return data
    finally:
        os.close(fd)


# ===========================================================================
# AIPOS-F78 件③: 产物入口(单一 ingest)—— `lybra artifact ingest --task-id <ID>`
#
# 执行引擎(pi/codex/claude-code)交回的是「分支 + Return 文件」; 产品按项目声明找文件、校验必填
# frontmatter 与分支 tip, 再经既有薄壳(queue return / audit-verdict, 驱动方 token)铸记录。
# 声明: transitions.schema.json artifact_ingest(文件候选/必填字段) + project.json paths(落点根)。
# 命令构建与执行复用 next_resolver(推导核派生同一条命令, execute_derived_action 同一执行体), 禁第二路径。
# 上面的 scratch ingestion(AIPOS-196a)是另一件事(confined worker 草稿区拷贝), 两者不互调。
# ===========================================================================

# AIPOS-F101 件③: 退出码唯一声明 = verbs.schema verbs.lybra_artifact_ingest.exit_codes(原写死 4 与 transitions 重复声明删除);
# 模块名保留为读声明的值(既有调用方/夹具按名引用), 声明缺 = 导入即 SchemaLoadError(fail-closed)
INGEST_EXIT_RECORDED = declared_exit_code("lybra_artifact_ingest", "recorded")
INGEST_EXIT_SHELL_REJECTED = declared_exit_code("lybra_artifact_ingest", "shell_rejected")
INGEST_EXIT_REJECTED = declared_exit_code("lybra_artifact_ingest", "rejected")


def _git_out(repo: Path, *argv: str) -> str | None:
    import subprocess

    try:
        result = subprocess.run(["git", *argv], cwd=str(repo), capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        import sys

        print(f"Warning: git {' '.join(argv)} failed in {repo}: {exc}", file=sys.stderr)
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _code_repo_for(workspace_root: Path, task_id: str, card_frontmatter: dict[str, Any]) -> tuple[Path | None, dict[str, Any]]:
    """AIPOS-F78C 件②③: 卡分支所在仓 = 该卡声明的仓(workspace_config.resolve_card_repo 唯一解析; 卡 lane.repo → project.json
    repos 清单 → code_repo 别名 → 治理根)。返回 (仓路径, 拒因 out 片段); 解析不到/不是 git 仓根 = (None, {category, reasons})。
    chris 实撞: 卡无 lane.repo、project.json 无 repos、code_repo 指治理根(非 git 仓)→ 这里停在 LANE_REPO_UNDECLARED(声明缺失, 非崩溃)。"""
    from tools.aipos_cli.workspace_config import CardRepoUnresolved, project_repos, resolve_card_repo

    try:
        repo = resolve_card_repo(workspace_root, {**card_frontmatter, "task_id": str(card_frontmatter.get("task_id") or task_id)})
    except CardRepoUnresolved as exc:
        return None, {"category": exc.code, "reasons": [exc.reason]}
    if not (repo / ".git").exists():
        lane = card_frontmatter.get("lane") if isinstance(card_frontmatter.get("lane"), dict) else {}
        lane_repo = str(lane.get("repo") or "").strip() or "未声明"
        try:
            declared = "已声明" if project_repos(workspace_root)["declared"] else "未声明"
        except CardRepoUnresolved as exc:
            return None, {"category": exc.code, "reasons": [exc.reason]}
        return None, {
            "category": "LANE_REPO_UNDECLARED",
            "reasons": [
                f"卡 {task_id} 解析到的产品仓 {repo} 不是 git 仓根(无 .git): 卡 lane.repo={lane_repo}, project.json repos 清单{declared}, "
                f"code_repo={project_repos(workspace_root)['code_repo'] or '未声明'}。"
                "出口: 在 project.json 声明 repos {default, items: {仓名: 绝对路径}} 清单, 并在卡 frontmatter lane.repo 写产物所在仓名(一卡一仓); "
                "单仓项目 `lybra project set-repo <name> --code-repo <真实产品仓>`"
            ],
        }
    return repo, {}


def _external_finalize_pending(workspace_root: Path, task_id: str) -> tuple[bool, dict[str, Any]]:
    """AIPOS-F78B 件②: 本卡是否处在「finalize_mode=external 且裁决 PASS 且尚无 finalization 记录」——即产物入口应读 FINALIZE Return。
    返回 (pending, records)。判据读声明: N5.guards.has_pass_verdict.allowed_verdict_values。"""
    from tools.aipos_cli.next_resolver import _finalize_mode, _read_task_records, _transition_node

    if _finalize_mode(workspace_root) != "external":
        return False, {}
    records = _read_task_records(workspace_root, task_id)
    verdict = str((records.get("latest_verdict") or {}).get("verdict") or "").strip()
    allowed = [str(v) for v in (_transition_node("N5").get("guards", {}).get("has_pass_verdict", {}).get("allowed_verdict_values") or [])]
    from tools.aipos_cli.finalization_record import existing_finalization_records  # AIPOS-F120: 有无记录唯一判据

    has_finalization = bool(existing_finalization_records(workspace_root, task_id))
    return (bool(verdict) and verdict in allowed and not has_finalization), records


def slot_code_refusal(slot_dir: Path) -> dict[str, Any] | None:
    """AIPOS-F195 件①: 报告落点(含 artifacts/、run_long/ 等子目录)出现代码文件 = 拒入门(交回 / 审计报告 / 外部 FINALIZE 交回同此)。
    判据 = governance_guardrails.is_code_file(只读 config.schema code_files_blacklist), 拒因码与文案读 config.schema
    governance_worktree.path_constraints.artifact_slot_code_files(声明缺 = 不查, 行为不变; 声明坏 = 拒 INGEST_DECLARATION_MISSING)。
    返回 None(无代码文件)或 {category, reasons, code_files}。"""
    from tools.aipos_cli.governance_guardrails import GuardrailDeclarationError, load_guardrail_declarations, slot_code_files_in_dir

    try:
        decls = load_guardrail_declarations()
    except GuardrailDeclarationError as exc:
        return {"category": "INGEST_DECLARATION_MISSING", "reasons": [f"报告落点代码文件检查的声明不可用(fail-closed): {exc}"], "code_files": []}
    if decls.slot_code is None:
        return None
    found = slot_code_files_in_dir(Path(slot_dir), decls)
    if not found:
        return None
    files = "; ".join(f"{item['path']}(sha256 {item['sha256']}, {item['size']} 字节)" for item in found)
    category = decls.slot_code.ingest_category
    reason = decls.slot_code.text("ingest_reason", category=category, slot=str(slot_dir), count=len(found), files=files,
                                  extensions=", ".join(f".{e}" for e in decls.code_extensions))
    return {"category": category, "reasons": [reason], "code_files": found}


_PROGRAM_GUARD_KEYS = ("task_modes", "max_lines", "executable_bit", "shebang_prefix", "sniff_bytes", "max_bytes", "skip_dirs", "category", "texts")
_PROGRAM_GUARD_TEXTS = ("refusal", "file", "exec_part", "exec_rule", "draft_warning")


def program_guard_declaration() -> dict[str, Any] | None:
    """AIPOS-F230 件②: transitions artifact_ingest.return.program_guard(唯一读取口)。缺本节 = None(不查, 行为不变); 键 / 文案缺或形不合 =
    SchemaLoadError(fail-closed)。"""
    from tools.aipos_cli.next_resolver import _artifact_ingest_declaration
    from tools.schema_loader import SchemaLoadError

    decl = (_artifact_ingest_declaration().get("return") or {}).get("program_guard")
    if decl is None:
        return None
    texts = decl.get("texts") if isinstance(decl, dict) else None
    ints = ("max_lines", "sniff_bytes", "max_bytes")
    if (not isinstance(decl, dict) or any(k not in decl for k in _PROGRAM_GUARD_KEYS) or not isinstance(texts, dict)
            or not all(isinstance(texts.get(k), str) and texts[k].strip() for k in _PROGRAM_GUARD_TEXTS)
            or not all(isinstance(decl[k], int) and not isinstance(decl[k], bool) and decl[k] > 0 for k in ints)
            or not isinstance(decl["executable_bit"], bool) or not isinstance(decl["task_modes"], list) or not decl["task_modes"]
            or not isinstance(decl["skip_dirs"], list) or not str(decl["category"]).strip() or not str(decl["shebang_prefix"])):
        raise SchemaLoadError(f"transitions.schema.json artifact_ingest.return.program_guard({list(_PROGRAM_GUARD_KEYS)} / texts."
                              f"{list(_PROGRAM_GUARD_TEXTS)})未声明齐(AIPOS-F230 件②)")
    copy = decl.get("input_copy")
    if copy is not None:  # AIPOS-F236 件①: 缺 = 不豁免(行为同 F230); 在而形不合 = fail-closed
        ctexts = copy.get("texts") if isinstance(copy, dict) else None
        if not isinstance(ctexts, dict) or not all(isinstance(ctexts.get(k), str) and ctexts[k].strip() for k in _INPUT_COPY_TEXTS):
            raise SchemaLoadError(f"transitions.schema.json artifact_ingest.return.program_guard.input_copy.texts.{list(_INPUT_COPY_TEXTS)}"
                                  f"未声明齐(AIPOS-F236 件①)")
    return decl


_INPUT_COPY_TEXTS = ("note", "file", "repo_error")


def git_blob_id(data: bytes) -> str:
    """AIPOS-F236 件①: 内容的 git blob 对象 id(sha1(`blob <字节数>\\0` + 内容), 与 `git hash-object --no-filters` 同算; 唯一实现)。"""
    import hashlib

    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def _declared_repos(workspace_root: Path, card_fm: dict[str, Any] | None) -> tuple[list[tuple[str, Path]], list[dict[str, str]]]:
    """AIPOS-F236 件①: 输入副本的来源仓集合(卡 lane 仓 → project.json repos.items → code_repo; 按真实路径去重; 治理根不算)。
    唯一读取口 workspace_config.resolve_card_repo / project_repos。返回 ([(仓名或路径, 路径)], [读取错误 {repo, path, error}])。"""
    from tools.aipos_cli.workspace_config import CardRepoUnresolved, project_repos, resolve_card_repo

    root = Path(workspace_root)
    out: list[tuple[str, Path]] = []
    errors: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(name: str, path: Path) -> None:
        try:
            key = str(Path(path).expanduser().resolve())
        except OSError:
            key = str(path)
        if key in seen or key == str(root.resolve()):
            return
        seen.add(key)
        out.append((name, Path(path).expanduser()))

    try:
        lane = resolve_card_repo(root, card_fm, allow_governance_root=False)
        add(str(((card_fm or {}).get("lane") or {}).get("repo") or lane), lane)
    except CardRepoUnresolved:
        pass  # 只写治理根的卡 / 无产品仓: lane 仓不存在, 照下方清单取
    try:
        repos = project_repos(root)
    except (CardRepoUnresolved, OSError, ValueError) as exc:
        errors.append({"repo": "project.json repos", "path": str(root), "error": f"{type(exc).__name__}: {exc}"})
        return out, errors
    for name in sorted(repos["items"]):
        add(name, repos["items"][name])
    if repos["code_repo"] is not None:
        add("code_repo", repos["code_repo"])
    return out, errors


def repo_blob_sources(workspace_root: Path, card_fm: dict[str, Any] | None, blobs: list[str]) -> dict[str, Any]:
    """AIPOS-F236 件①(唯一实现): 哪些 blob 已存在于本项目声明的仓(_declared_repos)。每仓一次 `git -C <仓> cat-file --batch-check`
    (与 `git cat-file -e` 同一对象存在判定的批量形; 类型须为 blob)。返回 {found: {blob: {repo, repo_path}}, errors: [{repo, path, error}]}。
    仓不在盘上 / 非 git 仓 / git 出错 = 该仓不作来源, 原因进 errors(fail-closed: 对应文件照旧算程序)。"""
    import subprocess

    wanted = [b for b in dict.fromkeys(blobs) if b]
    repos, errors = _declared_repos(Path(workspace_root), card_fm)
    found: dict[str, dict[str, str]] = {}
    for name, path in repos:
        remaining = [b for b in wanted if b not in found]
        if not remaining:
            break
        if not path.is_dir():
            errors.append({"repo": name, "path": str(path), "error": "不在盘上"})
            continue
        try:
            proc = subprocess.run(["git", "-C", str(path), "cat-file", "--batch-check"], input="\n".join(remaining) + "\n",
                                  capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError) as exc:
            errors.append({"repo": name, "path": str(path), "error": f"{type(exc).__name__}: {exc}"})
            continue
        if proc.returncode != 0:
            errors.append({"repo": name, "path": str(path), "error": (proc.stderr or proc.stdout or f"exit {proc.returncode}").strip()[:300]})
            continue
        for line in proc.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "blob" and parts[0] in remaining:
                found[parts[0]] = {"repo": name, "repo_path": str(path)}
    return {"found": found, "errors": errors}


def _program_file(path: Path, decl: dict[str, Any], code_decls: Any) -> dict[str, Any] | None:
    """一个文件是否属「可执行程序」(判据见 program_guard 声明): 是 = {path, lines, executable}; 否 = None。读不出 = 不算(不是文本程序)。"""
    import stat

    from tools.aipos_cli.governance_guardrails import is_code_file

    try:
        info = path.lstat()
    except OSError:
        return None
    if not stat.S_ISREG(info.st_mode) or info.st_size > int(decl["max_bytes"]):
        return None
    try:
        data = path.read_bytes()
    except OSError:
        return None
    head = data[: int(decl["sniff_bytes"])]
    if b"\0" in head:
        return None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if not (is_code_file(str(path), code_decls) or text.startswith(str(decl["shebang_prefix"]))):
        return None
    lines = text.count("\n") + (0 if text.endswith("\n") or not text else 1)
    executable = bool(info.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))
    if lines > int(decl["max_lines"]) or (decl["executable_bit"] and executable):
        return {"path": str(path), "lines": lines, "executable": executable, "blob": git_blob_id(data)}
    return None


def _walk_files(root: Path, skip: set[str]) -> list[Path]:
    """root 下普通文件(不跟软链; 目录名 ∈ skip 整棵跳过), 按路径排序。"""
    import os

    out: list[Path] = []
    for current, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in skip)
        out += [Path(current) / f for f in sorted(files)]
    return out


def program_scan(workspace_root: Path, task_id: str, card_fm: dict[str, Any] | None,
                 return_fm: dict[str, Any] | None = None) -> dict[str, Any]:
    """AIPOS-F230 件② + AIPOS-F236 件①(唯一实现): 配置 / 文档卡交回产物中命中程序判据的文件, 分「本卡程序」与「输入副本」。
    task_mode ∉ program_guard.task_modes / 声明缺 = 全空。扫: 本卡报告落点(含子目录)、交回报告 outputs 所列文件、本卡在本机的验证根
    run 目录(validation_root 声明 task_id = 本卡, run_directory 渲染; skip_dirs 跳过)。命中者内容 blob 已在本项目声明的仓
    (repo_blob_sources; 声明 program_guard.input_copy 在时才核)= 输入副本。返回 {programs: [{path, lines, executable, blob}],
    input_copies: [{path, lines, executable, blob, repo, repo_path}], repo_errors: [{repo, path, error}]}(各按路径)。"""
    empty: dict[str, Any] = {"programs": [], "input_copies": [], "repo_errors": []}
    decl = program_guard_declaration()
    fm = card_fm if isinstance(card_fm, dict) else {}
    if decl is None or str(fm.get("task_mode") or "").strip() not in [str(m) for m in decl["task_modes"]]:
        return empty
    from tools.aipos_cli.governance_guardrails import load_guardrail_declarations
    from tools.aipos_cli.next_resolver import return_artifact_dir
    from tools.aipos_cli.validation_root import is_local_host, project_validation_roots, run_directory

    code_decls = load_guardrail_declarations()
    skip = {str(d) for d in decl["skip_dirs"]}
    root = Path(workspace_root)
    paths: list[Path] = []
    slot = return_artifact_dir(root, task_id)
    if slot.is_dir():
        paths += _walk_files(slot, skip)
    outputs = (return_fm or {}).get("outputs")
    for ref in outputs if isinstance(outputs, list) else []:
        item = Path(str(ref)).expanduser()
        paths.append(item if item.is_absolute() else root / item)
    for entry in project_validation_roots(root):
        if str(entry.get("task_id") or "") != task_id or not is_local_host(entry.get("host")):
            continue
        for run in entry.get("runs") or []:
            run_dir = Path(run_directory(entry, str(run.get("run_id") or "")))
            if run_dir.is_dir():
                paths += _walk_files(run_dir, skip)
    found: dict[str, dict[str, Any]] = {}
    for path in paths:
        hit = _program_file(path, decl, code_decls)
        if hit is not None:
            found.setdefault(hit["path"], hit)
    hits = [found[k] for k in sorted(found)]
    if not hits or decl.get("input_copy") is None:
        return {**empty, "programs": hits}
    sources = repo_blob_sources(root, fm, [h["blob"] for h in hits])
    copies = [{**h, **sources["found"][h["blob"]]} for h in hits if h["blob"] in sources["found"]]
    return {"programs": [h for h in hits if h["blob"] not in sources["found"]], "input_copies": copies, "repo_errors": sources["errors"]}


def input_copy_note(copies: list[dict[str, Any]]) -> str | None:
    """AIPOS-F236 件①: 输入副本注记原文(texts.note / file; 路径 + 来源仓 + blob)。无副本 / 声明缺 = None。"""
    decl = program_guard_declaration() or {}
    copy = decl.get("input_copy")
    if not copies or not isinstance(copy, dict):
        return None
    texts = copy["texts"]
    files = "; ".join(texts["file"].format(path=c["path"], repo=c["repo"], blob=c["blob"], lines=c["lines"]) for c in copies)
    return texts["note"].format(files=files)


def program_guard_refusal(workspace_root: Path, task_id: str, card_fm: dict[str, Any] | None,
                          scan: dict[str, Any]) -> dict[str, Any] | None:
    """AIPOS-F230 件②: 交回入口拒因(scan = program_scan 结果; programs 非空 = {category, reasons, program_files, input_copies, legal_exits};
    否则 None)。AIPOS-F236 件①③: 输入副本不算(另列注记); 拒因出口 = 合法出口全集命令原文(next_resolver.legal_exits 的 return_rejected
    状态, 与 lybra next 同源, 含撤回预演命令与门侧是否须 Owner); 仓核对失败另列(fail-closed)。"""
    hits = scan["programs"]
    if not hits:
        return None
    from tools.aipos_cli.next_resolver import legal_exits

    decl = program_guard_declaration() or {}
    texts = decl["texts"]
    files = "; ".join(texts["file"].format(path=h["path"], lines=h["lines"], exec=texts["exec_part"] if h["executable"] else "") for h in hits)
    exits = legal_exits(Path(workspace_root), task_id, card_fm, return_rejected_declaration()["legal_exits_state"],
                        recommended="new_subject", why=_program_exit_why())
    reason = texts["refusal"].format(task_id=task_id, task_mode=str((card_fm or {}).get("task_mode") or ""), files=files,
                                     max_lines=decl["max_lines"], exec_rule=texts["exec_rule"] if decl["executable_bit"] else "",
                                     exits=exits["text"])
    reasons = [reason]
    copy = decl.get("input_copy")
    if scan.get("repo_errors") and isinstance(copy, dict):
        reasons.append("; ".join(copy["texts"]["repo_error"].format(**e) for e in scan["repo_errors"]))
    note = input_copy_note(scan.get("input_copies") or [])
    if note:
        reasons.append(note)
    return {"category": str(decl["category"]), "reasons": reasons, "program_files": hits,
            "input_copies": list(scan.get("input_copies") or []), "legal_exits": exits}


def _program_exit_why() -> str:
    """配置卡交付程序的推荐依据(legal_exits.evidence_overrides.return_rejected_program; 缺 = 声明 recommend)。"""
    from tools.aipos_cli.next_resolver import legal_exits_declaration

    rule = (legal_exits_declaration().get("evidence_overrides") or {}).get("return_rejected_program") or {}
    return str(rule.get("why") or "")


# ---------------------------------------------------------------------------
# AIPOS-F236 件②: 交回拒收入真相(声明 transitions artifact_ingest.return.rejection_record)
# ---------------------------------------------------------------------------

_REJECTION_KEYS = ("event_type", "input_copies_event_type", "id_template", "record_fields", "recorded_by", "reason_chars",
                   "skip_categories", "shell_rejected_category", "action_type", "legal_exits_state", "body", "texts")
_REJECTION_TEXTS = ("rejected_title", "rejected_summary", "copies_title", "copies_summary", "missing", "suggested", "notes",
                    "revalidated_ok", "gate_side", "record_failed")


def return_rejected_declaration() -> dict[str, Any]:
    """AIPOS-F236 件②: transitions artifact_ingest.return.rejection_record(唯一读取口)。缺 / 键或文案不齐 = SchemaLoadError(fail-closed)。"""
    from tools.aipos_cli.next_resolver import _artifact_ingest_declaration
    from tools.schema_loader import SchemaLoadError

    decl = (_artifact_ingest_declaration().get("return") or {}).get("rejection_record")
    texts = decl.get("texts") if isinstance(decl, dict) else None
    if (not isinstance(decl, dict) or any(k not in decl for k in _REJECTION_KEYS) or not isinstance(texts, dict)
            or not all(isinstance(texts.get(k), str) and texts[k].strip() for k in _REJECTION_TEXTS)
            or not isinstance(decl["record_fields"], list) or not isinstance(decl["skip_categories"], list)
            or not isinstance(decl["reason_chars"], int) or isinstance(decl["reason_chars"], bool) or decl["reason_chars"] <= 0):
        raise SchemaLoadError(f"transitions.schema.json artifact_ingest.return.rejection_record({list(_REJECTION_KEYS)} / texts."
                              f"{list(_REJECTION_TEXTS)})未声明齐(AIPOS-F236 件②)")
    return decl


def _file_digest_line(path: Path, label: str) -> str:
    import hashlib

    try:
        if path.is_symlink() or not path.is_file():
            return f"missing  {label}"
        return f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {label}"
    except OSError as exc:
        return f"unreadable:{type(exc).__name__}  {label}"


def return_fingerprint(workspace_root: Path, task_id: str, files: list[str] | None = None) -> str:
    """AIPOS-F236 件②(唯一实现): 交回产物指纹 = 报告落点目录(含子目录, 不跟软链)全部普通文件 + files(拒因点名的报告落点外文件)逐个
    `<sha256|missing>  <路径>` 按路径排序拼接后 sha256。只读。"""
    import hashlib
    import os

    from tools.aipos_cli.next_resolver import return_artifact_dir

    slot = return_artifact_dir(Path(workspace_root), task_id)
    lines: dict[str, str] = {}
    if slot.is_dir():
        for current, dirs, names in os.walk(slot, followlinks=False):
            dirs.sort()
            for name in sorted(names):
                path = Path(current) / name
                label = f"slot/{path.relative_to(slot)}"
                lines[label] = _file_digest_line(path, label)
    for ref in files or []:
        label = str(ref)
        lines.setdefault(label, _file_digest_line(Path(label), label))
    text = "".join(lines[k] + "\n" for k in sorted(lines))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def return_rejection_records(workspace_root: Path, task_id: str, *, event_type: str | None = None) -> list[dict[str, Any]]:
    """AIPOS-F236 件②(读口唯一): 本卡交回拒收(缺省)/ 输入副本事件记录, 按 recorded_at 旧→新; 每条 = frontmatter + _path。
    读不出 = FrontmatterReadError 向上(fail-closed)。"""
    from tools.aipos_cli.frontmatter import require_frontmatter
    from tools.aipos_cli.record_writer import record_dir

    decl = return_rejected_declaration()
    kind = str(event_type or decl["event_type"])
    directory = record_dir(Path(workspace_root), "events") / task_id
    out: list[dict[str, Any]] = []
    for path in sorted(directory.glob(f"{kind}_*.md")) if directory.is_dir() else []:
        fm, _body = require_frontmatter(path)
        if str(fm.get("event_type") or "") == kind:
            out.append({**fm, "_path": str(path)})
    return sorted(out, key=lambda r: (str(r.get("recorded_at") or ""), _record_seq(r)))


def _record_seq(record: dict[str, Any]) -> int:
    tail = str(record.get("rejection_id") or "").rsplit("_", 1)[-1]
    return int(tail) if tail.isdigit() else 0


def write_return_rejection(workspace_root: Path, task_id: str, *, category: str, reasons: list[str], return_path: str,
                           files: list[str] | None = None, event_type: str | None = None) -> dict[str, Any]:
    """AIPOS-F236 件②(写口唯一): 一次交回拒收(或入门通过而有输入副本, event_type = input_copies_event_type)= 一条事件记录
    (record_writer.write_records_atomic kind event; 字段只读声明 record_fields)。返回 {rejection_id, path}。写不进 = 异常向上。"""
    from tools.aipos_cli.clock import iso_z
    from tools.aipos_cli.record_writer import render_markdown, write_records_atomic
    from tools.schema_constants import RecordType

    decl = return_rejected_declaration()
    kind = str(event_type or decl["event_type"])
    texts = decl["texts"]
    seq = 1 + len(return_rejection_records(Path(workspace_root), task_id, event_type=kind))
    rid = str(decl["id_template"]).format(event_type=kind, task_id=task_id, seq=seq)
    chars = int(decl["reason_chars"])
    refs = [str(f) for f in files or []]
    values = {"record_type": RecordType.TASK_PROGRESS_EVENT, "event_type": kind, "rejection_id": rid, "task_id": task_id,
              "category": category, "reasons": [str(r)[:chars] for r in reasons], "return_path": return_path, "files": refs,
              "fingerprint": return_fingerprint(Path(workspace_root), task_id, refs), "recorded_at": iso_z(),
              "recorded_by": str(decl["recorded_by"])}
    meta = {key: values[key] for key in decl["record_fields"]}  # 字段只读声明(声明多出未知字段 = KeyError, fail-closed)
    rejected = kind == str(decl["event_type"])
    body = str(decl["body"]).format(title=texts["rejected_title" if rejected else "copies_title"], task_id=task_id,
                                    summary=texts["rejected_summary" if rejected else "copies_summary"].format(category=category))
    written = write_records_atomic(Path(workspace_root), [("event", rid, render_markdown(meta, body, list(meta)), task_id)])
    paths = list(written.get("paths") or [])
    return {"rejection_id": rid, "path": paths[0] if paths else None}


def _record_ingest_event(workspace_root: Path, task_id: str, result: dict[str, Any], check: dict[str, Any], *,
                         rejected: bool) -> None:
    """ingest_task_artifact 的落记录(真交回才调): 拒 = 交回拒收记录(skip_categories / 无交回报告 = 不落); 通过且有输入副本 = 输入副本记录。
    声明不可用 / 写不进 = 拒因(拒时)或 notes(通过时)追加原文(出声, 不吞; 拒的仍拒)。"""
    from tools.aipos_cli.frontmatter import FrontmatterReadError
    from tools.schema_loader import SchemaLoadError

    key = "reasons" if rejected else "notes"
    try:
        decl = return_rejected_declaration()
    except SchemaLoadError as exc:
        result.setdefault(key, []).append(f"交回拒收记录声明不可用, 未落记录(fail-closed): {exc}")
        return
    path = str(check.get("path") or "")
    if check.get("kind") != "return" or not path:
        return
    if rejected:
        if result.get("category") in [str(c) for c in decl["skip_categories"]]:
            return
        files = [str(h.get("path")) for h in check.get("program_files") or [] if h.get("path")]
        files += [str(f) for f in check.get("credential_files") or [] if str(f) not in files]  # AIPOS-F239: 报告落点外的命中文件
        kwargs: dict[str, Any] = {"category": str(result.get("category") or ""),
                                  "reasons": [str(r) for r in result.get("reasons") or []], "files": files}
    else:
        copies = list(check.get("input_copies") or [])
        if not copies:
            return
        kwargs = {"category": "", "reasons": [input_copy_note(copies) or ""], "files": [str(c["path"]) for c in copies],
                  "event_type": str(decl["input_copies_event_type"])}
    try:
        rel = str(Path(path).resolve().relative_to(Path(workspace_root).resolve()))
    except (ValueError, OSError):
        rel = path
    try:
        written = write_return_rejection(Path(workspace_root), str(task_id), return_path=rel, **kwargs)
    except (OSError, ValueError, KeyError, FrontmatterReadError) as exc:
        result.setdefault(key, []).append(decl["texts"]["record_failed"].format(error=f"{type(exc).__name__}: {exc}"))
        return
    result["rejection_record"] = written


def return_rejection_state(workspace_root: Path, task_id: str) -> dict[str, Any] | None:
    """AIPOS-F236 件②(唯一判定; 推导核交回步 next_resolver._return_submission_step 同调): 本卡最新交回拒收记录 + 指纹复核 + 产物入口同一
    校验复核。无记录 / 指纹已变(报告或产物自拒后改过)= None(照常交回, 产物入口再判)。指纹未变 = {record, still_rejected, gate_side,
    check}: 此刻校验仍拒 = still_rejected True(check = 此刻校验结果, 拒因原文); 此刻通过 = still_rejected False(gate_side = 记录拒因是门侧拒)。
    记录读不出 = 向上(fail-closed)。"""
    decl = return_rejected_declaration()
    records = return_rejection_records(Path(workspace_root), task_id)
    if not records:
        return None
    latest = records[-1]
    files = [str(f) for f in latest.get("files") or []] if isinstance(latest.get("files"), list) else []
    if return_fingerprint(Path(workspace_root), task_id, files) != str(latest.get("fingerprint") or ""):
        return None
    check = validate_task_artifact(task_id, Path(workspace_root))
    gate_side = str(latest.get("category") or "") == str(decl["shell_rejected_category"])
    return {"record": latest, "still_rejected": not check.get("ok"), "gate_side": gate_side, "check": check}


# ---------------------------------------------------------------------------
# AIPOS-F239: 产物入口凭据检查(声明 transitions artifact_ingest.credential_guard)
# ---------------------------------------------------------------------------

_CREDENTIAL_KEYS = ("category", "scan", "patterns", "exempt", "texts")
_CREDENTIAL_SCAN_KEYS = ("full_text_dirs", "slot_globs", "output_target", "outputs", "skip_gitignored", "skip_dirs", "sniff_bytes",
                         "max_scan_bytes", "max_listed")
_CREDENTIAL_EXEMPT_KEYS = ("strip_chars", "key_patterns", "value_patterns", "loose_value_patterns", "value_words", "value_substrings")
_CREDENTIAL_TEXTS = ("refusal", "hit", "more", "what_return", "what_verdict", "what_finalization", "stock_title", "stock_root",
                     "stock_clean", "stock_summary", "stock_slot", "stock_hit", "stock_next", "charter_rule")


def credential_guard_declaration() -> dict[str, Any] | None:
    """AIPOS-F239 件①: transitions artifact_ingest.credential_guard(唯一读取口)。缺本节 = None(不查, 行为不变); 键 / 文案不齐、
    正则编不过 = SchemaLoadError(fail-closed)。返回声明副本, 另带 _patterns [(kind, label, 编译后正则, 最短值长, loose)] 与 _exempt(编译后豁免)。"""
    from tools.aipos_cli.next_resolver import _artifact_ingest_declaration
    from tools.schema_loader import SchemaLoadError

    decl = _artifact_ingest_declaration().get("credential_guard")
    if decl is None:
        return None
    where = "transitions.schema.json artifact_ingest.credential_guard"
    scan = decl.get("scan") if isinstance(decl, dict) else None
    exempt = decl.get("exempt") if isinstance(decl, dict) else None
    texts = decl.get("texts") if isinstance(decl, dict) else None
    if (not isinstance(decl, dict) or any(k not in decl for k in _CREDENTIAL_KEYS) or not str(decl["category"]).strip()
            or not isinstance(scan, dict) or any(k not in scan for k in _CREDENTIAL_SCAN_KEYS)
            or not isinstance(exempt, dict) or any(k not in exempt for k in _CREDENTIAL_EXEMPT_KEYS)
            or not isinstance(texts, dict) or not all(isinstance(texts.get(k), str) and texts[k].strip() for k in _CREDENTIAL_TEXTS)
            or not isinstance(decl["patterns"], list) or not decl["patterns"]
            or not all(isinstance(scan[k], list) for k in ("full_text_dirs", "slot_globs", "skip_dirs"))
            or not all(isinstance(scan[k], int) and not isinstance(scan[k], bool) and scan[k] > 0
                       for k in ("sniff_bytes", "max_scan_bytes", "max_listed"))
            or not all(isinstance(scan[k], bool) for k in ("output_target", "outputs", "skip_gitignored"))
            or not all(isinstance(exempt[k], list) for k in ("key_patterns", "value_patterns", "loose_value_patterns", "value_words",
                                                             "value_substrings"))
            or not isinstance(exempt["strip_chars"], str)):
        raise SchemaLoadError(f"{where}({list(_CREDENTIAL_KEYS)} / scan.{list(_CREDENTIAL_SCAN_KEYS)} / exempt.{list(_CREDENTIAL_EXEMPT_KEYS)} / "
                              f"texts.{list(_CREDENTIAL_TEXTS)})未声明齐(AIPOS-F239 件①)")
    compiled: list[tuple[str, str, re.Pattern[str], int, bool]] = []
    try:
        for item in decl["patterns"]:
            if not isinstance(item, dict) or not all(isinstance(item.get(k), str) and item[k].strip() for k in ("kind", "label", "regex")):
                raise SchemaLoadError(f"{where}.patterns 每项须有 kind / label / regex(AIPOS-F239 件①)")
            low = item.get("min_value_chars", 1)
            if not isinstance(low, int) or isinstance(low, bool) or low < 1:
                raise SchemaLoadError(f"{where}.patterns[{item['kind']}].min_value_chars 须为 ≥ 1 的整数")
            if not isinstance(item.get("loose", False), bool):
                raise SchemaLoadError(f"{where}.patterns[{item['kind']}].loose 须为布尔")
            compiled.append((item["kind"], item["label"], re.compile(item["regex"], re.MULTILINE), low, bool(item.get("loose", False))))
        exempt_patterns = [re.compile(str(p)) for p in exempt["value_patterns"]]
        key_patterns = [re.compile(str(p)) for p in exempt["key_patterns"]]
        loose_patterns = [re.compile(str(p)) for p in exempt["loose_value_patterns"]]
    except re.error as exc:
        raise SchemaLoadError(f"{where}: 正则编不过(fail-closed): {exc}") from exc
    return {**decl, "_patterns": compiled, "_exempt": {"patterns": exempt_patterns, "keys": key_patterns, "loose": loose_patterns,
                                                       "words": {str(w).casefold() for w in exempt["value_words"]},
                                                       "substrings": [str(s).casefold() for s in exempt["value_substrings"]],
                                                       "strip": exempt["strip_chars"]}}


def credential_charter_rule() -> str:
    """AIPOS-F242 件④: 执行 / 审计章程「报告不写明文凭据」事前提示(章程占位 {{credential_rule}}, charter_render 唯一调用)。文案唯一声明
    transitions artifact_ingest.credential_guard.texts.charter_rule(与入门判据同节); 声明缺本节 = 明确文字(不查, 不留空占位)。
    声明坏 = SchemaLoadError(fail-closed, 章程不渲染半截)。"""
    decl = credential_guard_declaration()
    if decl is None:
        return "(本产品未声明产物凭据判据 transitions artifact_ingest.credential_guard; 报告与产物仍不得写明文凭据)"
    return str(decl["texts"]["charter_rule"])


def _credential_value_exempt(value: str, decl: dict[str, Any], min_chars: int, loose: bool = False) -> bool:
    """命中值是否豁免(声明 exempt 唯一判定): 去首尾引号 / 尾随标点后过短、全匹配占位式(loose 模式另加 loose_value_patterns)、等于占位词、
    含占位子串 = 豁免。"""
    ex = decl["_exempt"]
    core = value.strip().strip(ex["strip"])
    if len(core) < min_chars:
        return True
    folded = core.casefold()
    if folded in ex["words"] or any(s in folded for s in ex["substrings"]):
        return True
    return any(p.fullmatch(core) for p in ex["patterns"] + (ex["loose"] if loose else []))


def credential_hits(text: str, decl: dict[str, Any]) -> list[dict[str, Any]]:
    """AIPOS-F239 件①(唯一判定): 文本中的明文凭据命中 [{line, kind, label}](按行号、种类去重排序)。绝不返回命中值。"""
    seen: dict[tuple[int, str], dict[str, Any]] = {}
    for kind, label, rx, low, loose in decl["_patterns"]:
        for m in rx.finditer(text):
            value = m.group("value") if "value" in rx.groupindex else m.group(0)
            key = m.group("key") if "key" in rx.groupindex else None
            if value is None or _credential_value_exempt(value, decl, low, loose):
                continue
            if key and any(p.fullmatch(key) for p in decl["_exempt"]["keys"]):
                continue
            line = text.count("\n", 0, m.start()) + 1
            seen.setdefault((line, kind), {"line": line, "kind": kind, "label": label})
    return [seen[k] for k in sorted(seen)]


def _credential_text(path: Path, scan: dict[str, Any]) -> str | None:
    """可扫的文本内容(普通文件, 不跟软链; 前 sniff_bytes 字节无 NUL; 只读前 max_scan_bytes 字节)。不是文本 / 读不出 = None。"""
    try:
        info = path.lstat()
        if not stat_module.S_ISREG(info.st_mode):
            return None
        with path.open("rb") as handle:
            data = handle.read(int(scan["max_scan_bytes"]))
    except OSError:
        return None
    if b"\0" in data[: int(scan["sniff_bytes"])]:
        return None
    return data.decode("utf-8", errors="replace")


def _git_ignore_rules(base: Path, paths: list[Path]) -> dict[str, str] | None:
    """base 所在 git 仓对 paths 的忽略规则 {路径: 「来源\0行号\0模式」}(git check-ignore -v --stdin -z; 已跟踪者不算忽略, 不在结果中)。
    不是 git 仓 / 出错 = None(调用方全扫, fail-closed)。
    AIPOS-F245: 子进程所读的忽略规则来源经 task_loader.truth_git_ignore_inputs 声明给真相指纹结果缓存(看板只读推导内; 判定本身不变)。"""
    import subprocess

    from tools.aipos_cli.task_loader import truth_external_read, truth_git_ignore_inputs

    if not paths:
        return {}
    try:
        with truth_external_read(lambda: truth_git_ignore_inputs(base, paths)):
            proc = subprocess.run(["git", "-C", str(base), "check-ignore", "-v", "--stdin", "-z"],
                                  input="\0".join(str(p) for p in paths) + "\0", capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode not in (0, 1):
        return None
    fields = proc.stdout.split("\0")
    return {fields[i + 3]: "\0".join(fields[i:i + 3]) for i in range(0, len(fields) - 3, 4) if fields[i + 3]}


def _private_files(root: Path, slot: Path, paths: list[Path]) -> set[str]:
    """AIPOS-F239 件①: 私有运行文件 = 被 git 忽略、且忽略它的规则 ≠ 忽略报告落点目录本身的规则(落点未被忽略 = 无规则)。整个落点都被忽略的
    项目(如 lybra task_cards/)忽略不带「私有」语义 = 全扫; 不是 git 仓 / 出错 = 全扫(fail-closed)。"""
    private: set[str] = set()
    inside = [p for p in paths if _is_within(root, p)]
    outside = [p for p in paths if not _is_within(root, p)]
    for base, group in ((root, inside), (slot, outside)):
        if not group or not base.is_dir():
            continue
        rules = _git_ignore_rules(base, [slot, *group])
        if rules is None:
            continue
        slot_rule = rules.get(str(slot))
        private |= {str(p) for p in group if rules.get(str(p)) is not None and rules[str(p)] != slot_rule}
    return private


def _card_lane_is_governance_root(workspace_root: Path, card_fm: dict[str, Any] | None) -> bool:
    """卡 lane 仓是否解析为治理根自身(workspace_config.resolve_card_repo 唯一解析; 解析不到 = 否, 不扫 output_target)。"""
    from tools.aipos_cli.workspace_config import CardRepoUnresolved, resolve_card_repo

    try:
        repo = resolve_card_repo(workspace_root, card_fm if isinstance(card_fm, dict) else {})
        return repo.resolve() == Path(workspace_root).resolve()
    except (CardRepoUnresolved, OSError, ValueError):
        return False


def _as_path_items(value: Any) -> list[str]:
    items = value if isinstance(value, list) else str(value or "").split(",")
    return [str(i).strip() for i in items if str(i).strip()]


def credential_scan(workspace_root: Path, slot: Path, *, card_fm: dict[str, Any] | None = None, report: Path | None = None,
                    report_fm: dict[str, Any] | None = None, decl: dict[str, Any] | None = None,
                    extra_files: list[Path] | None = None, extra_texts: list[tuple[str, str]] | None = None) -> dict[str, Any]:
    """AIPOS-F239 件①(唯一实现; 交回 / 裁决 / FINALIZE 入门与存量只读检查同调): 按声明 scan 收集本次入门会提交或会被审计读的文本
    (报告本身 + 报告落点 full_text_dirs 下全部 + 其余位置匹配 slot_globs 者 + 报告 outputs + lane 为治理根的卡的 output_target + extra_files),
    去掉私有运行文件(_private_files; 报告本身永远照扫), 逐个 credential_hits。返回 {hits: [{path, abs_path, line, kind, label}],
    files: 扫描文件数, ignored: 跳过的私有文件数}。声明缺 = 空结果。
    AIPOS-F242 件③: extra_texts = [(显示名, 文本)]——门侧写入口随请求提交、尚未落盘的文本(MCP 直调交回的 result_summary / return_body,
    裁决的 findings_summary), 与文件同一 credential_hits 判定, 命中 path = 显示名。"""
    import fnmatch

    decl = decl if decl is not None else credential_guard_declaration()
    if decl is None:
        return {"hits": [], "files": 0, "ignored": 0}
    scan = decl["scan"]
    root, slot = Path(workspace_root).resolve(), Path(slot).resolve()
    skip = {str(d) for d in scan["skip_dirs"]}
    full_dirs = {str(d) for d in scan["full_text_dirs"]}
    globs = [str(g) for g in scan["slot_globs"]]
    report_path = Path(report).resolve() if report is not None else None
    paths: list[Path] = [report_path] if report_path is not None else []
    paths += [Path(p).resolve() for p in extra_files or [] if not Path(p).is_symlink()]
    if slot.is_dir():
        for path in _walk_files(slot, skip):
            rel = path.relative_to(slot)
            if (len(rel.parts) > 1 and rel.parts[0] in full_dirs) or any(fnmatch.fnmatch(path.name, g) for g in globs):
                paths.append(path)
    extra: list[str] = []
    if scan["outputs"]:
        extra += _as_path_items((report_fm or {}).get("outputs") if isinstance((report_fm or {}).get("outputs"), list) else [])
    if scan["output_target"] and (card_fm or {}).get("output_target") and _card_lane_is_governance_root(root, card_fm):
        extra += _as_path_items((card_fm or {}).get("output_target"))
    for ref in extra:
        item = Path(ref).expanduser()
        item = item if item.is_absolute() else root / item
        if item.is_symlink():
            continue
        item = item.resolve()
        if not _is_within(root, item):
            continue
        if item.is_dir():
            paths += _walk_files(item, skip)
        elif item.is_file():
            paths.append(item)
    unique = list(dict.fromkeys(paths))
    ignored = _private_files(root, slot, [p for p in unique if p != report_path]) if scan["skip_gitignored"] else set()
    hits: list[dict[str, Any]] = []
    scanned = 0
    for path in unique:
        if str(path) in ignored:
            continue
        text = _credential_text(path, scan)
        if text is None:
            continue
        scanned += 1
        try:
            shown = str(path.relative_to(root))
        except ValueError:
            shown = str(path)
        hits += [{"path": shown, "abs_path": str(path), **h} for h in credential_hits(text, decl)]
    for shown, text in extra_texts or []:  # AIPOS-F242 件③: 随请求提交的文本(无文件路径; abs_path = 显示名, 不算报告落点外文件)
        if str(text or "").strip():
            scanned += 1
            hits += [{"path": shown, "abs_path": None, **h} for h in credential_hits(str(text), decl)]
    return {"hits": hits, "files": scanned, "ignored": len(ignored)}


def credential_refusal(workspace_root: Path, slot: Path, *, kind: str, report: Path | None = None, card_fm: dict[str, Any] | None = None,
                       report_fm: dict[str, Any] | None = None, extra_texts: list[tuple[str, str]] | None = None) -> dict[str, Any] | None:
    """AIPOS-F239 件②: 产物入口凭据拒因(kind = return / verdict / finalization)。无命中 / 声明缺 = None; 命中 = {category, reasons,
    credential_hits(无值), credential_files(报告落点外的命中文件, 交回拒收记录 files 用)}; 声明坏 = {category INGEST_DECLARATION_MISSING}。"""
    from tools.schema_loader import SchemaLoadError

    try:
        decl = credential_guard_declaration()
    except SchemaLoadError as exc:
        return {"category": "INGEST_DECLARATION_MISSING", "reasons": [f"{exc}"], "credential_hits": [], "credential_files": []}
    if decl is None:
        return None
    result = credential_scan(Path(workspace_root), Path(slot), card_fm=card_fm, report=report, report_fm=report_fm, decl=decl,
                             extra_texts=extra_texts)
    hits = result["hits"]
    if not hits:
        return None
    texts = decl["texts"]
    cap = int(decl["scan"]["max_listed"])
    listed = "; ".join(texts["hit"].format(path=h["path"], line=h["line"], label=h["label"], kind=h["kind"]) for h in hits[:cap])
    if len(hits) > cap:
        listed += " " + texts["more"].format(count=len(hits))
    what = texts.get(f"what_{kind}") or texts["what_return"]
    reason = texts["refusal"].format(what=what, count=len(hits), hits=listed)
    outside = sorted({h["abs_path"] for h in hits if h["abs_path"] and not _is_within(Path(slot).resolve(), Path(h["abs_path"]))})
    return {"category": str(decl["category"]), "reasons": [reason],
            "credential_hits": [{k: h[k] for k in ("path", "line", "kind", "label")} for h in hits], "credential_files": outside}


def gate_credential_blocking(workspace_root: Path, task_id: str, *, kind: str, card_fm: dict[str, Any] | None,
                             report: Path | None = None, extra_texts: list[tuple[str, str]] | None = None) -> list[str]:
    """AIPOS-F242 件③: 门侧交回 / 裁决写入唯一口的凭据判定(board_adapter._build_return_preview / _build_audit_verdict_preview 调,
    同 F162/F163 运行证据下沉做法)——CLI(artifact ingest 派生 / 手敲)与门 MCP 直调一切提交路径同核。判据 = credential_refusal
    (同一 credential_scan / credential_hits, 不另写), 拒因同码同文「<category>: <拒因>」(不回显值)。
    kind = return(落点 = 交回落点 return_artifact_dir, 报告缺省 = find_return_artifact)/ verdict(落点 = 审计落点 verdict_artifact_dir,
    报告缺省 = _check_verdict_artifact; task_id = 审计卡)。报告 frontmatter 读不出 = 按空(报告正文照扫)。无命中 = []。"""
    from tools.aipos_cli.frontmatter import FrontmatterReadError, require_frontmatter
    from tools.aipos_cli.next_resolver import _check_verdict_artifact, find_return_artifact, return_artifact_dir, verdict_artifact_dir

    root = Path(workspace_root)
    if kind == "verdict":
        slot = verdict_artifact_dir(root, task_id)
        report = report if report is not None else _check_verdict_artifact(root, task_id)
    else:
        slot = return_artifact_dir(root, task_id)
        report = report if report is not None else find_return_artifact(root, task_id)
    report_fm: dict[str, Any] = {}
    if report is not None and Path(report).is_file():
        try:
            report_fm, _body = require_frontmatter(Path(report), allow_missing_block=True)
        except FrontmatterReadError:
            report_fm = {}  # 头读不出: 报告全文与落点照扫(只少了报告头 outputs 清单这一扩展来源); 头本身的拒由其余入门判据给出
    else:
        report = None
    refusal = credential_refusal(root, slot, kind=kind, report=report, card_fm=card_fm, report_fm=report_fm, extra_texts=extra_texts)
    if refusal is None:
        return []
    return [f"{refusal['category']}: {reason}" for reason in refusal["reasons"]]


def credential_stock_scan(workspace_root: Path) -> dict[str, Any]:
    """AIPOS-F239 件③(只读): 已有报告按入门同一判据 credential_scan 扫一遍——治理根报告落点(paths.return_root / verdict_root 下各卡目录,
    报告 = 交回 / 裁决文件候选首个命中)与已入门审计报告快照(transitions artifact_ingest.verdict.report_snapshot.location 的目录, records 下)。
    返回 {declared, root, slots: 扫描落点数, files, hits: [{slot, path, line, kind, label}], by_kind}; 不改任何文件、不回显值。"""
    from tools.aipos_cli.board_adapter import report_snapshot_declaration
    from tools.aipos_cli.next_resolver import _artifact_ingest_declaration, _pick_candidate, _project_paths
    from tools.aipos_cli.record_writer import records_root

    root = Path(workspace_root)
    decl = credential_guard_declaration()
    out: dict[str, Any] = {"declared": decl is not None, "root": str(root), "slots": 0, "files": 0, "hits": [], "by_kind": {}}
    if decl is None:
        return out
    ingest = _artifact_ingest_declaration()
    candidates = [str(c) for c in list(ingest["return"].get("return_file_candidates") or [])
                  + list(ingest["verdict"].get("verdict_file_candidates") or [])]
    paths = _project_paths(root)
    bases = list(dict.fromkeys(Path(str(paths[k])) for k in ("return_root", "verdict_root") if paths.get(k)))
    slots: list[tuple[Path, Path | None]] = []
    for base in bases:
        if base.is_dir():
            slots += [(d, _pick_candidate(d, candidates)) for d in sorted(base.iterdir()) if d.is_dir() and not d.is_symlink()]
    snapshot_dirs = Path(str(report_snapshot_declaration()["location"])).parent
    pattern = re.sub(r"\{[^}]*\}", "*", str(snapshot_dirs))
    slots += [(d, None) for d in sorted((root / records_root()).glob(pattern)) if d.is_dir() and not d.is_symlink()]
    seen: set[Path] = set()
    for slot, report in slots:
        if slot.resolve() in seen:
            continue
        seen.add(slot.resolve())
        result = credential_scan(root, slot, report=report, decl=decl)
        out["slots"] += 1
        out["files"] += int(result["files"])
        try:
            label = str(slot.resolve().relative_to(root.resolve()))
        except ValueError:
            label = str(slot)
        for hit in result["hits"]:
            out["hits"].append({"slot": label, **{k: hit[k] for k in ("path", "line", "kind", "label")}})
    for hit in out["hits"]:
        out["by_kind"][hit["kind"]] = out["by_kind"].get(hit["kind"], 0) + 1
    out["texts"] = decl["texts"]
    return out


def render_credential_stock(result: dict[str, Any]) -> str:
    """AIPOS-F239 件③: 存量只读检查人读输出(唯一出口; 只列文件 / 行 / 种类, 不含值)。"""
    if not result.get("declared"):
        return "artifact_ingest.credential_guard not declared (no check)"
    texts = result["texts"]
    lines = [texts["stock_title"], texts["stock_root"].format(root=result["root"], slots=result["slots"], files=result["files"])]
    hits = result["hits"]
    if not hits:
        lines.append(texts["stock_clean"])
        return "\n".join(lines)
    per_slot: dict[str, list[dict[str, Any]]] = {}
    for hit in hits:
        per_slot.setdefault(hit["slot"], []).append(hit)
    by_kind = ", ".join(f"{k} {v}" for k, v in sorted(result["by_kind"].items()))
    lines.append(texts["stock_summary"].format(count=len(hits), slot_count=len(per_slot), by_kind=by_kind))
    for slot, items in per_slot.items():
        lines.append(texts["stock_slot"].format(slot=slot, count=len(items)))
        lines += [texts["stock_hit"].format(path=h["path"], line=h["line"], label=h["label"], kind=h["kind"]) for h in items]
    lines.append(texts["stock_next"])
    return "\n".join(lines)


def run_credential_scan_cli(args: Any) -> int:
    """CLI 入口(`lybra artifact credential-scan`, AIPOS-F239 件③): 只读存量检查。退出码读 verbs.schema lybra_artifact_credential_scan.exit_codes。"""
    import json as _json
    import sys

    from tools.aipos_cli.aipos_cli import _find_repo_root_for_args
    from tools.schema_loader import SchemaLoadError

    try:
        governance_root = Path(_find_repo_root_for_args(args))
        result = credential_stock_scan(governance_root)
    except (FileNotFoundError, SchemaLoadError, OSError) as exc:
        print(f"lybra artifact credential-scan: {exc}", file=sys.stderr)
        return declared_exit_code("lybra_artifact_credential_scan", "error")
    if getattr(args, "json", False):
        print(_json.dumps({k: v for k, v in result.items() if k != "texts"}, indent=2, ensure_ascii=False))
    else:
        print(render_credential_stock(result))
    return declared_exit_code("lybra_artifact_credential_scan", "hits" if result["hits"] else "clean")


def _apply_credential_refusal(out: dict[str, Any], workspace_root: Path, slot: Path, *, kind: str, report: Path,
                              card_fm: dict[str, Any] | None, report_fm: dict[str, Any] | None) -> bool:
    """AIPOS-F239 件②: 入门校验里的凭据判定(唯一调用口, return / verdict / finalization 同调)。命中 = 填 out(category / reasons /
    credential_hits / credential_files)并返回 True; 否则 False。"""
    refusal = credential_refusal(workspace_root, slot, kind=kind, report=report, card_fm=card_fm, report_fm=report_fm)
    if refusal is None:
        return False
    out["category"], out["reasons"] = refusal["category"], refusal["reasons"]
    out["credential_hits"], out["credential_files"] = refusal["credential_hits"], refusal["credential_files"]
    return True


def validate_task_artifact(task_id: str, workspace_root: Path) -> dict[str, Any]:
    """校验一张卡的产物(见 _validate_task_artifact)。AIPOS-F100 件②: 卡/记录/产物 frontmatter 读不出 = 拒
    INGEST_FRONTMATTER_UNREADABLE(原文点名文件与行号), 不取缺省继续校验。"""
    from tools.aipos_cli.frontmatter import FrontmatterReadError

    from tools.aipos_cli.audit_derivation import is_audit_card

    try:
        return _with_header_repair_note(_validate_task_artifact(task_id, workspace_root))
    except FrontmatterReadError as exc:
        is_audit = is_audit_card(task_id)
        out = {"ok": False, "kind": "verdict" if is_audit else "return",
               "category": "INGEST_FRONTMATTER_UNREADABLE", "reasons": [f"{exc}。出口: 按拒因修正该文件 frontmatter 后重推导"],
               "path": exc.path, "frontmatter": {}, "exit_code": INGEST_EXIT_REJECTED}
        problem = report_unreadable_problem(workspace_root, task_id, exc, is_audit=is_audit)
        if problem is not None:  # AIPOS-F232 件②: 读不出的是本卡报告 = 报告格式错(可纠正, loop 续接会话只修报告头)
            out["header_problem"] = problem
            out["reasons"] = [f"{exc}。出口(报告格式错, 例行修法): {problem['problem']}; {problem['fix']}"]
        return out


def report_unreadable_problem(workspace_root: Path, task_id: str, exc: Any, *, is_audit: bool) -> dict[str, Any] | None:
    """AIPOS-F232 件②(声明 transitions artifact_ingest.return.header_fix.unreadable): 读不出的文件(exc = FrontmatterReadError)是本卡报告
    (执行卡 = 交回落点 return_root/<卡> 下, 审计卡 = 裁决落点 verdict_root/<卡> 下的报告文件)= 报告格式错 {kinds, correctable: True, problem,
    fix, line_no, line_text, reason, path}; 不是(卡面 / 记录 / 他卡)= None(照旧硬停)。该行原文读不出 = 只给原因(problem_no_line)。"""
    from tools.aipos_cli.next_resolver import return_artifact_dir, verdict_artifact_dir

    decl = header_fix_declaration().get("unreadable")
    needed = ("kind", "line_chars", "problem", "problem_no_line", "fix")
    if not isinstance(decl, dict) or any(k not in decl for k in needed) or not isinstance(decl["line_chars"], int):
        from tools.schema_loader import SchemaLoadError

        raise SchemaLoadError(f"transitions.schema.json artifact_ingest.return.header_fix.unreadable({' / '.join(needed)})未声明齐(AIPOS-F232)")
    raw = str(getattr(exc, "path", "") or "")
    if not raw:
        return None
    path = Path(raw)
    slot = (verdict_artifact_dir if is_audit else return_artifact_dir)(Path(workspace_root), task_id)
    try:
        if path.resolve().parent != Path(slot).resolve():
            return None
    except OSError:
        return None
    raw_reason = str(getattr(exc, "reason", "") or exc)
    reason = " ".join(raw_reason.split())  # 一行(解析器原文多行带 ^ 指示行; 行号与该行原文另给)
    line_no = getattr(exc, "line_no", None)
    line_text = ""
    if isinstance(line_no, int) and line_no > 0:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
            line_text = lines[line_no - 1].strip()[: int(decl["line_chars"])] if line_no <= len(lines) else ""
        except (OSError, UnicodeDecodeError):
            line_text = ""
    problem = (str(decl["problem"]).format(line_no=line_no, line_text=line_text, reason=reason) if line_text
               else str(decl["problem_no_line"]).format(reason=reason))
    return {"kinds": [str(decl["kind"])], "correctable": True, "problem": problem, "fix": str(decl["fix"]), "line_no": line_no,
            "line_text": line_text, "reason": raw_reason, "path": str(path), "role": "auditor" if is_audit else "executor"}


def _with_header_repair_note(out: dict[str, Any]) -> dict[str, Any]:
    """AIPOS-F233 件⑥: 本卡报告头靠自由文本键加引号容错才读出(frontmatter.frontmatter_repair_notes 唯一判定)= 附 notes(报告原文不改)。"""
    path = out.get("path") if isinstance(out, dict) else None
    if not path:
        return out
    from tools.aipos_cli.frontmatter import frontmatter_repair_notes
    from tools.aipos_cli.next_resolver import _artifact_ingest_declaration

    try:
        text = Path(str(path)).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return out
    repairs = frontmatter_repair_notes(text)
    if repairs:
        template = str((_artifact_ingest_declaration().get("report_header_yaml") or {}).get("repair_note") or "")
        out.setdefault("notes", []).extend(template.format(**r) for r in repairs)
    return out


def _validate_task_artifact(task_id: str, workspace_root: Path) -> dict[str, Any]:
    """校验一张卡的产物(执行卡=Return; R 卡=裁决报告; external finalize 且裁决 PASS 的执行卡=FINALIZE 卡 Return)是否可铸记录。纯校验, 不提交。

    返回 {ok, kind: return|verdict|finalization, category, reasons[], path, frontmatter, exit_code}。
    fail-closed: 声明缺/文件缺/字段缺/sha 不符 = ok=False + 原文 + 出口。
    """
    from tools.schema_loader import SchemaLoadError
    from tools.aipos_cli.next_resolver import (
        _artifact_ingest_declaration,
        _check_verdict_artifact,
        _find_task_in_queue,
        _read_frontmatter,
        extract_return_summary_text,
        find_return_artifact,
        finalize_task_id_for,
        invalid_finalization_frontmatter,
        missing_return_frontmatter,
        return_artifact_dir,
        verdict_artifact_dir,
    )
    from tools.aipos_cli.audit_derivation import audit_card_reviewed_id, is_audit_card
    from tools.aipos_cli.task_loader import AmbiguousTaskCard
    from tools.aipos_cli.validation_common import report_placeholder_refusal

    workspace_root = Path(workspace_root)
    is_audit = is_audit_card(task_id)
    kind = "verdict" if is_audit else "return"
    out: dict[str, Any] = {"ok": False, "kind": kind, "category": "", "reasons": [], "path": None, "frontmatter": {},
                           "exit_code": INGEST_EXIT_REJECTED}
    try:
        task_path, _queue = _find_task_in_queue(workspace_root, task_id)
    except AmbiguousTaskCard as exc:
        out["category"], out["reasons"] = "TASK_AMBIGUOUS", [str(exc)]
        return out
    if not task_path:
        out["category"], out["reasons"] = "TASK_NOT_FOUND", [f"queue 目录中找不到任务卡 {task_id}(按 frontmatter task_id 查找, 文件名不限)"]
        return out
    # AIPOS-F112: 审计卡判据唯一实现 audit_derivation.is_audit_card(卡面为准, 认得复审轮 R2/R3…; 原按号尾 R 判)
    is_audit = is_audit_card(task_id, _read_frontmatter(task_path))
    kind = out["kind"] = "verdict" if is_audit else "return"
    if not is_audit:
        pending, records = _external_finalize_pending(workspace_root, task_id)
        if pending:
            kind = out["kind"] = "finalization"
            out["records"] = records
    try:
        decl = _artifact_ingest_declaration()[kind]
    except (SchemaLoadError, KeyError) as exc:
        out["category"], out["reasons"] = "INGEST_DECLARATION_MISSING", [f"transitions.schema artifact_ingest.{kind}: {exc}"]
        return out
    card_fm = _read_frontmatter(task_path)

    if kind == "finalization":
        # AIPOS-F78B 件②: 外部 FINALIZE 卡 Return(落点 return_root/<finalize_task_id>, 与执行体 Return 同一候选序)
        fin_id = finalize_task_id_for(task_id, card_fm)
        out["finalize_task_id"] = fin_id
        path = find_return_artifact(workspace_root, fin_id)
        if path is None:
            cands = list(_artifact_ingest_declaration()["return"].get("return_file_candidates") or [])
            out["category"] = "INGEST_RETURN_MISSING"
            out["reasons"] = [
                f"未在声明落点 {return_artifact_dir(workspace_root, fin_id)} 找到 FINALIZE 卡 {fin_id} 的 Return(候选: {', '.join(map(str, cands))})。"
                f"出口: 外部 FINALIZE 卡执行体把 Return 落到该目录(project.json paths.return_root), frontmatter 含 "
                f"{', '.join(str(k) for k in decl.get('required_frontmatter') or [])}"
            ]
            return out
        out["path"] = str(path)
        refusal = slot_code_refusal(return_artifact_dir(workspace_root, fin_id))  # AIPOS-F195 件①
        if refusal is not None:
            out["category"], out["reasons"], out["code_files"] = refusal["category"], refusal["reasons"], refusal["code_files"]
            return out
        fm = _read_frontmatter(path, allow_missing_block=True)
        out["frontmatter"] = fm
        if _apply_credential_refusal(out, workspace_root, return_artifact_dir(workspace_root, fin_id), kind="finalization", report=path,
                                     card_fm=card_fm, report_fm=fm):  # AIPOS-F239 件②
            return out
        problems = invalid_finalization_frontmatter(fm)
        if problems:
            out["category"] = "INGEST_FRONTMATTER_MISSING" if any("缺" in p for p in problems) else "INGEST_DEPLOY_STATUS_INVALID"
            out["reasons"] = [f"{path}: {p}(声明: transitions.schema artifact_ingest.finalization)" for p in problems]
            return out
        out["ok"], out["category"], out["exit_code"] = True, "OK", INGEST_EXIT_RECORDED
        return out

    # AIPOS-F197 件②: 只写治理根的卡(及其审计卡)交回契约分形(transitions artifact_ingest.<return|verdict>.governance_root):
    # 不核卡仓 / 卡分支(本卡无卡分支, 治理根不是产品仓), 改核产出清单(唯一实现 next_resolver.governance_outputs_manifest)
    from tools.aipos_cli.next_resolver import card_governance_outputs

    governance = card_governance_outputs(workspace_root, {**card_fm, "task_id": str(card_fm.get("task_id") or task_id)})
    # AIPOS-F78C 件②: 核 tip/被审分支的仓 = 该卡(R 卡: 被审卡)声明的仓; 解析不到 = 声明缺失出口(非崩溃), 不进 git
    repo_fm: dict[str, Any] = {}
    if is_audit:
        reviewed_for_repo = audit_card_reviewed_id(task_id, card_fm)
        try:
            reviewed_path, _rq = _find_task_in_queue(workspace_root, reviewed_for_repo)
        except AmbiguousTaskCard as exc:
            out["category"], out["reasons"] = "TASK_AMBIGUOUS", [str(exc)]
            return out
        repo_fm = _read_frontmatter(reviewed_path) if reviewed_path else {}
        code_repo, repo_problem = (workspace_root, {}) if governance else _code_repo_for(workspace_root, reviewed_for_repo, repo_fm)
    else:
        code_repo, repo_problem = (workspace_root, {}) if governance else _code_repo_for(workspace_root, task_id, card_fm)
    if code_repo is None:
        out["category"], out["reasons"] = repo_problem["category"], repo_problem["reasons"]
        return out

    if not is_audit:
        path = find_return_artifact(workspace_root, task_id)
        if path is None:
            cands = list(decl.get("return_file_candidates") or [])
            out["category"] = "INGEST_RETURN_MISSING"
            out["reasons"] = [
                f"未在声明落点 {return_artifact_dir(workspace_root, task_id)} 找到 Return(候选: {', '.join(map(str, cands))})。"
                f"出口: 执行体把 Return 落到该目录(project.json paths.return_root 声明), 或修正声明"
            ]
            return out
        out["path"] = str(path)
        # AIPOS-F195 件①: 报告落点里的代码文件入门即拦(与产物形校验同处, 早于结案后 N6 提交门 B①)
        refusal = slot_code_refusal(return_artifact_dir(workspace_root, task_id))
        if refusal is not None:
            out["category"], out["reasons"], out["code_files"] = refusal["category"], refusal["reasons"], refusal["code_files"]
            return out
        fm = _read_frontmatter(path, allow_missing_block=True)
        out["frontmatter"] = fm
        missing = missing_return_frontmatter(fm, governance=governance)
        if missing:
            where = "return.governance_root.required_frontmatter(只写治理根卡: 产出清单, 无分支三键)" if governance else "return.required_frontmatter"
            # AIPOS-F204 件①: 补法 = 本卡开工提示同一契约(候选主体卡 = 产品给值; 误用另一契约另附说明), 唯一实现 return_header_problem
            problem = out["header_problem"] = return_header_problem(workspace_root, task_id, card_fm, fm, missing=missing)
            out["category"] = "INGEST_FRONTMATTER_MISSING"
            out["reasons"] = [
                f"{path}: frontmatter 缺 {', '.join(missing)}(声明: transitions.schema artifact_ingest.{where})。"
                f"出口: 执行体补齐后产品自动重试(loop 重推导)。补法: {problem['fix']}" + (f"。{problem['note']}" if problem["note"] else "")
            ]
            return out
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            out["category"], out["reasons"] = "INGEST_RETURN_UNREADABLE", [f"{path}: {exc}"]
            return out
        # AIPOS-F148 件②: 摘要来源读声明(transitions artifact_ingest.return.summary_source + 项目 return_summary_source,
        # 唯一读取口 workspace_config.return_summary_source), 与推导核就绪判据同一解析 extract_return_summary_text; 拒因同一文案
        from tools.aipos_cli.next_resolver import return_summary_missing_text
        from tools.aipos_cli.workspace_config import ReturnSummarySourceError, return_summary_source

        try:
            summary_source = return_summary_source(workspace_root)
        except (ReturnSummarySourceError, OSError, ValueError) as exc:
            out["category"], out["reasons"] = "INGEST_SUMMARY_MISSING", [
                f"{path}: 本项目 Return 摘要来源声明不可用(fail-closed): {exc}。出口: lybra project set-return-summary 重新声明"]
            return out
        if not extract_return_summary_text(content, summary_source):
            out["category"], out["reasons"] = "INGEST_SUMMARY_MISSING", [return_summary_missing_text(path, summary_source)]
            return out
        # AIPOS-F215 件①(gap #219): 正文仍有占位 = 拒(判定唯一实现 validation_common.report_body_placeholders, 审计报告同判)
        unfilled = report_placeholder_refusal(content, path, "return")
        if unfilled is not None:
            out["category"], out["reasons"], out["placeholders"] = unfilled["category"], unfilled["reasons"], unfilled["placeholders"]
            return out
        # AIPOS-F239 件②: 交回报告 / 报告落点 / outputs / output_target 含明文凭据 = 拒(判据唯一实现 credential_scan, 拒因不回显值)
        if _apply_credential_refusal(out, workspace_root, return_artifact_dir(workspace_root, task_id), kind="return", report=path,
                                     card_fm=card_fm, report_fm=fm):
            return out
        # AIPOS-F230 件②: 配置 / 文档卡交回产物含可执行程序 = 拒并指路改走代码卡(判据唯一实现 program_scan)
        # AIPOS-F236 件①: 他仓已入库代码的输入副本不算程序, 结果列 input_copies 并附注记(审计可见)
        scan = program_scan(workspace_root, task_id, card_fm, fm)
        program = program_guard_refusal(workspace_root, task_id, card_fm, scan)
        if program is not None:
            out["category"], out["reasons"], out["program_files"] = program["category"], program["reasons"], program["program_files"]
            out["input_copies"], out["legal_exits"] = program["input_copies"], program["legal_exits"]
            return out
        if scan["input_copies"]:
            out["input_copies"] = scan["input_copies"]
            out.setdefault("notes", []).append(input_copy_note(scan["input_copies"]) or "")
        if governance:
            # AIPOS-F197 件②: 产出清单逐个核对并计算 sha256(唯一实现 governance_outputs_manifest; 门交回记录随后绑定同一清单)
            from tools.aipos_cli.next_resolver import GovernanceOutputsError, governance_outputs_manifest

            try:
                manifest = governance_outputs_manifest(workspace_root, task_id, card_fm, fm)
            except GovernanceOutputsError as exc:
                out["category"], out["reasons"] = exc.code, [
                    f"{path}: {exc.reason}(声明: transitions.schema artifact_ingest.return.governance_root)。出口: 执行体修正产出或 "
                    f"Return frontmatter outputs 后产品自动重试(loop 重推导)"]
                return out
            out["outputs_manifest"] = manifest
            out["ok"], out["category"], out["exit_code"] = True, "OK", INGEST_EXIT_RECORDED
            return out
        # AIPOS-F78C 件③: Return 自述 repo(可选, 声明 artifact_ingest.return.optional_frontmatter)须与卡 lane.repo 解析仓一致
        optional = [str(k) for k in (decl.get("optional_frontmatter") or [])]
        self_repo = str(fm.get("repo") or "").strip() if "repo" in optional else ""
        if self_repo:
            from tools.aipos_cli.workspace_config import CardRepoUnresolved, resolve_card_repo

            try:
                self_repo_path = resolve_card_repo(workspace_root, {"task_id": task_id, "lane": {"repo": self_repo}})
            except CardRepoUnresolved as exc:
                self_repo_path = None
                self_repo_reason = exc.reason
            if self_repo_path is None or self_repo_path.resolve() != code_repo.resolve():
                out["category"] = "INGEST_REPO_MISMATCH"
                out["reasons"] = [
                    f"{path}: Return 自述 repo={self_repo!r} → {self_repo_path or '不可解析: ' + self_repo_reason} ≠ 卡 {task_id} lane.repo 解析仓 {code_repo}。"
                    "出口: 一卡一仓——执行体把产物提交到卡声明的仓, 或修正 Return frontmatter repo; 跨仓改动拆卡"
                ]
                return out
        # AIPOS-F196 件①: 交付主体为固定候选的卡(validation)= 核候选三键(candidate_subject_values 唯一实值), 不核卡分支 tip
        candidate_check = _candidate_subject_check(workspace_root, task_id, card_fm, fm, path, kind="return")
        if candidate_check is not None:
            mismatched = candidate_check.pop("mismatched", None)
            if mismatched:  # AIPOS-F204 件②: 三键 ≠ 产品给值 = 报告头可纠正(判定与补法唯一实现 return_header_problem)
                out["header_problem"] = return_header_problem(workspace_root, task_id, card_fm, fm, mismatched=mismatched)
            out.update(candidate_check)
            return out
        branch = str(fm.get("branch")).strip()
        commit_sha = str(fm.get("commit_sha")).strip()
        tree_hash = str(fm.get("tree_hash")).strip()
        tip = _git_out(code_repo, "rev-parse", "--verify", f"{branch}^{{commit}}")
        if tip is None:
            out["category"], out["reasons"] = "INGEST_BRANCH_MISSING", [f"{code_repo}: 分支 {branch} 不存在(frontmatter.branch)"]
            return out
        if tip != commit_sha:
            # AIPOS-F180B item 1: full values on both sides + form diagnosis (single implementation tip_mismatch_diagnosis)
            diag = out["tip_diagnosis"] = tip_mismatch_diagnosis(code_repo, tip, commit_sha, branch=branch)
            out["header_problem"] = return_header_problem(workspace_root, task_id, card_fm, fm, form=diag)  # AIPOS-F204 件②
            out["category"] = "INGEST_TIP_MISMATCH"
            out["reasons"] = [
                f"分支 {branch} tip={tip} ≠ Return 自述 commit_sha={commit_sha}。形式诊断: {diag['text']}。"
                f"出口: 执行体以分支 tip 更新 Return frontmatter(交回的必须是当前产物)"
            ]
            return out
        tree = _git_out(code_repo, "rev-parse", f"{commit_sha}^{{tree}}")
        if tree != tree_hash:
            if tree:  # AIPOS-F204 件②: tree_hash 写错 = 报告头可纠正(应为该 commit 的 tree)
                out["header_problem"] = return_header_problem(workspace_root, task_id, card_fm, fm, mismatched={"tree_hash": (tree_hash, tree)})
            out["category"] = "INGEST_TREE_MISMATCH"
            out["reasons"] = [f"{commit_sha[:12]} tree={str(tree)[:12]} ≠ Return 自述 tree_hash={tree_hash[:12]}"]
            return out
        out["ok"], out["category"], out["exit_code"] = True, "OK", INGEST_EXIT_RECORDED
        return out

    # R 卡: 裁决报告
    path = _check_verdict_artifact(workspace_root, task_id)
    if path is None:
        out["category"] = "INGEST_VERDICT_MISSING"
        out["reasons"] = [
            f"未在声明落点找到已完成的审计报告(project.json paths.verdict_root / {task_id}; "
            f"候选: {', '.join(map(str, decl.get('verdict_file_candidates') or []))}; 完成判据 artifact_ingest.verdict.readiness: "
            f"{'/'.join(map(str, decl.get('required_frontmatter') or []))} 已填且非占位, 认领时的空模板不算)"
        ]
        return out
    out["path"] = str(path)
    refusal = slot_code_refusal(verdict_artifact_dir(workspace_root, task_id))  # AIPOS-F195 件①: 审计落点同理
    if refusal is not None:
        out["category"], out["reasons"], out["code_files"] = refusal["category"], refusal["reasons"], refusal["code_files"]
        return out
    fm = _read_frontmatter(path, allow_missing_block=True)
    out["frontmatter"] = fm
    # AIPOS-F89 件③c: 报告完成判据唯一实现 next_resolver.missing_verdict_frontmatter(占位 = 未填, 空模板不算产物)
    from tools.aipos_cli.next_resolver import card_branch_name, missing_verdict_frontmatter

    missing = missing_verdict_frontmatter(fm, governance=governance)
    if missing:
        where = "verdict.governance_root.required_frontmatter" if governance else "verdict.required_frontmatter"
        out["category"] = "INGEST_FRONTMATTER_MISSING"
        out["reasons"] = [f"{path}: frontmatter 缺 {', '.join(missing)}(声明: transitions.schema artifact_ingest.{where})"]
        return out
    # AIPOS-F215 件①(gap #219): 审计报告正文仍有占位 = 拒(与交回报告同一判定 report_placeholder_refusal)
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        out["category"], out["reasons"] = "INGEST_VERDICT_UNREADABLE", [f"{path}: {exc}"]
        return out
    unfilled = report_placeholder_refusal(content, path, "verdict")
    if unfilled is not None:
        out["category"], out["reasons"], out["placeholders"] = unfilled["category"], unfilled["reasons"], unfilled["placeholders"]
        return out
    # AIPOS-F239 件②: 审计报告 / 审计落点含明文凭据 = 拒(与交回同一判据; loop 续接审计会话脱敏一次, verbs lybra_loop.launch.report_fix.artifact)
    if _apply_credential_refusal(out, workspace_root, verdict_artifact_dir(workspace_root, task_id), kind="verdict", report=path,
                                 card_fm=card_fm, report_fm=fm):
        return out
    # AIPOS-F227 件①: 报告 fail_scope 值不在声明内 = 拒(唯一判定 next_resolver.fail_scope_problem; 未写 = 放行, 推导核按 candidate 兼容)
    from tools.aipos_cli.next_resolver import fail_scope_problem

    scope_problem = fail_scope_problem(fm, path=path)
    if scope_problem is not None:
        out["category"], out["reasons"] = "INGEST_FAIL_SCOPE_INVALID", [scope_problem]
        return out
    reviewed =str(fm.get("reviewed_task_id") or "").strip() or audit_card_reviewed_id(task_id, card_fm)
    if governance:
        # AIPOS-F197 件②: 被审卡只写治理根 = 报告 outputs_digest 须 = 交回绑定指纹 = 此刻指纹(governance_outputs_recheck 唯一实现)
        from tools.aipos_cli.next_resolver import governance_outputs_recheck

        recheck = governance_outputs_recheck(workspace_root, reviewed, repo_fm)
        reported = str(fm.get("outputs_digest") or "").strip()
        if not recheck["ok"] or reported != str((recheck.get("bound") or {}).get("outputs_digest") or ""):
            out["category"] = recheck["code"] if not recheck["ok"] else "INGEST_OUTPUTS_CHANGED"
            out["reasons"] = [recheck["reason"] if not recheck["ok"] else
                              f"{path}: 报告 outputs_digest={reported!r} ≠ 被审卡 {reviewed} 交回绑定指纹 "
                              f"{recheck['bound']['outputs_digest']}(审的必须是交回的那份产出); 出口: 报告照抄开工提示里产品给出的值"]
            return out
        from tools.aipos_cli.board_adapter import runtime_evidence_check

        evidence = runtime_evidence_check(workspace_root, task_id, card_fm, report_path=path, report_fm=fm)
        if evidence is not None:
            out["category"], out["reasons"] = evidence["category"], evidence["reasons"]
            return out
        out["outputs_manifest"] = recheck["current"]
        out["ok"], out["category"], out["exit_code"] = True, "OK", INGEST_EXIT_RECORDED
        return out
    # AIPOS-F196 件①: 被审卡交付主体为固定候选(validation)= 报告 commit_sha 核候选, 不核被审卡分支 tip
    candidate_check = _candidate_subject_check(workspace_root, reviewed, repo_fm, fm, path, kind="verdict")
    if candidate_check is not None:
        if candidate_check["ok"]:
            from tools.aipos_cli.board_adapter import runtime_evidence_check

            evidence = runtime_evidence_check(workspace_root, task_id, card_fm, report_path=path, report_fm=fm)
            if evidence is not None:
                out["category"], out["reasons"] = evidence["category"], evidence["reasons"]
                return out
        out.update(candidate_check)
        return out
    branch = card_branch_name(reviewed)  # AIPOS-F108 件②: 唯一读取口(声明缺 = SchemaLoadError, 不回落写死)
    tip = _git_out(code_repo, "rev-parse", "--verify", f"{branch}^{{commit}}")
    commit_sha = str(fm.get("commit_sha")).strip()
    if tip is None:
        out["category"], out["reasons"] = "INGEST_BRANCH_MISSING", [f"{code_repo}: 被审卡分支 {branch} 不存在"]
        return out
    if tip != commit_sha:
        # AIPOS-F180B item 1: full values on both sides + form diagnosis; correctable = report header only (loop resumes the
        # same audit session, verbs.schema lybra_loop.launch.report_fix); other_commit keeps the F114 round outlet
        diag = out["tip_diagnosis"] = tip_mismatch_diagnosis(code_repo, tip, commit_sha, branch=branch)
        head = f"被审分支 {branch} tip={tip} ≠ 报告自述 commit_sha={commit_sha}。形式诊断: {diag['text']}"
        out["category"] = "INGEST_TIP_MISMATCH"
        out["reasons"] = [f"{head}。出口: {diag['outlet']}" if diag["correctable"]
                          else f"{head}: {diag['outlet']}" + _stale_round_outlet(workspace_root, task_id, card_fm, reviewed)]
        return out
    # AIPOS-F162 件① / AIPOS-F163: 运行证据硬门的判据在门侧裁决写入唯一口(board_adapter.runtime_evidence_check, 门
    # _build_audit_verdict_preview 对一切裁决提交路径同核); 此处调用同一判据预检, 拒收即不提交(拒因码同门侧)
    from tools.aipos_cli.board_adapter import runtime_evidence_check

    evidence = runtime_evidence_check(workspace_root, task_id, card_fm, report_path=path, report_fm=fm)
    if evidence is not None:
        out["category"], out["reasons"] = evidence["category"], evidence["reasons"]
        return out
    out["ok"], out["category"], out["exit_code"] = True, "OK", INGEST_EXIT_RECORDED
    return out


def _candidate_subject_check(workspace_root: Path, subject_task_id: str, subject_fm: dict[str, Any] | None, report_fm: dict[str, Any],
                             path: Path, *, kind: str) -> dict[str, Any] | None:
    """AIPOS-F196 件①: 交付主体为固定候选的卡(enums.schema task_mode.candidate_subject)的报告核对(return / verdict 同一实现)。
    None = 被核卡非 candidate 模式(调用方走卡分支 tip 判据); 否则 {ok, category, reasons[, exit_code]}:
    候选声明不合 = INGEST_CANDIDATE_INVALID; 报告三键(return: commit_sha / tree_hash / branch; verdict: commit_sha)≠ 产品给值 = INGEST_CANDIDATE_MISMATCH。"""
    from tools.aipos_cli.next_resolver import CandidateSubjectError, candidate_subject_values

    try:
        values = candidate_subject_values(workspace_root, subject_task_id, subject_fm or {})
    except CandidateSubjectError as exc:
        return {"ok": False, "category": "INGEST_CANDIDATE_INVALID",
                "reasons": [f"卡 {subject_task_id} 固定候选不可用: {exc}。出口: 顾问 lybra queue amend 修正来源卡 / 候选(enums.schema task_mode.candidate_subject)"]}
    if values is None:
        return None
    keys = ("commit_sha", "tree_hash", "branch") if kind == "return" else ("commit_sha",)
    mismatched = {k: (str(report_fm.get(k) or "").strip(), values[k]) for k in keys if str(report_fm.get(k) or "").strip() != values[k]}
    wrong = [f"{k}={got!r} ≠ 产品给出 {want}" for k, (got, want) in mismatched.items()]
    if wrong:
        return {"ok": False, "category": "INGEST_CANDIDATE_MISMATCH", "mismatched": mismatched,
                "reasons": [f"{path}: 本卡交付主体 = 来源卡 {values['source']} 的固定候选 {values['commit_sha']}(验证卡不建卡分支): "
                            + "; ".join(wrong) + "。出口: 报告 frontmatter 照抄开工提示里产品给出的值"]}
    return {"ok": True, "category": "OK", "reasons": [], "exit_code": INGEST_EXIT_RECORDED}


def header_fix_declaration() -> dict[str, Any]:
    """AIPOS-F204: transitions.schema artifact_ingest.return.header_fix(唯一读取口)。缺 / 形不合 = SchemaLoadError(fail-closed)。"""
    from tools.aipos_cli.next_resolver import _artifact_ingest_declaration
    from tools.schema_loader import SchemaLoadError

    decl = (_artifact_ingest_declaration().get("return") or {}).get("header_fix")
    kinds = decl.get("correctable_kinds") if isinstance(decl, dict) else None
    misuse = decl.get("contract_misuse") if isinstance(decl, dict) else None
    if (not isinstance(kinds, list) or not kinds or not isinstance(misuse, dict)
            or any(not isinstance(misuse.get(k), str) or not misuse[k].strip() for k in ("candidate", "branch", "governance"))):
        raise SchemaLoadError("transitions.schema.json artifact_ingest.return.header_fix(correctable_kinds / contract_misuse.candidate|branch|"
                              "governance)未声明齐(AIPOS-F204)")
    return decl


def return_header_problem(workspace_root: Path, task_id: str, card_fm: dict[str, Any], return_fm: dict[str, Any], *,
                          missing: list[str] | None = None, mismatched: dict[str, tuple[str, str]] | None = None,
                          form: dict[str, Any] | None = None) -> dict[str, Any]:
    """AIPOS-F204 件①②(声明 transitions artifact_ingest.return.header_fix): 执行卡交回报告头错误的唯一判定与补法渲染。

    调用方给出已判定的错处(missing = 缺 / 占位的必填键; mismatched = {键: (报告值, 应为值)}; form = 卡分支 tip_mismatch_diagnosis 结果);
    契约分形与逐键说明 = 本卡开工提示同一契约(next_resolver.card_report_contract), 另判误用另一契约的键(contract_misuse)。
    返回 {contract, kinds, missing, mismatched, misused, correctable, problem, fix, note, keys}:
      fix = 需改键逐键 `键 = 产品给值(说明)`(产品无值 = `键 = 说明`); note = 误用另一契约的说明句(无 = 空);
      problem = 判定原文(拒因 / 修头指令同读)。候选声明不合 = contract=invalid、correctable=False(出口照旧 amend)。"""
    from tools.aipos_cli.next_resolver import (
        CandidateSubjectError, _is_placeholder_value, candidate_subject_values, card_branch_name, card_governance_outputs,
        card_report_contract, required_return_frontmatter,
    )

    decl = header_fix_declaration()
    fm = {**(card_fm or {}), "task_id": str((card_fm or {}).get("task_id") or task_id)}
    missing = [str(k) for k in (missing or [])]
    mismatched = dict(mismatched or {})
    out: dict[str, Any] = {"contract": "", "kinds": [], "missing": missing, "mismatched": {k: list(v) for k, v in mismatched.items()},
                           "misused": [], "correctable": False, "problem": "", "fix": "", "note": "", "keys": []}
    try:
        candidate = candidate_subject_values(Path(workspace_root), task_id, fm)
        governance = candidate is None and card_governance_outputs(Path(workspace_root), fm)
        entries = card_report_contract(Path(workspace_root), task_id, fm)
    except CandidateSubjectError as exc:
        out["contract"] = "invalid"
        out["problem"] = f"卡 {task_id} 固定候选不可用({exc}), 产品给不出交回三键"
        out["fix"] = "顾问 lybra queue amend 修正来源卡 / 候选(enums.schema task_mode.candidate_subject)"
        return out
    contract = "candidate" if candidate is not None else "governance" if governance else "branch"
    out["contract"] = contract
    keys = [str(e["key"]) for e in entries]
    out["keys"] = keys
    other_keys = required_return_frontmatter(governance=not governance)
    misused = [k for k in other_keys if k not in keys and not _is_placeholder_value((return_fm or {}).get(k))]
    out["misused"] = misused
    expected = {str(e["key"]): e.get("value") for e in entries}
    hints = {str(e["key"]): str(e["hint"]) for e in entries}
    kinds: list[str] = []
    parts: list[str] = []
    need: list[str] = []
    if missing:
        kinds.append("missing")
        parts.append(f"缺 {', '.join(missing)}")
        need += missing
    if mismatched:
        kinds.append("mismatch")
        parts.append("; ".join(f"{k}={got!r} ≠ 应为 {want}" for k, (got, want) in mismatched.items()))
        for key, (_got, want) in mismatched.items():
            expected[key] = want
            need.append(key)
    if form:
        kinds.append("form")
        parts.append(f"commit_sha={form.get('commit_sha')} ≠ 卡分支 {form.get('branch')} tip={form.get('tip')}(形式诊断: {form.get('text')})")
        expected["commit_sha"] = str(form.get("tip") or "") or expected.get("commit_sha")
        need.append("commit_sha")
    if misused:
        kinds.append("contract_misuse")
        what = {"candidate": candidate or {}, "branch": {"branch": card_branch_name(task_id)}}.get(contract, {})
        note = str(decl["contract_misuse"][contract])
        for key, value in (("source", what.get("source", "")), ("branch", what.get("branch", "")), ("keys", " / ".join(keys)),
                           ("misused", " / ".join(misused))):
            note = note.replace("{" + key + "}", str(value))
        out["note"] = note
        parts.append(note)
    out["kinds"] = kinds
    ordered = [k for k in keys if k in need] + [k for k in need if k not in keys]
    out["fix"] = "; ".join(f"{k} = {expected[k]}({hints[k]})" if expected.get(k) and k in hints else
                           f"{k} = {expected[k]}" if expected.get(k) else f"{k} = {hints.get(k, '按声明填写实值')}"
                           for k in dict.fromkeys(ordered))
    out["problem"] = "; ".join(p for p in parts if p)
    allowed = {str(k) for k in decl["correctable_kinds"]}
    out["correctable"] = bool(kinds) and all(k in allowed for k in kinds) and (form is None or bool(form.get("correctable")))
    return out


# ---------------------------------------------------------------------------
# AIPOS-F162 件①: 运行证据硬门——声明 transitions.schema artifact_ingest.verdict.runtime_evidence(唯一);
# 本节 = 声明读取 / 卡面声明解析 / 证据节解析 / 要求段渲染。判据本身(AIPOS-F163 下沉)在门侧裁决写入唯一口
# board_adapter.runtime_evidence_check(门 _build_audit_verdict_preview、本模块 _validate_task_artifact 预检、推导核 next_resolver 同读)
# ---------------------------------------------------------------------------

class RuntimeEvidenceDeclarationError(ValueError):
    """卡面 audit_runtime_evidence 形不对(fail-closed: 不当未声明放行)。"""


def runtime_evidence_declaration() -> dict[str, Any]:
    """transitions artifact_ingest.verdict.runtime_evidence 唯一读取口。缺 / 关键键缺 = SchemaLoadError。"""
    from tools.aipos_cli.next_resolver import _artifact_ingest_declaration
    from tools.schema_loader import SchemaLoadError

    decl = (_artifact_ingest_declaration().get("verdict") or {}).get("runtime_evidence")
    needed = ("card_field", "applies_to_verdicts", "section_heading", "block_language", "entry_required_fields", "log_fields",
              "placeholders", "rejection_category", "declaration_invalid_category", "loop")
    if not isinstance(decl, dict) or any(not decl.get(k) for k in needed):
        raise SchemaLoadError(f"transitions.schema.json artifact_ingest.verdict.runtime_evidence({'/'.join(needed)})未声明")
    return decl


def runtime_evidence_requirement(audit_fm: dict[str, Any] | None, reviewed_fm: dict[str, Any] | None) -> dict[str, Any] | None:
    """卡面运行证据要求: 审计卡自身声明优先(补充审计写入), 缺省读被审卡。返回 {commands, source} 或 None(未声明 / required 非 true)。
    形不对 = RuntimeEvidenceDeclarationError。draft 校验与裁决入口同用本函数解析。"""
    key = str(runtime_evidence_declaration()["card_field"])
    raw, source = None, ""
    for fm, where in ((audit_fm, "audit_card"), (reviewed_fm, "reviewed_card")):
        if isinstance(fm, dict) and fm.get(key) not in (None, ""):
            raw, source = fm.get(key), where
            break
    if raw is None:
        return None
    return parse_runtime_evidence_declaration(raw, source=source)


def parse_runtime_evidence_declaration(raw: Any, *, source: str = "card") -> dict[str, Any] | None:
    key = str(runtime_evidence_declaration()["card_field"])
    if not isinstance(raw, dict):
        raise RuntimeEvidenceDeclarationError(f"{key} 须为对象 {{required: true|false, commands: [...]}}, 实为 {type(raw).__name__}")
    unknown = sorted(set(raw) - {"required", "commands", "host"})
    if unknown:
        raise RuntimeEvidenceDeclarationError(f"{key} 含未声明的键 {unknown}(只认 required / commands / host)")
    host = raw.get("host")  # AIPOS-F223 件①: 可选执行主机(主机标签形 = 验证根声明 host 同一形校验 validation_root.is_host_label; 缺省 = 不指定)
    from tools.aipos_cli.validation_root import is_host_label

    if host is not None and (not isinstance(host, str) or not is_host_label(host.strip())):
        raise RuntimeEvidenceDeclarationError(f"{key}.host 须为主机标签(字母数字开头, 只含字母数字 . _ -, 至多 63 字符; 同 project.json "
                                              f"validation_roots 声明的 host), 实为 {host!r}")
    required = raw.get("required")
    if not isinstance(required, bool):
        raise RuntimeEvidenceDeclarationError(f"{key}.required 须为布尔 true/false, 实为 {required!r}")
    commands = raw.get("commands", [])
    if commands is None:
        commands = []
    if not isinstance(commands, list) or any(not isinstance(c, str) or not c.strip() for c in commands):
        raise RuntimeEvidenceDeclarationError(f"{key}.commands 须为非空命令串列表, 实为 {commands!r}")
    if not required:
        return None
    return {"commands": [c.strip() for c in commands], "source": source, "host": host.strip() if isinstance(host, str) else None,
            "declaration": dict(raw)}


def runtime_evidence_amend_problems(current: Any, new: Any, *, claimed: bool, card_fm: dict[str, Any] | None = None) -> list[str]:
    """AIPOS-F223 件②: queue amend 改卡面 audit_runtime_evidence 的判据(唯一实现; `lybra queue amend` 调用)。新值形校验同出卡
    (parse_runtime_evidence_declaration); claimed 卡(--restricted)只许改 card.schema restricted_amend.claimed_card_subfields 所列子键,
    其余子键须与卡面原值相同(不改命令 / required = 不改验收)。AIPOS-F233 件④: restricted_amend.render_equivalent_subfields 所列子键
    (commands)改前改后按占位词表渲染等价(evidence_commands_render_equivalent; 交付主体 = card_fm)= 放行(只改占位名, 不改验收)。
    词表外的 {xxx} 是字面文本, 不拒(提示由 normalize_runtime_evidence_declaration 给)。返回拒因原文(空 = 通过)。"""
    key = str(runtime_evidence_declaration()["card_field"])
    try:
        parse_runtime_evidence_declaration(new)
    except RuntimeEvidenceDeclarationError as exc:
        return [f"{key} 新值形不合: {exc}"]
    if not claimed:
        return []
    allowed = set(restricted_amend_subfields().get(key) or [])
    equivalent = set(render_equivalent_subfields().get(key) or [])
    before = dict(current) if isinstance(current, dict) else {}
    if isinstance(before.get("commands"), str):  # 卡面声明形不合(commands 写成单串)= 按单条列表比(形修正不改验收, AIPOS-F233 件③ 出口)
        before["commands"] = [before["commands"]]
    changed = sorted(k for k in set(before) | set(new) if before.get(k) != new.get(k))
    subject = evidence_subject(card_fm)
    illegal = [k for k in changed if k not in allowed
               and not (k in equivalent and evidence_commands_render_equivalent(before.get(k) or [], new.get(k) or [], subject))]
    if illegal:
        return [f"已认领卡受限 amend 只许改 {key} 的子键 {sorted(allowed)}(card.schema restricted_amend.claimed_card_subfields)"
                + (f", 及按占位词表渲染等价的 {sorted(equivalent)}(只改占位名 / 别名规范化, AIPOS-F233; card.schema "
                   "restricted_amend.render_equivalent_subfields)" if equivalent else "")
                + f", 实改了 {illegal}; 命令 / required 属验收, 在途不改(须改 = 走返工轮或撤卡重发)"]
    return []


def render_equivalent_subfields() -> dict[str, list[str]]:
    """AIPOS-F233 件④: card.schema restricted_amend.render_equivalent_subfields(字段 → 渲染等价时可改子键)唯一读取口。缺 = {}(行为不变)。
    形不合 = SchemaLoadError。"""
    from tools.schema_loader import SchemaLoadError, load_schema

    raw = (load_schema("card").get("restricted_amend") or {}).get("render_equivalent_subfields")
    if raw is None:
        return {}
    out = {k: v for k, v in raw.items() if k != "description"} if isinstance(raw, dict) else None
    if out is None or any(not isinstance(v, list) or not v or any(not isinstance(s, str) or not s for s in v) for v in out.values()):
        raise SchemaLoadError("card.schema.json restricted_amend.render_equivalent_subfields 须为 {字段: [子键, …]}")
    return out


# ---------------------------------------------------------------------------
# AIPOS-F233 件①②③④: 审计 / 验证命令占位统一词表(声明 transitions artifact_ingest.verdict.runtime_evidence.placeholder_vocabulary)。
# 渲染(裁决入口 / 开工稿 / 派审步骤)、发卡规范化(draft create / publish / queue amend / 补审派发)、受限 amend 等价判据与
# 返工自动放行范围判据全部经本节唯一实现; 原 F199 旧占位迁移提示(runtime_evidence_legacy_problems)退役。
# ---------------------------------------------------------------------------

_VOCAB_SUBJECTS = ("branch", "candidate")
_SUBJECT_LABELS = {"branch": "卡分支", "candidate": "固定候选"}
_VOCAB_TEXTS = ("rewrite", "literal", "literal_suggestion", "declaration_invalid", "amend_outlet", "amend_suggestion")


def placeholder_vocabulary() -> dict[str, Any]:
    """placeholder_vocabulary 唯一读取口 + 加载守卫(fail-closed): 主体恰为 branch / candidate; 每个占位名恰属一个语义; 各主体规范名 =
    该主体已声明占位(branch = runtime_evidence.placeholders, candidate = candidate_subject.placeholders); 两主体已声明占位全部入表;
    placeholder_pattern 可编译且恰一个捕获组; 文案齐。返回 {pattern, subjects, meanings, by_name: 名 → 语义, texts}。"""
    from tools.schema_loader import SchemaLoadError

    decl = runtime_evidence_declaration().get("placeholder_vocabulary")
    where = "transitions.schema.json artifact_ingest.verdict.runtime_evidence.placeholder_vocabulary"
    if not isinstance(decl, dict):
        raise SchemaLoadError(f"{where} 未声明(AIPOS-F233)")
    try:
        pattern = re.compile(str(decl.get("placeholder_pattern") or ""))
    except re.error as exc:
        raise SchemaLoadError(f"{where}.placeholder_pattern 不可编译: {exc}") from exc
    if pattern.groups != 1 or not str(decl.get("placeholder_pattern") or ""):
        raise SchemaLoadError(f"{where}.placeholder_pattern 须恰一个捕获组(占位名)")
    subjects = decl.get("subjects")
    if not isinstance(subjects, dict) or set(subjects) != set(_VOCAB_SUBJECTS):
        raise SchemaLoadError(f"{where}.subjects 须恰为 {list(_VOCAB_SUBJECTS)}")
    texts = decl.get("texts")
    if not isinstance(texts, dict) or any(not str(texts.get(k) or "").strip() for k in _VOCAB_TEXTS):
        raise SchemaLoadError(f"{where}.texts 须声明齐 {list(_VOCAB_TEXTS)}")
    declared = {"branch": {str(k) for k in runtime_evidence_declaration()["placeholders"]},
                "candidate": {str(k) for k in runtime_evidence_candidate_declaration()["placeholders"]}}
    by_name: dict[str, dict[str, Any]] = {}
    meanings = decl.get("meanings")
    if not isinstance(meanings, list) or not meanings:
        raise SchemaLoadError(f"{where}.meanings 须为非空列表")
    for meaning in meanings:
        names = meaning.get("names") if isinstance(meaning, dict) else None
        canonical = meaning.get("canonical") if isinstance(meaning, dict) else None
        if (not isinstance(names, list) or not names or any(not isinstance(n, str) or not pattern.fullmatch("{" + n + "}") for n in names)
                or not isinstance(canonical, dict) or set(canonical) != set(_VOCAB_SUBJECTS)
                or any(str(canonical[s]) not in declared[s] for s in _VOCAB_SUBJECTS) or not str(meaning.get("id") or "").strip()):
            raise SchemaLoadError(f"{where}.meanings 项形不合(须 id / names[占位名] / canonical{{branch, candidate}} = 各主体已声明占位): {meaning!r}")
        for name in names:
            if name in by_name:
                raise SchemaLoadError(f"{where}: 占位名 {name!r} 同时属语义 {by_name[name]['id']} 与 {meaning['id']}(每个名恰属一个语义)")
            by_name[name] = meaning
    missing = sorted((declared["branch"] | declared["candidate"]) - set(by_name))
    if missing:
        raise SchemaLoadError(f"{where}: 已声明占位 {missing} 不在任何语义 names 内(两主体占位须全部入表)")
    return {"pattern": pattern, "subjects": subjects, "meanings": meanings, "by_name": by_name, "texts": texts}


def evidence_subject(card_fm: dict[str, Any] | None) -> str:
    """声明所在卡(被审卡)的交付主体: 固定候选(enums.schema task_mode.candidate_subject, F196 判据)= candidate, 其余 = branch。"""
    from tools.aipos_cli.next_resolver import task_mode_subject_is_candidate

    return "candidate" if task_mode_subject_is_candidate(str((card_fm or {}).get("task_mode") or "")) else "branch"


def canonicalize_evidence_command(command: str, subject: str, vocab: dict[str, Any] | None = None) -> dict[str, Any]:
    """一条声明命令按交付主体规范化: 别名改写为该主体规范名(语义不变); 词表外的 {xxx} 是字面文本, 原样保留。
    返回 {command, pairs: [(原名, 规范名)], unknown: [词表外 {名} 的名(字面)]}。"""
    vocab = vocab or placeholder_vocabulary()
    pairs: list[tuple[str, str]] = []
    unknown: list[str] = []

    def swap(match: re.Match[str]) -> str:
        name = match.group(1)
        meaning = vocab["by_name"].get(name)
        if meaning is None:
            if name not in unknown:
                unknown.append(name)
            return match.group(0)
        target = str(meaning["canonical"][subject])
        if target != name and (name, target) not in pairs:
            pairs.append((name, target))
        return "{" + target + "}"

    return {"command": vocab["pattern"].sub(swap, str(command)), "pairs": pairs, "unknown": unknown}


def render_evidence_command(command: str, subject: str, values: dict[str, Any]) -> str:
    """AIPOS-F233 件①: 声明命令渲染唯一实现(裁决入口逐字匹配、开工稿 / 派审步骤同用): 先按交付主体把别名改写为规范形, 再以 values
    (规范名 → 值)代值; values 缺某占位或值为空 = 原样保留(开工稿未起 run 时); 词表外的 {xxx} 是字面文本, 原样保留(按字面与实跑比对)。"""
    vocab = placeholder_vocabulary()
    canonical = canonicalize_evidence_command(command, subject, vocab)["command"]

    def fill(match: re.Match[str]) -> str:
        value = values.get(match.group(1))
        return str(value) if value not in (None, "") else match.group(0)

    return vocab["pattern"].sub(fill, canonical)


def _vocabulary_listing(vocab: dict[str, Any]) -> dict[str, str]:
    """字面花括号提示里的可用占位清单(各主体规范名 + 同义别名组)。"""
    branch = ", ".join(dict.fromkeys("{" + str(m["canonical"]["branch"]) + "}" for m in vocab["meanings"]))
    candidate = ", ".join(dict.fromkeys("{" + str(m["canonical"]["candidate"]) + "}" for m in vocab["meanings"]))
    parts = []
    for m in vocab["meanings"]:
        if len(m["names"]) > 1:
            parts.append(" ≡ ".join("{" + n + "}" for n in m["names"]) + f"({m['label']})")
        elif any(str(m["canonical"][s]) != m["names"][0] for s in _VOCAB_SUBJECTS):
            parts.append("{" + m["names"][0] + "}(" + ", ".join(f"{_SUBJECT_LABELS[s]}主体按 {{{m['canonical'][s]}}} 渲染" for s in _VOCAB_SUBJECTS
                                                              if str(m["canonical"][s]) != m["names"][0]) + ")")
    aliases = "; ".join(parts)
    return {"branch": branch, "candidate": candidate, "aliases": aliases or "(无)"}


def evidence_literal_notes(commands: list[Any], *, task_id: str, card_fm: dict[str, Any] | None = None,
                           card_field: str | None = None) -> list[str]:
    """AIPOS-F233 件②: 声明命令中形如 {xxx} 但不在词表的花括号 = 字面文本(awk / jq / json 片段等), 不拒; 每条命令一句提示(texts.literal:
    按字面保留与比对 + 可用占位清单 + difflib 相近的已知占位建议, 如 {workdir} → {worktree})。无 = []。"""
    import difflib

    vocab = placeholder_vocabulary()
    field = card_field or str(runtime_evidence_declaration()["card_field"])
    subject = evidence_subject(card_fm)
    known = sorted(vocab["by_name"])
    out: list[str] = []
    for index, command in enumerate(commands or [], 1):
        literal = canonicalize_evidence_command(str(command), subject, vocab)["unknown"]
        if not literal:
            continue
        pairs = []
        for name in literal:
            close = difflib.get_close_matches(name, known, n=1, cutoff=0.6)
            if close:
                pairs.append("{" + name + "} → {" + str(vocab["by_name"][close[0]]["canonical"][subject]) + "}")
        suggestion = str(vocab["texts"]["literal_suggestion"]).format(pairs=", ".join(pairs)) if pairs else ""
        out.append(str(vocab["texts"]["literal"]).format(task_id=task_id, card_field=field, index=index,
                                                         names=" / ".join("{" + n + "}" for n in literal), suggestion=suggestion,
                                                         **_vocabulary_listing(vocab)))
    return out


def normalize_runtime_evidence_declaration(raw: Any, *, card_fm: dict[str, Any] | None, task_id: str | None = None) -> dict[str, Any]:
    """AIPOS-F233 件②: 发卡规范化唯一实现(draft create / publish / queue amend / 补审派发同调): 声明命令里的别名按声明所在卡(被审卡)
    交付主体改写为规范形。返回 {value(改写后的声明, 无改写 = 原值), notes(每条改写一句, 写明改写前后原文), literals(词表外字面花括号提示,
    不拒; 起草 / 发卡由声明校验 check_runtime_evidence_declaration 同一函数出提示, 此处给 amend 等不经声明校验的调用方)}。
    形不合(非对象 / commands 非列表)= 原样返回(形校验由 parse_runtime_evidence_declaration 唯一判)。"""
    out: dict[str, Any] = {"value": raw, "notes": [], "literals": []}
    if not isinstance(raw, dict) or not isinstance(raw.get("commands"), list):
        return out
    vocab = placeholder_vocabulary()
    tid = str(task_id or (card_fm or {}).get("task_id") or "<卡>")
    field = str(runtime_evidence_declaration()["card_field"])
    subject = evidence_subject(card_fm)
    out["literals"] = evidence_literal_notes(raw["commands"], task_id=tid, card_fm=card_fm, card_field=field)
    commands: list[Any] = []
    for index, command in enumerate(raw["commands"], 1):
        if not isinstance(command, str):
            commands.append(command)
            continue
        canon = canonicalize_evidence_command(command, subject, vocab)
        commands.append(canon["command"])
        if canon["pairs"]:
            out["notes"].append(str(vocab["texts"]["rewrite"]).format(
                subject=subject, task_id=tid, card_field=field, index=index, before=command, after=canon["command"],
                pairs=", ".join("{" + a + "} → {" + b + "}" for a, b in canon["pairs"])))
    if out["notes"]:
        out["value"] = {**raw, "commands": commands}
    return out


def _placeholder_slots(command: str, vocab: dict[str, Any]) -> tuple[str, list[str]]:
    """(骨架 = 占位全换成同一记号, 占位名序列)。"""
    names = [m.group(1) for m in vocab["pattern"].finditer(command)]
    return vocab["pattern"].sub("\x00", command), names


def evidence_commands_render_equivalent(before: Any, after: Any, subject: str) -> bool:
    """AIPOS-F233 件④ 渲染等价判据唯一实现(声明 placeholder_vocabulary.equivalence; queue amend --restricted 与返工自动放行范围判据同读):
    条数相同; 逐条骨架逐字相同; 逐个位置: 改前已知占位 = 改后已知且按本主体规范名相同(渲染为同一值); 改前字面花括号 = 改后逐字相同
    (字面文本改动属改命令, 不算等价)。"""
    if not isinstance(before, list) or not isinstance(after, list) or len(before) != len(after):
        return False
    vocab = placeholder_vocabulary()
    for old, new in zip(before, after):
        if not isinstance(old, str) or not isinstance(new, str):
            return False
        old_skeleton, old_names = _placeholder_slots(old, vocab)
        new_skeleton, new_names = _placeholder_slots(new, vocab)
        if old_skeleton != new_skeleton or len(old_names) != len(new_names):
            return False
        for old_name, new_name in zip(old_names, new_names):
            new_meaning = vocab["by_name"].get(new_name)
            old_meaning = vocab["by_name"].get(old_name)
            if old_meaning is None:  # 字面花括号: 须逐字不变
                if new_name != old_name:
                    return False
                continue
            # 按本主体渲染等价 = 两侧规范名相同(如卡分支主体 {run_dir} 与 {worktree} 都渲染为审计分配工作树)
            if new_meaning is None or old_meaning["canonical"][subject] != new_meaning["canonical"][subject]:
                return False
    return True


def evidence_fingerprint_value(value: Any, card_fm: dict[str, Any] | None) -> Any:
    """AIPOS-F233 件④: 返工自动放行范围指纹里的运行证据声明值(queue_mutation.card_scope_fingerprint 调用): commands 按声明所在卡交付主体
    规范化(别名改名不改指纹); 其余子键原样。非对象 = 原样。"""
    if not isinstance(value, dict) or not isinstance(value.get("commands"), list):
        return value
    vocab = placeholder_vocabulary()
    subject = evidence_subject(card_fm)
    return {**value, "commands": [canonicalize_evidence_command(c, subject, vocab)["command"] if isinstance(c, str) else c
                                  for c in value["commands"]]}


def evidence_declaration_problem(workspace_root: Path | str, audit_task_id: str, audit_fm: dict[str, Any] | None, *,
                                 reviewed_fm: dict[str, Any] | None = None, reviewed_task_id: str | None = None) -> dict[str, Any] | None:
    """AIPOS-F233 件③: 审计卡生效的运行证据声明本身是否有问题(唯一判据; 推导核 claimed 审计卡与裁决入口 runtime_evidence_check 同读):
    形不合(RuntimeEvidenceDeclarationError)/ 含本主体渲染不了的已知占位(该主体在词表无已声明规范名; 加载守卫已保证不发生, 此为
    fail-closed 兜底)= {category(declaration_invalid_category), reasons, card(声明所在卡), queue, value(卡面原声明), amend(修正命令原文),
    outlet(出口一句)}; 未声明 / 无问题 = None。词表外的 {xxx} 是字面文本, 不算声明错(按字面渲染与比对)。"""
    from tools.aipos_cli.audit_derivation import audit_card_reviewed_id
    from tools.aipos_cli.next_resolver import _find_task_in_queue, _read_frontmatter

    decl = runtime_evidence_declaration()
    key = str(decl["card_field"])
    root = Path(workspace_root)
    reviewed = str(reviewed_task_id or "").strip() or audit_card_reviewed_id(audit_task_id, audit_fm or {})
    if reviewed_fm is None:
        path, _q = _find_task_in_queue(root, reviewed) if reviewed else (None, None)
        reviewed_fm = _read_frontmatter(path) if path else {}
    on_audit = isinstance(audit_fm, dict) and audit_fm.get(key) not in (None, "")
    card, card_fm = (audit_task_id, audit_fm or {}) if on_audit else (reviewed, reviewed_fm or {})
    raw = card_fm.get(key)
    if raw in (None, ""):
        return None
    try:
        requirement = parse_runtime_evidence_declaration(raw)
    except RuntimeEvidenceDeclarationError as exc:
        reasons = [f"卡面 {key} 声明不合({exc})"]
    else:
        if requirement is None:
            return None
        reasons = _unrenderable_known_placeholders(requirement["commands"], evidence_subject(reviewed_fm or {}), card)
    if not reasons:
        return None
    vocab = placeholder_vocabulary()
    _path, queue = _find_task_in_queue(root, card) if card else (None, None)
    fixed = {**raw, "commands": [raw["commands"]]} if isinstance(raw, dict) and isinstance(raw.get("commands"), str) else raw
    amend = _evidence_amend_command(root, card, queue, fixed)  # 形修正建议: commands 单串 → 列表
    outlet = str(vocab["texts"]["amend_outlet"]).format(amend=amend, suggestion="")
    return {"category": str(decl["declaration_invalid_category"]), "reasons": reasons, "card": card, "queue": queue, "value": raw,
            "amend": amend, "outlet": outlet,
            "line": str(vocab["texts"]["declaration_invalid"]).format(card_field=key, card=card, problems="; ".join(reasons)) + f"; {outlet}"}


def _unrenderable_known_placeholders(commands: list[Any], subject: str, card: str) -> list[str]:
    """已知占位而本主体无已声明规范名(渲染不了)的命令 → 拒因(加载守卫保证不出现; fail-closed 兜底)。字面花括号不计。"""
    vocab = placeholder_vocabulary()
    declared = {"branch": {str(k) for k in runtime_evidence_declaration()["placeholders"]},
                "candidate": {str(k) for k in runtime_evidence_candidate_declaration()["placeholders"]}}
    out: list[str] = []
    for index, command in enumerate(commands or [], 1):
        bad = [n for n in (m.group(1) for m in vocab["pattern"].finditer(str(command)))
               if n in vocab["by_name"] and str(vocab["by_name"][n]["canonical"].get(subject) or "") not in declared.get(subject, set())]
        if bad:
            out.append(f"卡 {card} 命令 #{index} `{command}` 的占位 {' / '.join('{' + n + '}' for n in bad)} 在交付主体 {subject} 渲染不了")
    return out


def _evidence_amend_command(root: Path, card: str, queue: str | None, value: Any) -> str:
    """运行证据声明修正命令原文(既有动词 queue amend 预演形; 已认领卡 --restricted)。"""
    import json
    import shlex

    from tools.aipos_cli.workspace_config import lybra_command_prefix

    key = str(runtime_evidence_declaration()["card_field"])
    return (f"{lybra_command_prefix(root)} queue amend --task-id {shlex.quote(card or '<卡>')}{' --restricted' if queue == 'claimed' else ''} "
            f"--actor <顾问实例> --amendments {shlex.quote(json.dumps({key: value}, ensure_ascii=False))} "
            f"--amendment-reason {shlex.quote('修正运行证据声明本身(不改验收, AIPOS-F233)')} --dry-run")


def evidence_declaration_fix_line(workspace_root: Path | str, task_id: str) -> str:
    """loop 例行修法占位 evidence_amend 的值(verbs.schema routine_rejections.placeholders.evidence_amend): task_id = 运行卡(业务卡或审计卡);
    业务卡取本卡当前一轮审计卡(审计轮 audit_round_ids 末项, 缺省 <卡>R)。此刻声明已无问题 = 一句说明。"""
    from tools.aipos_cli.audit_derivation import audit_round_ids, is_audit_card
    from tools.aipos_cli.next_resolver import _find_task_in_queue, _read_frontmatter

    root = Path(workspace_root)
    path, _q = _find_task_in_queue(root, task_id)
    fm = _read_frontmatter(path) if path else {}
    if is_audit_card(task_id, fm):
        audit_id, audit_fm = task_id, fm
    else:
        rounds = list(audit_round_ids(task_id, root))
        audit_id = rounds[-1] if rounds else f"{task_id}R"
        audit_path, _aq = _find_task_in_queue(root, audit_id)
        audit_fm = _read_frontmatter(audit_path) if audit_path else {"task_id": audit_id, "reviewed_task_id": task_id}
    problem = evidence_declaration_problem(root, audit_id, audit_fm)
    return problem["line"] if problem else f"卡 {task_id} 的运行证据声明此刻已无问题(已修正), 直接用原命令续跑"


def restricted_amend_subfields() -> dict[str, list[str]]:
    """card.schema restricted_amend.claimed_card_subfields(字段 → 可改子键)唯一读取口。缺 = {}(行为不变: 无字段可按子键受限 amend)。
    形不合 = SchemaLoadError。"""
    from tools.schema_loader import SchemaLoadError, load_schema

    raw = (load_schema("card").get("restricted_amend") or {}).get("claimed_card_subfields")
    if raw is None:
        return {}
    out = {k: v for k, v in raw.items() if k != "description"} if isinstance(raw, dict) else None
    if out is None or any(not isinstance(v, list) or not v or any(not isinstance(s, str) or not s for s in v) for v in out.values()):
        raise SchemaLoadError("card.schema.json restricted_amend.claimed_card_subfields 须为 {字段: [子键, …]}")
    return out


# ---------------------------------------------------------------------------
# AIPOS-F165 件①: 派发前可执行核查——声明 transitions.schema artifact_ingest.verdict.runtime_evidence.executable_check;
# 声明校验唯一处 check_runtime_evidence_declaration(出卡 draft_validator 与补审派发 board_adapter 同调; 裁决入口判据不经此, 逐字匹配不变)
# ---------------------------------------------------------------------------

_SHELL_SEPARATORS = frozenset({"&&", "||", ";", "|", "&", ";;", "|&", "(", ")"})
_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def runtime_evidence_command_words(command: str) -> list[str]:
    """声明命令中每段的可执行词(按 shell 控制符切段; 跳过前导 VAR=值; cd 段不计; env 跳过选项/赋值后取可执行)。
    shell 引号不配对 = RuntimeEvidenceDeclarationError(声明形不合, fail-closed)。"""
    import shlex

    check = runtime_evidence_declaration().get("executable_check") or {}
    with_arg = {str(o) for o in check.get("env_options_with_arg") or []}
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError as exc:
        raise RuntimeEvidenceDeclarationError(f"命令 {command!r} 不可按 shell 词法解析: {exc}") from exc
    segments: list[list[str]] = [[]]
    for token in tokens:
        if token in _SHELL_SEPARATORS:
            segments.append([])
        else:
            segments[-1].append(token)
    words: list[str] = []
    for segment in segments:
        rest = list(segment)
        while rest and _ASSIGNMENT_RE.match(rest[0]):
            rest.pop(0)
        if not rest or rest[0] == "cd":
            continue
        if rest[0] == "env" or rest[0].endswith("/env"):
            words.append(rest[0])
            rest.pop(0)
            while rest and (rest[0].startswith("-") or _ASSIGNMENT_RE.match(rest[0])):
                option = rest.pop(0)
                if option == "--":
                    break
                if option in with_arg and rest:
                    rest.pop(0)
            while rest and _ASSIGNMENT_RE.match(rest[0]):
                rest.pop(0)
            if not rest:
                continue
        words.append(rest[0])
    return words


def check_runtime_evidence_declaration(raw: Any, *, governance_root: Path | str, audit_instance: str | None,
                                       card_task_id: str | None = None, card_frontmatter: dict[str, Any] | None = None) -> dict[str, Any]:
    """运行证据声明校验唯一处(出卡 draft create/publish 与补审派发 audit dispatch --supplementary 同调): 形校验(同裁决入口
    parse_runtime_evidence_declaration)+ 派发前可执行核查。返回 {requirement, problems: [拒因原文], notes: [未核提示]}。
    形不合 = RuntimeEvidenceDeclarationError。problems 非空 = 调用方拒(预演报错, --confirm 拒)。
    AIPOS-F233 件②(取代 F199 旧占位迁移提示): 词表外的 {xxx} 是字面文本(awk / jq / json 片段等), 不拒, notes 给提示与相近已知占位
    (evidence_literal_notes); 别名不拒(调用方先经 normalize_runtime_evidence_declaration 改写为规范形)。
    card_task_id / card_frontmatter = 声明所在卡(拒因点名用)。"""
    import socket

    requirement = parse_runtime_evidence_declaration(raw)
    out: dict[str, Any] = {"requirement": requirement, "problems": [], "notes": []}
    if requirement is None:
        return out
    decl = runtime_evidence_declaration()
    out["notes"].extend(evidence_literal_notes(requirement["commands"], task_id=str(card_task_id or (card_frontmatter or {}).get("task_id") or "<卡>"),
                                               card_fm=card_frontmatter, card_field=str(decl["card_field"])))
    check = decl.get("executable_check")
    needed = ("category", "privilege_category", "privilege_commands", "fix_hint", "unverified_note")
    if not isinstance(check, dict) or any(not check.get(k) for k in needed):
        from tools.schema_loader import SchemaLoadError

        raise SchemaLoadError(f"transitions.schema.json artifact_ingest.verdict.runtime_evidence.executable_check({'/'.join(needed)})未声明")
    placeholders = ["{" + str(k) + "}" for k in (*decl["placeholders"], *runtime_evidence_candidate_declaration()["placeholders"])]
    privileged = {str(c) for c in check["privilege_commands"]}
    location: dict[str, Any] = {}
    basis = ""
    if audit_instance:
        from tools.aipos_cli.enrollment import workstation_location
        from tools.schema_loader import SchemaLoadError

        try:
            location = workstation_location(governance_root, str(audit_instance))
        except (OSError, ValueError, SchemaLoadError) as exc:
            basis = f"审计工位 {audit_instance} 位置读取失败({exc}), 按治理根所在机核"
    remote = bool(location.get("found")) and location.get("transport") == "remote"
    declared_host = str(requirement.get("host") or "")
    if declared_host:  # AIPOS-F223: 卡面声明了执行主机 = 按该主机判(非本机 = 不在本机核, 只给未核提示)
        from tools.aipos_cli.validation_root import is_local_host

        remote = not is_local_host(declared_host)
        location = {**location, "host": declared_host}
        basis = f"卡面 {decl['card_field']}.host={declared_host}"
    if not basis:
        basis = (f"审计工位 {audit_instance} 在本机" if location.get("found") else
                 f"审计工位{(' ' + str(audit_instance) + ' ') if audit_instance else ''}位置定位不到"
                 f"({location.get('reason') or '无审计实例'}), 按治理根所在机核")
    host = socket.gethostname()
    unverified: list[str] = []
    for index, command in enumerate(requirement["commands"], 1):
        for word in runtime_evidence_command_words(command):
            name = word.rsplit("/", 1)[-1]
            if word in privileged or name in privileged:
                out["problems"].append(f"{check['privilege_category']}: 命令 #{index} `{command}` 含提权命令 `{word}`; 运行证据命令须以审计体自身权限"
                                       f"可执行, 不得要求提权")
                continue
            if not word.startswith("/") or any(p in word for p in placeholders):
                continue  # PATH 名 / 相对路径 / 含占位 = 审计体按其 PATH 与工作树解析, 派发时不核
            if remote:
                unverified.append(word)
                continue
            if not (os.path.isfile(word) and os.access(word, os.X_OK)):
                out["problems"].append(
                    f"{check['category']}: 命令 #{index} `{command}` 中的可执行 `{word}` 在审计工位主机(本机 {host}; {basis})不存在或不可执行; "
                    + str(check["fix_hint"]).replace("{name}", name))
    if unverified:
        out["notes"].append(str(check["unverified_note"]).replace("{instance}", str(audit_instance))
                            .replace("{host}", str(location.get("host"))).replace("{paths}", ", ".join(dict.fromkeys(unverified))))
    return out


def runtime_evidence_guidance() -> str:
    """AIPOS-F165 件②: 运行证据拒因 / 要求段的固定指引。文案唯一来源 = roles.schema write_boundary.hard_rules 中
    id = transitions runtime_evidence.rejection_guidance.guidance_rule_id 的条文(章程「写权限边界」节同源下发)。缺 = SchemaLoadError。"""
    from tools.aipos_cli.write_boundary import load_write_boundary_declaration
    from tools.schema_loader import SchemaLoadError

    rule_id = str((runtime_evidence_declaration().get("rejection_guidance") or {}).get("guidance_rule_id") or "")
    rule = next((r for r in load_write_boundary_declaration()["hard_rules"] if isinstance(r, dict) and r.get("id") == rule_id), None)
    if not rule_id or rule is None or not str(rule.get("text") or "").strip():
        raise SchemaLoadError(f"roles.schema.json write_boundary.hard_rules 无 id={rule_id!r} 的条文"
                              "(transitions runtime_evidence.rejection_guidance.guidance_rule_id)")
    return f"固定指引(硬条款 {rule_id}): {str(rule['text']).strip()}"


def runtime_evidence_command_diff(declared: str, ran: list[str]) -> str:
    """AIPOS-F165 件②: 一条缺运行记录的声明命令(已渲染)与最接近的实跑命令逐词对比 → 「声明 vs 实跑」差异原文。
    最接近 = difflib 相似度最高的一条; 无实跑记录 = 只说明无可对比。只作拒因说明, 判据(逐字匹配)不变。"""
    import difflib

    if not ran:
        return "逐条差异: 报告无任何实跑命令可对比"
    closest = max(ran, key=lambda c: difflib.SequenceMatcher(None, declared, c).ratio())
    want, got = declared.split(), closest.split()
    parts: list[str] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, want, got).get_opcodes():
        if tag != "equal":
            parts.append(f"声明 `{' '.join(want[i1:i2]) or '(无)'}` ≠ 实跑 `{' '.join(got[j1:j2]) or '(无)'}`")
    return f"逐条差异: 声明 `{declared}` vs 最接近的实跑 `{closest}`: " + ("; ".join(parts) or "仅空白不同")


def runtime_evidence_candidate_declaration() -> dict[str, Any]:
    """AIPOS-F199 件①: transitions runtime_evidence.candidate_subject(固定候选卡审计证据绑定审计自己的验证根 run)唯一读取口。缺 = SchemaLoadError。"""
    from tools.schema_loader import SchemaLoadError

    decl = runtime_evidence_declaration().get("candidate_subject")
    needed = ("entry_required_fields", "placeholders", "outlet", "steps_title", "steps", "command_line", "pending", "remote_steps")
    if not isinstance(decl, dict) or any(not decl.get(k) for k in needed):
        raise SchemaLoadError(f"transitions.schema.json artifact_ingest.verdict.runtime_evidence.candidate_subject({'/'.join(needed)})未声明")
    return decl


def render_candidate_evidence_command(command: str, values: dict[str, Any]) -> str:
    """AIPOS-F199: 固定候选卡声明命令的占位渲染(裁决入口逐字匹配与开工稿 / 派审步骤同用)。AIPOS-F233: 薄封装唯一渲染
    render_evidence_command(主体 candidate: 别名先改写为规范形再代值); values 缺某占位 = 原样保留(开工稿未起 run 时)。"""
    return render_evidence_command(command, "candidate", values)


def render_candidate_evidence_steps(governance_root: Path | str, *, audit_task_id: str, audit_instance: str | None, reviewed_task_id: str,
                                    reviewed_fm: dict[str, Any] | None, requirement: dict[str, Any] | None) -> list[str]:
    """AIPOS-F199 件②: 审计「验证卡运行证据步骤」(派审注入 / 开工稿同一渲染; 步骤措辞读 candidate_subject.steps, 渲染值与命令读
    validation_root.candidate_evidence_values 唯一铸造口)。被审卡非固定候选 = []。候选不可用 = CandidateSubjectError 上抛。"""
    from tools.aipos_cli.next_resolver import candidate_subject_values
    from tools.aipos_cli.validation_root import candidate_evidence_values

    subject = candidate_subject_values(Path(governance_root), reviewed_task_id, reviewed_fm or {})
    if subject is None:
        return []
    decl = runtime_evidence_candidate_declaration()
    # AIPOS-F223 件①: run 选择按卡面执行主机(requirement.host; 唯一实现 validation_root.select_evidence_declaration, 裁决入口同读)
    evidence_card = audit_task_id if (requirement or {}).get("source") == "audit_card" else reviewed_task_id
    values = candidate_evidence_values(governance_root, audit_task_id=audit_task_id, audit_instance=str(audit_instance or ""),
                                       candidate=subject["commit_sha"], candidate_tree=subject["tree_hash"], requirement=requirement,
                                       evidence_card=evidence_card)
    if values.get("host_refusal"):  # 选不出 run = 拒因一行(不渲染任何 run 命令; 裁决入口同判据拒)
        return [str(decl["steps_title"]), str(values["host_refusal"]),
                *(f"   ℹ {p}" for p in evidence_literal_notes((requirement or {}).get("commands") or [], task_id=reviewed_task_id,
                                                            card_fm=reviewed_fm))]
    values["heading"] = str(runtime_evidence_declaration()["section_heading"])
    known = {k: v for k, v in values.items() if not str(v).startswith("<")}
    lines = [str(decl["steps_title"])]
    if values.get("host_note"):
        lines.append(str(values["host_note"]))
    after = decl.get("commands_after_step")
    for index, step in enumerate(decl["steps"], 1):
        lines.append(str(step).format(**values))
        if index == after:  # 声明命令逐条(占位按已知值渲染), 位置读声明 commands_after_step
            lines.extend(str(decl["command_line"]).format(command=render_candidate_evidence_command(c, known))
                         for c in (requirement or {}).get("commands") or [])
    if values.get("remote"):  # AIPOS-F200 件④: 审计 run 在远端主机 = 证据清单 + 取回副本两步(声明 candidate_subject.remote_steps)
        lines.extend(str(step).format(**values) for step in decl.get("remote_steps") or [])
    lines.extend(f"   ℹ {p}" for p in evidence_literal_notes((requirement or {}).get("commands") or [], task_id=reviewed_task_id,
                                                        card_fm=reviewed_fm))
    return lines


def render_runtime_evidence_requirement(requirement: dict[str, Any], *, candidate_steps: list[str] | None = None) -> str:
    """审计卡正文「独立运行证据要求」段(唯一渲染; 派生审计卡与手动/补充派审同读): 命令清单 + 证据节格式 + 示例。
    AIPOS-F199 件②: candidate_steps(被审卡交付主体为固定候选, render_candidate_evidence_steps)= 证据绑定审计自己的验证根 run, 不提审计分配工作树。"""
    decl = runtime_evidence_declaration()
    keys = decl["log_fields"]
    heading = str(decl["section_heading"])
    if candidate_steps:
        cand = runtime_evidence_candidate_declaration()
        lines = ["", "## 独立运行证据要求(AIPOS-F162: 裁决入口硬门; AIPOS-F199: 固定候选卡绑定审计自己的验证根 run)", "",
                 f"- 本卡声明 {decl['card_field']}.required=true: PASS / PASS_WITH_NOTES 报告必须含 `## {heading}` 节, 否则裁决入口拒收"
                 f"({decl['rejection_category']}), loop 让你补做",
                 "- 独立核验 = 在**你自己的验证根 run**(审计卡声明的 run, 不是执行实例的 run)重跑下列命令; 只核执行体原件 hash/退出码不算; "
                 "跑不了(环境/时长)给 FAIL 或 BLOCK 并写原因",
                 f"- 命令占位: {', '.join('{' + str(k) + '}' for k in cand['placeholders'])}(本 run 目录 / 本 run 送达的候选源码 / 固定候选 / 候选 tree), "
                 "记录里写渲染后的实际命令, 逐字一致",
                 f"- {runtime_evidence_guidance()}", "", *candidate_steps, "",
                 f"证据节格式(节内第一个 ```{decl['block_language']} 块; 每条必填 {', '.join(cand['entry_required_fields'])}):", "",
                 f"```{decl['block_language']}",
                 '[{"command": "<实际命令>", "run_id": "<run-id>", "run_dir": "<本 run 目录绝对路径>", "exit_code": 0, '
                 f'"{keys["path"]}": "<日志绝对路径>", "{keys["sha256"]}": "<sha256>"' + "}]",
                 "```", ""]
        return "\n".join(lines)
    lines = ["", "## 独立运行证据要求(AIPOS-F162: 裁决入口硬门)", "",
             f"- 本卡声明 {decl['card_field']}.required=true: PASS / PASS_WITH_NOTES 报告必须含 `## {heading}` 节, 否则裁决入口拒收"
             f"({decl['rejection_category']}), loop 让你补做",
             "- 独立核验 = 在**你自己的审计分配工作树**重跑下列命令; 只核执行体原件 hash/退出码不算; 跑不了(环境/时长)给 FAIL 或 BLOCK 并写原因",
             f"- 命令占位: {', '.join('{' + str(k) + '}' for k in decl['placeholders'])}(审计分配工作树 / 被审 tip / tree), 记录里写渲染后的实际命令, 逐字一致",
             f"- {runtime_evidence_guidance()}"]  # AIPOS-F165 件②: 与拒因同一固定指引
    if requirement.get("commands"):
        lines.append("- 必须逐条运行并记录:")
        # AIPOS-F233: 存量别名声明按卡分支主体规范化后展示(审计体照规范占位渲染, 与裁决入口同一渲染)
        lines.extend(f"  - `{canonicalize_evidence_command(c, 'branch')['command']}`" for c in requirement["commands"])
    else:
        lines.append("- 卡面未列具体命令: 记录你实际运行的验证命令(至少一条)")
    lines += ["", f"证据节格式(节内第一个 ```{decl['block_language']} 块; 日志二选一: {keys['excerpt']} 或 {keys['path']}+{keys['sha256']}):", "",
              f"```{decl['block_language']}",
              '[{"command": "<实际命令>", "worktree": "<审计分配工作树绝对路径>", "exit_code": 0, '
              f'"{keys["path"]}": "<日志绝对路径>", "{keys["sha256"]}": "<sha256>"' + "}]",
              "```", ""]
    return "\n".join(lines)


def parse_runtime_evidence_section(report_text: str) -> tuple[list[Any] | None, str]:
    """报告「独立运行证据」节内首个声明语言代码块 → (运行记录列表, 问题)。节缺 / 块缺 / JSON 坏 / 非列表 = (None, 问题原文)。"""
    import json as _json

    decl = runtime_evidence_declaration()
    heading = str(decl["section_heading"])
    fence = "```" + str(decl["block_language"])
    lines = report_text.splitlines()
    start = None
    level = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#") and stripped.lstrip("#").strip() == heading:
            start, level = i + 1, len(stripped) - len(stripped.lstrip("#"))
            break
    if start is None:
        return None, f"报告缺「{heading}」节(标题行 `{'#' * 2} {heading}`)"
    body: list[str] = []
    for line in lines[start:]:
        stripped = line.strip()
        if stripped.startswith("#") and len(stripped) - len(stripped.lstrip("#")) <= level and stripped.lstrip("#").startswith(" "):
            break
        body.append(line)
    block: list[str] | None = None
    for line in body:
        stripped = line.strip()
        if block is None:
            if stripped == fence:
                block = []
            continue
        if stripped == "```":
            break
        block.append(line)
    else:
        if block is not None:
            return None, f"「{heading}」节的 {fence} 代码块未闭合"
    if block is None:
        return None, f"「{heading}」节内无 {fence} 代码块(运行记录须机器可解析)"
    try:
        entries = _json.loads("\n".join(block))
    except ValueError as exc:
        return None, f"「{heading}」节 {fence} 代码块不是合法 JSON: {exc}"
    if not isinstance(entries, list):
        return None, f"「{heading}」节 {fence} 代码块须为运行记录数组, 实为 {type(entries).__name__}"
    return entries, ""


_TIP_DIAGNOSIS_KINDS = ("length_invalid", "non_hex", "prefix_miscopy", "not_a_commit", "other_commit")


def tip_mismatch_declaration() -> dict[str, Any]:
    """AIPOS-F180B item 1: transitions.schema artifact_ingest.tip_mismatch_diagnosis (single reader). Missing / malformed = SchemaLoadError."""
    from tools.aipos_cli.next_resolver import _artifact_ingest_declaration
    from tools.schema_loader import SchemaLoadError

    decl = _artifact_ingest_declaration().get("tip_mismatch_diagnosis")
    where = "transitions.schema.json artifact_ingest.tip_mismatch_diagnosis"
    if not isinstance(decl, dict) or not isinstance(decl.get("kinds"), dict) or not isinstance(decl.get("outlets"), dict):
        raise SchemaLoadError(f"{where} (kinds / outlets) not declared")
    for key in ("prefix_min_chars", "min_resolve_chars"):
        if not isinstance(decl.get(key), int) or isinstance(decl.get(key), bool) or decl[key] < 1:
            raise SchemaLoadError(f"{where}.{key} must be a positive integer")
    for kind in _TIP_DIAGNOSIS_KINDS:
        spec = decl["kinds"].get(kind)
        if not isinstance(spec, dict) or not isinstance(spec.get("correctable"), bool) or not str(spec.get("text") or "").strip():
            raise SchemaLoadError(f"{where}.kinds.{kind} (correctable bool / text) not declared")
    if any(not str(decl["outlets"].get(k) or "").strip() for k in ("correctable", "not_correctable")):
        raise SchemaLoadError(f"{where}.outlets (correctable / not_correctable) not declared")
    return decl


def tip_mismatch_diagnosis(repo: Path, tip: str, commit_sha: str, *, branch: str = "") -> dict[str, Any]:
    """AIPOS-F180B item 1: form diagnosis of a self-reported commit_sha that differs from the branch tip (single implementation;
    judged only where INGEST_TIP_MISMATCH is decided). Order per declaration: resolves to another commit = other_commit (the only
    non-correctable kind); otherwise accumulate length_invalid / non_hex / prefix_miscopy, none of them = not_a_commit.
    Returns {branch, tip, commit_sha, kinds, correctable, resolved, text, outlet}."""
    decl = tip_mismatch_declaration()
    kinds_decl = decl["kinds"]
    sha = str(commit_sha or "")
    resolved = None
    if len(sha) >= int(decl["min_resolve_chars"]) and re.fullmatch(r"[0-9a-fA-F]+", sha):
        resolved = _git_out(repo, "rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}")
    hits: list[tuple[str, dict[str, Any]]] = []
    if resolved and resolved != tip:
        hits.append(("other_commit", {"resolved": resolved}))
    else:
        if len(sha) != 40:
            hits.append(("length_invalid", {"length": len(sha)}))
        bad = "".join(sorted(set(re.sub(r"[0-9a-f]", "", sha))))
        if bad:
            hits.append(("non_hex", {"chars": bad}))
        common = len(os.path.commonprefix([sha, str(tip)]))
        if common >= int(decl["prefix_min_chars"]):
            hits.append(("prefix_miscopy", {"common": common, "diverge": common + 1}))
        if not hits:
            hits.append(("not_a_commit", {}))
    texts = []
    for kind, values in hits:
        text = str(kinds_decl[kind]["text"])
        for key, value in values.items():
            text = text.replace("{" + key + "}", str(value))
        texts.append(text)
    correctable = all(bool(kinds_decl[kind]["correctable"]) for kind, _v in hits)
    return {"branch": branch, "tip": tip, "commit_sha": sha, "kinds": [k for k, _v in hits], "correctable": correctable, "resolved": resolved,
            "text": "; ".join(texts), "outlet": str(decl["outlets"]["correctable" if correctable else "not_correctable"])}


def _stale_round_outlet(workspace_root: Path, audit_task_id: str, card_fm: dict[str, Any], reviewed: str) -> str:
    """AIPOS-F114 件②: 裁决入口遇旧 tip 的拒因出口(推导核同一判据: audit_derivation.superseding_round / next_resolver.return_staleness)。
    本轮已被取代 → 点名取代它的一轮; 所审交回已过期 → 驱动方重交回命令(产品随后派下一轮, 本轮作废); 其余 → 审计体按当前 tip 重审。"""
    from tools.aipos_cli.audit_derivation import superseding_round
    from tools.aipos_cli.next_resolver import (
        _find_connection_json, _find_task_in_queue, _read_frontmatter, ingest_command, return_staleness,
    )

    successor = superseding_round(audit_task_id, workspace_root, card_fm)
    if successor is not None:
        return (f"。出口: 本轮 {audit_task_id} 已被 {successor['audit_task_id']} 取代(派审记录 {successor['dispatch_id']} supersedes), "
                f"本报告不入门; 审计在 {successor['audit_task_id']} 对当前 tip 进行")
    reviewed_path, _q = _find_task_in_queue(workspace_root, reviewed)
    stale = return_staleness(workspace_root, reviewed, _read_frontmatter(reviewed_path)) if reviewed_path else None
    if stale is not None and str(stale.get("audit_task_id") or "") == audit_task_id:
        command = ingest_command(reviewed, "return", workspace_root, _find_connection_json(workspace_root))
        return (f"。交回已过期({stale['reason']})。出口: 驱动方重交回被审卡 `{command}`"
                f"(执行体 Return 须已是卡分支当前 tip), 产品随后按卡号演进派下一轮审计, 本轮 {audit_task_id} 作废")
    return "。出口: 审计体对被审分支当前 tip 重审并更新报告 commit_sha"


# ---------------------------------------------------------------------------
# AIPOS-F90 件②: 报告模型字段由产品从会话记录填写(Owner 2026-10-02 裁定; 审计体自报连续五次不实)
# 定位器唯一声明 card.schema intent_face.harness.session_record_locators; 本节是唯一读取口
# ---------------------------------------------------------------------------

def _session_locators() -> dict[str, Any]:
    from tools.aipos_cli.machine_zone import intent_face_declaration
    from tools.schema_loader import SchemaLoadError

    decl = (intent_face_declaration().get("harness") or {}).get("session_record_locators")
    if not isinstance(decl, dict) or not str(decl.get("missing_value") or "").strip():
        raise SchemaLoadError("card.schema.json intent_face.harness.session_record_locators(含 missing_value)未声明")
    return decl


def _dig(obj: Any, path: list[Any]) -> Any:
    for key in path or []:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def _epoch(value: Any) -> float | None:
    from datetime import datetime, timezone

    text = str(value or "").strip()
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)).timestamp()


def _scan_session_records(locator: dict[str, Any], artifact: Path, since: float | None) -> tuple[list[str], list[str]]:
    """按定位器扫会话记录: 返回(写入该报告的条目所在模型列表, 会话记录文件名列表)。只读, 不改任何文件。"""
    import glob as _glob
    import json as _json

    target = artifact.resolve()
    needles = {str(target), str(artifact), _json.dumps(str(target))[1:-1]}
    write_names = {str(n) for n in locator.get("write_tool_names") or []}
    path_keys = [str(k) for k in locator.get("write_path_keys") or []]
    models: list[str] = []
    sources: list[str] = []
    for pattern in locator.get("session_globs") or []:
        for name in sorted(_glob.glob(os.path.expanduser(str(pattern)))):
            session = Path(name)
            try:
                if since is not None and session.stat().st_mtime < since:
                    continue
                text = session.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                import sys

                print(f"Warning: 会话记录不可读, 跳过 {session}: {exc}", file=sys.stderr)
                continue
            if not any(n in text for n in needles):
                continue
            session_cwd = ""
            for line in text.splitlines():
                if not any(n in line for n in needles) and session_cwd:
                    continue
                try:
                    entry = _json.loads(line)
                except ValueError:
                    continue
                if not isinstance(entry, dict):
                    continue
                if not session_cwd and entry.get("type") == "session":
                    session_cwd = str(entry.get("cwd") or "")
                content = _dig(entry, list(locator.get("content_path") or []))
                if not isinstance(content, list):
                    continue
                hit = False
                for item in content:
                    if not isinstance(item, dict) or item.get("type") != locator.get("tool_call_type"):
                        continue
                    if str(item.get(str(locator.get("tool_name_key") or "name")) or "") not in write_names:
                        continue
                    tool_args = item.get(str(locator.get("tool_args_key") or "arguments"))
                    tool_args = tool_args if isinstance(tool_args, dict) else {}
                    for key in path_keys:
                        raw = str(tool_args.get(key) or "").strip()
                        if not raw:
                            continue
                        candidate = Path(raw).expanduser()
                        if not candidate.is_absolute():
                            base = str(entry.get("cwd") or session_cwd or "")
                            if not base:
                                continue
                            candidate = Path(base) / candidate
                        if candidate.resolve() == target:
                            hit = True
                model = str(_dig(entry, list(locator.get("model_path") or [])) or "").strip()
                if hit and model:
                    models.append(model)
                    sources.append(session.name)
    return models, sources


def _same_model(self_reported: str, located: str) -> bool:
    def norm(value: str) -> str:
        return value.strip().lower()

    reported = norm(self_reported)
    for model in located.split("+"):
        m = norm(model)
        if reported == m or reported.endswith("/" + m) or m.endswith("/" + reported):
            return True
    return False


def runtime_model_bundle(workspace_root: Path, *, writer_task_id: str, writer_fm: dict[str, Any], artifact_path: Path,
                         self_reported: str) -> dict[str, Any]:
    """AIPOS-F90 件②: 产品填写的模型字段(agent_runtime 形)。harness = 写报告那张卡的 harness(AIPOS-F211 件③: 唯一推导
    loop_driver.card_harness——卡面值, 缺 = 本卡执行方式推出, 与 loop 拉起同读; 原卡面缺按 task_mode 补 "code" 查表退役);
    执行者实例无子 agent 接入登记(CardHarnessUnresolved)= harness 不可知 → 模型 missing_value;
    定位器缺/会话记录里找不到写该报告的条目 = missing_value(「未声明会话记录」), 绝不采信自报; 自报只作对照, 不一致标 model_mismatch。"""
    from tools.aipos_cli.loop_driver import CardHarnessUnresolved, card_harness
    from tools.aipos_cli.next_resolver import _read_task_records

    decl = _session_locators()
    missing = str(decl["missing_value"]).strip()
    try:
        harness = card_harness(writer_fm, Path(workspace_root), task_id=writer_task_id)
    except CardHarnessUnresolved:
        harness = ""
    claimed_at = _epoch((_read_task_records(Path(workspace_root), writer_task_id).get("latest_claim") or {}).get("claimed_at"))
    locator = decl.get(harness)
    models: list[str] = []
    sources: list[str] = []
    if isinstance(locator, dict):
        models, sources = _scan_session_records(locator, Path(artifact_path), claimed_at - 60 if claimed_at else None)
    located = "+".join(sorted(set(models)))
    reported = str(self_reported or "").strip()
    bundle: dict[str, Any] = {
        "harness": harness,
        "model": located or missing,
        "model_source": f"{harness}:{','.join(sorted(set(sources)))}" if located else missing,
        "model_mismatch": bool(located and reported and not _same_model(reported, located)),
    }
    if reported:
        bundle["model_self_reported"] = reported
    return bundle


def ingest_task_artifact(
    task_id: str,
    workspace_root: Path,
    *,
    connection_json: str | None = None,
    dry_run: bool = False,
    execute: Any = None,
    kind: str | None = None,
) -> dict[str, Any]:
    """产物入口主函数: 校验 → 推导核派生同一条薄壳命令 → 产品填模型字段 → execute_derived_action(既有执行体)提交。

    AIPOS-F90 件②: 推导核对 return/verdict 步派生的命令就是本入口(`lybra artifact ingest --kind`); 本入口执行的是推导核同时
    给出的 shell_command(唯一构建), 补上 runtime_model_bundle(运行时模型取自会话记录)后经 execute_derived_action 提交。
    裁决入门时审计报告全文由门快照进 records(transitions artifact_ingest.verdict.report_snapshot)。
    返回 {ok, exit_code, category, reasons, command, path, output, message, agent_runtime}。execute 可注入(靶场)。
    """
    from tools.aipos_cli.next_resolver import (
        _find_task_in_queue,
        _read_frontmatter,
        derive_next_step,
        execute_derived_action,
        with_runtime_model,
    )

    check = validate_task_artifact(task_id, workspace_root)
    result: dict[str, Any] = {
        "task_id": task_id, "kind": check["kind"], "ok": False, "exit_code": check["exit_code"],
        "category": check["category"], "reasons": list(check["reasons"]), "command": "", "path": check["path"],
        "output": "", "message": "", "agent_runtime": None,
    }
    if check.get("notes"):  # AIPOS-F233 件⑥: 报告头容错 note(报告原文不改)
        result["notes"] = list(check["notes"])
    if kind and kind != check["kind"]:
        result.update({"exit_code": INGEST_EXIT_REJECTED, "category": "INGEST_KIND_MISMATCH",
                       "reasons": [f"推导核声明入门节点 --kind {kind}, 但产物入口按卡与记录判定为 {check['kind']}: 卡态已变, 重推导后再入门"],
                       "message": "artifact ingest 拒: 入门节点不符"})
        return result
    if not check["ok"]:
        result["message"] = f"artifact ingest 拒: {check['category']}"
        if not dry_run and not (kind and kind != check["kind"]):
            _record_ingest_event(Path(workspace_root), task_id, result, check, rejected=True)  # AIPOS-F236 件②: 拒收入真相
        return result
    if check.get("input_copies"):
        result["input_copies"] = list(check["input_copies"])  # AIPOS-F236 件①: 输入副本注记(--json 可见; 文本输出走 notes)

    derivation = derive_next_step(task_id, Path(workspace_root))
    action_type = derivation.get("verb") or ""
    expected_verb = {"return": "lybra_queue_return_dry_run", "verdict": "lybra_audit_verdict_dry_run", "finalization": "lybra_finalize"}[check["kind"]]
    if not derivation.get("derivable") or action_type != expected_verb:
        result["category"] = "INGEST_NOT_AT_RETURN_NODE"
        result["reasons"] = [
            f"推导核当前节点 {derivation.get('current_node')}/{derivation.get('current_state')} 的动作不是 {expected_verb}: "
            f"missing={derivation.get('missing_records')}; suggested={derivation.get('suggested_action')}"
        ]
        result["message"] = "artifact ingest 拒: 卡不在可交回/可裁决节点"
        result["exit_code"] = INGEST_EXIT_REJECTED  # AIPOS-F101 件③: 原沿用校验通过的 0 = 拒却 exit 0(与声明不符)
        return result
    shell_command = str(derivation.get("shell_command") or derivation.get("command") or "")
    if check["kind"] in ("return", "verdict"):
        writer_path, _q = _find_task_in_queue(Path(workspace_root), task_id)
        bundle = runtime_model_bundle(
            Path(workspace_root), writer_task_id=task_id, writer_fm=_read_frontmatter(writer_path) if writer_path else {},
            artifact_path=Path(str(check["path"])), self_reported=str((check.get("frontmatter") or {}).get("model") or ""),
        )
        result["agent_runtime"] = bundle
        shell_command = with_runtime_model(shell_command, kind=check["kind"], agent_runtime=bundle)
    result["command"] = shell_command
    if dry_run:
        result.update({"ok": True, "exit_code": INGEST_EXIT_RECORDED, "category": "DRY_RUN", "message": "校验通过(未提交)"})
        return result
    if check["kind"] == "finalization":
        # AIPOS-F78B 件②: 同一 writer(finalization_record.write_finalization_record)铸记录; actor=被审卡 claim 记录实例,
        # authorization=裁决 verdict_id, commit/merge_commit/remote_ref/deploy_status 来自 FINALIZE Return frontmatter
        from tools.aipos_cli.finalization_record import write_finalization_record
        from tools.aipos_cli.next_resolver import _claimer_instance

        records = check.get("records") or {}
        fm = check["frontmatter"]
        claimer = _claimer_instance(records)
        verdict_id = str((records.get("latest_verdict") or {}).get("verdict_id") or "").strip()
        if not claimer or not verdict_id:
            result["category"] = "INGEST_RECORD_MISSING"
            result["reasons"] = [f"finalization 记录依据缺: claim 记录 agent_instance={claimer!r}, 裁决 verdict_id={verdict_id!r}"]
            result["message"] = "artifact ingest 拒: 记录依据不齐"
            result["exit_code"] = INGEST_EXIT_REJECTED
            return result
        try:
            rel = str(Path(check["path"]).resolve().relative_to(Path(workspace_root).resolve()))
        except ValueError:
            rel = str(check["path"])
        deploy_status = str(fm.get("deploy_status") or "").strip()
        merge_commit = str(fm.get("merge_commit") or "").strip()
        written = write_finalization_record(
            governance_root=Path(workspace_root), task_id=task_id, actor=claimer, commit=merge_commit, merge_commit=merge_commit,
            authorization_type="verdict_ref", authorization_ref=verdict_id, deployed=(deploy_status == "deployed"),
            deploy_status=deploy_status, remote_ref=str(fm.get("remote_ref") or "").strip(), finalize_return_ref=rel,
        )
        result["ok"] = bool(written.get("ok"))
        result["exit_code"] = INGEST_EXIT_RECORDED if result["ok"] else INGEST_EXIT_SHELL_REJECTED
        result["output"] = str(written.get("path") or "")
        result["message"] = "finalization 记录已落(external finalize, 来自 FINALIZE Return)" if result["ok"] else "finalization 记录写入失败"
        result["category"] = "OK" if result["ok"] else "RECORD_WRITE_FAILED"
        return result
    runner = execute or execute_derived_action
    exec_result = runner({**derivation, "command": shell_command}, Path(workspace_root), connection_json)
    result["ok"] = bool(exec_result.get("ok"))
    result["exit_code"] = INGEST_EXIT_RECORDED if result["ok"] else int(exec_result.get("exit_code") or INGEST_EXIT_SHELL_REJECTED)
    result["output"] = str(exec_result.get("output") or "")
    result["message"] = str(exec_result.get("message") or "")
    result["category"] = "OK" if result["ok"] else "SHELL_REJECTED"
    if not result["ok"] and result["output"]:
        result["reasons"] = [result["output"]]
    if check["kind"] == "return":  # AIPOS-F236: 门侧拒同落拒收记录(category = shell_rejected_category); 通过且有输入副本 = 输入副本记录
        _record_ingest_event(Path(workspace_root), task_id, result, check, rejected=not result["ok"])
    return result


def run_ingest_cli(args: Any) -> int:
    """CLI 入口(`lybra artifact ingest`): 治理根自发现或 --workspace-root; 输出人读或 --json; token 永不上屏。"""
    import json as _json
    import sys

    from tools.aipos_cli.aipos_cli import _find_repo_root_for_args

    try:
        governance_root = Path(_find_repo_root_for_args(args))  # AIPOS-F144: 唯一薄壳(全局/子命令级 --workspace-root 同义)
    except FileNotFoundError as exc:
        print(f"lybra artifact ingest: cannot resolve governance root: {exc}", file=sys.stderr)
        return INGEST_EXIT_REJECTED
    result = ingest_task_artifact(
        args.task_id, governance_root,
        connection_json=getattr(args, "connection_json", None), dry_run=bool(getattr(args, "dry_run", False)),
        kind=getattr(args, "kind", None),
    )
    if getattr(args, "json", False):
        print(_json.dumps(result, indent=2, ensure_ascii=False))
    else:
        status = "ok" if result["ok"] else f"rejected ({result['category']})"
        print(f"artifact ingest {args.task_id} [{result['kind']}]: {status}")
        if result.get("path"):
            print(f"  artifact: {result['path']}")
        if result.get("command"):
            print(f"  command: {result['command']}")
        for reason in result.get("reasons") or []:
            print(f"  - {reason}", file=sys.stderr if not result["ok"] else sys.stdout)
        for note in result.get("notes") or []:  # AIPOS-F233 件⑥
            print(f"  note: {note}")
        if result.get("output") and result["ok"]:
            print(result["output"].rstrip())
    return int(result["exit_code"])


# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation
check_direct_invocation(__name__)
