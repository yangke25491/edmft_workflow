# edmft_workflow

A reproducible automation layer for **WIEN2k + Kristjan Haule eDMFT** on PBS/Torque clusters.

The project is built around exactly **two manual scientific checkpoints**, both performed in the full WIEN2k DFT directory:

```text
PROJECT/dft : init_lapw      # before the DFT run
PROJECT/dft : init_dmft.py   # after DFT convergence
```

Everything else is script-driven: PBS generation/submission, DFT-to-DMFT snapshot preparation, `params.dat`, initial `sig.inp`, charge-self-consistent DMFT, convergence checks, MaxEnt, real-axis DOS, band spectral function and plotting.

## Directory layout

```text
PROJECT/
├── config.toml
├── dft/
│   └── tmp/
└── dmft/
    ├── DFT_SOURCE -> ../dft   # provenance only
    ├── tmp/
    ├── maxent/
    ├── onreal/
    ├── band/
    └── results/
```

The numerical DMFT state is copied into `dmft/`; it is not symlinked back into the pristine DFT baseline because charge-self-consistent DMFT updates WIEN2k charge/potential files.

## Run directly from git: no pip install required

Keep the code checkout separate from calculations:

```text
/home/kyang/apps/edmft_workflow/      # software
/home/kyang/test/DMFT/MnO/            # calculation
```

Clone/update the repository normally, then invoke the launcher explicitly:

```bash
/home/kyang/miniforge3/envs/edmft/bin/python \
/home/kyang/apps/edmft_workflow/workflow.py \
-c /path/to/PROJECT/config.toml \
<command>
```

No alias, `.bashrc` modification, global workflow variable, `env.sh`, or `pip install -e .` is required during validation.

## Project-local configuration

A portable calculation config should use relative project paths:

```toml
[project]
case = "01"
root_dir = "."
scratch_dir = "dft/tmp"
dmft_scratch_dir = "dmft/tmp"
```

`.` is resolved relative to the directory containing `config.toml`, so the whole calculation directory can be moved without editing an absolute project path.

Compiler/MPI/WIEN2k/eDMFT installation paths remain site configuration, for example:

```toml
[environment]
wienroot = "/home/kyang/apps/edmft/wien2k"
edmft_root = "/home/kyang/apps/edmft/eDMFT/bin"
python = "/home/kyang/miniforge3/envs/edmft/bin/python"
python_bin_dir = "/home/kyang/miniforge3/envs/edmft/bin"
intel_root = "/opt/intel2019/compilers_and_libraries_2019.4.243"
intel_arch = "intel64"
fftw_lib = "/opt/fftw-3.3.10/lib"
ulimit_stack = "unlimited"
ulimit_core = "unlimited"
```

The workflow reproduces the Intel compiler/MPI/MKL runtime setup inside subprocesses and generated PBS jobs.

## Production sequence

Create directories:

```bash
python /path/to/workflow.py -c PROJECT/config.toml init-layout
```

Manual checkpoint 1:

```bash
cd PROJECT/dft
export SCRATCH="$PWD/tmp"
init_lapw
```

Generate/submit the DFT PBS job:

```bash
python /path/to/workflow.py -c PROJECT/config.toml pbs dft
python /path/to/workflow.py -c PROJECT/config.toml submit dft
```

After DFT convergence, manual checkpoint 2 is performed in the **same `dft/` directory**:

```bash
cd PROJECT/dft
init_dmft.py
```

Inspect the generated correlated-space definitions:

```text
case.indmf
case.indmfl
case.indmfi
```

Then create the isolated DMFT snapshot:

```bash
python /path/to/workflow.py -c PROJECT/config.toml prepare-dmft
```

`prepare-dmft`:

1. verifies converged DFT plus `case.indmf/indmfl/indmfi`;
2. runs `dmft_copy.py PROJECT/dft` from inside `PROJECT/dmft`;
3. explicitly copies converged `case.vsp/case.vns` (and spin variants when present), which upstream `dmft_copy.py` omits;
4. copies `case.indmf` for provenance;
5. creates `DFT_SOURCE -> ../dft` as a provenance-only link;
6. generates `params.dat` from config;
7. creates the starting `sig.inp` with Haule's official `szero.py` by default.

Then generate/submit DMFT:

```bash
python /path/to/workflow.py -c PROJECT/config.toml pbs dmft
python /path/to/workflow.py -c PROJECT/config.toml submit dmft
```

## `params.dat` from config

Top-level DMFT controls go in `[dmft_params]`:

```toml
[dmft_params]
solver = "CTQMC"
DCs = "nominal"
max_dmft_iterations = 1
max_lda_iterations = 100
```

Each impurity gets a section such as:

```toml
[impurity0]
U = 8.0
J = 0.8
beta = 100.0
nf0 = 5.0
M = 5000000
```

which is rendered into Haule-style `iparams0 = {...}` with `[value, comment]` entries. The workflow intentionally refuses to invent a non-empty impurity block.

Initial self-energy preparation is controlled by:

```toml
[dmft_prepare]
initial_sigma = "szero"
szero_args = []
```

Extra `szero.py` options can be passed explicitly when desired.

## `dmft_copy.py` convention

The upstream convention is always:

```text
dmft_copy.py SOURCE
```

which copies **from `SOURCE` into the current working directory**. The workflow follows that literally:

```text
cwd = PROJECT/dmft
    dmft_copy.py PROJECT/dft

cwd = PROJECT/dmft/onreal
    dmft_copy.py PROJECT/dmft

cwd = PROJECT/dmft/band
    dmft_copy.py PROJECT/dmft/onreal
```

## Runtime preflight

Before expensive runs:

```bash
python /path/to/workflow.py -c PROJECT/config.toml doctor-env
```

This checks WIEN2k/eDMFT roots, `mpirun`, `mpi4py`, the reported MPI implementation, dynamic linking of `ctqmc/dmft/dmft2`, and Intel MKL runtime availability. Generated PBS jobs run the same preflight before numerical work.

## Foreground vs PBS

**Foreground / fast:** layout creation, `prepare-dmft`, runtime checks, convergence parsing and plotting.

**PBS / numerical:** DFT, charge-self-consistent DMFT, MaxEnt, real-axis DOS and band spectral-function calculations.

Numerical stages use MPI. WIEN2k DFT uses `run_lapw -p` with `.machines`; eDMFT stages use `mpi_prefix.dat` / `mpi_prefix.dat2`; MaxEnt is launched through MPI.

## Post-processing

After DMFT convergence:

```bash
python /path/to/workflow.py -c PROJECT/config.toml check
python /path/to/workflow.py -c PROJECT/config.toml submit post
```

The PBS dependency chain is:

```text
MaxEnt -> DOS/onreal -> band/A(k,w)
```

After the numerical chain finishes:

```bash
python /path/to/workflow.py -c PROJECT/config.toml run plots
```

See `docs/WORKFLOW.md`, `docs/EXECUTION_POLICY.md`, and `docs/NO_INSTALL_LAYOUT.md` for the detailed contracts.

## Current limitations

- Multi-impurity MaxEnt automation still needs generalization beyond one selected impurity index.
- Spin-polarized/SOC filename combinations are only partially tested.
- The NumPy `A(k,w)` plotter assumes the unit-coherence-factor case; if `cohfactorsd.dat` exists it stops rather than silently ignoring it.

## Upstream

This project is an independent workflow layer around WIEN2k and Kristjan Haule's eDMFT project: `https://github.com/ru-ccmt/eDMFT`.
