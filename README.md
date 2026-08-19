# OJCOMS-06622-2026 : TwinSync

_Status: Accepted_

Simulation code and results for **TwinSync**, a mission-aware synchronization scheduler for
digital twins over dynamic cislunar networks. This directory contains the scheduler
implementations, network topology generator, scenario data, experiment scripts, and all
figures/tables produced for the paper.

## Prerequisites

- Conda (Miniconda or Anaconda)
- Git

## Setup

1. Clone the repository:
```bash
git clone <repository-url>
cd V2
```

2. Create the conda environment:
```bash
conda env create -f conda_env.yml
```

3. Activate the environment:
```bash
conda activate nsdt
```

## Running the Code

### Generate Scenario Data

First, generate the required scenario data files:

```bash
python generate_cntgen_scenarios.py
```

This creates network topology and routing data in the `scenarios/` directory for three test
scenarios:
- Balanced Dual-Mission
- Asymmetric Geographic
- Artemis-like Congestion

### Run Performance Tests

After generating scenarios, run the performance comparison:

```bash
python run_realistic_scenarios.py
```

This compares TwinSync against FIFO and RoundRobin schedulers across the three scenarios above
and writes graphs, a summary report, and LaTeX tables to `output/`.

### Run Limit-Breaking Experiments

`run_limit_experiments.py` extends `RealisticScenarioTest` to stress-test TwinSync beyond the
baseline scenarios (TT rate/deadline saturation sweeps, dynamic/bursty traffic, network
degradation, and mission-count scaling):

```bash
python run_limit_experiments.py               # run all four experiments (A-D)
python run_limit_experiments.py --experiment A # run a single experiment (A, B, C, or D)
python run_limit_experiments.py --smoke        # quick smoke test
python run_limit_experiments.py --plot-only    # regenerate plots from cached CSVs
python run_limit_experiments.py --seeds 30     # number of random seeds per config (default 30)
```

Experiment D generates its own scaling scenarios (`scenarios/scale_2`, `scale_4`, `scale_6`, ...)
on the fly. Results are written to `output/limit_breaking/`.

### Network Topology Visualization

`visualization/cnt_snapshot.py` renders a publication-quality snapshot of the network topology
(satellite positions, active links, and routed paths) at a given simulation timestep:

```bash
python visualization/cnt_snapshot.py --scenario artemis --time 0
python visualization/cnt_snapshot.py --scenario balanced --time 50 --output fig.pdf
```

## Output

All results are saved in the `output/` directory:

```
output/
├── summary_overview.md        # Detailed performance analysis (baseline scenarios)
├── combined_overview.png      # All baseline graphs in one image
├── balanced/                  # Individual scenario graphs (PDF)
├── asymmetric/
├── artemis/
├── summary/                   # LaTeX tables for publication
└── limit_breaking/            # Limit-breaking experiment results (A-D)
    ├── experiment_a/          # TT saturation surface (heatmaps, thresholds)
    ├── experiment_b/          # Dynamic/bursty traffic profiles
    ├── experiment_c/          # Network degradation robustness
    ├── experiment_d/          # Mission-count scaling
    └── summary/                # Combined results across all four experiments
```

### Key Outputs

- **Performance Graphs**: Individual PDF files for each metric in `output/<scenario>/`
- **Summary Report**: Markdown analysis in `output/summary_overview.md`
- **LaTeX Tables**: IEEE-format tables in `output/summary/` for direct inclusion in papers
- **Limit-Breaking Results**: Heatmaps, robustness curves, and scaling plots in
  `output/limit_breaking/`, with raw per-run data in each experiment's `raw_results.csv`

### Using LaTeX Tables

To include tables in your LaTeX document:

1. Add to preamble:
```latex
\usepackage{booktabs}
```

2. Include individual tables:
```latex
\input{output/summary/combined_comparison.tex}
```

Or include all tables:
```latex
\input{output/summary/all_tables.tex}
```

## Project Structure

```
.
├── README.md                     # This file
├── conda_env.yml                 # Conda environment specification
├── generate_cntgen_scenarios.py  # Scenario generation script
├── run_realistic_scenarios.py    # Baseline scheduler comparison script
├── run_limit_experiments.py      # Limit-breaking experiments (A-D)
├── modules/                      # Core scheduler implementations
│   ├── TwinSync.py
│   ├── FIFOScheduler.py
│   ├── RoundRobinScheduler.py
│   ├── BaseScheduler.py
│   ├── BSComps.py
│   └── CNTGen.py
├── scenarios/                    # Generated scenario data
│   ├── balanced/, asymmetric/, artemis/   # Baseline scenarios
│   └── scale_2/, scale_4/, scale_6/       # Scaling scenarios (Experiment D)
├── visualization/                # Network topology snapshot rendering
│   └── cnt_snapshot.py
└── output/                       # Test results and analysis
```

## Quick Start

```bash
# Setup
conda env create -f conda_env.yml
conda activate nsdt

# Run everything
python generate_cntgen_scenarios.py
python run_realistic_scenarios.py
python run_limit_experiments.py

# View results
open output/combined_overview.png  # macOS
# or
xdg-open output/combined_overview.png  # Linux
```
