"""TEST-ONLY stand-in for the Java engine, used to exercise process handling in the runner.

Accepts the same CLI as the real engine (--config FILE --out DIR --experiment-id ID) and behaves
according to the FAKE_ENGINE_MODE environment variable:

  ok           write a valid fixture experiment and exit 0
  fail         print an error and exit 1
  config       print a configuration error and exit 2
  sleep        sleep longer than any test timeout
  malformed    exit 0 but write a summary.csv with a broken column
  incomplete   exit 0 but omit one requested policy
  nooutput     exit 0 without writing anything
"""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fixtures_util import write_experiment  # noqa: E402


def main(argv: list[str]) -> int:
    args = dict(zip(argv[::2], argv[1::2]))
    props = {}
    for line in Path(args["--config"]).read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            props[k] = v
    out = Path(args["--out"]) / args["--experiment-id"]
    workloads = props["experiment.workloads"].split(",")
    policies = props["experiment.policies"].split(",")
    seed = int(props["simulation.seed"])
    mode = os.environ.get("FAKE_ENGINE_MODE", "ok")

    if mode == "fail":
        print("Simulation failed: java.lang.IllegalStateException: boom", file=sys.stderr)
        return 1
    if mode == "config":
        print("Configuration error: lower threshold (0.9) must be < upper threshold (0.7)", file=sys.stderr)
        return 2
    if mode == "sleep":
        time.sleep(60)
        return 0
    if mode == "nooutput":
        return 0
    if mode == "incomplete":
        policies = policies[:-1]
    write_experiment(out, exp_id=args["--experiment-id"], workloads=workloads, policies=policies, seed=seed)
    if mode == "malformed":
        text = (out / "summary.csv").read_text(encoding="utf-8").replace(",0.2,", ",not-a-number,")
        (out / "summary.csv").write_text(text, encoding="utf-8")
    print("fake engine done")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
