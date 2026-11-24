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

#ifndef UPF_OTEL_H
#define UPF_OTEL_H

#include "context.h"
#include "ogs-otel.h"

#ifdef __cplusplus
extern "C" {
#endif

/*
 * OpenTelemetry PoC - UPF (N4 / PFCP).
 *
 * PFCP has no header for trace context. The SMF appends its PFCP CLIENT
 * span's W3C traceparent as an enterprise-specific IE (lib/pfcp/xact.c).
 * For a Session Establishment/Modification/Deletion Request carrying it the
 * UPF opens a SERVER span, child of the SMF's span, so the UPF appears in
 * the PDU session trace:
 *
 *   smf.pdu_session.establishment                 SMF
 *   └ PFCP Session Establishment       CLIENT     SMF
 *     └ PFCP Session Establishment     SERVER     UPF   <- this file
 *
 * The span is the active span while the request is handled (UPF log lines
 * carry its trace ID) and ends when the response is sent, with the PFCP
 * Cause of the response (ogs_pfcp_xact_otel_end_hook), or as no_response
 * if the transaction is deleted without one. Requests without the IE
 * (other SMFs, untraced procedures) create nothing.
 */
void upf_otel_init(void);
void upf_otel_n4_request(ogs_pfcp_xact_t *xact,
        ogs_pfcp_message_t *message, ogs_pkbuf_t *body);

/* domain attributes, set by the N4 handlers before the response is sent */
void upf_otel_n4_session(ogs_pfcp_xact_t *xact, upf_sess_t *sess,
        int num_of_created_pdr);

#ifdef __cplusplus
}
#endif

#endif /* UPF_OTEL_H */
