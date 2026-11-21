"""
detector.py - decides NOBODY / MOVING / BREATHING / PAUSED.

Radar  -> is someone there, and are they moving (slow, every few seconds).
Chest  -> the breathing wave itself (20 readings a second). Pauses come from here,
          because the radar's breathing number is a slow average that can't see
          a 10-second pause.

All tuning knobs are at the top.
"""

from collections import deque
from statistics import median, pstdev

# ---------- tuning knobs ----------
PAUSE_SECONDS = 10        # no breathing this long = a pause
LOW_FRACTION = 0.30       # breathing size under this share of normal = "not breathing"
AMP_WINDOW = 4.0          # seconds of wave used to measure breathing size
SMOOTH_WINDOW = 1.0       # seconds averaged to remove jitter
DETREND_WINDOW = 10.0     # seconds averaged to remove slow drift
BASELINE_SECONDS = 60     # how far back "normal breathing size" looks
WARMUP_SECONDS = 20       # chest data needed before pauses can be flagged
RADAR_MOVE_THRESHOLD = 40 # radar moving_range above this = moving
CHEST_MOVE_FACTOR = 6.0   # chest jump this many times normal breathing size = moving
MOVE_HOLD_SECONDS = 5     # stay MOVING this long after movement stops
RADAR_STALE_SECONDS = 15  # ignore radar presence if it hasn't reported for this long
# radar-only mode (no chest sensor attached):
RADAR_NO_BREATH_STATES = set()  # breath_state codes meaning "not breathing" (fill in after the long-hold test)
RADAR_LOW_FRACTION = 0.5        # radar rate below this share of its normal = "not breathing"
# -----------------------------------


class Window:
    """Values from the last `seconds`, with a running sum for fast averages."""

    def __init__(self, seconds):
        self.seconds = seconds
        self.items = deque()
        self.total = 0.0

    def add(self, t, v):
        self.items.append((t, v))
        self.total += v
        while self.items and t - self.items[0][0] > self.seconds:
            self.total -= self.items.popleft()[1]

    def mean(self):
        return self.total / len(self.items) if self.items else 0.0

    def values(self):
        return [v for _, v in self.items]


