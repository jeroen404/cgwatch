import os

SYSFS_CGROUP_PATH = "/sys/fs/cgroup"

class CGroup:
    def __init__(self, name, parent=None):
        self.name = name
        self.parent = parent
        self.children = []
        if parent:
            parent.add_child(self)
        self.cpu_usage_history = CGroupCPUUsageHistory(self)

    def add_child(self, child):
        self.children.append(child)
    def get_path(self):
        if self.parent:
            return f"{self.parent.get_path()}/{self.name}"
        return self.name
    def get_sysfs_path(self):
        return f"{SYSFS_CGROUP_PATH}/{self.get_path()}"
    def get_sysfs_children(self):
        # read the sysfs directory to find child cgroups
        path = self.get_sysfs_path()
        try:
            entries = os.listdir(path)
            children = []
            for entry in entries:
                entry_path = os.path.join(path, entry)
                if os.path.isdir(entry_path):
                    children.append(entry)
            return children
        except FileNotFoundError:
            return []
    def build_subtree(self):
        children_names = self.get_sysfs_children()
        for child_name in children_names:
            child_cgroup = CGroup(child_name, parent=self)
            child_cgroup.build_subtree()
    def update_subtree(self):
        # Update existing children and add new ones
        current_children_names = set(self.get_sysfs_children())
        existing_children_names = set(child.name for child in self.children)
        # Remove deleted children
        self.children = [child for child in self.children if child.name in current_children_names]
        # Update existing children
        for child in self.children:
            child.update_subtree()
        # Add new children
        for child_name in current_children_names - existing_children_names:
            child_cgroup = CGroup(child_name, parent=self)
            child_cgroup.build_subtree()
    def get_memory_limit(self):
        limit_file = os.path.join(self.get_sysfs_path(), "memory.max")
        try:
            with open(limit_file, "r") as f:
                limit = f.read().strip()
                return limit
        except FileNotFoundError:
            return "max"
    def has_memory_limit(self):
        limit = self.get_memory_limit()
        return limit != "max"
    def get_current_memory_usage(self):
        usage_file = os.path.join(self.get_sysfs_path(), "memory.current")
        try:
            with open(usage_file, "r") as f:
                usage = f.read().strip()
                return usage
        except FileNotFoundError:
            return "0"
    def get_memory_cache(self):
        stat_file = os.path.join(self.get_sysfs_path(), "memory.stat")
        try:
            with open(stat_file, "r") as f:
                lines = f.readlines()
                for line in lines:
                    key, value = line.strip().split()
                    if key == "file":
                        return int(value)
                return 0
        except FileNotFoundError:
            return 0
    def get_effective_memory_usage(self):
        return max(0, int(self.get_current_memory_usage()) - self.get_memory_cache())
    def get_percent_memory_usage(self):
        limit = self.get_memory_limit()
        usage = self.get_effective_memory_usage()
        if limit == "max" or int(limit) == 0:
            return 0.0
        return (int(usage) / int(limit)) * 100
    def get_cpu_quotum(self):
        quota_file = os.path.join(self.get_sysfs_path(), "cpu.max")
        try:
            with open(quota_file, "r") as f:
                quotaline = f.read().strip()
                quota_value = quotaline.split()[0]
                quota_unit = quotaline.split()[1]
                if quota_value == "max":
                    return "max"
                quota = int(quota_value) / int(quota_unit) * 100
                return quota
        except FileNotFoundError:
            return "max"
    def has_cpu_quota(self):
        quota = self.get_cpu_quotum()
        return quota != "max"
    # usage_usec 212910650
    # user_usec 141909552
    # system_usec 71001097
    # nice_usec 0
    # nr_periods 196783
    # nr_throttled 311
    # throttled_usec 89540730
    # nr_bursts 0
    # burst_usec 0
    def get_cpu_stat(self):
        usage_file = os.path.join(self.get_sysfs_path(), "cpu.stat")
        try:
            with open(usage_file, "r") as f:
                lines = f.readlines()
                usage_dict = {}
                for line in lines:
                    key, value = line.strip().split()
                    usage_dict[key] = value
                return usage_dict
        except FileNotFoundError:
            return {}
    def refresh_cpu_usage_history(self):
        stat = self.get_cpu_stat()
        self.cpu_usage_history.refresh(stat)
    def get_cpu_last_usage_percent(self):
        self.refresh_cpu_usage_history()
        return self.cpu_usage_history.get_last_cpu_usage_percent()
    def throttled_since_last(self):
        return self.cpu_usage_history.throttled_since_last()
    def get_short_name(self):
        name = self.name
        if "@" in name:
            name = name.split("@")[0]
        if name.startswith("app-"):
            name = name[4:]
        name = name.replace("\\x2d", "-")
        return name
    
    def __repr__(self):
        return f"CGroup(name={self.name}, children={len(self.children)})"       


