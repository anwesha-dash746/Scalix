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
| **Overview** | One strategy in the latest run: workers at the end, workload pattern, average and peak utilization, scaling events, worker-minutes (resource-time), estimated cost, response proxy, and workers over time. | SIMULATED |
| **Workload simulation** | A form for the pattern (normal / rising / spike / mixed), duration, step, demand intensity, seeds, workers, start-up delay, price and every strategy setting. "Run simulation" runs all three strategies locally. It shows demand and utilization over time, a table view and CSV downloads. | SIMULATED |
| **Policy comparison** | Workers over time, requests waiting (overload), and a side-by-side table of resource-time, cost, utilization, time above threshold, overload, unserved demand, response proxies and scaling counts. Values are mean ± 95% CI with a "clear lead / too close to call" verdict. There's also a bar chart per metric and the decision log with reasons. | SIMULATED |
| **AWS monitoring** | **Live tab:** in READ_ONLY mode, two buttons, *Read group and CPU now* and *Run access check*, that make read-only calls only when pressed. In SIMULATION mode it explains how to enable reading. **Mocked tab:** scripted situations (busy, quiet, no data, stale, broken) judged by the same rules as live data. | LIVE_AWS / MOCKED |
| **Scaling history** | Simulated decisions from the latest run; the AWS controller's audit log (`results/aws/audit.jsonl`) with an **"actual AWS change?"** column that says YES only for APPLIED; and the mocked controller log, with a one-click mocked dry run. | SIMULATED / LIVE_AWS / MOCKED |

On first visit the dashboard runs a small default simulation (Mixed pattern, 3 seeds), so every page has data. It takes about a second, locally.

## Safety

* **SIMULATION is the default.** The sidebar always shows the current mode.
* **AWS is only read, and only on a button press.** Opening a page, refreshing or navigating makes no AWS call. Tests count every call to check this.
* **There's no control that changes AWS.** The UI code never references the capacity writer, `--apply`, `apply=True`, operator confirmation or the kill switch, and a static scan test enforces that. Another test booby-traps the writer and the controller's apply path and clicks through every page. Dry runs from the History page can't apply anything.
* **Credentials are never entered or shown.** The AWS page shows the profile *name*, region and group only. The UI code contains no `boto3` import, no credential reads and no file writes; a test scans for these.
* **Errors are explained, not crashed on.** Denied permissions, expired credentials, network errors, missing, stale or malformed CPU data, and a corrupt audit log each show a message, and none of them is ever shown as 0%.
* **Every value is labelled** SIMULATED, MOCKED or LIVE_AWS, and costs always say they're illustrative.

## Charts

* **Fixed colours.** Plotly with one colour per strategy everywhere: Static blue, Threshold orange, SCALIX aqua. The palette passed the colour-vision checks on the app background. Aqua is below 3:1 contrast, so every multi-series chart has a legend and every chart's numbers are also in a table.
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
