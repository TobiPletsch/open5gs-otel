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

#ifdef __cplusplus
}
#endif

#endif /* AMF_OTEL_H */
