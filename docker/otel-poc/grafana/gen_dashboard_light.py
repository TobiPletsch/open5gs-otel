#!/usr/bin/env python3
"""Generates the Grafana dashboard "Otel Poc - Trace and Logs - Light".

  [DS_UID=<datasource uid>] gen_dashboard_light.py <out.json>

Traces and logs only:
  * Trace overview: "Recent registration traces" and "Recent PDU session
    traces" (same content as in the main dashboard), filterable by outcome.
  * Clicking a trace_id (or a registration_trace) selects that trace: one
    large trace panel and one logs panel show it and all AMF/SMF log lines
    carrying the same trace_id.
"""
import json
import os
import sys

DS = {"type": "grafana-clickhouse-datasource",
      "uid": os.environ.get("DS_UID", "ClickHouse")}
NS = "open5gs-ns"
UID = "open5gs-otel-poc-light"
TRACE_LINK = ("/d/" + UID + "/otel-poc-trace-and-logs-light?${__url_time_range}"
              "&var-outcome=${outcome}&var-trace_id=${__value.raw}")

# '${outcome}' is 'all' or one of the outcome values
OUTCOME_FILTER = "('${outcome}' = 'all' OR %s = '${outcome}')"

REGISTRATIONS_SQL = f"""
SELECT Timestamp AS time,
       TraceId AS trace_id,
       SpanAttributes['5gs.outcome'] AS outcome,
       SpanAttributes['5gs.registration.type'] AS type,
       SpanAttributes['5gs.5gmm.cause'] AS cause,
       nullIf(StatusMessage, '') AS reason,
       round(Duration / 1e6, 1) AS duration_ms,
       SpanAttributes['5gs.ue.ref'] AS ue_ref,
       arrayStringConcat(Events.Name, ' > ') AS milestones
FROM default.otel_traces
WHERE $__timeFilter(Timestamp)
  AND SpanName = 'amf.registration'
  AND ResourceAttributes['deployment.environment'] = '{NS}'
  AND {OUTCOME_FILTER % "SpanAttributes['5gs.outcome']"}
ORDER BY Timestamp DESC
LIMIT 200"""

SESSIONS_SQL = f"""
SELECT a.Timestamp AS time,
       a.TraceId AS trace_id,
       a.SpanAttributes['5gs.outcome'] AS outcome,
       s.smf_outcome AS smf_outcome,
       a.SpanAttributes['5gs.dnn'] AS dnn,
       s.cause AS cause,
       nullIf(a.StatusMessage, '') AS reason,
       round(a.Duration / 1e6, 1) AS duration_ms,
       a.SpanAttributes['5gs.ue.ref'] AS ue_ref,
       a.SpanAttributes['5gs.registration.trace_id'] AS registration_trace,
       s.milestones AS smf_milestones
FROM default.otel_traces AS a
LEFT JOIN (
  SELECT TraceId,
         SpanAttributes['5gs.outcome'] AS smf_outcome,
         SpanAttributes['5gs.5gsm.cause'] AS cause,
         arrayStringConcat(Events.Name, ' > ') AS milestones
  FROM default.otel_traces
  WHERE $__timeFilter(Timestamp)
    AND SpanName = 'smf.pdu_session.establishment') AS s ON s.TraceId = a.TraceId
WHERE $__timeFilter(a.Timestamp)
  AND a.SpanName = 'amf.pdu_session.establishment'
  AND a.ResourceAttributes['deployment.environment'] = '{NS}'
  AND {OUTCOME_FILTER % "a.SpanAttributes['5gs.outcome']"}
ORDER BY a.Timestamp DESC
LIMIT 200"""

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
                Events.Name, Events.Timestamp, Events.Attributes) AS logs,
       arrayMap((t, s) -> map('traceID', t, 'spanID', s), Links.TraceId, Links.SpanId) AS references
FROM default.otel_traces
WHERE TraceId = '${trace_id}'"""

LOGS_SQL = """
SELECT Timestamp AS timestamp,
       Body AS body,
       SeverityText AS level,
       LogAttributes['k8s.container.name'] AS container,
       LogAttributes['log_domain'] AS domain,
       TraceId AS traceID,
       SpanId AS spanID
FROM default.otel_logs
WHERE $__timeFilter(Timestamp)
  AND TraceId = '${trace_id}'
