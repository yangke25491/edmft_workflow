# edmft_workflow

A reproducible automation layer for **WIEN2k + Kristjan Haule eDMFT** on PBS/Torque clusters.

The project is intentionally built around only **two manual scientific checkpoints**:

```text
PROJECT/dft  : init_lapw
PROJECT/dmft : init_dmft.py
```

Everything else is script-driven: DFT submission, DMFT submission, convergence checks, MaxEnt, real-axis DOS, band spectral function, validation and plotting.

## Fixed directory layout

```text
PROJECT/
├── dft/
│   └── tmp/                 # ordinary WIEN2k SCRATCH
└── dmft/
    ├── maxent/
    ├── onreal/
    ├── band/
    └── results/
```

The fixed layout is deliberate. It prevents the many WIEN2k/eDMFT files with the same names (`case.vector`, `case.energy`, `sig.inp`, `case.indmfl`, ...) from different stages being mixed together.

## `dmft_copy.py` convention

`dmft_copy.py SOURCE` copies **from `SOURCE` into the current working directory**. The workflow always follows that rule:

```text
cwd = PROJECT/dmft
    dmft_copy.py PROJECT/dft

cwd = PROJECT/dmft/onreal
    dmft_copy.py PROJECT/dmft

cwd = PROJECT/dmft/band
    dmft_copy.py PROJECT/dmft/onreal
```

The real-axis and band stages also explicitly copy the converged WIEN2k potential files (`case.vsp`, `case.vns`, and spin variants when present) from `dmft/`, because `x_dmft.py lapw1` requires them locally.

## Installation

```bash
git clone git@github.com:yangke25491/edmft_workflow.git
cd edmft_workflow
python -m pip install -e .
```

Copy the configuration template:

```bash
cp config.example.toml config.toml
vim config.toml
```

For the user's cluster, `examples/mno/env.sh.example` contains the Intel 2019 / Intel MPI / MKL / FFTW / eDMFT environment template. Copy it outside the repository, for example:

```bash
mkdir -p ~/.config/edmft_workflow
cp examples/mno/env.sh.example ~/.config/edmft_workflow/env.sh
```

and configure:

```toml
[environment]
setup_script = "/home/kyang/.config/edmft_workflow/env.sh"
```

Every numerical foreground command and every generated PBS job sources this same environment file.

## Runtime preflight

Before expensive calculations:

```bash
edmft-workflow -c config.toml doctor-env
```

This checks the configured WIEN2k/eDMFT roots, `mpirun`, `mpi4py`, the MPI implementation reported by Python, dynamic linking of `ctqmc/dmft/dmft2`, and direct availability of:

```text
libmkl_intel_lp64.so
libmkl_intel_thread.so
libmkl_core.so
```

Generated PBS jobs run the same preflight automatically before starting numerical work.

## Production workflow

Create the layout:

```bash
edmft-workflow -c config.toml init-layout
```

Manual checkpoint 1:

```bash
cd PROJECT/dft
export SCRATCH="$PWD/tmp"
init_lapw
```

Submit DFT:

```bash
edmft-workflow -c config.toml submit dft
```

After DFT converges, prepare the DMFT working directory:

```bash
edmft-workflow -c config.toml prepare-dmft
```

Manual checkpoint 2:

```bash
cd PROJECT/dmft
init_dmft.py
```

Inspect/edit the correlated-space and impurity inputs, then submit the charge-self-consistent DFT+DMFT calculation:

```bash
edmft-workflow -c config.toml submit dmft
```

Check convergence:

```bash
edmft-workflow -c config.toml check
```

Submit the complete numerical post-processing chain:

```bash
edmft-workflow -c config.toml submit post
```

which creates PBS dependencies:

```text
MaxEnt -> DOS/onreal -> band/A(k,w)
```

After the band job finishes, generate all figures in the foreground:

```bash
edmft-workflow -c config.toml run plots
```

## Foreground vs PBS

**Foreground / fast:** directory creation, `dmft_copy.py` preparation, runtime checks, convergence parsing and plotting.

**PBS / numerical:** DFT, charge-self-consistent DMFT, MaxEnt, real-axis DOS and band spectral-function calculations.

Numerical stages use MPI. WIEN2k DFT uses `run_lapw -p` with `.machines`; eDMFT stages use `mpi_prefix.dat` / `mpi_prefix.dat2`; MaxEnt is launched explicitly through `mpirun`.

See [`docs/EXECUTION_POLICY.md`](docs/EXECUTION_POLICY.md) for the exact policy and [`docs/WORKFLOW.md`](docs/WORKFLOW.md) for the directory/file contract.

## Parallel configuration

```toml
[parallel]
mpi_launcher = "mpirun"
mpi_np_flag = "-np"
write_mpi_prefix2 = true
foreground_ranks = 4

dft_ranks = "allocation"
dmft_ranks = "allocation"
maxent_ranks = "allocation"
dos_ranks = "allocation"
band_ranks = "allocation"
```

`allocation` means the stage uses the PBS allocation. A fixed integer can be used to cap a stage. MaxEnt additionally limits useful MPI ranks by the number of active bath/self-energy channels.

## Post-processing outputs

```text
PROJECT/dmft/
├── maxent/
│   ├── Sig.average
│   ├── maxent_params.dat
│   └── Sig.out
├── onreal/
│   ├── case.cdos
│   ├── case.gc1
│   ├── case.dlt1
│   └── case.Eimp1
├── band/
│   ├── case.klist_band
│   ├── case.vector
│   ├── case.energy
│   └── eigvals.dat
└── results/
    ├── sigma_matsubara.png/.pdf
    ├── sigma_realaxis.png/.pdf
    ├── dos_total.png/.pdf
    ├── spectral_local.png/.pdf
    ├── hybridization.png/.pdf
    └── Akw.png/.pdf
```

The workflow validates non-empty vector/energy files, MaxEnt grids, `DMFT1 END`, k-path counts, `numkpt`, `tot-k`, and `eigvals.dat` block counts so that a command finishing successfully is not automatically mistaken for a physically usable result.

## Current limitations

- Multi-impurity MaxEnt automation still needs to be generalized beyond one selected impurity index.
- Spin-polarized/SOC filename variants are partially supported for copied potential files but are not yet exhaustively tested.
- The NumPy `A(k,w)` plotter assumes the ordinary unit-coherence-factor case; if `cohfactorsd.dat` is present it stops instead of silently ignoring it.

## Upstream

This project is an independent workflow layer around:

- https://github.com/ru-ccmt/eDMFT
- WIEN2k
- Haule eDMFT Python drivers and executables
