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

#ifndef SMF_OTEL_H
#define SMF_OTEL_H

#include "context.h"

#ifdef __cplusplus
extern "C" {
#endif

/*
 * "smf.pdu_session.establishment" span (5GC, non-roaming), stored in
 * sess->establishment_span. Starts with POST /sm-contexts and ends when the
 * user plane is active and the SMF is registered at the UDM (204 to the
 * AMF), or on a terminal failure.
 *
 * Note: fivegs_smffunction_sm_pdusessioncreationsucc is already incremented
 * when 201 Created is sent, i.e. before UDM/PCF/PFCP.
 */

void smf_otel_establishment_start(smf_sess_t *sess);
void smf_otel_establishment_event(smf_sess_t *sess, const char *name);

/* Records attributes without ending (0 = not set) */
void smf_otel_establishment_set_int(smf_sess_t *sess,
        const char *key, int64_t value);

/* gsm_cause/http_status 0 = none. No-op without an open span */
void smf_otel_establishment_end(smf_sess_t *sess, const char *outcome,
        int gsm_cause, int http_status, const char *description);

#ifdef __cplusplus
}
#endif

#endif /* SMF_OTEL_H */
