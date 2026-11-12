# 4. Triggering UE registration, deregistration and PDU sessions

What the registration and the PDU session consist of is explained in
[07-registration-walkthrough.md](07-registration-walkthrough.md).

The UEs are simulated by UERANSIM in the pod
`deploy/ueransim-ueransim-gnb-ues`, namespace `open5gs-ns`. The pod runs
`nr-ue -n 10`, i.e. 10 UEs `imsi-999700000000001` … `imsi-999700000000010`.
At start-up each UE registers and establishes one PDU session (DNN `internet`,
SST 1, SD `0x111111`).

**Traces appear only when a procedure runs.** Once the UEs are registered and
their sessions are up, no new `amf.registration` or
`smf.pdu_session.establishment` spans are produced. The dashboard's time range
(default: last hour) then shows logs but no traces. Use one of the triggers
below, or widen the time range.

All commands run on `pub-k8s-cp`, or anywhere with a kubeconfig for pub-k8s:

```sh
NS=open5gs-ns
UE=deploy/ueransim-ueransim-gnb-ues
```

## 4.1 Inspecting the UEs (read-only)

```sh
kubectl -n $NS exec $UE -- nr-cli --dump                     # list UE nodes
kubectl -n $NS exec $UE -- nr-cli imsi-999700000000001 -e status
kubectl -n $NS exec $UE -- nr-cli imsi-999700000000001 -e ps-list
kubectl -n $NS exec $UE -- nr-cli imsi-999700000000001 -e commands   # all commands
```

A registered UE shows `rm-state: RM-REGISTERED` and
`mm-state: MM-REGISTERED/NORMAL-SERVICE`.

## 4.2 Re-registering all 10 UEs (simplest, recommended)

Restart the UE pod. Every UE then performs a fresh initial registration
followed by a PDU session establishment, which gives 10 + 10 new traces.

```sh
kubectl -n $NS rollout restart $UE
kubectl -n $NS rollout status  $UE
sleep 45
kubectl -n $NS logs $UE | grep -cE 'Registration is successful'   # expect 10
kubectl -n $NS logs $UE | grep -c  'TUN interface'                # expect 10 (sessions up)
```

The old UE processes disappear without deregistering, so the AMF still has
their contexts. The new Registration Requests are handled normally, as initial
registrations with SUCI.

If the gNB has also been restarted, or registrations fail, use the ordered
restart instead: `scripts/restart-core.sh --ran-only`, or without the flag for
the whole core (see [03](03-deployment.md#35-restarting-the-core-scriptsrestart-coresh)).

## 4.3 Deregistering / re-registering a single UE

```sh
# UE-initiated deregistration
kubectl -n $NS exec $UE -- nr-cli imsi-999700000000001 -e "deregister normal"
kubectl -n $NS exec $UE -- nr-cli imsi-999700000000001 -e status
```

The `deregister` modes in UERANSIM are:

| Mode | Effect |
|---|---|
| `normal` | Deregistration Request (normal). The UE goes to MM-DEREGISTERED and, while it has coverage, normally starts a new initial registration on its own. |
| `switch-off` | Deregistration with the switch-off flag. The UE instance stops and only comes back with a pod restart (4.2). |
| `disable-5g` | Deregisters and stays deregistered (5G disabled). |
| `remove-sim` | Deregisters as if the USIM were removed. It stays deregistered. |

After a `normal` deregistration, check the `status` output again. If the UE
stays in `MM-DEREGISTERED`, restart the UE pod (4.2) to bring it back. These
UERANSIM behaviours have not been verified in this lab, so check `status`
before relying on them in a demo.

What you see in telemetry:

* **Deregistration itself is not traced.** Only registration is instrumented.
  The AMF's deregistration log lines therefore carry **no** `trace_id`. They are
  in `otel_logs` (Grafana Explore), but not reachable via the trace link.
* The following registration produces a new `amf.registration` trace.
* The UE's PDU session is released on deregistration. After re-registration
  UERANSIM re-establishes it, which produces a new `smf.pdu_session.establishment` trace.

## 4.4 PDU sessions only

```sh
kubectl -n $NS exec $UE -- nr-cli imsi-999700000000001 -e ps-list
kubectl -n $NS exec $UE -- nr-cli imsi-999700000000001 -e "ps-release 1"      # release PSI 1 (not traced)
kubectl -n $NS exec $UE -- nr-cli imsi-999700000000001 -e "ps-establish IPv4 --sst 1 --sd 0x111111 --dnn internet"
kubectl -n $NS exec $UE -- nr-cli imsi-999700000000001 -e ps-release-all
```

`ps-establish` produces one `smf.pdu_session.establishment` trace without any
registration. This is the quickest way to get a fresh SMF trace.

## 4.5 Provoking failures (for failure traces)

These all produce `outcome = failure` or `aborted` spans with a cause:

| Action | Expected trace |
|---|---|
| `ps-establish` with an unknown DNN, e.g. `--dnn foo` | `smf.pdu_session.establishment` `failure`, `5gs.5gsm.cause` set (PDU Session Establishment Reject) |
| `ps-establish` with a slice not in the subscription/config, e.g. `--sd 0x222222` | The AMF/SMF rejects it: a `failure` trace or no SMF trace at all, depending on where it is rejected |
| Scale UPF to 0, then `ps-establish` | PFCP has no peer: SMF trace `failure`/`aborted`, PFCP timeout log lines with `trace_id` |
| Remove a subscriber in the WebUI, then restart the UE pod | `amf.registration` `failure`, Registration/Authentication Reject |

Scaling Flux-managed deployments is reverted on the next reconcile. Undo
manually anyway with `kubectl -n $NS scale deploy/open5gs-upf --replicas=1`,
followed by `scripts/restart-core.sh`.

## 4.6 Checking that traces arrived

```sh
kubectl -n observability exec clickhouse-0 -- clickhouse-client -q "
  SELECT SpanName, SpanAttributes['5gs.outcome'] AS outcome, count()
  FROM otel_traces
  WHERE Timestamp > now() - INTERVAL 10 MINUTE
    AND ResourceAttributes['deployment.environment'] = 'open5gs-ns'
  GROUP BY SpanName, outcome"
```

Spans are exported when they **end**, and the BatchSpanProcessor flushes every
second. Allow a few seconds for the agent → Kafka → gateway → ClickHouse path
before the traces show up.
