import time
import logging

logger = logging.getLogger('loop.timing')

class PhaseTimer:
    """One line per main-loop cycle: total time plus time per phase.
    Logs WARNING when the cycle or any single phase runs long, so a
    silent slowdown (T1 board Sep 21, tier refresh Sep 22) shows up
    in the log with the phase named, instead of needing py-spy."""

    def __init__(self, cycle=None, warn_total=60.0, warn_phase=30.0):
        self.cycle = cycle
        self.t0 = self.last = time.monotonic()
        self.phases = []
        self.warn_total = warn_total
        self.warn_phase = warn_phase

    def mark(self, name):
        """Close the phase that just ran, under this name."""
        now = time.monotonic()
        self.phases.append((name, now - self.last))
        self.last = now

    def log(self):
        total = time.monotonic() - self.t0
        parts = ' · '.join(f"{n} {d:.1f}" for n, d in self.phases)
        slow = total > self.warn_total or any(d > self.warn_phase for _, d in self.phases)
        (logger.warning if slow else logger.info)(
            f"cycle {self.cycle} | {total:.1f}s | {parts}")