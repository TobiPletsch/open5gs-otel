# 8. Design: per-request spans and linked traces (AMF + SMF only)

**Status: investigation / proposal. Not implemented.**

Code references point to the `otel-poc` branch (base v2.8.0-364).

## 8.1 Goal and scope

Requirements:

* **Two traces per UE attach, linked by reference, not merged:**
  1. a **registration trace**, rooted in the AMF;
  2. a **PDU session trace**, rooted in the AMF and continued in the SMF. It
     carries an OTel **span link** to the registration span.
* **Per-request detail inside each trace.** Every SBI request that AMF or SMF
  sends becomes a child span with its own duration, and so does every PFCP
  request the SMF sends.
* **Only AMF and SMF are changed.** AUSF, UDM, UDR, PCF, NRF, SCP and UPF stay
  untouched. They appear as the *remote end* of AMF/SMF client spans (duration
  = network + SCP + remote processing), not with spans of their own.

## 8.2 Target picture

Registration trace:

```
amf.registration                                         [AMF, INTERNAL] ███████████████████  130 ms
 ├ POST nausf-auth /ue-authentications                   [AMF, CLIENT]   ████                   via SCP → AUSF
 ├ PUT  nausf-auth /ue-authentications/{id}/5g-aka-confirmation [CLIENT]     ███
 ├ PUT  nudm-uecm /{supi}/registrations/amf-3gpp-access  [CLIENT]               ██
 ├ GET  nudm-sdm /{supi}                                 [CLIENT]                 ██
 ├ POST nudm-sdm /{supi}/sdm-subscriptions               [CLIENT]                  █
 ├ POST npcf-am-policy-control /policies                 [CLIENT]                   ██
 └ events: authentication_request_sent, security_mode_command_sent, registration_accept_sent, registration_complete_received
```

PDU session trace (new root in the AMF, linked to the registration):

```
amf.pdu_session.establishment                           [AMF, INTERNAL]  ████████████████████████████████  5030 ms
 │  link → amf.registration (trace 17ba…, span 2b5e…)
 ├ POST nsmf-pdusession /sm-contexts                     [AMF, CLIENT]    █
 │  └ smf.pdu_session.establishment                      [SMF, SERVER]    ██████████████████████████████
 │     ├ GET  nudm-sdm /{supi}/sm-data                   [SMF, CLIENT]    █
 │     ├ POST npcf-smpolicycontrol /sm-policies          [SMF, CLIENT]     █
 │     ├ PFCP Session Establishment                      [SMF, CLIENT]      █
 │     └ POST namf-comm /ue-contexts/{supi}/n1-n2-messages [SMF, CLIENT]     █
 │        └ POST namf-comm .../n1-n2-messages            [AMF, SERVER]       █   (NGAP setup to gNB as event)
 ├ POST nsmf-pdusession /sm-contexts/{ref}/modify        [AMF, CLIENT]        ███████████████████████████
 │  └ POST nsmf-pdusession .../modify                    [SMF, SERVER]        ███████████████████████████
 │     ├ PFCP Session Modification                       [SMF, CLIENT]        █████████████████████████ 5000 ms
 │     │    events: retransmit #1 (+2.5 s), retransmit #2 (+5 s)
 │     └ PUT nudm-uecm /{supi}/registrations/smf-registrations/{psi} [SMF, CLIENT] █
```

