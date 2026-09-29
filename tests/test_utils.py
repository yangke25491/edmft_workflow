from pathlib import Path

from edmft_workflow.utils import patch_indmfl, count_klist_points


def test_patch_indmfl(tmp_path: Path):
    p = tmp_path / "01.indmfl"
    p.write_text(
        "5 15 1 5\n"
        "1 0.025 0.025 200 -3.000000 1.000000\n"
        "1\n"
    )
    patch_indmfl(p, matsubara=0, nomega=300, wmin=-6, wmax=6)
    second = p.read_text().splitlines()[1].split()
    assert second[0] == "0"
    assert second[3] == "300"
    assert float(second[4]) == -6
    assert float(second[5]) == 6


def test_count_klist(tmp_path: Path):
    p = tmp_path / "01.klist_band"
    p.write_text("GAMMA 0 0 0 40 2.0\n 1 0 0 40 2.0\nX 2 0 0 40 2.0\nEND\n")
    assert count_klist_points(p) == 3
