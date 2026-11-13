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

#ifndef AMF_OTEL_H
#define AMF_OTEL_H

#include "context.h"

#ifdef __cplusplus
extern "C" {
#endif

/*
 * "amf.registration" span: one per counted Registration Request
 * (same place as fivegs_amffunction_rm_reg*req), stored in
 * amf_ue->registration_span until a terminal outcome.
 */

/* Registration Request in DE-REGISTERED/REGISTERED; supersedes any open span */
void amf_otel_registration_start(amf_ue_t *amf_ue);

/* Registration Request in another state: continue the open span or start */
void amf_otel_registration_restart(amf_ue_t *amf_ue);

/* After gmm_handle_registration_request(): registration type, TAI */
void amf_otel_registration_update(amf_ue_t *amf_ue);

void amf_otel_registration_event(amf_ue_t *amf_ue, const char *name);

/* gmm_cause 0 = none. No-op without an open span */
void amf_otel_registration_end(amf_ue_t *amf_ue,
        const char *outcome, int gmm_cause, const char *description);

/*
 * "amf.pdu_session.establishment" span: one per Create SM Context, stored in
 * sess->establishment_span. Root of its own trace with a span link to the
 * UE's last registration span. Its CLIENT spans (Create/Update SM Context)
 * carry the trace context to the SMF in the W3C traceparent header.
 * Ends when the SMF answers the activating Update SM Context (success).
 */
void amf_otel_session_start(amf_ue_t *amf_ue, amf_sess_t *sess);
void amf_otel_session_event(amf_sess_t *sess, const char *name);
/* http_status 0 = none. No-op without an open span */
void amf_otel_session_end(amf_sess_t *sess,
        const char *outcome, int http_status, const char *description);
/*
 * true if the SBI request being processed belongs to the current session
 * attempt: its traceparent is in the trace of sess->establishment_span (or
 * it carries none, so it cannot be told apart)
 */
bool amf_otel_session_owns_request(amf_sess_t *sess);

/*
 * SERVER span for an incoming SBI request (child of its traceparent, if
 * any). NULL if tracing is off. End it with amf_otel_server_end().
 */
ogs_otel_span_t *amf_otel_server_start(const char *name);
void amf_otel_server_end(ogs_otel_span_t *span);

#ifdef __cplusplus
}
#endif

#endif /* AMF_OTEL_H */
