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

#include "otel.h"

static const char *registration_type_name(uint8_t value)
{
    switch (value) {
    case OGS_NAS_5GS_REGISTRATION_TYPE_INITIAL:
        return "initial";
    case OGS_NAS_5GS_REGISTRATION_TYPE_MOBILITY_UPDATING:
        return "mobility_updating";
    case OGS_NAS_5GS_REGISTRATION_TYPE_PERIODIC_UPDATING:
        return "periodic_updating";
    case OGS_NAS_5GS_REGISTRATION_TYPE_EMERGENCY:
        return "emergency";
    default:
        return "unknown";
    }
}

void amf_otel_registration_start(amf_ue_t *amf_ue)
{
    ran_ue_t *ran_ue = NULL;

    ogs_assert(amf_ue);

    ogs_otel_span_finish(&amf_ue->registration_span,
            OGS_OTEL_OUTCOME_SUPERSEDED, "new Registration Request");

    amf_ue->registration_span = ogs_otel_span_start("amf.registration");
    if (!amf_ue->registration_span)
        return;

    ogs_otel_span_set_str(amf_ue->registration_span,
            OGS_OTEL_ATTR_NF, "AMF");
    ogs_otel_span_set_str(amf_ue->registration_span,
            OGS_OTEL_ATTR_PROCEDURE, "registration");

    /* Remembered for the span link of later PDU session traces */
    ogs_cpystrn(amf_ue->last_registration_trace_id,
            ogs_otel_span_trace_id(amf_ue->registration_span),
            sizeof(amf_ue->last_registration_trace_id));
    ogs_cpystrn(amf_ue->last_registration_span_id,
            ogs_otel_span_span_id(amf_ue->registration_span),
            sizeof(amf_ue->last_registration_span_id));

    ran_ue = ran_ue_find_by_id(amf_ue->ran_ue_id);
    if (ran_ue) {
        ogs_otel_span_set_int(amf_ue->registration_span,
                "5gs.ngap.ran_ue_ngap_id", (int64_t)ran_ue->ran_ue_ngap_id);
        ogs_otel_span_set_int(amf_ue->registration_span,
                "5gs.ngap.amf_ue_ngap_id", (int64_t)ran_ue->amf_ue_ngap_id);
    }

    ogs_otel_span_activate(amf_ue->registration_span);
}

void amf_otel_registration_restart(amf_ue_t *amf_ue)
{
    ogs_assert(amf_ue);

    if (amf_ue->registration_span) {
        ogs_otel_span_add_event(amf_ue->registration_span,
                "registration_request_restart");
        ogs_otel_span_activate(amf_ue->registration_span);
    } else {
        amf_otel_registration_start(amf_ue);
    }
}

void amf_otel_registration_update(amf_ue_t *amf_ue)
{
    char plmn_id[OGS_PLMNIDSTRLEN];

    ogs_assert(amf_ue);

    if (!amf_ue->registration_span)
        return;

    ogs_otel_span_set_str(amf_ue->registration_span,
            "5gs.registration.type",
            registration_type_name(amf_ue->nas.registration.value));
    ogs_otel_span_set_str(amf_ue->registration_span, "5gs.plmn_id",
            ogs_plmn_id_to_string(&amf_ue->nr_tai.plmn_id, plmn_id));
    ogs_otel_span_set_int(amf_ue->registration_span, "5gs.tac",
            (int64_t)amf_ue->nr_tai.tac.v);
}

void amf_otel_registration_event(amf_ue_t *amf_ue, const char *name)
{
    ogs_assert(amf_ue);
    ogs_otel_span_add_event(amf_ue->registration_span, name);
}

void amf_otel_registration_end(amf_ue_t *amf_ue,
        const char *outcome, int gmm_cause, const char *description)
{
    char ue_ref[OGS_OTEL_UE_REF_LEN];
    char error_type[32];

    ogs_assert(amf_ue);
    ogs_assert(outcome);

    if (!amf_ue->registration_span)
        return;

    /* SUPI is known once authenticated; otherwise fall back to SUCI */
    if (ogs_otel_ue_ref(amf_ue->supi ? amf_ue->supi : amf_ue->suci, ue_ref))
        ogs_otel_span_set_str(amf_ue->registration_span,
                OGS_OTEL_ATTR_UE_REF, ue_ref);

    if (gmm_cause) {
        ogs_otel_span_set_int(amf_ue->registration_span,
                "5gs.5gmm.cause", gmm_cause);
        ogs_snprintf(error_type, sizeof(error_type),
                "5gmm_cause_%d", gmm_cause);
        ogs_otel_span_set_str(amf_ue->registration_span,
                "error.type", error_type);
    } else if (strcmp(outcome, OGS_OTEL_OUTCOME_SUCCESS)) {
        ogs_otel_span_set_str(amf_ue->registration_span,
                "error.type", outcome);
    }

    ogs_otel_span_finish(&amf_ue->registration_span, outcome, description);
}

