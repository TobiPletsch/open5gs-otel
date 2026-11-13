#!/usr/bin/env python3
"""Generates the Grafana dashboard "Otel Poc - Trace and Logs - Light".

  [DS_UID=<datasource uid>] gen_dashboard_light.py <out.json>

Traces and logs only:
  * Three blocks side by side, per registration (amf.registration):
    all attempts | successful (a PDU session was established, i.e. a linked
    amf.pdu_session.establishment span ended with success) | failing.
  * UE search (variable "ue" in the top bar): IMSI or ue_ref. Filters the
    three blocks and lists all registration and PDU session traces of the UE.
  * Clicking a trace selects it: one trace panel + its AMF/SMF logs.

Traces only contain the pseudonym 5gs.ue.ref = first 16 hex of
SHA-256(salt || SUPI). The search computes it from the IMSI in ClickHouse,
so it only works with the salt the NFs use (UE_REF_SALT, default of the PoC).
"""
import json
import os
import sys

DS = {"type": "grafana-clickhouse-datasource",
      "uid": os.environ.get("DS_UID", "ClickHouse")}
NS = "open5gs-ns"
UID = "open5gs-otel-poc-light"
UE_REF_SALT = os.environ.get("UE_REF_SALT", "open5gs-otel-poc")
TRACE_LINK = ("/d/" + UID + "/otel-poc-trace-and-logs-light?${__url_time_range}"
              "&var-ue=${ue}&var-trace_id=${__value.raw}")

UE = "${ue:sqlstring}"
# Empty search = all UEs. Accepts "imsi-<digits>", "<digits>" or a ue_ref.
UE_FILTER = (
    f"({UE} = '' OR {{col}} IN (lower({UE}), lower(substring(hex(SHA256(concat("
    f"'{UE_REF_SALT}', if(startsWith({UE}, 'imsi-'), {UE}, concat('imsi-', {UE}))))), 1, 16))))")
ENV = f"ResourceAttributes['deployment.environment'] = '{NS}'"

# One row per registration, with the PDU sessions linked to it
REG_BASE = f"""
WITH sess AS (
  SELECT SpanAttributes['5gs.registration.trace_id'] AS reg_trace,
         count() AS sessions,
         countIf(SpanAttributes['5gs.outcome'] = 'success') AS ok,
         argMaxIf(TraceId, Timestamp, SpanAttributes['5gs.outcome'] = 'success') AS ok_session,
         argMax(TraceId, Timestamp) AS last_session,
         argMax(StatusMessage, Timestamp) AS last_reason
  FROM default.otel_traces
  WHERE $__timeFilter(Timestamp)
    AND SpanName = 'amf.pdu_session.establishment'
    AND {ENV}
  GROUP BY reg_trace)
SELECT r.Timestamp AS time,
       r.TraceId AS trace_id,
       r.SpanAttributes['5gs.ue.ref'] AS ue_ref,
       r.SpanAttributes['5gs.outcome'] AS outcome,
       r.StatusMessage AS reg_reason,
       s.sessions AS sessions,
       s.ok AS ok,
       s.ok_session AS ok_session,
       s.last_session AS last_session,
       s.last_reason AS last_reason
FROM default.otel_traces AS r
LEFT JOIN sess AS s ON s.reg_trace = r.TraceId
WHERE $__timeFilter(r.Timestamp)
  AND r.SpanName = 'amf.registration'
  AND r.{ENV}
  AND {UE_FILTER.replace("{col}", "r.SpanAttributes['5gs.ue.ref']")}"""

ATTEMPTS_SQL = f"""
SELECT time, trace_id, ue_ref, outcome, sessions AS pdu_sessions
FROM ({REG_BASE})
ORDER BY time DESC
LIMIT 500"""

SUCCESS_SQL = f"""
SELECT time, trace_id, ue_ref, ok_session AS session_trace
FROM ({REG_BASE})
WHERE outcome = 'success' AND ok > 0
ORDER BY time DESC
LIMIT 500"""

FAILING_SQL = f"""
SELECT time, trace_id, ue_ref,
       multiIf(outcome != 'success', concat('registration ', outcome,
                   if(reg_reason != '', concat(': ', reg_reason), '')),
               sessions = 0, 'no PDU session attempt',
               concat('PDU session failed: ', last_reason)) AS reason,
       nullIf(last_session, '') AS session_trace
FROM ({REG_BASE})
WHERE NOT (outcome = 'success' AND ok > 0)
ORDER BY time DESC
LIMIT 500"""

UE_TRACES_SQL = f"""
SELECT Timestamp AS time,
       if(SpanName = 'amf.registration', 'registration', 'PDU session') AS procedure,
       TraceId AS trace_id,
       SpanAttributes['5gs.outcome'] AS outcome,
       nullIf(StatusMessage, '') AS reason,
       round(Duration / 1e6, 1) AS duration_ms,
       SpanAttributes['5gs.ue.ref'] AS ue_ref,
       nullIf(SpanAttributes['5gs.registration.trace_id'], '') AS registration_trace,
       arrayStringConcat(Events.Name, ' > ') AS milestones
FROM default.otel_traces
WHERE $__timeFilter(Timestamp)
  AND SpanName IN ('amf.registration', 'amf.pdu_session.establishment')
  AND {ENV}
  AND {UE_FILTER.replace("{col}", "SpanAttributes['5gs.ue.ref']")}
ORDER BY Timestamp DESC
LIMIT 500"""

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


def link(title):
    return [{"id": "links", "value": [{"title": title, "url": TRACE_LINK}]}]


