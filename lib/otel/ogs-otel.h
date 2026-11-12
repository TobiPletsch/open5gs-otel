/*
 * Copyright (C) 2026 by Tobias Pletsch
 *
 * This file is part of Open5GS.
 *
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU Affero General Public License as published by
 * the Free Software Foundation, either version 3 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program.  If not, see <https://www.gnu.org/licenses/>.
 */

/*
 * Minimal C interface to the OpenTelemetry C++ SDK (traces only).
 *
 * Built with -Dotel=true, spans are exported via OTLP/HTTP (protobuf) by a
 * BatchSpanProcessor running in its own thread. Configuration uses the
 * standard environment variables only:
 *
 *   OTEL_EXPORTER_OTLP_ENDPOINT / OTEL_EXPORTER_OTLP_TRACES_ENDPOINT
 *   OTEL_SERVICE_NAME, OTEL_RESOURCE_ATTRIBUTES, OTEL_SDK_DISABLED
 *
 * Tracing stays disabled unless an OTLP endpoint is configured. When
 * disabled (or built without -Dotel=true) every function is a no-op and
 * ogs_otel_span_start() returns NULL. All functions accept a NULL span.
 *
 * Header is plain C and does not include ogs-core.h so that the C++
 * implementation can use it directly.
 */

#ifndef OGS_OTEL_H
#define OGS_OTEL_H

#include <stdint.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct ogs_otel_span_s ogs_otel_span_t;

typedef enum {
    OGS_OTEL_STATUS_UNSET = 0,
    OGS_OTEL_STATUS_OK,
    OGS_OTEL_STATUS_ERROR,
} ogs_otel_status_e;

/* Common attribute keys (no 3GPP semantic conventions exist in OTel) */
#define OGS_OTEL_ATTR_NF                "5gs.nf"
#define OGS_OTEL_ATTR_PROCEDURE         "5gs.procedure"
#define OGS_OTEL_ATTR_OUTCOME           "5gs.outcome"
#define OGS_OTEL_ATTR_UE_REF            "5gs.ue.ref"

#define OGS_OTEL_OUTCOME_SUCCESS        "success"
#define OGS_OTEL_OUTCOME_FAILURE        "failure"
#define OGS_OTEL_OUTCOME_ABORTED        "aborted"
#define OGS_OTEL_OUTCOME_SUPERSEDED     "superseded"

int ogs_otel_init(const char *service_name, const char *service_version);
void ogs_otel_final(void);

/* Starts a new root span (SpanKind INTERNAL); NULL if tracing disabled */
ogs_otel_span_t *ogs_otel_span_start(const char *name);

typedef enum {
    OGS_OTEL_KIND_INTERNAL = 0,
    OGS_OTEL_KIND_SERVER,
    OGS_OTEL_KIND_CLIENT,
} ogs_otel_kind_e;

/*
 * Starts a span with explicit kind and relations. The parent is, in this
 * order: the local span `parent`, else the remote parent in the W3C
 * `traceparent` string ("00-<32 hex>-<16 hex>-<2 hex>"), else none (root).
 * link_trace_id/link_span_id (lower-case hex) add a span link, e.g. from a
 * PDU session trace to the registration trace. NULL if tracing disabled.
 */
ogs_otel_span_t *ogs_otel_span_start_ex(const char *name,
        ogs_otel_kind_e kind, ogs_otel_span_t *parent,
        const char *traceparent,
        const char *link_trace_id, const char *link_span_id);

/* W3C traceparent of the span ("00-<trace>-<span>-01"), or NULL */
#define OGS_OTEL_TRACEPARENT_LEN 56
char *ogs_otel_span_traceparent(ogs_otel_span_t *span, char *buf);

/* HTTP header name used for propagation */
#define OGS_OTEL_TRACEPARENT_HEADER "traceparent"

void ogs_otel_span_set_str(ogs_otel_span_t *span,
        const char *key, const char *value);
void ogs_otel_span_set_int(ogs_otel_span_t *span,
        const char *key, int64_t value);
void ogs_otel_span_add_event(ogs_otel_span_t *span, const char *name);

/* Ends and frees the span. description is used for STATUS_ERROR only */
void ogs_otel_span_end(ogs_otel_span_t *span,
        ogs_otel_status_e status, const char *description);

/* Lower-case hex IDs, or NULL */
const char *ogs_otel_span_trace_id(ogs_otel_span_t *span);
const char *ogs_otel_span_span_id(ogs_otel_span_t *span);

/*
 * Makes the span the log context of the calling thread, so that
 * ogs_log lines carry its trace_id/span_id. NULL clears the context.
 */
void ogs_otel_span_activate(ogs_otel_span_t *span);

/*
 * W3C traceparent of the SBI request currently being processed by this
 * thread (set when an SBI server event is dispatched, NULL afterwards).
 * The string is owned by the request; it is only valid during the event.
 */
void ogs_otel_set_incoming_traceparent(const char *traceparent);
const char *ogs_otel_incoming_traceparent(void);

/*
 * Pseudonymous, non-reversible UE reference for span attributes:
 * first 8 bytes of SHA-256(salt || id) as 16 hex chars.
 * Salt from OGS_OTEL_UE_REF_SALT. buf must hold 17 bytes.
 * Returns buf, or NULL if id is NULL/empty.
 */
#define OGS_OTEL_UE_REF_LEN 17
char *ogs_otel_ue_ref(const char *id, char *buf);

/*
 * Ends *span_ptr with outcome/status, sets it NULL and clears the log
 * context if this span was active. No-op if *span_ptr is NULL.
 */
void ogs_otel_span_finish(ogs_otel_span_t **span_ptr,
        const char *outcome, const char *description);

#ifdef __cplusplus
}
#endif

#endif /* OGS_OTEL_H */
