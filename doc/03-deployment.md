# 3. Deployment

## 3.1 Where things run and which repo owns them

| Component | Cluster / namespace | Git repo (path) | Flux Kustomization |
|---|---|---|---|
| Open5GS core (all NFs) + UERANSIM gNB/UEs | pub-k8s, `open5gs-ns`, pinned to `k8s-worker3` | GitLab `tobias.pletsch/open5gs` (`open5gs/`, `ueransim/`), clone `~/tobi_projects/open5gs` on `pub-k8s-cp` | `open5gs-clab` |
| otel-agent, Kafka, otel-gateway, ClickHouse, Grafana | pub-k8s, `observability` | GitLab `dta9-workloads` (`observability/`) | `wl-observability` |
| Public Grafana (behind Authelia/NGINX) | pub-nuc, `observability` | GitHub `assurance-services` (`apps/observability/`), wired by `factory_infra/clusters/nuc-cluster/observability.yaml` | `observability` |
| Source + image build | `pub-k8s-w3` host | GitHub `TobiPletsch/open5gs-otel`, branch `otel-poc` | – (built manually, see [02](02-build.md)) |

All Kubernetes changes go through Git and Flux. Do not `kubectl apply` or
`kubectl edit` resources that Flux owns, because the next reconcile reverts
them.

## 3.2 Open5GS HelmRelease changes

These are in the deployment repo, `open5gs/open5gs-helmrelease.yaml`
(Gradiant `open5gs` chart). Excerpt:

```yaml
amf:
  image: &otel_image
    registry: docker.io
    repository: library/open5gs-otel
    tag: "9f0813191"            # = git short sha of the built commit
    pullPolicy: IfNotPresent    # image exists only in k8s-worker3's containerd
  nodeSelector: &otel_node
    kubernetes.io/hostname: k8s-worker3
  extraEnvVars: &otel_env
    - name: OTEL_EXPORTER_OTLP_ENDPOINT
      value: http://otel-agent.observability.svc.cluster.local:4318
    - name: OTEL_RESOURCE_ATTRIBUTES
      value: deployment.environment=open5gs-ns
  metrics:
    enabled: true               # AMF registration counters for the dashboard
smf:
  image: *otel_image
  nodeSelector: *otel_node
  extraEnvVars: *otel_env
  metrics: { enabled: true }
upf: { image: *otel_image, nodeSelector: *otel_node, ... }
ausf/bsf/nrf/nssf/pcf/scp/sepp/udm/udr: { image: *otel_image, nodeSelector: *otel_node }
```

Why each part is there:

* **`nodeSelector`.** The image is not in any registry. It is imported into
  `k8s-worker3`'s containerd only.
* **One image for all NFs.** This avoids mixing Open5GS versions.
* **`OTEL_EXPORTER_OTLP_ENDPOINT`.** Only the AMF and SMF get it, so only they
  export spans. The other NFs run the same binary with tracing off.
* **`deployment.environment=open5gs-ns`.** This resource attribute is what the
  dashboard filters on. It separates the PoC from the other Open5GS deployment
  in namespace `open5gs`.
* **`OGS_OTEL_UE_REF_SALT`.** Not set, so the default salt is used. Set it via
  `extraEnvVars` if `ue_ref` values should differ from other deployments.

UERANSIM (`ueransim/ueransim-helmrelease.yaml`, chart `ueransim-gnb` 0.2.6,
image 3.3.0) is unchanged: PLMN 999/70, slice SST 1 / SD `0x111111`, TAC 1, and
**10 UEs** (`imsi-999700000000001` … `…010`), each with one `internet` session.

## 3.3 Telemetry pipeline (`dta9-workloads/observability`)

