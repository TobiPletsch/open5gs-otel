#!/usr/bin/env python3
"""Generates "Otel Poc - Metrics, Traces and Logs".

  [DS_UID=<datasource uid>] gen_dashboard_metrics.py <out.json>

Layout (top to bottom):
  Registration (AMF):  attempts | successes | failures by cause   (one line)
                       failure causes (list)
  PDU session (SMF):   attempts | successes | failures by cause   (one line)
                       failure causes (list) | attempt duration | failure duration
  Traces behind the selection (table)
  Selected trace + its logs
  (then the light dashboard: registration tables, all traces of a UE)

Metrics come from the OTel metrics SDK (manual instrumentation):
  open5gs.amf.registration.attempts               {5gs.outcome, error.type, 5gs.registration.type}
  open5gs.amf.registration.duration               {5gs.outcome}   (ms histogram)
  open5gs.smf.pdu_session.establishment.attempts  {5gs.outcome, error.type, 5gs.dnn}
  open5gs.smf.pdu_session.establishment.duration  {5gs.outcome}   (ms histogram)
Each is recorded where the procedure span (amf.registration /
smf.pdu_session.establishment) ends, with the span's attribute values.

Drill-down: EVERY bar is clickable. A click sets procedure, outcome, cause
and time bucket -> the table lists exactly those traces, the trace panel
shows the newest of them (or the one clicked in the table) and the log panel
its logs. Duration bars sort the table by duration (slowest first).
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
ENV = f"ResourceAttributes['deployment.environment'] = '{NS}'"

PROCS = {
    "registration": {"nf": "AMF", "span": "amf.registration",
                     "attempts": "open5gs.amf.registration.attempts",
                     "duration": "open5gs.amf.registration.duration"},
    "pdu": {"nf": "SMF", "span": "smf.pdu_session.establishment",
            "attempts": "open5gs.smf.pdu_session.establishment.attempts",
            "duration": "open5gs.smf.pdu_session.establishment.duration"},
}

PROC = "${proc:sqlstring}"
OUTCOME = "${outcome:sqlstring}"
CAUSE = "${cause:sqlstring}"
BSTART = "${bucket_start:sqlstring}"
ORDER = "${order:sqlstring}"
SPAN = (f"if({PROC} = 'registration', '{PROCS['registration']['span']}', "
        f"'{PROCS['pdu']['span']}')")
METRIC = (f"if({PROC} = 'registration', '{PROCS['registration']['attempts']}', "
          f"'{PROCS['pdu']['attempts']}')")

# Meaning of error.type values (TS 24.501 5GMM / 5GSM causes + PoC outcomes)
CAUSE_TEXT = {
    "5gmm_cause_3": "Illegal UE", "5gmm_cause_5": "PEI not accepted",
    "5gmm_cause_6": "Illegal ME", "5gmm_cause_7": "5GS services not allowed (not provisioned)",
    "5gmm_cause_9": "UE identity cannot be derived by the network",
    "5gmm_cause_10": "Implicitly de-registered", "5gmm_cause_11": "PLMN not allowed",
    "5gmm_cause_12": "Tracking area not allowed",
    "5gmm_cause_13": "Roaming not allowed in this tracking area",
    "5gmm_cause_15": "No suitable cells in tracking area", "5gmm_cause_20": "MAC failure",
    "5gmm_cause_21": "Synch failure", "5gmm_cause_22": "Congestion",
    "5gmm_cause_26": "Non-5G authentication unacceptable",
    "5gmm_cause_27": "N1 mode not allowed", "5gmm_cause_62": "No network slices available",
    "5gmm_cause_65": "Maximum number of PDU sessions reached",
    "5gmm_cause_90": "Payload was not forwarded",
    "5gmm_cause_91": "DNN not supported or not subscribed in the slice",
    "5gmm_cause_92": "Insufficient user-plane resources for the PDU session",
    "5gmm_cause_95": "Semantically incorrect message",
    "5gmm_cause_96": "Invalid mandatory information",
    "5gmm_cause_111": "Protocol error, unspecified",
    "5gsm_cause_8": "Operator determined barring", "5gsm_cause_26": "Insufficient resources",
    "5gsm_cause_27": "Missing or unknown DNN", "5gsm_cause_28": "Unknown PDU session type",
    "5gsm_cause_29": "User authentication or authorization failed",
    "5gsm_cause_31": "Request rejected, unspecified",
    "5gsm_cause_32": "Service option not supported",
    "5gsm_cause_33": "Requested service option not subscribed",
    "5gsm_cause_36": "Regular deactivation",
    "5gsm_cause_38": "Network failure (e.g. UPF not reachable)",
    "5gsm_cause_43": "Invalid PDU session identity",
    "5gsm_cause_54": "PDU session does not exist",
    "5gsm_cause_67": "Insufficient resources for slice and DNN",
    "5gsm_cause_68": "Not supported SSC mode",
    "5gsm_cause_69": "Insufficient resources for slice",
    "5gsm_cause_70": "Missing or unknown DNN in a slice",
    "5gsm_cause_111": "Protocol error, unspecified",
    "aborted": "UE/session context removed before the procedure ended",
    "superseded": "replaced by a new request of the same UE",
    "failure": "failed without a NAS cause (e.g. timer expiry, SBI error)",
}
CAUSE_MEANING = ("transform(cause, [" + ", ".join(f"'{k}'" for k in CAUSE_TEXT) + "], ["
                 + ", ".join("'" + v.replace("'", "''") + "'" for v in CAUSE_TEXT.values())
                 + "], '')")


def bucket(col="TimeUnix"):
    return f"toStartOfInterval({col}, INTERVAL ${{bucket}} second)"


def attempts_sql(proc, where, value="attempts", by_cause=False):
    p = PROCS[proc]
    cause = ", Attributes['error.type'] AS cause" if by_cause else ""
    group = ", cause" if by_cause else ""
    return f"""