def table(title, sql, x, y, w, h, desc):
    color = [{"id": "custom.cellOptions", "value": {"type": "color-text"}},
             {"id": "mappings", "value": [{"type": "value", "options": {
                 "success": {"color": "green"}, "failure": {"color": "red"},
                 "aborted": {"color": "orange"}, "superseded": {"color": "yellow"}}}]}]
    panels.append({
        "id": nid(), "type": "table", "title": title, "description": desc,
        "datasource": DS, "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "targets": [target(sql, "table", 1)],
        "fieldConfig": {"defaults": {"custom": {"filterable": True}}, "overrides": [
            {"matcher": {"id": "byName", "options": "trace_id"},
             "properties": link("Show this trace and its logs")},
            {"matcher": {"id": "byName", "options": "session_trace"},
             "properties": link("Show the PDU session trace")},
            {"matcher": {"id": "byName", "options": "registration_trace"},
             "properties": link("Show the linked registration trace")},
            {"matcher": {"id": "byName", "options": "ue_ref"},
             "properties": [{"id": "links", "value": [{
                 "title": "Show all traces of this UE",
                 "url": "/d/" + UID + "/otel-poc-trace-and-logs-light?${__url_time_range}"
                        "&var-ue=${__value.raw}&var-trace_id=${trace_id}"}]}]},
            {"matcher": {"id": "byName", "options": "outcome"}, "properties": color},
            {"matcher": {"id": "byName", "options": "duration_ms"},
             "properties": [{"id": "unit", "value": "ms"}]}]}})


y = 0
panels.append({"id": nid(), "type": "text", "title": "",
               "gridPos": {"x": 0, "y": y, "w": 24, "h": 4},
               "options": {"mode": "markdown", "content": """
**Open5GS OTel PoC - traces and logs.** **UE search:** enter an IMSI (`999700000000001` or
`imsi-999700000000001`) or a `ue_ref` in the **UE** field at the top (empty = all UEs), or click a
`ue_ref` in any table. Click a `trace_id` / `session_trace` / `registration_trace` to show that trace
and its AMF/SMF logs at the bottom. Traces store no IMSI, only the pseudonym `ue_ref`; the search
computes it from the IMSI.
"""}})
y += 4

panels.append({"id": nid(), "type": "row", "title": "Registrations (UE: ${ue})",
               "collapsed": False, "gridPos": {"x": 0, "y": y, "w": 24, "h": 1}, "panels": []})
y += 1
table("Registration attempts", ATTEMPTS_SQL, 0, y, 8, 11,
      "Every Registration Request (amf.registration), any outcome. pdu_sessions = "
      "PDU session attempts linked to this registration.")
table("Successful (PDU session established)", SUCCESS_SQL, 8, y, 8, 11,
      "Registration succeeded and at least one linked PDU session ended with success "
      "(SMF: user plane active, registered at the UDM). session_trace = that session.")
table("Failing (no PDU session established)", FAILING_SQL, 16, y, 8, 11,
      "Registration failed, or it succeeded but no linked PDU session succeeded. "
      "session_trace = the last session attempt, if any.")
y += 11

panels.append({"id": nid(), "type": "row", "title": "Traces of UE: ${ue}",
               "collapsed": False, "gridPos": {"x": 0, "y": y, "w": 24, "h": 1}, "panels": []})
y += 1
table("All registration and PDU session traces (filtered by the UE search, newest first)",
      UE_TRACES_SQL, 0, y, 24, 10,
      "Root spans of both procedures. Use the column filters (funnel icon) to filter "
      "e.g. by procedure or outcome.")
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
panels.append({"id": nid(), "type": "logs",
               "title": "Logs of the selected trace (AMF/SMF, matched by TraceId)",
               "datasource": DS, "gridPos": {"x": 0, "y": y, "w": 24, "h": 18},
               "targets": [target(LOGS_SQL, "logs", 2)],
               "options": {"showTime": True, "wrapLogMessage": True,
                           "sortOrder": "Ascending", "enableLogDetails": True,
                           "dedupStrategy": "none"}})

variables = [
    {"type": "textbox", "name": "ue", "label": "UE (IMSI or ue_ref)", "query": "",
     "current": {"text": "", "value": ""},
     "description": "e.g. 999700000000001 or imsi-999700000000001 or a ue_ref; empty = all"},
    {"type": "textbox", "name": "trace_id", "label": "Trace ID", "query": "",
     "current": {"text": "", "value": ""},
     "description": "Set by clicking a trace in one of the tables"},
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
    # Shown in the top bar next to the variables: textbox variables have no
    # clear ("X") button, so these links reload the dashboard with empty values
    # (keepTime keeps the selected time range).
    "links": [
        {"type": "link", "title": "✕ Clear UE + trace", "icon": "dashboard",
         "url": "/d/" + UID + "/otel-poc-trace-and-logs-light?var-ue=&var-trace_id=",
         "keepTime": True, "includeVars": False, "targetBlank": False,
         "tooltip": "Reset the UE search and the selected trace", "tags": [],
         "asDropdown": False},
        {"type": "link", "title": "✕ Clear trace", "icon": "dashboard",
         "url": "/d/" + UID + "/otel-poc-trace-and-logs-light?var-ue=${ue}&var-trace_id=",
         "keepTime": True, "includeVars": False, "targetBlank": False,
         "tooltip": "Reset only the selected trace (keeps the UE search)", "tags": [],
         "asDropdown": False},
    ],
    "panels": panels,
}

with open(sys.argv[1], "w") as f:
    json.dump(dashboard, f, indent=2)
print("panels:", len(panels))
