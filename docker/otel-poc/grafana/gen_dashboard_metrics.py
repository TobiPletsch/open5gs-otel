#!/usr/bin/env python3
"""Generates "Otel Poc - Metrics, Traces and Logs" (branch otel-metrics).

  [DS_UID=<datasource uid>] gen_dashboard_metrics.py <out.json>

= the light dashboard (gen_dashboard_light.py, unchanged, reused) with a
metrics section on top: what metrics add on top of traces and logs.

Metrics come from the SMF via the OTel metrics SDK (manual instrumentation):
  open5gs.smf.pdu_session.establishment.attempts  {5gs.outcome, error.type, 5gs.dnn}
  open5gs.smf.pdu_session.establishment.duration  {5gs.outcome}   (ms histogram)
Both are recorded where the smf.pdu_session.establishment span ends, with the
same attribute values as the span. Drill-down: click a bar (cause + time
bucket) or a cause -> table of exactly those failing traces -> trace + logs.
"""
import copy
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gen_dashboard_light as light          # noqa: E402 (honours DS_UID)

DS = light.DS
NS = light.NS
UID = "open5gs-otel-poc-metrics"
SLUG = "otel-poc-metrics-traces-and-logs"
TITLE = "Otel Poc - Metrics, Traces and Logs"
BASE = "/d/" + UID + "/" + SLUG + "?${__url_time_range}"
ATTEMPTS = "open5gs.smf.pdu_session.establishment.attempts"
DURATION = "open5gs.smf.pdu_session.establishment.duration"
ENV = f"ResourceAttributes['deployment.environment'] = '{NS}'"
CAUSE = "${cause:sqlstring}"
BSTART = "${bucket_start:sqlstring}"

# Exact drill-down window: a delta data point covers [StartTimeUnix, TimeUnix]
# (one 10 s export interval). The bar of a bucket sums the points whose
# TimeUnix falls into it, so its measurements happened between the first
# point's StartTimeUnix and the last point's TimeUnix - and the counter is
# incremented where the span ends.
BUCKET_POINTS = f"""WHERE MetricName = '{ATTEMPTS}' AND {ENV}
           AND toUnixTimestamp(toStartOfInterval(TimeUnix, INTERVAL ${{bucket}} second))
               = intDiv(toInt64OrZero({BSTART}), 1000)"""

FAILURES_SQL = f"""
SELECT toStartOfInterval(TimeUnix, INTERVAL ${{bucket}} second) AS time,
       Attributes['error.type'] AS cause,
       sum(Value) AS failures
FROM default.otel_metrics_sum
WHERE $__timeFilter(TimeUnix)
  AND MetricName = '{ATTEMPTS}'
  AND Attributes['5gs.outcome'] != 'success'
  AND {ENV}
GROUP BY time, cause
ORDER BY time"""

OUTCOMES_SQL = f"""
SELECT toStartOfInterval(TimeUnix, INTERVAL ${{bucket}} second) AS time,
       Attributes['5gs.outcome'] AS outcome,
       sum(Value) AS attempts
FROM default.otel_metrics_sum
WHERE $__timeFilter(TimeUnix)
  AND MetricName = '{ATTEMPTS}'
  AND {ENV}
GROUP BY time, outcome
ORDER BY time"""

DURATION_SQL = f"""
SELECT toStartOfInterval(TimeUnix, INTERVAL ${{bucket}} second) AS time,
       Attributes['5gs.outcome'] AS outcome,
       round(sum(Sum) / greatest(sum(Count), 1), 1) AS avg_ms,
       round(max(Max), 1) AS max_ms
FROM default.otel_metrics_histogram
WHERE $__timeFilter(TimeUnix)
  AND MetricName = '{DURATION}'
  AND {ENV}
GROUP BY time, outcome
ORDER BY time"""

CAUSES_SQL = f"""
SELECT Attributes['error.type'] AS cause,
       sum(Value) AS failures,
       min(TimeUnix) AS first_seen,
       max(TimeUnix) AS last_seen
FROM default.otel_metrics_sum
WHERE $__timeFilter(TimeUnix)
  AND MetricName = '{ATTEMPTS}'
  AND Attributes['5gs.outcome'] != 'success'
  AND {ENV}
GROUP BY cause
ORDER BY failures DESC"""