SELECT {bucket()} AS time{cause},
       sum(Value) AS {value}
FROM default.otel_metrics_sum
WHERE $__timeFilter(TimeUnix)
  AND MetricName = '{p["attempts"]}'
  AND {ENV}{where}
GROUP BY time{group}
ORDER BY time"""


def duration_sql(proc, where):
    p = PROCS[proc]
    return f"""
SELECT {bucket()} AS time,
       round(sum(Sum) / greatest(sum(Count), 1), 1) AS avg_ms,
       round(max(Max), 1) AS max_ms
FROM default.otel_metrics_histogram
WHERE $__timeFilter(TimeUnix)
  AND MetricName = '{p["duration"]}'
  AND {ENV}{where}
GROUP BY time
ORDER BY time"""


def causes_sql(proc):
    p = PROCS[proc]
    return f"""
SELECT cause, {CAUSE_MEANING} AS meaning, failures,
       round(100 * failures / greatest(sum(failures) OVER (), 1), 1) AS share_pct,
       first_seen, last_seen
FROM (
  SELECT Attributes['error.type'] AS cause,
         sum(Value) AS failures,
         min(TimeUnix) AS first_seen,
         max(TimeUnix) AS last_seen
  FROM default.otel_metrics_sum
  WHERE $__timeFilter(TimeUnix)
    AND MetricName = '{p["attempts"]}'
    AND Attributes['5gs.outcome'] != 'success'
    AND {ENV}
  GROUP BY cause)
