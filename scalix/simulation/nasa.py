"""Real traffic: the NASA Kennedy Space Center web server log, July 1995 (Phase 7).

Source: the "NASA-HTTP" traces in the Internet Traffic Archive,
https://ita.ee.lbl.gov/html/contrib/NASA-HTTP.html (file ``NASA_access_log_Jul95.gz``, 1,891,715
lines, 1 July 00:00:01 to 28 July 13:32:25, timestamps in local time, UTC-4). The archive says
"The traces may be freely redistributed" and asks that no analysis go "beyond general traffic
patterns". SCALIX keeps only the *number of requests per 5 seconds*: hosts, URLs, status codes and
sizes are discarded when the log is converted, and the raw log is never committed.

Committed file: ``data/traces/nasa_jul95_5s.csv.gz``, one row per 5-second bin starting at
1 July 1995 00:00:00 local time, column ``requests``. To rebuild it from the raw log::

    python -m scalix.simulation.nasa path/to/NASA_access_log_Jul95.gz

How the simulator uses it (``Scenario.NASA``):

* One run = one full calendar day, 00:00 to 24:00 local time, replayed in real time (no time
  compression). The run's ``seed`` picks the day: 1 = 1 July ... 27 = 27 July (28 July is partial).
* Arrivals per 5 s step = the real count x ``nasa_scale`` (default 2.37). The scale makes the
  busiest minute of the month (6.75 req/s) about 16 req/s, i.e. 80% of the 20-worker maximum, so the
  same capacity limits as the synthetic scenarios apply. Scaling keeps the real shape: daily cycles,
  weekends, the 13 July Shuttle launch peak, and real second-to-second bursts.
* The ``expected_rate`` column is a 1-minute moving average of the scaled arrivals, for plotting.
"""

from __future__ import annotations

import gzip
import re
import sys
from functools import lru_cache
from pathlib import Path

import numpy as np

from scalix.config import PROJECT_ROOT

NASA_FILE = PROJECT_ROOT / "data" / "traces" / "nasa_jul95_5s.csv.gz"
BIN_SECONDS = 5
FULL_DAYS = 27                    # 1..27 July are complete; 28 July stops at 13:32
DEFAULT_SCALE = 2.37              # busiest minute 6.75 req/s -> ~16 req/s
SECONDS_PER_DAY = 86_400

_STAMP = re.compile(rb"\[(\d\d)/Jul/1995:(\d\d):(\d\d):(\d\d) -0400\]")


def convert_log(log_path: Path, out_path: Path = NASA_FILE) -> tuple[int, int]:
    """Count requests per 5 s bin from the raw July 1995 log. Returns (counted, skipped lines)."""
    counts = np.zeros(28 * SECONDS_PER_DAY // BIN_SECONDS, dtype=np.int64)
    counted = skipped = 0
    with gzip.open(log_path, "rb") as f:
        for line in f:
            m = _STAMP.search(line)
            if not m:
                skipped += 1
                continue
            d, h, mi, s = (int(x) for x in m.groups())
            second = ((d - 1) * 24 + h) * 3600 + mi * 60 + s
            counts[second // BIN_SECONDS] += 1
            counted += 1
    last = int(np.nonzero(counts)[0].max()) + 1
    out_path.parent.mkdir(parents=True, exist_ok=True)
    text = "requests\n" + "\n".join(str(int(c)) for c in counts[:last]) + "\n"
    # mtime=0 and no file name in the header: the same log always gives a byte-identical file.
    with open(out_path, "wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=9, mtime=0) as f:
        f.write(text.encode("ascii"))
    return counted, skipped


@lru_cache(maxsize=1)
def load_counts(path: Path = NASA_FILE) -> np.ndarray:
    """Requests per 5 s bin from 1 July 1995 00:00 local time (read-only array)."""
    if not path.is_file():
        raise FileNotFoundError(f"NASA trace not found at {path}; see scalix/simulation/nasa.py to rebuild it")
    with gzip.open(path, "rt", encoding="ascii") as f:
        header = f.readline().strip()
        if header != "requests":
            raise ValueError(f"unexpected header {header!r} in {path}")
        counts = np.loadtxt(f, dtype=np.int64, ndmin=1)
    counts.setflags(write=False)
    return counts


def day_counts(day: int, duration_seconds: float, step_seconds: float) -> np.ndarray:
    """Real requests per step for ``duration_seconds`` starting at 00:00 on ``day`` (1..27 July)."""
    if not (isinstance(day, int) and not isinstance(day, bool) and 1 <= day <= FULL_DAYS):
        raise ValueError(f"for the NASA scenario the seed is the day of July 1995, 1..{FULL_DAYS}; got {day!r}")
    if duration_seconds > SECONDS_PER_DAY:
        raise ValueError("a NASA run covers at most one day (86400 s)")
    ratio = step_seconds / BIN_SECONDS
    if abs(ratio - round(ratio)) > 1e-9 or round(ratio) < 1:
        raise ValueError(f"for the NASA scenario the step must be a multiple of {BIN_SECONDS} s")
    per_step = int(round(ratio))
    start = (day - 1) * SECONDS_PER_DAY // BIN_SECONDS
    n_bins = int(round(duration_seconds / BIN_SECONDS))
    bins = load_counts()[start:start + n_bins].astype(float)
    if len(bins) < n_bins:
        raise ValueError("the NASA trace does not cover the requested time")
    return bins.reshape(-1, per_step).sum(axis=1)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: python -m scalix.simulation.nasa path/to/NASA_access_log_Jul95.gz", file=sys.stderr)
        return 2
    counted, skipped = convert_log(Path(args[0]))
    print(f"counted {counted} requests, skipped {skipped} unparseable line(s); wrote {NASA_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
