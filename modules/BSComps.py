import numpy as np
from math import *
import math
import re
import random
from collections import defaultdict, deque

SEED = 456
random.seed(SEED)
np.random.seed(SEED)  # Also set numpy seed for consistency

# TwinSync Components
class Queue:
    """Queue class representing a data stream from an asset"""
    def __init__(self, asset_id, queue_id, queue_class, priority, item_size, data_rate=1.0):
        self.asset_id = asset_id      # Asset this queue belongs to
        self.queue_id = queue_id      # Unique identifier for this queue
        # Validate queue class
        if queue_class not in [1, 2, 3]:
            raise ValueError(f"Invalid queue class {queue_class}. Must be 1 (BE), 2 (RC), or 3 (TT)")
        self.queue_class = queue_class  # TT(3), RC(2), or BE(1)
        # Validate other parameters
        if priority < 0:
            raise ValueError(f"Priority must be non-negative, got {priority}")
        if item_size <= 0:
            raise ValueError(f"Item size must be positive, got {item_size}")
        if data_rate < 0:
            raise ValueError(f"Data rate must be non-negative, got {data_rate}")
        self.priority = priority      # Priority within class
        self.item_size = item_size    # Size of each item in the queue
        self.data_rate = data_rate    # Items per time unit
        self.items = deque()          # Items with deadlines
        self.length = 0               # Current queue length
    
    def enqueue(self, deadline):
        """Add an item to the queue with a specified deadline"""
        self.items.append(deadline)
        self.length += 1
    
    def dequeue(self):
        """Remove an item from the queue and return its deadline"""
        if self.length > 0:
            self.length -= 1
            return self.items.popleft()
        return None
    
    def update_length(self, current_time, arrivals=0, departures=0):
        """Update queue length based on arrivals and departures (Equation 4 from paper)"""
        # Process departures first
        for _ in range(min(departures, len(self.items))):
            self.dequeue()
        
        # Process arrivals with appropriate deadlines
        for _ in range(arrivals):
            # Deadline based on queue class: TT=strict, RC=moderate, BE=relaxed
            if self.queue_class == 3:  # TT
                deadline = current_time + 100
            elif self.queue_class == 2:  # RC
                deadline = current_time + 300
            else:  # BE
                deadline = current_time + 500
            self.enqueue(deadline)
        
        # Update length (though enqueue/dequeue already handle this)
        self.length = len(self.items)
    
    def get_aos(self, current_time):
        """Calculate Age of Synchronization for this queue"""
        if not self.items:
            return float('inf')  # Queue is empty
        
        # Modified AoS from the paper: time until deadline for future events
        earliest_deadline = min(self.items)
        if current_time < earliest_deadline:
            # Future deadline: return time until deadline
            return earliest_deadline - current_time
        else:
            # Past deadline: return how late we are
            return current_time - earliest_deadline
    
    def export(self):
        """Export queue definition to string format"""
        return f"QUEUE[{self.asset_id},{self.queue_id},{self.queue_class},{self.priority},{self.item_size},{self.data_rate}]"
    
    @staticmethod
    def parse(export_str):
        """Parse queue from export string"""
        pattern = r'QUEUE\[([^,]+),([^,]+),([^,]+),([^,]+),([^,]+),([^,]+)\]'
        match = re.match(pattern, export_str)
        if match:
            return Queue(
                asset_id=match.group(1),
                queue_id=int(match.group(2)),
                queue_class=int(match.group(3)),
                priority=float(match.group(4)),
                item_size=float(match.group(5)),
                data_rate=float(match.group(6))
            )
        else:
            # Try parsing old format without data_rate
            pattern_old = r'QUEUE\[([^,]+),([^,]+),([^,]+),([^,]+),([^,]+)\]'
            match_old = re.match(pattern_old, export_str)
            if match_old:
                return Queue(
                    asset_id=match_old.group(1),
                    queue_id=int(match_old.group(2)),
                    queue_class=int(match_old.group(3)),
                    priority=float(match_old.group(4)),
                    item_size=float(match_old.group(5)),
                    data_rate=1.0  # Default rate
                )
        return None
    
    def __str__(self):
        return f"Queue[Asset:{self.asset_id}, ID:{self.queue_id}, Class:{self.queue_class}, " \
               f"Priority:{self.priority}, Size:{self.item_size}, Rate:{self.data_rate}, Length:{self.length}]"


