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

#include "ogs-otel.h"
#include "otel.h"

static const char *n4_name(uint8_t type)
{
    switch (type) {
    case OGS_PFCP_SESSION_ESTABLISHMENT_REQUEST_TYPE:
        return "PFCP Session Establishment";
    case OGS_PFCP_SESSION_MODIFICATION_REQUEST_TYPE:
        return "PFCP Session Modification";
    case OGS_PFCP_SESSION_DELETION_REQUEST_TYPE:
        return "PFCP Session Deletion";
    default:
        return NULL;
    }
}

/* ogs_pfcp_xact_otel_end_hook: response sent (cause) or none (0) */
static void n4_end(ogs_pfcp_xact_t *xact, int cause)
{
    ogs_otel_span_t *span = NULL;
    char text[48];

    ogs_assert(xact);
    span = xact->otel_span;
    if (!span)
        return;

    /*
     * The response is sent in the middle of the event: keep the log suffix
     * for the remaining lines of this event, but do not leave a dangling
     * active-span pointer.
     */
    ogs_otel_span_forget_active(span);

    if (cause == OGS_PFCP_CAUSE_REQUEST_ACCEPTED) {
        ogs_otel_span_set_int(span, "5gs.pfcp.cause", cause);
        ogs_otel_span_end(span, OGS_OTEL_STATUS_UNSET, NULL);
    } else if (cause > 0) {
        ogs_otel_span_set_int(span, "5gs.pfcp.cause", cause);
        ogs_snprintf(text, sizeof(text), "pfcp_cause_%d", cause);
        ogs_otel_span_set_str(span, "error.type", text);
        ogs_snprintf(text, sizeof(text), "PFCP cause %d", cause);
        ogs_otel_span_end(span, OGS_OTEL_STATUS_ERROR, text);
    } else if (cause < 0) {
        ogs_otel_span_end(span, OGS_OTEL_STATUS_UNSET, NULL);
    } else {
        ogs_otel_span_set_str(span, "error.type", "no_response");
        ogs_otel_span_end(span, OGS_OTEL_STATUS_ERROR, "no PFCP response");
    }
}

void upf_otel_init(void)
{
    ogs_pfcp_xact_otel_end_hook = n4_end;
}

void upf_otel_n4_request(ogs_pfcp_xact_t *xact,
        ogs_pfcp_message_t *message, ogs_pkbuf_t *body)
{
    char traceparent[OGS_PFCP_OTEL_TRACEPARENT_LEN];
    ogs_otel_span_t *span = NULL;
    const char *name = NULL;

    ogs_assert(message);

    name = n4_name(message->h.type);
    if (!name || !xact || xact->otel_span)
        return;
    if (!ogs_pfcp_otel_traceparent(body, traceparent, sizeof(traceparent)))
        return;

    span = ogs_otel_span_start_ex(name, OGS_OTEL_KIND_SERVER,
            NULL, traceparent, NULL, NULL);
    if (!span)
        return;

    ogs_otel_span_set_str(span, OGS_OTEL_ATTR_NF, "UPF");
    ogs_otel_span_set_str(span, "5gs.pfcp.message", name);
    ogs_otel_span_set_int(span, "5gs.pfcp.xid", xact->xid);

    xact->otel_span = span;
    ogs_otel_span_activate(span);
}

void upf_otel_n4_session(ogs_pfcp_xact_t *xact, upf_sess_t *sess,
        int num_of_created_pdr)
{
    ogs_otel_span_t *span = NULL;
    char seid[24];

    if (!xact || !sess)
        return;
    span = xact->otel_span;
    if (!span)
        return;

    ogs_snprintf(seid, sizeof(seid), "0x%llx",
            (unsigned long long)sess->upf_n4_seid);
    ogs_otel_span_set_str(span, "5gs.pfcp.up_seid", seid);
    if (sess->apn_dnn)
        ogs_otel_span_set_str(span, "5gs.dnn", sess->apn_dnn);
    ogs_otel_span_set_int(span, "5gs.upf.pdrs",
            (int64_t)ogs_list_count(&sess->pfcp.pdr_list));
    if (num_of_created_pdr)
        ogs_otel_span_set_int(span, "5gs.upf.pdrs_created",
                num_of_created_pdr);
}
