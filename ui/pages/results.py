"""Results: the final evaluation, read from the committed experiment outputs (no new simulation)."""

import math

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from scalix import evaluation as ev
from scalix.sources import DataSource
from ui import charts, common

common.page_header("Evaluation results", "The final, pre-registered evaluation: every strategy on identical traces, "
                   "10 seeds per synthetic pattern and all 27 real NASA days. Read from the saved experiment "
                   "outputs; nothing is re-run here.")

EXPERIMENTS = {
    "main": "Default settings, synthetic patterns (seeds 1–10)",
    "nasa": "Default settings, real NASA traffic (27 days)",
    "tuned": "Tuned settings, synthetic patterns (unseen test seeds)",
    "nasa-tuned": "Tuned settings, real NASA traffic",
    "nasa-aws": "Real NASA traffic with AWS-like timing (5-minute decisions, CPU-only signal)",
}


@st.cache_data(show_spinner=False)
def load_all() -> dict:
    found = {}
    for name in ev.SUITE:
        if (ev.RESULTS_ROOT / name / "summary.csv").exists():
            found[name] = ev.load(ev.RESULTS_ROOT, name)["summary"]
    return found


data = load_all()
if "main" not in data:
    st.warning("No saved evaluation outputs found. Run `python -m scalix.evaluation run` first.")
    st.stop()

common.show_source(DataSource.SIMULATED, "Response time from the queueing model; cost estimated from AWS list prices "
                                         "(t3.micro, us-east-1). Not AWS measurements")

c = st.columns([3, 2])
with c[0]:
    exp = st.selectbox("Experiment", [e for e in EXPERIMENTS if e in data], format_func=EXPERIMENTS.get, key="res_exp")
summary = data[exp]
scenarios = [s for s in ("NORMAL", "RISING", "SPIKE", "MIXED", "NASA") if s in set(summary["scenario"])]
with c[1]:
    scenario = st.selectbox("Pattern", scenarios, format_func=lambda s: ev.SCENARIO_NAMES[s], key="res_scenario",
                            index=scenarios.index("SPIKE") if "SPIKE" in scenarios else 0)
present = [p for p in charts.POLICY_ORDER if p in set(summary["policy"])]
runs = int(summary[(summary.scenario == scenario)].groupby("policy").size().max())

# ----------------------------------------------------------------------- headline cards
def stats(policy: str, metric: str):
    vals = summary[(summary.scenario == scenario) & (summary.policy == policy)][metric].to_numpy(dtype=float)
    return ev._ci_stats(vals)


st.subheader(f"{ev.SCENARIO_NAMES[scenario]}: {runs} runs per strategy")
cols = st.columns(len(present))
for col, p in zip(cols, present):
    p95, h95, _ = stats(p, "response_time_model_p95_s")
    cost, hc, _ = stats(p, "estimated_cost")
    col.metric(charts.POLICY_SHORT[p] + " · p95 response", f"{p95:.2f} s",
               help=f"Mean p95 response time ± {h95:.2f} s (95% CI) over {runs} runs. Lower is faster.")
    col.caption(f"Estimated cost {cost:.3f} ± {hc:.3f} USD per run")


def dot_chart(metric: str, title: str, unit: str, log: bool = False) -> go.Figure:
    fig = go.Figure()
    for p in present:
        mean, half, _ = stats(p, metric)
        fig.add_trace(go.Scatter(
            x=[charts.POLICY_SHORT[p]], y=[mean], mode="markers", name=charts.POLICY_LABELS[p],
            marker=dict(size=13, color=charts.POLICY_COLORS[p], line=dict(color=charts.SURFACE, width=2)),
            error_y=dict(type="data", array=[0 if math.isnan(half) else half], color=charts.TEXT_MUTED, thickness=1,
                         width=6),
            hovertemplate=f"%{{x}}: %{{y:.3f}} {unit} ± {half:.3f}<extra></extra>"))
    fig = charts._layout(fig, title, unit, height=300, legend=False)
    fig.update_xaxes(title=None)
    fig.update_layout(hovermode="closest")
    if log:
        ticks = [v for v in (1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000)]
        fig.update_yaxes(type="log", tickvals=ticks, ticktext=[str(v) for v in ticks])
    return fig


