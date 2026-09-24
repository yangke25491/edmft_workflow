from pathlib import Path

import numpy as np

from edmft_workflow.analysis import _fit_z
from edmft_workflow.provenance import (
    refresh_stage_manifest,
    verify_stage_manifest,
    write_stage_manifest,
)


def test_low_frequency_z_diagnostic_linear_case():
    omega = np.array([0.1, 0.2, 0.3, 0.4])
    imag_sigma = -omega
    slope, intercept, r2, z, mass = _fit_z(omega, imag_sigma, 4)

    assert np.isclose(slope, -1.0)
    assert np.isclose(intercept, 0.0, atol=1e-12)
    assert np.isclose(r2, 1.0)
    assert np.isclose(z, 0.5)
    assert np.isclose(mass, 2.0)


def test_manifest_allows_review_edits_then_freezes_at_pbs_generation(tmp_path: Path):
    stage = tmp_path / "maxent"
    stage.mkdir()
    source = tmp_path / "sig.inp.10.1"
    source.write_text("# s_oo=[]\n# Edc=[]\n0.1 0.0 -0.1\n", encoding="utf-8")
    params = stage / "maxent_params.dat"
    params.write_text("params={'Ntau':400}\n", encoding="utf-8")

    write_stage_manifest(stage, "maxent", sources={"sigma": source}, prepared=[params])
    ok, _ = verify_stage_manifest(stage)
    assert ok

    # User edits between prepare-* and PBS freeze are intentional and doctor-safe.
    params.write_text("params={'Ntau':500}\n", encoding="utf-8")
    ok, detail = verify_stage_manifest(stage)
    assert ok
    assert "allowed before PBS freeze" in detail

    # A strict check can still see that the prepare-time snapshot changed.
    ok, detail = verify_stage_manifest(stage, strict=True)
    assert not ok
    assert "maxent_params.dat" in detail

    # PBS generation refreshes the manifest and freezes the inspected state.
    pbs = stage / "run_maxent.pbs"
    pbs.write_text("#!/bin/bash\n", encoding="utf-8")
    refresh_stage_manifest(stage, extra_prepared=[pbs])
    ok, _ = verify_stage_manifest(stage)
    assert ok

    # Changes after freeze must be detected.
    params.write_text("params={'Ntau':600}\n", encoding="utf-8")
    ok, detail = verify_stage_manifest(stage)
    assert not ok
    assert "maxent_params.dat" in detail
