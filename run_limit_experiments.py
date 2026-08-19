#!/usr/bin/env python3
"""
Limit-Breaking Experiments for TwinSync
========================================
Addresses Reviewer 3 comments (R3-C1 through R3-C4) and R1-C5.

Experiments:
  A — TT Saturation Surface (rate × deadline sweep)
  B — Dynamic Traffic Profiles (bursts, phases, correlated surges)
  C — Network Degradation (stale data, link failures, capacity noise)
  D — Scalability Tests (mission count scaling with timing)

Uses RealisticScenarioTest as the simulation backbone, extending it
via subclassing to inject new parameters without modifying the original.
"""

import sys
import os
import copy
import time
import json
import itertools
import traceback
from multiprocessing import Pool, cpu_count
from collections import defaultdict

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import LogNorm
from matplotlib import cm

# Ensure we can import from the sim directory
SIM_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SIM_DIR)

from run_realistic_scenarios import RealisticScenarioTest
from modules.CNTGen import CNTGenerator
from modules.BSComps import RADIUS_MOON

# ---------------------------------------------------------------------------
# Output directory
# ---------------------------------------------------------------------------
OUTPUT_BASE = os.path.join(SIM_DIR, "output", "limit_breaking")
PAPER_IMAGES_DIR = os.path.join(os.path.dirname(SIM_DIR), "dtjournal", "images")

def ensure_dir(path):
    os.makedirs(path, exist_ok=True)
    return path


# ---------------------------------------------------------------------------
# Per-process cache for CNT data (avoids re-reading files for same scenario)
# ---------------------------------------------------------------------------
_cnt_cache = {}

def _load_cached_cnt(scenario_path):
    """Load CNT data from cache or disk."""
    if scenario_path in _cnt_cache:
        return _cnt_cache[scenario_path]

    data = {}
    with open(os.path.join(scenario_path, "CNT_allocations.json"), 'r') as f:
        data['cnt_allocations'] = json.load(f)
    with open(os.path.join(scenario_path, "CNT_summary.json"), 'r') as f:
        data['cnt_summary'] = json.load(f)
    with open(os.path.join(scenario_path, "scenario_info.json"), 'r') as f:
        data['scenario_info'] = json.load(f)

    cnt_routes = []
    with open(os.path.join(scenario_path, "CNT_routes.txt"), 'r') as f:
        for line in f:
            if line.strip():
                time_routes = []
                if '|' in line:
                    for route_str in line.strip().split('|'):
                        if route_str:
                            time_routes.append(list(map(int, route_str.split(','))))
                cnt_routes.append(time_routes)
    data['cnt_routes'] = cnt_routes

    cnt_links = []
    with open(os.path.join(scenario_path, "CNT_links.txt"), 'r') as f:
        current_matrix = []
        for line in f:
            if '# ENDMAT' in line:
                if current_matrix:
                    cnt_links.append(np.array(current_matrix))
                current_matrix = []
            else:
                row = list(map(int, line.strip().split(',')))
                current_matrix.append(row)
    data['cnt_links'] = cnt_links

    _cnt_cache[scenario_path] = data
    return data


# ===========================================================================
# Extended RealisticScenarioTest
# ===========================================================================

class LimitBreakingTest(RealisticScenarioTest):
    """
    Extends RealisticScenarioTest with:
      - configurable TT rate multiplier and deadline (Exp A)
      - dynamic traffic injection (Exp B)
      - CNT degradation (Exp C)
      - per-timestep scheduling timing (Exp D)
    """

    def __init__(self, scenario_type, scheduler_type,
                 tt_rate_mult=1.0, tt_deadline=3.0,
                 dynamic_config=None,
                 cnt_staleness=0, cnt_failure_rate=0.0, cnt_noise_config=None,
                 sim_length_override=None,
                 scenario_path_override=None):
        # Stash parameters BEFORE super().__init__ which calls setup + load
        self._tt_rate_mult = tt_rate_mult
        self._tt_deadline = tt_deadline
        self._dynamic_config = dynamic_config
        self._cnt_staleness = cnt_staleness
        self._cnt_failure_rate = cnt_failure_rate
        self._cnt_noise_config = cnt_noise_config
        self._sim_length_override = sim_length_override
        self._scenario_path_override = scenario_path_override

        # Timing instrumentation (Exp D)
        self.scheduling_times = []

        # Call parent init (loads data, sets up scenario)
        # NOTE: The parent __init__ clears cnt_allocations/cnt_routes/etc.
        # after setup, so we must re-apply them.
        super().__init__(scenario_type, scheduler_type)

        # Re-load CNT data because parent __init__ clears it after setup
        self.load_cnt_data(scenario_type)

        # Override sim_length if requested
        if sim_length_override is not None:
            self.sim_length = min(sim_length_override, len(self.cnt_allocations))

    # ------------------------------------------------------------------
    # Override load_cnt_data to support custom scenario paths + caching
    # ------------------------------------------------------------------
    def load_cnt_data(self, scenario_type):
        if self._scenario_path_override:
            scenario_path = self._scenario_path_override
        else:
            scenario_path = os.path.join(SIM_DIR, "scenarios", scenario_type)

        data = _load_cached_cnt(scenario_path)
        self.cnt_allocations = data['cnt_allocations']
        self.cnt_summary = data['cnt_summary']
        self.scenario_info = copy.deepcopy(data['scenario_info'])
        self.cnt_routes = data['cnt_routes']
        self.cnt_links = data['cnt_links']

    # ------------------------------------------------------------------
    # Override generate_traffic for Experiments A & B
    # ------------------------------------------------------------------
    def generate_traffic(self):
        """Generate traffic with configurable TT rate multiplier, deadline,
        and dynamic traffic profiles."""
        # Determine rate multiplier at this timestep
        rate_mult = self._get_current_rate_multiplier()

        for mission in self.missions:
            for asset in mission['assets']:
                # --- TT traffic ---
                tt_base_rate = mission['traffic_rates']['TT']
                rate = tt_base_rate * self._tt_rate_mult * rate_mult / 3
                if np.random.random() < rate:
                    item = {
                        'id': self.item_id_counter,
                        'generation_time': self.current_time,
                        'deadline': self.current_time + self._tt_deadline,
                        'size': 0.5,
                        'class': 'TT',
                        'priority': 10,
                        'mission': mission['name'],
                        'asset': asset['name']
                    }
                    self.item_id_counter += 1
                    asset['queues']['TT'].append(item)
                    self.all_items.append(item)
                    self.total_items['TT'] += 1

                # --- RC traffic ---
                rc_base_rate = mission['traffic_rates']['RC']
                rate = rc_base_rate * rate_mult / 3
                if np.random.random() < rate:
                    item = {
                        'id': self.item_id_counter,
                        'generation_time': self.current_time,
                        'deadline': self.current_time + 10,
                        'size': 1.0,
                        'class': 'RC',
                        'priority': 5,
                        'mission': mission['name'],
                        'asset': asset['name']
                    }
                    self.item_id_counter += 1
                    asset['queues']['RC'].append(item)
                    self.all_items.append(item)
                    self.total_items['RC'] += 1

                # --- BE traffic ---
                be_base_rate = mission['traffic_rates']['BE']
                rate = be_base_rate * rate_mult / 3
                if np.random.random() < rate:
                    item = {
                        'id': self.item_id_counter,
                        'generation_time': self.current_time,
                        'deadline': self.current_time + 20,
                        'size': 1.5,
                        'class': 'BE',
                        'priority': 1,
                        'mission': mission['name'],
                        'asset': asset['name']
                    }
                    self.item_id_counter += 1
                    asset['queues']['BE'].append(item)
                    self.all_items.append(item)
                    self.total_items['BE'] += 1

    def _get_current_rate_multiplier(self):
        """Return a dynamic rate multiplier for the current timestep (Exp B)."""
        if self._dynamic_config is None:
            return 1.0

        cfg = self._dynamic_config
        t = self.current_time

        if cfg['type'] == 'burst':
            for burst in cfg.get('bursts', []):
                if burst['start'] <= t < burst['end']:
                    return burst['multiplier']
            return 1.0

        elif cfg['type'] == 'phase':
            schedule = cfg.get('schedule', [])
            for phase_name, start, end in schedule:
                if start <= t < end:
                    profiles = cfg['profiles']
                    if phase_name in profiles:
                        # Return the TT multiplier as the overall rate multiplier
                        return profiles[phase_name].get('TT', 1.0)
            return 1.0

        elif cfg['type'] == 'correlated':
            # Check simultaneous surge
            sim = cfg.get('simultaneous')
            if sim and sim['start'] <= t < sim['end']:
                return sim['multiplier']
            # Check cascade
            for cascade_burst in cfg.get('cascade', []):
                if cascade_burst['start'] <= t < cascade_burst['end']:
                    return cascade_burst['multiplier']
            return 1.0

        return 1.0

    # ------------------------------------------------------------------
    # Override get_available_bandwidth for Experiment C
    # ------------------------------------------------------------------
    def get_available_bandwidth(self, mission_name):
        """Get available bandwidth with optional CNT degradation."""
        # Apply staleness
        effective_time = max(0, self.current_time - self._cnt_staleness)
        if effective_time >= len(self.cnt_allocations):
            bw = self.total_capacity / len(self.missions)
        else:
            time_alloc = self.cnt_allocations[effective_time]
            if 'missions' in time_alloc and mission_name in time_alloc['missions']:
                bw = time_alloc['missions'][mission_name].get('allocated', 0)
            else:
                bw = self.total_capacity / len(self.missions)

        # Apply link failures (reduce bandwidth proportionally)
        if self._cnt_failure_rate > 0:
            # Each "route" survives with probability (1 - failure_rate)
            # Approximate as overall bandwidth reduction
            survive_rate = max(0.1, 1.0 - self._cnt_failure_rate)
            bw *= survive_rate

        # Apply capacity noise
        if self._cnt_noise_config is not None:
            nc = self._cnt_noise_config
            t = self.current_time
            sinusoidal = nc['amplitude'] * np.sin(2 * np.pi * t / nc['period'])
            gaussian = np.random.normal(0, nc['gaussian_std'])
            factor = max(0.1, 1.0 + sinusoidal + gaussian)
            bw *= factor

        return bw

    def get_route_delay(self, mission_name):
        """Get route delay with staleness applied."""
        effective_time = max(0, self.current_time - self._cnt_staleness)
        if effective_time >= len(self.cnt_allocations):
            return 1.3
        time_alloc = self.cnt_allocations[effective_time]
        if 'missions' in time_alloc and mission_name in time_alloc['missions']:
            routes = time_alloc['missions'][mission_name].get('routes', [])
            if routes:
                return routes[0].get('delay', 1.3)
        return 1.3

    # ------------------------------------------------------------------
    # Override simulate to add timing instrumentation (Exp D)
    # ------------------------------------------------------------------
    def simulate(self):
        """Run simulation with optional per-timestep timing."""
        for t in range(self.sim_length):
            self.current_time = t
            self.generate_traffic()
            self.record_metrics()

            # Time the scheduling step
            t_start = time.perf_counter()
            if self.scheduler_type == "TwinSync":
                used_bw = self.schedule_twinsync()
            elif self.scheduler_type == "FIFO":
                used_bw = self.schedule_fifo()
            else:
                used_bw = self.schedule_roundrobin()
            t_end = time.perf_counter()
            self.scheduling_times.append(t_end - t_start)

            utilization = (used_bw / self.total_capacity * 100) if self.total_capacity > 0 else 0
            self.utilization_history.append(utilization)
            self.drop_expired()

        return self.calculate_final_metrics()