class PauseDetector:
    def __init__(self):
        self.state = "NOBODY"
        self.gap = 0.0
        self.bpm = 0
        self.wave = 0.0

        # radar
        self.r_presence = None
        self.r_time = None
        self.r_rate = 0
        self.last_move_t = None

        # chest processing
        self.start_t = None
        self.smooth = [Window(SMOOTH_WINDOW) for _ in range(3)]
        self.trend = [Window(DETREND_WINDOW) for _ in range(3)]
        self.detr = [Window(30) for _ in range(3)]     # detrended, per axis, for picking the axis
        self.raw_1s = [Window(1.0) for _ in range(3)]
        self.axis = 2
        self.last_axis_pick = 0
        self.wave_win = Window(AMP_WINDOW)
        self.wave_30 = deque()                          # (t, wave) for breaths per minute
        self.amps = deque()                             # (t, amplitude) sampled once a second
        self.last_amp_t = 0
        self.low_since = None

        # finished pauses: (start_t, length_s)
        self.pauses = []
        self.pause_start = None

    # ---------- radar ----------
    def update_radar(self, t, presence, movement, moving_range, breath_state, breath_rate):
        self.r_presence = presence
        self.r_time = t
        self.r_rate = breath_rate
        if moving_range > RADAR_MOVE_THRESHOLD:
            self.last_move_t = t
        if self.start_t is None:          # no chest sensor: radar decides on its own
            self._radar_only(t, presence, breath_state, breath_rate)

    def _radar_only(self, t, presence, breath_state, breath_rate):
        if not hasattr(self, "r_rates"):
            self.r_rates = deque()
            self.r_seen = False
            self.r_low_since = None
        if presence == 0:
            self.r_rates.clear()
            self.r_seen = False
            self.r_low_since = None
            self._set(t, "NOBODY", 0.0)
            self.bpm = 0
            return
        moving = self.last_move_t is not None and t - self.last_move_t < MOVE_HOLD_SECONDS
        if moving:
            self.r_low_since = None
            self._set(t, "MOVING", 0.0)
            return
        if breath_rate > 0 and self.r_low_since is None:
            self.r_rates.append((t, breath_rate))
            self.r_seen = True
        while self.r_rates and t - self.r_rates[0][0] > BASELINE_SECONDS:
            self.r_rates.popleft()
        good = [r for _, r in self.r_rates]
        base = median(good) if len(good) >= 3 else None
        no_breath = (
            breath_rate == 0
            or breath_state in RADAR_NO_BREATH_STATES
            or (base is not None and breath_rate < base * RADAR_LOW_FRACTION)
        )
        self.bpm = breath_rate
        if no_breath and self.r_seen:
            if self.r_low_since is None:
                self.r_low_since = t
            gap = t - self.r_low_since
            self._set(t, "PAUSED" if gap >= PAUSE_SECONDS else "BREATHING", gap)
        else:
            self.r_low_since = None
            self._set(t, "BREATHING", 0.0)

    def _nobody(self, t):
        if self.r_presence is None or self.r_time is None:
            return False
        if t - self.r_time > RADAR_STALE_SECONDS:
            return False
        return self.r_presence == 0

    # ---------- chest ----------
    def baseline(self):
        good = [a for _, a in self.amps]
        return median(good) if len(good) >= 10 else None

    def update_chest(self, t, ax, ay, az):
        """Call 20 times a second. Returns (state, gap_seconds, breaths_per_min, wave)."""
        if self.start_t is None:
            self.start_t = t

        raw = (ax, ay, az)
        for i in range(3):
            self.raw_1s[i].add(t, raw[i])
            self.smooth[i].add(t, raw[i])
            s = self.smooth[i].mean()
            self.trend[i].add(t, s)
            self.detr[i].add(t, s - self.trend[i].mean())

        # Every few seconds, follow the axis that moves most with breathing.
        if t - self.last_axis_pick > 5 and len(self.detr[0].items) > 100:
            spreads = [pstdev(self.detr[i].values()) for i in range(3)]
            self.axis = spreads.index(max(spreads))
            self.last_axis_pick = t

        a = self.axis
        self.wave = self.detr[a].items[-1][1]
        self.wave_win.add(t, self.wave)
        w = self.wave_win.values()
        amp = max(w) - min(w)

        self.wave_30.append((t, self.wave))
        while self.wave_30 and t - self.wave_30[0][0] > 30:
            self.wave_30.popleft()

        base = self.baseline()

        # Movement: radar says so, or the chest jumped far more than a breath does.
        raw1 = self.raw_1s[a].values()
        if base and (max(raw1) - min(raw1)) > CHEST_MOVE_FACTOR * base:
            self.last_move_t = t
        moving = self.last_move_t is not None and t - self.last_move_t < MOVE_HOLD_SECONDS

        if self._nobody(t):
            self._set(t, "NOBODY", 0.0, reset=True)
        elif moving:
            self._set(t, "MOVING", 0.0)
        elif base is None or t - self.start_t < WARMUP_SECONDS:
            self._learn_amp(t, amp)
            self._set(t, "BREATHING", 0.0)
        elif amp < LOW_FRACTION * base:
            if self.low_since is None:
                self.low_since = t
            # The amplitude window lags the real stop by about half its length.
            gap = t - self.low_since + AMP_WINDOW / 2
            self._set(t, "PAUSED" if gap >= PAUSE_SECONDS else "BREATHING", gap)
        else:
            self._learn_amp(t, amp)
            self._set(t, "BREATHING", 0.0)

        self.bpm = self._breaths_per_min(base)
        return self.state, self.gap, self.bpm, self.wave

    def _learn_amp(self, t, amp):
        """Record normal breathing size once a second (never during pauses or movement)."""
        if t - self.last_amp_t >= 1.0:
            self.last_amp_t = t
            self.amps.append((t, amp))
            while self.amps and t - self.amps[0][0] > BASELINE_SECONDS:
                self.amps.popleft()

    def _breaths_per_min(self, base):
        """Count upward crossings of the wave in the last 30 s, with a dead band so jitter doesn't count."""
        if not base or len(self.wave_30) < 20:
            return 0
        band = 0.2 * base
        below = False
        count = 0
        for _, v in self.wave_30:
            if v < -band:
                below = True
            elif v > band and below:
                count += 1
                below = False
        span = self.wave_30[-1][0] - self.wave_30[0][0]
        return round(count * 60 / span) if span > 10 else 0

    def _set(self, t, state, gap, reset=False):
        if state != "PAUSED" and self.pause_start is not None:
            self.pauses.append((self.pause_start, t - self.pause_start))
            self.pause_start = None
        if state == "PAUSED" and self.pause_start is None:
            self.pause_start = t - gap
        if state != "PAUSED" and gap == 0.0:
            self.low_since = None
        if reset:
            self.amps.clear()
            self.start_t = t
        self.state = state
        self.gap = gap
