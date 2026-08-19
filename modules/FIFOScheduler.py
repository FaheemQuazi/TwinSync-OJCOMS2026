"""
Corrected FIFO (First-In-First-Out) Scheduler
Each mission gets an equal fraction of bandwidth
Within each mission's allocation, serves data in FIFO order (ignoring priorities)
"""

from .BaseScheduler import BaseScheduler, DataItem
from collections import deque

class FIFOScheduler(BaseScheduler):
    """
    Fair FIFO scheduler that:
    - Allocates bandwidth equally among missions
    - Within each mission, serves all data in FIFO order regardless of class/priority
    """
    
    def __init__(self, cnt_prefix="CNT"):
        super().__init__(cnt_prefix, scheduler_name="FIFO")
        
    def schedule_transmission(self, current_time):
        """
        Fair FIFO scheduling: 
        1. Divide bandwidth equally among missions
        2. Within each mission, serve items in FIFO order
        """
        time_idx = int(current_time / self.time_step)
        if time_idx >= len(self.allocations_over_time):
            return
        
        # Get total available bandwidth
        total_bandwidth = self.get_available_bandwidth(current_time)
        if total_bandwidth <= 0:
            return
        
        # Divide bandwidth equally among active missions
        active_missions = [m for m in self.missions if len(m.assets) > 0]
        if len(active_missions) == 0:
            return
            
        bandwidth_per_mission = total_bandwidth / len(active_missions)
        total_bandwidth_used = 0
        
        # Process each mission with its fair share
        for mission in active_missions:
            mission_bandwidth = bandwidth_per_mission
            mission_items = []
            
            # Collect all items from this mission in FIFO order
            for asset in mission.assets:
                for queue in asset.queues:
                    # Get items from queue
                    temp_items = []
                    while queue.length > 0:
                        deadline = queue.dequeue()
                        if deadline is not None:
                            # Create item with metadata
                            item = DataItem(
                                item_id=self.item_counter,
                                generation_time=current_time - (deadline - current_time),  # Approximate when it was generated
                                deadline=deadline,
                                size=queue.item_size,
                                mission_name=mission.name,
                                asset_name=asset.name,
                                queue_class=queue.queue_class
                            )
                            self.item_counter += 1
                            
                            # Store with original queue for requeuing
                            temp_items.append((item, queue))
                    
                    # Add to mission's item list (FIFO order preserved by generation time)
                    mission_items.extend(temp_items)
            
            # Sort mission items by generation time (true FIFO within mission)
            mission_items.sort(key=lambda x: x[0].generation_time)
            
            # Transmit what we can with this mission's bandwidth
            items_transmitted = []
            items_to_requeue = []
            
            for item, original_queue in mission_items:
                if mission_bandwidth >= item.size:
                    # Can transmit this item
                    item.transmission_start = current_time
                    item.delivery_time = current_time + 0.2  # Simple fixed delay
                    self.in_transit_items.append(item)
                    mission_bandwidth -= item.size
                    total_bandwidth_used += item.size
                    items_transmitted.append(item)
                else:
                    # Can't transmit, need to requeue
                    items_to_requeue.append((item, original_queue))
            
            # Requeue items that couldn't be transmitted
            for item, original_queue in items_to_requeue:
                original_queue.enqueue(item.deadline)
        
        # Record bandwidth usage
        self.bandwidth_usage_history.append(total_bandwidth_used)
        
    def get_available_bandwidth(self, current_time):
        """Get total bandwidth available at current time"""
        # Sum bandwidth across all missions
        total = 0
        time_idx = int(current_time / self.time_step)
        
        if time_idx < len(self.allocations_over_time):
            allocations = self.allocations_over_time[time_idx]
            for mission_name, mission_alloc in allocations.items():
                if 'allocated' in mission_alloc:
                    total += mission_alloc['allocated']
        
        return total