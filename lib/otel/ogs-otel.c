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

/* Parts of ogs-otel that need Open5GS core/crypt (C only) */

#include "ogs-core.h"
#include "ogs-crypt.h"
#include "ogs-otel.h"

#define DEFAULT_UE_REF_SALT "open5gs-otel-poc"

static __thread ogs_otel_span_t *active_span;
static __thread const char *incoming_traceparent;

void ogs_otel_set_incoming_traceparent(const char *traceparent)
{
    incoming_traceparent = traceparent;
}

const char *ogs_otel_incoming_traceparent(void)
{
    return incoming_traceparent;
}

char *ogs_otel_span_traceparent(ogs_otel_span_t *span, char *buf)
{
    const char *trace_id = ogs_otel_span_trace_id(span);
    const char *span_id = ogs_otel_span_span_id(span);

    ogs_assert(buf);

    if (!trace_id || !span_id)
        return NULL;

    ogs_snprintf(buf, OGS_OTEL_TRACEPARENT_LEN, "00-%s-%s-01", trace_id, span_id);
    return buf;
}

void ogs_otel_span_activate(ogs_otel_span_t *span)
{
    active_span = span;
    ogs_log_set_trace_context(
            ogs_otel_span_trace_id(span), ogs_otel_span_span_id(span));
}

void ogs_otel_span_finish(ogs_otel_span_t **span_ptr,
        const char *outcome, const char *description)
{
    ogs_otel_span_t *span = NULL;
    ogs_otel_status_e status = OGS_OTEL_STATUS_ERROR;

    if (!span_ptr || !*span_ptr)
        return;

    span = *span_ptr;
    *span_ptr = NULL;

    if (outcome) {
        ogs_otel_span_set_str(span, OGS_OTEL_ATTR_OUTCOME, outcome);
        if (!strcmp(outcome, OGS_OTEL_OUTCOME_SUCCESS))
            status = OGS_OTEL_STATUS_OK;
    }

    /*
     * Log the terminal outcome while the span is still the log context,
     * so that at least this line is always correlated with the trace.
     */
    if (active_span != span)
        ogs_log_set_trace_context(
                ogs_otel_span_trace_id(span), ogs_otel_span_span_id(span));
    if (status == OGS_OTEL_STATUS_OK)
        ogs_info("[otel] procedure finished [outcome:%s]", outcome);
    else
        ogs_warn("[otel] procedure finished [outcome:%s] %s",
                outcome ? outcome : "unknown",
                description ? description : "");

    if (active_span == span)
        active_span = NULL;

    /* Restore whatever context was active before (or clear it) */
    ogs_log_set_trace_context(ogs_otel_span_trace_id(active_span),
            ogs_otel_span_span_id(active_span));

    ogs_otel_span_end(span, status, description);
}

char *ogs_otel_ue_ref(const char *id, char *buf)
{
    const char *salt = NULL;
    char *input = NULL;
    uint8_t digest[OGS_SHA256_DIGEST_SIZE];

    ogs_assert(buf);

    if (!id || !id[0])
        return NULL;

    salt = getenv("OGS_OTEL_UE_REF_SALT");
    if (!salt || !salt[0])
        salt = DEFAULT_UE_REF_SALT;

    input = ogs_msprintf("%s%s", salt, id);
    ogs_assert(input);
    ogs_sha256((const uint8_t *)input, strlen(input), digest);
    ogs_free(input);

    ogs_hex_to_ascii(digest, 8, buf, OGS_OTEL_UE_REF_LEN);

    return buf;
}
