"""
Cislunar Network Twin Generator
Generates the complete CNT including network topology, link availability,
routes, and bandwidth allocation based on mission requirements.

Note on scaling:
- Visual positions are compressed 10x for better visualization
- Real distances are calculated properly for propagation delays
- Earth-Moon distance: visual 384.4 units = real 384,400 km
"""

from .BSComps import *
import numpy as np
import random
from collections import defaultdict, deque
import json

class CNTGenerator:
    def __init__(self, sim_length=100, time_step=1.0, config=None):
        """
        Initialize CNT Generator
        
        Args:
            sim_length: Total simulation time
            time_step: Time step for simulation
            config: Optional configuration dictionary
        """
        self.sim_length = sim_length
        self.time_step = time_step
        self.satellites = []
        self.missions = []
        self.link_matrices = []
        self.routes_over_time = []
        self.allocations_over_time = []
        
        # Default configuration
        self.config = {
            'num_earth_optical': 2,
            'num_earth_radio': 3,
            'num_moon_radio': 3,
            'num_ground_stations': 6,
            'link_capacities': {1: 20.0, 2: 30.0, 3: 50.0},  # Mbps per technology type
            'propagation_speed': 3e5  # km/s (speed of light)
        }
        if config:
            self.config.update(config)
    
    def generate_satellites(self):
        """Generate satellite constellation based on configuration"""
        sats = []
        
        # Earth optical satellites (capability 3)
        for i in range(self.config['num_earth_optical']):
            orbit_factor = random.randint(1, 5)
            phase_shift = random.random() * pi
            sats.append(Satellite(
                f"E_O{i}", 
                Orbit2D(RADIUS_EARTH * (1 + (1/orbit_factor)), phase_shift), 
                B_Earth, 
                pi/(2*orbit_factor), 
                [3]
            ))
        
        # Earth radio satellites (capability 1)
        for i in range(self.config['num_earth_radio']):
            orbit_factor = random.randint(1, 5)
            phase_shift = random.random() * pi
            sats.append(Satellite(
                f"E_R{i}", 
                Orbit2D(RADIUS_EARTH * (1 + (1/orbit_factor)), phase_shift), 
                B_Earth, 
                pi/(2*orbit_factor), 
                [1]
            ))
        
        # Moon radio satellites (capability 2)
        for i in range(self.config['num_moon_radio']):
            orbit_factor = random.randint(1, 5)
            phase_shift = random.random() * pi
            sats.append(Satellite(
                f"M_R{i}", 
                Orbit2D(RADIUS_MOON * (2 + (1/orbit_factor)), phase_shift), 
                B_Moon, 
                pi/orbit_factor, 
                [2]
            ))
        
        # Earth ground stations
        for i in range(self.config['num_ground_stations']):
            capabilities = [2, 3] if i % 3 != 0 else [1]
            sats.append(Satellite(
                f"BE{i}", 
                Orbit2D(RADIUS_EARTH + 1, 2 * pi * (i / self.config['num_ground_stations'])), 
                B_Earth, 
                pi/8,  # Earth rotation
                capabilities
            ))
        
        self.satellites = sats
    
    def generate_missions(self, mission_configs=None):
        """Generate missions with assets and queues"""
        if mission_configs is None:
            # Default mission configurations
            mission_configs = [
                {
                    'name': 'ArtemisBase',
                    'location': (RADIUS_MOON + 1, 0),  # Position on moon surface
                    'num_assets': 2,
                    'capabilities': [1, 2, 3]
                },
                {
                    'name': 'CommercialLander',
                    'location': (RADIUS_MOON + 1, pi/2),  # Different position
                    'num_assets': 1,
                    'capabilities': [1, 2]
                }
            ]
        
        for config in mission_configs:
            # Create mission
            mission = MissionDef(config['name'], 0)  # Bandwidth will be calculated
            mission.generate_assets(num_assets=config['num_assets'])
            
            # Create base station satellite for this mission
            base_sat = Satellite(
                f"BM_{config['name']}", 
                Orbit2D(config['location'][0], config['location'][1]), 
                B_Moon, 
                0,  # Stationary on surface
                config['capabilities']
            )
            self.satellites.append(base_sat)
            self.missions.append(mission)
    
    def calculate_link_matrix(self, time):
        """Calculate link availability matrix at given time"""
        n = len(self.satellites)
        matrix = np.zeros((n, n), dtype=int)
        
        for i in range(n):
            sat1 = self.satellites[i]
            pos1 = sat1.pos()
            
            for j in range(i+1, n):
                sat2 = self.satellites[j]
                pos2 = sat2.pos()
                
                # Check technology compatibility
                if not set(sat1.cap).isdisjoint(set(sat2.cap)):
                    # Check line of sight (no obstruction by Earth or Moon)
                    los_clear = (
                        line_circle_intersection(pos1[0], pos1[1], pos2[0], pos2[1], 
                                               B_Earth.pos[0], B_Earth.pos[1], B_Earth.size) and
                        line_circle_intersection(pos1[0], pos1[1], pos2[0], pos2[1], 
                                               B_Moon.pos[0], B_Moon.pos[1], B_Moon.size)
                    )
                    
                    if los_clear:
                        matrix[i][j] = 1
                        matrix[j][i] = 1
        
        return matrix
    
    def generate_link_matrices(self):
        """Generate link matrices over simulation time"""
        self.link_matrices = []
        current_time = 0
        
        while current_time <= self.sim_length:
            # Update satellite positions
            for sat in self.satellites:
                sat._ct = current_time
            
            # Calculate link matrix
            matrix = self.calculate_link_matrix(current_time)
            self.link_matrices.append(matrix)
            
            current_time += self.time_step
    
    def find_routes(self, matrix, start_idx, end_indices):
        """Find all routes from start to any end node"""
        routes = []
        
        def dfs(current, visited, path):
            if current in end_indices:
                routes.append(path.copy())
                return
            
            for next_node in range(len(matrix)):
                if (matrix[current][next_node] == 1 and 
                    next_node not in visited and
                    not self._is_intermediate_base(next_node)):
                    
                    visited.add(next_node)
                    path.append(next_node)
                    dfs(next_node, visited, path)
                    path.pop()
                    visited.remove(next_node)
        
        visited = {start_idx}
        dfs(start_idx, visited, [start_idx])
        return routes
    
    def _is_intermediate_base(self, idx):
        """Check if a satellite is a base station (should not be used as intermediate node)"""
        sat_name = self.satellites[idx].name
        return sat_name.startswith(('BM_', 'BE')) and idx not in self.start_indices + self.end_indices
    
    def generate_routes(self):
        """Generate routes for all time slices"""
        # Identify start nodes (mission bases) and end nodes (ground stations)
        self.start_indices = []
        self.end_indices = []
        
        for i, sat in enumerate(self.satellites):
            if sat.name.startswith('BM_'):
                self.start_indices.append(i)
            elif sat.name.startswith('BE'):
                self.end_indices.append(i)
        
        # Generate routes for each time slice
        self.routes_over_time = []
        for matrix in self.link_matrices:
            time_routes = []
            for start_idx in self.start_indices:
                routes = self.find_routes(matrix, start_idx, self.end_indices)
                time_routes.extend(routes)
            self.routes_over_time.append(time_routes)
    
    def calculate_route_properties(self, route, time_idx):
        """Calculate bandwidth and delay for a route"""
        if len(route) < 2:
            return 0, float('inf')
        
        # Calculate minimum bandwidth along route
        min_bandwidth = float('inf')
        total_delay = 0
        
        for i in range(len(route) - 1):
            sat1 = self.satellites[route[i]]
            sat2 = self.satellites[route[i+1]]
            
            # Get common technologies
            common_tech = set(sat1.cap) & set(sat2.cap)
            if not common_tech:
                return 0, float('inf')
            
            # Use highest capacity technology
            link_capacity = max(self.config['link_capacities'][tech] for tech in common_tech)
            min_bandwidth = min(min_bandwidth, link_capacity)
            
            # Calculate propagation delay using real distances
            pos1 = sat1.pos()
            pos2 = sat2.pos()
            distance_km = calculate_real_distance(pos1, pos2)
            delay = distance_km / self.config['propagation_speed']
            total_delay += delay
        
        return min_bandwidth, total_delay
    
    def allocate_bandwidth(self):
        """Allocate bandwidth to missions based on their requirements"""
        self.allocations_over_time = []
        
        for time_idx, routes in enumerate(self.routes_over_time):
            if not routes:
                # No routes available at this time
                self.allocations_over_time.append({})
                continue
            
            # Calculate route properties
            route_info = []
            for route in routes:
                bandwidth, delay = self.calculate_route_properties(route, time_idx)
                if bandwidth > 0:
                    route_info.append({
                        'route': route,
                        'bandwidth': bandwidth,
                        'delay': delay,
                        'available': bandwidth
                    })
            
            # Sort routes by delay (shorter is better)
            route_info.sort(key=lambda x: x['delay'])
            
            # Allocate bandwidth to missions
            time_allocations = {}
            
            for mission in self.missions:
                mission_bw_req = mission.reqbw
                time_allocations[mission.name] = {
                    'required': mission_bw_req,
                    'allocated': 0,
                    'routes': []
                }
                
                # Find which satellite corresponds to this mission
                mission_sat_idx = None
                for i, sat in enumerate(self.satellites):
                    if f"BM_{mission.name}" == sat.name:
                        mission_sat_idx = i
                        break
                
                if mission_sat_idx is None:
                    continue
                
                # Allocate on routes starting from this mission's base
                for route_data in route_info:
                    if route_data['route'][0] == mission_sat_idx and route_data['available'] > 0:
                        # Allocate bandwidth
                        allocation = min(mission_bw_req - time_allocations[mission.name]['allocated'],
                                       route_data['available'])
                        
                        if allocation > 0:
                            time_allocations[mission.name]['allocated'] += allocation
                            time_allocations[mission.name]['routes'].append({
                                'route': route_data['route'],
                                'bandwidth': allocation,
                                'delay': route_data['delay']
                            })
                            route_data['available'] -= allocation
                        
                        if time_allocations[mission.name]['allocated'] >= mission_bw_req:
                            break
            
            self.allocations_over_time.append(time_allocations)
    
    def export_cnt_data(self, prefix="CNT"):
        """Export all CNT data to files"""
        # Export satellites
        with open(f"{prefix}_satellites.txt", 'w') as f:
            for sat in self.satellites:
                f.write(sat.export() + '\n')
        
        # Export missions with full hierarchy
        saveMissionHierarchy(self.missions, f"{prefix}_missions.txt")
        
        # Export link matrices
        with open(f"{prefix}_links.txt", 'w') as f:
            for matrix in self.link_matrices:
                np.savetxt(f, matrix, delimiter=',', fmt='%d')
                f.write("# ENDMAT\n")
        
        # Export routes
        with open(f"{prefix}_routes.txt", 'w') as f:
            for time_routes in self.routes_over_time:
                route_strs = []
                for route in time_routes:
                    route_strs.append(','.join(map(str, route)))
                f.write('|'.join(route_strs) + '\n')
        
        # Export allocations as JSON for easier parsing
        allocations_export = []
        for time_idx, time_alloc in enumerate(self.allocations_over_time):
            time_data = {
                'time': time_idx * self.time_step,
                'missions': {}
            }
            for mission_name, alloc_data in time_alloc.items():
                time_data['missions'][mission_name] = {
                    'required': alloc_data['required'],
                    'allocated': alloc_data['allocated'],
                    'routes': [
                        {
                            'path': r['route'],
                            'bandwidth': r['bandwidth'],
                            'delay': r['delay']
                        }
                        for r in alloc_data['routes']
                    ]
                }
            allocations_export.append(time_data)
        
        with open(f"{prefix}_allocations.json", 'w') as f:
            json.dump(allocations_export, f, indent=2)
        
        # Export summary
        summary = {
            'sim_length': self.sim_length,
            'time_step': self.time_step,
            'num_satellites': len(self.satellites),
            'num_missions': len(self.missions),
            'config': self.config
        }
        with open(f"{prefix}_summary.json", 'w') as f:
            json.dump(summary, f, indent=2)
        
        print(f"CNT data exported with prefix '{prefix}'")
        print(f"  - Satellites: {len(self.satellites)}")
        print(f"  - Missions: {len(self.missions)}")
        print(f"  - Time slices: {len(self.link_matrices)}")
        print(f"  - Total simulation time: {self.sim_length}")
    
    def generate_complete_cnt(self):
        """Generate complete CNT data"""
        print("Generating Cislunar Network Twin...")
        
        print("  1. Generating satellites...")
        self.generate_satellites()
        
        print("  2. Generating missions with assets...")
        self.generate_missions()
        
        print("  3. Calculating link matrices over time...")
        self.generate_link_matrices()
        
        print("  4. Finding routes...")
        self.generate_routes()
        
        print("  5. Allocating bandwidth...")
        self.allocate_bandwidth()
        
        print("  6. Exporting data...")
        self.export_cnt_data()
        
        print("CNT generation complete!")
        
        # Print some example delays for validation
        if self.allocations_over_time and self.allocations_over_time[0]:
            print("\nExample propagation delays:")
            for mission_name, alloc in list(self.allocations_over_time[0].items())[:2]:
                if alloc['routes']:
                    route = alloc['routes'][0]
                    print(f"  {mission_name}: {route['delay']:.3f} seconds ({route['delay']*1000:.1f} ms)")
                    # For Earth-Moon communication, expect ~1.3 seconds one-way


# Example usage
if __name__ == "__main__":
    # Create CNT generator with custom configuration
    config = {
        'num_earth_optical': 3,
        'num_earth_radio': 4,
        'num_moon_radio': 3,
        'num_ground_stations': 8,
        'link_capacities': {1: 20.0, 2: 30.0, 3: 50.0},  # Mbps
    }
    
    generator = CNTGenerator(sim_length=50, time_step=0.5, config=config)
    generator.generate_complete_cnt()