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
 * OpenTelemetry PoC: generic SERVER spans for incoming SBI requests.
 *
 * Used by NFs that have no procedure spans of their own (AUSF, UDM, UDR).
 * One span per traced request (= request with a traceparent header):
 *
 *   started   ogs_sbi_server_handler()  -> ogs_sbi_server_otel_request()
 *   ended     ogs_sbi_server_send_response() / _send_rspmem_persistent()
 *             -> ogs_sbi_server_otel_response()
 *   fallback  stream removed without a response
 *             -> ogs_sbi_server_otel_stream_removed()
 *
 * Spans are kept in a hash keyed by the stream's pool ID, the same ID the
 * NFs use (e->h.sbi.data, xact->assoc_stream_id), so no backend struct has
 * to change. Everything runs in the NF's single event-loop thread.
 */

#include "ogs-sbi.h"
#include "ogs-otel.h"

typedef struct otel_server_span_s {
    ogs_pool_id_t stream_id;        /* hash key, must stay in place */
    ogs_otel_span_t *span;
} otel_server_span_t;

static bool otel_enabled;
static ogs_hash_t *otel_spans;

void ogs_sbi_server_otel_enable(void)
{
    otel_enabled = true;
    if (!otel_spans) {
        otel_spans = ogs_hash_make();
        ogs_assert(otel_spans);
    }
}

bool ogs_sbi_server_otel_enabled(void)
{
    return otel_enabled;
}

static otel_server_span_t *otel_find(ogs_pool_id_t stream_id)
{
    if (!otel_enabled || !otel_spans)
        return NULL;
    return ogs_hash_get(otel_spans, &stream_id, sizeof(stream_id));
}

static void otel_remove(otel_server_span_t *entry)
{
    ogs_assert(entry);
    ogs_hash_set(otel_spans, &entry->stream_id, sizeof(entry->stream_id), NULL);
    ogs_free(entry);
}

/* "suci-0-<mcc>-<mnc>-<routing>-0-..." : SUPI type IMSI, null scheme */
static bool otel_is_null_scheme_suci(const char *suci)
{
    const char *p = suci;
    int dashes = 0;

    if (strncmp(suci, "suci-0-", 7))
        return false;
    for (; *p; p++) {
        if (*p == '-' && ++dashes == 5)
            return p[1] == '0' && p[2] == '-';
    }
    return false;
}

char *ogs_sbi_otel_ue_ref(const char *supi_or_suci, char *buf)
{
    char *supi = NULL, *ref = NULL;

    ogs_assert(buf);

    if (!supi_or_suci || !supi_or_suci[0])
        return NULL;

    if (!otel_is_null_scheme_suci(supi_or_suci))
        return ogs_otel_ue_ref(supi_or_suci, buf);

    /* Same reference as for the SUPI (and as the dashboard's IMSI search) */
    supi = ogs_supi_from_suci(supi_or_suci);
    ref = ogs_otel_ue_ref(supi ? supi : supi_or_suci, buf);
    if (supi)
        ogs_free(supi);
    return ref;
}

static void otel_set_ue_ref(ogs_otel_span_t *span, const char *supi_or_suci)
{
    char ue_ref[OGS_OTEL_UE_REF_LEN];

    if (span && ogs_sbi_otel_ue_ref(supi_or_suci, ue_ref))
        ogs_otel_span_set_str(span, OGS_OTEL_ATTR_UE_REF, ue_ref);
}

void ogs_sbi_server_otel_request(
        ogs_sbi_request_t *request, ogs_pool_id_t stream_id)
{
    const char *traceparent = NULL;
    otel_server_span_t *entry = NULL;
    ogs_otel_span_t *span = NULL;
    char name[256], path[200];
    ogs_sbi_nf_instance_t *nf_instance = NULL;

    if (!otel_enabled || !request || !request->h.method || !request->h.uri)
        return;

    traceparent = ogs_sbi_header_get(
            request->http.headers, OGS_OTEL_TRACEPARENT_HEADER);
    if (!traceparent || !traceparent[0])
        return;                     /* caller not traced: no span */

    /* A stale entry for this ID (should not happen) is closed first */
    entry = otel_find(stream_id);
    if (entry) {
        ogs_otel_span_forget_active(entry->span);
        ogs_otel_span_set_str(entry->span, "error.type", "no_response");
        ogs_otel_span_end(entry->span,
                OGS_OTEL_STATUS_ERROR, "superseded stream");
        otel_remove(entry);
    }

    ogs_sbi_otel_url_template(request, path, sizeof(path));
    ogs_snprintf(name, sizeof(name), "%s %s", request->h.method, path);

    span = ogs_otel_span_start_ex(
            name, OGS_OTEL_KIND_SERVER, NULL, traceparent, NULL, NULL);
    if (!span)
        return;                     /* tracing disabled in this process */

    ogs_otel_span_set_str(span, "http.request.method", request->h.method);
    ogs_otel_span_set_str(span, "url.template", path);
    {
        /* first template component = service name, e.g. "nudm-ueau" */
        char service[64];
        const char *slash = strchr(path, '/');
        size_t len = slash ? (size_t)(slash - path) : strlen(path);

        if (len && len < sizeof(service)) {
            memcpy(service, path, len);
            service[len] = '\0';
            ogs_otel_span_set_str(span, "5gs.sbi.service", service);
        }
    }
    nf_instance = ogs_sbi_self()->nf_instance;
    if (nf_instance && nf_instance->nf_type)
        ogs_otel_span_set_str(span, OGS_OTEL_ATTR_NF,
                OpenAPI_nf_type_ToString(nf_instance->nf_type));

    /* SUPI / SUCI in the path (e.g. /nudr-dr/v1/subscription-data/imsi-...) */
    {
        char *copy = ogs_strdup(request->h.uri);
        char *component = NULL, *saveptr = NULL, *query = NULL;

        ogs_assert(copy);
        query = strchr(copy, '?');
        if (query)
            *query = '\0';
        for (component = strtok_r(copy, "/", &saveptr); component;
                component = strtok_r(NULL, "/", &saveptr)) {
            if (!strncmp(component, "imsi-", 5) ||
                !strncmp(component, "suci-", 5)) {
                otel_set_ue_ref(span, component);
                break;
            }
        }
        ogs_free(copy);
    }

    entry = ogs_calloc(1, sizeof(*entry));
    ogs_assert(entry);
    entry->stream_id = stream_id;
    entry->span = span;
    ogs_hash_set(otel_spans, &entry->stream_id, sizeof(entry->stream_id), entry);
}

