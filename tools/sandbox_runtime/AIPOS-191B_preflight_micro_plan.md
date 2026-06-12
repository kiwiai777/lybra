# AIPOS-191B Pre-Flight Micro-Plan (two 196b leftovers)

Scope: clear two AIPOS-196b leftovers before the AIPOS-191B real heterogeneous
autonomous rerun. Verify-first per slice; change only if a real problem is
confirmed. Do not touch 191B itself, SC② rendering/SOP, finalize-writer, or
planner-autonomy. Raw tokens never enter log/record/Board/git — fingerprints only.

Target module: `tools/sandbox_runtime/confined_worker.py` (AIPOS-196b). Do not
refactor unrelated parts. Do not relax the truth/.lybra/product read-only
boundary, add a mount source into truth, or change `_FORBIDDEN_NETWORKS` / the
non-public gate constraint.

---

## Slice A — projection raw-token fallback scan

### Verify (read-only) — DONE
- `build_projection` (confined_worker.py:239-258) renders the projection, then
  scans it with `assert_no_secrets(dest, scan)` where `scan` =
  `all_raw_secrets(connection.json)` (all three role tokens) + the Anthropic key.
- `assert_no_secrets` (:220-236) raises `ConfinedWorkerError` (fail-closed) on a
  hit and reports only the relative path, never the raw value.
- It runs in `build_request_from_args` before `run_confined_worker`; `main()`
  catches `ConfinedWorkerError` and returns 2 before any docker run.

### Conclusion
The content-scan belt-and-suspenders ALREADY EXISTS and is fail-closed. One
precise gap vs the stated requirement ("含文件名/内容"): `assert_no_secrets` scans
file *contents* only, not file *names/paths*. Current projection filenames are
fixed, so it is practically unreachable, but `build_projection` is generic and
the requirement explicitly asks for filename coverage.

### Minimal change (only this)
Extend `assert_no_secrets` to also scan each entry's relative path string (file
and directory names) against the same needles, fail-closed, fingerprint-safe (the
error already prints no raw value). Do NOT touch the structural exclusion logic
validated in 195/196b, nor the content scan. Pure additive belt-and-suspenders.

---

## Slice B — WSL2 scratch permission

### Reproduce (real WSL2)
The tool creates `scratch_run_dir = approved_root/<run_id>` via
`mkdir(parents=True, exist_ok=True)` (default mode, umask), and runs the container
with `--read-only` rootfs + a writable bind mount of that dir at `/scratch`,
**without** setting `--user`. Whether the container can write `/scratch` depends on
the image's default uid vs the host dir owner/mode:

- container default user = root -> writes succeed;
- container non-root uid == host owner (kiwi uid 1000) -> writes succeed;
- container non-root uid != owner, dir not world-writable -> writes BLOCKED;
- if `approved_root` itself is 0700 owned by kiwi, a mismatched non-root uid
  cannot even traverse into it -> BLOCKED.

Reproduction uses a local image (`--pull never`, e.g. `lybra/probe:adversarial`)
with the same mount semantics and a simple `echo > /scratch/probe`, across
`--user` root / matching / mismatched, plus a 0700 approved-root case. This
isolates the scratch-write permission dimension without needing the Claude image,
the gate, or the LLM.

### Decision rule
- If writes are NOT blocked under the realistic worker posture -> keep deferred;
  write the conclusion here; no code change.
- If blocked -> minimal fix touching ONLY scratch permission/mount semantics:
  set the per-run scratch dir to a mode the container uid can write (e.g. 0770 +
  shared gid, or 0777 as the 196bR note suggested) at creation time, inside the
  approved root only. Must NOT relax truth/.lybra/product read-only, must NOT add
  a truth mount source, must NOT change `_FORBIDDEN_NETWORKS` or the non-public
  gate constraint. The approved-root containment and 196a ingestion path are
  unchanged.

---

## Shared boundary

- Touch only necessary code; no 196b refactor.
- Reports use fingerprints; raw token never in log/record/Board/git.
- Each slice: full suite (CLI/Board/MCP) + cc glm independent audit; Owner
  approval before commit/finalize. The two slices are separate from 191B itself.

## Findings (execution result)

### Slice A — projection raw-token fallback scan
- Verify: the content-scan fallback ALREADY EXISTS (`build_projection` scans the
  rendered projection against all three raw role tokens + the LLM key via
  `assert_no_secrets`, fail-closed before any docker run). Confirmed present.
- Gap: `assert_no_secrets` scanned file *contents* only, not path/file *names*,
  while the requirement says "含文件名/内容".
- Minimal fix applied: `assert_no_secrets` now also scans each projection path
  name against the same needles, fail-closed; both name- and content-leak errors
  report only a `secret_fingerprint`, never the raw value. Structural exclusion
  and the existing content scan are unchanged.

### Slice B — WSL2 scratch permission (BLOCKS — fix applied)
Reproduced on this WSL2 host (docker, image `lybra/probe:adversarial`, tool mount
semantics `--read-only` + writable `/scratch` bind, no `--user`):

| case | result |
| --- | --- |
| approved-root 0700, scratch run dir 0755, mismatched non-root uid | **Permission denied** |
| scratch run dir 0777, mismatched non-root uid | WROTE_OK |
| approved-root 0700 + scratch run dir 0777, mismatched non-root uid | WROTE_OK |
| container uid == host owner (1000), 0755 | WROTE_OK |

Root cause: docker bind-mounts the run dir directly, so only the run dir's own
mode gates container access; with no `--user`, a worker image whose default uid
differs from the host owner cannot write a 0755/host-owned scratch dir. This is
the WSL2 blocker the AIPOS-196bR audit flagged.

Minimal fix applied: new `provision_scratch_dir(path)` mkdirs the per-run scratch
dir and `chmod 0777` (posix), called by `run_confined_worker` in place of the bare
mkdir. It touches ONLY the per-run scratch dir; it does NOT change the
operator's `LYBRA_APPROVED_SCRATCH_ROOT` mode, the truth/.lybra/product read-only
boundary, the mount sources, `_FORBIDDEN_NETWORKS`, or the non-public gate.

Real-container validation after the fix: a mismatched non-root uid wrote
`/scratch` successfully, while the rootfs stayed read-only (truth still not
writable). Operators are advised to keep the approved scratch root at 0700 so the
0777 run dir is not reachable by other host users through the filesystem path
(the bind mount bypasses the parent only for the container).

### Tests
- `tools/sandbox_runtime/tests/test_confined_worker.py`: 20 passed (17 + 3 new:
  filename-leak detection, scratch-dir world-writable, idempotent).
- Full `tools/` suite: 358 passed.

### Status
- [x] Slice A minimal fix + test
- [x] Slice B reproduce + minimal fix + test + real-container validation
- [ ] cc glm independent audit -> Owner approval -> finalize