# ===========================================================================
# Worker function for multiprocessing
# ===========================================================================

def run_single_config(args):
    """
    Run a single experiment configuration.
    Args is a dict with all parameters.
    Returns a dict with results.
    """
    try:
        seed = args['seed']
        np.random.seed(seed)
        import random
        random.seed(seed)

        test = LimitBreakingTest(
            scenario_type=args['scenario_type'],
            scheduler_type=args['scheduler_type'],
            tt_rate_mult=args.get('tt_rate_mult', 1.0),
            tt_deadline=args.get('tt_deadline', 3.0),
            dynamic_config=args.get('dynamic_config', None),
            cnt_staleness=args.get('cnt_staleness', 0),
            cnt_failure_rate=args.get('cnt_failure_rate', 0.0),
            cnt_noise_config=args.get('cnt_noise_config', None),
            sim_length_override=args.get('sim_length_override', None),
            scenario_path_override=args.get('scenario_path_override', None),
        )

        metrics = test.simulate()

        result = {
            'scenario_type': args['scenario_type'],
            'scheduler_type': args['scheduler_type'],
            'seed': seed,
            'tt_rate_mult': args.get('tt_rate_mult', 1.0),
            'tt_deadline': args.get('tt_deadline', 3.0),
            'dynamic_config_id': args.get('dynamic_config_id', 'none'),
            'cnt_staleness': args.get('cnt_staleness', 0),
            'cnt_failure_rate': args.get('cnt_failure_rate', 0.0),
            'cnt_noise_id': args.get('cnt_noise_id', 'none'),
            'mission_count': args.get('mission_count', 0),
            'experiment': args['experiment'],
            # Key metrics
            'tt_miss_rate': metrics['deadline_miss_rate'].get('TT', 0),
            'rc_miss_rate': metrics['deadline_miss_rate'].get('RC', 0),
            'be_miss_rate': metrics['deadline_miss_rate'].get('BE', 0),
            'tt_misses': test.deadline_misses['TT'],
            'rc_misses': test.deadline_misses['RC'],
            'be_misses': test.deadline_misses['BE'],
            'tt_total': test.total_items['TT'],
            'rc_total': test.total_items['RC'],
            'be_total': test.total_items['BE'],
            'jain_index': metrics['fairness']['jain_index'],
            'mean_utilization': np.mean(test.utilization_history) if test.utilization_history else 0,
            # AoS stats
            'mean_worst_tt_aos': np.mean(test.aos_history['Worst_TT']) if test.aos_history['Worst_TT'] else 0,
            'max_worst_tt_aos': np.max(test.aos_history['Worst_TT']) if test.aos_history['Worst_TT'] else 0,
            # Timing stats (Exp D)
            'mean_sched_time': np.mean(test.scheduling_times) if test.scheduling_times else 0,
            'median_sched_time': np.median(test.scheduling_times) if test.scheduling_times else 0,
            'p95_sched_time': np.percentile(test.scheduling_times, 95) if test.scheduling_times else 0,
            'max_sched_time': np.max(test.scheduling_times) if test.scheduling_times else 0,
        }
        return result

    except Exception as e:
        return {
            'experiment': args.get('experiment', 'unknown'),
            'error': str(e),
            'traceback': traceback.format_exc(),
            'args': {k: v for k, v in args.items() if k != 'dynamic_config'}
        }


# ===========================================================================
# Experiment A — TT Saturation Surface
# ===========================================================================

def build_experiment_a_configs(seeds):
    """Build all configurations for Experiment A."""
    scenarios = ['balanced', 'asymmetric', 'artemis']
    schedulers = ['TwinSync', 'FIFO', 'RoundRobin']
    tt_rate_mults = [1.0, 1.5, 2.0, 3.0, 4.0, 5.0, 7.0, 10.0, 15.0, 20.0]
    tt_deadlines = [5.0, 4.0, 3.0, 2.5, 2.0, 1.8, 1.5, 1.3, 1.1, 1.0]

    configs = []
    for scenario, scheduler, mult, deadline, seed in itertools.product(
            scenarios, schedulers, tt_rate_mults, tt_deadlines, seeds):
        configs.append({
            'experiment': 'A',
            'scenario_type': scenario,
            'scheduler_type': scheduler,
            'tt_rate_mult': mult,
            'tt_deadline': deadline,
            'seed': seed,
            'sim_length_override': 300,
        })
    return configs