```
AMF/SMF --OTLP/HTTP :4318--> otel-agent (DaemonSet, hostPort 4318)
AMF/SMF stdout --> /var/log/pods/open5gs-ns_*/... --filelog--> otel-agent
otel-agent --> Kafka topics otlp-traces / otlp-logs / otlp-metrics
otel-gateway <-- Kafka, + Prometheus scrape of open5gs-{amf,smf}-metrics.open5gs-ns:9090
otel-gateway --clickhouse exporter (ttl 72h)--> ClickHouse default.otel_traces / otel_logs / otel_metrics_*
```

PoC-specific additions to `observability/otel.yaml`:

| Change | Why |
|---|---|
| filelog `include: /var/log/pods/open5gs-ns_*/*/*.log` | Collect the PoC core's logs, not only namespace `open5gs` |
| processor **`transform/open5gs`** in the logs pipeline | Turns the text marker into real log/trace correlation (below) |
| scrape jobs `open5gs-ns-amf` and `open5gs-ns-smf` (15 s), label `namespace: open5gs-ns` | Existing Prometheus counters of the PoC core. The dashboard filters on `Attributes['namespace']` |
| `batch/metrics` (send_batch_size 250, max 500) in the agent's metrics pipeline | Bigger batches exceeded Kafka's 1 MB message limit. The endlessly retried batches filled the agent's memory, and `memory_limiter` then refused **all** signals |
| agent memory 128Mi request / 512Mi limit | Headroom for the above |
| gateway: `memory_limiter` in all pipelines, memory 256Mi request / 1Gi limit | After a restart the gateway consumed the whole Kafka backlog at once and was OOM-killed at 300Mi in a loop (2026-11-12), so nothing reached ClickHouse |
| ClickHouse memory 1Gi request / 4Gi limit (`observability/clickhouse.yaml`) | At 1500Mi ClickHouse was OOM-killed 26 times in 20 h (2026-11-13); dashboard queries failed with "Memory limit (total) exceeded" |

### `transform/open5gs`: from a log line to `TraceId`

These OTTL statements apply only when `k8s.namespace.name` matches `^open5gs`:

1. Strip ANSI colour codes from the body.
2. Parse `^<date> <time>: [<domain>] <LEVEL>: ` and set `severity_text` and
   `severity_number` (FATAL/ERROR/WARNING/INFO/DEBUG/TRACE).
3. Extract `\[trace_id=(?P<trace_id>[0-9a-f]{32}) span_id=(?P<span_id>[0-9a-f]{16})\]`.
4. `set(trace_id.string, …)` and `set(span_id.string, …)`. This fills the
   **OTel log record's own TraceId/SpanId fields**. The ClickHouse exporter
   writes those to the `otel_logs.TraceId` and `otel_logs.SpanId` columns.
5. Delete the temporary attributes.

