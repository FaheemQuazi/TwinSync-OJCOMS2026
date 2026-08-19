#!/usr/bin/env python3
"""
CNT Snapshot Visualization
---------------------------
Renders a publication-quality snapshot of the Cislunar Network Twin at a
given timestep, showing:
  - Earth and Moon bodies
  - Satellite positions (by role)
  - Active links colored by technology (Radio / Mixed / Optical)
  - Allocated route paths

Usage:
    python visualization/cnt_snapshot.py --scenario artemis --time 0
    python visualization/cnt_snapshot.py --scenario balanced --time 50 --output fig.pdf
"""

import argparse
import json
import math
import re
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

# ── Constants ────────────────────────────────────────────────────────
EARTH_CENTER = np.array([0.0, 0.0])
MOON_CENTER = np.array([-384.4, 0.0])
EARTH_RADIUS = 63.78135
MOON_RADIUS = 17.2209645

# Technology colors and labels  (1=Radio, 2=Mixed, 3=Optical)
TECH_COLORS = {"1": "#d62728", "2": "#2ca02c", "3": "#1f77b4"}
TECH_LABELS = {"1": "Radio (2 Mbps)", "2": "Mixed (3 Mbps)", "3": "Optical (5 Mbps)"}

# Node styling by role
NODE_STYLES = {
    "E_O": {"color": "#1f77b4", "marker": "o", "size": 2.5, "label": "Earth Optical Sat"},
    "E_R": {"color": "#2ca02c", "marker": "o", "size": 2.5, "label": "Earth Radio Sat"},
    "M_R": {"color": "#d62728", "marker": "o", "size": 2.5, "label": "Moon Radio Sat"},
    "BE":  {"color": "#9467bd", "marker": "^", "size": 3.0, "label": "Ground Station"},
    "BM_": {"color": "#ff7f0e", "marker": "s", "size": 3.5, "label": "Moon Mission"},
}


# ── Data Loading ─────────────────────────────────────────────────────

def parse_satellite_file(path: Path):
    """Parse CNT_satellites.txt → list of dicts."""
    satellites = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(",", 1)
            name = parts[0]
            rest = parts[1] if len(parts) > 1 else ""

            orbit_match = re.search(r"ORBIT\[([^,]+),([^\]]+)\]", rest)
            body_match = re.search(r"BODY\[(\w+),\(([^,]+),\s*([^)]+)\),([^\]]+)\]", rest)

            if orbit_match and body_match:
                semi_major = float(orbit_match.group(1))
                initial_angle = float(orbit_match.group(2))
                body_x = float(body_match.group(2))
                body_y = float(body_match.group(3))

                # Angular velocity and tech field(s)
                after_body = rest.split("]")
                remaining = [p.strip() for p in after_body[-1].split(",") if p.strip()]
                angular_velocity = float(remaining[0]) if remaining else 0.0
                tech_str = remaining[1] if len(remaining) > 1 else "1"

                # Determine node type
                node_type = "unknown"
                for prefix in NODE_STYLES:
                    if name.startswith(prefix):
                        node_type = prefix
                        break

                satellites.append({
                    "name": name,
                    "semi_major": semi_major,
                    "initial_angle": initial_angle,
                    "angular_velocity": angular_velocity,
                    "body_center": np.array([body_x, body_y]),
                    "type": node_type,
                    "techs": set(tech_str.split("|")),
                })
    return satellites


def get_position(sat: dict, time: float) -> np.ndarray:
    angle = sat["initial_angle"] + sat["angular_velocity"] * time
    return sat["body_center"] + sat["semi_major"] * np.array([math.cos(angle), math.sin(angle)])


def get_link_tech(sat_a: dict, sat_b: dict) -> str:
    """Return the best common technology between two satellites."""
    common = sat_a["techs"] & sat_b["techs"]
    if not common:
        return "1"  # fallback
    # Prefer highest: 3 > 2 > 1
    for t in ["3", "2", "1"]:
        if t in common:
            return t
    return "1"


def parse_link_matrix(path: Path, time_idx: int):
    matrices, current = [], []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line == "# ENDMAT":
                matrices.append(np.array(current))
                current = []
                continue
            if line.startswith("#") or not line:
                continue
            current.append([int(x) for x in line.split(",")])
    if current:
        matrices.append(np.array(current))
    idx = min(time_idx, len(matrices) - 1) if matrices else 0
    return matrices[idx] if matrices else None


def load_allocations(path: Path, time_idx: int):
    with open(path) as f:
        allocs = json.load(f)
    idx = min(time_idx, len(allocs) - 1) if allocs else 0
    return allocs[idx] if allocs else None


def load_summary(path: Path):
    with open(path) as f:
        return json.load(f)


# ── Drawing ──────────────────────────────────────────────────────────

