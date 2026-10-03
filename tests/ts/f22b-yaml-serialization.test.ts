/**
 * AIPOS-F22B 大项B: 门写卡 YAML 序列化夹具
 *
 * 验收:
 * 1. result_summary 含粗体标记、引号、冒号、换行等特殊字符, 经 render_markdown 输出后 yaml.safe_load 解析成功
 * 2. 空列表、空字典、布尔、null、数字、嵌套映射正确序列化
 * 3. stdlib fallback (yaml 不可用时) 同样正确(由 Python 单测覆盖, 本文件仅测 PyYAML 路径)
 *
 * 本夹具验证 tools/aipos_cli/record_writer.render_markdown 的 YAML 序列化修复。
 */

import { describe, it } from "node:test";
import assert from "node:assert";
import { execFileSync } from "node:child_process";
import { readFileSync, writeFileSync, mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

function findProjectRoot(): string {
  let dir = process.cwd();
  for (let i = 0; i < 10; i++) {
    if (
      existsSync(join(dir, "package.json")) &&
      existsSync(join(dir, "agents"))
    ) {
      return dir;
    }
    const parent = join(dir, "..");
    if (parent === dir) break;
    dir = parent;
  }
  return process.cwd();
}

const PROJECT_ROOT = findProjectRoot();

function existsSync(p: string): boolean {
  try {
    require("fs").existsSync(p);
    return true;
  } catch {
    return false;
  }
}

function runPythonRenderMarkdown(metadata: Record<string, unknown>, body: string): string {
  const tmpDir = mkdtempSync(join(tmpdir(), "f22b-yaml-"));
  const inputFile = join(tmpDir, "input.json");
  const outputFile = join(tmpDir, "output.md");
  const metadataJson = JSON.stringify({ metadata, body, order: null });

  writeFileSync(inputFile, metadataJson);

  const pythonScript = `
import json
import sys
sys.path.insert(0, "${PROJECT_ROOT}")
from tools.aipos_cli.record_writer import render_markdown

with open("${inputFile}") as f:
    data = json.load(f)

result = render_markdown(data["metadata"], data["body"], data.get("order"))
with open("${outputFile}", "w") as f:
    f.write(result)
`;

  execFileSync("python3", ["-c", pythonScript], { timeout: 10000 });

  const output = readFileSync(outputFile, "utf-8");
  rmSync(tmpDir, { recursive: true, force: true });
  return output;
}

function parseFrontmatterWithYaml(md: string): Record<string, unknown> {
  const tmpDir = mkdtempSync(join(tmpdir(), "f22b-yaml-parse-"));
  const inputFile = join(tmpDir, "input.md");
  const outputFile = join(tmpDir, "output.json");

  writeFileSync(inputFile, md);

  const pythonScript = `
import json
import sys
sys.path.insert(0, "${PROJECT_ROOT}")
import yaml

with open("${inputFile}") as f:
    content = f.read()

lines = content.splitlines()
end_idx = lines.index("---", 1) if "---" in lines[1:] else -1
if end_idx == -1:
    result = {"error": "no end marker"}
else:
    frontmatter = "\\n".join(lines[1:end_idx])
    parsed = yaml.safe_load(frontmatter)
    result = {"parsed": parsed}

with open("${outputFile}", "w") as f:
    json.dump(result, f, ensure_ascii=False)
`;

  execFileSync("python3", ["-c", pythonScript], { timeout: 10000 });

  const result = JSON.parse(readFileSync(outputFile, "utf-8"));
  rmSync(tmpDir, { recursive: true, force: true });
  if (result.error) {
    throw new Error(result.error);
  }
  return result.parsed;
}

describe("F22B-B: YAML 序列化修复", () => {
  it("result_summary 含 **粗体** 应被正确引号", () => {
    const metadata = {
      task_id: "AIPOS-F22B",
      result_summary: "**完成**",
    };
    const md = runPythonRenderMarkdown(metadata, "body");
    console.log("MD:", md.split("\\n").slice(0, 10).join("\\n"));

    const parsed = parseFrontmatterWithYaml(md);
    assert.strictEqual(parsed["task_id"], "AIPOS-F22B");
    assert.strictEqual(parsed["result_summary"], "**完成**");
    console.log("✓ **粗体** 正确序列化并解析");
  });

  it("result_summary 含引号/冒号/换行应正确序列化", () => {
    const metadata = {
      task_id: "AIPOS-F22B",
      result_summary: '含"引号"和:冒号\\n及换行',
    };
    const md = runPythonRenderMarkdown(metadata, "body");
    const parsed = parseFrontmatterWithYaml(md);
    assert.strictEqual(parsed["result_summary"], '含"引号"和:冒号\\n及换行');
    console.log("✓ 引号/冒号/换行 正确序列化");
  });

  it("列表项含 * 开头字符串应被引号", () => {
    const metadata = {
      task_id: "AIPOS-F22B",
      tags: ["normal", "*leading-star", "**bold**", "&ampersand"],
    };
    const md = runPythonRenderMarkdown(metadata, "body");
    const parsed = parseFrontmatterWithYaml(md);
    assert.deepStrictEqual(parsed["tags"], [
      "normal",
      "*leading-star",
      "**bold**",
      "&ampersand",
    ]);
    console.log("✓ 列表项含 * / & 开头正确引号");
  });

  it("空列表/空字典/布尔/null/数字正确序列化", () => {
    const metadata = {
      empty_list: [],
      empty_dict: {},
      bool_true: true,
      bool_false: false,
      null_val: null,
      int_val: 42,
      float_val: 3.14,
    };
    const md = runPythonRenderMarkdown(metadata, "body");
    const parsed = parseFrontmatterWithYaml(md);
    assert.deepStrictEqual(parsed["empty_list"], []);
    assert.deepStrictEqual(parsed["empty_dict"], {});
    assert.strictEqual(parsed["bool_true"], true);
    assert.strictEqual(parsed["bool_false"], false);
    assert.strictEqual(parsed["null_val"], null);
    assert.strictEqual(parsed["int_val"], 42);
    assert.strictEqual(parsed["float_val"], 3.14);
    console.log("✓ 空列表/空字典/布尔/null/数字正确");
  });

  it("嵌套映射(depth-1)正确序列化", () => {
    const metadata = {
      task_id: "AIPOS-F22B",
      agent_runtime: {
        harness: "pi",
        model_self_reported: "glm-5.3",
        tokens_in: 1000,
        tokens_out: 500,
      },
    };
    const md = runPythonRenderMarkdown(metadata, "body");
    const parsed = parseFrontmatterWithYaml(md);
    assert.deepStrictEqual(parsed["agent_runtime"], {
      harness: "pi",
      model_self_reported: "glm-5.3",
      tokens_in: 1000,
      tokens_out: 500,
    });
    console.log("✓ 嵌套映射正确序列化");
  });
});