"""
Generate CNTGen network topology files for each scenario used in run_realistic_scenarios.py
Creates organized folder structure with all CNT data files for each scenario
"""

import os
import sys
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from modules.CNTGen import CNTGenerator
from modules.BSComps import RADIUS_MOON, pi
import json

def create_balanced_scenario():
    """Generate CNTGen network for Balanced Dual-Mission scenario"""
    print("\nGenerating Balanced Dual-Mission scenario...")
    
    # Configuration matching the balanced scenario requirements
    config = {
        'num_earth_optical': 2,      # Optical satellites for high bandwidth
        'num_earth_radio': 3,         # Radio satellites for redundancy
        'num_moon_radio': 2,          # Lunar orbit relays
        'num_ground_stations': 6,     # Global DSN coverage
        'link_capacities': {
            1: 2.0,   # Radio: Limited (matching severe congestion)
            2: 3.0,   # Mixed: Moderate 
            3: 5.0    # Optical: Higher (but still limited for congestion)
        }
    }
    
    # Two identical missions with equal traffic profiles
    mission_configs = [
        {
            'name': 'Apollo_Redux',
            'location': (RADIUS_MOON + 1, 0),  # Equatorial position
            'num_assets': 2,  # Rover_A and Lander_A
            'capabilities': [1, 2]  # Radio and mixed capabilities
        },
        {
            'name': 'Chang_E_Next',
            'location': (RADIUS_MOON + 1, pi/2),  # 90 degrees offset
            'num_assets': 2,  # Rover_C and Lander_C
            'capabilities': [1, 2]  # Same capabilities
        }
    ]
    
    # Create CNT generator
    generator = CNTGenerator(sim_length=500, time_step=1.0, config=config)
    generator.generate_satellites()
    generator.generate_missions(mission_configs)
    generator.generate_link_matrices()
    generator.generate_routes()
    generator.allocate_bandwidth()
    
    # Create scenario directory
    os.makedirs("scenarios/balanced", exist_ok=True)
    
    # Export with scenario-specific prefix
    generator.export_cnt_data("scenarios/balanced/CNT")
    
    # Add scenario description
    scenario_info = {
        'scenario_name': 'Balanced Dual-Mission',
        'description': '2 identical missions with equal traffic profiles',
        'total_capacity': 4.0,  # Mbps - matching run_realistic_scenarios.py
        'expected_demand': 13.0,  # Mbps - severe congestion
        'missions': [
            {
                'name': 'Apollo_Redux',
                'assets': ['Rover_A', 'Lander_A'],
                'traffic_rates': {'TT': 1.5, 'RC': 3.0, 'BE': 2.0}
            },
            {
                'name': 'Chang_E_Next',
                'assets': ['Rover_C', 'Lander_C'],
                'traffic_rates': {'TT': 1.5, 'RC': 3.0, 'BE': 2.0}
            }
        ],
        'num_satellites': len(generator.satellites),
        'num_routes': len(generator.routes_over_time[0]) if generator.routes_over_time else 0
    }
    
    with open("scenarios/balanced/scenario_info.json", 'w') as f:
        json.dump(scenario_info, f, indent=2)
    
    print(f"  ✓ Generated {len(generator.satellites)} satellites")
    print(f"  ✓ Generated {len(generator.missions)} missions")
    print(f"  ✓ Found {scenario_info['num_routes']} initial routes")
    print(f"  ✓ Files saved to scenarios/balanced/")
    
    return generator

