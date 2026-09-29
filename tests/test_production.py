from pathlib import Path

import pytest

from edmft_workflow.config import WorkflowConfig
from edmft_workflow.production import _validate_dmft_snapshot, render_params_dat
from edmft_workflow.utils import WorkflowError


def make_cfg(tmp_path: Path) -> WorkflowConfig:
    data = {
        "project": {"case": "01", "root_dir": str(tmp_path)},
        "dmft_params": {
            "solver": "CTQMC",
            "DCs": "nominal",
            "max_dmft_iterations": 1,
            "max_lda_iterations": 100,
        },
        "impurity0": {
            "exe": "ctqmc",
            "U": 8.0,
            "J": 0.8,
            "beta": 100.0,
            "nf0": 5.0,
            "M": 5000000,
        },
    }
    return WorkflowConfig(data=data, source=tmp_path / "config.toml")


def write_nonempty(path: Path, text: str = "x\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def populate_ready_snapshot(cfg: WorkflowConfig) -> None:
    case = cfg.case
    for suffix in (
        "struct",
        "in0",
        "in1",
        "in2",
        "inm",
        "klist",
        "clmsum",
        "indmf",
        "indmfl",
        "indmfi",
        "vsp",
        "vns",
    ):
        write_nonempty(cfg.dmft_dir / f"{case}.{suffix}")
    write_nonempty(cfg.dmft_dir / "params.dat")
    write_nonempty(cfg.dmft_dir / "sig.inp")
    cfg.dmft_scratch_dir.mkdir(parents=True, exist_ok=True)


def test_render_params_dat_is_valid_python(tmp_path):
    cfg = make_cfg(tmp_path)
    text = render_params_dat(cfg)
    namespace = {}
    exec(compile(text, "params.dat", "exec"), {}, namespace)

    assert namespace["solver"] == "CTQMC"
    assert namespace["DCs"] == "nominal"
    assert namespace["max_dmft_iterations"] == 1
    assert namespace["iparams0"]["U"][0] == 8.0
    assert namespace["iparams0"]["beta"][0] == 100.0
    assert namespace["iparams0"]["M"][0] == 5000000


def test_fixed_layout_is_derived_from_root(tmp_path):
    cfg = make_cfg(tmp_path)
    assert cfg.dft_dir == tmp_path / "dft"
    assert cfg.dmft_dir == tmp_path / "dmft"
    assert cfg.scratch_dir == tmp_path / "dft" / "tmp"
    assert cfg.dmft_scratch_dir == tmp_path / "dmft" / "tmp"


def test_ready_snapshot_passes_final_gate_without_dft_source_symlink(tmp_path):
    cfg = make_cfg(tmp_path)
    populate_ready_snapshot(cfg)

    _validate_dmft_snapshot(cfg)

    assert not (cfg.dmft_dir / "DFT_SOURCE").exists()


def test_ready_snapshot_rejects_incomplete_potential_state(tmp_path):
    cfg = make_cfg(tmp_path)
    populate_ready_snapshot(cfg)
    (cfg.dmft_dir / "01.vns").unlink()

    with pytest.raises(WorkflowError, match="Incomplete WIEN2k potential state"):
        _validate_dmft_snapshot(cfg)
