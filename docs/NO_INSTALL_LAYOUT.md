# Run directly from a git clone (no pip install)

The workflow repository and scientific calculations are kept separate. A single
clone can drive many material calculations.

Recommended layout:

```text
/home/kyang/apps/
└── edmft_workflow/              # git clone; software only
    ├── workflow.py
    ├── edmft_workflow/
    └── ...

/home/kyang/test/DMFT/
├── MnO/
│   ├── config.toml              # complete per-calculation configuration
│   ├── dft/
│   │   └── tmp/
│   └── dmft/
│       ├── DFT_SOURCE -> ../dft
│       ├── tmp/
│       ├── maxent/
│       ├── onreal/
│       ├── band/
│       └── results/
└── La3Ni2O7/
    ├── config.toml
    ├── dft/
    └── dmft/
```

No `pip install`, alias, shell export, `.bashrc` modification, or separate
`env.sh` is required for validation. `config.toml` contains the Intel compiler,
Intel MPI, MKL/FFTW, WIEN2k, eDMFT and Python locations. The workflow generates
the runtime shell setup for foreground subprocesses and PBS jobs.

Run the git checkout explicitly:

```bash
/home/kyang/miniforge3/envs/edmft/bin/python \
/home/kyang/apps/edmft_workflow/workflow.py \
-c /home/kyang/test/DMFT/MnO/config.toml \
init-layout
```

Environment preflight:

```bash
/home/kyang/miniforge3/envs/edmft/bin/python \
/home/kyang/apps/edmft_workflow/workflow.py \
-c /home/kyang/test/DMFT/MnO/config.toml \
doctor-env
```

Generate, but do not submit, a PBS script during validation:

```bash
/home/kyang/miniforge3/envs/edmft/bin/python \
/home/kyang/apps/edmft_workflow/workflow.py \
-c /home/kyang/test/DMFT/MnO/config.toml \
pbs dft
```

The launcher inserts the repository root into `sys.path`, so it can be run from
any working directory.

## What belongs where

The git repository contains workflow code and documentation only. Do not put
WIEN2k/eDMFT outputs in the repository.

A project-local config can use:

```toml
[project]
root_dir = "."
scratch_dir = "dft/tmp"
dmft_scratch_dir = "dmft/tmp"
```

so moving/copying the whole calculation directory does not require editing an
absolute project path.

The two intentionally manual scientific initialization steps are both performed
in the full DFT working directory:

```text
root_dir/dft : init_lapw      # before DFT
root_dir/dft : init_dmft.py   # after DFT convergence
```

Then `prepare-dmft` creates the isolated `dmft/` snapshot, generates
`params.dat` and the starting `sig.inp`, and `run_dmft.py` executes only in
`dmft/`.

`DFT_SOURCE -> ../dft` is a provenance link only. Mutable WIEN2k state used by
charge-self-consistent DMFT is copied locally into `dmft/`; it is not symlinked
back to the pristine DFT baseline.

## Updating the workflow

Because it is not installed into site-packages:

```bash
cd /home/kyang/apps/edmft_workflow
git pull
```

The next direct invocation of `workflow.py` uses the updated source immediately.
