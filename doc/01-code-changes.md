# 1. Code changes – what and why

The branch is `otel-poc`. Its base is upstream commit `39a8c771f` (main, v2.8.0-364).

| Commit | Content |
|---|---|
| `4f000c96d` | Manual tracing for AMF registration and SMF PDU session establishment, trace context in logs |
| `cce324e9e` | SMF: record `sm_context_created` where the 201 is actually sent, add `pcf_sm_policy_created` |
| `3b67ed0d0` | S-NSSAI SD as hex string, PoC README, Grafana dashboard generator |
| `0bbfb0a95` | SMF: correlate PFCP transaction timeout logs with the session span |
| `e0be16cb5`, `7e44b7c0c`, `9f0813191` | Per-request SBI/PFCP spans in AMF/SMF, `traceparent` propagation, linked PDU session traces (see [08](08-distributed-tracing.md)) |
| `7445859e8` | Dashboard: linked session traces, request latency, span links in the trace view |

Overall about 1,700 added lines in 38 files. More than half of that is new files:
`lib/otel/`, `src/{amf,smf}/otel.[ch]` and `docker/otel-poc/`. The changes to
existing Open5GS files are mostly single-line hooks.

## 1.1 Design principles

These come from the PoC brief:

* **Minimal and specific, not a framework.** Only the two procedures are traced.
  There is no generic auto-instrumentation of SBI, NGAP or PFCP.
* **Zero cost when disabled.** With `-Dotel=false` (the default), no-op stubs are
  built and there is no new dependency. With `-Dotel=true` but no
  `OTEL_EXPORTER_OTLP_ENDPOINT` set, every call is a no-op as well.
* **No subscriber identities in telemetry attributes.** UEs appear only as
  `5gs.ue.ref`, a salted SHA-256 pseudonym. Note that the existing log lines
  still contain SUPIs (see [06](06-limitations-and-troubleshooting.md)).
* **Keep the existing Prometheus metrics untouched.** Spans start and end at the
  same places as the existing counters wherever that is semantically correct,
  so the two can be compared.
* **OTLP/HTTP (protobuf) instead of gRPC.** This avoids pulling gRPC and abseil
  into the C build. The local otel-agent accepts both protocols.

## 1.2 Why spans live on the UE and session contexts

Open5GS NFs run a **single-threaded event loop**. A procedure such as
registration is not one function call. It is a sequence of events (NAS
messages, SBI responses from AUSF/UDM/PCF, NGAP messages, timers) dispatched
into per-UE (`gmm-sm.c`) and per-session (`gsm-sm.c`) finite state machines,
interleaved with events of other UEs.

So the open span is stored on the context object that lives as long as the
procedure:

* `amf_ue_t.registration_span` (`src/amf/context.h`)
* `smf_sess_t.establishment_span` (`src/smf/context.h`)

Before each per-UE or per-session FSM dispatch, that span is made *current*
(`ogs_otel_span_activate()`). After every event in the main loop, the current
span is cleared. This keeps the trace context of one UE from ever leaking into
log lines of the next event.

In the first version (up to `0bbfb0a95`) both procedure spans were independent
**root spans** (scope `open5gs`) without any context propagation. Since image
`9f0813191` the procedure spans have CLIENT child spans per SBI/PFCP request,
the SMF span is a child of the AMF's Create SM Context request (`traceparent`
header), and the PDU session trace is linked to the registration trace. The
mechanics are in the same "span on the context object" style and are described
in [08-distributed-tracing.md](08-distributed-tracing.md). Sections 1.3–1.6
describe the first version; 08 lists what changed on top of it.

## 1.3 New library `lib/otel`

| File | Purpose |
|---|---|
| `ogs-otel.h` | Plain-C API (no `ogs-core.h` include, so that the C++ file can use it) |
| `ogs-otel-sdk.cpp` | Implementation on opentelemetry-cpp, built with `-Dotel=true` |
| `ogs-otel-void.c` | No-op implementation, built otherwise |
| `ogs-otel.c` | Parts that need Open5GS core/crypt: span activation, finish, UE pseudonym |
| `meson.build` | Selects the SDK or the no-op file; links the static opentelemetry-cpp archives |

API (all functions accept a `NULL` span, so callers never have to check):

```c
int  ogs_otel_init(const char *service_name, const char *service_version);
void ogs_otel_final(void);                           /* flush + shutdown */
ogs_otel_span_t *ogs_otel_span_start(const char *name);   /* NULL if disabled */
void ogs_otel_span_set_str / ogs_otel_span_set_int / ogs_otel_span_add_event(...);
void ogs_otel_span_end(span, status, description);
const char *ogs_otel_span_trace_id(span), *ogs_otel_span_span_id(span);
void ogs_otel_span_activate(span);                   /* log context of this thread */
char *ogs_otel_ue_ref(const char *id, char *buf);    /* pseudonym */
void ogs_otel_span_finish(ogs_otel_span_t **span_ptr, outcome, description);
```

