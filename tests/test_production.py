from pathlib import Path

from edmft_workflow.config import WorkflowConfig
from edmft_workflow.production import render_params_dat


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