With this, the 5 s from [07 §7.3](07-registration-walkthrough.md#73-how-to-read-a-trace-from-the-poc)
becomes a single long "PFCP Session Modification" bar with retransmission
events, instead of a gap between two events.

## 8.3 Findings from the code investigation

| Question | Finding | Consequence |
|---|---|---|
| Is there one place where AMF/SMF send SBI requests? | Yes. All go through `ogs_sbi_xact_add()` (`lib/sbi/context.c`), then `ogs_sbi_discover_and_send()`. The xact holds the request (`xact->request->http.headers`) and is freed in `ogs_sbi_xact_remove()`. The AMF wrappers are `amf_ue_sbi_discover_and_send()` and `amf_sess_sbi_discover_and_send()`; the SMF wrapper is `smf_sbi_discover_and_send()`. | A CLIENT span can be created when the xact is created (the procedure span is current at that moment) and stored on the xact. The `traceparent` header goes into `xact->request->http.headers` via `ogs_sbi_header_set()`. |
| Where do responses arrive? | `OGS_EVENT_SBI_CLIENT` in `amf-sm.c` / `smf-sm.c` looks up the xact by ID (`ogs_sbi_xact_find_by_id`), handles the response and removes the xact. Timeouts arrive as `OGS_TIMER_SBI_CLIENT_WAIT`. | End the client span there with `http.response.status_code`. As a safety net, `ogs_sbi_xact_remove()` ends a still-open span (no leaks). |
| Does the SCP keep a `traceparent` header? | Yes. `copy_request()` in `src/scp/sbi-path.c` copies **all** headers except Target-apiRoot, Discovery-* and the pseudo-headers. | **No SCP change needed.** AMF ↔ SMF propagation works through the SCP. |
| Can the receiving NF read the header? | Yes. The nghttp2 server stores request headers in `request->http.headers`. The SMF sees the request in `smf-sm.c` (`OGS_EVENT_SBI_SERVER`, `e->h.sbi.request`), and the AMF in `amf-sm.c`. Lookup with `ogs_sbi_header_get()`. | SERVER spans in SMF (`sm-contexts`, `modify`) and AMF (`n1-n2-messages`) get the caller's span as parent. |
| Where does the AMF start a PDU session? | `gmm-handler.c`, UL NAS Transport with a PDU Session Establishment Request, state `AMF_CREATE_SM_CONTEXT_NO_STATE` (≈ l. 1670), calls `amf_sess_sbi_discover_and_send(…create_sm_context…)`. The session object is `amf_sess_t`. | New span `amf.pdu_session.establishment` stored on `amf_sess_t`, started there. |
| Where does it end in the AMF? | `amf_nsmf_pdusession_handle_update_sm_context()` (`nsmf-handler.c`), state `AMF_UPDATE_SM_CONTEXT_ACTIVATED`: the SMF's 204 after the modify. Failures: create-SM-context error status, N1N2 transfer with reject only, session removal. | Success and failure points are known. |
| What does the link point to? | The registration span has ended by then, but its IDs are still known. | Keep `trace_id` and `span_id` of the last registration on `amf_ue_t` (two short strings). Pass them as a span link when the session span starts. |
| PFCP | Requests go through `ogs_pfcp_xact_local_create()`. Retransmission happens in `lib/pfcp/xact.c` `response_timeout()`, logged at DEBUG only. Default timer 10 s / (3+1) = **2.5 s** per retry, which matches the observed 2.5 s / 5 s steps. | CLIENT span per PFCP request in the SMF, stored on the session per request type. Retransmission events need a small, optional callback in `lib/pfcp/xact.c` (`response_timeout()` → "retransmit #n"). |
| opentelemetry-cpp support | v1.16 supports explicit span kinds, remote parents (`SpanContext(..., is_remote=true)` in `StartSpanOptions.parent`) and links (`StartSpan(name, attributes, links, options)`). The ClickHouse exporter writes links to `otel_traces.Links.*`. | No library upgrade needed. |
| Beyla (eBPF) | It already records about 25k SBI HTTP spans per hour in `open5gs-ns`, all without a parent. Its client spans for AMF/SMF would duplicate the new ones. | Exclude `open5gs-amf`/`open5gs-smf` from Beyla (or the whole namespace) once this is live. That change is in the `dta9-workloads` repo. |

## 8.4 Proposed changes

### `lib/otel` (extend the C API)

```c
typedef enum { OGS_OTEL_KIND_INTERNAL, OGS_OTEL_KIND_SERVER,
               OGS_OTEL_KIND_CLIENT } ogs_otel_kind_e;

/* child of `parent`; parent NULL = root */
ogs_otel_span_t *ogs_otel_span_start_child(const char *name, ogs_otel_kind_e,
        ogs_otel_span_t *parent);
/* child of a remote parent given as W3C traceparent; invalid/NULL = root */
ogs_otel_span_t *ogs_otel_span_start_remote(const char *name, ogs_otel_kind_e,
        const char *traceparent);
/* new root span with a link to another (ended) span; links can only be set at start */
ogs_otel_span_t *ogs_otel_span_start_linked(const char *name,
        const char *link_trace_id, const char *link_span_id);
/* "00-<trace>-<span>-01" into buf (56 bytes) */
char *ogs_otel_span_traceparent(ogs_otel_span_t *span, char *buf);
ogs_otel_span_t *ogs_otel_span_current(void);
```

A no-op version goes in `ogs-otel-void.c`, as before.

### AMF

1. **SBI client spans.** In `amf_ue_sbi_discover_and_send()` and
   `amf_sess_sbi_discover_and_send()`: if a procedure span is current, start a
   CLIENT child span named `"<METHOD> <service> /<resource template>"` and set
   `traceparent`.
   * Attributes: `http.request.method`, `5gs.sbi.service`, `5gs.sbi.target_nf`
     and, at the end, `http.response.status_code`. IDs (SUPI, context refs) in
     the path are replaced by `{supi}`/`{id}`.
   * The span is stored on the xact: one new pointer field in `ogs_sbi_xact_t`
     (`lib/sbi/context.h`), ended in `amf-sm.c` on response or timeout. A
     fallback end in `ogs_sbi_xact_remove()` prevents leaks.
2. **`amf.pdu_session.establishment`** on `amf_sess_t`, linked to the last
   registration span. Start, success and failure points are as in 8.3.
3. **SERVER span** for `POST /namf-comm/.../n1-n2-messages` from the SMF, as a
   child of the incoming `traceparent`. Event `ngap_pdu_session_setup_sent`.

### SMF

1. **`smf.pdu_session.establishment`** becomes a **SERVER** span, child of the
   AMF's `sm-contexts` client span (taken from `traceparent`). Its name, events
   and attributes stay the same, so the dashboard queries keep working.
2. **SERVER span** for `POST /sm-contexts/{ref}/modify`, child of the AMF's
   modify client span.
3. **SBI client spans** in `smf_sbi_discover_and_send()`, as in the AMF.
4. **PFCP client spans** for Session Establishment and Session Modification:
   * started in `pfcp-path.c`, ended in `n4-handler.c` or `sess_5gc_timeout()`;
   * retransmission events via a hook in `lib/pfcp/xact.c`;
   * attributes `5gs.pfcp.message`, `5gs.pfcp.cause`.

### Unchanged

The log correlation stays as it is. Log lines now carry the ID of the
innermost current span (client or server span), and the `trace_id` is the
same within a trace, so the dashboard's log panel needs no change.

## 8.5 Dashboard changes

* **Trace panel:** add `references` (links) to the query so Grafana shows
  "linked trace" in the span details:
  ```sql
  arrayMap((t, s) -> map('traceID', t, 'spanID', s), Links.TraceId, Links.SpanId) AS references
  ```
* **"Recent PDU session traces":** filter on the new root
  `amf.pdu_session.establishment`. Add a column `registration_trace` with a
  data link that opens the linked registration trace.
* **New panel "SBI / PFCP request latency":** p50/p95/max per CLIENT span name
  for AMF and SMF. This shows which peer is slow without instrumenting the
  peers.

## 8.6 Effort and risks

| Item | Estimate |
|---|---|
| `lib/otel` API + no-op | ~150 lines |
| `lib/sbi` (span pointer on xact + fallback end) | ~15 lines |
| `lib/pfcp` (optional retransmit callback) | ~15 lines |
| AMF (client spans, session span, N1N2 server span) | ~200 lines |
| SMF (server spans, client spans, PFCP spans) | ~200 lines |
| Dashboard generator + docs | ~100 lines |
| Build, rollout (`restart-core.sh`), verification with 10 UEs | as before |

Risks and open points:

* **`lib/sbi` and `lib/pfcp` are shared by all NFs.** The changes there are
  passive: a pointer and an optional callback. Other NFs behave exactly as
  before.
* **Asynchronous ordering.** NAS/NGAP events can interleave with SBI responses.
  The rule "activate the procedure span before each dispatch, clear after each
  event" stays, and client spans are found through the xact, not through
  "current span".
* **NRF discovery.** A request that first needs NF discovery
  (`client_discover_cb`) shows discovery time inside the client span. An event
  `nf_discovery_done` can mark it.
* **Span volume.** About 6 extra spans per registration and about 8 per
  session. That is negligible for 10 UEs.
* **The remote ends stay black boxes.** A slow "POST nausf-auth …" span says
  that AUSF (or SCP/UDM behind it) is slow, not why. Instrumenting more NFs
  later reuses exactly the same API. The SERVER-side code in SMF/AMF is the
  template for them.
