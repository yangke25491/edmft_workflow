# Run directly from a git clone (no pip install)

The workflow repository, machine profile, and scientific calculations are separate concerns. A single clone can drive many material calculations without installation into site-packages.

Recommended layout:

```text
~/apps/
└── edmft_workflow/              # git clone; workflow code only
    ├── workflow.py
    ├── site.example.toml
    └── edmft_workflow/

~/sites/
└── edmft-site.toml              # private machine profile; configure once

~/calculations/
├── MnO/
│   ├── config.toml              # physics/resources/commands for this calculation
│   ├── dft/
│   └── dmft/
└── La3Ni2O7/
    ├── config.toml
    ├── dft/
    └── dmft/
```

No `pip install`, alias, `.bashrc` modification, or sourced `env.sh` is required. Machine-specific software paths, compiler/MKL/FFTW setup, MPI launchers, and scheduler semantics live in `site.toml`; calculation-specific physics and requested resources remain in `config.toml`.

Select a site profile in any of three ways:

```bash
# explicit
python ~/apps/edmft_workflow/workflow.py \
  --site ~/sites/edmft-site.toml \
  -c ~/calculations/MnO/config.toml status

# environment pointer
export EDMFT_WORKFLOW_SITE=~/sites/edmft-site.toml
python ~/apps/edmft_workflow/workflow.py -c ~/calculations/MnO/config.toml status

# or put a private site.toml next to config.toml
cp ~/apps/edmft_workflow/site.example.toml ~/calculations/MnO/site.toml
```

`site.toml` is gitignored by this repository.

## What belongs where

`config.toml` contains things that change with the calculation or are commonly tuned:

```text
case / project paths
DMFT, MaxEnt, DOS, band and plotting parameters
foreground MPI rank counts
requested nodes / ppn / memory / walltime / queue
run_lapw / run_dmft / MaxEnt command overrides
```

`site.toml` contains stable machine facts:

```text
WIEN2k / eDMFT / Python locations
compiler and numerical-library setup
PATH / LD_LIBRARY_PATH additions
native MPI and MaxEnt MPI launchers
scheduler job-id/nodefile variables
how allocated CPU slots are determined
scheduler resource-directive syntax
```

The workflow code does not need to know whether a particular profile describes one PBS-family implementation or another; the profile describes behavior.

## Project-relative layout

A project-local config should normally use:

```toml
[project]
root_dir = "."
scratch_dir = "dft/tmp"
dmft_scratch_dir = "dmft/tmp"
```

so moving or copying the entire calculation directory does not require changing absolute project paths.

The two intentionally manual scientific initialization steps are performed in the DFT working directory:

```text
root_dir/dft : init_lapw      # before DFT
root_dir/dft : init_dmft.py   # after DFT convergence
```

Then `prepare-dmft` creates the isolated `dmft/` snapshot. Mutable numerical files are copied into the DMFT working tree rather than mutating the converged DFT baseline.

## Standalone numerical execution

The selected `config.toml` and `site.toml` are evaluated while generating a PBS script. The resulting `run_*.pbs` contains the resolved setup and native commands directly. The compute node does not import the workflow or reread either TOML file.

DOS and band use the same site-defined native runtime in disposable foreground child shells. They do not modify the parent login shell.

## Updating the workflow

Because normal use is direct from the checkout:

```bash
cd ~/apps/edmft_workflow
git pull
```

The next invocation of `workflow.py` uses the updated source immediately.