DRILL_SQL = f"""
SELECT addNanoseconds(Timestamp, Duration) AS ended,
       TraceId AS trace_id,
       SpanAttributes['5gs.outcome'] AS outcome,
       SpanAttributes['error.type'] AS cause,
       nullIf(StatusMessage, '') AS reason,
       round(Duration / 1e6, 1) AS duration_ms,
       SpanAttributes['5gs.dnn'] AS dnn,
       SpanAttributes['5gs.ue.ref'] AS ue_ref,
       arrayStringConcat(Events.Name, ' > ') AS milestones
FROM default.otel_traces
WHERE $__timeFilter(Timestamp)
  AND SpanName = 'smf.pdu_session.establishment'
  AND SpanAttributes['5gs.outcome'] != 'success'
  AND {ENV}
  AND ({CAUSE} = '' OR SpanAttributes['error.type'] = {CAUSE})
  AND ({BSTART} = '' OR addNanoseconds(Timestamp, Duration) BETWEEN
         (SELECT min(StartTimeUnix) FROM default.otel_metrics_sum {BUCKET_POINTS})
     AND (SELECT max(TimeUnix) FROM default.otel_metrics_sum {BUCKET_POINTS}))
  AND {light.UE_FILTER.replace("{col}", "SpanAttributes['5gs.ue.ref']")}
ORDER BY ended DESC
LIMIT 500"""

KEEP = "&var-ue=${ue}&var-bucket=${bucket}"
LINK_BAR = (BASE + KEEP + "&var-cause=${__field.labels.cause}"
            "&var-bucket_start=${__value.time}&var-trace_id=")
LINK_CAUSE = BASE + KEEP + "&var-cause=${__value.raw}&var-bucket_start=&var-trace_id="
LINK_TRACE = (BASE + KEEP + "&var-cause=${cause}&var-bucket_start=${bucket_start}"
              "&var-trace_id=${__value.raw}")

panels = []
_id = [100]


def nid():
    _id[0] += 1
    return _id[0]


def target(sql, kind, fmt):
    return {"datasource": DS, "editorType": "sql", "format": fmt,
            "queryType": kind, "rawSql": sql.strip(), "refId": "A"}


def bars(title, sql, x, y, w, h, desc, links=None, overrides=None, stacked=True):
    defaults = {"custom": {"drawStyle": "bars", "fillOpacity": 70, "lineWidth": 1,
                           "stacking": {"mode": "normal" if stacked else "none"}},
                "unit": "short", "decimals": 0}
    if links:
        defaults["links"] = links
    panels.append({"id": nid(), "type": "timeseries", "title": title, "description": desc,
                   "datasource": DS, "gridPos": {"x": x, "y": y, "w": w, "h": h},
                   "targets": [target(sql, "timeseries", 0)],
                   "fieldConfig": {"defaults": defaults, "overrides": overrides or []},
                   "options": {"legend": {"displayMode": "list", "placement": "bottom"},
                               "tooltip": {"mode": "multi"}}})


def color(name, c):
    # series display names contain the outcome label, e.g. "attempts success"
    return {"matcher": {"id": "byRegexp", "options": f".*{name}.*"},
            "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": c}}]}


y = 0
panels.append({"id": nid(), "type": "text", "title": "",
               "gridPos": {"x": 0, "y": y, "w": 24, "h": 4},
               "options": {"mode": "markdown", "content": f"""
**What metrics add on top of traces and logs.** The SMF counts every PDU session establishment
attempt with the OTel metrics SDK (`{ATTEMPTS}`, `{DURATION}`) - cheap, always on, good for
spotting *that* and *how often* something fails. Traces and logs explain *why*.
**Drill-down:** click a bar in *Failed attempts* (cause + time bucket) or a cause in the table -> the
*Failing traces* table lists exactly those attempts -> click a `trace_id` -> trace and logs below.
"""}})
y += 4

panels.append({"id": nid(), "type": "row",
               "title": "Metrics: PDU session establishment (SMF, OTel metrics SDK, per ${bucket} s)",
               "collapsed": False, "gridPos": {"x": 0, "y": y, "w": 24, "h": 1}, "panels": []})
y += 1
bars("Failed PDU session attempts by cause (click a bar)", FAILURES_SQL, 0, y, 12, 9,
     "Sum of the delta counter per time bucket, attempts with outcome != success, split by "
     "error.type (5GSM cause, HTTP status, aborted). Click a bar to list its traces.",
     links=[{"title": "Show the failing traces of this bar", "url": LINK_BAR}])
bars("PDU session attempts by outcome", OUTCOMES_SQL, 12, y, 12, 9,
     "All attempts per bucket by 5gs.outcome as reported by the SMF.",
     overrides=[color("success", "green"), color("failure", "red"),
                color("aborted", "orange"), color("superseded", "yellow")])
y += 9
panels.append({"id": nid(), "type": "timeseries",
               "title": "Attempt duration (ms): average and maximum per bucket",
               "description": f"From the histogram {DURATION} (sum/count and max of each export).",
               "datasource": DS, "gridPos": {"x": 0, "y": y, "w": 12, "h": 8},
               "targets": [target(DURATION_SQL, "timeseries", 0)],
               "fieldConfig": {"defaults": {"unit": "ms", "custom": {"drawStyle": "line",
                                            "pointSize": 5, "showPoints": "always"}},
                               "overrides": []}})