Implementation details and the reasons behind them:

* **Exporter.** The exporter is OTLP/HTTP with binary protobuf, behind a
  `BatchSpanProcessor`. Settings are queue 2048, batch 256 and delay 1 s.
  Export runs in the SDK's own thread, so the event loop never blocks on the
  network.
* **Configuration only via the standard OTel environment variables.**
  `OTEL_EXPORTER_OTLP_ENDPOINT` (tracing is off without it),
  `OTEL_SERVICE_NAME`, `OTEL_RESOURCE_ATTRIBUTES` and `OTEL_SDK_DISABLED`.
  Defaults are `service.name=open5gs-amf|open5gs-smf`,
  `service.version=<open5gs version>` and `service.namespace=open5gs`.
  Nothing was added to the Open5GS YAML config.
* **Every C++ entry point catches all exceptions.** An exception crossing into
  C code would be undefined behaviour.
* **`ogs_otel_span_finish()` is the one way a procedure ends.** It:
  1. sets `5gs.outcome`,
  2. sets status OK for `success` and ERROR with a description otherwise,
  3. writes one log line `[otel] procedure finished [outcome:…]` *with* the
     span's trace context, so every trace has at least one correlated log line,
  4. restores the previous log context,
  5. ends and frees the span,
  6. sets the pointer to `NULL`, which makes double-ending impossible.
* **`ogs_otel_ue_ref()`** returns the first 8 bytes of
  `SHA-256(salt || SUPI-or-SUCI)` as 16 hex characters. The salt comes from
  `OGS_OTEL_UE_REF_SALT` (default `open5gs-otel-poc`). The same UE gets the
  same `ue_ref` in AMF and SMF spans, so they can be joined without exposing
  the IMSI.

Build options added to `meson_options.txt`:

```
option('otel',        type: 'boolean', value: false)
option('otel_prefix', type: 'string',  value: '/usr/local')   # opentelemetry-cpp install prefix
```

`lib/meson.build` gains `subdir('otel')`. The AMF and SMF `meson.build` files
add `otel.c` and `libotel_dep`.

## 1.4 Trace context in logs (`lib/core/ogs-log.[ch]`)

New function `ogs_log_set_trace_context(trace_id, span_id)`. It stores
` [trace_id=<32 hex> span_id=<16 hex>]` in a **thread-local** buffer. While the
buffer is set, `ogs_log_vprintf()` appends it to every log line, after the
message and before the `(file:line)` suffix:

```
11/11 13:51:42.252: [gmm] INFO: [imsi-999700000000001] Registration complete [trace_id=17baf6b489d786d403b2eab23f9c320a span_id=2b5eeefc1fbc52da] (../src/amf/gmm-sm.c:3556)
11/11 13:51:42.253: [core] INFO: [otel] procedure finished [outcome:success] [trace_id=17baf6b489d786d403b2eab23f9c320a span_id=2b5eeefc1fbc52da] (../lib/otel/ogs-otel.c:63)
```

Why it works this way:

* **Text, not structured fields.** Open5GS logs go to stdout as plain text, and
  the cluster ships them with the collector's filelog receiver. A text marker
  needs no change to the logging backend. The collector parses it back into
  the OTel `TraceId`/`SpanId` fields (see [03](03-deployment.md)).
* **Thread-local.** The SBI client and other threads cannot pick up another
  thread's context.

## 1.5 AMF: `amf.registration`

The helpers are in `src/amf/otel.[ch]`.

| Function | Called from | Effect |
|---|---|---|
| `amf_otel_registration_start()` | `gmm-sm.c` `common_register_state()` on Registration Request, i.e. in DE-REGISTERED/REGISTERED. This is exactly where `fivegs_amffunction_rm_reg*req` is counted. | Ends an open span as `superseded`, starts a new one, sets `5gs.nf`, `5gs.procedure`, NGAP IDs, makes it current |
| `amf_otel_registration_restart()` | Registration Request received in the authentication, security-mode, initial-context-setup or exception state | Adds event `registration_request_restart` to the open span (or starts one) |
| `amf_otel_registration_update()` | After `gmm_handle_registration_request()` | `5gs.registration.type` (initial / mobility_updating / periodic_updating / emergency), `5gs.plmn_id`, `5gs.tac` |
| `amf_otel_registration_event()` | `nas-path.c`: `identity_request_sent`, `authentication_request_sent`, `security_mode_command_sent`, `registration_accept_sent`; `gmm-sm.c`: `registration_complete_received` | Span events, the "milestones" in Grafana |
| `amf_otel_registration_end()` | See the outcome table below | Sets `5gs.ue.ref`, `5gs.5gmm.cause`, `error.type`, then finishes the span |

Outcomes:

| Outcome | Where |
|---|---|
| `success` | Registration Complete received (`gmm_state_initial_context_setup`). Also Registration Accept sent **without** a new 5G-GUTI: then no Complete is expected (no T3550), so the Accept is terminal. |
| `failure` | Registration Reject (`5gs.5gmm.cause` = cause); Authentication Reject; T3550 expiry ("no Registration Complete"); GMM exception state; transaction failure inside the `AMF_RESTORE_CONTEXT_ON_FAILURE` macro, but only if the running NAS procedure is a Registration Request |
| `aborted` | `amf_ue_remove()`: the UE context disappears while a span is open |
| `superseded` | A new Registration Request starts a new span while one is open |

Context activation: `amf-sm.c` calls `ogs_otel_span_activate(amf_ue->registration_span)`
before the three `ogs_fsm_dispatch(&amf_ue->sm, e)` calls (SBI responses, NAS
messages, timers). `init.c` calls `ogs_otel_init()` at startup and
`ogs_otel_final()` at shutdown, and clears the context after every event.

## 1.6 SMF: `smf.pdu_session.establishment`

The helpers are in `src/smf/otel.[ch]`.

Span start: `nsmf-handler.c` `smf_nsmf_handle_create_sm_context()`, i.e. the
AMF's `POST /nsmf-pdusession/v1/sm-contexts`.

Span events, in their normal order:

| Event | Where |
|---|---|
| `sm_context_created` | `sbi-path.c` `smf_sbi_send_sm_context_created_data()`, where the `201 Created` to the AMF is sent and `pdusessioncreationsucc` is counted |
| `pcf_sm_policy_created` | `gsm-sm.c`, SM policy association created at the PCF |
| `pfcp_session_established` | `gsm-sm.c`, PFCP Session Establishment Response from the UPF |
| `n1n2_accept_sent` | `gsm-sm.c`, N1N2 message transfer (Accept + N2 resource setup) to the AMF |
| `n2_setup_response_received` | `gsm-sm.c`, PDU Session Resource Setup Response from the gNB |
| `pfcp_session_activated` | `n4-handler.c`, PFCP Session Modification Response: downlink path active |
| `udm_uecm_registered` | `smf-sm.c`, SMF registered at the UDM (UECM), followed by `success` |

Outcomes:

| Outcome | Where |
|---|---|
| `success` | UDM UECM registration done, the 204 to the AMF follows (`smf-sm.c`) |
| `failure` | `gsm_build_pdu_session_establishment_reject()` (`5gs.5gsm.cause`); entry into the N1N2-reject state (cause #38 network failure); `PDU_RES_SETUP_FAIL` from the gNB; UDM UECM registration error (`http.response.status_code`); GSM exception state. A rejected PFCP establishment additionally records `5gs.pfcp.cause`. |
| `aborted` | `smf_sess_remove()` while the span is open |
| `superseded` | New SM context create for the same session |

Attributes are set at the end, when they are complete: `5gs.pdu_session.id`,
`5gs.dnn`, `5gs.pdu_session.type`, `5gs.ssc_mode`, `5gs.s_nssai.sst`,
`5gs.s_nssai.sd` (hex, e.g. `111111`, as in the config), `5gs.plmn_id`,
`5gs.ue.ref`, `error.type`.

Context activation: `smf-sm.c` (16 dispatch sites: SBI, NAS/N2, GTP, Diameter),
`pfcp-sm.c` (PFCP responses and timers) and `pfcp-path.c` `sess_5gc_timeout()`.
The last one runs from the timer manager, outside any FSM dispatch. It is
needed so that "PFCP transaction timeout" lines also carry the trace ID
(commit `0bbfb0a95`).

**Why the end is "UDM registered" and not the `201`.** On this code base the
`201 Created` is sent right after the SM context is created, *before*
PCF/PFCP/N2 have happened. Ending the span there would make it meaningless.
The span therefore covers the whole procedure until the user plane is active.
Commit `cce324e9e` corrected the earlier assumption about where the `201` is
sent: in the non-roaming case it is sent from `smf_sbi_send_sm_context_created_data()`.

## 1.7 Packaging (`docker/otel-poc/`)

| File | Purpose |
|---|---|
| `Dockerfile.builder` | Ubuntu 24.04 + Open5GS build deps + opentelemetry-cpp v1.16.1 (static, PIC, OTLP/HTTP only) |
| `Dockerfile` | Runtime image, drop-in for `gradiant/open5gs`: `/opt/open5gs`, user `open5gs` (UID 999), `/entrypoint.sh` |
| `entrypoint.sh` | Same behaviour as Gradiant's entrypoint: creates `ogstun` for UPF/PGW, then `exec "$@"` |
| `build-image.sh` | Compile + install + image build (+ optional import into containerd) |
| `README.md` | Short version of this documentation |
| `grafana/gen_dashboard.py` | Generates the Grafana dashboard JSON (see [05](05-grafana-dashboard.md)) |

Which step of the registration and session each hook corresponds to is shown
in [07-registration-walkthrough.md](07-registration-walkthrough.md).

Build and deployment are covered in [02-build.md](02-build.md) and
[03-deployment.md](03-deployment.md).