ORDER BY failures DESC"""


SUCCESS = "\n  AND Attributes['5gs.outcome'] = 'success'"
FAILED = "\n  AND Attributes['5gs.outcome'] != 'success'"

# Exact drill-down window: a delta data point covers [StartTimeUnix, TimeUnix]
# (one 10 s export interval). A bar sums the points whose TimeUnix falls into
# its bucket, so its measurements happened between the first point's
# StartTimeUnix and the last point's TimeUnix - and the counters are
# incremented where the spans end.
BUCKET_POINTS = f"""WHERE MetricName = {METRIC} AND {ENV}
           AND toUnixTimestamp({bucket()})
               = intDiv(toInt64OrZero({BSTART}), 1000)"""

DRILL_SQL = f"""
SELECT addNanoseconds(Timestamp, Duration) AS ended,
       TraceId AS trace_id,
       if(SpanName = 'amf.registration', 'registration', 'PDU session') AS procedure,
       SpanAttributes['5gs.outcome'] AS outcome,
       nullIf(SpanAttributes['error.type'], '') AS cause,
       nullIf(StatusMessage, '') AS reason,
       round(Duration / 1e6, 1) AS duration_ms,
       nullIf(SpanAttributes['5gs.dnn'], '') AS dnn,
       SpanAttributes['5gs.ue.ref'] AS ue_ref,
       arrayStringConcat(Events.Name, ' > ') AS milestones
FROM default.otel_traces
WHERE $__timeFilter(Timestamp)
  AND SpanName = {SPAN}
  AND ScopeName = 'open5gs'
  AND {ENV}
  AND ({OUTCOME} = 'all' OR ({OUTCOME} = 'success') = (SpanAttributes['5gs.outcome'] = 'success'))
  AND ({CAUSE} = '' OR SpanAttributes['error.type'] = {CAUSE})
  AND ({BSTART} = '' OR addNanoseconds(Timestamp, Duration) BETWEEN
         (SELECT min(StartTimeUnix) FROM default.otel_metrics_sum {BUCKET_POINTS})
     AND (SELECT max(TimeUnix) FROM default.otel_metrics_sum {BUCKET_POINTS}))
  AND {light.UE_FILTER.replace("{col}", "SpanAttributes['5gs.ue.ref']")}
