# 6. Limitations, findings and troubleshooting

## 6.1 Known limitations of the PoC

| Limitation | Why / impact |
|---|---|
| Only two procedures are traced | By design (minimal PoC). Deregistration, service request, handover and PDU session release have no spans. |
| AMF and SMF spans are separate traces | No W3C `traceparent` propagation over SBI. Join them via `5gs.ue.ref` and time. |
| Metrics ↔ traces only by time | prometheus-client-c supports no exemplars |
| Log correlation only while a span is current | Lines outside the per-UE/per-session dispatch carry no trace ID |
| Log lines still contain SUPI/SUCI | Only span attributes are pseudonymised (`5gs.ue.ref`). Changing existing log statements was out of scope. |
| Spans are exported only when they end | A hanging procedure becomes visible when it times out, when the context is removed (`aborted`), or at NF shutdown (`ogs_otel_final()` flushes) |
| `pdusessioncreationsucc` double count | Upstream behaviour on this base: incremented at 201 and after UDM registration |
| AMF fast path not counted in `rm_reg*succ` | Upstream behaviour. The trace counts it as success. |
| Image is local to `k8s-worker3` | No registry. All Open5GS pods are pinned to that node. |
| Telemetry retention 72 h | ClickHouse exporter `ttl: 72h` |

## 6.2 Findings from the PoC

* **PFCP retransmissions between SMF and UPF** show up as gaps of about 2.5 s
  or 5 s before `pfcp_session_established` or `pfcp_session_activated`. See the
  panel "Establishment latency per milestone". The corresponding
  `PFCP transaction timeout`/retransmission log lines are correlated with the
  session trace since commit `0bbfb0a95`. Without tracing, this latency was not
  visible in the metrics at all.
* **Where the SMF sends `201 Created`.** In the non-roaming case the 201 is sent
  from `smf_sbi_send_sm_context_created_data()`, early in the procedure. That
  is also where `pdusessioncreationsucc` is first counted.

## 6.3 Lab environment caveats (pub-k8s)

* **4 GB workers** can OOM when everything restarts at once.
* **Stale state after mass restarts:**
  * NFs keep peers discovered from deleted pods. Symptom: Registration Reject
    #90.
  * The SMF associates with a vanished UPF via the ClusterIP, or the PFCP UDP
    conntrack entry wedges after a UPF reschedule. Symptom: PDU Session
    Establishment Reject.
  * The UEs resolve the gNB IP once at start-up.

  Always use `scripts/restart-core.sh` (see
  [03](03-deployment.md#35-restarting-the-core-scriptsrestart-coresh)).

## 6.4 Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Dashboard shows logs but no traces | No procedure ran in the selected time range. Widen the range or trigger registrations or sessions ([04](04-ue-registration.md)). Traces older than 72 h are deleted. |
| No spans at all, AMF/SMF log has no `[otel] tracing enabled` on stderr at start | `OTEL_EXPORTER_OTLP_ENDPOINT` not set, `OTEL_SDK_DISABLED=true`, or an image built with `-Dotel=false` |
| Spans in ClickHouse, but the logs panel is empty for a trace | Check that `transform/open5gs` is in the agent's logs pipeline and that the filelog `include` covers `open5gs-ns`. In ClickHouse: `SELECT count() FROM otel_logs WHERE TraceId = '<id>'`. Also check the time range, because the logs query uses `$__timeFilter`. |
| Collector drops everything (`memory_limiter` refusals) | Metric batches over Kafka's 1 MB limit. Keep `batch/metrics` (max 500 data points) in the agent. |
| Pods `ErrImageNeverPulled` / `ImagePullBackOff` | The image tag is not imported on `k8s-worker3` (`sudo ctr -n k8s.io images ls \| grep open5gs-otel`), or the pod was scheduled on another node |
| Registration Reject #90 or PDU Session Establishment Reject after a rollout | Stale NF state, run `scripts/restart-core.sh` |
| Public Grafana: datasource "ClickHouse pub-k8s" fails | `kubectl -n observability get svc clickhouse-nodeport` on pub-k8s (NodePort 30901), and check that the user `grafana_nuc` exists: `SELECT name FROM system.users`. Check that Secret `clickhouse-pub-k8s-ro` is decrypted on the NUC (Flux `observability` Kustomization ready, `decryption` set). |
| `git push` / large uploads from lab hosts stall after ~64 KB | The lab uplink's path MTU is 1460 while the hosts use 1500, and no ICMP "fragmentation needed" comes back (PMTUD black hole). Fix: MSS clamping on the lab router, or a lower MTU on the hosts. Push from a machine with MTU ≤ 1460 as a workaround. |

## 6.5 Possible next steps

* Propagate `traceparent` over SBI (AMF → SMF), so a registration and its PDU
  session become one trace.
* Use OTel logs (OTLP) instead of text markers. Structured attributes would
  also allow removing SUPIs from the log body.
* Exemplars, by moving the counters to an OTel metrics SDK.
* Push the image to a registry and drop the `nodeSelector`.