class Asset:
    """Represents a lunar asset with multiple data streams (queues)"""
    def __init__(self, asset_id, name, mission_id):
        self.asset_id = asset_id
        self.name = name
        self.mission_id = mission_id
        self.queues = []
        self.queue_accumulators = {}  # Track fractional items per queue
    
    def __str__(self):
        return f"Asset[ID:{self.asset_id}, Name:{self.name}, Mission:{self.mission_id}, Queues:{len(self.queues)}]"
    
    def add_queue(self, queue_class, priority, item_size, data_rate):
        """Add a new queue to this asset"""
        queue_id = len(self.queues)
        new_queue = Queue(self.asset_id, queue_id, queue_class, priority, item_size, data_rate)
        self.queues.append(new_queue)
        self.queue_accumulators[queue_id] = 0.0  # Initialize accumulator
        return new_queue
    
    def update(self, current_time, time_step):
        """Generate new data for all queues based on their data rates"""
        for queue in self.queues:
            # Add fractional items to accumulator
            self.queue_accumulators[queue.queue_id] += queue.data_rate * time_step
            
            # Generate whole items when accumulator >= 1
            new_items = int(self.queue_accumulators[queue.queue_id])
            if new_items > 0:
                self.queue_accumulators[queue.queue_id] -= new_items
                
                # Add new items with deadlines
                for _ in range(new_items):
                    # Set deadline based on class
                    if queue.queue_class == 3:  # TT (Time-Triggered)
                        deadline = current_time + random.uniform(1, 3)  # Strict deadline
                    elif queue.queue_class == 2:  # RC (Rate-Constrained)
                        deadline = current_time + random.uniform(3, 6)  # Medium deadline
                    else:  # BE (Best-Effort)
                        deadline = current_time + random.uniform(6, 10)  # Relaxed deadline
                    
                    queue.enqueue(deadline)
    
    def get_total_bandwidth_requirement(self):
        """Calculate total bandwidth requirement for this asset"""
        return sum(q.item_size * q.data_rate for q in self.queues)
    
    def export(self):
        """Export asset definition to string format"""
        return f"ASSET[{self.asset_id},{self.name},{self.mission_id}]"
    
    @staticmethod
    def parse(export_str):
        """Parse asset from export string"""
        pattern = r'ASSET\[([^,]+),([^,]+),([^,]+)\]'
        match = re.match(pattern, export_str)
        if match:
            asset = Asset(
                asset_id=match.group(1),
                name=match.group(2),
                mission_id=match.group(3)
            )
            # Initialize queue_accumulators dict
            asset.queue_accumulators = {}
            return asset
        return None


