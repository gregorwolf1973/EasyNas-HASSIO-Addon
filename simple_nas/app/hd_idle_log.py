#!/usr/bin/env python3
"""Filter for hd-idle's output: adds timestamps, durations and running totals.

hd-idle prints bare lines such as 'sda spindown', and the add-on log does not
timestamp raw stdout. run.sh pipes hd-idle through this script, so every event
reaches the add-on log with the time it happened. Totals per disk are kept in
/data/hdd_stats.json and survive restarts. Lines that are not spin events pass
through with a timestamp only.
"""
import json
import os
import re
import sys
import time

STATS_FILE = os.environ.get("SIMPLE_NAS_HDD_STATS", "/data/hdd_stats.json")
EVENT = re.compile(r"^(\S+) (spindown|spinup)$")


def fmt_duration(seconds):
    """Format seconds using the two largest units: 45s, 12m 5s, 3h 7m, 2d 4h."""
    seconds = int(seconds)
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def load_stats(path=None):
    try:
        with open(path or STATS_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_stats(stats, path=None):
    path = path or STATS_FILE
    tmp = f"{path}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(stats, fh)
        os.replace(tmp, path)
    except OSError as exc:
        print(f"hd-idle log: cannot save statistics: {exc}", file=sys.stderr)


def totals(entry):
    """One-line summary of a disk's statistics."""
    return (
        f"running {fmt_duration(entry.get('running_seconds', 0))}, "
        f"standby {fmt_duration(entry.get('standby_seconds', 0))}, "
        f"{entry.get('spindowns', 0)} spindowns"
    )


class Tracker:
    """Turns spin events into log messages and updates the statistics.

    hd-idle treats every disk as running when it starts, so the first running
    period is counted from `started`. A period still open when the add-on stops
    is not added to the totals.
    """

    def __init__(self, stats, started):
        self.stats = stats
        self.started = started
        self.state = {}  # disk -> (last event, time of that event)

    def event(self, disk, kind, now):
        entry = self.stats.setdefault(disk, {})
        previous, since = self.state.get(disk, ("spinup", self.started))
        self.state[disk] = (kind, now)
        if previous == kind:
            return f"{disk} {kind}"
        elapsed = max(0, now - since)
        if kind == "spindown":
            entry["running_seconds"] = entry.get("running_seconds", 0) + int(elapsed)
            entry["spindowns"] = entry.get("spindowns", 0) + 1
            period = f"running for {fmt_duration(elapsed)}"
        else:
            entry["standby_seconds"] = entry.get("standby_seconds", 0) + int(elapsed)
            entry["spinups"] = entry.get("spinups", 0) + 1
            period = f"standby for {fmt_duration(elapsed)}"
        return f"{disk} {kind} after {period} (total: {totals(entry)})"


def stamp(message, now):
    return f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(now))} {message}"


def run(lines, out=sys.stdout, clock=time.time, path=None):
    stats = load_stats(path)
    tracker = Tracker(stats, clock())
    for disk, entry in sorted(stats.items()):
        print(f"hd-idle totals so far: {disk}: {totals(entry)}", file=out, flush=True)
    for line in lines:
        line = line.strip()
        if not line:
            continue
        now = clock()
        match = EVENT.match(line)
        if match:
            line = tracker.event(match.group(1), match.group(2), now)
            save_stats(stats, path)
        print(stamp(line, now), file=out, flush=True)


if __name__ == "__main__":
    run(sys.stdin)
