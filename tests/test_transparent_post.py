from pathlib import Path

import pytest

from edmft_workflow.config import WorkflowConfig
from edmft_workflow.foreground import render_foreground
from edmft_workflow.maxent import OFFICIAL_MAXENT_PARAMS
from edmft_workflow.pbs import render_pbs
from edmft_workflow.realaxis import _indmfl_flag, _prepare_real_axis_indmfl
from edmft_workflow.utils import WorkflowError, build_helper_env, edmft_helper_command, shell_preamble


def make_cfg(tmp_path: Path) -> WorkflowConfig:
    data = {
        "project": {"case": "dft", "root_dir": str(tmp_path)},
        "environment": {
            "wienroot": "/opt/wien2k",
            "edmft_root": "/opt/edmft",
            "python": "/opt/miniforge3/envs/edmft/bin/python",
            "python_bin_dir": "/opt/miniforge3/envs/edmft/bin",
            "intel_root": "/opt/intel2019",
            "intel_arch": "intel64",
            "fftw_lib": "/opt/fftw/lib",
        },
        "parallel": {"mpi_launcher": "/opt/intel/bin/mpirun", "write_mpi_prefix2": True},
        "foreground": {"dos_np": 6, "band_np": 7},
        "pbs": {"queue": "batch", "nodes": 1, "ppn": 4, "walltime": "01:00:00"},
        "pbs_maxent": {"ppn": 2},
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


def test_prepare_helper_uses_absolute_python_and_minimal_environment(tmp_path):
    cfg = make_cfg(tmp_path)
    cmd = edmft_helper_command(cfg, "saverage.py")
    assert cmd == ["/opt/miniforge3/envs/edmft/bin/python", "/opt/edmft/saverage.py"]

    scratch = tmp_path / "scratch"
    env = build_helper_env(cfg, scratch=scratch)
    assert env == {
        "WIENROOT": "/opt/wien2k",
        "WIEN_DMFT_ROOT": "/opt/edmft",
        "SCRATCH": str(scratch.resolve()),
    }
    assert "PATH" not in env
    assert "LD_LIBRARY_PATH" not in env
    assert "PYTHONPATH" not in env
    assert "OMP_NUM_THREADS" not in env
    assert "MKL_NUM_THREADS" not in env
    assert not any(key.startswith("I_MPI_") for key in env)


def test_full_job_preamble_deduplicates_thread_settings(tmp_path):
    cfg = make_cfg(tmp_path)
    cfg.data["environment_extra"] = {
        "OMP_NUM_THREADS": "2",
        "MKL_NUM_THREADS": "3",
        "I_MPI_HYDRA_BOOTSTRAP": "ssh",
    }
    text = shell_preamble(cfg, tmp_path)
    assert text.count("export OMP_NUM_THREADS=") == 1
    assert text.count("export MKL_NUM_THREADS=") == 1
    assert "export OMP_NUM_THREADS=2" in text
    assert "export MKL_NUM_THREADS=3" in text


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


def test_maxent_pbs_matches_validated_openmpi_launch(tmp_path):
    cfg = make_cfg(tmp_path)
    (cfg.dmft_dir / "maxent").mkdir(parents=True, exist_ok=True)

    text = render_pbs(cfg, "maxent")

    assert "#PBS -l nodes=1:ppn=2" in text
    assert "source \"$INTEL/linux/bin/compilervars.sh\" intel64" in text
    assert "source /opt/miniforge3/etc/profile.d/conda.sh" in text
    assert "conda activate edmft" in text
    assert "linux/mpi/intel64/lib/release" in text
    assert "MPI=/opt/miniforge3/envs/edmft/bin/mpirun" in text
    assert 'echo "$MPI -np $NP" > mpi_prefix.dat' in text
    assert '"$MPI" -np "$NP" "$ENV/bin/python" /opt/edmft/maxent_run.py sig.inpx > sig1.out 2>&1' in text
    assert "--hostfile" not in text
    assert "Sig.average" not in text


def test_pbs_is_reserved_for_heavy_stages(tmp_path):
    cfg = make_cfg(tmp_path)
    with pytest.raises(WorkflowError):
        render_pbs(cfg, "dos")
    with pytest.raises(WorkflowError):
        render_pbs(cfg, "band")


def test_dos_and_band_render_as_foreground_mpi(tmp_path):
    cfg = make_cfg(tmp_path)
    dos = render_foreground(cfg, "dos")
    band = render_foreground(cfg, "band")

    assert "NP=6" in dos
    assert "MPI=/opt/intel/bin/mpirun" in dos
    assert 'echo "$MPI -np $NP" > mpi_prefix.dat' in dos
    assert "cp mpi_prefix.dat mpi_prefix.dat2" in dos
    assert "/opt/wien2k/x_lapw -f dft lapw0" in dos
    assert "/opt/edmft/x_dmft.py lapw1" in dos
    assert "/opt/edmft/x_dmft.py dmft1" in dos

    assert "NP=7" in band
    assert "MPI=/opt/intel/bin/mpirun" in band
    assert "/opt/edmft/x_dmft.py lapw1 --band" in band
    assert "/opt/edmft/x_dmft.py dmftp" in band


def test_maxent_can_override_matching_openmpi_launcher(tmp_path):
    cfg = make_cfg(tmp_path)
    cfg.data["maxent"] = {
        "mpi_launcher": "/custom/openmpi/bin/mpirun",
        "mpi_np_flag": "-np",
    }
    (cfg.dmft_dir / "maxent").mkdir(parents=True, exist_ok=True)
    text = render_pbs(cfg, "maxent")
    assert "MPI=/custom/openmpi/bin/mpirun" in text
    assert '"$MPI" -np "$NP"' in text
