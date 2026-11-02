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
import re as _re
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
    """语言取值域与缺省的唯一声明 = verbs.schema board_readonly_pages.locale(fail-closed: supported 为非空列表、default ∈ supported, 与
    board_pages.pages_declaration 同一核法)。AIPOS-F277: 直接经 schema_loader 读该节, 不再导入 board_pages——workspace_config(finalize 合并后
    预载闭包内, 静态导入扫描)经 i18n 取 home 根来源文案, 经 board_pages 会把看板 / TUI 模块牵进闭包; 两口同值由 F277 夹具核。"""
    from tools.schema_loader import SchemaLoadError, code_repo_schema_root, load_schema

    locale = (load_schema("verbs", code_repo_schema_root()).get("board_readonly_pages") or {}).get("locale")
    if (not isinstance(locale, dict) or not isinstance(locale.get("supported"), list) or not locale["supported"]
            or locale.get("default") not in locale["supported"]):
        raise SchemaLoadError("verbs.schema.json board_readonly_pages.locale(supported 非空 / default ∈ supported)未声明齐")
    return locale


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


# ---------------------------------------------------------------------------
# AIPOS-F277: Owner 面双语——模板填充 / 双语渲染值 / 译文覆盖层
# ---------------------------------------------------------------------------

#: 模板占位 = {标识符}(可带转换 !r / !s, 同 str.format); 字面的「{标识符}」写成 {{标识符}}; 其他花括号(如「{default, items}」)原样保留
_PLACEHOLDER = _re.compile(r"\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}|\{([A-Za-z_][A-Za-z0-9_]*)(?:!([rs]))?\}")


def placeholders(template: str) -> set[str]:
    """模板里的占位名集合(字面转义 {{名}} 不计)。双语块各语言同一键的占位集须相等(守卫 placeholder_problems)。"""
    return {m.group(2) for m in _PLACEHOLDER.finditer(str(template)) if m.group(2)}


def fill(template: Any, values: dict[str, Any] | None = None, *, where: str, lang: str | None = None) -> str:
    """Owner 面文案模板填充(唯一实现): {名} 换成 values[名](双语渲染值 Localized 按 lang 取那种语言), {{名}} 还原为字面 {名},
    其余花括号原样。占位无值 = SchemaLoadError(声明与实现不符, 不猜)。"""
    from tools.schema_loader import SchemaLoadError

    vals = values or {}

    def sub(m: _re.Match[str]) -> str:
        if m.group(1):
            return "{" + m.group(1) + "}"
        name = m.group(2)
        if name not in vals:
            raise SchemaLoadError(f"{where} 模板占位 {{{name}}} 无值")
        value = vals[name]
        if m.group(3) == "r":
            return repr(pick(value, lang)) if isinstance(value, Localized) else repr(value)
        return pick(value, lang)

    return _PLACEHOLDER.sub(sub, str(template))


class Localized(str):
    """双语渲染值: str 本身 = 缺省语言(看板 locale.default)那份——事项 ID / 哈希 / JSON / 看板照旧用它, 行为不变;
    by_lang 另带各语言渲染, 出声点按用户语言经 pick 取。拼接(+ / f-string / join)得普通 str(只剩缺省语言), 双语只经 fill / localize 传递。"""

    by_lang: dict[str, str]
    default: str

    def __new__(cls, by_lang: dict[str, str], default: str | None = None) -> "Localized":
        code = default or default_language()
        obj = super().__new__(cls, by_lang[code])
        obj.by_lang = dict(by_lang)
        obj.default = code
        return obj

    def __reduce__(self) -> tuple[Any, ...]:  # pickle / deepcopy(task_loader.truth_cached 冻存)保留各语言
        return (Localized, (dict(self.by_lang), self.default))


def _register_yaml_representer() -> None:
    """双语渲染值写进 YAML(记录 frontmatter 等)时按普通 str(缺省语言那份)落盘: SafeDumper / Dumper 都登记(PyYAML 不认 str 子类)。"""
    try:
        import yaml
    except ImportError:  # PyYAML 缺 = 无 YAML 写路径, 无事可做
        return
    for dumper in (yaml.SafeDumper, yaml.Dumper):
        dumper.add_representer(Localized, lambda d, value: d.represent_str(str(value)))


_register_yaml_representer()


