"""
Round-Robin Scheduler (DSN-style)
Each mission gets exclusive network access for a fixed time slice
No priority handling - just empty queues during the mission's slot
"""

from .BaseScheduler import BaseScheduler, DataItem

class RoundRobinScheduler(BaseScheduler):
    """
    Round-Robin scheduler mimicking Deep Space Network operations
    - Each mission gets exclusive network access for a time slice
    - During its slot, mission sends whatever it can (no priorities)
    - After time slice, switches to next mission
    - Cycles through all missions equally
    """
    
    def __init__(self, cnt_prefix="CNT", time_slice_duration=600):
        """
        Initialize Round-Robin scheduler
        
        Args:
            cnt_prefix: Prefix for CNT data files
            time_slice_duration: Duration of each mission's exclusive slot (seconds)
                                Default 600 = 10 minutes
        """
        super().__init__(cnt_prefix, scheduler_name="RoundRobin")
        self.time_slice_duration = time_slice_duration
        self.current_mission_index = 0
        self.last_switch_time = 0
        
    def get_active_mission(self, current_time):
        """
        Determine which mission has exclusive access at current time
        
        Returns:
            Active mission object or None
        """
        if not self.missions:
            return None
        
        # Calculate which time slot we're in
        slot_number = int(current_time / self.time_slice_duration)
        
        # Determine active mission (round-robin through all missions)
        active_mission_idx = slot_number % len(self.missions)
        
        # Check if we need to log a mission switch
        if active_mission_idx != self.current_mission_index:
            self.current_mission_index = active_mission_idx
            print(f"  [RoundRobin] t={current_time}: Switching to mission "
                  f"{self.missions[active_mission_idx].name} "
                  f"(slot {slot_number}, duration {self.time_slice_duration}s)")
        
        return self.missions[active_mission_idx]
    
    def schedule_transmission(self, current_time):
        """
        Round-robin scheduling: give all bandwidth to one mission at a time
        """
        time_idx = int(current_time / self.time_step)
        if time_idx >= len(self.allocations_over_time):
            return
        
        # Get available bandwidth
        total_bandwidth = self.get_available_bandwidth(current_time)
        if total_bandwidth <= 0:
            return
        
        # Get the active mission for this time slot
        active_mission = self.get_active_mission(current_time)
        if not active_mission:
            return
        
        bandwidth_used = 0
        items_transmitted = []
        
        # Give ALL bandwidth to the active mission
        # Process its queues without any priority - just empty what we can
        for asset in active_mission.assets:
            for queue in asset.queues:
                # Transmit as many items as bandwidth allows
                while queue.length > 0 and bandwidth_used + queue.item_size <= total_bandwidth:
                    deadline = queue.dequeue()
                    if deadline is not None:
                        # Create and transmit item
                        item = DataItem(
                            item_id=self.item_counter,
                            generation_time=deadline - 5,  # Approximate
                            deadline=deadline,
                            size=queue.item_size,
                            mission_name=active_mission.name,
                            asset_name=asset.name,
                            queue_class=queue.queue_class
                        )
                        self.item_counter += 1
                        
                        item.transmission_start = current_time
                        item.delivery_time = current_time + 0.2  # Fixed delay
                        self.in_transit_items.append(item)
                        
                        bandwidth_used += queue.item_size
                        items_transmitted.append(item)
        
        # All other missions get NOTHING during this slot
        # Their queues will build up until their turn
        
        # Record bandwidth usage
        self.bandwidth_usage_history.append(bandwidth_used)
        
        # Log activity at slot boundaries and periodically
        time_in_slot = current_time % self.time_slice_duration
        if time_in_slot == 0 or (current_time % 100 == 0 and len(items_transmitted) > 0):
            print(f"  [RoundRobin] t={current_time}: Mission {active_mission.name} "
                  f"transmitted {len(items_transmitted)} items, "
                  f"used {bandwidth_used:.1f}/{total_bandwidth:.1f} Mbps")
            
            # Show what types were sent (no priority, just for info)
            if items_transmitted:
                class_counts = {3: 0, 2: 0, 1: 0}
                for item in items_transmitted:
                    class_counts[item.queue_class] += 1
                
                class_names = {3: "TT", 2: "RC", 1: "BE"}
                class_str = ", ".join([f"{class_names[c]}:{count}" 
                                      for c, count in class_counts.items() if count > 0])
                print(f"              Mix: {class_str} (no priority applied)")
        
        # Warn about idle time if mission has no data
        if bandwidth_used == 0 and time_in_slot == 0:
            print(f"  [RoundRobin] t={current_time}: WARNING - Mission {active_mission.name} "
                  f"has no data, wasting time slot!")
    
    def print_summary(self):
        """Print Round-Robin specific summary"""
        super().print_summary()
        
        print(f"\nRound-Robin Specific Metrics:")
        print(f"  Time slice duration: {self.time_slice_duration} seconds")
        print(f"  Slots per mission: {int(self.sim_length / self.time_slice_duration / len(self.missions))}")
        
        # Calculate per-mission slot utilization
        print(f"\n  Slot Utilization by Mission:")
        
        for mission in self.missions:
            mission_transmitted = sum(1 for item in self.transmitted_items 
                                    if item.mission_name == mission.name)
            
            # Estimate how many slots this mission had
            total_slots = int(self.sim_length / self.time_slice_duration)
            mission_slots = total_slots // len(self.missions)
            
            if mission_slots > 0:
                avg_items_per_slot = mission_transmitted / mission_slots
                print(f"    {mission.name}: {avg_items_per_slot:.1f} items/slot "
                      f"({mission_slots} slots total)")
        
        # Calculate waiting time impact on deadlines
        print(f"\n  Deadline Impact:")
        
        # For each class, check deadline performance
        for class_id in [3, 2, 1]:
            class_name = {3: "TT", 2: "RC", 1: "BE"}[class_id]
            
            class_items = [item for item in self.transmitted_items + self.dropped_items 
                          if item.queue_class == class_id]
            
            if class_items:
                on_time = sum(1 for item in class_items if item in self.transmitted_items)
                late = len(class_items) - on_time
                
                print(f"    {class_name}: {on_time}/{len(class_items)} on-time "
                      f"({on_time/len(class_items)*100:.1f}%)")
                
                # Calculate average wait time
                wait_times = []
                for item in class_items:
                    if item.transmission_start:
                        wait = item.transmission_start - item.generation_time
                        wait_times.append(wait)
                
                if wait_times:
                    avg_wait = sum(wait_times) / len(wait_times)
                    max_wait = max(wait_times)
                    print(f"        Avg wait: {avg_wait:.1f}s, Max wait: {max_wait:.1f}s")
        
        # Mission fairness (should be perfect for round-robin)
        mission_counts = {}
        for mission in self.missions:
            mission_counts[mission.name] = sum(1 for item in self.transmitted_items 
                                              if item.mission_name == mission.name)
        
        if len(mission_counts) > 1:
            counts = list(mission_counts.values())
            # Jain's fairness index
            if sum(counts) > 0:
                jains = (sum(counts) ** 2) / (len(counts) * sum(c ** 2 for c in counts))
                print(f"\n  Mission Fairness (Jain's Index): {jains:.3f}")
                print(f"  (Expected ~1.0 for equal time slices)")
        
        # Bandwidth waste analysis
        if self.bandwidth_usage_history:
            zero_usage_steps = sum(1 for usage in self.bandwidth_usage_history if usage == 0)
            waste_percentage = zero_usage_steps / len(self.bandwidth_usage_history) * 100
            
            print(f"\n  Bandwidth Efficiency:")
            print(f"    Idle timesteps: {zero_usage_steps}/{len(self.bandwidth_usage_history)} "
                  f"({waste_percentage:.1f}% waste)")
            
            # Average utilization during active periods
            active_usage = [u for u in self.bandwidth_usage_history if u > 0]
            if active_usage:
                avg_active = sum(active_usage) / len(active_usage)
                print(f"    Avg usage when active: {avg_active:.1f} Mbps")