c = st.columns(2)
c[0].plotly_chart(dot_chart("response_time_model_p95_s", "p95 response time (lower is faster)", "s",
                            log=summary["response_time_model_p95_s"].max() > 50), width="stretch", theme=None)
c[1].plotly_chart(dot_chart("estimated_cost", "Estimated cost per run (lower is cheaper)", "USD"),
                  width="stretch", theme=None)
st.caption("Dots are means; whiskers are 95% confidence intervals.")

# ----------------------------------------------------------------------- full table
METRICS = [("response_time_model_avg_s", "Response time, average (s)", ".2f"),
           ("response_time_model_p95_s", "Response time, p95 (s)", ".2f"),
           ("time_with_queue_s", "Time with requests waiting (s)", ".0f"),
           ("estimated_cost", "Estimated cost (USD)", ".3f"),
           ("cost_hourly_part", "Cost without CPU credits (USD)", ".3f"),
           ("worker_minutes", "Worker-minutes", ".0f"),
           ("avg_utilization", "Average utilization", ".1%"),
           ("scale_up_events", "Scale-ups", ".1f"), ("scale_down_events", "Scale-downs", ".1f")]
table = {"Metric": [label for _, label, _ in METRICS]}
for p in present:
    table[charts.POLICY_SHORT[p]] = [ev.cell(summary, scenario, p, m, f) for m, _, f in METRICS]
st.dataframe(pd.DataFrame(table), hide_index=True, width="stretch")
st.caption("Mean ± 95% confidence-interval half-width.")

# ----------------------------------------------------------------------- SCALIX vs the others, paired
st.subheader("SCALIX compared, run by run")
st.caption("Every strategy saw the identical trace in each seed or day, so the comparison is made run by run "
           "(paired). Negative = SCALIX lower. 'Clear' = the 95% interval excludes 0.")
rows = []
for other in ("THRESHOLD", "TARGET_TRACKING", "STATIC"):
    if other not in present or "SCALIX" not in present:
        continue
    row = {"SCALIX minus": ev.POLICY_NAMES[other]}
    for metric, label, fmt in [("response_time_model_p95_s", "p95 response (s)", ".2f"),
                               ("estimated_cost", "cost (USD)", ".4f"), ("worker_minutes", "worker-min", ".0f")]:
        mean, half, n = ev.paired_difference(summary, scenario, "SCALIX", other, metric)
        clear = not math.isnan(half) and (mean - half > 0 or mean + half < 0)
        row[label] = f"{format(mean, '+' + fmt)} ± {format(half, fmt)}" + (" (clear)" if clear else "")
    rows.append(row)
st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

# ----------------------------------------------------------------------- target and context
with st.expander("Which strategies met the 10-second p95 target, in every experiment"):
    out = []
    for name, s in data.items():
        if name not in EXPERIMENTS:
            continue
        for sc in [x for x in ("NORMAL", "RISING", "SPIKE", "MIXED", "NASA") if x in set(s["scenario"])]:
            r = {"Experiment": EXPERIMENTS[name], "Pattern": ev.SCENARIO_NAMES[sc]}
            for p in charts.POLICY_ORDER:
                v = s[(s.scenario == sc) & (s.policy == p)]["response_time_model_p95_s"].mean()
                r[charts.POLICY_SHORT[p]] = "–" if math.isnan(v) else f"{v:.2f} {'✓' if v <= ev.SLO_P95_SECONDS else '✗'}"
            out.append(r)
    st.dataframe(pd.DataFrame(out), hide_index=True, width="stretch")

with st.expander("Key figures from the report"):
    for fig_name, caption in [("nasa_day.png", "One real day (13 July 1995): how each strategy followed the traffic"),
                              ("tradeoff.png", "Cost against slow responses, one dot per run"),
                              ("response.png", "Average response time per pattern")]:
        path = ev.DOCS_FIGURES / fig_name
        if path.is_file():
            st.image(str(path), caption=caption)
    st.caption("Full tables: docs/results/RESULTS_TABLES.md. Method and discussion: docs/EVALUATION.md.")
