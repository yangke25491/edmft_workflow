from pathlib import Path

from edmft_workflow.checks import parse_info_iterate, convergence_report


def test_parse_info_iterate(tmp_path: Path):
    p = tmp_path / "info.iterate"
    p.write_text(
        """
80   9.   0     6.525884    37.865399    -2467.790935    -2467.799975    -2467.791909     5.054945     5.024774     0.038520    -0.085156
86   9.   6     6.525884    37.865399    -2467.790766    -2467.801475    -2467.791903     5.057925     5.024774     0.038520    -0.085156
164  15.   7     6.531263    37.917886    -2467.784794    -2467.790230    -2467.784821     5.031487     5.030921    -0.085861    -0.213710
""".strip() + "\n"
    )
    rows = parse_info_iterate(p)
    assert len(rows) == 3
    assert rows[-1].outer == 15
    assert rows[-1].inner == 7
    assert abs(rows[-1].dn - 0.000566) < 1e-9


def test_convergence_report(tmp_path: Path):
    p = tmp_path / "info.iterate"
    p.write_text(
        """
100 14. 7  6.531000 37.91 -1 -1 -1 5.0314 5.0308 0 0
101 15. 7  6.531263 37.91 -1 -1 -1 5.031487 5.030921 0 0
""".strip() + "\n"
    )
    report = convergence_report(tmp_path, max_dn=0.005, drift_tol=0.005)
    assert report["pass"]
