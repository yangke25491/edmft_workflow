from pathlib import Path

import edmft_workflow.config as config_module
from edmft_workflow.config import load_config
from edmft_workflow.parallel import allocated_ranks
from edmft_workflow.pbs import render_pbs


def _write_project(tmp_path: Path) -> Path:
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        """
[project]
case = "01"
root_dir = "."

# Legacy machine keys should not override an active site profile.
[environment]
wienroot = "/legacy/wien"
edmft_root = "/legacy/edmft"
python = "/legacy/python"

[parallel]
mpi_launcher = "/legacy/mpirun"

[pbs]
nodes = 1
ppn = 12
walltime = "01:00:00"
queue = "batch"

[pbs_dft]
job_name = "DFT"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return cfg


def _write_site(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    site = directory / "site.toml"
    site.write_text(
        """
[software]
wienroot = "/site/wien"
edmft_root = "/site/edmft"
python = "/site/env/bin/python"

[runtime]
setup_commands = []
prepend_path = []
prepend_ld_library_path = []

[mpi.native]
launcher = "/site/native/mpirun"
np_flag = "-np"

[mpi.maxent]
launcher = "/site/maxent/mpirun"
np_flag = "-np"

[wien2k]
write_mpi_prefix2 = true
machines_mode = "single_node_compact"

[scheduler]
jobid_env = "JOB_ID"
nodefile_env = "NODE_FILE"
slot_count_source = "command"
slot_count_command = "echo 12"
resource_template = "#PBS -l select={nodes}:ncpus={ppn}:mpiprocs={ppn}"
submit_command = "qsub"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return site


def test_site_profile_owns_machine_settings(tmp_path):
    cfg_path = _write_project(tmp_path)
    site_path = _write_site(tmp_path / "explicit-site")
    cfg = load_config(cfg_path, site_path=site_path)

    assert cfg.get("environment.wienroot") == "/site/wien"
    assert cfg.get("environment.edmft_root") == "/site/edmft"
    assert cfg.get("environment.python_bin_dir") == "/site/env/bin"
    assert cfg.get("parallel.mpi_launcher") == "/site/native/mpirun"
    assert cfg.get("maxent.mpi_launcher") == "/site/maxent/mpirun"
    assert cfg.get("pbs.ppn") == 12
    assert cfg.site_source == site_path.resolve()


def test_default_site_is_loaded_from_workflow_root(tmp_path, monkeypatch):
    project_dir = tmp_path / "calculation"
    project_dir.mkdir()
    cfg_path = _write_project(project_dir)

    workflow_root = tmp_path / "workflow"
    site_path = _write_site(workflow_root)
    monkeypatch.setattr(config_module, "WORKFLOW_ROOT", workflow_root)

    cfg = load_config(cfg_path)
    assert cfg.site_source == site_path.resolve()
    assert cfg.get("environment.wienroot") == "/site/wien"
    assert cfg.get("parallel.mpi_launcher") == "/site/native/mpirun"


def test_explicit_site_overrides_workflow_root_default(tmp_path, monkeypatch):
    project_dir = tmp_path / "calculation"
    project_dir.mkdir()
    cfg_path = _write_project(project_dir)

    workflow_root = tmp_path / "workflow"
    default_site = _write_site(workflow_root)
    explicit_dir = tmp_path / "other-site"
    explicit_site = _write_site(explicit_dir)
    explicit_site.write_text(
        explicit_site.read_text(encoding="utf-8").replace("/site/wien", "/explicit/wien"),
        encoding="utf-8",
    )
    monkeypatch.setattr(config_module, "WORKFLOW_ROOT", workflow_root)

    cfg = load_config(cfg_path, site_path=explicit_site)
    assert cfg.site_source == explicit_site.resolve()
    assert cfg.site_source != default_site.resolve()
    assert cfg.get("environment.wienroot") == "/explicit/wien"


def test_command_slot_count_is_scheduler_data(tmp_path):
    cfg = load_config(_write_project(tmp_path), site_path=_write_site(tmp_path / "site-a"))
    assert allocated_ranks(cfg) == 12


def test_render_pbs_freezes_site_resource_and_slot_policy(tmp_path):
    cfg = load_config(_write_project(tmp_path), site_path=_write_site(tmp_path / "site-b"))
    text = render_pbs(cfg, "dft")

    assert "#PBS -l select=1:ncpus=12:mpiprocs=12" in text
    assert 'NP=$(echo 12)' in text
    assert "/site/native/mpirun" not in text  # DFT uses WIEN2k .machines, not MPI prefix directly.
    assert "config.toml or site.toml" in text
