# 8. Distributed tracing in AMF and SMF: per-request spans and linked traces

**Status: implemented and deployed.**

| Item | Value |
|---|---|
| Code commits | `e0be16cb5`, `7e44b7c0c`, `9f0813191` on `otel-poc` |
| Dashboard commit | `7445859e8` |
| Image | `open5gs-otel:9f0813191` |
| Verified | 2026-11-12 with 10 UEs |

Only AMF and SMF are instrumented. AUSF, UDM, UDR, PCF, NRF, SCP and UPF are
unchanged. They appear as the *remote end* of AMF/SMF request spans.

## 8.1 What you get

Every UE attach produces **two traces**:

1. **A registration trace.** The root is `amf.registration`, with one CLIENT
   span per SBI request the AMF sends during the registration.
2. **A PDU session trace.** The root is `amf.pdu_session.establishment` in the
   AMF, continued in the SMF. It carries a **span link** to the registration
   trace, and the same ID is also stored as the attribute
   `5gs.registration.trace_id`.

The traces are separate because registration and PDU session are separate
3GPP procedures. The link connects them without merging them.

Real registration trace (2026-11-12):

```
amf.registration                                           INTERNAL   0.0 ms  118.9 ms
├ POST nausf-auth/ue-authentications                       CLIENT     0.1 ms    5.2 ms  AUSF 201
├ PUT  nausf-auth/ue-authentications/{id}/5g-aka-confirmation        13.1 ms    3.7 ms  AUSF 200
├ PUT  nudm-uecm/{id}/registrations/amf-3gpp-access                  28.6 ms    2.4 ms  UDM  201
├ GET  nudm-sdm/{id}                                                 31.0 ms    2.8 ms  UDM  200
├ GET  nudm-sdm/{id}/ue-context-in-smf-data                          33.8 ms    1.0 ms  UDM  200
├ POST nudm-sdm/{id}/sdm-subscriptions                               34.9 ms    1.1 ms  UDM  201
└ POST npcf-am-policy-control/policies                               36.2 ms    4.0 ms  PCF  201
```

The gaps between the requests are the NAS round trips with the UE
(authentication, security mode). They are still visible as span events.

Real PDU session trace (2026-11-12, the slowest of the run):

```
amf.pdu_session.establishment                      [AMF INTERNAL]     0.0 ms  10048.9 ms   link → registration trace
├ POST nsmf-pdusession/sm-contexts                 [AMF CLIENT]       0.1 ms     21.2 ms
│ └ smf.pdu_session.establishment                  [SMF SERVER]       4.2 ms  10043.1 ms   (milestone events as before)
│   ├ GET  nudm-sdm/{id}/sm-data                   [SMF CLIENT]       5.2 ms     11.5 ms
│   ├ POST nudm-sdm/{id}/sdm-subscriptions         [SMF CLIENT]      16.8 ms      2.2 ms
│   ├ POST npcf-smpolicycontrol/sm-policies        [SMF CLIENT]      19.1 ms     15.8 ms
│   ├ PFCP Session Establishment                   [SMF CLIENT]      35.0 ms   5001.1 ms   events: pfcp_retransmission_1, _2
│   └ POST namf-comm/ue-contexts/{id}/n1-n2-messages [SMF CLIENT]  5036.5 ms      1.6 ms
│     └ POST namf-comm/ue-contexts/{id}/n1-n2-messages [AMF SERVER] 5037.5 ms   0.1 ms
└ POST nsmf-pdusession/sm-contexts/{id}/modify     [AMF CLIENT]    5040.2 ms   5008.6 ms
  └ POST nsmf-pdusession/sm-contexts/{id}/modify   [SMF SERVER]    5040.9 ms   5006.4 ms
    ├ PFCP Session Modification                    [SMF CLIENT]    5040.9 ms   5003.1 ms   events: pfcp_retransmission_1, _2
    └ PUT  nudm-uecm/{id}/registrations/smf-registrations/{id}   10044.1 ms      3.2 ms
```

What used to be "a 5 s gap between two events" is now named: **both PFCP
requests to the UPF needed two retransmissions** (2.5 s each) before the
UPF answered.

## 8.2 How the context travels

