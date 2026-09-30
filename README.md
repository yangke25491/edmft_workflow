# edmft_workflow

A transparent workflow manager for **WIEN2k + Kristjan Haule eDMFT**.

The workflow automates repetitive file preparation, checks, runtime setup, and postprocessing while keeping scientific inputs and native WIEN2k/eDMFT commands visible. It never calls `qsub` automatically and never modifies the user's persistent shell environment.

## Configuration model

The workflow separates **calculation choices** from **machine environment**.

```text
calculation/config.toml
    = physics + frequently tuned run settings
      (case, DMFT/MaxEnt/DOS/band parameters,
       requested cores/memory/walltime/queue,
       foreground ranks, command overrides)

workflow-repository/site.toml
    = machine/site environment
      (WIEN2k/eDMFT/Python locations,
       compiler/MKL/FFTW setup,
       native MPI, MaxEnt MPI,
       scheduler environment variables and slot-count semantics)
```

The normal layout is therefore:

```text
~/apps/edmft_workflow/
├── workflow.py
├── edmft_workflow/
├── site.example.toml
└── site.toml              # private machine profile, ignored by git

~/test/DMFT/MnO/
├── config.toml            # this calculation only
├── inputs/
├── dft/
└── dmft/

~/test/DMFT/La3Ni2O7/
├── config.toml
├── inputs/
├── dft/
└── dmft/
```

Create the machine profile once in the workflow repository root:

```bash
cd ~/apps/edmft_workflow
cp site.example.toml site.toml
```

`site.toml` is ignored by git. Every calculation can then keep only its own `config.toml`.

The site-profile selection rule is intentionally simple:

```text
--site /path/to/another/site.toml   # explicit override, highest priority
otherwise:
<workflow-repository-root>/site.toml
```

For example, normal use needs no `--site`:

```bash
cd ~/test/DMFT/MnO
python ~/apps/edmft_workflow/workflow.py -c config.toml doctor env
```

To use another machine profile explicitly:

```bash
python ~/apps/edmft_workflow/workflow.py \
    --site ~/sites/other-cluster.toml \
    -c config.toml doctor env
```

For transition, an old `config.toml` that still contains `[environment]` / `[parallel]` remains readable when no site profile is loaded. Once a site profile is active, machine/runtime values come from `site.toml`, not from old environment entries in `config.toml`.

## Scheduler policy is behavior-driven

The Python code does not branch on names such as "Torque" or "PBS Pro". The site profile describes how the local scheduler behaves:

```toml
[scheduler]
jobid_env = "PBS_JOBID"
nodefile_env = "PBS_NODEFILE"
submit_command = "qsub"
slot_count_source = "nodefile"
resource_template = "#PBS -l nodes={nodes}:ppn={ppn}"
```

For a system where the nodefile contains hosts rather than one line per CPU slot, configure a command that prints the actual allocation:

```toml
[scheduler]
jobid_env = "PBS_JOBID"
nodefile_env = "PBS_NODEFILE"
submit_command = "qsub"
slot_count_source = "command"
slot_count_command = "qstat -f {jobid} | awk '/resources_used.ncpus/{print $NF; exit}'"
resource_template = "#PBS -l select={nodes}:ncpus={ppn}:mpiprocs={ppn}"
```

The workflow itself only asks, "how many slots were allocated?" and "what resource-request line should be generated?" Scheduler-specific syntax stays in `site.toml`.

## MPI/runtime policy

Native WIEN2k/eDMFT and MaxEnt may use different MPI implementations. They are configured independently:

```toml
[mpi.native]
launcher = "/path/to/native/mpirun"
np_flag = "-np"

[mpi.maxent]
launcher = "/path/to/mpi4py-compatible/mpirun"
np_flag = "-np"
```

Common runtime setup belongs in `[runtime]`; MaxEnt-only additions belong in `[runtime.maxent]`:

```toml
[runtime]
setup_commands = ["source /path/to/compiler/setup.sh"]
prepend_path = []
prepend_ld_library_path = []

[runtime.env]
OMP_NUM_THREADS = "1"
MKL_NUM_THREADS = "1"

[runtime.maxent]
setup_commands = []
prepend_path = []
prepend_ld_library_path = []
```

Generated PBS files are still **standalone static scripts**. `config.toml` and `site.toml` are read only while generating the script; compute nodes do not read either file at runtime.

## Execution model

```text
Heavy PBS jobs
  DFT
  DMFT
  MaxEnt

Foreground MPI postprocessing
  DOS
  Band / A(k,w)

Lightweight foreground work
  prepare-*
  doctor / check / status
  analyze / plots
```