/* "cause" of an application/problem+json body, e.g. "USER_NOT_FOUND" */
static bool otel_problem_cause(
        ogs_sbi_response_t *response, char *cause, size_t size)
{
    char body[1024];
    size_t len;
    char *p = NULL, *end = NULL;

    if (!response->http.content || !response->http.content_length)
        return false;

    len = ogs_min(response->http.content_length, sizeof(body) - 1);
    memcpy(body, response->http.content, len);
    body[len] = '\0';

    p = strstr(body, "\"cause\"");
    if (!p)
        return false;
    p = strchr(p + 7, ':');
    if (!p)
        return false;
    p = strchr(p, '"');
    if (!p)
        return false;
    end = strchr(++p, '"');
    if (!end || end == p || (size_t)(end - p) >= size)
        return false;

    memcpy(cause, p, end - p);
    cause[end - p] = '\0';
    return true;
}

void ogs_sbi_server_otel_response(
        ogs_sbi_stream_t *stream, ogs_sbi_response_t *response)
{
    otel_server_span_t *entry = NULL;
    ogs_otel_span_t *span = NULL;
    char cause[64], desc[96];

    if (!otel_enabled || !stream || !response)
        return;

    entry = otel_find(ogs_sbi_id_from_stream(stream));
    if (!entry)
        return;
    span = entry->span;
    otel_remove(entry);

    ogs_otel_span_set_int(span, "http.response.status_code", response->status);

    /*
     * The span may be the log context of the current event (activated by
     * the NF). Forget the pointer but keep the IDs in the log context, so
     * the remaining lines of this event are still correlated.
     */
    ogs_otel_span_forget_active(span);

    if (response->status >= 400) {
        if (otel_problem_cause(response, cause, sizeof(cause))) {
            ogs_otel_span_set_str(span, "5gs.sbi.cause", cause);
            ogs_otel_span_set_str(span, "error.type", cause);
            ogs_snprintf(desc, sizeof(desc),
                    "HTTP %d %s", response->status, cause);
        } else {
            ogs_snprintf(desc, sizeof(desc), "HTTP %d", response->status);
            ogs_otel_span_set_str(span, "error.type", desc + 5);
        }
        ogs_otel_span_end(span, OGS_OTEL_STATUS_ERROR, desc);
    } else {
        ogs_otel_span_end(span, OGS_OTEL_STATUS_UNSET, NULL);
    }
}

void ogs_sbi_server_otel_stream_removed(ogs_pool_id_t stream_id)
{
    otel_server_span_t *entry = otel_find(stream_id);

    if (!entry)
        return;

    ogs_otel_span_forget_active(entry->span);
    ogs_otel_span_set_str(entry->span, "error.type", "no_response");
    ogs_otel_span_end(entry->span,
            OGS_OTEL_STATUS_ERROR, "stream closed without response");
    otel_remove(entry);
}

ogs_otel_span_t *ogs_sbi_server_otel_span_by_id(ogs_pool_id_t stream_id)
{
    otel_server_span_t *entry = otel_find(stream_id);
    return entry ? entry->span : NULL;
}

ogs_otel_span_t *ogs_sbi_server_otel_span(ogs_sbi_stream_t *stream)
{
    if (!otel_enabled || !stream)
        return NULL;
    return ogs_sbi_server_otel_span_by_id(ogs_sbi_id_from_stream(stream));
}

void ogs_sbi_server_otel_set_str(ogs_sbi_stream_t *stream,
        const char *key, const char *value)
{
    ogs_otel_span_t *span = ogs_sbi_server_otel_span(stream);

    if (span && key && value)
        ogs_otel_span_set_str(span, key, value);
}

void ogs_sbi_server_otel_set_ue(
        ogs_sbi_stream_t *stream, const char *supi_or_suci)
{
    otel_set_ue_ref(ogs_sbi_server_otel_span(stream), supi_or_suci);
}
