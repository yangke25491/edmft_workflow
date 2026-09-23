from pathlib import Path
import numpy as np

from edmft_workflow.plotting import akw_from_eigvals, read_eigvals
from edmft_workflow.checks import count_eigvals_blocks


def test_akw_lorentzian_like():
    omega = np.array([-1.0, 0.0, 1.0])
    eig = np.empty((1, 3, 1), complex)
    eig[0, :, 0] = 0.0 - 0.1j
    a = akw_from_eigvals(omega, eig, mu=0.0)
    assert a.shape == (3, 1)
    assert a[1, 0] > a[0, 0]
    assert a[1, 0] > a[2, 0]


def test_read_eigvals(tmp_path: Path):
    p = tmp_path / "eigvals.dat"
    p.write_text(
        "# 1 1 2 5 2\n"
        "-1.0 0.0 -0.1 1.0 -0.2\n"
        " 1.0 0.1 -0.1 1.1 -0.2\n"
        "# 2 1 2 5 2\n"
        "-1.0 0.2 -0.1 1.2 -0.2\n"
        " 1.0 0.3 -0.1 1.3 -0.2\n"
    )
    om, eig = read_eigvals(p)
    assert eig.shape == (2, 2, 2)
    assert np.allclose(om, [-1, 1])
    assert count_eigvals_blocks(p) == 2
