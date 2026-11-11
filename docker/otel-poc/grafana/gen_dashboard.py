#!/usr/bin/env python3
"""Generates the Grafana dashboard "Open5GS OTel PoC" (ClickHouse datasource).

  gen_dashboard.py <out.json> [<sql-test-dir>]

With a test dir, every panel query is also written there with the Grafana
macros replaced by plain ClickHouse SQL, so it can be run with clickhouse-client.
"""
import json
import os
import re
import sys

DS = {"type": "grafana-clickhouse-datasource", "uid": "ClickHouse"}
FORMAT = {"timeseries": 0, "table": 1, "logs": 2, "traces": 3}
NS = "open5gs-ns"
UID = "open5gs-otel-poc"
TRACE_LINK = ("/d/" + UID + "/open5gs-otel-poc?${__url_time_range}"
              "&var-trace_id=${__value.raw}")

panels = []
_id = [0]


def nid():
    _id[0] += 1
    return _id[0]


def target(sql, kind):
    return {"datasource": DS, "editorType": "sql", "format": FORMAT[kind],
            "queryType": kind, "rawSql": sql.strip(), "refId": "A"}


def panel(ptype, title, sql, kind, x, y, w, h, desc="", **extra):
    p = {"id": nid(), "type": ptype, "title": title, "datasource": DS,
         "gridPos": {"x": x, "y": y, "w": w, "h": h},
         "targets": [target(sql, kind)]}
    if desc:
        p["description"] = desc
    p.update(extra)
    panels.append(p)
    return p


def row(title, y):
    panels.append({"id": nid(), "type": "row", "title": title,
                   "collapsed": False, "gridPos": {"x": 0, "y": y, "w": 24, "h": 1},
                   "panels": []})


def text(title, md, x, y, w, h):
    panels.append({"id": nid(), "type": "text", "title": title,
                   "gridPos": {"x": x, "y": y, "w": w, "h": h},
                   "options": {"mode": "markdown", "content": md}})


def counter_increase(metrics):
    """Per-interval increase of Prometheus counters.

    StartTimeUnix changes when a counter resets (NF restart), so each
    (metric, cause, start) partition is one monotonic series.
    """
    names = ", ".join("'%s'" % m for m in metrics)
    return f"""
SELECT time, metric, sum(inc) AS value
FROM (
  SELECT time, metric,
         greatest(v - lagInFrame(v, 1, v) OVER (PARTITION BY metric, series, st ORDER BY time), 0) AS inc
  FROM (
    SELECT $__timeInterval(TimeUnix) AS time,
           replaceRegexpOne(MetricName, '^fivegs_[a-z]+function_', '') AS metric,
           Attributes['cause'] AS series,
           StartTimeUnix AS st,
           max(Value) AS v
    FROM default.otel_metrics_sum
    WHERE $__timeFilter(TimeUnix)
      AND Attributes['namespace'] = '{NS}'
      AND MetricName IN ({names})
    GROUP BY time, metric, series, st))
GROUP BY time, metric
ORDER BY time"""


def traces_per_interval(span):
    return f"""
SELECT $__timeInterval(Timestamp) AS time,
       concat('trace ', SpanAttributes['5gs.outcome']) AS outcome,
       count() AS value
FROM default.otel_traces
WHERE $__timeFilter(Timestamp)
  AND SpanName = '{span}'
  AND ResourceAttributes['deployment.environment'] = '{NS}'
GROUP BY time, outcome
ORDER BY time"""


def outcome_summary(span):
    return f"""
SELECT SpanAttributes['5gs.outcome'] AS outcome,
       count() AS traces,
       round(quantile(0.5)(Duration) / 1e6, 1) AS p50_ms,
       round(quantile(0.95)(Duration) / 1e6, 1) AS p95_ms,
       round(max(Duration) / 1e6, 1) AS max_ms
FROM default.otel_traces
WHERE $__timeFilter(Timestamp)
  AND SpanName = '{span}'
  AND ResourceAttributes['deployment.environment'] = '{NS}'
GROUP BY outcome
ORDER BY traces DESC"""


def recent_traces(span, cause_attr, extra_cols):
    return f"""
SELECT Timestamp AS time,
       TraceId AS trace_id,
       SpanAttributes['5gs.outcome'] AS outcome,
       {extra_cols},
       SpanAttributes['{cause_attr}'] AS cause,
       nullIf(StatusMessage, '') AS reason,
       round(Duration / 1e6, 1) AS duration_ms,
       SpanAttributes['5gs.ue.ref'] AS ue_ref,
       arrayStringConcat(Events.Name, ' > ') AS milestones
FROM default.otel_traces
WHERE $__timeFilter(Timestamp)
  AND SpanName = '{span}'
  AND ResourceAttributes['deployment.environment'] = '{NS}'
ORDER BY Timestamp DESC
LIMIT 100"""


