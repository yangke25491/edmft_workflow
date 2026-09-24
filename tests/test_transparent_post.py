from pathlib import Path

from edmft_workflow.config import WorkflowConfig
from edmft_workflow.maxent import OFFICIAL_MAXENT_PARAMS
from edmft_workflow.pbs import render_pbs
from edmft_workflow.realaxis import _indmfl_flag, _prepare_real_axis_indmfl


def make_cfg(tmp_path: Path) -> WorkflowConfig:
    data = {
        "project": {"case": "dft", "root_dir": str(tmp_path)},
        "environment": {
            "wienroot": "/opt/wien2k",
            "edmft_root": "/opt/edmft",
            "python": "/opt/python/bin/python",
            "python_bin_dir": "/opt/python/bin",
        },
        "parallel": {"mpi_launcher": "/opt/intel/bin/mpirun", "write_mpi_prefix2": True},
        "pbs": {"queue": "batch", "nodes": 1, "ppn": 4, "walltime": "01:00:00"},
    }
    return WorkflowConfig(data=data, source=tmp_path / "config.toml")


def test_upstream_maxent_template_is_explicit_python():
    namespace = {}
    exec(compile(OFFICIAL_MAXENT_PARAMS, "maxent_params.dat", "exec"), {}, namespace)
    params = namespace["params"]
    assert params["Ntau"] == 400
    assert params["Nw"] == 450
    assert params["Nitt"] == 500
    assert params["SymCum"] is True


def test_real_axis_conversion_preserves_matsubara_backup(tmp_path):
    indmfl = tmp_path / "dft.indmfl"
    indmfl.write_text(
        "header\n"
        "1 0.025 0.025 100 -10.0 10.0\n"
        "tail\n",
        encoding="utf-8",
    )

    backup = _prepare_real_axis_indmfl(indmfl, nomega=300, wmin=-3.0, wmax=1.0)

    assert _indmfl_flag(backup) == 1
    assert _indmfl_flag(indmfl) == 0
    fields = indmfl.read_text().splitlines()[1].split()
    assert fields[3] == "300"
    assert float(fields[4]) == -3.0
    assert float(fields[5]) == 1.0
    diff = (tmp_path / "indmfl.diff").read_text()
    assert "dft.indmfl.matsubara" in diff
    assert "dft.indmfl" in diff


def test_post_pbs_is_standalone_and_uses_native_commands(tmp_path):
    cfg = make_cfg(tmp_path)
    for stage_dir in [
        cfg.dmft_dir / "maxent",
        cfg.dmft_dir / "onreal",
        cfg.dmft_dir / "band",
    ]:
        stage_dir.mkdir(parents=True, exist_ok=True)

    maxent = render_pbs(cfg, "maxent")
    dos = render_pbs(cfg, "dos")
    band = render_pbs(cfg, "band")

    for script in (maxent, dos, band):
        assert "-m edmft_workflow" not in script
        assert "config.toml" not in script

    assert "/opt/edmft/maxent_run.py" in maxent
    assert "sig.inpx" in maxent
    assert "/opt/wien2k/x_lapw" in dos
    assert "/opt/edmft/x_dmft.py lapw1" in dos
    assert "/opt/edmft/x_dmft.py dmft1" in dos
    assert "/opt/edmft/x_dmft.py lapw1 --band" in band
    assert "/opt/edmft/x_dmft.py dmftp" in band