def plot_experiment_a(df, out_dir):
    """Generate Experiment A plots."""
    ensure_dir(out_dir)

    scenarios = ['balanced', 'asymmetric', 'artemis']
    schedulers = ['TwinSync', 'FIFO', 'RoundRobin']
    tt_rate_mults = sorted(df['tt_rate_mult'].unique())
    tt_deadlines = sorted(df['tt_deadline'].unique(), reverse=True)

    # 1. Survivability Heatmaps (3×3 grid: scenario × scheduler)
    fig, axes = plt.subplots(3, 3, figsize=(18, 15))
    for row, scenario in enumerate(scenarios):
        for col, scheduler in enumerate(schedulers):
            ax = axes[row, col]
            subset = df[(df['scenario_type'] == scenario) & (df['scheduler_type'] == scheduler)]
            pivot = subset.groupby(['tt_rate_mult', 'tt_deadline'])['tt_miss_rate'].mean().reset_index()
            heatmap_data = pivot.pivot(index='tt_deadline', columns='tt_rate_mult', values='tt_miss_rate')
            # Sort index descending
            heatmap_data = heatmap_data.sort_index(ascending=False)

            im = ax.imshow(heatmap_data.values, aspect='auto', cmap='RdYlGn_r',
                          vmin=0, vmax=1,
                          extent=[0, len(tt_rate_mults), 0, len(tt_deadlines)])

            ax.set_xticks(np.arange(len(tt_rate_mults)) + 0.5)
            ax.set_xticklabels([f'{x:.0f}x' if x >= 1 else f'{x}x' for x in tt_rate_mults], fontsize=7, rotation=45)
            ax.set_yticks(np.arange(len(tt_deadlines)) + 0.5)
            ax.set_yticklabels([f'{d:.1f}s' for d in sorted(tt_deadlines)], fontsize=7)

            ax.set_title(f'{scenario} / {scheduler}', fontsize=10)
            if col == 0:
                ax.set_ylabel('TT Deadline', fontsize=9)
            if row == 2:
                ax.set_xlabel('TT Rate Multiplier', fontsize=9)

            # Overlay 1% contour if possible
            try:
                cs = ax.contour(np.arange(len(tt_rate_mults)) + 0.5,
                               np.arange(len(tt_deadlines)) + 0.5,
                               heatmap_data.values, levels=[0.01],
                               colors='blue', linewidths=2)
            except Exception:
                pass

    fig.colorbar(im, ax=axes, label='TT Miss Rate', shrink=0.6)
    fig.suptitle('Experiment A: TT Saturation Surface', fontsize=14, fontweight='bold')
    plt.tight_layout(rect=[0, 0, 0.92, 0.96])
    plt.savefig(os.path.join(out_dir, 'heatmap_all.pdf'), dpi=150, bbox_inches='tight')
    plt.close()

    # Individual heatmaps per scenario
    for scenario in scenarios:
        fig, axes_row = plt.subplots(1, 3, figsize=(18, 5))
        for col, scheduler in enumerate(schedulers):
            ax = axes_row[col]
            subset = df[(df['scenario_type'] == scenario) & (df['scheduler_type'] == scheduler)]
            pivot = subset.groupby(['tt_rate_mult', 'tt_deadline'])['tt_miss_rate'].mean().reset_index()
            heatmap_data = pivot.pivot(index='tt_deadline', columns='tt_rate_mult', values='tt_miss_rate')
            heatmap_data = heatmap_data.sort_index(ascending=False)

            im = ax.imshow(heatmap_data.values, aspect='auto', cmap='RdYlGn_r',
                          vmin=0, vmax=1,
                          extent=[0, len(tt_rate_mults), 0, len(tt_deadlines)])
            ax.set_xticks(np.arange(len(tt_rate_mults)) + 0.5)
            ax.set_xticklabels([f'{x:.0f}x' if x >= 1 else f'{x}x' for x in tt_rate_mults], fontsize=8, rotation=45)
            ax.set_yticks(np.arange(len(tt_deadlines)) + 0.5)
            ax.set_yticklabels([f'{d:.1f}s' for d in sorted(tt_deadlines)], fontsize=8)
            ax.set_title(f'{scheduler}', fontsize=11)
            ax.set_ylabel('TT Deadline (s)' if col == 0 else '', fontsize=10)
            ax.set_xlabel('TT Rate Multiplier', fontsize=10)
        fig.colorbar(im, ax=axes_row, label='TT Miss Rate', shrink=0.8)
        fig.suptitle(f'Experiment A: {scenario} — TT Survivability Surface', fontsize=13)
        plt.tight_layout(rect=[0, 0, 0.92, 0.96])
        plt.savefig(os.path.join(out_dir, f'heatmap_{scenario}.pdf'), dpi=150, bbox_inches='tight')
        plt.close()

    # 2. Threshold Table
    threshold_rows = []
    for scenario in scenarios:
        for deadline in tt_deadlines:
            for scheduler in schedulers:
                subset = df[(df['scenario_type'] == scenario) &
                           (df['scheduler_type'] == scheduler) &
                           (df['tt_deadline'] == deadline)]
                means = subset.groupby('tt_rate_mult')['tt_miss_rate'].mean()
                # Find max mult where miss rate is 0
                zero_miss_mults = means[means == 0].index
                max_zero = max(zero_miss_mults) if len(zero_miss_mults) > 0 else 0
                threshold_rows.append({
                    'scenario': scenario,
                    'deadline': deadline,
                    'scheduler': scheduler,
                    'max_zero_miss_mult': max_zero
                })
    threshold_df = pd.DataFrame(threshold_rows)
    threshold_df.to_csv(os.path.join(out_dir, 'threshold_table.csv'), index=False)

    # 3. Cross-class Impact Plot (at fixed deadline=3.0s)
    fig, axes_row = plt.subplots(1, 3, figsize=(18, 5))
    colors = {'TwinSync': 'blue', 'FIFO': 'red', 'RoundRobin': 'green'}
    for col, scenario in enumerate(scenarios):
        ax = axes_row[col]
        for scheduler in schedulers:
            subset = df[(df['scenario_type'] == scenario) &
                       (df['scheduler_type'] == scheduler) &
                       (df['tt_deadline'] == 3.0)]
            means = subset.groupby('tt_rate_mult').agg({
                'tt_miss_rate': 'mean',
                'rc_miss_rate': 'mean',
                'be_miss_rate': 'mean'
            }).reset_index()

            ax.plot(means['tt_rate_mult'], means['tt_miss_rate'] * 100,
                   '-o', color=colors[scheduler], label=f'{scheduler} TT', markersize=4)
            ax.plot(means['tt_rate_mult'], means['rc_miss_rate'] * 100,
                   '--s', color=colors[scheduler], label=f'{scheduler} RC', markersize=3, alpha=0.7)
            ax.plot(means['tt_rate_mult'], means['be_miss_rate'] * 100,
                   ':^', color=colors[scheduler], label=f'{scheduler} BE', markersize=3, alpha=0.5)

        ax.set_xlabel('TT Rate Multiplier', fontsize=10)
        ax.set_ylabel('Miss Rate (%)' if col == 0 else '', fontsize=10)
        ax.set_title(f'{scenario}', fontsize=11)
        ax.legend(fontsize=6, ncol=3)
        ax.grid(True, alpha=0.3)
        ax.set_xscale('log')
    fig.suptitle('Experiment A: Cross-Class Impact (deadline=3.0s)', fontsize=13)
    plt.tight_layout(rect=[0, 0, 1, 0.94])
    plt.savefig(os.path.join(out_dir, 'cross_class_impact.pdf'), dpi=150, bbox_inches='tight')
    plt.close()


# ===========================================================================
# Experiment B — Dynamic Traffic Profiles
# ===========================================================================

def get_dynamic_configs():
    """Return the 7 dynamic traffic configurations for Experiment B."""
    configs = {}

    # B1: Instantaneous Bursts
    configs['B1-a'] = {
        'type': 'burst',
        'bursts': [{'start': 200, 'end': 220, 'multiplier': 5.0}]
    }
    configs['B1-b'] = {
        'type': 'burst',
        'bursts': [{'start': 200, 'end': 210, 'multiplier': 10.0}]
    }
    configs['B1-c'] = {
        'type': 'burst',
        'bursts': [{'start': 200, 'end': 205, 'multiplier': 20.0}]
    }
    configs['B1-d'] = {
        'type': 'burst',
        'bursts': [{'start': 100, 'end': 120, 'multiplier': 5.0},
                   {'start': 300, 'end': 320, 'multiplier': 5.0}]
    }
    configs['B1-e'] = {
        'type': 'burst',
        'bursts': [{'start': 200, 'end': 220, 'multiplier': 10.0}]
        # Note: single-mission targeting would require per-mission logic;
        # here we apply to all but the effect is similar for stress testing
    }

    # B2: Mission Phase Transitions
    configs['B2-phase'] = {
        'type': 'phase',
        'profiles': {
            'cruise': {'TT': 0.3, 'RC': 0.5, 'BE': 0.2},
            'approach': {'TT': 1.0, 'RC': 1.0, 'BE': 1.0},
            'landing': {'TT': 5.0, 'RC': 3.0, 'BE': 0.5},
            'surface_ops': {'TT': 1.0, 'RC': 2.0, 'BE': 4.0},
            'eva': {'TT': 8.0, 'RC': 4.0, 'BE': 1.0},
        },
        'schedule': [
            ('cruise', 0, 100),
            ('approach', 100, 150),
            ('landing', 150, 200),
            ('surface_ops', 200, 350),
            ('eva', 350, 400),
            ('surface_ops', 400, 500),
        ]
    }

    # B3: Correlated Surges
    configs['B3-correlated'] = {
        'type': 'correlated',
        'simultaneous': {'start': 200, 'end': 250, 'multiplier': 3.0},
        'cascade': [
            {'start': 300, 'end': 310, 'multiplier': 5.0},
            {'start': 305, 'end': 315, 'multiplier': 5.0},
            {'start': 310, 'end': 320, 'multiplier': 5.0},
        ]
    }

    return configs


def build_experiment_b_configs(seeds):
    """Build all configurations for Experiment B."""
    scenarios = ['balanced', 'asymmetric', 'artemis']
    schedulers = ['TwinSync', 'FIFO', 'RoundRobin']
    dynamic_cfgs = get_dynamic_configs()

    configs = []
    for scenario, scheduler, (cfg_id, cfg), seed in itertools.product(
            scenarios, schedulers, dynamic_cfgs.items(), seeds):
        configs.append({
            'experiment': 'B',
            'scenario_type': scenario,
            'scheduler_type': scheduler,
            'dynamic_config': cfg,
            'dynamic_config_id': cfg_id,
            'seed': seed,
            'sim_length_override': 500,
        })
    return configs


