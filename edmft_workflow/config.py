from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
from typing import Any, Dict

try:
    import tomllib  # py>=3.11
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib


class ConfigError(RuntimeError):
    pass


def _expand(value: Any, base: Path) -> Any:
    if not isinstance(value, str):
        return value
    value = os.path.expandvars(os.path.expanduser(value))
    p = Path(value)
    if value and not p.is_absolute() and ("/" in value or value.startswith(".")):
        return str((base / p).resolve())
    return value


def _deep_expand(obj: Any, base: Path) -> Any:
    if isinstance(obj, dict):
        return {k: _deep_expand(v, base) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_deep_expand(v, base) for v in obj]
    return _expand(obj, base)


def _deep_merge(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(a)
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


@dataclass(frozen=True)
class WorkflowConfig:
    data: Dict[str, Any]
    source: Path

    @property
    def base(self) -> Path:
        return self.source.parent

    def section(self, name: str) -> Dict[str, Any]:
        return dict(self.data.get(name, {}))

    def get(self, dotted: str, default: Any = None) -> Any:
        cur: Any = self.data
        for part in dotted.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    def require(self, dotted: str) -> Any:
        val = self.get(dotted, None)
        if val in (None, ""):
            raise ConfigError(f"Missing required configuration key: {dotted}")
        return val

    @property
    def case(self) -> str:
        return str(self.require("project.case"))

    @property
    def root_dir(self) -> Path:
        return Path(str(self.require("project.root_dir"))).expanduser().resolve()

    @property
    def dft_dir(self) -> Path:
        return self.root_dir / "dft"

    @property
    def dmft_dir(self) -> Path:
        return self.root_dir / "dmft"

    @property
    def scratch_dir(self) -> Path:
        raw = self.get("project.scratch_dir", str(self.dft_dir / "tmp"))
        return Path(str(raw)).expanduser().resolve()

    @property
    def work_root(self) -> Path:
        return self.dmft_dir


def load_config(path: str | Path) -> WorkflowConfig:
    path = Path(path).expanduser().resolve()
    if not path.exists():
        raise ConfigError(f"Configuration file not found: {path}")
    with path.open("rb") as f:
        data = tomllib.load(f)

    local = path.with_name(path.stem + ".local" + path.suffix)
    if local.exists():
        with local.open("rb") as f:
            data = _deep_merge(data, tomllib.load(f))

    data = _deep_expand(data, path.parent)
    cfg = WorkflowConfig(data=data, source=path)
    _validate(cfg)
    return cfg


def _validate(cfg: WorkflowConfig) -> None:
    # Do not require root_dir to exist here: `init-layout` is precisely the
    # command that creates it. Stage-specific runners validate the files/dirs
    # they actually need before launching expensive work.
    cfg.require("project.case")
    cfg.require("project.root_dir")
    if cfg.get("maxent.average_last", 1) < 1:
        raise ConfigError("maxent.average_last must be >= 1")
    for sec in ("dos", "band"):
        wmin = float(cfg.get(f"{sec}.wmin", -3))
        wmax = float(cfg.get(f"{sec}.wmax", 1))
        if not wmin < wmax:
            raise ConfigError(f"{sec}.wmin must be < {sec}.wmax")