def pick(value: Any, lang: str | None = None) -> str:
    """出声点取值: Localized = 该语言那份(lang 缺省 = 缺省语言, 即 str 本身); 其余 = str(value)。"""
    if isinstance(value, Localized) and lang:
        return value.by_lang.get(lang, str(value))
    return str(value)


def localize(templates: dict[str, Any], values: dict[str, Any] | None = None, *, where: str) -> str:
    """同一组值按各语言模板各填一份 → Localized(只有缺省语言模板 = 普通 str)。templates = {语言: 模板}, 须含缺省语言。"""
    from tools.schema_loader import SchemaLoadError

    default = default_language()
    if default not in templates:
        raise SchemaLoadError(f"{where} 缺缺省语言 {default} 的模板")
    rendered = {code: fill(tpl, values, where=where, lang=code) for code, tpl in templates.items()}
    return Localized(rendered, default) if len(rendered) > 1 else rendered[default]


def lines_in(lines: list[Any], lang: str | None) -> list[str]:
    """一组出声行按用户语言取值(Localized 取该语言; 普通 str 原样)。"""
    return [pick(line, lang) for line in lines]


def placeholder_problems(block: dict[str, Any]) -> list[str]:
    """双语块各语言同一键的占位集须相等(否则某语言渲染缺值 / 漏信息)。返回问题清单(空 = 合格)。"""
    langs = [code for code in block if isinstance(block.get(code), dict)]
    if len(langs) < 2:
        return []
    base, others = langs[0], langs[1:]
    problems: list[str] = []
    for path in sorted(_key_paths(block[base])):
        want = placeholders(_leaf(block[base], path)) if isinstance(_leaf(block[base], path), str) else set()
        for code in others:
            leaf = _leaf(block[code], path)
            got = placeholders(leaf) if isinstance(leaf, str) else set()
            if got != want:
                problems.append(f"{path}: {base} {sorted(want)} ≠ {code} {sorted(got)}")
    return problems


def surface_texts(decl: Any, lang: str | None, *, where: str) -> dict[str, Any]:
    """Owner 面一个出声点的双语块(声明 {zh, en})按用户语言取表: 结构经 check_block, 占位集各语言相等(fail-closed)。"""
    from tools.schema_loader import SchemaLoadError

    table = texts(decl, lang, where=where)
    problems = placeholder_problems(decl)
    if problems:
        raise SchemaLoadError(f"{where} 双语块占位不一致(AIPOS-F277): " + "; ".join(problems))
    return table


# 译文覆盖层: 原位单语文案被车道外模块按路径读取(不能挪走)时, 其余语言的同路径译文放在同一声明的 translations.<语言> 里;
# 缺省语言仍是原位那份(单一来源), 覆盖层只放其余语言。守卫 overlay_problems: 覆盖层每个叶子在原位有同路径字符串、占位集相等;
# 声明的应译范围内含中日韩字符的原位叶子都有译文(完整性)。

#: 应译范围内不译的键(开发面说明)
OVERLAY_SKIP_KEYS = frozenset({"description", "note", "notes", "single_implementation", "closed_card_reasons_note"})
_CJK = _re.compile(r"[　-〿㐀-䶿一-鿿＀-￯]")


def has_cjk(text: Any) -> bool:
    return bool(_CJK.search(str(text or "")))


def _get_path(decl: Any, path: str) -> Any:
    node: Any = decl
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def overlay_template(decl: dict[str, Any], path: str, lang: str) -> str | None:
    """覆盖层取 path 那条模板的 lang 译文(无 = None)。缺省语言 = 原位那份。"""
    if lang == default_language():
        found = _get_path(decl, path)
        return found if isinstance(found, str) else None
    found = _get_path((decl.get("translations") or {}).get(lang) or {}, path)
    return found if isinstance(found, str) else None


def overlay_localize(decl: dict[str, Any], path: str, template: Any, values: dict[str, Any] | None, *, where: str) -> str:
    """原位模板 template(path 处, 缺省语言)+ 覆盖层同路径各语言译文 → Localized(无译文的语言不出, pick 落回缺省语言)。"""
    templates: dict[str, Any] = {default_language(): template}
    for code in supported_languages():
        if code != default_language():
            alt = overlay_template(decl, path, code)
            if alt is not None:
                templates[code] = alt
    return localize(templates, values, where=where)