def plot_experiment_b(df, out_dir):
    """Generate Experiment B plots."""
    ensure_dir(out_dir)

    scenarios = ['balanced', 'asymmetric', 'artemis']
    schedulers = ['TwinSync', 'FIFO', 'RoundRobin']
    colors = {'TwinSync': 'blue', 'FIFO': 'red', 'RoundRobin': 'green'}

    # 1. Burst Response: TT miss rate by config
    fig, axes_row = plt.subplots(1, 3, figsize=(18, 5))
    for col, scenario in enumerate(scenarios):
        ax = axes_row[col]
        subset = df[df['scenario_type'] == scenario]
        config_ids = sorted(subset['dynamic_config_id'].unique())

        x = np.arange(len(config_ids))
        width = 0.25
        for i, scheduler in enumerate(schedulers):
            sched_data = subset[subset['scheduler_type'] == scheduler]
            means = sched_data.groupby('dynamic_config_id')['tt_miss_rate'].mean()
            stds = sched_data.groupby('dynamic_config_id')['tt_miss_rate'].std()
            vals = [means.get(c, 0) * 100 for c in config_ids]
            errs = [stds.get(c, 0) * 100 for c in config_ids]
            ax.bar(x + i * width, vals, width, yerr=errs, label=scheduler,
                  color=colors[scheduler], alpha=0.8, capsize=2)

        ax.set_xlabel('Dynamic Config', fontsize=10)
        ax.set_ylabel('TT Miss Rate (%)' if col == 0 else '', fontsize=10)
        ax.set_title(f'{scenario}', fontsize=11)
        ax.set_xticks(x + width)
        ax.set_xticklabels(config_ids, fontsize=7, rotation=45)
        ax.legend(fontsize=8)
        ax.grid(True, alpha=0.3, axis='y')

    fig.suptitle('Experiment B: Dynamic Traffic — TT Miss Rates', fontsize=13)
    plt.tight_layout(rect=[0, 0, 1, 0.94])
    plt.savefig(os.path.join(out_dir, 'burst_response_tt_miss.pdf'), dpi=150, bbox_inches='tight')
    plt.close()

    # 2. Recovery / overall metrics table
    summary_rows = []
    for scenario in scenarios:
        for config_id in df['dynamic_config_id'].unique():
            for scheduler in schedulers:
                subset = df[(df['scenario_type'] == scenario) &
                           (df['dynamic_config_id'] == config_id) &
                           (df['scheduler_type'] == scheduler)]
                if len(subset) == 0:
                    continue
                summary_rows.append({
                    'scenario': scenario,
                    'config': config_id,
                    'scheduler': scheduler,
                    'mean_tt_miss': subset['tt_miss_rate'].mean(),
                    'std_tt_miss': subset['tt_miss_rate'].std(),
                    'mean_rc_miss': subset['rc_miss_rate'].mean(),
                    'mean_be_miss': subset['be_miss_rate'].mean(),
                    'mean_worst_tt_aos': subset['mean_worst_tt_aos'].mean(),
                    'mean_utilization': subset['mean_utilization'].mean(),
                })
    pd.DataFrame(summary_rows).to_csv(os.path.join(out_dir, 'recovery_table.csv'), index=False)


# ===========================================================================
# Cross-class combined figure (uses Exp A + Exp B data)
# ===========================================================================

def plot_cross_class_combined(df_a, df_b, out_dir):
    """Generate consolidated 2×3 multi-class stress figure.

    Row 1 (Exp A): TT/RC/BE miss rate vs TT rate multiplier at deadline=3.0s.
              Shows TwinSync's priority hierarchy — TT guaranteed, BE absorbs load.
    Row 2 (Exp B): TT/RC/BE miss rates under key dynamic traffic configs.
              Shows the same tradeoff under realistic traffic dynamics.

    Saved as: cross_class_stress.pdf
    """
    import shutil

    ensure_dir(out_dir)

    scenarios = ['balanced', 'asymmetric', 'artemis']
    scenario_labels = ['Balanced', 'Asymmetric', 'Artemis']
    ts_color = '#1f77b4'
    fifo_color = '#d62728'
    rr_color = '#2ca02c'

    fig, axes = plt.subplots(2, 3, figsize=(15, 8))
    fig.suptitle('Multi-Class Traffic Performance Under Stress', fontsize=13, fontweight='bold')

    # ── Row 1: TT saturation cross-class impact (Exp A, deadline=3.0s) ──────
    has_a = df_a is not None and len(df_a) > 0
    if has_a and 'tt_deadline' in df_a.columns:
        deadline_data = df_a[df_a['tt_deadline'] == 3.0]
    else:
        deadline_data = pd.DataFrame()

    for col, (scenario, label) in enumerate(zip(scenarios, scenario_labels)):
        ax = axes[0, col]
        if len(deadline_data) > 0:
            scen = deadline_data[deadline_data['scenario_type'] == scenario]
            # TwinSync: all three classes
            ts = scen[scen['scheduler_type'] == 'TwinSync']
            ts_m = ts.groupby('tt_rate_mult').agg(
                tt=('tt_miss_rate', 'mean'),
                rc=('rc_miss_rate', 'mean'),
                be=('be_miss_rate', 'mean'),
            ).reset_index()
            ax.plot(ts_m['tt_rate_mult'], ts_m['tt'] * 100,
                    '-o', color=ts_color, linewidth=2, markersize=4, label='TS: TT')
            ax.plot(ts_m['tt_rate_mult'], ts_m['rc'] * 100,
                    '--s', color=ts_color, linewidth=1.5, markersize=3, alpha=0.75, label='TS: RC')
            ax.plot(ts_m['tt_rate_mult'], ts_m['be'] * 100,
                    ':^', color=ts_color, linewidth=1.5, markersize=3, alpha=0.55, label='TS: BE')
            # FIFO TT: de-emphasised reference line
            fifo = scen[scen['scheduler_type'] == 'FIFO']
            fifo_m = fifo.groupby('tt_rate_mult')['tt_miss_rate'].mean().reset_index()
            ax.plot(fifo_m['tt_rate_mult'], fifo_m['tt_miss_rate'] * 100,
                    '-D', color='#aaaaaa', linewidth=1.0, markersize=3, alpha=0.5, label='FIFO: TT')

        ax.set_title(label, fontsize=11)
        ax.set_xscale('log')
        ax.set_xlim(left=0.9)
        ax.set_ylim(-3, 108)
        ax.set_xlabel('TT Rate Multiplier' if col == 1 else '', fontsize=9)
        ax.grid(True, alpha=0.3)
        if col == 0:
            ax.set_ylabel('Miss Rate (%)\n[TT Saturation, deadline=3.0\u202fs]', fontsize=9)
            ax.legend(fontsize=7, loc='upper left')

    # ── Row 2: Dynamic traffic multi-class response (Exp B) ──────────────────
    has_b = df_b is not None and len(df_b) > 0
    key_configs = ['B1-c', 'B2-phase', 'B3-correlated']
    config_labels = ['20× Burst', 'Phase Trans.', 'Corr. Surge']

    bar_items = [
        ('TwinSync',   'tt_miss_rate', ts_color,   'TS: TT',   0.90),
        ('TwinSync',   'rc_miss_rate', '#6baed6',   'TS: RC',   0.90),
        ('TwinSync',   'be_miss_rate', '#bdd7e7',   'TS: BE',   0.90),
        ('FIFO',       'tt_miss_rate', '#bbbbbb',   'FIFO: TT', 0.55),
        ('RoundRobin', 'tt_miss_rate', '#cccccc',   'RR: TT',   0.55),
    ]
    n_bars = len(bar_items)
    bar_width = 0.14
    offsets = np.arange(n_bars) * bar_width - (n_bars - 1) * bar_width / 2
    x = np.arange(len(key_configs))

    for col, (scenario, label) in enumerate(zip(scenarios, scenario_labels)):
        ax = axes[1, col]
        if has_b:
            scen = df_b[df_b['scenario_type'] == scenario]
            for j, (sched, metric, color, bar_label, alpha) in enumerate(bar_items):
                vals = []
                for cfg in key_configs:
                    subset = scen[(scen['dynamic_config_id'] == cfg) &
                                  (scen['scheduler_type'] == sched)]
                    vals.append(subset[metric].mean() * 100 if len(subset) > 0 else 0)
                ax.bar(x + offsets[j], vals, bar_width,
                       color=color, alpha=alpha,
                       label=bar_label if col == 0 else '')

        ax.set_xticks(x)
        ax.set_xticklabels(config_labels, fontsize=8)
        ax.set_title(label, fontsize=11)
        ax.set_ylim(-2, 108)
        ax.grid(True, alpha=0.3, axis='y')
        ax.set_xlabel('Traffic Config' if col == 1 else '', fontsize=9)
        if col == 0:
            ax.set_ylabel('Miss Rate (%)\n[Dynamic Traffic]', fontsize=9)
            ax.legend(fontsize=7, loc='upper right', ncol=2)

    plt.tight_layout(rect=[0, 0, 1, 0.95])

    out_path = os.path.join(out_dir, 'cross_class_stress.pdf')
    plt.savefig(out_path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"Saved: {out_path}")

    if os.path.isdir(PAPER_IMAGES_DIR):
        shutil.copy2(out_path, os.path.join(PAPER_IMAGES_DIR, 'cross_class_stress.pdf'))
        print(f"Copied cross_class_stress.pdf to paper images")


