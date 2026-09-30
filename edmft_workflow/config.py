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


_MISSING = object()


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


def _lookup(data: Dict[str, Any], dotted: str, default: Any = None) -> Any:
    cur: Any = data
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def _read_toml(path: Path) -> Dict[str, Any]:
    with path.open("rb") as f:
        return tomllib.load(f)


@dataclass(frozen=True)
class WorkflowConfig:
    """Project configuration plus an optional machine/site profile.

    ``data`` is the calculation-local config.toml. ``site`` is deliberately
    separate: it contains software locations, compiler/runtime setup, MPI and
    scheduler semantics.  Existing modules still use the historical
    ``environment.*`` / ``parallel.*`` accessors through the compatibility
    mapping below, so calculation code does not need to know where the machine
    profile lives.
    """

    data: Dict[str, Any]
    source: Path
    site: Dict[str, Any] | None = None
    site_source: Path | None = None

    @property
    def base(self) -> Path:
        return self.source.parent

    def _site_value(self, dotted: str, default: Any = _MISSING) -> Any:
        if not self.site:
            return default

        direct = _lookup(self.site, dotted, _MISSING)
        if direct is not _MISSING:
            return direct

        aliases = {
            "environment.wienroot": "software.wienroot",
            "environment.edmft_root": "software.edmft_root",
            "environment.python": "software.python",
            "environment.python_bin_dir": "software.python_bin_dir",
            "environment.setup_script": "runtime.setup_script",
            "environment.setup_commands": "runtime.setup_commands",
            "environment.intel_root": "runtime.intel_root",  # legacy-compatible optional key
            "environment.intel_arch": "runtime.intel_arch",
            "environment.fftw_lib": "runtime.fftw_lib",
            "environment.ulimit_stack": "runtime.ulimit_stack",
            "environment.ulimit_core": "runtime.ulimit_core",
            "environment.prepend_path": "runtime.prepend_path",
            "environment.prepend_ld_library_path": "runtime.prepend_ld_library_path",
            "parallel.mpi_launcher": "mpi.native.launcher",
            "parallel.mpi_np_flag": "mpi.native.np_flag",
            "parallel.mpi_extra_args": "mpi.native.extra_args",
            "parallel.write_mpi_prefix2": "wien2k.write_mpi_prefix2",
            "parallel.wien_machines_mode": "wien2k.machines_mode",
            "parallel.dmft_wien_machines": "wien2k.dmft_wien_machines",
            "maxent.mpi_launcher": "mpi.maxent.launcher",
            "maxent.mpi_np_flag": "mpi.maxent.np_flag",
            "maxent.mpi_extra_args": "mpi.maxent.extra_args",
        }
        target = aliases.get(dotted)
        if target:
            value = _lookup(self.site, target, _MISSING)
            if value is not _MISSING:
                return value

        if dotted == "environment.python_bin_dir":
            python = _lookup(self.site, "software.python", _MISSING)
            if python is not _MISSING:
                return str(Path(str(python)).expanduser().parent)

        if dotted.startswith("environment_extra."):
            key = dotted.split(".", 1)[1]
            return _lookup(self.site, f"runtime.env.{key}", default)

        if dotted.startswith("scheduler."):
            return _lookup(self.site, dotted, default)

        if dotted.startswith("maxent_runtime."):
            key = dotted.split(".", 1)[1]
            return _lookup(self.site, f"runtime.maxent.{key}", default)

        return default

    def section(self, name: str) -> Dict[str, Any]:
        if self.site:
            if name == "environment_extra":
                return dict(_lookup(self.site, "runtime.env", {}) or {})
            if name == "scheduler":
                return dict(_lookup(self.site, "scheduler", {}) or {})
        return dict(self.data.get(name, {}))

    def get(self, dotted: str, default: Any = None) -> Any:
        # Machine/runtime keys belong to site.toml whenever a site profile is
        # active.  This prevents an old config.toml from silently overriding
        # the selected machine environment.
        machine_key = (
            dotted.startswith("environment.")
            or dotted.startswith("environment_extra.")
            or dotted.startswith("parallel.")
            or dotted.startswith("scheduler.")
            or dotted.startswith("maxent_runtime.")
            or dotted in {"maxent.mpi_launcher", "maxent.mpi_np_flag", "maxent.mpi_extra_args"}
        )
        if self.site and machine_key:
            value = self._site_value(dotted, _MISSING)
            return default if value is _MISSING else value
        return _lookup(self.data, dotted, default)

    def require(self, dotted: str) -> Any:
        val = self.get(dotted, None)
        if val in (None, ""):
            where = "site.toml" if self.site and (
                dotted.startswith("environment.")
                or dotted.startswith("parallel.")
                or dotted.startswith("scheduler.")
            ) else "config.toml"
            raise ConfigError(f"Missing required configuration key: {dotted} ({where})")
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
    def dmft_scratch_dir(self) -> Path:
        raw = self.get("project.dmft_scratch_dir", str(self.dmft_dir / "tmp"))
        return Path(str(raw)).expanduser().resolve()

    @property
    def work_root(self) -> Path:
        return self.dmft_dir


def _resolve_site_path(config_path: Path, site_path: str | Path | None) -> Path | None:
    if site_path is not None:
        candidate = Path(site_path).expanduser().resolve()
        if not candidate.exists():
            raise ConfigError(f"Site configuration file not found: {candidate}")
        return candidate

    env_site = os.environ.get("EDMFT_WORKFLOW_SITE")
    if env_site:
        candidate = Path(env_site).expanduser().resolve()
        if not candidate.exists():
            raise ConfigError(f"EDMFT_WORKFLOW_SITE does not exist: {candidate}")
        return candidate

    candidate = config_path.with_name("site.toml")
    return candidate if candidate.exists() else None


def load_config(path: str | Path, site_path: str | Path | None = None) -> WorkflowConfig:
    path = Path(path).expanduser().resolve()
    if not path.exists():
        raise ConfigError(f"Configuration file not found: {path}")
    data = _read_toml(path)

    local = path.with_name(path.stem + ".local" + path.suffix)
    if local.exists():
        data = _deep_merge(data, _read_toml(local))
    data = _deep_expand(data, path.parent)

    resolved_site = _resolve_site_path(path, site_path)
    site: Dict[str, Any] | None = None
    if resolved_site is not None:
        site = _deep_expand(_read_toml(resolved_site), resolved_site.parent)

    cfg = WorkflowConfig(data=data, source=path, site=site, site_source=resolved_site)
    _validate(cfg)
    return cfg


def _validate(cfg: WorkflowConfig) -> None:
    cfg.require("project.case")
    cfg.require("project.root_dir")
    if cfg.get("maxent.average_last", 1) < 1:
        raise ConfigError("maxent.average_last must be >= 1")
    for sec in ("dos", "band"):
        wmin = float(cfg.get(f"{sec}.wmin", -3))
        wmax = float(cfg.get(f"{sec}.wmax", 1))
        if not wmin < wmax:
            raise ConfigError(f"{sec}.wmin must be < {sec}.wmax")