class MissionDef:
    """Mission definition with associated assets and bandwidth requirements"""
    
    _pattern = r'MD\[([^,]+),([0-9.]+)\]'

    def __init__(self, name: str, reqbw: float = 0.0):
        # Parse if string input matches pattern
        match = re.match(self._pattern, name)
        if match:
            self.name = match.group(1)
            self.reqbw = float(match.group(2))
        else:
            self.name = name
            self.reqbw = reqbw
        
        self.assets = []
        self.mission_id = f"M_{self.name}"
    
    def __str__(self):
        return f"Mission[Name:{self.name}, BW:{self.reqbw:.2f}, Assets:{len(self.assets)}]"
    
    def generate_assets(self, num_assets=None, asset_configs=None):
        """Generate assets with realistic queue configurations"""
        if num_assets is None:
            num_assets = random.randint(1, 3)
        
        if asset_configs is None:
            # Default configurations for different asset types
            asset_configs = [
                # High-priority sensor (e.g., life support monitoring)
                {
                    'name': 'LifeSupport',
                    'queues': [
                        {'class': 3, 'priority': 10, 'size': 0.1, 'rate': 5.0},  # TT - critical telemetry
                        {'class': 2, 'priority': 8, 'size': 0.5, 'rate': 2.0},   # RC - status updates
                    ]
                },
                # Science instrument
                {
                    'name': 'ScienceInstrument',
                    'queues': [
                        {'class': 2, 'priority': 6, 'size': 2.0, 'rate': 1.0},   # RC - processed data
                        {'class': 1, 'priority': 4, 'size': 10.0, 'rate': 0.5},  # BE - raw data dumps
                    ]
                },
                # Rover/mobility system
                {
                    'name': 'Rover',
                    'queues': [
                        {'class': 3, 'priority': 9, 'size': 0.2, 'rate': 3.0},   # TT - position/status
                        {'class': 2, 'priority': 5, 'size': 1.0, 'rate': 1.0},   # RC - navigation data
                        {'class': 1, 'priority': 2, 'size': 5.0, 'rate': 0.3},   # BE - camera feeds
                    ]
                }
            ]
        
        # Select random asset configurations
        selected_configs = random.sample(asset_configs, min(num_assets, len(asset_configs)))
        
        for i, config in enumerate(selected_configs):
            asset_id = f"{self.mission_id}_A{i}"
            asset = Asset(asset_id, config['name'], self.mission_id)
            
            # Add queues to asset
            for q_config in config['queues']:
                asset.add_queue(
                    queue_class=q_config['class'],
                    priority=q_config['priority'],
                    item_size=q_config['size'],
                    data_rate=q_config['rate']
                )
            
            self.assets.append(asset)
        
        # Update required bandwidth based on generated assets
        self.update_bandwidth_requirement()
    
    def update_bandwidth_requirement(self):
        """Update mission bandwidth requirement based on assets"""
        total_bw = sum(asset.get_total_bandwidth_requirement() for asset in self.assets)
        # Add 10% overhead for protocol and control data
        self.reqbw = total_bw * 1.1
    
    def add_asset(self, asset):
        """Add an asset to this mission"""
        asset.mission_id = self.mission_id
        self.assets.append(asset)
        self.update_bandwidth_requirement()
    
    def get_all_queues(self):
        """Get all queues from all assets in this mission"""
        all_queues = []
        for asset in self.assets:
            all_queues.extend(asset.queues)
        return all_queues
    
    def export(self):
        """Export mission definition to string format"""
        return f"MD[{self.name},{self.reqbw}]"
    
    def export_full(self):
        """Export complete mission hierarchy including assets and queues"""
        lines = []
        lines.append(f"# MISSION: {self.export()}")
        for asset in self.assets:
            lines.append(f"# {asset.export()}")
            for queue in asset.queues:
                lines.append(queue.export())
        lines.append("# END_MISSION")
        return '\n'.join(lines)


# CNT Components
class Body2D():
    name = "Body"
    pos = (0,0)     # center
    size = 0        # radius [km * 100]

    _pattern = r'BODY\[([^,\[\]]+),(\([^)]+\)),([0-9.]+)\]'
    
    def __init__(self, n: str, p = (0,0), s = 0):
        match = re.match(self._pattern, n)
        if match:
            self.name = match.group(1)
            pos_str = match.group(2)
            pos_tuple = eval(pos_str)
            self.pos = pos_tuple
            self.size = float(match.group(3))
        else:
            self.name = n
            self.pos = p
            self.size =  s

    def export(self):
        return f"BODY[{self.name},{self.pos},{self.size}]"
    

class Orbit2D():
    dist = 0    # distance from center (km)
    pshift = 0  # phase-shift in orbit (rad)

    _pattern = r'ORBIT\[(.*?),(.*?)\]'

    def __init__(self, d, p):
        # Check if input is in string format
        if isinstance(d, str):
            match = re.match(self._pattern, d)
            if match:
                self.dist = float(match.group(1))
                self.pshift = float(match.group(2))
        else:
            self.dist = d
            self.pshift = p

    def getPos(self, at):
        angle = (at + self.pshift) % (2 * pi)
        return (self.dist * cos(angle), self.dist * sin(angle))
    
    def export(self):
        return f"ORBIT[{self.dist},{self.pshift}]"
    

