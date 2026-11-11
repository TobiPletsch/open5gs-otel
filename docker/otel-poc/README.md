# Open5GS – manual OpenTelemetry instrumentation (PoC)

Two procedures are traced with manually created spans; logs written while a
span is active carry its `trace_id`/`span_id`; the existing Prometheus metrics
are unchanged.

| Span | Starts | Ends (outcome) |
|---|---|---|
| `amf.registration` | Registration Request in DE-REGISTERED/REGISTERED (where `fivegs_amffunction_rm_reg*req` is counted) | Registration Complete, or Accept without new 5G-GUTI (`success`); Registration/Authentication Reject, T3550 expiry, transaction failure, GMM exception (`failure`); UE context removed (`aborted`); new Registration Request (`superseded`) |
| `smf.pdu_session.establishment` | `POST /nsmf-pdusession/v1/sm-contexts` | 204 to the AMF after PFCP activation and UDM UECM registration (`success`); PDU Session Establishment Reject, PDU Session Resource Setup failure, UDM registration error, GSM exception (`failure`); session removed (`aborted`) |

Both are root spans (`SpanKind INTERNAL`, scope `open5gs`). Milestones are span
events (`authentication_request_sent`, …, `pfcp_session_established`, …).
Attributes use a `5gs.*` namespace plus `error.type`; UEs appear only as
`5gs.ue.ref`, a salted SHA-256 pseudonym (salt: `OGS_OTEL_UE_REF_SALT`).

## Design

* `lib/otel` – C interface (`ogs-otel.h`) to opentelemetry-cpp, traces only.
  `-Dotel=true` builds `ogs-otel-sdk.cpp` (BatchSpanProcessor, OTLP/HTTP
  protobuf); otherwise `ogs-otel-void.c` (no-ops, no new dependencies).
* `lib/core/ogs-log.c` – `ogs_log_set_trace_context()`: while set (thread-local),
  every log line gets ` [trace_id=<32 hex> span_id=<16 hex>]` before the
  `(file:line)` suffix.
* `src/amf/otel.c`, `src/smf/otel.c` – span lifecycle per procedure. The span
  lives in `amf_ue_t.registration_span` / `smf_sess_t.establishment_span`
  because both procedures span many asynchronous events (NAS, SBI responses,
  PFCP, timers). The span is made current before each per-UE/per-session FSM
  dispatch and cleared after every event in the main loop.
* Spans are exported only once they end; a procedure that hangs shows up when
  the UE context/session is removed (`aborted`), at the latest at shutdown
  (`ogs_otel_final()` flushes).

## Configuration (environment)

| Variable | Example |
|---|---|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://otel-agent.observability.svc.cluster.local:4318` (tracing is off without it) |
| `OTEL_RESOURCE_ATTRIBUTES` | `deployment.environment=open5gs-ns` |
| `OTEL_SERVICE_NAME` | default `open5gs-amf` / `open5gs-smf` |
| `OTEL_SDK_DISABLED` | `true` disables tracing |
| `OGS_OTEL_UE_REF_SALT` | salt for `5gs.ue.ref` |

## Build

```sh
sudo docker build -f docker/otel-poc/Dockerfile.builder -t open5gs-otel-builder:latest docker/otel-poc
docker/otel-poc/build-image.sh --import     # -> open5gs-otel:<git-sha>, imported into containerd (k8s.io)
```

The runtime image is a drop-in for `gradiant/open5gs` (Helm charts
`gradiantcharts/open5gs`): `/opt/open5gs`, user `open5gs` (999), `/entrypoint.sh`.

## Collector and Grafana

* Logs: filelog receiver; `transform/open5gs` strips ANSI colours, maps the
  level to severity and moves `trace_id`/`span_id` from the text into the log
  record, so `otel_logs.TraceId` links logs to traces.
* Metrics: Prometheus receiver scrapes `open5gs-{amf,smf}-metrics:9090`.
* Traces: OTLP receiver (`:4318`). All three end up in ClickHouse.
* `grafana/gen_dashboard.py` generates the dashboard "Open5GS OTel PoC".

## Known limitations

* Metrics and traces correlate by time only (prometheus-client-c has no exemplars).
* `pdusessioncreationsucc` is incremented at the `201 Created` (before PCF/PFCP)
  and again after UDM registration on this code base, so it is not comparable
  1:1 with successful traces.
* The AMF fast path (Accept without new GUTI) is a successful trace but does not
  increment `rm_reg*succ`.
* Log lines are correlated only while a span is current (per-UE/per-session
  dispatch); NGAP/gNB-level lines before dispatch carry no trace context.
* Existing log lines still contain SUPIs; only span attributes are pseudonymous.