def step_latency(span):
    return f"""
SELECT step, round(avg(ms), 1) AS avg_ms, round(quantile(0.95)(ms), 1) AS p95_ms,
       round(max(ms), 1) AS max_ms, count() AS n
FROM (
  SELECT arrayJoin(arrayZip(
           arrayEnumerate(Events.Name),
           Events.Name,
           arrayMap((t, p) -> dateDiff('microsecond', p, t) / 1000,
                    Events.Timestamp,
                    arrayPushFront(arrayPopBack(Events.Timestamp), Timestamp)))) AS e,
         e.1 AS idx, e.2 AS step, e.3 AS ms
  FROM default.otel_traces
  WHERE $__timeFilter(Timestamp)
    AND SpanName = '{span}'
    AND SpanAttributes['5gs.outcome'] = 'success'
    AND ResourceAttributes['deployment.environment'] = '{NS}')
GROUP BY step
ORDER BY min(idx)"""


TRACE_SQL = """
SELECT TraceId AS traceID,
       SpanId AS spanID,
       ParentSpanId AS parentSpanID,
       ServiceName AS serviceName,
       SpanName AS operationName,
       Timestamp AS startTime,
       multiply(Duration, 0.000001) AS duration,
       arrayMap(key -> map('key', key, 'value', SpanAttributes[key]), mapKeys(SpanAttributes)) AS tags,
       arrayMap(key -> map('key', key, 'value', ResourceAttributes[key]), mapKeys(ResourceAttributes)) AS serviceTags,
       if(StatusCode IN ('Error', 'STATUS_CODE_ERROR'), 2, 0) AS statusCode,
       StatusMessage AS statusMessage,
       SpanKind AS kind,
       arrayMap((name, timestamp, attributes) -> tuple(name, toString(toUnixTimestamp64Milli(timestamp)),
                arrayMap(key -> map('key', key, 'value', attributes[key]), mapKeys(attributes)))::Tuple(name String, timestamp String, fields Array(Map(String, String))),
                Events.Name, Events.Timestamp, Events.Attributes) AS logs
FROM default.otel_traces
WHERE TraceId = '${trace_id}'"""

LOGS_SQL = """
SELECT Timestamp AS timestamp,
       Body AS body,
       SeverityText AS level,
       LogAttributes['k8s.container.name'] AS container,
       TraceId AS traceID,
       SpanId AS spanID
FROM default.otel_logs
WHERE $__timeFilter(Timestamp)
  AND TraceId = '${trace_id}'
ORDER BY Timestamp"""


def trace_table_overrides():
    return {"defaults": {"custom": {"filterable": True}},
            "overrides": [
                {"matcher": {"id": "byName", "options": "trace_id"},
                 "properties": [{"id": "links", "value": [
                     {"title": "Show this trace and its logs", "url": TRACE_LINK}]}]},
                {"matcher": {"id": "byName", "options": "outcome"},
                 "properties": [{"id": "custom.cellOptions",
                                 "value": {"type": "color-text"}},
                                {"id": "mappings", "value": [{"type": "value", "options": {
                                    "success": {"color": "green"},
                                    "failure": {"color": "red"},
                                    "aborted": {"color": "orange"},
                                    "superseded": {"color": "yellow"}}}]}]},
                {"matcher": {"id": "byName", "options": "duration_ms"},
                 "properties": [{"id": "unit", "value": "ms"}]}]}


def ts_options():
    return {"defaults": {"custom": {"drawStyle": "bars", "fillOpacity": 60,
                                    "stacking": {"mode": "none"}},
                         "unit": "short", "decimals": 0},
            "overrides": []}


y = 0
text("About this dashboard", f"""
**Manual OpenTelemetry instrumentation PoC** (Open5GS `otel-poc` build, namespace `{NS}`).

* **Temporal correlation**: the bar charts put the existing Prometheus counters (scraped by the
  OTel gateway) next to the number of traces per interval, for the same time range.
  There is **no direct link** between a counter sample and a trace (no exemplars).
* **Direct correlation**: click a `trace_id` in a table. The *Selected trace* row shows that
  span and every AMF/SMF log line carrying the same `trace_id`.
* `pdusessioncreationsucc` is counted at the `201 Created` (before PCF/PFCP) and again after UDM
  registration on this build, so it can exceed `pdusessioncreationreq`. The SMF trace only succeeds
  once the user plane is active.
* Trace attributes never contain SUPI/SUCI; `ue_ref` is a salted SHA-256 pseudonym.
""", 0, y, 24, 6)
y += 6