def draw_cnt_snapshot(scenario_dir: Path, time_idx: int, output_path: Path = None,
                      show_links: bool = True, show_routes: bool = True):

    satellites = parse_satellite_file(scenario_dir / "CNT_satellites.txt")
    link_matrix = parse_link_matrix(scenario_dir / "CNT_links.txt", time_idx)
    allocation = load_allocations(scenario_dir / "CNT_allocations.json", time_idx)
    summary = load_summary(scenario_dir / "CNT_summary.json")

    actual_time = time_idx * summary.get("time_step", 1.0)
    positions = [get_position(s, actual_time) for s in satellites]

    # Collect route edges
    route_edges = set()
    if show_routes and allocation and "missions" in allocation:
        for m_alloc in allocation["missions"].values():
            for route in m_alloc.get("routes", []):
                for k in range(len(route["path"]) - 1):
                    route_edges.add(tuple(sorted((route["path"][k], route["path"][k + 1]))))

    # ── Figure ──
    fig, ax = plt.subplots(1, 1, figsize=(7.16, 3.0), dpi=300)
    ax.set_aspect("equal")

    # Bodies
    ax.add_patch(plt.Circle(EARTH_CENTER, EARTH_RADIUS, facecolor="#4a90d9",
                            alpha=0.2, linewidth=0.5, edgecolor="#2c5f9e"))
    ax.add_patch(plt.Circle(MOON_CENTER, MOON_RADIUS, facecolor="#b0b0b0",
                            alpha=0.25, linewidth=0.5, edgecolor="#707070"))
    ax.text(*EARTH_CENTER, "Earth", ha="center", va="center",
            fontsize=6, fontweight="bold", color="#2c5f9e", zorder=5)
    ax.text(MOON_CENTER[0], MOON_CENTER[1] - MOON_RADIUS - 4, "Moon",
            ha="center", va="top", fontsize=6, fontweight="bold", color="#505050", zorder=5)

    # Inactive links (faint)
    if show_links and link_matrix is not None:
        n = len(satellites)
        for i in range(n):
            for j in range(i + 1, n):
                if link_matrix[i][j] and tuple(sorted((i, j))) not in route_edges:
                    p1, p2 = positions[i], positions[j]
                    ax.plot([p1[0], p2[0]], [p1[1], p2[1]],
                            color="#e0e0e0", linewidth=0.1, alpha=0.15, zorder=1)

    # Route edges colored by link technology
    techs_used = set()
    if show_routes and allocation and "missions" in allocation:
        for m_alloc in allocation["missions"].values():
            for route in m_alloc.get("routes", []):
                path = route["path"]
                bw = route["bandwidth"]
                lw = max(0.5, min(1.5, bw / 2.5))
                for k in range(len(path) - 1):
                    si, sj = path[k], path[k + 1]
                    if si < len(positions) and sj < len(positions):
                        tech = get_link_tech(satellites[si], satellites[sj])
                        techs_used.add(tech)
                        p1, p2 = positions[si], positions[sj]
                        ax.plot([p1[0], p2[0]], [p1[1], p2[1]],
                                color=TECH_COLORS[tech], linewidth=lw,
                                alpha=0.7, zorder=2, solid_capstyle="round")

    # Nodes
    for sat, pos in zip(satellites, positions):
        style = NODE_STYLES.get(sat["type"], NODE_STYLES["E_R"])
        ax.plot(pos[0], pos[1], marker=style["marker"], color=style["color"],
                markersize=style["size"], markeredgecolor="white",
                markeredgewidth=0.2, zorder=4)

    # ── Legend ──
    handles = []
    # Node types
    for key in ["BM_", "M_R", "E_O", "E_R", "BE"]:
        s = NODE_STYLES[key]
        if any(sat["type"] == key for sat in satellites):
            handles.append(plt.Line2D([0], [0], marker=s["marker"], color="w",
                                      markerfacecolor=s["color"], markersize=3,
                                      label=s["label"], linewidth=0))
    # Technology links
    for t in ["3", "2", "1"]:
        if t in techs_used:
            handles.append(plt.Line2D([0], [0], color=TECH_COLORS[t],
                                      linewidth=1.2, label=TECH_LABELS[t]))
    # Inactive link
    handles.append(plt.Line2D([0], [0], color="#d0d0d0", linewidth=0.6,
                              label="Unused link"))

    ax.legend(handles=handles, fontsize=5, loc="upper center",
              bbox_to_anchor=(0.5, -0.18), ncol=len(handles),
              framealpha=0.9, edgecolor="#cccccc", borderpad=0.3,
              handlelength=1.2, handletextpad=0.3, labelspacing=0.2,
              columnspacing=0.8)

    # Axes
    all_x = [p[0] for p in positions]
    all_y = [p[1] for p in positions]
    margin = 30
    ax.set_xlim(min(all_x) - margin, max(all_x) + margin)
    ax.set_ylim(min(all_y) - margin, max(all_y) + margin)
    ax.set_xlabel("x (×1000 km)", fontsize=7)
    ax.set_ylabel("y (×1000 km)", fontsize=7)
    ax.tick_params(axis="both", labelsize=6)
    ax.set_title(f"Cislunar Network Twin — t = {actual_time:.0f} s",
                 fontsize=8, fontweight="bold")

    plt.tight_layout(pad=0.5)
    plt.subplots_adjust(bottom=0.25)

    if output_path:
        fig.savefig(output_path, bbox_inches="tight", dpi=300)
        print(f"Saved: {output_path}")
    else:
        plt.show()
    plt.close(fig)


# ── CLI ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Render a CNT snapshot at a given timestep")
    parser.add_argument("--scenario", required=True,
                        help="Scenario name (e.g., artemis, balanced, asymmetric)")
    parser.add_argument("--time", type=int, default=0,
                        help="Timestep index to visualize (default: 0)")
    parser.add_argument("--output", type=str, default=None,
                        help="Output file path (e.g., cnt_snapshot.pdf)")
    parser.add_argument("--no-links", action="store_true",
                        help="Hide background link edges")
    parser.add_argument("--no-routes", action="store_true",
                        help="Hide route allocations")

    args = parser.parse_args()
    sim_root = Path(__file__).resolve().parent.parent
    scenario_dir = sim_root / "scenarios" / args.scenario

    if not scenario_dir.exists():
        print(f"Error: scenario directory not found: {scenario_dir}")
        sys.exit(1)

    draw_cnt_snapshot(
        scenario_dir=scenario_dir,
        time_idx=args.time,
        output_path=Path(args.output) if args.output else None,
        show_links=not args.no_links,
        show_routes=not args.no_routes,
    )


if __name__ == "__main__":
    main()
