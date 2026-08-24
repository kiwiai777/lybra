/**
 * AIPOS-F37(-fix1-fix1) 大项C: 凭据副本清理 — 先红后绿夹具(经 bin 入 run-all)
 *
 * 场景(凭据副本): 真跑两版 tools/aipos_cli/token_rotation.py(bin/lybra 所载 CLI 器官)
 *  - 红 = 修复前模块(git 取 F37 引入 commit 的父版): _backup_config 铸出 connection.json.bak-*
 *        可误读前缀副本且无清理函数 → 磁盘旧副本永存, 前缀型读取器误用面扩大
 *  - 绿 = HEAD 模块: 备份名 .backup-connection-{ts}.json.disabled(不可误读),
 *        _cleanup_legacy_backups 将旧 .bak-* 全量改名为 .disabled, 候选发现只剩 connection.json 唯一源
 * 真实执行(非源码 grep): python3 importlib 加载两版模块, 在一次性 tmp 工作区跑函数, JSON 回传。
 *
 * 锚点: C2 身份解析单源(.bak-token 旧副本 fp 误用实撞)
 * 跑法: node tests/f37b-credential-copy-redgreen.test.ts (或经 run-all.sh 常驻)
 */
import { describe, it } from "node:test";
import assert from "node:assert";
import { readFileSync, existsSync, mkdtempSync, writeFileSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";

function findProjectRoot(): string {
  let dir = process.cwd();
  for (let i = 0; i < 10; i++) {
    if (existsSync(join(dir, "package.json")) && existsSync(join(dir, "agents"))) return dir;
    const parent = join(dir, "..");
    if (parent === dir) break;
    dir = parent;
  }
  return process.cwd();
}
const PROJECT_ROOT = findProjectRoot();
const MOD_REL = "tools/aipos_cli/token_rotation.py";

function gitOut(args: string[]): string {
  const r = spawnSync("git", ["-C", PROJECT_ROOT, ...args], { encoding: "utf8" });
  if (r.status !== 0) throw new Error(`git ${args.join(" ")} 失败: ${r.stderr}`);
  return r.stdout;
}
function f37Commit(): string {
  const h = gitOut(["log", "--format=%H", "--grep=AIPOS-F37: 审计车道零人肉三合一", "-1"]).trim();
  assert.ok(h.length === 40, `应能定位 F37 引入 commit`);
  return h;
}

// 一次性工作区: 模拟修复前磁盘状态(connection.json + 2 个旧 .bak 副本, 内含可误用 token 载荷)
function seedWorkdir(tag: string): string {
  const dir = mkdtempSync(join(tmpdir(), `f37b-${tag}-`));
  const payload = (n: string) => JSON.stringify({ tokens: [{ role: "executor", token: `LEGACY-${n}-PLAINTEXT` }] });
  writeFileSync(join(dir, "connection.json"), JSON.stringify({ mcp: { url: "http://stub" }, tokens: [] }));
  writeFileSync(join(dir, "connection.json.bak-20260822-131610"), payload("a"));
  writeFileSync(join(dir, "connection.json.bak-20260822-131641-411"), payload("b"));
  return dir;
}

// python 驱动: 加载指定版本模块并真跑 _backup_config / _cleanup_legacy_backups
const PY_DRIVER = [
  "import importlib.util, json, sys",
  "from pathlib import Path",
  "repo, mod_path, workdir = sys.argv[1], sys.argv[2], sys.argv[3]",
  "sys.path.insert(0, repo)",
  "spec = importlib.util.spec_from_file_location('tr_mod_under_test', mod_path)",
  "mod = importlib.util.module_from_spec(spec)",
  "spec.loader.exec_module(mod)",
  "lybra_dir = Path(workdir)",
  "conn = lybra_dir / 'connection.json'",
  "legacy_before = sorted(p.name for p in lybra_dir.glob('connection.json.bak-*'))",
  "has_cleanup = hasattr(mod, '_cleanup_legacy_backups')",
  "backup = mod._backup_config(conn)",
  "cleaned = mod._cleanup_legacy_backups(lybra_dir) if has_cleanup else []",
  "legacy_after = sorted(p.name for p in lybra_dir.glob('connection.json.bak-*'))",
  "disabled = sorted(p.name for p in lybra_dir.glob('*.disabled'))",
  "misread = sorted(p.name for p in lybra_dir.glob('connection.json*'))",
  "print('F37B_JSON::' + json.dumps({",
  "  'module_has_cleanup': has_cleanup,",
  "  'backup_name': backup.name,",
  "  'legacy_before': legacy_before, 'legacy_after': legacy_after,",
  "  'cleaned': cleaned, 'disabled': disabled, 'misread_candidates': misread,",
  "}))",
].join("\n");

interface DriverResult {
  module_has_cleanup: boolean;
  backup_name: string;
  legacy_before: string[];
  legacy_after: string[];
  cleaned: string[];
  disabled: string[];
  misread_candidates: string[];
}

function runDriver(modPath: string, workdir: string): DriverResult {
  const driverFile = join(tmpdir(), `f37b-driver-${Date.now()}-${Math.random().toString(36).slice(2)}.py`);
  writeFileSync(driverFile, PY_DRIVER);
  const r = spawnSync("python3", [driverFile, PROJECT_ROOT, modPath, workdir], { encoding: "utf8" });
  if (r.status !== 0) throw new Error(`python 驱动失败: ${r.stderr}\n${r.stdout}`);
  const line = r.stdout.split("\n").find((l) => l.startsWith("F37B_JSON::"));
  assert.ok(line, `应回传 JSON 结果行, 实得 stdout: ${r.stdout.slice(0, 300)}`);
  return JSON.parse(line.slice("F37B_JSON::".length));
}

describe("F37-C 凭据副本 — 先红后绿(真跑两版模块)", () => {
  it("红: 修复前模块备份铸出 .bak- 可误读副本, 无清理函数, 误用面扩大", () => {
    const prePath = join(tmpdir(), `f37b-prefix-${Date.now()}.py`);
    writeFileSync(prePath, gitOut(["show", `${f37Commit()}^:${MOD_REL}`]));
    const wd = seedWorkdir("red");
    const res = runDriver(prePath, wd);
    console.log(`[RED] 修复前模块真跑结果:`);
    console.log(`  module_has_cleanup = ${res.module_has_cleanup}`);
    console.log(`  _backup_config 产出 = ${res.backup_name}`);
    console.log(`  旧格式副本: 前=${res.legacy_before.length}个 → 后=${res.legacy_after.length}个(${res.legacy_after.join(", ")})`);
    console.log(`  前缀型读取器误用候选(connection.json*) = ${res.misread_candidates.join(", ")}`);
    assert.strictEqual(res.module_has_cleanup, false, "修复前: 无 _cleanup_legacy_backups");
    assert.ok(/^connection\.json\.bak-/.test(res.backup_name), "修复前: 备份名=可误读 .bak- 前缀");
    assert.strictEqual(res.legacy_after.length, res.legacy_before.length + 1, "修复前: 备份行为本身新增一个 .bak- 副本");
    assert.ok(res.misread_candidates.length > 1, "修复前: connection.json* 误用候选 >1(唯一源被破坏)");
    assert.strictEqual(res.disabled.length, 0, "修复前: 磁盘无 .disabled 不可误读形态");
    console.log("[RED 判定] 凭据副本修复前=红: 备份铸新风险+旧副本永存 ✗");
  });

  it("绿: HEAD 模块备份名 .disabled 不可误读, 旧副本全量改名, 候选只剩唯一源", () => {
    const wd = seedWorkdir("green");
    const res = runDriver(join(PROJECT_ROOT, MOD_REL), wd);
    console.log(`[GREEN] HEAD 模块真跑结果:`);
    console.log(`  module_has_cleanup = ${res.module_has_cleanup}`);
    console.log(`  _backup_config 产出 = ${res.backup_name}`);
    console.log(`  清理改名 = ${res.cleaned.join(", ")}`);
    console.log(`  旧格式残留(.bak-) = ${res.legacy_after.length}个; .disabled = ${res.disabled.length}个`);
    console.log(`  前缀型读取器误用候选(connection.json*) = ${res.misread_candidates.join(", ")}`);
    assert.strictEqual(res.module_has_cleanup, true, "修复后: _cleanup_legacy_backups 存在");
    assert.ok(/^\.backup-connection-\d{8}-\d{6}-\d+(\.\d+)?\.json\.disabled$/.test(res.backup_name), "修复后: 备份名=.backup-connection-{ts}.json.disabled");
    assert.deepStrictEqual(res.legacy_after, [], "修复后: 旧 .bak- 副本 0 残留");
    assert.deepStrictEqual(res.cleaned.sort(), ["connection.json.bak-20260822-131610", "connection.json.bak-20260822-131641-411"], "修复后: 两个种子旧副本都被改名");
    assert.deepStrictEqual(res.misread_candidates, ["connection.json"], "修复后: 候选发现只剩唯一源 connection.json");
    assert.ok(res.disabled.length >= 3, "修复后: 1 新备份 + 2 legacy 均为 .disabled 形态");
    console.log("[GREEN 判定] 凭据副本修复后=绿: 唯一源成立, 副本不可误读 ✓");
  });

  it("单源纪律: 模块只从 .lybra/connection.json 读, 清理函数只此一份", () => {
    const src = readFileSync(join(PROJECT_ROOT, MOD_REL), "utf-8");
    assert.ok(src.includes('".lybra" / "connection.json"'), "凭据路径=workspace/.lybra/connection.json(唯一读取源)");
    assert.strictEqual((src.match(/def _cleanup_legacy_backups/g) || []).length, 1, "清理函数只有一份实现");
    assert.strictEqual((src.match(/def _backup_config/g) || []).length, 1, "备份函数只有一份实现");
    assert.ok(!/\.bak-token/.test(src), "不新增 .bak- 前缀形态");
    console.log("[GREEN 判定] 单源纪律: 读源唯一/备份唯一/清理唯一 ✓");
  });
});
