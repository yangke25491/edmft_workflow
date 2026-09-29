from pathlib import Path

from edmft_workflow.fermi import (
    RY2EV,
    copy_fermi_snapshot,
    dft_fermi_from_scf2,
    fermi_warnings,
    final_info_iterate_mu,
)


class DummyCfg:
    def __init__(self, root: Path):
        self.root = root
        self.case = "01"
        self.dft_dir = root / "dft"
        self.dmft_dir = root / "dmft"

    def get(self, key, default=None):
        if key == "fermi.tolerance_eV":
            return 1e-6
        return default


def test_dft_fermi_and_iterate_mu(tmp_path: Path):
    scf2 = tmp_path / "01.scf2"
    scf2.write_text(":FER  : F E R M I - ENERGY(TETRAH.M.)=   0.479646\n", encoding="utf-8")
    assert abs(dft_fermi_from_scf2(scf2) - 0.479646 * RY2EV) < 1e-12

    info = tmp_path / "info.iterate"
    info.write_text(
        "100 14. 7  6.531000 37.91 -1 -1 -1 5.0314 5.0308 0 0\n"
        "101 15. 7  6.531263 37.91 -1 -1 -1 5.0314 5.0308 0 0\n",
        encoding="utf-8",
    )
    assert final_info_iterate_mu(info) == 6.531263


def test_snapshot_and_stage_mismatch_is_warning_only(tmp_path: Path):
    cfg = DummyCfg(tmp_path)
    cfg.dft_dir.mkdir()
    cfg.dmft_dir.mkdir()
    maxent = cfg.dmft_dir / "maxent"
    band = cfg.dmft_dir / "band"
    maxent.mkdir()
    band.mkdir()

    (cfg.dmft_dir / "EF.dat").write_text("6.500000\n", encoding="utf-8")
    assert copy_fermi_snapshot(cfg.dmft_dir / "EF.dat", maxent / "fermi_level.snapshot") == 6.5

    (band / "EF.dat").write_text("6.400000\n", encoding="utf-8")
    (band / "fermi_level.snapshot").write_text("6.500000\n", encoding="utf-8")
    (band / "maxent_fermi_level.snapshot").write_text("6.500000\n", encoding="utf-8")

    warnings = fermi_warnings(cfg, "band")
    assert warnings
    assert any("mismatch" in item for item in warnings)
    # Diagnostics are strings only: callers can print WARN without blocking execution.
    assert all(isinstance(item, str) for item in warnings)
