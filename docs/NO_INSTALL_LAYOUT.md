# Run directly from a git clone (no pip install)

The workflow repository and the scientific calculations should be kept separate.
A single clone can drive many material calculations.

Recommended layout:

```text
/home/kyang/apps/
└── edmft_workflow/              # git clone; software only
    ├── workflow.py
    ├── edmft_workflow/
    └── ...

/home/kyang/test/DMFT/
├── MnO/
│   ├── config.toml              # configuration for this calculation
│   ├── dft/
│   └── dmft/
│       ├── maxent/
│       ├── onreal/
│       └── band/
└── La3Ni2O7/
    ├── config.toml
    ├── dft/
    └── dmft/

/home/kyang/.config/edmft_workflow/
└── env.sh                       # Intel/MPI/MKL/WIEN2k/eDMFT environment
```

No `pip install -e .` is required. Run the repository launcher explicitly with
the Python interpreter from the eDMFT environment:

```bash
PY=/home/kyang/miniforge3/envs/edmft/bin/python
WF=/home/kyang/apps/edmft_workflow/workflow.py
CFG=/home/kyang/test/DMFT/MnO/config.toml

$PY $WF -c $CFG init-layout
$PY $WF -c $CFG doctor-env
$PY $WF -c $CFG submit dft
```

The launcher inserts the repository root into `sys.path`, therefore the command
works regardless of the current working directory.

For convenience in an interactive shell, aliases may be defined without
installing the package:

```bash
export EDMFT_WF=/home/kyang/apps/edmft_workflow/workflow.py
export EDMFT_PY=/home/kyang/miniforge3/envs/edmft/bin/python
alias ewf='$EDMFT_PY $EDMFT_WF'
```

Then, for MnO:

```bash
cd /home/kyang/test/DMFT/MnO
ewf -c config.toml doctor-env
ewf -c config.toml check
```

## What belongs where

The git repository contains only workflow code and documentation. Do not put
WIEN2k/DMFT outputs in the repository.

Each material/project has its own calculation root. `project.root_dir` points to
that root. The workflow uses a fixed layout:

```text
root_dir/dft
root_dir/dmft
```

and creates post-processing directories under `root_dir/dmft`:

```text
root_dir/dmft/maxent
root_dir/dmft/onreal
root_dir/dmft/band
root_dir/dmft/results
```

A per-material `config.toml` should normally live directly in that material's
calculation root, next to `dft/` and `dmft/`.

## Updating the workflow

Because it is not installed into site-packages, updating is simply:

```bash
cd /home/kyang/apps/edmft_workflow
git pull
```

The next invocation of `workflow.py` immediately uses the updated source tree.