From this point, logs and spans share the same `TraceId` column value in
ClickHouse. That is the whole correlation mechanism, explained further in
[05](05-grafana-dashboard.md#53-how-logs-and-traces-are-correlated).

### Grafana on pub-k8s

`observability/grafana.yaml` in `dta9-workloads` provisions the dashboard JSON
(`observability/dashboards/open5gs-otel-poc.json`, ConfigMap
`grafana-dashboard-open5gs-otel`) into the folder **"Open5GS OTel PoC"**. It
uses the local datasource uid `ClickHouse`. You can reach it via
`ssh -L 3000:192.168.2.183:30000 pub-k8s-cp`, then open http://localhost:3000.

## 3.4 Public Grafana on the NUC

The publicly reachable Grafana runs on the pub-nuc cluster and has no data of
its own for the PoC. It reads the **pub-k8s ClickHouse** over the lab network.

| Piece | Where |
|---|---|
| ClickHouse user `grafana_nuc`: `readonly=2`, database `default` only, max 60 s / 1 GB per query; only the SHA-256 hash is stored | `dta9-workloads/observability/clickhouse.yaml` (`users.d/grafana-nuc-user.xml`) |
| Service `clickhouse-nodeport`, NodePort **30901** → 9000 (native protocol) | same file |
| Password as a **SOPS-encrypted** Secret `clickhouse-pub-k8s-ro` (age recipients: admin key + NUC Flux key) | `assurance-services/apps/observability/clickhouse-pub-k8s-ro.sops.yaml`, rules in `assurance-services/.sops.yaml` |
| Flux decryption (`decryption: provider: sops, secretRef: sops-age`) | `factory_infra/clusters/nuc-cluster/observability.yaml` |
| Datasource "ClickHouse pub-k8s (OTel PoC)", uid `ClickHouse-pub-k8s`, `192.168.2.183:30901`, password from env `CLICKHOUSE_PUB_K8S_PASSWORD` (`envValueFrom` the Secret) | `assurance-services/apps/observability/grafana.yaml` |
| Dashboard, folder **"Open5GS-Otel-PoC"** (provider `open5gs-otel-poc`, ConfigMap `grafana-dashboard-open5gs-otel-poc` from a `configMapGenerator`) | `assurance-services/apps/observability/dashboards/open5gs-otel-poc.json` + `kustomization.yaml` |

The NUC copy of the dashboard is the same generated JSON with the datasource uid
`ClickHouse` replaced by `ClickHouse-pub-k8s` (generate it with
`DS_UID=ClickHouse-pub-k8s python3 gen_dashboard.py <out.json>`).

To change the password:

1. On the NUC control plane, `sops apps/observability/clickhouse-pub-k8s-ro.sops.yaml`
   (needs the admin age key).
2. Put the new SHA-256 hash in the pub-k8s `clickhouse.yaml`:
   `echo -n '<pw>' | sha256sum`.
3. Push both repos.

## 3.5 Restarting the core: `scripts/restart-core.sh`

This script is in the deployment repo (`~/tobi_projects/open5gs/scripts/` on
`pub-k8s-cp`).

```sh
ssh pub-k8s-cp
cd ~/tobi_projects/open5gs
scripts/restart-core.sh             # full ordered restart
scripts/restart-core.sh --ran-only  # only gNB, then UEs
```

Restarting all NFs at once, as happens after an image change, leaves stale
state. Order matters:

1. **NRF + SCP**, then 10 s pause.
2. **UPF**, then 10 s pause. The SMF must later associate via PFCP with the
   *new* UPF pod.
3. **AMF, SMF, AUSF, UDM, UDR, PCF, BSF, NSSF, SEPP**, then 15 s pause. These
   rediscover their peers through the fresh NRF. The script then prints the
   SMF's last `PFCP associated` line.
4. **gNB.** The script waits until the old gNB pod is gone, because the UEs
   resolve the gNB IP once at start, then pauses 10 s.
5. **UEs.** After 45 s it prints `registered: N, PDU sessions up: N, rejects: N`.
   Expected: `10, 10, 0`.

## 3.6 Rolling out a new image

1. Build and import the image on `pub-k8s-w3` (see [02](02-build.md)). Note the
   tag `open5gs-otel:<sha>`.
2. On `pub-k8s-cp`, in `~/tobi_projects/open5gs`, set `tag: "<sha>"` in
   `open5gs/open5gs-helmrelease.yaml` (anchor `&otel_image`; all NFs follow).
   Commit and push.
3. Run `flux reconcile kustomization open5gs-clab --with-source`. Helm upgrades
   all NF deployments.
4. Run `scripts/restart-core.sh` to bring the core back into a clean state.
5. Check in Grafana that new traces arrive (see [04](04-ue-registration.md)).

| Image tag | Commit content |
|---|---|
| `4f000c96d` | First version |
| `cce324e9e` | SMF span events fix |
| `3b67ed0d0` | SD as hex |
| `0bbfb0a95` | PFCP timeout log correlation |
| `e0be16cb5` | Per-request SBI/PFCP spans, linked session traces (first rollout) |
| `7e44b7c0c` | Span names for URI-addressed requests, SMF modify span parent |
| `9f0813191` | AMF server spans only with caller context (current) |