# ===========================================================================
# Experiment C — Network Degradation
# ===========================================================================

def build_experiment_c_configs(seeds):
    """Build all configurations for Experiment C."""
    scenarios = ['balanced', 'asymmetric', 'artemis']
    schedulers = ['TwinSync', 'FIFO', 'RoundRobin']

    # C1: Stale Twin Data
    staleness_values = [0, 5, 10, 20, 50, 100]
    # C2: Random Link Failures
    failure_rates = [0.0, 0.05, 0.10, 0.20, 0.30, 0.50]
    # C3: Capacity Fluctuations
    noise_configs = [
        {'id': 'mild', 'amplitude': 0.1, 'period': 50, 'gaussian_std': 0.05},
        {'id': 'moderate', 'amplitude': 0.3, 'period': 50, 'gaussian_std': 0.10},
        {'id': 'severe', 'amplitude': 0.5, 'period': 30, 'gaussian_std': 0.15},
        {'id': 'extreme', 'amplitude': 0.7, 'period': 20, 'gaussian_std': 0.20},
    ]

    configs = []

    # C1
    for scenario, scheduler, staleness, seed in itertools.product(
            scenarios, schedulers, staleness_values, seeds):
        configs.append({
            'experiment': 'C',
            'scenario_type': scenario,
            'scheduler_type': scheduler,
            'cnt_staleness': staleness,
            'cnt_failure_rate': 0.0,
            'cnt_noise_config': None,
            'cnt_noise_id': 'none',
            'dynamic_config_id': f'C1_stale_{staleness}',
            'seed': seed,
            'sim_length_override': 500,
        })

    # C2
    for scenario, scheduler, failure_rate, seed in itertools.product(
            scenarios, schedulers, failure_rates, seeds):
        configs.append({
            'experiment': 'C',
            'scenario_type': scenario,
            'scheduler_type': scheduler,
            'cnt_staleness': 0,
            'cnt_failure_rate': failure_rate,
            'cnt_noise_config': None,
            'cnt_noise_id': 'none',
            'dynamic_config_id': f'C2_fail_{failure_rate}',
            'seed': seed,
            'sim_length_override': 500,
        })

    # C3
    for scenario, scheduler, nc, seed in itertools.product(
            scenarios, schedulers, noise_configs, seeds):
        configs.append({
            'experiment': 'C',
            'scenario_type': scenario,
            'scheduler_type': scheduler,
            'cnt_staleness': 0,
            'cnt_failure_rate': 0.0,
            'cnt_noise_config': {k: v for k, v in nc.items() if k != 'id'},
            'cnt_noise_id': nc['id'],
            'dynamic_config_id': f'C3_noise_{nc["id"]}',
            'seed': seed,
            'sim_length_override': 500,
        })

    return configs


def plot_experiment_c(df, out_dir):
    """Generate Experiment C plots."""
    ensure_dir(out_dir)

    scenarios = ['balanced', 'asymmetric', 'artemis']
    schedulers = ['TwinSync', 'FIFO', 'RoundRobin']
    colors = {'TwinSync': 'blue', 'FIFO': 'red', 'RoundRobin': 'green'}

    # 1. Robustness Curves — Staleness
    c1 = df[df['dynamic_config_id'].str.startswith('C1_')]
    if len(c1) > 0:
        fig, axes_row = plt.subplots(1, 3, figsize=(18, 5))
        for col, scenario in enumerate(scenarios):
            ax = axes_row[col]
            for scheduler in schedulers:
                subset = c1[(c1['scenario_type'] == scenario) & (c1['scheduler_type'] == scheduler)]
                means = subset.groupby('cnt_staleness')['tt_miss_rate'].mean().reset_index()
                stds = subset.groupby('cnt_staleness')['tt_miss_rate'].std().reset_index()
                ax.errorbar(means['cnt_staleness'], means['tt_miss_rate'] * 100,
                           yerr=stds['tt_miss_rate'] * 100,
                           label=scheduler, color=colors[scheduler], marker='o', capsize=3)
            ax.set_xlabel('Staleness (timesteps)', fontsize=10)
            ax.set_ylabel('TT Miss Rate (%)' if col == 0 else '', fontsize=10)
            ax.set_title(f'{scenario}', fontsize=11)
            ax.legend(fontsize=9)
            ax.grid(True, alpha=0.3)
        fig.suptitle('Experiment C1: Robustness to Stale Twin Data', fontsize=13)
        plt.tight_layout(rect=[0, 0, 1, 0.94])
        plt.savefig(os.path.join(out_dir, 'robustness_staleness.pdf'), dpi=150, bbox_inches='tight')
        plt.close()

    # 2. Robustness Curves — Link Failures
    c2 = df[df['dynamic_config_id'].str.startswith('C2_')]
    if len(c2) > 0:
        fig, axes_row = plt.subplots(1, 3, figsize=(18, 5))
        for col, scenario in enumerate(scenarios):
            ax = axes_row[col]
            for scheduler in schedulers:
                subset = c2[(c2['scenario_type'] == scenario) & (c2['scheduler_type'] == scheduler)]
                means = subset.groupby('cnt_failure_rate')['tt_miss_rate'].mean().reset_index()
                stds = subset.groupby('cnt_failure_rate')['tt_miss_rate'].std().reset_index()
                ax.errorbar(means['cnt_failure_rate'], means['tt_miss_rate'] * 100,
                           yerr=stds['tt_miss_rate'] * 100,
                           label=scheduler, color=colors[scheduler], marker='s', capsize=3)
            ax.set_xlabel('Link Failure Rate', fontsize=10)
            ax.set_ylabel('TT Miss Rate (%)' if col == 0 else '', fontsize=10)
            ax.set_title(f'{scenario}', fontsize=11)
            ax.legend(fontsize=9)
            ax.grid(True, alpha=0.3)
        fig.suptitle('Experiment C2: Robustness to Link Failures', fontsize=13)
        plt.tight_layout(rect=[0, 0, 1, 0.94])
        plt.savefig(os.path.join(out_dir, 'robustness_failures.pdf'), dpi=150, bbox_inches='tight')
        plt.close()

    # 3. Robustness Curves — Capacity Noise
    c3 = df[df['dynamic_config_id'].str.startswith('C3_')]
    if len(c3) > 0:
        noise_order = ['mild', 'moderate', 'severe', 'extreme']
        fig, axes_row = plt.subplots(1, 3, figsize=(18, 5))
        for col, scenario in enumerate(scenarios):
            ax = axes_row[col]
            for scheduler in schedulers:
                subset = c3[(c3['scenario_type'] == scenario) & (c3['scheduler_type'] == scheduler)]
                means = subset.groupby('cnt_noise_id')['tt_miss_rate'].mean()
                stds = subset.groupby('cnt_noise_id')['tt_miss_rate'].std()
                x_vals = range(len(noise_order))
                y_vals = [means.get(n, 0) * 100 for n in noise_order]
                y_errs = [stds.get(n, 0) * 100 for n in noise_order]
                ax.errorbar(list(x_vals), y_vals, yerr=y_errs,
                           label=scheduler, color=colors[scheduler], marker='^', capsize=3)
            ax.set_xlabel('Noise Level', fontsize=10)
            ax.set_ylabel('TT Miss Rate (%)' if col == 0 else '', fontsize=10)
            ax.set_title(f'{scenario}', fontsize=11)
            ax.set_xticks(range(len(noise_order)))
            ax.set_xticklabels(noise_order, fontsize=9)
            ax.legend(fontsize=9)
            ax.grid(True, alpha=0.3)
        fig.suptitle('Experiment C3: Robustness to Capacity Fluctuations', fontsize=13)
        plt.tight_layout(rect=[0, 0, 1, 0.94])
        plt.savefig(os.path.join(out_dir, 'robustness_capacity.pdf'), dpi=150, bbox_inches='tight')
        plt.close()

    # 4. Robustness multi-class combined figure (C2 link failures, all classes for TwinSync)
    if len(c2) > 0:
        import shutil
        fig, axes_row = plt.subplots(1, 3, figsize=(15, 5))
        for col, scenario in enumerate(scenarios):
            ax = axes_row[col]
            for scheduler in schedulers:
                subset = c2[(c2['scenario_type'] == scenario) &
                            (c2['scheduler_type'] == scheduler)]
                if len(subset) == 0:
                    continue
                means = subset.groupby('cnt_failure_rate').agg(
                    tt=('tt_miss_rate', 'mean'),
                    rc=('rc_miss_rate', 'mean'),
                    be=('be_miss_rate', 'mean'),
                    tt_std=('tt_miss_rate', 'std'),
                ).reset_index()

                if scheduler == 'TwinSync':
                    ax.errorbar(means['cnt_failure_rate'], means['tt'] * 100,
                                yerr=means['tt_std'] * 100,
                                label='TS: TT', color=colors['TwinSync'],
                                marker='o', capsize=3, linewidth=2)
                    ax.plot(means['cnt_failure_rate'], means['rc'] * 100,
                            '--s', color=colors['TwinSync'],
                            markersize=4, alpha=0.75, label='TS: RC')
                    ax.plot(means['cnt_failure_rate'], means['be'] * 100,
                            ':^', color=colors['TwinSync'],
                            markersize=4, alpha=0.55, label='TS: BE')
                else:
                    stds = subset.groupby('cnt_failure_rate')['tt_miss_rate'].std().reset_index()
                    lbl = 'FIFO: TT' if scheduler == 'FIFO' else 'RR: TT'
                    ax.errorbar(means['cnt_failure_rate'], means['tt'] * 100,
                                yerr=stds['tt_miss_rate'] * 100,
                                label=lbl, color='#aaaaaa',
                                marker='s' if scheduler == 'FIFO' else '^',
                                capsize=2, linewidth=1.0, alpha=0.45)

            ax.set_xlabel('Link Failure Rate', fontsize=10)
            ax.set_ylabel('Miss Rate (%)' if col == 0 else '', fontsize=10)
            ax.set_title(scenario.capitalize(), fontsize=11)
            ax.legend(fontsize=8)
            ax.grid(True, alpha=0.3)

        fig.suptitle('Network Resilience: Multi-Class Traffic Under Link Failures', fontsize=12,
                     fontweight='bold')
        plt.tight_layout(rect=[0, 0, 1, 0.94])

        out_path = os.path.join(out_dir, 'robustness_multiclass.pdf')
        plt.savefig(out_path, dpi=150, bbox_inches='tight')
        plt.close()
        print(f"Saved: {out_path}")

        if os.path.isdir(PAPER_IMAGES_DIR):
            shutil.copy2(out_path, os.path.join(PAPER_IMAGES_DIR, 'robustness_multiclass.pdf'))
            print(f"Copied robustness_multiclass.pdf to paper images")

    # 5. Degradation ratio table
    degradation_rows = []
    for scenario in scenarios:
        for scheduler in schedulers:
            # Pristine = C1_stale_0 or C2_fail_0.0
            pristine = df[(df['scenario_type'] == scenario) &
                         (df['scheduler_type'] == scheduler) &
                         (df['dynamic_config_id'] == 'C1_stale_0')]
            pristine_miss = pristine['tt_miss_rate'].mean() if len(pristine) > 0 else 0

            for config_id in df['dynamic_config_id'].unique():
                if not config_id.startswith('C'):
                    continue
                subset = df[(df['scenario_type'] == scenario) &
                           (df['scheduler_type'] == scheduler) &
                           (df['dynamic_config_id'] == config_id)]
                if len(subset) == 0:
                    continue
                deg_miss = subset['tt_miss_rate'].mean()
                ratio = deg_miss / max(pristine_miss, 1e-6) if pristine_miss > 0 else (0 if deg_miss == 0 else float('inf'))
                degradation_rows.append({
                    'scenario': scenario,
                    'scheduler': scheduler,
                    'config': config_id,
                    'pristine_miss': pristine_miss,
                    'degraded_miss': deg_miss,
                    'ratio': ratio
                })
    pd.DataFrame(degradation_rows).to_csv(os.path.join(out_dir, 'degradation_table.csv'), index=False)