```
AMF                                           SCP            SMF
amf.pdu_session.establishment (root, link → registration)
 └ CLIENT "POST nsmf-pdusession/sm-contexts"
     header  traceparent: 00-<trace>-<client span>-01  ──▶ (forwards all headers) ──▶
                                                              SERVER smf.pdu_session.establishment
                                                                (parent = AMF client span)
                                                               └ CLIENT spans (UDM, PCF, PFCP, AMF)
     ◀── traceparent of the SMF's N1N2 CLIENT span ──────────────────────
 SERVER "POST namf-comm/.../n1-n2-messages" (parent = SMF client span)
```

* The **W3C `traceparent` header** carries the context, as
  `00-<32 hex trace id>-<16 hex parent span id>-01`.
* The **SCP needs no change**: its `copy_request()` forwards all headers except
  routing headers.
* The registration trace stays inside the AMF. AUSF/UDM/PCF receive the header
  but ignore it, because they are not instrumented.

## 8.3 Code changes

### `lib/otel`

| Function | Purpose |
|---|---|
| `ogs_otel_span_start_ex(name, kind, parent, traceparent, link_trace_id, link_span_id)` | Span with kind INTERNAL/SERVER/CLIENT. The parent is a local span, else the remote `traceparent`, else none (root). An optional span link. |
| `ogs_otel_span_traceparent(span, buf)` | Formats the W3C header value |
| `ogs_otel_set_incoming_traceparent()` / `ogs_otel_incoming_traceparent()` | The `traceparent` of the SBI request being processed. Thread-local, set per SBI server event, cleared after each event in the main loop. |

`ogs_otel_span_start()` (root INTERNAL) is now a wrapper around
`ogs_otel_span_start_ex()`. The SDK implementation parses `traceparent` itself
(version `00` only, all-zero IDs rejected) and passes links via
`Tracer::StartSpan(name, attributes, links, options)`.

### `lib/sbi` (shared, passive)

* **New fields.** `ogs_sbi_xact_t` gets `otel_span` and `otel_status`.
* **`ogs_sbi_xact_otel_start(xact, parent)`** creates a CLIENT child span for
  the transaction's request and adds the `traceparent` header. It is a no-op
  without a parent, so NFs or procedures that are not traced create nothing.
* **`ogs_sbi_xact_otel_status(xact, status)`** records the HTTP status of the
  response.
* **`ogs_sbi_xact_remove()`** ends the span:
  * `http.response.status_code` set, status ERROR for ≥ 400;
  * without a response (timeout or local failure), `error.type=no_response`.
* **Span names** are `"<METHOD> <service>/<resource template>"`. IDs (SUPI/SUCI,
  numeric refs, UUIDs) become `{id}` and the API version is dropped. This also
  works for requests addressed by full URI, such as `modify` and
  `5g-aka-confirmation`.
* **Attributes:** `http.request.method`, `url.template`, `5gs.sbi.service`,
  `5gs.sbi.target_nf`.

### `lib/pfcp` (shared, passive)

`ogs_pfcp_xact_retransmit_hook`, an optional function pointer, is called in
`response_timeout()` for every retransmission of a locally originated request.
It is NULL in every NF except the SMF.

### AMF

| Where | What |
|---|---|
| `sbi-path.c` `amf_ue_sbi_discover_and_send()` (+ 5G-EIR) | CLIENT span under `registration_span` |
| `sbi-path.c` `amf_sess_sbi_discover_and_send()` | CLIENT span under the session span, else under the registration span |
| `gmm-handler.c`, Create SM Context for a new session | `amf_otel_session_start()`: root span with a link to the last registration (its IDs are kept on `amf_ue_t` after the registration span ended) |
| `amf-sm.c` SBI server event | Remember the incoming `traceparent`. SERVER span around the N1N2 message transfer, only if the request carried a `traceparent`. |
| `amf-sm.c` SBI client event | Record the response status before the xact is removed; make the session span current for `nsmf-pdusession` responses |
| `nsmf-handler.c`, activating Update SM Context answered | Session span `success` (event `sm_context_activated`) or `failure` with the HTTP status |
| `amf-sm.c` Create SM Context failed; `namf-handler.c` N1N2 with reject only | `failure` |
| `context.c` `amf_sess_remove()` | `aborted` |

### SMF