class CGroupCPUUsageHistory:
    PERIOD_USEC = 100000  # cgroup cpu period in microseconds    
    def __init__(self, cgroup: CGroup, max_length: int = 60):
        self.cgroup = cgroup
        self.max_length = max_length
        self.usage_history = []
    def refresh(self,stat: dict):
        self.usage_history.append(stat)
        if len(self.usage_history) > self.max_length:
            self.usage_history.pop(0)
    def get_last_cpu_usage_percent(self):
        if len(self.usage_history) >= 2:
            periods_diff = int(self.usage_history[-1].get("nr_periods", 0)) - int(self.usage_history[-2].get("nr_periods", 0))
            usage_diff = int(self.usage_history[-1].get("usage_usec", 0)) - int(self.usage_history[-2].get("usage_usec", 0))
            usec_passed = periods_diff * self.PERIOD_USEC
            cpu_percent = (usage_diff / usec_passed) * 100 if usec_passed > 0 else 0.0
            return cpu_percent
        return 0.0
    def throttled_since_last(self):
        if len(self.usage_history) >= 2:
            throttled_diff = int(self.usage_history[-1].get("nr_throttled", 0)) - int(self.usage_history[-2].get("nr_throttled", 0))
            # Clamp negatives: nr_throttled is a cumulative counter that resets
            # to 0 when the service restarts (new cgroup, fresh counters), which
            # would otherwise yield a bogus negative "throttled since last".
            # Mirrors logic.js throttledDelta()'s clamp.
            return max(0, throttled_diff)
        return 0
            

def _walk(cgroup):
    """Pre-order depth-first traversal of a cgroup subtree: `cgroup`
    itself, then each child's subtree, in child-list order.

    Shared by `CGroupTree.all_cgroups`/`map_cgroups`/`filter_cgroups` and
    `__repr__`, which previously each hand-rolled an identical recursive
    `traverse()` closure.
    """
    yield cgroup
    for child in cgroup.children:
        yield from _walk(child)


def _depth(cgroup):
    """Distance from `cgroup` to the tree root, by walking `.parent`."""
    depth = 0
    node = cgroup.parent
    while node is not None:
        depth += 1
        node = node.parent
    return depth


class CGroupTree:

    def __init__(self, root_name):
        self.root = CGroup(root_name)
        self.build_tree()

    def build_tree(self):
        self.root.build_subtree()
    def update_tree(self):
        self.root.update_subtree()

    def all_cgroups(self):
        return list(_walk(self.root))
    def map_cgroups(self, func):
        return [func(cg) for cg in _walk(self.root)]
    def filter_cgroups(self, predicate):
        return [cg for cg in _walk(self.root) if predicate(cg)]
    def get_memory_limited_cgroups(self):
        return self.filter_cgroups(lambda cg: cg.has_memory_limit())
    def get_cpu_limited_cgroups(self):
        return self.filter_cgroups(lambda cg: cg.has_cpu_quota())
    def __repr__(self):
        result = ""
        for cg in _walk(self.root):
            result += "  " * _depth(cg) + repr(cg) + "\n"
        return result