row("AMF - UE registration (amf.registration)", y); y += 1
panel("timeseries", "Registration counters (Prometheus, increase per interval)",
      counter_increase(["fivegs_amffunction_rm_reginitreq",
                        "fivegs_amffunction_rm_reginitsucc",
                        "fivegs_amffunction_rm_reginitfail"]),
      "timeseries", 0, y, 8, 8,
      desc="Existing Open5GS AMF counters for initial registrations.",
      fieldConfig=ts_options())
panel("timeseries", "Registration traces per interval (by outcome)",
      traces_per_interval("amf.registration"), "timeseries", 8, y, 8, 8,
      fieldConfig=ts_options())
panel("table", "Registration outcomes (traces)",
      outcome_summary("amf.registration"), "table", 16, y, 8, 8)
y += 8
panel("table", "Recent registration traces (click trace_id)",
      recent_traces("amf.registration", "5gs.5gmm.cause",
                    "SpanAttributes['5gs.registration.type'] AS type"),
      "table", 0, y, 24, 8, fieldConfig=trace_table_overrides())
y += 8

row("SMF - PDU session establishment (smf.pdu_session.establishment)", y); y += 1
panel("timeseries", "PDU session counters (Prometheus, increase per interval)",
      counter_increase(["fivegs_smffunction_sm_pdusessioncreationreq",
                        "fivegs_smffunction_sm_pdusessioncreationsucc",
                        "fivegs_smffunction_sm_pdusessioncreationfail"]),
      "timeseries", 0, y, 8, 8,
      desc="Existing Open5GS SMF counters. creationsucc is counted at 201 Created "
           "(and again after UDM registration on this build).",
      fieldConfig=ts_options())
panel("timeseries", "PDU session traces per interval (by outcome)",
      traces_per_interval("smf.pdu_session.establishment"), "timeseries", 8, y, 8, 8,
      fieldConfig=ts_options())
panel("table", "PDU session outcomes (traces)",
      outcome_summary("smf.pdu_session.establishment"), "table", 16, y, 8, 8)
y += 8
panel("table", "Establishment latency per milestone (successful traces)",
      step_latency("smf.pdu_session.establishment"), "table", 0, y, 10, 8,
      desc="Time from the previous milestone (or span start) to this span event. "
           "Steps of ~2.5 s / 5 s indicate PFCP retransmissions between SMF and UPF.",
      fieldConfig={"defaults": {"unit": "ms"}, "overrides": [
          {"matcher": {"id": "byName", "options": "n"},
           "properties": [{"id": "unit", "value": "short"}]}]})
panel("table", "Recent PDU session traces (click trace_id)",
      recent_traces("smf.pdu_session.establishment", "5gs.5gsm.cause",
                    "SpanAttributes['5gs.dnn'] AS dnn, "
                    "SpanAttributes['5gs.pdu_session.type'] AS session_type"),
      "table", 10, y, 14, 8, fieldConfig=trace_table_overrides())
y += 8

row("Selected trace (${trace_id})", y); y += 1
panel("traces", "Trace", TRACE_SQL, "traces", 0, y, 24, 9)
y += 9
panel("logs", "Logs of the selected trace (AMF/SMF, matched by TraceId)",
      LOGS_SQL, "logs", 0, y, 24, 12,
      options={"showTime": True, "wrapLogMessage": True, "sortOrder": "Ascending",
               "enableLogDetails": True, "dedupStrategy": "none"})

dashboard = {
    "uid": UID,
    "title": "Open5GS OTel PoC",
    "tags": ["open5gs", "opentelemetry", "poc"],
    "timezone": "browser",
    "schemaVersion": 39,
    "editable": True,
    "refresh": "30s",
    "time": {"from": "now-1h", "to": "now"},
    "templating": {"list": [{
        "type": "textbox", "name": "trace_id", "label": "Trace ID",
        "query": "", "current": {"text": "", "value": ""},
        "description": "Set by clicking a trace_id in one of the tables"}]},
    "panels": panels,
}

with open(sys.argv[1], "w") as f:
    json.dump(dashboard, f, indent=2)
print("panels:", len(panels))

if len(sys.argv) > 2:
    outdir = sys.argv[2]
    os.makedirs(outdir, exist_ok=True)
    for p in panels:
        for t in p.get("targets", []):
            sql = t["rawSql"]
            sql = re.sub(r"\$__timeFilter\((\w+)\)", r"\1 >= now() - INTERVAL 2 HOUR", sql)
            sql = re.sub(r"\$__timeInterval\((\w+)\)",
                         r"toStartOfInterval(\1, INTERVAL 60 second)", sql)
            sql = sql.replace("${trace_id}", "@TRACE_ID@")
            name = "%02d.sql" % p["id"]
            with open(os.path.join(outdir, name), "w") as f:
                f.write("-- " + p["title"] + "\n" + sql + "\nFORMAT Null\n")