class Satellite():
    name = "Sat"
    Orb = None      # Orbit Definition
    Bod = None      # Body around which to orbit
    speed = pi / 32 # rad/s (sim metric, not real speed)
    cap = [1]       # technical capability (technology filter)
    _ct = 0         # current time
    
    def __init__(self, n, o, b, s, c=[1]):
        self.name = n
        self.Orb = o
        self.Bod = b
        self.speed = s
        self.cap = c

    def pos(self):
        oP = self.Orb.getPos(self.speed * self._ct)
        bP = self.Bod.pos
        return (oP[0] + bP[0], oP[1] + bP[1])

    def tick(self, dt):
        self._ct += dt
        
    def export(self):
        cap_str = "|".join(map(str, self.cap))
        return f"{self.name},{self.Orb.export()},{self.Bod.export()},{self.speed},{cap_str}"
    
    @staticmethod
    def parseStr(export_str):
        parts = []
        current = ""
        bracket_count = 0

        for char in export_str:
            if char == ',' and bracket_count == 0:
                parts.append(current)
                current = ""
            else:
                current += char
                if char == '[':
                    bracket_count += 1
                elif char == ']':
                    bracket_count -= 1
        parts.append(current)

        name = parts[0]
        orbit = Orbit2D(parts[1], 0)  # The p parameter won't be used
        body = Body2D(parts[2])
        speed = float(parts[3])
        capabilities = list(map(int, parts[4].split('|')))

        return Satellite(name, orbit, body, speed, capabilities)


# Constants
# Visual scale: 1 unit = 100 km, but compressed 10x for visualization
RADIUS_EARTH = 127.5627 / 2 # Real: 6,371 km → 63.71 units
RADIUS_MOON = (RADIUS_EARTH * 0.27) # Real: 1,737 km → 17.37 units
VISUAL_SCALE_FACTOR = 10  # Visual positions are compressed by this factor

# Real distances in km
REAL_EARTH_RADIUS = 6371  # km
REAL_MOON_RADIUS = 1737   # km
REAL_EARTH_MOON_DISTANCE = 384400  # km
    
B_Earth = Body2D("Earth", (0,0), RADIUS_EARTH)
B_Moon = Body2D("Moon", (-384.4, 0), RADIUS_MOON)  # Visual position (compressed)


# Utility functions
# Utility functions
def calculate_real_distance(pos1, pos2):
    """
    Calculate real distance in kilometers between two positions.
    Accounts for the visual scale compression.
    """
    # Visual distance
    visual_distance = np.sqrt((pos1[0] - pos2[0])**2 + (pos1[1] - pos2[1])**2)
    
    # Convert to real distance
    # Since visual scale is compressed by 10x, multiply by 10
    # Then multiply by 100 to convert from units to km
    real_distance = visual_distance * VISUAL_SCALE_FACTOR * 100
    
    return real_distance


def line_circle_intersection(x1, y1, x2, y2, cx, cy, radius):
    """
    Check if a line segment intersects with a circle.
    
    Returns:
        True if NO intersection (line of sight is clear)
        False if intersection exists (line of sight is blocked)
    """
    """
    Check if a line segment intersects with a circle.
    
    Returns:
        True if NO intersection (line of sight is clear)
        False if intersection exists (line of sight is blocked)
    """
    # Translate the circle and line segment so that the circle is at the origin
    x1 -= cx
    y1 -= cy
    x2 -= cx
    y2 -= cy

    # Calculate the coefficients of the quadratic equation
    dx = x2 - x1
    dy = y2 - y1
    a = dx * dx + dy * dy
    b = 2 * (x1 * dx + y1 * dy)
    c = x1 * x1 + y1 * y1 - radius * radius

    # Calculate the discriminant
    discriminant = b * b - 4 * a * c

    # Check if the line segment intersects the circle
    if discriminant < 0:
        return True  # No intersection
    else:
        t1 = (-b + math.sqrt(discriminant)) / (2 * a)
        t2 = (-b - math.sqrt(discriminant)) / (2 * a)
        if (0 <= t1 <= 1) or (0 <= t2 <= 1):
            return False  # Intersection occurs within the line segment
        else:
            return True  # Intersection does not occur within the line segment