def create_asymmetric_scenario():
    """Generate CNTGen network for Asymmetric Geographic scenario"""
    print("\nGenerating Asymmetric Geographic scenario...")
    
    config = {
        'num_earth_optical': 3,      # More optical for high data rates
        'num_earth_radio': 2,         # Less radio
        'num_moon_radio': 3,          # More lunar relays for coverage
        'num_ground_stations': 6,
        'link_capacities': {
            1: 1.5,   # Radio: Very limited
            2: 2.5,   # Mixed: Limited
            3: 4.0    # Optical: Moderate (for telescope data)
        }
    }
    
    # Different traffic patterns between missions
    mission_configs = [
        {
            'name': 'Shackleton_Base',
            'location': (RADIUS_MOON + 1, pi),  # South pole region
            'num_assets': 1,  # LifeSupport system
            'capabilities': [1, 2, 3]  # All capabilities for critical systems
        },
        {
            'name': 'Farside_Observatory',
            'location': (RADIUS_MOON + 1, pi * 1.5),  # Far side
            'num_assets': 1,  # Telescope
            'capabilities': [2, 3]  # High bandwidth capabilities only
        }
    ]
    
    generator = CNTGenerator(sim_length=500, time_step=1.0, config=config)
    generator.generate_satellites()
    generator.generate_missions(mission_configs)
    generator.generate_link_matrices()
    generator.generate_routes()
    generator.allocate_bandwidth()
    
    os.makedirs("scenarios/asymmetric", exist_ok=True)
    generator.export_cnt_data("scenarios/asymmetric/CNT")
    
    scenario_info = {
        'scenario_name': 'Asymmetric Geographic',
        'description': 'Diverse traffic patterns - TT-heavy vs RC-heavy',
        'total_capacity': 3.5,  # Even more limited
        'expected_demand': 17.5,  # 5x oversubscription
        'missions': [
            {
                'name': 'Shackleton_Base',
                'assets': ['LifeSupport'],
                'traffic_rates': {'TT': 4.5, 'RC': 2.0, 'BE': 1.0},
                'profile': 'TT-heavy (critical life support)'
            },
            {
                'name': 'Farside_Observatory',
                'assets': ['Telescope'],
                'traffic_rates': {'TT': 1.0, 'RC': 6.0, 'BE': 3.0},
                'profile': 'RC-heavy (telescope data streaming)'
            }
        ],
        'num_satellites': len(generator.satellites),
        'num_routes': len(generator.routes_over_time[0]) if generator.routes_over_time else 0
    }
    
    with open("scenarios/asymmetric/scenario_info.json", 'w') as f:
        json.dump(scenario_info, f, indent=2)
    
    print(f"  ✓ Generated {len(generator.satellites)} satellites")
    print(f"  ✓ Generated {len(generator.missions)} missions")
    print(f"  ✓ Found {scenario_info['num_routes']} initial routes")
    print(f"  ✓ Files saved to scenarios/asymmetric/")
    
    return generator

def create_artemis_scenario():
    """Generate CNTGen network for Artemis-like Congestion scenario"""
    print("\nGenerating Artemis-like Congestion scenario...")
    
    config = {
        'num_earth_optical': 3,
        'num_earth_radio': 4,
        'num_moon_radio': 4,      # More relays but still insufficient
        'num_ground_stations': 8,  # More ground stations
        'link_capacities': {
            1: 2.0,   # Radio: Limited
            2: 3.0,   # Mixed: Limited
            3: 5.0    # Optical: Limited (severe congestion)
        }
    }
    
    # Six competing missions
    mission_configs = [
        {
            'name': 'Artemis_Base',
            'location': (RADIUS_MOON + 1, pi),  # South pole
            'num_assets': 1,  # Habitat
            'capabilities': [1, 2, 3]
        },
        {
            'name': 'Artemis_Rover1',
            'location': (RADIUS_MOON + 1, pi - 0.1),  # Near base
            'num_assets': 1,  # Explorer1
            'capabilities': [1, 2]
        },
        {
            'name': 'Artemis_Rover2',
            'location': (RADIUS_MOON + 1, pi + 0.1),  # Near base
            'num_assets': 1,  # Explorer2
            'capabilities': [1, 2]
        },
        {
            'name': 'CLPS_Lander1',
            'location': (RADIUS_MOON + 1, pi/3),  # Different region
            'num_assets': 1,  # Science1
            'capabilities': [1]
        },
        {
            'name': 'CLPS_Lander2',
            'location': (RADIUS_MOON + 1, pi/2),  # Different region
            'num_assets': 1,  # Science2
            'capabilities': [1]
        },
        {
            'name': 'ESA_Science',
            'location': (RADIUS_MOON + 1, pi/4),  # North region
            'num_assets': 1,  # Monitor
            'capabilities': [2, 3]
        }
    ]
    
    generator = CNTGenerator(sim_length=500, time_step=1.0, config=config)
    generator.generate_satellites()
    generator.generate_missions(mission_configs)
    generator.generate_link_matrices()
    generator.generate_routes()
    generator.allocate_bandwidth()
    
    os.makedirs("scenarios/artemis", exist_ok=True)
    generator.export_cnt_data("scenarios/artemis/CNT")
    
    scenario_info = {
        'scenario_name': 'Artemis-like Congestion',
        'description': '6 missions competing for limited bandwidth',
        'total_capacity': 10.0,
        'expected_demand': 45.0,  # ~450% congestion
        'missions': [
            {
                'name': 'Artemis_Base',
                'assets': ['Habitat'],
                'traffic_rates': {'TT': 5.0, 'RC': 2.0, 'BE': 1.0},
                'profile': 'TT-heavy (habitat life support)'
            },
            {
                'name': 'Artemis_Rover1',
                'assets': ['Explorer1'],
                'traffic_rates': {'TT': 2.0, 'RC': 2.0, 'BE': 2.0},
                'profile': 'Balanced'
            },
            {
                'name': 'Artemis_Rover2',
                'assets': ['Explorer2'],
                'traffic_rates': {'TT': 2.0, 'RC': 2.0, 'BE': 2.0},
                'profile': 'Balanced'
            },
            {
                'name': 'CLPS_Lander1',
                'assets': ['Science1'],
                'traffic_rates': {'TT': 0.5, 'RC': 1.0, 'BE': 4.0},
                'profile': 'BE-heavy (science data)'
            },
            {
                'name': 'CLPS_Lander2',
                'assets': ['Science2'],
                'traffic_rates': {'TT': 0.5, 'RC': 1.0, 'BE': 4.0},
                'profile': 'BE-heavy (science data)'
            },
            {
                'name': 'ESA_Science',
                'assets': ['Monitor'],
                'traffic_rates': {'TT': 1.0, 'RC': 5.0, 'BE': 2.0},
                'profile': 'RC-heavy (monitoring data)'
            }
        ],
        'num_satellites': len(generator.satellites),
        'num_routes': len(generator.routes_over_time[0]) if generator.routes_over_time else 0
    }
    
    with open("scenarios/artemis/scenario_info.json", 'w') as f:
        json.dump(scenario_info, f, indent=2)
    
    print(f"  ✓ Generated {len(generator.satellites)} satellites")
    print(f"  ✓ Generated {len(generator.missions)} missions")
    print(f"  ✓ Found {scenario_info['num_routes']} initial routes")
    print(f"  ✓ Files saved to scenarios/artemis/")
    
    return generator