| Where | What |
|---|---|
| `otel.c` `smf_otel_establishment_start()` | `smf.pdu_session.establishment` is now a **SERVER** span, child of the AMF's Create SM Context span. Name, events and attributes are unchanged, so existing queries keep working. |
| `ngap-path.c` `ngap_send_to_n2sm()` | SERVER span for the activating `modify`. It is started here because the SMF handles the N2 part in a **later, queued event**, when the request's `traceparent` is no longer current. |
| `sbi-path.c` both send paths | CLIENT spans under the modify span, else under the establishment span |
| `pfcp-path.c` establishment / all-PDR modification requests | PFCP CLIENT span (`5gs.pfcp.message`, `5gs.pfcp.xid`) |
| `n4-handler.c` responses; `sess_5gc_timeout()` | End the PFCP span with `5gs.pfcp.cause`, or `no_response` |
| `pfcp-path.c` retransmit hook | Events `pfcp_retransmission_<n>`, attribute `5gs.pfcp.retransmissions` |
| `smf-sm.c` | Remember the incoming `traceparent`; record SBI response status |
| establishment end / `smf_sess_remove()` | End the modify and PFCP spans that are still open |

## 8.4 Differences from the original design

| Design | Implementation | Why |
|---|---|---|
| Start the SMF modify span when the N2 setup response is processed | Started in `ngap_send_to_n2sm()` when the HTTP request arrives | The N2 processing runs in a queued event without the caller's context. The first build produced parentless modify spans. |
| AMF SERVER span for every N1N2 request | Only when the request carries a `traceparent` | During a restart storm, about 200 rejected sessions produced one-span orphan traces |
| `ogs_otel_span_start_child` / `_remote` / `_linked` | One function, `ogs_otel_span_start_ex()` | Smaller API |
| Exclude AMF/SMF from Beyla | **Not done yet** | See 8.6 |

## 8.5 Verification (2026-11-12, 10 UEs, after `scripts/restart-core.sh`)

* 10 registration traces, each with 7 client spans (AUSF ×2, UDM ×4, PCF ×1).
* 12 session traces (9 success, 3 failure). All 12 have a span link, and every
  link resolves to an existing `amf.registration` trace.
* The only root spans are `amf.registration` and `amf.pdu_session.establishment`.
  Every CLIENT/SERVER span has a parent.
* PFCP spans of about 2.5/5/7.5 s carry 1–3 retransmission events.
* All 12 dashboard queries (including the trace panel with `references`) return
  data through the public NUC Grafana.

## 8.6 Open points

* **Beyla duplicates.** Beyla (`dta9-workloads/observability/beyla.yaml`)
  instruments every process listening on port 7777, so AMF/SMF SBI calls also
  appear as parentless Beyla spans. Excluding them needs a Beyla config file
  (`discovery.services` / `exclude_services` by namespace and deployment), not
  just environment variables. This is untested and left for later.
* **Black-box peers.** The remote NFs remain black boxes. A slow
  `POST nausf-auth/...` says AUSF (or SCP/UDM behind it) is slow, not why.
  Instrumenting another NF needs the same steps as the SMF: init/final calls,
  remembering the incoming `traceparent`, and SERVER spans.
* **Roaming path not traced.** Sessions that go through NSSF selection first
  (roaming) start the AMF session span only when Create SM Context is sent, so
  the NSSF request is not in the trace. That path is not used in this lab.
* **Restart storms.** During `restart-core.sh` the old UEs retry their session
  every second while the core restarts. That produces many short `failure`
  traces (about 200 in 20 s). They are real, but they are noise.
* **Collector memory.** The first rollout pushed the otel-gateway (300 MiB
  limit) into an OOM loop while it consumed the Kafka backlog after a restart.
  The fix in `dta9-workloads`: a `memory_limiter` in the gateway pipelines and a
  1 GiB limit.

## 8.7 Useful queries

```sql
-- the registration trace linked from a session trace
SELECT SpanAttributes['5gs.registration.trace_id'] FROM otel_traces
WHERE TraceId = '<session trace id>' AND SpanName = 'amf.pdu_session.establishment';

-- slowest requests per peer in the last hour
SELECT ServiceName, SpanName, quantile(0.95)(Duration)/1e6 AS p95_ms, count()
FROM otel_traces
WHERE Timestamp > now() - INTERVAL 1 HOUR AND ScopeName = 'open5gs'
  AND SpanKind = 'SPAN_KIND_CLIENT'
GROUP BY ServiceName, SpanName ORDER BY p95_ms DESC;

-- PFCP requests that needed retransmissions
SELECT Timestamp, SpanName, SpanAttributes['5gs.pfcp.retransmissions'] AS n, Duration/1e6 AS ms
FROM otel_traces
WHERE SpanName LIKE 'PFCP%' AND SpanAttributes['5gs.pfcp.retransmissions'] != ''
ORDER BY Timestamp DESC;
```
