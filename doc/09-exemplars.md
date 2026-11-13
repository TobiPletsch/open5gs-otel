# 9. Metric ↔ trace correlation with exemplars: investigation and effort

**Status: investigation only, nothing implemented.** Code state: branch
`otel-poc` (base v2.8.0-364), opentelemetry-cpp v1.16.1, collector contrib
0.100.0, investigated 2026-11-13.

## 9.1 What exemplars are

An **exemplar** is a sample attached to a metric data point: a single
measurement plus the `trace_id`/`span_id` that was active when it was recorded.
Example in OpenMetrics text format:

```
fivegs_amffunction_rm_reginitreq_total 10 # {trace_id="17baf6b4…",span_id="2b5eeefc…"} 1 1762869102.123
```

Grafana then shows a dot on the counter graph, and clicking it opens that
trace. Today the dashboard can only compare counters and traces **by time**
(see [05 §5.4](05-grafana-dashboard.md#54-how-metrics-and-traces-are-correlated)).

## 9.2 Findings

| Layer | State | Consequence |
|---|---|---|
| **Open5GS counters** (`lib/metrics`) | `ogs_metrics_inst_add()` calls `prom_counter_add()` of the bundled **prometheus-client-c** (`subprojects/prometheus-client-c`, ~3,500 lines C, upstream archived) | Exemplars must be added in this vendored library |
| prometheus-client-c data model | `struct prom_metric_sample` = `{type, l_value (name+labels), _Atomic double r_value}`; no exemplar fields | New fields + locking (the scrape runs in the libmicrohttpd thread, increments in the NF main thread) |
| Exposition | `lib/metrics/prometheus/context.c` always answers `text/plain; version=0.0.4`, even to `Accept: application/openmetrics-text` (tested against the AMF). Exemplars exist **only in OpenMetrics**. | An OpenMetrics formatter is needed: counter samples as `<name>_total`, exemplar syntax, `# EOF`, plus content negotiation |
| Counter names | `fivegs_amffunction_rm_reginitreq` etc. have no `_total` | In OpenMetrics the sample name becomes `…_total`. The collector may then store a different `MetricName`, so dashboard queries need adapting. |
| Increment points vs. spans | All six PoC counters (AMF `rm_reginit{req,succ,fail}`, SMF `sm_pdusessioncreation{req,succ,fail}`) are incremented **while the matching span is current**: requests after the span start, success/failure before the span ends | The exemplar can take the trace ID of the current span **inside `ogs_metrics_inst_add()`**. No AMF/SMF code changes are needed. |
| **opentelemetry-cpp** (alternative route) | Exemplar reservoirs exist behind `WITH_METRICS_EXEMPLAR_PREVIEW` (default OFF), but **no exporter serializes exemplars**, and the SDK's point data does not even carry them to exporters. Checked in v1.16.1 **and** current main (v1.30+, 2026-11-13). | The OTel-SDK route needs a patched opentelemetry-cpp (SDK data model + OTLP exporter) |
| Collector (prometheus receiver 0.100) | Scrapes OpenMetrics when the target offers it and converts exemplars into OTLP exemplars | Expected to work without changes; **to be confirmed in a spike** |
| ClickHouse (`otel_metrics_sum`) | Already has `Exemplars.TraceId/SpanId/Value/TimeUnix/FilteredAttributes` columns, all empty today | No schema change |
| Grafana ClickHouse plugin | No native exemplar rendering in time series panels (that is a Prometheus-datasource feature) | Workaround: a second query in the same panel that returns exemplar points (time, value, `trace_id`), with a data link to the trace |

## 9.3 Options

### A. Exemplars on the existing Prometheus counters (library change)

Approach:

* **Exemplar storage.** Extend prometheus-client-c so each counter sample
  keeps its last exemplar (trace ID, span ID, value, timestamp) under a small
  mutex.
* **OpenMetrics output.** Add an OpenMetrics formatter and answer
  `Accept: application/openmetrics-text` with it. Keep 0.0.4 as the default,
  so existing scrapers don't change.
* **Automatic trace ID.** `ogs_metrics_inst_add()` attaches the current span
  (`lib/otel` active span) automatically. `lib/metrics` then depends on
  `lib/otel` (already built before it).
* **Collector.** Enable OpenMetrics scraping for the `open5gs-ns` jobs and
  check how the `_total` names arrive in ClickHouse.
* **Dashboard.** Counter panels get an exemplar overlay with trace links.

| Work item | Effort |
|---|---|
| prometheus-client-c: exemplar fields, locking, `prom_counter_add_exemplar()` | 1 d |
| OpenMetrics formatter (`_total`, exemplars, `# EOF`) + content negotiation in `lib/metrics/prometheus/context.c` | 1–1.5 d |
| `ogs_metrics_inst_add()` → exemplar from the current span; void build without OTel | 0.5 d |
| Collector: OpenMetrics scrape, verify exemplar conversion and metric names in ClickHouse | 0.5–1 d |
| Dashboard: counter panels with exemplar points + trace links, adapt metric names | 0.5–1 d |
| Build, rollout, verification with UERANSIM, docs | 1 d |
| **Total** | **≈ 5–6 person-days** |

Risks:

* **Maintaining a fork.** The vendored, archived prometheus-client-c becomes
  something we maintain ourselves. Upstreaming to Open5GS is unlikely without
  a broader metrics discussion.
* **OpenMetrics strictness.** Naming rules, `# EOF`, and label/exemplar length
  limits (the exemplar label set is limited to 128 characters, which a trace ID
  plus span ID fits).
* **Exemplars are sampled.** Only the *last* exemplar per series is kept
  between scrapes (15 s). That is enough to jump from a spike to an example
  trace, but it is not a complete mapping.

### B. OpenTelemetry metrics SDK in AMF/SMF (OTel-native)

Approach: emit the PoC counters additionally through the opentelemetry-cpp
metrics SDK, with exemplars, over OTLP.

| Work item | Effort |
|---|---|
| Patch opentelemetry-cpp: carry exemplars in point data, collect them from the reservoirs, serialize them in the OTLP exporter; build with the preview flag | 4–6 d (+ risk: preview API changes between releases) |
| C shim for counters in `lib/otel`, hook into `ogs_metrics_inst_add()` (dual emit) or in AMF/SMF | 1.5 d |
| OTLP metrics pipeline, dashboard | 1 d |
| Build, rollout, verification, docs | 1 d |
| **Total** | **≈ 8–10 person-days**, plus a maintained fork of opentelemetry-cpp |

Not recommended now, because the upstream C++ SDK does not support exemplar
export yet. It becomes attractive once opentelemetry-cpp exports exemplars
upstream; the remaining effort would then be about 3 days.

### C. Metrics derived from spans (`spanmetrics` connector), no Open5GS change

The collector's `spanmetrics` connector (part of contrib 0.100) turns spans into
RED metrics: calls and a duration histogram per span name and selected
attributes such as `5gs.outcome`. It attaches **exemplars** natively. Those
metrics flow over OTLP to ClickHouse like everything else.

| Work item | Effort |
|---|---|
| Connector config in the otel-agent/gateway (`dta9-workloads`), dimensions `5gs.outcome`, `5gs.sbi.target_nf` | 0.5 d |
| Dashboard panels (rate/duration per procedure and outcome + exemplar points with trace links) | 0.5 d |
| **Total** | **≈ 1 person-day** |

Limitation: these are **new metrics computed from the traces**, not the
existing 3GPP-style Open5GS counters. They answer "show me a trace behind this
spike" for traced procedures, but they don't add exemplars to
`fivegs_*` counters.

## 9.4 Recommendation

1. **Start with C (≈ 1 day).** It gives clickable metric → trace correlation
   for registration, PDU session and every SBI/PFCP request without touching
   Open5GS.
2. **If the existing `fivegs_*` counters themselves must carry exemplars, do A
   (≈ 5–6 days).** Start with a half-day spike that confirms the collector's
   OpenMetrics/exemplar conversion and the resulting metric names in ClickHouse,
   before changing prometheus-client-c.
3. **Revisit B only when opentelemetry-cpp exports exemplars upstream.**
