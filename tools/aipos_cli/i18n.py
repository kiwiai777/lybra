"""AIPOS-F276 — P4 双语机制: 用户语言选择的唯一解析 + 双语 texts 块的唯一读取器。

一句话: 用户设一次语言(home 配置 / 环境变量 / 单次 --lang), 所有已双语的出声点按同一结果出文案; 双语块结构只有一种、读法只有一处。

声明(config.schema language 节, language_declaration 唯一读取口, fail-closed):
  - 优先序 precedence = 全局 --lang(CLI 根解析器一处注册, dest = ARGS_DEST)> 环境变量 LYBRA_LANG > home 配置
    (configuration_sources.global_config, ~/.lybra/config.json 的 language 键; 读口 workspace_config.load_global_config)> 缺省;
  - 取值域与缺省只读 verbs.schema board_readonly_pages.locale(board_pages.pages_declaration 唯一读取口; 不另列语言清单);
  - 任一档给了非法值 = LanguageError(拒因 + 出口, texts.invalid), 不静默回退。
双语 texts 标准: {<语言>: {…}} 两层表(可嵌套), 语言集恰为 locale.supported, 各语言键集(含嵌套路径)相等; 唯一读取器 texts(block, lang, where=...)
每次读取先核结构(缺键 / 多键 / 语言不齐 = SchemaLoadError, 测试期即红, 与看板 _I18N_TEMPLATES「缺 en = 测试失败」同一纪律)。
写入只有 set_language 一处(home 配置 language 键, 经 workspace_config.set_global_config_value 唯一写路径); 其余只读。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

#: CLI 根解析器全局 --lang 的 argparse dest(aipos_cli.build_parser 一处注册; 子命令兼容旗标写同一 dest)
ARGS_DEST = "global_lang"
#: 本模块读取的环境变量名(config.schema environment_variables 声明同名; language_declaration 核声明 env 与之一致)
LANG_ENV = "LYBRA_LANG"
_PRECEDENCE = ("cli_flag", "env", "home_config", "default")
_TEXT_KEYS = ("user_language_line", "source_cli_flag", "source_env", "source_home_config", "source_default", "invalid", "exit_cli_flag",
              "exit_env", "exit_home_config", "set_done", "set_same", "show_line", "unset")


class LanguageError(ValueError):
    """语言取值非法(消息 = 声明 texts.invalid 渲染: 取值 / 合法值 / 来源 / 出口)。"""

    def __init__(self, message: str, *, value: Any, source: str) -> None:
        super().__init__(message)
        self.value = value
        self.source = source


# ---------------------------------------------------------------------------
# 声明(唯一读取口)
# ---------------------------------------------------------------------------

def locale_declaration() -> dict[str, Any]:
    """语言取值域与缺省的唯一声明 = verbs.schema board_readonly_pages.locale(经 board_pages.pages_declaration, fail-closed)。"""
    from tools.aipos_cli.board_pages import pages_declaration

    return pages_declaration()["locale"]


def supported_languages() -> list[str]:
    return [str(code) for code in locale_declaration()["supported"]]


def default_language() -> str:
    return str(locale_declaration()["default"])


def language_declaration() -> dict[str, Any]:
    """config.schema language 节(fail-closed): cli_flag / env / home_config_key 为非空串; precedence 恰为四档依序;
    texts = 双语块(结构经 check_block)且每种语言有 _TEXT_KEYS 与每个支持语言的 name_<语言>。"""
    from tools.schema_loader import SchemaLoadError, code_repo_schema_root, load_schema

    decl = load_schema("config", code_repo_schema_root()).get("language")
    where = "config.schema.json language"
    if not isinstance(decl, dict) or not all(isinstance(decl.get(k), str) and decl[k] for k in ("cli_flag", "env", "home_config_key")):
        raise SchemaLoadError(f"{where}(cli_flag / env / home_config_key)未声明齐")
    if decl["env"] != LANG_ENV:
        raise SchemaLoadError(f"{where}.env 须为 {LANG_ENV}(config.schema environment_variables 声明的名字), 得到 {decl['env']!r}")
    if list(decl.get("precedence") or []) != list(_PRECEDENCE):
        raise SchemaLoadError(f"{where}.precedence 须恰为 {list(_PRECEDENCE)}(全局参数 > 环境变量 > home 配置 > 缺省)")
    block = check_block(decl.get("texts"), where=f"{where}.texts")
    required = [*_TEXT_KEYS, *(f"name_{code}" for code in supported_languages())]
    missing = sorted(k for k in required if not isinstance(block[default_language()].get(k), str))
    if missing:
        raise SchemaLoadError(f"{where}.texts 缺 {missing}")
    return decl


def home_config_key() -> str:
    return str(language_declaration()["home_config_key"])


def env_name() -> str:
    return str(language_declaration()["env"])


def cli_flag() -> str:
    return str(language_declaration()["cli_flag"])


# ---------------------------------------------------------------------------
# 双语 texts: 结构标准 + 唯一读取器
# ---------------------------------------------------------------------------

def _key_paths(table: Any, prefix: str = "") -> set[str]:
    """一种语言表的键路径集(嵌套表逐层展开; 叶子须为非空串以外的值也计路径, 类型由调用方各自核)。"""
    paths: set[str] = set()
    for key, value in (table or {}).items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            paths |= {f"{path}.{sub}" for sub in _key_paths(value)} or {path}
        else:
            paths.add(path)
    return paths


def block_problems(block: Any) -> list[str]:
    """双语块结构问题清单(空 = 合格): 非对象 / 语言集 ≠ locale.supported / 某语言非对象 / 各语言键集(含嵌套路径)不等 / 叶子非字符串。"""
    supported = supported_languages()
    if not isinstance(block, dict):
        return [f"须为 {{<语言>: {{…}}}} 两层表, 得到 {type(block).__name__}"]
    problems: list[str] = []
    if sorted(block) != sorted(supported):
        problems.append(f"语言须恰为看板 locale.supported {supported}, 得到 {sorted(block)}")
    tables = {lang: block.get(lang) for lang in supported if lang in block}
    for lang, table in tables.items():
        if not isinstance(table, dict):
            problems.append(f"{lang} 须为对象")
    paths = {lang: _key_paths(table) for lang, table in tables.items() if isinstance(table, dict)}
    union = set().union(*paths.values()) if paths else set()
    for lang, have in paths.items():
        if union - have:
            problems.append(f"{lang} 缺 {sorted(union - have)}")
    for lang, table in tables.items():
        if isinstance(table, dict):
            bad = sorted(p for p in paths[lang] if not isinstance(_leaf(table, p), str))
            if bad:
                problems.append(f"{lang} 非字符串叶子 {bad}")
    return problems


def _leaf(table: dict[str, Any], path: str) -> Any:
    node: Any = table
    for part in path.split("."):
        node = node.get(part) if isinstance(node, dict) else None
    return node


def check_block(block: Any, *, where: str) -> dict[str, Any]:
    """双语块结构校验(fail-closed): 有问题 = SchemaLoadError(点名块与问题)。合格原样返回。"""
    from tools.schema_loader import SchemaLoadError

    problems = block_problems(block)
    if problems:
        raise SchemaLoadError(f"{where} 双语块不合标准(AIPOS-F276): " + "; ".join(problems))
    return block


def texts(block: Any, lang: str | None = None, *, where: str) -> dict[str, Any]:
    """双语块的唯一读取器: 先核结构(check_block), 再取一种语言的表。lang 缺省(None / 空)= 看板 locale.default(既有「缺省语言」行为);
    给了不支持的语言 = LanguageError(不静默回退; 调用方应传 resolve_language 的结果)。"""
    table = check_block(block, where=where)
    code = str(lang or "") or default_language()
    if code not in table:
        raise _invalid(code, source="argument")
    return table[code]


# ---------------------------------------------------------------------------
# 语言选择: 唯一解析
# ---------------------------------------------------------------------------

def _fmt(template: str, **values: Any) -> str:
    from tools.schema_loader import SchemaLoadError

    try:
        return template.format(**values)
    except (KeyError, IndexError, ValueError) as exc:
        raise SchemaLoadError(f"config.schema.json language.texts 模板占位无值: {exc}") from exc


def _messages() -> dict[str, Any]:
    """拒因 / 来源说明用缺省语言(语言本身解析失败时无从得知用户语言)。"""
    return language_declaration()["texts"][default_language()]


def _source_text(source: str, *, path: Path | str | None = None, lang: str | None = None) -> str:
    decl = language_declaration()
    t = decl["texts"][lang or default_language()]
    if source == "cli_flag":
        return _fmt(t["source_cli_flag"], flag=decl["cli_flag"])
    if source == "env":
        return _fmt(t["source_env"], env=decl["env"])
    if source == "home_config":
        return _fmt(t["source_home_config"], path=str(path or "~/.lybra/config.json"), key=decl["home_config_key"])
    if source == "default":
        return t["source_default"]
    return source


def _invalid(value: Any, *, source: str, path: Path | str | None = None) -> LanguageError:
    decl = language_declaration()
    t = _messages()
    supported = " / ".join(supported_languages())
    exit_key = {"cli_flag": "exit_cli_flag", "env": "exit_env", "home_config": "exit_home_config"}.get(source, "exit_cli_flag")
    exit_text = _fmt(t[exit_key], flag=decl["cli_flag"], env=decl["env"], supported=supported,
                     path=str(path or "~/.lybra/config.json"))
    message = _fmt(t["invalid"], value=value, supported=supported, source=_source_text(source, path=path), exit=exit_text)
    return LanguageError(message, value=value, source=source)


def validate_language(value: Any, *, source: str, path: Path | str | None = None) -> str:
    """一个档位给出的语言值: 在 locale.supported 内 = 原值; 否则 LanguageError(拒因带来源与出口)。"""
    code = str(value).strip() if isinstance(value, str) else value
    if not isinstance(code, str) or code not in supported_languages():
        raise _invalid(value, source=source, path=path)
    return code


def _cli_value(args: Any) -> str | None:
    if args is None:
        return None
    if isinstance(args, str):
        return args or None
    value = getattr(args, ARGS_DEST, None)
    return str(value) if value not in (None, "") else None


def resolve_language_with_source(args: Any = None, env: dict[str, str] | None = None,
                                 home_config: dict[str, Any] | None = None) -> tuple[str, str]:
    """优先序(config.schema language.precedence): 全局 --lang(args 的 ARGS_DEST, 或直接给语言串)> 环境变量 LYBRA_LANG >
    home 配置 language 键 > 看板 locale.default。返回 (语言, 来源档 ∈ precedence)。
    env 缺省 = 进程环境; home_config 缺省 = workspace_config.load_global_config(env)(显式 env 无 HOME = 不读真实 home)。
    命中档的值非法 = LanguageError(不往下一档回退)。"""
    from tools.aipos_cli.workspace_config import global_config_path, load_global_config

    source_env = env if env is not None else os.environ
    cli_value = _cli_value(args)
    if cli_value is not None:
        return validate_language(cli_value, source="cli_flag"), "cli_flag"
    env_value = str(source_env.get(env_name()) or "").strip()
    if env_value:
        return validate_language(env_value, source="env"), "env"
    config = home_config if home_config is not None else load_global_config(env)
    key = home_config_key()
    if config.get(key) not in (None, ""):
        return validate_language(config[key], source="home_config", path=global_config_path(env)), "home_config"
    return default_language(), "default"


def resolve_language(args: Any = None, env: dict[str, str] | None = None, home_config: dict[str, Any] | None = None) -> str:
    """resolve_language_with_source 的语言值(各 CLI 出声点与开工稿 / 看板缺省同调)。"""
    return resolve_language_with_source(args, env, home_config)[0]


def describe_resolution(args: Any = None, env: dict[str, str] | None = None) -> str:
    """一行人话: 当前用户语言与来源(lybra config set-language 回显 / 诊断用; 文案按解析出的语言)。"""
    from tools.aipos_cli.workspace_config import global_config_path

    lang, source = resolve_language_with_source(args, env)
    t = language_declaration()["texts"][lang]
    return _fmt(t["show_line"], language=lang, source=_source_text(source, path=global_config_path(env), lang=lang))


def board_default_language(env: dict[str, str] | None = None) -> str:
    """看板缺省语言(浏览器未选时): 环境变量 / home 配置 / locale.default(无 --lang 档; 看板进程的环境与 home)。
    home 配置或环境变量给了非法值 = 看板不能整页拒, 用 locale.default 并在 stderr 打一行拒因(不静默)。"""
    try:
        return resolve_language(None, env)
    except LanguageError as exc:
        print(f"lybra board: {exc}", file=sys.stderr)
        return default_language()


def set_language(language: str, *, env: dict[str, str] | None = None) -> dict[str, Any]:
    """`lybra config set-language` 的写入: home 配置(configuration_sources.global_config)的 home_config_key 键, 写口复用
    workspace_config.set_global_config_value(唯一写路径, 保留其余键; 不新增文件格式)。取值经 validate_language; 非法 = LanguageError
    (不写)。返回 {path, before, after, changed}; 值不变 = 不改写文件。"""
    from tools.aipos_cli.workspace_config import global_config_path, load_global_config, set_global_config_value

    key = home_config_key()
    path = global_config_path(env)
    if path is None:
        raise ValueError(f"HOME_NOT_RESOLVED: cannot locate ~/.lybra/config.json to set {key}")
    after = validate_language(language, source="home_config", path=path)
    before = load_global_config(env).get(key)
    if before == after:
        return {"path": path, "before": before, "after": after, "changed": False}
    return {"path": set_global_config_value(key, after, env=env), "before": before, "after": after, "changed": True}


# ---------------------------------------------------------------------------
# 用户语言行(开工稿 / 顾问启动稿)
# ---------------------------------------------------------------------------

def language_name(code: str, lang: str | None = None) -> str:
    """语言名(按 lang 那种语言写; 缺省 = code 自身那种语言)。"""
    table = language_declaration()["texts"][lang or code]
    return str(table[f"name_{code}"])


def user_language_line(lang: str) -> str:
    """「用户语言 = <语言>; 对用户(Owner)说话一律用该语言」一行, 用该语言本身写(声明 texts.user_language_line)。"""
    code = validate_language(lang, source="argument")
    t = language_declaration()["texts"][code]
    return _fmt(t["user_language_line"], language=code, language_name=language_name(code))