ORDER BY if({ORDER} = 'duration', Duration, 0) DESC, ended DESC
LIMIT 500"""

# The trace shown: the one clicked in a table, else the first one of the
# selection (a clicked bar / cause), else none.
SELECTED = (f"if('${{trace_id}}' != '', '${{trace_id}}', "
            f"if({BSTART} = '' AND {CAUSE} = '', '', "
            f"ifNull((SELECT trace_id FROM ({DRILL_SQL}) LIMIT 1), '')))")
TRACE_SQL = light.TRACE_SQL.replace("WHERE TraceId = '${trace_id}'",
                                    f"WHERE TraceId = {SELECTED}")
LOGS_SQL = light.LOGS_SQL.replace("AND TraceId = '${trace_id}'",
                                  f"AND TraceId != '' AND TraceId = {SELECTED}")
assert TRACE_SQL != light.TRACE_SQL and LOGS_SQL != light.LOGS_SQL

KEEP = "&var-ue=${ue}&var-bucket=${bucket}"


def select_link(proc, outcome, cause="", bucket_start="${__value.time}", order="time"):
    return (BASE + KEEP + f"&var-proc={proc}&var-outcome={outcome}&var-cause={cause}"
            f"&var-bucket_start={bucket_start}&var-order={order}&var-trace_id=")


LINK_TRACE = (BASE + KEEP + "&var-proc=${proc}&var-outcome=${outcome}&var-cause=${cause}"
              "&var-bucket_start=${bucket_start}&var-order=${order}&var-trace_id=${__value.raw}")

panels = []
_id = [100]


def nid():
    _id[0] += 1
    return _id[0]


def target(sql, kind, fmt):
    return {"datasource": DS, "editorType": "sql", "format": fmt,
            "queryType": kind, "rawSql": sql.strip(), "refId": "A"}


def fixed(c):
    return {"mode": "fixed", "fixedColor": c}


def bars(title, sql, x, y, w, h, desc, link, link_title, color=None, unit="short",
         stacked=True, overrides=None):
    defaults = {"custom": {"drawStyle": "bars", "fillOpacity": 70, "lineWidth": 1,
                           "stacking": {"mode": "normal" if stacked else "none"}},
                "unit": unit, "decimals": 0 if unit == "short" else 1,
                "links": [{"title": link_title, "url": link}]}
    if color:
        defaults["color"] = fixed(color)
    panels.append({"id": nid(), "type": "timeseries", "title": title, "description": desc,
                   "datasource": DS, "gridPos": {"x": x, "y": y, "w": w, "h": h},
                   "targets": [target(sql, "timeseries", 0)],
                   "fieldConfig": {"defaults": defaults, "overrides": overrides or []},
                   "options": {"legend": {"displayMode": "list", "placement": "bottom"},
                               "tooltip": {"mode": "multi"}}})


def causes_table(proc, x, y, w, h):
    p = PROCS[proc]
    panels.append({
        "id": nid(), "type": "table",
        "title": f"{'Registration' if proc == 'registration' else 'PDU session'} "
                 "failure causes in the time range (click a cause)",
        "description": f"error.type of the failed attempts ({p['attempts']}), with the "
                       "meaning of the 3GPP cause. Click a cause to list all its traces.",
        "datasource": DS, "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "targets": [target(causes_sql(proc), "table", 1)],
        "fieldConfig": {"defaults": {"custom": {"filterable": True}}, "overrides": [
            {"matcher": {"id": "byName", "options": "cause"},
             "properties": [{"id": "links", "value": [{
                 "title": "Show all failing traces with this cause",
                 "url": select_link(proc, "failure", "${__value.raw}", "")}]}]},
            {"matcher": {"id": "byName", "options": "share_pct"},
             "properties": [{"id": "unit", "value": "percent"}]},
            {"matcher": {"id": "byName", "options": "meaning"},
             "properties": [{"id": "custom.width", "value": 330}]}]}})


def procedure_line(proc, y):
    """attempts | successes | failures by cause - one line."""
    p = PROCS[proc]
    name = "Registration" if proc == "registration" else "PDU session"
    src = f"{p['attempts']} ({p['nf']})"
    bars(f"{name} attempts", attempts_sql(proc, ""), 0, y, 8, 8,
         f"All attempts per bucket, any outcome - {src}. Click a bar: all its traces.",
         select_link(proc, "all"), "Show the traces of this bar", color="blue")
    bars(f"{name} successes", attempts_sql(proc, SUCCESS, "successes"), 8, y, 8, 8,
         f"Attempts with 5gs.outcome = success - {src}. Click a bar: its traces.",
         select_link(proc, "success"), "Show the traces of this bar", color="green")
    bars(f"{name} failures by cause", attempts_sql(proc, FAILED, "failures", True),
         16, y, 8, 8,
         f"Attempts with 5gs.outcome != success, stacked by error.type - {src}. "
         "Click a bar: the traces of that cause in that bucket.",
         select_link(proc, "failure", "${__field.labels.cause}"),
         "Show the failing traces of this bar")


y = 0
panels.append({"id": nid(), "type": "text", "title": "",
               "gridPos": {"x": 0, "y": y, "w": 24, "h": 4},
               "options": {"mode": "markdown", "content": """