ORDER BY Timestamp"""

panels = []
_id = [0]


def nid():
    _id[0] += 1
    return _id[0]


def target(sql, kind, fmt):
    return {"datasource": DS, "editorType": "sql", "format": fmt,
            "queryType": kind, "rawSql": sql.strip(), "refId": "A"}


def table_overrides():
    link = lambda title: [{"id": "links", "value": [{"title": title, "url": TRACE_LINK}]}]
    color = [{"id": "custom.cellOptions", "value": {"type": "color-text"}},
             {"id": "mappings", "value": [{"type": "value", "options": {
                 "success": {"color": "green"}, "failure": {"color": "red"},
                 "aborted": {"color": "orange"}, "superseded": {"color": "yellow"}}}]}]
    return {"defaults": {"custom": {"filterable": True}},
            "overrides": [
                {"matcher": {"id": "byName", "options": "trace_id"},
                 "properties": link("Show this trace and its logs")},
                {"matcher": {"id": "byName", "options": "registration_trace"},
                 "properties": link("Show the linked registration trace")},
                {"matcher": {"id": "byName", "options": "outcome"}, "properties": color},
                {"matcher": {"id": "byName", "options": "smf_outcome"}, "properties": color},
                {"matcher": {"id": "byName", "options": "duration_ms"},
                 "properties": [{"id": "unit", "value": "ms"}]}]}


y = 0
panels.append({"id": nid(), "type": "text", "title": "",
               "gridPos": {"x": 0, "y": y, "w": 24, "h": 5},
               "options": {"mode": "markdown", "content": """
**Open5GS OTel PoC - traces and logs.** Click a `trace_id` in one of the tables: the
*Selected trace* section below shows that trace and every AMF/SMF log line with the same `trace_id`.

* **Registration traces**: one per UE registration, AMF only (`amf.registration`): authentication
  (AUSF), UDM registration and subscription data, PCF access policy, Registration Accept/Complete.
* **PDU session traces**: one per session attempt, AMF → SMF (`amf.pdu_session.establishment` with
  `smf.pdu_session.establishment` as child): UDM session data, PCF session policy, PFCP to the UPF,
  N1N2 to the gNB. `outcome` is the AMF view, `smf_outcome` what the SMF reported.
  `registration_trace` jumps to the registration the session belongs to (span link).
"""}})
y += 5

panels.append({"id": nid(), "type": "row", "title": "Trace overview (filter: outcome = ${outcome})",
               "collapsed": False, "gridPos": {"x": 0, "y": y, "w": 24, "h": 1}, "panels": []})
y += 1
panels.append({"id": nid(), "type": "table",
               "title": "Recent registration traces (click trace_id)",
               "description": "Root span amf.registration: one per Registration Request.",
               "datasource": DS, "gridPos": {"x": 0, "y": y, "w": 24, "h": 9},
               "targets": [target(REGISTRATIONS_SQL, "table", 1)],
               "fieldConfig": table_overrides()})
y += 9
panels.append({"id": nid(), "type": "table",
               "title": "Recent PDU session traces (click trace_id / registration_trace)",
               "description": "Root span amf.pdu_session.establishment: one per Create SM "
                              "Context; SMF columns from the SMF span of the same trace.",
               "datasource": DS, "gridPos": {"x": 0, "y": y, "w": 24, "h": 10},
               "targets": [target(SESSIONS_SQL, "table", 1)],
               "fieldConfig": table_overrides()})
y += 10

panels.append({"id": nid(), "type": "row", "title": "Selected trace (${trace_id})",
               "collapsed": False, "gridPos": {"x": 0, "y": y, "w": 24, "h": 1}, "panels": []})
y += 1
panels.append({"id": nid(), "type": "traces", "title": "Trace",
               "description": "Procedure span with its SBI/PFCP request spans. Expand a span "
                              "for attributes, events and the References (span link).",
               "datasource": DS, "gridPos": {"x": 0, "y": y, "w": 24, "h": 20},
               "targets": [target(TRACE_SQL, "traces", 3)]})
y += 20
panels.append({"id": nid(), "type": "logs", "title": "Logs of the selected trace (AMF/SMF, matched by TraceId)",
               "datasource": DS, "gridPos": {"x": 0, "y": y, "w": 24, "h": 18},
               "targets": [target(LOGS_SQL, "logs", 2)],
               "options": {"showTime": True, "wrapLogMessage": True,
                           "sortOrder": "Ascending", "enableLogDetails": True,
                           "dedupStrategy": "none"}})

outcomes = ["all", "success", "failure", "aborted", "superseded"]
variables = [
    {"type": "custom", "name": "outcome", "label": "Outcome",
     "query": ",".join(outcomes),
     "current": {"text": "all", "value": "all"},
     "options": [{"text": o, "value": o, "selected": o == "all"} for o in outcomes]},
    {"type": "textbox", "name": "trace_id", "label": "Trace ID", "query": "",
     "current": {"text": "", "value": ""},
     "description": "Set by clicking a trace_id in one of the tables"},
]

dashboard = {
    "uid": UID,
    "title": "Otel Poc - Trace and Logs - Light",
    "tags": ["open5gs", "opentelemetry", "poc"],
    "timezone": "browser",
    "schemaVersion": 39,
    "editable": True,
    "refresh": "30s",
    "time": {"from": "now-6h", "to": "now"},
    "templating": {"list": variables},
    "panels": panels,
}

with open(sys.argv[1], "w") as f:
    json.dump(dashboard, f, indent=2)
print("panels:", len(panels))