void amf_otel_session_start(amf_ue_t *amf_ue, amf_sess_t *sess)
{
    ogs_assert(amf_ue);
    ogs_assert(sess);

    ogs_otel_span_finish(&sess->establishment_span,
            OGS_OTEL_OUTCOME_SUPERSEDED, "new Create SM Context");

    sess->establishment_span = ogs_otel_span_start_ex(
            "amf.pdu_session.establishment", OGS_OTEL_KIND_INTERNAL,
            NULL, NULL,
            amf_ue->last_registration_trace_id[0] ?
                amf_ue->last_registration_trace_id : NULL,
            amf_ue->last_registration_span_id[0] ?
                amf_ue->last_registration_span_id : NULL);
    if (!sess->establishment_span)
        return;

    ogs_otel_span_set_str(sess->establishment_span,
            OGS_OTEL_ATTR_NF, "AMF");
    ogs_otel_span_set_str(sess->establishment_span,
            OGS_OTEL_ATTR_PROCEDURE, "pdu_session_establishment");
    ogs_otel_span_set_int(sess->establishment_span,
            "5gs.pdu_session.id", sess->psi);
    /* Same IDs as the span link, as plain attributes for SQL joins */
    if (amf_ue->last_registration_trace_id[0])
        ogs_otel_span_set_str(sess->establishment_span,
                "5gs.registration.trace_id",
                amf_ue->last_registration_trace_id);

    ogs_otel_span_activate(sess->establishment_span);
}

void amf_otel_session_event(amf_sess_t *sess, const char *name)
{
    ogs_assert(sess);
    ogs_otel_span_add_event(sess->establishment_span, name);
}

void amf_otel_session_end(amf_sess_t *sess,
        const char *outcome, int http_status, const char *description)
{
    amf_ue_t *amf_ue = NULL;
    char ue_ref[OGS_OTEL_UE_REF_LEN];
    char error_type[32];

    ogs_assert(sess);
    ogs_assert(outcome);

    if (!sess->establishment_span)
        return;

    if (sess->dnn)
        ogs_otel_span_set_str(sess->establishment_span, "5gs.dnn", sess->dnn);
    ogs_otel_span_set_int(sess->establishment_span,
            "5gs.s_nssai.sst", sess->s_nssai.sst);

    amf_ue = amf_ue_find_by_id(sess->amf_ue_id);
    if (amf_ue && ogs_otel_ue_ref(amf_ue->supi, ue_ref))
        ogs_otel_span_set_str(sess->establishment_span,
                OGS_OTEL_ATTR_UE_REF, ue_ref);

    if (http_status) {
        ogs_otel_span_set_int(sess->establishment_span,
                "http.response.status_code", http_status);
        ogs_snprintf(error_type, sizeof(error_type), "http_%d", http_status);
        ogs_otel_span_set_str(sess->establishment_span,
                "error.type", error_type);
    } else if (strcmp(outcome, OGS_OTEL_OUTCOME_SUCCESS)) {
        ogs_otel_span_set_str(sess->establishment_span, "error.type", outcome);
    }

    ogs_otel_span_finish(&sess->establishment_span, outcome, description);
}

ogs_otel_span_t *amf_otel_server_start(const char *name)
{
    ogs_otel_span_t *span = NULL;

    ogs_assert(name);

    /* Only requests from a traced caller: no orphan one-span traces */
    if (!ogs_otel_incoming_traceparent())
        return NULL;

    span = ogs_otel_span_start_ex(name, OGS_OTEL_KIND_SERVER,
            NULL, ogs_otel_incoming_traceparent(), NULL, NULL);
    if (span)
        ogs_otel_span_set_str(span, OGS_OTEL_ATTR_NF, "AMF");
    return span;
}

void amf_otel_server_end(ogs_otel_span_t *span)
{
    if (!span)
        return;
    /* The span may be the log context of this thread: clear it first */
    ogs_otel_span_activate(NULL);
    ogs_otel_span_end(span, OGS_OTEL_STATUS_UNSET, NULL);
}