# Loading functions
def loadLA(filename):
    CNT = []
    with open(filename, 'r') as f:
        content = f.read()
        matrix_strings = content.split("# ENDMAT\n")
        for matrix_string in matrix_strings:
            if matrix_string.strip():
                matrix = np.loadtxt(matrix_string.strip().split('\n'), delimiter=',', dtype=int)
                CNT.append(matrix)
    print(f"CNT loaded from {filename}")
    return CNT


def loadMD(filename):
    missions = []
    with open(filename, 'r') as f:
        for line in f:
            # Create MissionDef object from the line
            mission = MissionDef(line.strip())
            missions.append(mission)
    print(f"Mission data loaded from {filename}")
    return missions


def loadMissionHierarchy(filename):
    """Load complete mission hierarchy including assets and queues"""
    missions = []
    current_mission = None
    current_asset = None
    
    with open(filename, 'r') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
                
            if line.startswith("# MISSION:"):
                # Parse mission definition
                md_str = line.replace("# MISSION: ", "")
                current_mission = MissionDef(md_str)
                missions.append(current_mission)
                
            elif line.startswith("# ASSET"):
                # Parse asset definition
                asset_str = line.replace("# ", "")
                current_asset = Asset.parse(asset_str)
                if current_mission and current_asset:
                    current_mission.add_asset(current_asset)
                    
            elif line.startswith("QUEUE["):
                # Parse queue definition
                queue = Queue.parse(line)
                if current_asset and queue:
                    current_asset.queues.append(queue)
                    # Initialize accumulator for this queue
                    current_asset.queue_accumulators[queue.queue_id] = 0.0
                    
            elif line == "# END_MISSION":
                current_mission = None
                current_asset = None
    
    print(f"Mission hierarchy loaded from {filename}")
    return missions


def saveMissionHierarchy(missions, filename):
    """Save complete mission hierarchy to file"""
    with open(filename, 'w') as f:
        for mission in missions:
            f.write(mission.export_full() + '\n')
    print(f"Mission hierarchy saved to {filename}")


def loadRT(filename):
    routes = []
    with open(filename, 'r') as f:
        for line in f:
            # Split the line by '|' to get individual routes
            matrix_routes = line.strip().split('|')
            # Remove empty strings and convert each route to list of integers
            matrix_routes = [
                [int(x) for x in route.split(',')]
                for route in matrix_routes if route
            ]
            routes.append(matrix_routes)
    print(f"Route data loaded from {filename}")
    return routes


def loadST(filename):
    satellites = []
    with open(filename, 'r') as f:
        for line in f:
            mission = Satellite.parseStr(line.strip())
            satellites.append(mission)
    print(f"Satellite data loaded from {filename}")
    return satellites


def loadBW(filename='3_ALLOCATION.txt'):
    """
    Parse the allocation file and return the data structure containing allocations.
    Returns: List of lists where:
    - Outer list index represents time slice
    - Inner list contains bandwidth allocations for each mission in that time slice
    """
    allocations = []
    try:
        with open(filename, 'r') as f:
            for line in f:
                # Convert each line into list of floats
                time_slice_alloc = [float(x) for x in line.strip().split()]
                allocations.append(time_slice_alloc)
        return allocations
    except FileNotFoundError:
        print(f"Error: {filename} not found")
        return None
    except Exception as e:
        print(f"Error parsing {filename}: {e}")
        return None


# Example usage
if __name__ == "__main__":
    # Create sample missions with assets
    mission1 = MissionDef("ArtemisIII", 10.0)
    mission1.generate_assets(num_assets=2)
    
    mission2 = MissionDef("CommercialLander", 5.0)
    mission2.generate_assets(num_assets=1)
    
    # Save to file
    missions = [mission1, mission2]
    saveMissionHierarchy(missions, "mission_hierarchy.txt")
    
    # Load from file
    loaded_missions = loadMissionHierarchy("mission_hierarchy.txt")
    
    # Display loaded missions
    for mission in loaded_missions:
        print(f"\nMission: {mission.name} (BW: {mission.reqbw})")
        for asset in mission.assets:
            print(f"  Asset: {asset.name}")
            for queue in asset.queues:
                print(f"    {queue}")