def _string_leaves(node: Any, prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    if isinstance(node, dict):
        for key, value in node.items():
            if key in OVERLAY_SKIP_KEYS or key == "translations":
                continue
            out.update(_string_leaves(value, f"{prefix}{key}."))
    elif isinstance(node, str):
        out[prefix.rstrip(".")] = node
    return out


def overlay_problems(decl: dict[str, Any], sections: list[str] | tuple[str, ...]) -> list[str]:
    """覆盖层守卫: ① 译文叶子在原位有同路径字符串; ② 占位集相等; ③ sections(应译范围)里含中日韩字符的原位叶子在每种其余语言都有译文;
    ④ 译文本身不含中日韩字符(英文等)。返回问题清单(空 = 合格)。"""
    problems: list[str] = []
    overlay = decl.get("translations") or {}
    for code in supported_languages():
        if code == default_language():
            continue
        table = overlay.get(code)
        if not isinstance(table, dict):
            problems.append(f"translations.{code} 缺")
            continue
        for path, text in _string_leaves(table).items():
            origin = _get_path(decl, path)
            if not isinstance(origin, str):
                problems.append(f"translations.{code}.{path}: 原位无同路径字符串")
                continue
            if placeholders(origin) != placeholders(text):
                problems.append(f"translations.{code}.{path}: 占位 {sorted(placeholders(origin))} ≠ {sorted(placeholders(text))}")
            if has_cjk(text):
                problems.append(f"translations.{code}.{path}: 译文含中日韩字符")
        for section in sections:
            for path, text in _string_leaves(_get_path(decl, section), f"{section}.").items():
                if has_cjk(text) and not isinstance(_get_path(table, path), str):
                    problems.append(f"translations.{code}.{path}: 缺译文")
    return problems


def block_localize(block: dict[str, Any], path: str, values: dict[str, Any] | None = None, *, where: str) -> str:
    """双语块(结构已核)里 path 那条文案, 各语言各填一份 → Localized(出声点按用户语言 pick; 缺省语言 = str 本身)。"""
    from tools.schema_loader import SchemaLoadError

    templates = {code: _leaf(table, path) for code, table in block.items()}
    missing = sorted(code for code, tpl in templates.items() if not isinstance(tpl, str))
    if missing:
        raise SchemaLoadError(f"{where}.{path} 在 {missing} 缺")
    return localize(templates, values, where=f"{where}.{path}")


def owner_block(name: str) -> dict[str, Any]:
    """config.schema owner_texts.<name>.texts(Owner 面出声点双语块; 结构与占位经 surface_texts 核, fail-closed)。"""
    from tools.schema_loader import SchemaLoadError, code_repo_schema_root, load_schema

    where = f"config.schema.json owner_texts.{name}.texts"
    section = (load_schema("config", code_repo_schema_root()).get("owner_texts") or {}).get(name)
    if not isinstance(section, dict):
        raise SchemaLoadError(f"config.schema.json owner_texts.{name} 未声明(AIPOS-F277)")
    block = section.get("texts")
    surface_texts(block, None, where=where)
    return block


def owner_text(name: str, key: str, /, **values: Any) -> str:
    """owner_texts.<name> 一条文案 → 双语渲染值(各语言各填一份; 出声点 pick 用户语言)。"""
    return block_localize(owner_block(name), key, values, where=f"config.schema.json owner_texts.{name}.texts")


def join(sep: Any, parts: list[Any]) -> str:
    """双语拼接: 各语言各连一份(sep 与各段可为 Localized)→ Localized(全是普通 str = 普通 str)。"""
    if not any(isinstance(p, Localized) for p in [sep, *parts]):
        return str(sep).join(str(p) for p in parts)
    langs = supported_languages()
    return Localized({code: pick(sep, code).join(pick(p, code) for p in parts) for code in langs}, default_language())


def concat(*parts: Any) -> str:
    """双语串接(join 的空分隔特例)。"""
    return join("", list(parts))


def materialize(value: Any, lang: str | None) -> Any:
    """结构化结果(dict / list / Localized)按用户语言落成普通 str(--json 与人读同语言; 键为 Localized 同样落成该语言)。"""
    if isinstance(value, Localized):
        return pick(value, lang)
    if isinstance(value, dict):
        return {materialize(k, lang): materialize(v, lang) for k, v in value.items()}
    if isinstance(value, list):
        return [materialize(v, lang) for v in value]
    if isinstance(value, tuple):
        return tuple(materialize(v, lang) for v in value)
    return value
