# Workflow and directory contract

A calculation has one project-local `config.toml`, optional native scientific inputs, and isolated stage directories. Machine/runtime details are supplied separately by `site.toml`.

```text
PROJECT/
├── config.toml
├── site.toml              # optional project-local profile; gitignored
├── inputs/
│   ├── params.dat
│   ├── maxent_params.dat
│   └── CASE.klist_band
├── dft/
└── dmft/
    ├── maxent/
    ├── onreal/
    ├── band/
    └── analysis/
```

The two scientific initializers remain manual:

1. `init_lapw` in `PROJECT/dft` before DFT.
2. `init_dmft.py` in `PROJECT/dft` after DFT convergence.

The workflow never calls `qsub` and never modifies persistent shell configuration.

## Configuration contract

```text
config.toml
  scientific parameters
  requested cores/memory/walltime/queue
  foreground rank counts
  frequently tuned command overrides

site.toml
  software installation paths
  compiler / MKL / FFTW runtime setup
  native MPI launcher
  MaxEnt/mpi4py MPI launcher
  scheduler environment variables
  scheduler slot-count and resource-request semantics
```

When a site profile is active, machine settings come from it even if an old `config.toml` still contains legacy `[environment]` or `[parallel]` sections.

## Execution classes

```text
PBS heavy jobs
  dft
  dmft
  maxent

Foreground MPI
  dos
  band

Lightweight foreground
  prepare-*
  doctor / check / status
  analyze / plots
```

Preparation helpers use explicit software paths and a minimal child-only environment. Numerical jobs/runs create their full runtime only inside the generated PBS script or disposable foreground child shell.

## DFT

```bash
python workflow.py -c config.toml init-layout
cd dft
init_lapw
cd ..

python workflow.py -c config.toml pbs dft
cat dft/run_dft.pbs
qsub dft/run_dft.pbs
```

The requested CPU count comes from `[pbs_dft]` in `config.toml`; how that request is rendered and how the scheduler allocation is interpreted come from `site.toml`.

After DFT convergence:

```bash
cd dft
init_dmft.py
cd ..
```

## DMFT

```bash
python workflow.py -c config.toml prepare-dmft
python workflow.py -c config.toml doctor dmft
python workflow.py -c config.toml pbs dmft
cat dmft/run_dmft.pbs
qsub dmft/run_dmft.pbs
```

The DMFT PBS creates `mpi_prefix.dat` using the native MPI launcher and slot-count behavior defined by the active site profile. No scheduler product name is hard-coded into this stage.

## MaxEnt

```bash
python workflow.py -c config.toml prepare-maxent
python workflow.py -c config.toml doctor maxent
python workflow.py -c config.toml pbs maxent
cat dmft/maxent/run_maxent.pbs
qsub dmft/maxent/run_maxent.pbs
```

Preparation performs:

```text
last N sig.inp.*.<impurity>
        ↓
saverage.py
        ↓
sig.inpx
        +
maxent_params.dat
```

MaxEnt uses the independent launcher from `[mpi.maxent]` in `site.toml`, so its `mpi4py` stack does not have to be the same MPI implementation as native WIEN2k/eDMFT. The workflow does not encode a particular MPI brand.

Required output:

```text
dmft/maxent/Sig.out
```

## Real-axis DOS: foreground MPI

```bash
python workflow.py -c config.toml prepare-dos
python workflow.py -c config.toml doctor dos
python workflow.py -c config.toml run dos
```

`prepare-dos` creates `dmft/onreal/`, copies `Sig.out -> sig.inp`, preserves `CASE.indmfl.matsubara`, and changes only the copied active `CASE.indmfl` from Matsubara flag `1` to real-axis flag `0`.

`run dos` starts a disposable child shell, applies the native runtime from `site.toml`, writes `mpi_prefix.dat` using `[foreground].dos_np`, and runs:

```text
x_lapw -f CASE lapw0
x_dmft.py lapw1
x_dmft.py dmft1
```

## Band / A(k,w): foreground MPI

```bash
python workflow.py -c config.toml prepare-band
python workflow.py -c config.toml doctor band
python workflow.py -c config.toml run band
```

The band path is taken from `inputs/CASE.klist_band` when present, otherwise from explicit `band.klist_source` configuration. `@wien:` paths are resolved against the WIEN2k location in `site.toml`.

`run band` writes `mpi_prefix.dat` using `[foreground].band_np` and runs:

```text
x_dmft.py lapw1 --band
x_dmft.py dmftp
```

Main output:

```text
dmft/band/eigvals.dat
```

## Scheduler abstraction

The site profile controls behavior rather than a scheduler name. For example:

```toml
[scheduler]
jobid_env = "PBS_JOBID"
nodefile_env = "PBS_NODEFILE"
submit_command = "qsub"
slot_count_source = "nodefile"
resource_template = "#PBS -l nodes={nodes}:ppn={ppn}"
```

or a machine may instead use:

```toml
slot_count_source = "command"
slot_count_command = "qstat -f {jobid} | awk '/resources_used.ncpus/{print $NF; exit}'"
resource_template = "#PBS -l select={nodes}:ncpus={ppn}:mpiprocs={ppn}"
```

No Python code change is required between those behaviors.

## Provenance and safety

- `prepare-*` never modifies the upstream calculation directory.
- `dmft/CASE.indmfl` remains Matsubara flag `1`.
- DOS/band modify only copied real-axis `indmfl` files.
- `--force` backs up non-empty prepared stage directories.
- Fermi-level mismatches are warning-only diagnostics.
- DFT/DMFT/MaxEnt PBS scripts are standalone and do not read `config.toml` or `site.toml` at runtime.
- DOS/band foreground runs happen in child shells and do not alter the parent shell.

## Analysis

```bash
python workflow.py -c config.toml analyze all --format png
python workflow.py -c config.toml analyze all --format pdf
python workflow.py -c config.toml analyze all --format both
```

This produces convergence, self-energy, DOS/local-spectrum, and `A(k,w)` diagnostics when the required outputs exist.
