#!/usr/bin/env python3
"""Generates the Grafana dashboard "Otel Poc - Trace and Logs - Light".

  [DS_UID=<datasource uid>] gen_dashboard_light.py <out.json>

Traces and logs only, for PDU session establishment:
  * Attempt - any PDU session trace (root amf.pdu_session.establishment)
  * Failure - traces whose SMF span reports outcome=failure
  * Success - traces whose SMF span reports outcome=success
Each section has a trace-ID dropdown (newest first, so the latest trace is
shown by default), a large trace panel and the logs of that trace.
"""
import json
import os
import sys

DS = {"type": "grafana-clickhouse-datasource",
      "uid": os.environ.get("DS_UID", "ClickHouse")}
NS = "open5gs-ns"
UID = "open5gs-otel-poc-light"

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
WHERE TraceId = '${%s}'"""

LOGS_SQL = """
SELECT Timestamp AS timestamp,
       Body AS body,
       SeverityText AS level,
       LogAttributes['k8s.container.name'] AS container,
       TraceId AS traceID,
       SpanId AS spanID
FROM default.otel_logs
WHERE $__timeFilter(Timestamp)
  AND TraceId = '${%s}'
ORDER BY Timestamp"""

# Variable queries: newest first; the first value is the default selection.
VAR_SQL = {
    "attempt_trace": f"""
SELECT TraceId FROM default.otel_traces
WHERE $__timeFilter(Timestamp)
  AND SpanName = 'amf.pdu_session.establishment'
  AND ResourceAttributes['deployment.environment'] = '{NS}'
ORDER BY Timestamp DESC LIMIT 200""",
    "failure_trace": f"""
SELECT TraceId FROM default.otel_traces
WHERE $__timeFilter(Timestamp)
  AND SpanName = 'smf.pdu_session.establishment'
  AND SpanAttributes['5gs.outcome'] = 'failure'
  AND ResourceAttributes['deployment.environment'] = '{NS}'
ORDER BY Timestamp DESC LIMIT 200""",
    "success_trace": f"""
SELECT TraceId FROM default.otel_traces
WHERE $__timeFilter(Timestamp)
  AND SpanName = 'smf.pdu_session.establishment'
  AND SpanAttributes['5gs.outcome'] = 'success'
  AND ResourceAttributes['deployment.environment'] = '{NS}'
ORDER BY Timestamp DESC LIMIT 200""",
}

SECTIONS = [
    ("attempt_trace", "PDU session establishment attempt",
     "Any PDU session establishment trace (root span amf.pdu_session.establishment, "
     "any outcome). Pick another attempt in the dropdown at the top."),
    ("failure_trace", "PDU session establishment failure (reported by the SMF)",
     "Traces whose smf.pdu_session.establishment span ended with outcome=failure "
     "(reject, N1N2 reject path, gNB setup failure, UDM error, PFCP failure)."),
    ("success_trace", "PDU session establishment success (reported by the SMF)",
     "Traces whose smf.pdu_session.establishment span ended with outcome=success "
     "(user plane active and SMF registered at the UDM)."),
]

panels = []
_id = [0]


def nid():
    _id[0] += 1
    return _id[0]


def target(sql, kind, fmt):
    return {"datasource": DS, "editorType": "sql", "format": fmt,
            "queryType": kind, "rawSql": sql.strip(), "refId": "A"}


y = 0
panels.append({"id": nid(), "type": "text", "title": "",
               "gridPos": {"x": 0, "y": y, "w": 24, "h": 3},
               "options": {"mode": "markdown", "content":
                   "**Open5GS OTel PoC - traces and logs only.** Each section shows one PDU "
                   "session establishment trace (AMF → SMF, with SBI/PFCP request spans) and "
                   "every AMF/SMF log line carrying the same `trace_id`. The newest trace of "
                   "the time range is selected by default; use the dropdowns at the top to "
                   "pick another one. Expand a span for attributes, events and the "
                   "*References* link to the registration trace."}})
y += 3

for var, title, desc in SECTIONS:
    panels.append({"id": nid(), "type": "row", "title": f"{title} - ${{{var}}}",
                   "collapsed": False, "gridPos": {"x": 0, "y": y, "w": 24, "h": 1},
                   "panels": []})
    y += 1
    panels.append({"id": nid(), "type": "traces", "title": f"Trace: {title}",
                   "description": desc, "datasource": DS,
                   "gridPos": {"x": 0, "y": y, "w": 24, "h": 18},
                   "targets": [target(TRACE_SQL % var, "traces", 3)]})
    y += 18
    panels.append({"id": nid(), "type": "logs", "title": f"Logs: {title}",
                   "datasource": DS,
                   "gridPos": {"x": 0, "y": y, "w": 24, "h": 16},
                   "targets": [target(LOGS_SQL % var, "logs", 2)],
                   "options": {"showTime": True, "wrapLogMessage": True,
                               "sortOrder": "Ascending", "enableLogDetails": True,
                               "dedupStrategy": "none"}})
    y += 16

variables = []
for var, label in (("attempt_trace", "Attempt"), ("failure_trace", "Failure"),
                   ("success_trace", "Success")):
    variables.append({
        "type": "query", "name": var, "label": label, "datasource": DS,
        "query": VAR_SQL[var].strip(), "definition": VAR_SQL[var].strip(),
        "refresh": 2,            # on time range change
        "sort": 0,               # keep the query order (newest first)
        "includeAll": False, "multi": False,
        "current": {}, "options": []})

dashboard = {
    "uid": UID,
    "title": "Otel Poc - Trace and Logs - Light",
    "tags": ["open5gs", "opentelemetry", "poc"],
    "timezone": "browser",
    "schemaVersion": 39,
    "editable": True,
    "time": {"from": "now-6h", "to": "now"},
    "templating": {"list": variables},
    "panels": panels,
}

with open(sys.argv[1], "w") as f:
    json.dump(dashboard, f, indent=2)
print("panels:", len(panels))
