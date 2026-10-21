# The SCALIX dashboard (AWS edition)

The dashboard is a local Streamlit app in `ui/`, built on the Python algorithms, simulator, metrics and the optional read-only AWS adapters. It runs on your computer only. The server is bound to `localhost` in `.streamlit/config.toml`.

## Launch

From the project root, macOS / Linux:

```bash
cd ~/Scalix
.venv/bin/pip install -r requirements.txt
.venv/bin/streamlit run app.py
```

The second line is only needed once, or after `requirements.txt` changes. Then open **http://localhost:8501**. Stop the dashboard with **Ctrl+C** in the terminal. On Windows, use `.venv\Scripts\streamlit run app.py`.

* **Port busy?** Use `.venv/bin/streamlit run app.py --server.port 8600`.
* **AWS monitoring page** needs boto3: `.venv/bin/pip install -r requirements-aws.txt`. Everything else works without it.
* **The original Part 1 dashboard,** which drives the Java engine, still works with `.venv/bin/streamlit run dashboard/app.py`. It needs the Java jar.

## Pages

| Page | What it shows | Data label |
|---|---|---|
| **Overview** | One strategy in the latest run: workers at the end, workload pattern, average and peak utilization, scaling events, worker-minutes (resource-time), estimated cost (AWS list prices), response time from the queueing model, and workers over time. | SIMULATED |
| **Demo: real traffic** | A guided, four-step replay of one real day of the NASA 1995 web log (default 13 July, the Shuttle launch day): the real demand, the workers each strategy paid for, who kept requests waiting (all four, then zoomed on the auto-scalers), and a scorecard for that day. 24 hours replay in 20 seconds per chart. The day is simulated once and cached, which takes about 10 seconds the first time. | SIMULATED (real request counts) |
| **Workload simulation** | A form for the pattern (normal / rising / spike / mixed), duration, step, demand intensity, seeds, workers, start-up delay, price per worker-hour (default: t3.micro at us-east-1 list prices), whether to add T3 CPU credits, and every strategy setting. "Run simulation" runs all three strategies locally. It shows demand and utilization over time, a table view and CSV downloads. | SIMULATED |
| **Policy comparison** | Workers over time, requests waiting (overload), and a side-by-side table of resource-time, cost, utilization, time above threshold, overload, unserved demand, response times (queueing model, plus the original proxy) and scaling counts. Values are mean ± 95% CI with a "clear lead / too close to call" verdict. There's also a bar chart per metric and the decision log with reasons. | SIMULATED |
| **Evaluation results** | The final evaluation read from the saved outputs, with nothing re-run: pick an experiment (default or tuned settings, synthetic or real NASA traffic, AWS-like timing) and a pattern. It shows each strategy's p95 response and cost with 95% intervals, the full metric table, SCALIX compared run by run with Threshold, AWS target tracking and Static, which strategies met the 10 s target, and the report's key figures. | SIMULATED |
| **AWS monitoring** | A bold **strict budget** notice (USD 20, from `aws.budget_usd`), and the result of the last saved access check (a local file; no AWS call). **Live tab:** in READ_ONLY mode, two buttons, *Read group and CPU now* and *Run access check*, that make read-only calls only when pressed. In SIMULATION mode it explains how to enable reading. **Mocked preview tab:** the same layout, buttons and result views as the live tab (profile, region and group line; *Read group and CPU now*; *Run access check*), filled with scripted data shaped like AWS's answers for six situations (busy, normal, quiet, no data yet, stale, broken) and judged by the same rules. It never contacts AWS. | LIVE_AWS / MOCKED |
| **Scaling history** | Simulated decisions from the latest run; the AWS controller's audit log (`results/aws/audit.jsonl`) with an **"actual AWS change?"** column that says YES only for APPLIED; and the mocked controller log, with a one-click mocked dry run. | SIMULATED / LIVE_AWS / MOCKED |

On first visit the dashboard runs a small default simulation (Mixed pattern, 3 seeds), so every page has data. It takes about a second, locally.

## Safety

* **SIMULATION is the default.** The sidebar always shows the current mode.
* **AWS is only read, and only on a button press.** Opening a page, refreshing or navigating makes no AWS call. Tests count every call to check this.
* **There's no control that changes AWS.** The UI code never references the capacity writer, `--apply`, `apply=True`, operator confirmation or the kill switch, and a static scan test enforces that. Another test booby-traps the writer and the controller's apply path and clicks through every page. Dry runs from the History page can't apply anything.
* **Credentials are never entered or shown.** The AWS page shows the profile *name*, region and group only. The UI code contains no `boto3` import, no credential reads and no file writes; a test scans for these.
* **Errors are explained, not crashed on.** Denied permissions, expired credentials, network errors, missing, stale or malformed CPU data, and a corrupt audit log each show a message, and none of them is ever shown as 0%.
* **Every value is labelled** SIMULATED, MOCKED or LIVE_AWS, and costs always say they are estimates from AWS list prices, not AWS bills.

## Charts

* **Live replay of each run.** The time charts (demand, utilization, workers, requests waiting) replay the simulated run as if it were happening live, all three strategies on the same clock. Each replay:
  * **starts only when at least half the chart is on screen**, pauses while it is scrolled away or the browser tab is hidden, and finishes in **20 seconds**. That's under 30 s by design, and a test enforces it.
  * shows a moving "now" line with the simulated time, and labels each strategy's current value at the tip of its line.
  * can be restarted with **Replay**, or skipped to the end with **Show all**. Hovering shows every strategy's value at that moment.
  * jumps straight to the finished chart if your system asks for reduced motion.

  The animation draws exactly the simulator's time series; nothing is interpolated into the data. The charts are plain SVG drawn by a small built-in script (`ui/live_chart.py`), with no external library, so they also work offline.
* **Fixed colours.** One colour per strategy everywhere: Static blue, Threshold orange, SCALIX aqua. The palette passed the colour-vision checks on the app background. Aqua is below 3:1 contrast, so every multi-series chart has a legend and every chart's numbers are also in a table.
* **One y-axis per chart.** No dual axes, 2 px lines and a hairline grid.
* **Confidence intervals** appear as whiskers when there's more than one run.

## Tests

`tests/test_ui.py` covers:

* every page rendering and being labelled
* the sidebar mode
* the overview tiles and their tooltips
* running and rejecting the simulation form
* the honesty of the comparison table
* AWS reads only on button press, and errors not crashing the page
* the access-check display
* credentials never shown
* the history "actual change" marking
* a corrupt audit log
* mocked dry runs never touching the real log
* the static safety scan
* chart conventions

Ten planted bugs in the dashboard's safety behaviour were all caught.
