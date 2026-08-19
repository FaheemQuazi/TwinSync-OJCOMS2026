"""
Base scheduler class with common functionality for all scheduling algorithms
Provides CNT loading, mission management, and metrics collection
"""

from .BSComps import *
import numpy as np
import json
import matplotlib.pyplot as plt
from collections import defaultdict, deque
import pandas as pd

class DataItem:
    """Represents a data item in transmission"""
    def __init__(self, item_id, generation_time, deadline, size, mission_name, asset_name, queue_class):
        self.id = item_id
        self.generation_time = generation_time
        self.deadline = deadline
        self.size = size
        self.mission_name = mission_name
        self.asset_name = asset_name
        self.queue_class = queue_class
        self.transmission_start = None
        self.delivery_time = None

class BaseScheduler:
    """Base class for all schedulers with common functionality"""
    
    def __init__(self, cnt_prefix="CNT", scheduler_name="Base"):
        self.cnt_prefix = cnt_prefix
        self.scheduler_name = scheduler_name
        self.current_time = 0
        self.time_step = 0
        self.missions = []
        self.satellites = []
        self.link_matrices = []
        self.routes_over_time = []
        self.allocations_over_time = []
        self.mission_to_sat_idx = {}
        self.sim_length = 0
        
        # Metrics tracking
        self.transmitted_items = []
        self.dropped_items = []
        self.in_transit_items = []
        self.item_counter = 0
        
        # History tracking
        self.time_history = []
        self.queue_sizes_history = defaultdict(list)
        self.throughput_history = defaultdict(list)
        self.bandwidth_usage_history = []
        self.missed_deadlines_history = defaultdict(list)
        
    def load_cnt_data(self):
        """Load CNT data from files"""
        print(f"Loading CNT data for {self.scheduler_name}...")
        
        # Load satellites
        self.satellites = loadST(f"{self.cnt_prefix}_satellites.txt")
        
        # Load missions
        self.missions = loadMS(f"{self.cnt_prefix}_missions.txt")
        
        # Load links
        self.link_matrices, self.time_step = loadCNT(f"{self.cnt_prefix}_links.txt")
        self.sim_length = len(self.link_matrices) * self.time_step
        
        # Load routes
        self.routes_over_time = loadRT(f"{self.cnt_prefix}_routes.txt")
        
        # Load allocations if available
        try:
            with open(f"{self.cnt_prefix}_allocations.json", 'r') as f:
                self.allocations_over_time = json.load(f)
        except:
            print(f"No allocations file found, using routes directly")
            self.allocations_over_time = self.routes_over_time
        
        # Map missions to satellite indices
        for i, sat in enumerate(self.satellites):
            for mission_name in sat.missions:
                self.mission_to_sat_idx[mission_name] = i
        
        print(f"Loaded {len(self.missions)} missions, {len(self.satellites)} satellites")
        print(f"Simulation length: {self.sim_length}, time step: {self.time_step}")
        
    def get_available_bandwidth(self, current_time):
        """Get total available bandwidth at current time"""
        time_idx = int(current_time / self.time_step)
        if time_idx >= len(self.allocations_over_time):
            return 0
        
        total_bandwidth = 0
        current_allocations = self.allocations_over_time[time_idx]
        
        if isinstance(current_allocations, dict) and 'missions' in current_allocations:
            for mission_name, mission_alloc in current_allocations['missions'].items():
                if 'allocated' in mission_alloc:
                    total_bandwidth += mission_alloc['allocated']
                elif 'routes' in mission_alloc:
                    for route in mission_alloc['routes']:
                        total_bandwidth += route.get('bandwidth', 0)
        
        return total_bandwidth
    
    def schedule_transmission(self, current_time):
        """To be implemented by subclasses"""
        raise NotImplementedError("Subclasses must implement schedule_transmission")
    
    def update_deliveries(self, current_time):
        """Update delivered items based on current time"""
        delivered_this_step = []
        
        for item in self.in_transit_items[:]:
            if item.delivery_time and item.delivery_time <= current_time:
                self.in_transit_items.remove(item)
                
                # Check if item met deadline
                if item.delivery_time <= item.deadline:
                    self.transmitted_items.append(item)
                else:
                    self.dropped_items.append(item)
                
                delivered_this_step.append(item)
        
        return delivered_this_step
    
    def record_metrics(self, current_time):
        """Record metrics for analysis"""
        self.time_history.append(current_time)
        
        # Record queue sizes per mission/asset
        for mission in self.missions:
            for asset in mission.assets:
                for queue in asset.queues:
                    key = f"{mission.name}_{asset.name}_Q{queue.queue_id}_C{queue.queue_class}"
                    self.queue_sizes_history[key].append(queue.length)
        
        # Record bandwidth usage
        time_idx = int(current_time / self.time_step)
        if time_idx < len(self.bandwidth_usage_history):
            # Already recorded by scheduling
            pass
        else:
            self.bandwidth_usage_history.append(0)
        
        # Record missed deadlines
        for mission in self.missions:
            missed_count = 0
            for asset in mission.assets:
                for queue in asset.queues:
                    # Count items past deadline
                    for deadline in queue.items:
                        if deadline < current_time:
                            missed_count += 1
            self.missed_deadlines_history[mission.name].append(missed_count)
    
    def simulate(self):
        """Run the simulation"""
        print(f"\nStarting {self.scheduler_name} simulation...")
        print(f"Simulation length: {self.sim_length} timesteps")
        
        current_time = 0
        update_interval = self.sim_length / 10
        next_update = update_interval
        
        while current_time <= self.sim_length:
            # Generate new data in queues
            for mission in self.missions:
                for asset in mission.assets:
                    asset.update(current_time, self.time_step)
            
            # Schedule transmissions (implemented by subclass)
            self.schedule_transmission(current_time)
            
            # Update deliveries
            self.update_deliveries(current_time)
            
            # Record metrics
            self.record_metrics(current_time)
            
            # Progress update
            if current_time >= next_update:
                progress = current_time / self.sim_length * 100
                print(f"  Progress: {current_time:.1f}/{self.sim_length} ({progress:.0f}%)")
                next_update += update_interval
            
            current_time += self.time_step
        
        print(f"{self.scheduler_name} simulation complete!")
        self.print_summary()
    
    def print_summary(self):
        """Print simulation summary"""
        print(f"\n{'='*60}")
        print(f"{self.scheduler_name} Simulation Summary")
        print(f"{'='*60}")
        
        # Overall statistics
        total_transmitted = len(self.transmitted_items)
        total_dropped = len(self.dropped_items)
        
        print(f"Total items transmitted: {total_transmitted}")
        print(f"Total items dropped: {total_dropped}")
        if total_transmitted + total_dropped > 0:
            success_rate = total_transmitted / (total_transmitted + total_dropped) * 100
            print(f"Success rate: {success_rate:.1f}%")
        
        # Per-mission statistics
        print(f"\nPer-Mission Statistics:")
        for mission in self.missions:
            mission_transmitted = sum(1 for item in self.transmitted_items 
                                    if item.mission_name == mission.name)
            mission_dropped = sum(1 for item in self.dropped_items 
                                if item.mission_name == mission.name)
            
            print(f"  {mission.name}:")
            print(f"    Transmitted: {mission_transmitted}")
            print(f"    Dropped: {mission_dropped}")
            
            # By class
            for class_id in [3, 2, 1]:
                class_name = {3: "TT", 2: "RC", 1: "BE"}[class_id]
                class_transmitted = sum(1 for item in self.transmitted_items 
                                      if item.mission_name == mission.name 
                                      and item.queue_class == class_id)
                print(f"      {class_name}: {class_transmitted}")
        
        # Bandwidth utilization
        if self.bandwidth_usage_history:
            avg_usage = np.mean(self.bandwidth_usage_history)
            max_usage = np.max(self.bandwidth_usage_history)
            print(f"\nBandwidth Utilization:")
            print(f"  Average: {avg_usage:.1f} Mbps")
            print(f"  Maximum: {max_usage:.1f} Mbps")
    
    def get_metrics_dataframe(self):
        """Return metrics as a pandas DataFrame for analysis"""
        metrics = {
            'scheduler': self.scheduler_name,
            'total_transmitted': len(self.transmitted_items),
            'total_dropped': len(self.dropped_items),
            'success_rate': len(self.transmitted_items) / max(1, len(self.transmitted_items) + len(self.dropped_items)),
            'avg_bandwidth_usage': np.mean(self.bandwidth_usage_history) if self.bandwidth_usage_history else 0,
        }
        
        # Per-class metrics
        for class_id in [3, 2, 1]:
            class_name = {3: "TT", 2: "RC", 1: "BE"}[class_id]
            class_transmitted = sum(1 for item in self.transmitted_items if item.queue_class == class_id)
            class_dropped = sum(1 for item in self.dropped_items if item.queue_class == class_id)
            
            metrics[f'{class_name}_transmitted'] = class_transmitted
            metrics[f'{class_name}_dropped'] = class_dropped
            metrics[f'{class_name}_success_rate'] = class_transmitted / max(1, class_transmitted + class_dropped)
        
        return pd.DataFrame([metrics])
    
    def plot_results(self, save_path=None):
        """Generate visualization plots"""
        fig, axes = plt.subplots(2, 2, figsize=(15, 10))
        fig.suptitle(f'{self.scheduler_name} Performance Metrics', fontsize=16)
        
        # Plot 1: Queue sizes over time
        ax1 = axes[0, 0]
        for key, values in self.queue_sizes_history.items():
            if "C3" in key:  # Only plot TT queues for clarity
                ax1.plot(self.time_history, values, label=key.split('_')[0])
        ax1.set_xlabel('Time')
        ax1.set_ylabel('Queue Length')
        ax1.set_title('TT Queue Lengths Over Time')
        ax1.legend()
        ax1.grid(True)
        
        # Plot 2: Bandwidth usage
        ax2 = axes[0, 1]
        ax2.plot(self.time_history[:len(self.bandwidth_usage_history)], 
                self.bandwidth_usage_history)
        ax2.set_xlabel('Time')
        ax2.set_ylabel('Bandwidth (Mbps)')
        ax2.set_title('Bandwidth Usage Over Time')
        ax2.grid(True)
        
        # Plot 3: Missed deadlines
        ax3 = axes[1, 0]
        for mission_name, missed_history in self.missed_deadlines_history.items():
            ax3.plot(self.time_history[:len(missed_history)], missed_history, label=mission_name)
        ax3.set_xlabel('Time')
        ax3.set_ylabel('Missed Deadlines')
        ax3.set_title('Cumulative Missed Deadlines')
        ax3.legend()
        ax3.grid(True)
        
        # Plot 4: Success rate by class
        ax4 = axes[1, 1]
        class_stats = {}
        for class_id in [3, 2, 1]:
            class_name = {3: "TT", 2: "RC", 1: "BE"}[class_id]
            transmitted = sum(1 for item in self.transmitted_items if item.queue_class == class_id)
            dropped = sum(1 for item in self.dropped_items if item.queue_class == class_id)
            if transmitted + dropped > 0:
                class_stats[class_name] = transmitted / (transmitted + dropped) * 100
            else:
                class_stats[class_name] = 0
        
        ax4.bar(class_stats.keys(), class_stats.values())
        ax4.set_ylabel('Success Rate (%)')
        ax4.set_title('Success Rate by Traffic Class')
        ax4.grid(True, axis='y')
        
        plt.tight_layout()
        
        if save_path:
            plt.savefig(save_path)
            print(f"Plot saved to {save_path}")
        else:
            plt.show()
        
        return fig