"""
TwinSync Router Simulator
Implements the AoS-aware scheduling algorithm for routing data from
lunar assets to Earth using the Cislunar Network Twin data.
"""

from .BSComps import *
import numpy as np
import json
import matplotlib.pyplot as plt
from collections import defaultdict, deque
import pandas as pd

class DataItem:
    """Represents a data item in a queue with metadata"""
    def __init__(self, item_id, generation_time, deadline, size):
        self.id = item_id
        self.generation_time = generation_time
        self.deadline = deadline
        self.size = size
        self.transmission_start = None
        self.delivery_time = None
        self.route = None

class TwinSyncRouter:
    def __init__(self, cnt_prefix="CNT", packing_mode="greedy", deadline_aware=False):
        """Initialize TwinSync Router with CNT data
        
        Args:
            cnt_prefix: Prefix for CNT data files
            packing_mode: "greedy" (default), "optimized", or "deadline_aware"
            deadline_aware: Enable deadline-aware yielding (RC yields to BE when ahead)
        """
        self.cnt_prefix = cnt_prefix
        self.current_time = 0
        self.time_step = 0
        self.packing_mode = packing_mode
        self.deadline_aware = deadline_aware
        self.missions = []
        self.satellites = []
        self.link_matrices = []
        self.routes_over_time = []
        self.allocations_over_time = []
        self.mission_to_sat_idx = {}
        
        # Metrics tracking
        self.queue_sizes_history = defaultdict(list)
        self.aos_history = defaultdict(list)
        self.lyapunov_history = []
        self.delivered_items = []
        self.dropped_items = []
        self.time_history = []
        
        # Item tracking
        self.item_counter = 0
        self.in_transit_items = []
        
    def load_cnt_data(self):
        """Load all CNT data from files"""
        print("Loading CNT data...")
        
        # Load satellites
        self.satellites = loadST(f"{self.cnt_prefix}_satellites.txt")
        
        # Load missions with full hierarchy
        self.missions = loadMissionHierarchy(f"{self.cnt_prefix}_missions.txt")
        
        # Load link matrices
        self.link_matrices = loadLA(f"{self.cnt_prefix}_links.txt")
        
        # Load routes
        self.routes_over_time = loadRT(f"{self.cnt_prefix}_routes.txt")
        
        # Load allocations
        with open(f"{self.cnt_prefix}_allocations.json", 'r') as f:
            self.allocations_over_time = json.load(f)
        
        # Load summary
        with open(f"{self.cnt_prefix}_summary.json", 'r') as f:
            summary = json.load(f)
            self.time_step = summary['time_step']
            self.sim_length = summary['sim_length']
        
        # Map missions to satellite indices
        for i, sat in enumerate(self.satellites):
            if sat.name.startswith("BM_"):
                mission_name = sat.name.replace("BM_", "")
                for mission in self.missions:
                    if mission.name == mission_name:
                        self.mission_to_sat_idx[mission.name] = i
                        break
        
        print(f"Loaded {len(self.missions)} missions, {len(self.satellites)} satellites")
        print(f"Simulation length: {self.sim_length}, time step: {self.time_step}")
        
        # Validate data consistency
        if len(self.link_matrices) != len(self.routes_over_time):
            raise ValueError(f"Inconsistent time steps: links={len(self.link_matrices)}, routes={len(self.routes_over_time)}")
        
        # Validate all missions have satellite mappings
        for mission in self.missions:
            if mission.name not in self.mission_to_sat_idx:
                print(f"Warning: Mission {mission.name} has no satellite mapping")
        
    def calculate_lyapunov_drift(self):
        """Calculate Lyapunov drift L(t) = 1/2 * sum(Q_n_x(t)^2)"""
        total = 0
        for mission in self.missions:
            for asset in mission.assets:
                for queue in asset.queues:
                    total += queue.length ** 2
        return total / 2
    
    def calculate_queue_weight(self, queue, mission, current_time, total_lyapunov, total_bandwidth):
        """Calculate queue weight based on paper formula"""
        # Get QAoS value
        qaos = queue.get_aos(current_time)
        if qaos < 1e-6:  # Prevent numerical instability while preserving urgency differentiation
            qaos = 1e-6  # Minimum threshold: (1e-6)^6 = 1e-36, well within safe range
        
        # Priority component
        priority = queue.priority
        
        # Queue contribution to Lyapunov drift
        if total_lyapunov > 0:
            queue_contribution = (queue.length ** 2) / (2 * total_lyapunov)  # Use squared length per paper
        else:
            queue_contribution = 0
        
        # Bandwidth normalization
        if total_bandwidth > 0:
            bandwidth_factor = queue.item_size / total_bandwidth
        else:
            bandwidth_factor = 1
        
        # Class-specific adaptive parameter Z
        if queue.queue_class == 3:  # TT
            z_factor = 1 / (qaos ** 3)
        elif queue.queue_class == 2:  # RC
            z_factor = queue.item_size
        else:  # BE
            z_factor = queue.priority
        
        # Calculate final weight
        weight = priority * (1/(qaos**3) + queue_contribution) * bandwidth_factor * z_factor
        
        return weight
    
    def get_sorted_queues(self, current_time):
        """Get all queues sorted by class and weight"""
        total_lyapunov = self.calculate_lyapunov_drift()
        
        # Get total available bandwidth at current time
        time_idx = int(current_time / self.time_step)
        if time_idx >= len(self.allocations_over_time):
            return []
        
        total_bandwidth = 0
        current_allocations = self.allocations_over_time[time_idx]
        if 'missions' in current_allocations:
            for mission_name, alloc in current_allocations['missions'].items():
                total_bandwidth += alloc.get('allocated', 0)
        
        # Collect all queues with their weights
        queue_list = []
        for mission in self.missions:
            for asset in mission.assets:
                for queue in asset.queues:
                    if queue.length > 0:
                        weight = self.calculate_queue_weight(
                            queue, mission, current_time, 
                            total_lyapunov, total_bandwidth
                        )
                        queue_list.append({
                            'queue': queue,
                            'mission': mission,
                            'asset': asset,
                            'weight': weight,
                            'class': queue.queue_class
                        })
        
        # Sort by class (descending) then by weight (descending)
        queue_list.sort(key=lambda x: (x['class'], x['weight']), reverse=True)
        
        return queue_list
    
    def schedule_transmission(self, current_time):
        """Schedule data transmission for current time step"""
        time_idx = int(current_time / self.time_step)
        if time_idx >= len(self.allocations_over_time):
            return
        
        current_allocations = self.allocations_over_time[time_idx]
        if 'missions' not in current_allocations:
            return
        
        # Get sorted queues
        sorted_queues = self.get_sorted_queues(current_time)
        
        # Track used bandwidth per route
        route_usage = defaultdict(float)
        
        # Schedule transmissions
        for queue_info in sorted_queues:
            queue = queue_info['queue']
            mission = queue_info['mission']
            
            if mission.name not in current_allocations['missions']:
                continue
            
            mission_alloc = current_allocations['missions'][mission.name]
            
            # Try each route for this mission
            for route_info in mission_alloc.get('routes', []):
                route_key = tuple(route_info['path'])
                available_bw = route_info['bandwidth'] - route_usage[route_key]
                
                if available_bw <= 0:
                    continue
                
                # Calculate how many items we can send
                items_to_send = min(
                    int(available_bw / queue.item_size),
                    queue.length
                )
                
                if items_to_send > 0:
                    # Dequeue and schedule items
                    for _ in range(items_to_send):
                        deadline = queue.dequeue()
                        if deadline is not None:
                            item = DataItem(
                                item_id=self.item_counter,
                                generation_time=deadline - 5,  # Approximate generation time
                                deadline=deadline,
                                size=queue.item_size
                            )
                            self.item_counter += 1
                            
                            item.transmission_start = current_time
                            item.route = route_info['path']
                            
                            # Calculate delivery time
                            delivery_time = current_time + route_info['delay']
                            item.delivery_time = delivery_time
                            
                            self.in_transit_items.append(item)
                            
                            # Update route usage
                            route_usage[route_key] += queue.item_size
                    
                    # If we've emptied the queue or used the route, move to next queue
                    if queue.length == 0 or route_usage[route_key] >= route_info['bandwidth']:
                        break
    
    def knapsack_select_queues(self, queues_info, bandwidth_limit):
        """Select queues using a greedy approach that maximizes bandwidth utilization
        
        Args:
            queues_info: List of queue info dicts with 'queue', 'weight', etc.
            bandwidth_limit: Maximum bandwidth available
            
        Returns:
            List of selected queue indices
        """
        if not queues_info or bandwidth_limit <= 0:
            return []
        
        # Sort queues by efficiency: prioritize smaller items that fit better
        # This helps achieve better bandwidth packing
        sorted_items = []
        for i, info in enumerate(queues_info):
            q = info['queue']
            if q.length > 0 and q.item_size <= bandwidth_limit:
                sorted_items.append({
                    'index': i,
                    'queue': q,
                    'size': q.item_size,
                    'weight': info['weight']
                })
        
        if not sorted_items:
            return []
        
        # Sort by: 1) Can fit multiple items, 2) Weight
        # This encourages selecting items that pack well
        sorted_items.sort(key=lambda x: (
            bandwidth_limit // x['size'],  # How many can fit
            x['weight']  # Then by weight
        ), reverse=True)
        
        # Greedy selection with backtracking for better packing
        selected = []
        remaining_bw = bandwidth_limit
        
        # First pass: try to fit items greedily
        for item in sorted_items:
            if item['size'] <= remaining_bw:
                items_count = min(
                    item['queue'].length,
                    int(remaining_bw / item['size'])
                )
                if items_count > 0:
                    selected.append(item['index'])
                    remaining_bw -= item['size'] * items_count
        
        # Second pass: try to fill remaining gaps with smaller items
        if remaining_bw > 0:
            for item in sorted_items:
                if item['index'] not in selected and item['size'] <= remaining_bw:
                    selected.append(item['index'])
                    remaining_bw -= item['size']
        
        return selected
    
    def schedule_transmission_optimized(self, current_time):
        """Optimized scheduling using class-aware best-fit packing
        
        Key innovation: Within each class level, find the combination that
        maximizes bandwidth utilization while respecting strict class priorities.
        """
        time_idx = int(current_time / self.time_step)
        if time_idx >= len(self.allocations_over_time):
            return
        
        current_allocations = self.allocations_over_time[time_idx]
        if 'missions' not in current_allocations:
            return
        
        # Get sorted queues
        sorted_queues = self.get_sorted_queues(current_time)
        
        # Group queues by class
        queues_by_class = {3: [], 2: [], 1: []}
        for info in sorted_queues:
            class_level = info['class']
            if class_level in queues_by_class:
                queues_by_class[class_level].append(info)
        
        # Track used bandwidth per route
        route_usage = defaultdict(float)
        
        # Get total bandwidth available
        total_bandwidth = 0
        for mission_name, mission_alloc in current_allocations['missions'].items():
            for route_info in mission_alloc.get('routes', []):
                route_key = tuple(route_info['path'])
                if route_key not in route_usage:
                    total_bandwidth = max(total_bandwidth, route_info['bandwidth'])
        
        # Alternative strategy: Find best combination across all classes
        # that respects priority but maximizes utilization
        best_combination = []
        best_utilization = 0
        
        # Try TT first (must always be included if it fits)
        tt_queues = queues_by_class[3]
        tt_bandwidth = sum(q['queue'].item_size for q in tt_queues if q['queue'].length > 0)
        
        if tt_bandwidth <= total_bandwidth:
            # TT fits, now try RC and BE
            remaining_after_tt = total_bandwidth - tt_bandwidth
            
            rc_queues = queues_by_class[2]
            rc_bandwidth = sum(q['queue'].item_size for q in rc_queues if q['queue'].length > 0)
            
            be_queues = queues_by_class[1]
            be_bandwidth = sum(q['queue'].item_size for q in be_queues if q['queue'].length > 0)
            
            # Option 1: TT + RC
            if rc_bandwidth <= remaining_after_tt:
                option1_util = tt_bandwidth + rc_bandwidth
                if option1_util > best_utilization:
                    best_utilization = option1_util
                    best_combination = ['TT', 'RC']
            
            # Option 2: TT + BE (only if RC doesn't fit perfectly)
            if be_bandwidth <= remaining_after_tt:
                option2_util = tt_bandwidth + be_bandwidth
                if option2_util > best_utilization:
                    best_utilization = option2_util
                    best_combination = ['TT', 'BE']
            
            # Option 3: TT only
            if tt_bandwidth > best_utilization:
                best_utilization = tt_bandwidth
                best_combination = ['TT']
        
        # Now schedule based on best combination
        # But still respect class priority - use original greedy for now
        # Process each class level in priority order
        for class_level in [3, 2, 1]:  # TT (3), RC (2), BE (1)
            class_queues = queues_by_class[class_level]
            if not class_queues:
                continue
            
            # Calculate available bandwidth for this class
            total_available = 0
            route_bandwidth = {}
            
            for queue_info in class_queues:
                mission = queue_info['mission']
                if mission.name not in current_allocations['missions']:
                    continue
                
                mission_alloc = current_allocations['missions'][mission.name]
                for route_info in mission_alloc.get('routes', []):
                    route_key = tuple(route_info['path'])
                    if route_key not in route_bandwidth:
                        route_bandwidth[route_key] = route_info['bandwidth'] - route_usage[route_key]
            
            # For simplicity, use total available bandwidth across all routes
            total_available = sum(route_bandwidth.values())
            
            if total_available <= 0:
                continue
            
            # Select queues using knapsack
            selected_indices = self.knapsack_select_queues(class_queues, total_available)
            
            # Schedule selected queues
            for idx in selected_indices:
                queue_info = class_queues[idx]
                queue = queue_info['queue']
                mission = queue_info['mission']
                
                if mission.name not in current_allocations['missions']:
                    continue
                
                mission_alloc = current_allocations['missions'][mission.name]
                
                # Try each route for this mission
                for route_info in mission_alloc.get('routes', []):
                    route_key = tuple(route_info['path'])
                    available_bw = route_info['bandwidth'] - route_usage[route_key]
                    
                    if available_bw <= 0:
                        continue
                    
                    # Calculate how many items we can send
                    items_to_send = min(
                        int(available_bw / queue.item_size),
                        queue.length
                    )
                    
                    if items_to_send > 0:
                        # Dequeue and schedule items
                        for _ in range(items_to_send):
                            deadline = queue.dequeue()
                            if deadline is not None:
                                item = DataItem(
                                    item_id=self.item_counter,
                                    generation_time=deadline - 5,
                                    deadline=deadline,
                                    size=queue.item_size
                                )
                                self.item_counter += 1
                                
                                item.transmission_start = current_time
                                item.route = route_info['path']
                                
                                # Calculate delivery time
                                delivery_time = current_time + route_info['delay']
                                item.delivery_time = delivery_time
                                
                                self.in_transit_items.append(item)
                                
                                # Update route usage
                                route_usage[route_key] += queue.item_size
                        
                        # If we've emptied the queue or used the route, move to next
                        if queue.length == 0 or route_usage[route_key] >= route_info['bandwidth']:
                            break
    
    def schedule_transmission_deadline_aware(self, current_time):
        """Semantically-aware scheduling that respects traffic class requirements
        
        Traffic class semantics:
        - TT (Time-Triggered): MUST arrive on-time or early, NEVER late. Zero tolerance.
        - RC (Rate-Constrained): MUST maintain average rate. Can tolerate jitter if rate is met.
        - BE (Best-Effort): Gets remaining bandwidth. No guarantees.
        
        Key principles:
        1. TT never yields unless it has MASSIVE slack and zero risk
        2. RC can yield if its rate requirements are being met
        3. BE only gets bandwidth when higher classes don't need it
        """
        time_idx = int(current_time / self.time_step)
        if time_idx >= len(self.allocations_over_time):
            return
        
        current_allocations = self.allocations_over_time[time_idx]
        if 'missions' not in current_allocations:
            return
        
        # Get sorted queues
        sorted_queues = self.get_sorted_queues(current_time)
        
        # Group queues by class
        queues_by_class = {3: [], 2: [], 1: []}
        for info in sorted_queues:
            class_level = info['class']
            if class_level in queues_by_class:
                queues_by_class[class_level].append(info)
        
        # Track used bandwidth per route
        route_usage = defaultdict(float)
        
        # Calculate deadline slack and urgency for each queue
        queue_urgency = {}
        for info in sorted_queues:
            q = info['queue']
            if q.length > 0 and q.items:
                earliest_deadline = min(q.items)
                slack = earliest_deadline - current_time
                # Urgency score: lower slack = higher urgency
                # Add class bonus to maintain base priority when deadlines are similar
                class_bonus = q.queue_class * 2  # Small bonus for higher classes
                urgency = -slack + class_bonus
                queue_urgency[q.queue_id] = {
                    'slack': slack,
                    'urgency': urgency,
                    'class': q.queue_class,
                    'queue_info': info
                }
        
        # Create dynamic scheduling order based on urgency
        # But respect hard constraints: never skip urgent high-priority traffic
        scheduled_queues = []
        
        for class_level in [3, 2, 1]:  # Check each class in priority order
            class_queues = queues_by_class[class_level]
            for queue_info in class_queues:
                q = queue_info['queue']
                if q.queue_id in queue_urgency:
                    urgency_info = queue_urgency[q.queue_id]
                    
                    # Determine if this queue should yield
                    should_yield = False
                    
                    # Calculate safe yielding threshold based on queue length and transmission time
                    # Each item takes 1 timestep to transmit when scheduled
                    # Need to account for queue buildup and ensure we can clear it before deadline
                    queue_clear_time = q.length  # Time to clear current queue if transmitted every timestep
                    safety_margin = 3  # Minimum safety margin in seconds
                    
                    if class_level == 3:  # TT (Time-Triggered)
                        # TT NEVER yields unless absolutely certain of meeting deadlines
                        # Requirements for TT to yield:
                        # 1. Queue must be nearly empty (≤2 items)
                        # 2. Slack must be enormous (>20s minimum)
                        # 3. Lower priority must be in crisis (<1s)
                        # This ensures TT semantics: on-time or early, NEVER late
                        
                        if q.length <= 2 and urgency_info['slack'] > 20:
                            # Only consider yielding if lower classes are in crisis
                            crisis_exists = False
                            for lower_class in [2, 1]:
                                for lower_q in queues_by_class[lower_class]:
                                    if lower_q['queue'].queue_id in queue_urgency:
                                        lower_slack = queue_urgency[lower_q['queue'].queue_id]['slack']
                                        if lower_slack < 1:  # Crisis threshold
                                            crisis_exists = True
                                            break
                                if crisis_exists:
                                    break
                            
                            if crisis_exists:
                                should_yield = True
                                
                    elif class_level == 2:  # RC (Rate-Constrained)
                        # RC can yield if it's maintaining its rate requirements
                        # More flexible than TT but still needs to meet rate guarantees
                        # Calculate if we're ahead of rate requirements
                        
                        # Simple heuristic: if queue is small and slack is good, can yield
                        # RC typically needs to maintain average rate, not strict deadlines
                        if q.length < 5 and urgency_info['slack'] > 8:
                            # Check if BE needs help
                            for be_q in queues_by_class[1]:
                                if be_q['queue'].queue_id in queue_urgency:
                                    be_slack = queue_urgency[be_q['queue'].queue_id]['slack']
                                    if be_slack < 3:
                                        should_yield = True
                                        break
                    
                    # Add to schedule with yield flag
                    scheduled_queues.append({
                        'queue_info': queue_info,
                        'urgency': urgency_info['urgency'],
                        'slack': urgency_info['slack'],
                        'class': class_level,
                        'should_yield': should_yield
                    })
        
        # Sort by: non-yielding first, then by urgency
        scheduled_queues.sort(key=lambda x: (
            x['should_yield'],  # Non-yielding queues first
            -x['urgency']       # Then by urgency (higher urgency first)
        ))
        
        # Process queues in the determined order
        for item in scheduled_queues:
            queue_info = item['queue_info']
            queue = queue_info['queue']
            mission = queue_info['mission']
            
            if mission.name not in current_allocations['missions']:
                continue
            
            mission_alloc = current_allocations['missions'][mission.name]
            
            for route_info in mission_alloc.get('routes', []):
                route_key = tuple(route_info['path'])
                available_bw = route_info['bandwidth'] - route_usage[route_key]
                
                if available_bw <= 0:
                    continue
                
                items_to_send = min(
                    int(available_bw / queue.item_size),
                    queue.length
                )
                
                if items_to_send > 0:
                    for _ in range(items_to_send):
                        deadline = queue.dequeue()
                        if deadline is not None:
                            item = DataItem(
                                item_id=self.item_counter,
                                generation_time=deadline - 5,
                                deadline=deadline,
                                size=queue.item_size
                            )
                            self.item_counter += 1
                            item.transmission_start = current_time
                            item.route = route_info['path']
                            item.delivery_time = current_time + route_info['delay']
                            self.in_transit_items.append(item)
                            route_usage[route_key] += queue.item_size
                    
                    if queue.length == 0 or route_usage[route_key] >= route_info['bandwidth']:
                        break
    
    def update_deliveries(self, current_time):
        """Update delivered items and calculate AoS"""
        delivered_this_step = []
        
        for item in self.in_transit_items[:]:
            if item.delivery_time <= current_time:
                self.in_transit_items.remove(item)
                
                # Calculate actual AoS at delivery
                aos_at_delivery = item.delivery_time - item.deadline
                
                item_record = {
                    'id': item.id,
                    'generation_time': item.generation_time,
                    'deadline': item.deadline,
                    'delivery_time': item.delivery_time,
                    'aos_at_delivery': aos_at_delivery,
                    'size': item.size
                }
                
                if aos_at_delivery > 0:
                    # Item was late
                    self.dropped_items.append(item_record)
                else:
                    # Item was on time or early
                    self.delivered_items.append(item_record)
                
                delivered_this_step.append(item_record)
        
        return delivered_this_step
    
    def record_metrics(self, current_time):
        """Record metrics for analysis"""
        self.time_history.append(current_time)
        
        # Record queue sizes
        for mission in self.missions:
            for asset in mission.assets:
                for queue in asset.queues:
                    key = f"{mission.name}_{asset.name}_Q{queue.queue_id}"
                    self.queue_sizes_history[key].append(queue.length)
        
        # Record AoS for each queue
        for mission in self.missions:
            for asset in mission.assets:
                for queue in asset.queues:
                    key = f"{mission.name}_{asset.name}_Q{queue.queue_id}"
                    aos = queue.get_aos(current_time)
                    self.aos_history[key].append(aos if aos != float('inf') else 0)
        
        # Record Lyapunov drift
        self.lyapunov_history.append(self.calculate_lyapunov_drift())
    
    def simulate(self):
        """Run the TwinSync router simulation"""
        print("\nStarting TwinSync Router simulation...")
        
        current_time = 0
        update_interval = self.sim_length / 10  # Progress updates
        next_update = update_interval
        
        while current_time <= self.sim_length:
            # Generate new data in queues
            for mission in self.missions:
                for asset in mission.assets:
                    asset.update(current_time, self.time_step)
            
            # Schedule transmissions using selected packing mode
            if self.deadline_aware:
                self.schedule_transmission_deadline_aware(current_time)
            elif self.packing_mode == "optimized":
                self.schedule_transmission_optimized(current_time)
            else:
                self.schedule_transmission(current_time)
            
            # Update deliveries
            self.update_deliveries(current_time)
            
            # Record metrics
            self.record_metrics(current_time)
            
            # Progress update
            if current_time >= next_update:
                print(f"  Progress: {current_time:.1f}/{self.sim_length} ({current_time/self.sim_length*100:.0f}%)")
                next_update += update_interval
            
            current_time += self.time_step
        
        print("Simulation complete!")
        self.print_summary()
    
    def print_summary(self):
        """Print simulation summary statistics"""
        print("\n=== Simulation Summary ===")
        print(f"Total items generated: {self.item_counter}")
        print(f"Items delivered on time: {len(self.delivered_items)}")
        print(f"Items delivered late: {len(self.dropped_items)}")
        print(f"Items still in transit: {len(self.in_transit_items)}")
        
        if self.delivered_items or self.dropped_items:
            all_items = self.delivered_items + self.dropped_items
            aos_values = [item['aos_at_delivery'] for item in all_items]
            print(f"\nAge of Synchronization statistics:")
            print(f"  Mean AoS: {np.mean(aos_values):.3f} seconds")
            print(f"  Min AoS: {np.min(aos_values):.3f} seconds")
            print(f"  Max AoS: {np.max(aos_values):.3f} seconds")
            print(f"  On-time delivery rate: {len(self.delivered_items)/len(all_items)*100:.1f}%")
    
    def plot_metrics(self):
        """Generate plots for queue sizes and AoS"""
        fig, axes = plt.subplots(3, 1, figsize=(12, 10))
        
        # Plot 1: Total queue sizes by mission
        ax1 = axes[0]
        mission_queue_totals = defaultdict(list)
        
        for t_idx in range(len(self.time_history)):
            for mission in self.missions:
                total = 0
                for asset in mission.assets:
                    for queue in asset.queues:
                        key = f"{mission.name}_{asset.name}_Q{queue.queue_id}"
                        if key in self.queue_sizes_history:
                            total += self.queue_sizes_history[key][t_idx]
                mission_queue_totals[mission.name].append(total)
        
        for mission_name, totals in mission_queue_totals.items():
            ax1.plot(self.time_history, totals, label=mission_name, linewidth=2)
        
        ax1.set_xlabel('Time (seconds)')
        ax1.set_ylabel('Total Queue Size (items)')
        ax1.set_title('Total Queue Sizes by Mission')
        ax1.legend()
        ax1.grid(True, alpha=0.3)
        
        # Plot 2: Average AoS by queue class
        ax2 = axes[1]
        class_aos_avg = {1: [], 2: [], 3: []}
        
        for t_idx in range(len(self.time_history)):
            class_aos = {1: [], 2: [], 3: []}
            
            for mission in self.missions:
                for asset in mission.assets:
                    for queue in asset.queues:
                        key = f"{mission.name}_{asset.name}_Q{queue.queue_id}"
                        if key in self.aos_history and t_idx < len(self.aos_history[key]):
                            aos_val = self.aos_history[key][t_idx]
                            if aos_val > 0:  # Only include non-zero AoS
                                class_aos[queue.queue_class].append(aos_val)
            
            for cls in [1, 2, 3]:
                if class_aos[cls]:
                    class_aos_avg[cls].append(np.mean(class_aos[cls]))
                else:
                    class_aos_avg[cls].append(0)
        
        class_names = {1: 'Best Effort', 2: 'Rate Constrained', 3: 'Time Triggered'}
        colors = {1: 'green', 2: 'orange', 3: 'red'}
        
        for cls in [3, 2, 1]:  # Plot in priority order
            if class_aos_avg[cls]:
                ax2.plot(self.time_history, class_aos_avg[cls], 
                        label=class_names[cls], color=colors[cls], linewidth=2)
        
        ax2.set_xlabel('Time (seconds)')
        ax2.set_ylabel('Average Age of Synchronization (seconds)')
        ax2.set_title('Average Age of Synchronization by Queue Class')
        ax2.legend()
        ax2.grid(True, alpha=0.3)
        ax2.set_ylim(bottom=0)
        
        # Plot 3: Lyapunov Drift
        ax3 = axes[2]
        ax3.plot(self.time_history, self.lyapunov_history, 'b-', linewidth=2)
        ax3.set_xlabel('Time (seconds)')
        ax3.set_ylabel('Lyapunov Drift L(t)')
        ax3.set_title('Queue Stability (Lyapunov Drift)')
        ax3.grid(True, alpha=0.3)
        
        plt.tight_layout()
        plt.savefig('twinsync_metrics.png', dpi=300, bbox_inches='tight')
        plt.show()
        
        # Additional plot: Delivery performance over time
        fig2, ax4 = plt.subplots(figsize=(10, 6))
        
        # Bin deliveries by time window
        time_bins = np.arange(0, self.sim_length + 5, 5)  # 5-second bins
        on_time_counts = []
        late_counts = []
        
        for i in range(len(time_bins) - 1):
            bin_start = time_bins[i]
            bin_end = time_bins[i + 1]
            
            on_time = sum(1 for item in self.delivered_items 
                         if bin_start <= item['delivery_time'] < bin_end)
            late = sum(1 for item in self.dropped_items 
                      if bin_start <= item['delivery_time'] < bin_end)
            
            on_time_counts.append(on_time)
            late_counts.append(late)
        
        bin_centers = (time_bins[:-1] + time_bins[1:]) / 2
        width = time_bins[1] - time_bins[0]
        
        ax4.bar(bin_centers, on_time_counts, width=width*0.8, label='On-time', 
                color='green', alpha=0.7)
        ax4.bar(bin_centers, late_counts, width=width*0.8, bottom=on_time_counts,
                label='Late', color='red', alpha=0.7)
        
        ax4.set_xlabel('Time (seconds)')
        ax4.set_ylabel('Number of Deliveries')
        ax4.set_title('Delivery Performance Over Time')
        ax4.legend()
        ax4.grid(True, alpha=0.3)
        
        plt.savefig('delivery_performance.png', dpi=300, bbox_inches='tight')
        plt.show()


# Main execution
if __name__ == "__main__":
    # Initialize router
    router = TwinSyncRouter(cnt_prefix="CNT")
    
    # Load CNT data
    router.load_cnt_data()
    
    # Run simulation
    router.simulate()
    
    # Generate plots
    router.plot_metrics()
    
    # Save detailed results
    print("\nSaving detailed results...")
    
    # Save delivery data
    if router.delivered_items or router.dropped_items:
        df_deliveries = pd.DataFrame(router.delivered_items + router.dropped_items)
        df_deliveries['on_time'] = df_deliveries['aos_at_delivery'] <= 0
        df_deliveries.to_csv('delivery_results.csv', index=False)
        print("Delivery results saved to 'delivery_results.csv'")
    
    # Save queue history
    queue_data = []
    for queue_name, sizes in router.queue_sizes_history.items():
        for t_idx, size in enumerate(sizes):
            queue_data.append({
                'time': router.time_history[t_idx],
                'queue': queue_name,
                'size': size,
                'aos': router.aos_history[queue_name][t_idx] if queue_name in router.aos_history else 0
            })
    
    df_queues = pd.DataFrame(queue_data)
    df_queues.to_csv('queue_history.csv', index=False)
    print("Queue history saved to 'queue_history.csv'")