# ===========================================================================
# Experiment D — Scalability Tests
# ===========================================================================

def generate_scale_scenario(mission_count, sim_length=200, topology_seed=42):
    """Generate a scenario with a specific number of missions for Experiment D."""
    import random as rng

    # Fix topology seed
    rng.seed(topology_seed)
    np.random.seed(topology_seed)

    K = mission_count
    config = {
        'num_earth_optical': max(2, K // 3),
        'num_earth_radio': max(3, K // 2),
        'num_moon_radio': max(2, K // 3),
        'num_ground_stations': max(6, K),
        'link_capacities': {1: 2.0, 2: 3.0, 3: 5.0},
    }

    from math import pi
    mission_configs = []
    for i in range(K):
        angle = 2 * pi * i / K
        mission_configs.append({
            'name': f'Mission_{i}',
            'location': (RADIUS_MOON + 1, angle),
            'num_assets': 1,
            'capabilities': [1, 2] if i % 3 != 0 else [1, 2, 3]
        })

    generator = CNTGenerator(sim_length=sim_length, time_step=1.0, config=config)
    generator.generate_satellites()
    generator.generate_missions(mission_configs)
    generator.generate_link_matrices()
    generator.generate_routes()
    generator.allocate_bandwidth()

    # Export to temp directory
    scenario_dir = os.path.join(SIM_DIR, "scenarios", f"scale_{K}")
    ensure_dir(scenario_dir)
    generator.export_cnt_data(os.path.join(scenario_dir, "CNT"))

    # Create scenario_info.json
    total_bw = 0
    if generator.allocations_over_time:
        first_alloc = generator.allocations_over_time[0]
        if isinstance(first_alloc, dict):
            for m_name, m_alloc in first_alloc.items():
                if isinstance(m_alloc, dict):
                    total_bw += m_alloc.get('allocated', 0)

    scenario_info = {
        'scenario_name': f'Scale Test K={K}',
        'description': f'{K} missions for scalability testing',
        'total_capacity': max(total_bw, K * 2.0),
        'expected_demand': K * 6.0,
        'missions': []
    }

    for mc in mission_configs:
        scenario_info['missions'].append({
            'name': mc['name'],
            'assets': [f'Asset_{mc["name"]}'],
            'traffic_rates': {'TT': 1.5, 'RC': 2.0, 'BE': 1.5}
        })

    with open(os.path.join(scenario_dir, 'scenario_info.json'), 'w') as f:
        json.dump(scenario_info, f, indent=2)

    return scenario_dir


def build_experiment_d_configs(seeds):
    """Build configs for Experiment D, generating scale scenarios first."""
    mission_counts = [2, 4, 6, 8, 10, 15, 20, 30, 40, 50]
    schedulers = ['TwinSync', 'FIFO', 'RoundRobin']
    topology_seed = 42

    # Generate scenarios
    print("Generating scale test scenarios for Experiment D...")
    scenario_paths = {}
    for K in mission_counts:
        try:
            path = generate_scale_scenario(K, sim_length=200, topology_seed=topology_seed)
            scenario_paths[K] = path
            print(f"  Generated scenario for K={K} missions")
        except Exception as e:
            print(f"  WARNING: Failed to generate scenario for K={K}: {e}")

    configs = []
    for K, scheduler, seed in itertools.product(mission_counts, schedulers, seeds):
        if K not in scenario_paths:
            continue
        configs.append({
            'experiment': 'D',
            'scenario_type': f'scale_{K}',
            'scheduler_type': scheduler,
            'seed': seed,
            'sim_length_override': 200,
            'scenario_path_override': scenario_paths[K],
            'mission_count': K,
        })
    return configs


def plot_experiment_d(df, out_dir):
    """Generate Experiment D plots."""
    ensure_dir(out_dir)

    schedulers = ['TwinSync', 'FIFO', 'RoundRobin']
    colors = {'TwinSync': 'blue', 'FIFO': 'red', 'RoundRobin': 'green'}

    # 1. Scaling Plot
    fig, ax = plt.subplots(figsize=(10, 6))
    for scheduler in schedulers:
        subset = df[df['scheduler_type'] == scheduler]
        means = subset.groupby('mission_count')['mean_sched_time'].mean().reset_index()
        stds = subset.groupby('mission_count')['mean_sched_time'].std().reset_index()
        ax.errorbar(means['mission_count'], means['mean_sched_time'] * 1000,
                   yerr=stds['mean_sched_time'] * 1000,
                   label=scheduler, color=colors[scheduler], marker='o', capsize=3)

    ax.set_xlabel('Mission Count (K)', fontsize=11)
    ax.set_ylabel('Mean Per-Timestep Scheduling Time (ms)', fontsize=11)
    ax.set_title('Experiment D: Scheduling Time vs Mission Count', fontsize=13)
    ax.legend(fontsize=10)
    ax.grid(True, alpha=0.3)
    ax.set_yscale('log')
    plt.tight_layout()
    plt.savefig(os.path.join(out_dir, 'scaling_plot.pdf'), dpi=150, bbox_inches='tight')
    plt.close()

    # 2. Miss Rate vs Scale
    fig, ax = plt.subplots(figsize=(10, 6))
    for scheduler in schedulers:
        subset = df[df['scheduler_type'] == scheduler]
        means = subset.groupby('mission_count')['tt_miss_rate'].mean().reset_index()
        stds = subset.groupby('mission_count')['tt_miss_rate'].std().reset_index()
        ax.errorbar(means['mission_count'], means['tt_miss_rate'] * 100,
                   yerr=stds['tt_miss_rate'] * 100,
                   label=scheduler, color=colors[scheduler], marker='s', capsize=3)

    ax.set_xlabel('Mission Count (K)', fontsize=11)
    ax.set_ylabel('TT Miss Rate (%)', fontsize=11)
    ax.set_title('Experiment D: TT Miss Rate vs Mission Count', fontsize=13)
    ax.legend(fontsize=10)
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(os.path.join(out_dir, 'missrate_vs_scale.pdf'), dpi=150, bbox_inches='tight')
    plt.close()

    # 3. Complexity Validation Table
    complexity_rows = []
    for scheduler in schedulers:
        subset = df[df['scheduler_type'] == scheduler]
        means = subset.groupby('mission_count')['mean_sched_time'].mean()
        ks = sorted(means.index)
        for i in range(1, len(ks)):
            k1, k2 = ks[i-1], ks[i]
            t1, t2 = means[k1], means[k2]
            if t1 > 0:
                empirical_ratio = t2 / t1
                # Theoretical O(K log K + K*R) where R ~ K
                # So O(K^2 + K log K) ≈ O(K^2) for large K
                theoretical_ratio = (k2**2) / (k1**2) if k1 > 0 else 0
                complexity_rows.append({
                    'scheduler': scheduler,
                    'K1': k1, 'K2': k2,
                    'T1_ms': t1 * 1000, 'T2_ms': t2 * 1000,
                    'empirical_ratio': empirical_ratio,
                    'theoretical_ratio_K2': theoretical_ratio,
                })
    pd.DataFrame(complexity_rows).to_csv(os.path.join(out_dir, 'complexity_table.csv'), index=False)

    # 4. Feasibility Table
    feasibility_rows = []
    for scheduler in schedulers:
        subset = df[df['scheduler_type'] == scheduler]
        for K in sorted(subset['mission_count'].unique()):
            k_subset = subset[subset['mission_count'] == K]
            mean_time = k_subset['mean_sched_time'].mean()
            p95_time = k_subset['p95_sched_time'].mean()
            max_time = k_subset['max_sched_time'].mean()
            feasibility_rows.append({
                'scheduler': scheduler,
                'mission_count': K,
                'mean_ms': mean_time * 1000,
                'p95_ms': p95_time * 1000,
                'max_ms': max_time * 1000,
                'realtime_feasible': 'Yes' if p95_time < 1.0 else 'No'
            })
    pd.DataFrame(feasibility_rows).to_csv(os.path.join(out_dir, 'feasibility_table.csv'), index=False)

    # 5. Combined scaling figure: timing + miss rate side-by-side
    import shutil
    fig, axes_pair = plt.subplots(1, 2, figsize=(12, 5))

    ax = axes_pair[0]
    for scheduler in schedulers:
        subset = df[df['scheduler_type'] == scheduler]
        means = subset.groupby('mission_count')['mean_sched_time'].mean().reset_index()
        stds = subset.groupby('mission_count')['mean_sched_time'].std().reset_index()
        ax.errorbar(means['mission_count'], means['mean_sched_time'] * 1000,
                    yerr=stds['mean_sched_time'] * 1000,
                    label=scheduler, color=colors[scheduler], marker='o', capsize=3)
    ax.set_xlabel('Mission Count (K)', fontsize=11)
    ax.set_ylabel('Mean Per-Timestep Time (ms)', fontsize=11)
    ax.set_title('(a) Scheduling Overhead', fontsize=11)
    ax.legend(fontsize=10)
    ax.grid(True, alpha=0.3)
    ax.set_yscale('log')

    ax = axes_pair[1]
    for scheduler in schedulers:
        subset = df[df['scheduler_type'] == scheduler]
        means = subset.groupby('mission_count')['tt_miss_rate'].mean().reset_index()
        stds = subset.groupby('mission_count')['tt_miss_rate'].std().reset_index()
        ax.errorbar(means['mission_count'], means['tt_miss_rate'] * 100,
                    yerr=stds['tt_miss_rate'] * 100,
                    label=scheduler, color=colors[scheduler], marker='s', capsize=3)
    ax.set_xlabel('Mission Count (K)', fontsize=11)
    ax.set_ylabel('TT Miss Rate (%)', fontsize=11)
    ax.set_title('(b) TT Deadline Violations at Scale', fontsize=11)
    ax.legend(fontsize=10)
    ax.grid(True, alpha=0.3)

    plt.tight_layout()

    out_path = os.path.join(out_dir, 'scaling_combined.pdf')
    plt.savefig(out_path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"Saved: {out_path}")

    if os.path.isdir(PAPER_IMAGES_DIR):
        shutil.copy2(out_path, os.path.join(PAPER_IMAGES_DIR, 'scaling_combined.pdf'))
        print(f"Copied scaling_combined.pdf to paper images")


# ===========================================================================
# Main orchestration
# ===========================================================================

def plot_only_from_cache():
    """Regenerate all plots from cached CSV files without re-running simulations."""
    print("\nRegenerating plots from cached CSV files...")

    dir_a = ensure_dir(os.path.join(OUTPUT_BASE, "experiment_a"))
    dir_b = ensure_dir(os.path.join(OUTPUT_BASE, "experiment_b"))
    dir_c = ensure_dir(os.path.join(OUTPUT_BASE, "experiment_c"))
    dir_d = ensure_dir(os.path.join(OUTPUT_BASE, "experiment_d"))

    def load_csv(path):
        if os.path.exists(path):
            df = pd.read_csv(path)
            print(f"  Loaded {len(df)} rows from {os.path.relpath(path, SIM_DIR)}")
            return df
        print(f"  WARNING: {path} not found, skipping")
        return pd.DataFrame()

    df_a = load_csv(os.path.join(dir_a, 'raw_results.csv'))
    df_b = load_csv(os.path.join(dir_b, 'raw_results.csv'))
    df_c = load_csv(os.path.join(dir_c, 'raw_results.csv'))
    df_d = load_csv(os.path.join(dir_d, 'raw_results.csv'))

    if len(df_a) > 0:
        print("\nPlotting Experiment A...")
        plot_experiment_a(df_a, dir_a)

    if len(df_b) > 0:
        print("\nPlotting Experiment B...")
        plot_experiment_b(df_b, dir_b)

    if len(df_a) > 0 or len(df_b) > 0:
        print("\nPlotting cross-class combined figure...")
        plot_cross_class_combined(
            df_a if len(df_a) > 0 else None,
            df_b if len(df_b) > 0 else None,
            dir_b,
        )

    if len(df_c) > 0:
        print("\nPlotting Experiment C...")
        plot_experiment_c(df_c, dir_c)

    if len(df_d) > 0:
        print("\nPlotting Experiment D...")
        plot_experiment_d(df_d, dir_d)

    print("\nPlot regeneration complete!")


def run_experiment_batch(configs, experiment_name, n_workers=None):
    """Run a batch of experiment configs in parallel."""
    if n_workers is None:
        n_workers = cpu_count()

    n_configs = len(configs)
    print(f"\n{'='*60}")
    print(f"Running {experiment_name}: {n_configs} configurations with {n_workers} workers")
    print(f"{'='*60}")

    t_start = time.time()

    if n_workers == 1 or n_configs <= 10:
        results = [run_single_config(c) for c in configs]
    else:
        # Use large chunksize to reduce IPC overhead
        chunksize = max(1, n_configs // (n_workers * 4))
        with Pool(processes=n_workers) as pool:
            results = list(pool.imap_unordered(run_single_config, configs, chunksize=chunksize))

    t_elapsed = time.time() - t_start
    print(f"{experiment_name} completed in {t_elapsed:.1f}s ({t_elapsed/60:.1f} min)")

    # Check for errors
    errors = [r for r in results if 'error' in r]
    if errors:
        print(f"  WARNING: {len(errors)} / {n_configs} runs failed")
        for e in errors[:5]:
            print(f"    Error: {e['error']}")
            if 'traceback' in e:
                print(f"    {e['traceback'][-200:]}")

    good_results = [r for r in results if 'error' not in r]
    print(f"  Successful: {len(good_results)} / {n_configs}")

    return good_results


def run_smoke_test():
    """Quick smoke test with minimal configs to verify everything works."""
    print("\n" + "="*60)
    print("SMOKE TEST")
    print("="*60)

    # Test Experiment A: 1 scenario, 1 scheduler, 2 rate mults, 2 deadlines, 2 seeds
    configs = []
    for mult in [1.0, 5.0]:
        for deadline in [3.0, 1.5]:
            for seed in [100, 200]:
                configs.append({
                    'experiment': 'A',
                    'scenario_type': 'balanced',
                    'scheduler_type': 'TwinSync',
                    'tt_rate_mult': mult,
                    'tt_deadline': deadline,
                    'seed': seed,
                    'sim_length_override': 50,
                })

    results_a = run_experiment_batch(configs, "Smoke Test A", n_workers=2)

    # Test Experiment B: 1 config
    configs_b = [{
        'experiment': 'B',
        'scenario_type': 'balanced',
        'scheduler_type': 'TwinSync',
        'dynamic_config': get_dynamic_configs()['B1-a'],
        'dynamic_config_id': 'B1-a',
        'seed': 100,
        'sim_length_override': 50,
    }]
    results_b = run_experiment_batch(configs_b, "Smoke Test B", n_workers=1)

    # Test Experiment C: 1 staleness config
    configs_c = [{
        'experiment': 'C',
        'scenario_type': 'balanced',
        'scheduler_type': 'TwinSync',
        'cnt_staleness': 10,
        'dynamic_config_id': 'C1_stale_10',
        'seed': 100,
        'sim_length_override': 50,
    }]
    results_c = run_experiment_batch(configs_c, "Smoke Test C", n_workers=1)

    # Test Experiment D: generate 1 scale scenario
    print("\nSmoke Test D: Generating scale scenario K=2...")
    try:
        path = generate_scale_scenario(2, sim_length=50, topology_seed=42)
        configs_d = [{
            'experiment': 'D',
            'scenario_type': 'scale_2',
            'scheduler_type': 'TwinSync',
            'seed': 100,
            'sim_length_override': 50,
            'scenario_path_override': path,
            'mission_count': 2,
        }]
        results_d = run_experiment_batch(configs_d, "Smoke Test D", n_workers=1)
    except Exception as e:
        print(f"Smoke Test D failed: {e}")
        traceback.print_exc()
        results_d = []

    all_results = results_a + results_b + results_c + results_d
    n_ok = sum(1 for r in all_results if 'error' not in r)
    n_total = len(all_results)
    print(f"\nSmoke test: {n_ok}/{n_total} runs succeeded")

    if n_ok < n_total:
        print("SMOKE TEST FAILED — check errors above before proceeding")
        return False
    else:
        print("SMOKE TEST PASSED")
        return True


def run_full_experiments(n_seeds=30):
    """Run all four experiments with full parameter sweeps."""
    seeds = list(range(1000, 1000 + n_seeds))
    n_workers = cpu_count()

    # Experiment A
    configs_a = build_experiment_a_configs(seeds)
    results_a = run_experiment_batch(configs_a, "Experiment A — TT Saturation", n_workers)

    # Experiment B
    configs_b = build_experiment_b_configs(seeds)
    results_b = run_experiment_batch(configs_b, "Experiment B — Dynamic Traffic", n_workers)

    # Experiment C
    configs_c = build_experiment_c_configs(seeds)
    results_c = run_experiment_batch(configs_c, "Experiment C — Network Degradation", n_workers)

    # Experiment D (fewer seeds as per plan)
    seeds_d = seeds[:3]  # 3 seeds for timing experiments
    configs_d = build_experiment_d_configs(seeds_d)
    results_d = run_experiment_batch(configs_d, "Experiment D — Scalability", n_workers)

    return results_a, results_b, results_c, results_d


def save_and_plot_results(results_a, results_b, results_c, results_d):
    """Save CSV files and generate all plots."""
    # Create output directories
    dir_a = ensure_dir(os.path.join(OUTPUT_BASE, "experiment_a"))
    dir_b = ensure_dir(os.path.join(OUTPUT_BASE, "experiment_b"))
    dir_c = ensure_dir(os.path.join(OUTPUT_BASE, "experiment_c"))
    dir_d = ensure_dir(os.path.join(OUTPUT_BASE, "experiment_d"))
    dir_summary = ensure_dir(os.path.join(OUTPUT_BASE, "summary"))

    # Convert to DataFrames and save
    df_a = pd.DataFrame(results_a) if results_a else pd.DataFrame()
    df_b = pd.DataFrame(results_b) if results_b else pd.DataFrame()
    df_c = pd.DataFrame(results_c) if results_c else pd.DataFrame()
    df_d = pd.DataFrame(results_d) if results_d else pd.DataFrame()

    if len(df_a) > 0:
        df_a.to_csv(os.path.join(dir_a, 'raw_results.csv'), index=False)
        print(f"Experiment A: {len(df_a)} results saved")
        plot_experiment_a(df_a, dir_a)

    if len(df_b) > 0:
        df_b.to_csv(os.path.join(dir_b, 'raw_results.csv'), index=False)
        print(f"Experiment B: {len(df_b)} results saved")
        plot_experiment_b(df_b, dir_b)

    # Cross-class combined figure (requires both A and B data)
    if len(df_a) > 0 or len(df_b) > 0:
        plot_cross_class_combined(
            df_a if len(df_a) > 0 else None,
            df_b if len(df_b) > 0 else None,
            dir_b,
        )

    if len(df_c) > 0:
        df_c.to_csv(os.path.join(dir_c, 'raw_results.csv'), index=False)
        print(f"Experiment C: {len(df_c)} results saved")
        plot_experiment_c(df_c, dir_c)

    if len(df_d) > 0:
        df_d.to_csv(os.path.join(dir_d, 'raw_results.csv'), index=False)
        print(f"Experiment D: {len(df_d)} results saved")
        plot_experiment_d(df_d, dir_d)

    # Combined dataset
    all_df = pd.concat([df_a, df_b, df_c, df_d], ignore_index=True)
    all_df.to_csv(os.path.join(dir_summary, 'all_results.csv'), index=False)
    print(f"\nTotal: {len(all_df)} results saved to summary/all_results.csv")

    # Generate experiment summary
    summary_lines = []
    summary_lines.append("Limit-Breaking Experiment Summary")
    summary_lines.append("=" * 50)
    summary_lines.append(f"Total runs: {len(all_df)}")
    summary_lines.append(f"  Experiment A: {len(df_a)}")
    summary_lines.append(f"  Experiment B: {len(df_b)}")
    summary_lines.append(f"  Experiment C: {len(df_c)}")
    summary_lines.append(f"  Experiment D: {len(df_d)}")
    summary_lines.append("")

    if len(df_a) > 0:
        summary_lines.append("Experiment A Highlights:")
        for scheduler in ['TwinSync', 'FIFO', 'RoundRobin']:
            subset = df_a[df_a['scheduler_type'] == scheduler]
            zero_miss = subset[subset['tt_miss_rate'] == 0]
            pct = len(zero_miss) / len(subset) * 100 if len(subset) > 0 else 0
            summary_lines.append(f"  {scheduler}: {pct:.1f}% of configs with zero TT misses")
        summary_lines.append("")

    if len(df_c) > 0:
        summary_lines.append("Experiment C Highlights:")
        pristine = df_c[df_c['dynamic_config_id'] == 'C1_stale_0']
        for scheduler in ['TwinSync', 'FIFO', 'RoundRobin']:
            p_miss = pristine[pristine['scheduler_type'] == scheduler]['tt_miss_rate'].mean()
            worst = df_c[(df_c['scheduler_type'] == scheduler)]['tt_miss_rate'].max()
            summary_lines.append(f"  {scheduler}: pristine TT miss={p_miss*100:.1f}%, worst={worst*100:.1f}%")
        summary_lines.append("")

    summary_text = "\n".join(summary_lines)
    with open(os.path.join(dir_summary, 'experiment_summary.txt'), 'w') as f:
        f.write(summary_text)
    print(summary_text)


# ===========================================================================
# Entry point
# ===========================================================================

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='TwinSync Limit-Breaking Experiments')
    parser.add_argument('--smoke', action='store_true', help='Run smoke test only')
    parser.add_argument('--plot-only', action='store_true',
                        help='Regenerate all plots from cached CSV files without re-running simulations')
    parser.add_argument('--seeds', type=int, default=30, help='Number of seeds per config')
    parser.add_argument('--experiment', type=str, default='all',
                       help='Run specific experiment: A, B, C, D, or all')
    args = parser.parse_args()

    os.chdir(SIM_DIR)
    ensure_dir(OUTPUT_BASE)

    if args.plot_only:
        plot_only_from_cache()
        sys.exit(0)

    if args.smoke:
        success = run_smoke_test()
        sys.exit(0 if success else 1)
    else:
        # Run full experiments
        if args.experiment == 'all':
            results_a, results_b, results_c, results_d = run_full_experiments(n_seeds=args.seeds)
        else:
            seeds = list(range(1000, 1000 + args.seeds))
            n_workers = cpu_count()
            results_a, results_b, results_c, results_d = [], [], [], []

            if 'A' in args.experiment.upper():
                configs = build_experiment_a_configs(seeds)
                results_a = run_experiment_batch(configs, "Experiment A", n_workers)
            if 'B' in args.experiment.upper():
                configs = build_experiment_b_configs(seeds)
                results_b = run_experiment_batch(configs, "Experiment B", n_workers)
            if 'C' in args.experiment.upper():
                configs = build_experiment_c_configs(seeds)
                results_c = run_experiment_batch(configs, "Experiment C", n_workers)
            if 'D' in args.experiment.upper():
                seeds_d = seeds[:3]
                configs = build_experiment_d_configs(seeds_d)
                results_d = run_experiment_batch(configs, "Experiment D", n_workers)

        save_and_plot_results(results_a, results_b, results_c, results_d)
        print("\nAll experiments complete!")