def print_scenario_summary():
    """Print summary of all generated scenarios"""
    print("\n" + "="*60)
    print("SCENARIO GENERATION SUMMARY")
    print("="*60)
    
    scenarios = ['balanced', 'asymmetric', 'artemis']
    
    for scenario in scenarios:
        info_file = f"scenarios/{scenario}/scenario_info.json"
        if os.path.exists(info_file):
            with open(info_file, 'r') as f:
                info = json.load(f)
            
            print(f"\n{info['scenario_name']}:")
            print(f"  Description: {info['description']}")
            print(f"  Capacity: {info['total_capacity']} Mbps")
            print(f"  Demand: {info['expected_demand']} Mbps")
            print(f"  Congestion: {info['expected_demand']/info['total_capacity']*100:.0f}%")
            print(f"  Missions: {len(info['missions'])}")
            print(f"  Satellites: {info['num_satellites']}")
            print(f"  Initial Routes: {info['num_routes']}")
            
            # List files created
            files = os.listdir(f"scenarios/{scenario}")
            cnt_files = [f for f in files if f.startswith('CNT')]
            print(f"  Files: {', '.join(cnt_files[:3])}...")

def main():
    """Generate all CNTGen scenarios"""
    print("="*60)
    print("GENERATING CNTGEN SCENARIOS FOR REALISTIC TESTS")
    print("="*60)
    print("\nThis will create CNTGen network topology files for:")
    print("  1. Balanced Dual-Mission scenario")
    print("  2. Asymmetric Geographic scenario")
    print("  3. Artemis-like Congestion scenario")
    
    # Create main scenarios directory
    os.makedirs("scenarios", exist_ok=True)
    
    # Generate each scenario
    balanced_gen = create_balanced_scenario()
    asymmetric_gen = create_asymmetric_scenario()
    artemis_gen = create_artemis_scenario()
    
    # Print summary
    print_scenario_summary()
    
    print("\n" + "="*60)
    print("GENERATION COMPLETE")
    print("="*60)
    print("\n✅ All CNTGen scenario files have been created in scenarios/")
    print("\nEach scenario folder contains:")
    print("  • CNT_satellites.txt - Satellite constellation")
    print("  • CNT_missions.txt - Mission hierarchy")
    print("  • CNT_links.txt - Link matrices over time")
    print("  • CNT_routes.txt - Available routes")
    print("  • CNT_allocations.json - Bandwidth allocations")
    print("  • CNT_summary.json - Configuration summary")
    print("  • scenario_info.json - Scenario description")
    
    print("\nThese files can now be used by schedulers (TwinSync, FIFO, RoundRobin)")
    print("to perform realistic cislunar network simulations with dynamic routing.")
    
    return {
        'balanced': balanced_gen,
        'asymmetric': asymmetric_gen,
        'artemis': artemis_gen
    }

if __name__ == "__main__":
    generators = main()