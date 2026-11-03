"""Relay channel declarations and credential loading (single source for paths and key names).

Paths are derived from HOME (no host, project, group or user is written into the code):
- credentials:  <HOME>/.config/lybra/relay/<channel>.env    (Owner-maintained, mode 600)
- probe output: <HOME>/.lybra/relay/probe/<channel>/<date>/  (outside any git repo, dirs 700 / files 600)

The relay is a host-level subsystem (one bot connection per host), not a per-project setting, so it adds no
project.json keys. Both locations can be overridden per run (--config-dir / --probe-root).

Credential values never leave this module except as return values for the channel adapter that sends them to the
channel's own endpoint; error messages name the file and the key, never the value.
"""
from __future__ import annotations

import os
import stat
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class ChannelSpec:
    name: str
    env_file: str
    required_keys: tuple[str, ...]
    optional_keys: tuple[str, ...] = field(default_factory=tuple)


#: Channel registry. A new channel (Feishu, mail, ...) = one entry here + one adapter module in relay/channels/.
CHANNELS: dict[str, ChannelSpec] = {
    "wecom": ChannelSpec(
        name="wecom",
        env_file="wecom.env",
        required_keys=("WECOM_BOT_ID", "WECOM_BOT_SECRET"),
        optional_keys=("WECOM_WS_URL",),
    ),
    "clawbot": ChannelSpec(
        name="clawbot",
        env_file="clawbot.env",
        required_keys=("CLAWBOT_BOT_TOKEN",),
        optional_keys=("CLAWBOT_BASE_URL", "CLAWBOT_CDN_BASE_URL", "CLAWBOT_BOT_ID", "CLAWBOT_USER_ID"),
    ),
}

CONFIG_DIR_PARTS = (".config", "lybra", "relay")
PROBE_ROOT_PARTS = (".lybra", "relay", "probe")


class ConfigError(Exception):
    """Refuse to start. `code` is a stable reason id; `fields` are safe to print (paths, key names, modes)."""

    def __init__(self, code: str, **fields: object) -> None:
        super().__init__(code)
        self.code = code
        self.fields = fields


def home_dir(env: dict[str, str] | None = None) -> Path:
    source = os.environ if env is None else env
    home = source.get("HOME") or ""
    if not home:
        raise ConfigError("no_home")
    return Path(home)


def default_config_dir(env: dict[str, str] | None = None) -> Path:
    return home_dir(env).joinpath(*CONFIG_DIR_PARTS)


def default_probe_root(env: dict[str, str] | None = None) -> Path:
    return home_dir(env).joinpath(*PROBE_ROOT_PARTS)


def channel_spec(name: str) -> ChannelSpec:
    try:
        return CHANNELS[name]
    except KeyError:
        raise ConfigError("unknown_channel", channel=name, known=", ".join(sorted(CHANNELS))) from None


def credential_path(config_dir: Path, spec: ChannelSpec) -> Path:
    return config_dir / spec.env_file


def parse_env_text(text: str, path: Path) -> dict[str, str]:
    """KEY=VALUE lines; blank lines and '#' comments ignored; optional surrounding quotes stripped."""
    values: dict[str, str] = {}
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or not key or not key.replace("_", "").isalnum():
            raise ConfigError("bad_line", path=str(path), line=lineno)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        values[key] = value
    return values


def check_private_file(path: Path) -> None:
    """Regular file, owned by the current user, no group/other permission bits (600 or stricter)."""
    try:
        st = path.lstat()
    except FileNotFoundError:
        raise ConfigError("missing_file", path=str(path)) from None
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise ConfigError("not_regular_file", path=str(path))
    if hasattr(os, "getuid") and st.st_uid != os.getuid():
        raise ConfigError("wrong_owner", path=str(path))
    mode = stat.S_IMODE(st.st_mode)
    if mode & 0o077:
        raise ConfigError("mode_too_open", path=str(path), mode=f"{mode:o}")


def load_credentials(config_dir: Path, spec: ChannelSpec) -> dict[str, str]:
    path = credential_path(config_dir, spec)
    check_private_file(path)
    values = parse_env_text(path.read_text(encoding="utf-8"), path)
    missing = [k for k in spec.required_keys if not values.get(k)]
    if missing:
        raise ConfigError("missing_keys", path=str(path), keys=", ".join(missing))
    allowed = set(spec.required_keys) | set(spec.optional_keys)
    unknown = sorted(k for k in values if k not in allowed)
    if unknown:
        raise ConfigError("unknown_keys", path=str(path), keys=", ".join(unknown),
                          allowed=", ".join(spec.required_keys + spec.optional_keys))
    return {k: v for k, v in values.items() if k in allowed}


def write_private_env(path: Path, values: dict[str, str], *, overwrite: bool) -> None:
    """Write a credential file with mode 600 (parent dir 700). Used by `login` for channels that issue tokens by QR scan."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    if path.exists() and not overwrite:
        raise ConfigError("file_exists", path=str(path))
    tmp = path.with_name(path.name + ".tmp")
    if tmp.exists():
        tmp.unlink()
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        for key, value in values.items():
            fh.write(f"{key}={value}\n")
    os.replace(tmp, path)
    os.chmod(path, 0o600)