**Metrics, traces and logs of the two 5G procedures.** The AMF counts every registration and the SMF
every PDU session establishment with the OTel metrics SDK - cheap, always on: *that* and *how often*
something fails. **Click any bar** (or a cause): the table *Traces behind the selection* lists exactly
those attempts, the **trace panel shows the newest one** (click another `trace_id` to switch) and the
log panel its logs from all NFs. **UE search:** IMSI or `ue_ref` in the *UE* field at the top.
"""}})
y += 4

panels.append({"id": nid(), "type": "row",
               "title": "Registration (AMF, OTel metrics SDK, per ${bucket} s)",
               "collapsed": False, "gridPos": {"x": 0, "y": y, "w": 24, "h": 1}, "panels": []})
y += 1
procedure_line("registration", y)
y += 8
causes_table("registration", 0, y, 24, 6)
y += 6

panels.append({"id": nid(), "type": "row",
               "title": "PDU session establishment (SMF, OTel metrics SDK, per ${bucket} s)",
               "collapsed": False, "gridPos": {"x": 0, "y": y, "w": 24, "h": 1}, "panels": []})
y += 1
procedure_line("pdu", y)
y += 8
causes_table("pdu", 0, y, 8, 8)
DUR_OVR = [{"matcher": {"id": "byName", "options": "max_ms"},
            "properties": [{"id": "color", "value": fixed("orange")},
                           {"id": "custom.drawStyle", "value": "points"},
                           {"id": "custom.pointSize", "value": 7}]}]
bars("PDU session attempt duration (ms): average bars, maximum points",
     duration_sql("pdu", ""), 8, y, 8, 8,
     f"All attempts, from the histogram {PROCS['pdu']['duration']} (sum/count and max). "
     "Click a bar: the attempts of that bucket, slowest first.",
     select_link("pdu", "all", order="duration"), "Show the attempts of this bar (slowest first)",
     color="blue", unit="ms", stacked=False, overrides=DUR_OVR)
bars("PDU session failure duration (ms): average bars, maximum points",
     duration_sql("pdu", FAILED), 16, y, 8, 8,
     "Failed attempts only (5gs.outcome != success): how long until the failure was reported. "
     "Click a bar: the failures of that bucket, slowest first.",
     select_link("pdu", "failure", order="duration"),
     "Show the failures of this bar (slowest first)",
     color="red", unit="ms", stacked=False, overrides=DUR_OVR)
y += 8

panels.append({"id": nid(), "type": "row",
               "title": "Traces behind the selection (procedure: ${proc}, outcome: ${outcome}, "
                        "cause: ${cause}, bucket start: ${bucket_start})",
               "collapsed": False, "gridPos": {"x": 0, "y": y, "w": 24, "h": 1}, "panels": []})
y += 1
panels.append({
    "id": nid(), "type": "table",
    "title": "Traces behind the selection (click a trace_id to show it below)",
    "description": "Procedure spans (amf.registration or smf.pdu_session.establishment) matching "
                   "the clicked bar / cause: procedure, outcome, cause, time bucket (empty = whole "
                   "range) and UE search. ended = span end = when the counter was incremented.",
    "datasource": DS, "gridPos": {"x": 0, "y": y, "w": 24, "h": 9},
    "targets": [target(DRILL_SQL, "table", 1)],
    "fieldConfig": {"defaults": {"custom": {"filterable": True}}, "overrides": [
        {"matcher": {"id": "byName", "options": "trace_id"},
         "properties": [{"id": "links", "value": [
             {"title": "Show this trace and its logs", "url": LINK_TRACE}]}]},
        {"matcher": {"id": "byName", "options": "outcome"},
         "properties": [{"id": "custom.cellOptions", "value": {"type": "color-text"}},
                        {"id": "mappings", "value": [{"type": "value", "options": {
                            "success": {"color": "green"}, "failure": {"color": "red"},
                            "aborted": {"color": "orange"},
                            "superseded": {"color": "yellow"}}}]}]},
        {"matcher": {"id": "byName", "options": "duration_ms"},
         "properties": [{"id": "unit", "value": "ms"}]}]}})
y += 9

# ---- selected trace + logs, then the rest of the light dashboard ----------
LIGHT_PATH = "/d/" + light.UID + "/otel-poc-trace-and-logs-light?"
lower = json.dumps(light.dashboard["panels"])
lower = lower.replace(LIGHT_PATH, "/d/" + UID + "/" + SLUG + "?")
lower = lower.replace("&var-trace_id=",
                      "&var-proc=${proc}&var-outcome=${outcome}&var-cause=${cause}"
                      "&var-bucket_start=${bucket_start}&var-order=${order}"
                      "&var-bucket=${bucket}&var-trace_id=")
lower = json.loads(lower)

trace_row = {"id": nid(), "type": "row",
             "title": "Selected trace (${trace_id}; empty = newest trace of the selection)",
             "collapsed": False, "gridPos": {"x": 0, "y": y, "w": 24, "h": 1}, "panels": []}
panels.append(trace_row)
y += 1
for p in lower:
    if p["type"] == "traces":
        p["targets"][0]["rawSql"] = TRACE_SQL.strip()
        p["description"] = ("Procedure span with the SBI/PFCP spans of all NFs (AMF, SMF, AUSF, "
                            "UDM, UDR, PCF, BSF). Expand a span for attributes, events, links.")
        p["gridPos"].update({"y": y, "h": 20})
        p["id"] = nid()
        panels.append(p)
        y += 20
for p in lower:
    if p["type"] == "logs":
        p["targets"][0]["rawSql"] = LOGS_SQL.strip()
        p["title"] = "Logs of the selected trace (all NFs, matched by TraceId)"
        p["gridPos"].update({"y": y, "h": 16})
        p["id"] = nid()
        panels.append(p)
        y += 16

# light: registration tables + all traces of a UE (no text, no trace/logs)
keep, start = [], None
for p in lower:
    if p["type"] in ("text", "traces", "logs"):
        continue
    if p["type"] == "row" and p["title"].startswith("Selected trace"):
        continue
    keep.append(p)
start = min(p["gridPos"]["y"] for p in keep)
for p in keep:
    p["gridPos"]["y"] += y - start
    p["id"] += 300
    panels.append(p)

links = json.loads(json.dumps(light.dashboard["links"])
                   .replace(LIGHT_PATH, "/d/" + UID + "/" + SLUG + "?")
                   .replace("&var-trace_id=", "&var-bucket=${bucket}&var-trace_id="))
links.append({"type": "link", "title": "✕ Clear selection", "icon": "dashboard",
              "url": "/d/" + UID + "/" + SLUG + "?var-ue=${ue}&var-trace_id="
                     "&var-bucket=${bucket}&var-proc=pdu&var-outcome=failure"
                     "&var-cause=&var-bucket_start=&var-order=time",
              "keepTime": True, "includeVars": False, "targetBlank": False,
              "tooltip": "Reset the clicked bar / cause and the trace (keeps the UE search)",
              "tags": [], "asDropdown": False})


def custom(name, label, values, default, desc):
    return {"type": "custom", "name": name, "label": label, "query": ",".join(values),
            "description": desc, "current": {"text": default, "value": default},
            "options": [{"text": v, "value": v, "selected": v == default} for v in values]}


buckets = ["60", "300", "900"]
variables = copy.deepcopy(light.dashboard["templating"]["list"]) + [
    custom("bucket", "Bucket (s)", buckets, "60", "Width of the bars"),
    custom("proc", "Procedure", ["registration", "pdu"], "pdu", "Set by clicking a bar"),
    custom("outcome", "Outcome", ["all", "success", "failure"], "failure",
           "Set by clicking a bar"),
    {"type": "textbox", "name": "cause", "label": "Cause", "query": "",
     "current": {"text": "", "value": ""},
     "description": "error.type, set by clicking a failure bar or a cause; empty = all"},
    {"type": "textbox", "name": "bucket_start", "label": "Bucket start (ms)", "query": "",
     "current": {"text": "", "value": ""},
     "description": "Set by clicking a bar; empty = whole time range"},
    custom("order", "Order", ["time", "duration"], "time",
           "Order of the trace table; duration bars set 'duration' (slowest first)"),
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