Only DFT, DMFT, and MaxEnt generate PBS scripts. DOS and band run in disposable foreground child shells with the site-defined native MPI/runtime. The parent/login shell remains unchanged.

## Calculation directory layout

```text
PROJECT/
├── config.toml
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

```text
PROJECT/dft : init_lapw
PROJECT/dft : init_dmft.py     # after DFT convergence
```

## Basic workflow

Initialize the layout:

```bash
python ~/apps/edmft_workflow/workflow.py -c config.toml init-layout
```

DFT:

```bash
cd dft
init_lapw
cd ..
python ~/apps/edmft_workflow/workflow.py -c config.toml pbs dft
cat dft/run_dft.pbs
qsub dft/run_dft.pbs
```

After DFT convergence:

```bash
cd dft
init_dmft.py
cd ..
```

DMFT:

```bash
python ~/apps/edmft_workflow/workflow.py -c config.toml prepare-dmft
python ~/apps/edmft_workflow/workflow.py -c config.toml doctor dmft
python ~/apps/edmft_workflow/workflow.py -c config.toml pbs dmft
cat dmft/run_dmft.pbs
qsub dmft/run_dmft.pbs
```

MaxEnt:

```bash
python ~/apps/edmft_workflow/workflow.py -c config.toml prepare-maxent
python ~/apps/edmft_workflow/workflow.py -c config.toml doctor maxent
python ~/apps/edmft_workflow/workflow.py -c config.toml pbs maxent
cat dmft/maxent/run_maxent.pbs
qsub dmft/maxent/run_maxent.pbs
```

DOS:

```bash
python ~/apps/edmft_workflow/workflow.py -c config.toml prepare-dos
python ~/apps/edmft_workflow/workflow.py -c config.toml doctor dos
python ~/apps/edmft_workflow/workflow.py -c config.toml run dos
```

Band / A(k,w):

```bash
python ~/apps/edmft_workflow/workflow.py -c config.toml prepare-band
python ~/apps/edmft_workflow/workflow.py -c config.toml doctor band
python ~/apps/edmft_workflow/workflow.py -c config.toml run band
```

Analysis:

```bash
python ~/apps/edmft_workflow/workflow.py -c config.toml analyze all --format png
python ~/apps/edmft_workflow/workflow.py -c config.toml analyze all --format pdf
python ~/apps/edmft_workflow/workflow.py -c config.toml analyze all --format both
```

## What stays in config.toml

Calculation-local settings intentionally remain in `config.toml`:

```toml
[foreground]
dos_np = 8
band_np = 8

[pbs_dft]
ppn = 64
walltime = "168:00:00"
mem = ""

[pbs_dmft]
ppn = 32
walltime = "168:00:00"
mem = ""

[pbs_maxent]
ppn = 2
walltime = "24:00:00"
mem = ""
```

Command overrides also remain project-local because they are frequently tuned:

```toml
[dft]
run_command = "run_lapw -p -cc 0.0001 -ec 0.0001 -i 100"

[dmft]
# run_command = "python run_dmft.py"

[maxent]
# Full-command override; if set, include the launcher yourself.
# run_command = "mpirun -np 2 python /path/to/maxent_run.py sig.inpx"
```

## Environment checking

```bash
python ~/apps/edmft_workflow/workflow.py -c config.toml doctor env
```

The check uses the selected `site.toml`. Optional site-specific shared-library checks can be listed under:

```toml
[checks]
shared_libraries = ["libmkl_rt.so"]
```

No particular MKL library name is hard-coded into the Python workflow.

## Fermi-level invariant

After DMFT convergence, `dmft/EF.dat` is the canonical chemical potential. MaxEnt records the corresponding Fermi-level snapshot; DOS and band inherit the same state. `doctor` prints consistency warnings but never blocks execution solely because of a Fermi-level warning.

## Safety invariants

- workflow never calls `qsub` automatically;
- workflow never modifies persistent shell configuration;
- machine-specific `site.toml` lives in the workflow root and is gitignored;
- preparation never mutates the upstream calculation directory;
- real-axis `indmfl` changes are made only in copied DOS/band directories;
- DFT/DMFT/MaxEnt PBS files are standalone and do not import this workflow at runtime;
- DOS/band foreground MPI runs happen in disposable child shells;
- native and MaxEnt MPI stacks are independently configurable;
- scheduler behavior is data-driven through the site profile;
- `--force` backs up existing non-empty prepared stage directories before recreating them.

## Upstream

This project is an independent workflow layer around Kristjan Haule's eDMFT project and WIEN2k.