panels.append({"id": nid(), "type": "table",
               "title": "Failure causes in the time range (click a cause)",
               "datasource": DS, "gridPos": {"x": 12, "y": y, "w": 12, "h": 8},
               "targets": [target(CAUSES_SQL, "table", 1)],
               "fieldConfig": {"defaults": {"custom": {"filterable": True}}, "overrides": [
                   {"matcher": {"id": "byName", "options": "cause"},
                    "properties": [{"id": "links", "value": [
                        {"title": "Show all failing traces with this cause", "url": LINK_CAUSE}]}]}]}})
y += 8

panels.append({"id": nid(), "type": "row",
               "title": "Failing traces behind the selection (cause: ${cause}, bucket start: ${bucket_start})",
               "collapsed": False, "gridPos": {"x": 0, "y": y, "w": 24, "h": 1}, "panels": []})
y += 1
panels.append({"id": nid(), "type": "table",
               "title": "Failing PDU session traces (SMF) for the selected cause / bucket / UE",
               "description": "smf.pdu_session.establishment spans with outcome != success, filtered "
                              "by the cause and time bucket chosen above (empty = whole range) and by "
                              "the UE search. ended = span end time (when the counter was incremented).",
               "datasource": DS, "gridPos": {"x": 0, "y": y, "w": 24, "h": 10},
               "targets": [target(DRILL_SQL, "table", 1)],
               "fieldConfig": {"defaults": {"custom": {"filterable": True}}, "overrides": [
                   {"matcher": {"id": "byName", "options": "trace_id"},
                    "properties": [{"id": "links", "value": [
                        {"title": "Show this trace and its logs", "url": LINK_TRACE}]}]},
                   {"matcher": {"id": "byName", "options": "duration_ms"},
                    "properties": [{"id": "unit", "value": "ms"}]}]}})
y += 10

# ---- the light dashboard below, links retargeted to this dashboard ---------
LIGHT_PATH = "/d/" + light.UID + "/otel-poc-trace-and-logs-light?"
lower = json.dumps(light.dashboard["panels"])
lower = lower.replace(LIGHT_PATH, "/d/" + UID + "/" + SLUG + "?")
lower = lower.replace("&var-trace_id=",
                      "&var-cause=${cause}&var-bucket_start=${bucket_start}"
                      "&var-bucket=${bucket}&var-trace_id=")
for p in json.loads(lower):
    p["gridPos"]["y"] += y
    p["id"] += 200
    panels.append(p)

links = json.loads(json.dumps(light.dashboard["links"])
                   .replace(LIGHT_PATH, "/d/" + UID + "/" + SLUG + "?")
                   .replace("&var-trace_id=", "&var-bucket=${bucket}&var-trace_id="))
links.append({"type": "link", "title": "✕ Clear metric selection", "icon": "dashboard",
              "url": "/d/" + UID + "/" + SLUG + "?var-ue=${ue}&var-trace_id=${trace_id}"
                     "&var-bucket=${bucket}&var-cause=&var-bucket_start=",
              "keepTime": True, "includeVars": False, "targetBlank": False,
              "tooltip": "Reset cause and time bucket (keeps UE and trace)", "tags": [],
              "asDropdown": False})

buckets = ["60", "300", "900"]
variables = copy.deepcopy(light.dashboard["templating"]["list"]) + [
    {"type": "custom", "name": "bucket", "label": "Bucket (s)", "query": ",".join(buckets),
     "current": {"text": "60", "value": "60"},
     "options": [{"text": b, "value": b, "selected": b == "60"} for b in buckets]},
    {"type": "textbox", "name": "cause", "label": "Cause", "query": "",
     "current": {"text": "", "value": ""},
     "description": "error.type, set by clicking a bar or a cause; empty = all"},
    {"type": "textbox", "name": "bucket_start", "label": "Bucket start (ms)", "query": "",
     "current": {"text": "", "value": ""},
     "description": "Set by clicking a bar; empty = whole time range"},
]

dashboard = {
    "uid": UID,
    "title": TITLE,
    "tags": ["open5gs", "opentelemetry", "poc", "metrics"],
    "timezone": "browser",
    "schemaVersion": 39,
    "editable": True,
    "refresh": "30s",
    "time": {"from": "now-6h", "to": "now"},
    "templating": {"list": variables},
    "links": links,
    "panels": panels,
}

if __name__ == "__main__":
    with open(sys.argv[1], "w") as f:
        json.dump(dashboard, f, indent=2)
    print("panels:", len(panels))
