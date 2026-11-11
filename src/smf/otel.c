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

static const char *pdu_session_type_name(uint8_t type)
{
    switch (type) {
    case OGS_PDU_SESSION_TYPE_IPV4:
        return "IPv4";
    case OGS_PDU_SESSION_TYPE_IPV6:
        return "IPv6";
    case OGS_PDU_SESSION_TYPE_IPV4V6:
        return "IPv4v6";
    case OGS_PDU_SESSION_TYPE_UNSTRUCTURED:
        return "Unstructured";
    case OGS_PDU_SESSION_TYPE_ETHERNET:
        return "Ethernet";
    default:
        return NULL;
    }
}

void smf_otel_establishment_start(smf_sess_t *sess)
{
    ogs_assert(sess);

    ogs_otel_span_finish(&sess->establishment_span,
            OGS_OTEL_OUTCOME_SUPERSEDED, "new SM context create");

    sess->establishment_span =
        ogs_otel_span_start("smf.pdu_session.establishment");
    if (!sess->establishment_span)
        return;

    ogs_otel_span_set_str(sess->establishment_span,
            OGS_OTEL_ATTR_NF, "SMF");
    ogs_otel_span_set_str(sess->establishment_span,
            OGS_OTEL_ATTR_PROCEDURE, "pdu_session_establishment");

    ogs_otel_span_activate(sess->establishment_span);
}

void smf_otel_establishment_event(smf_sess_t *sess, const char *name)
{
    ogs_assert(sess);
    ogs_otel_span_add_event(sess->establishment_span, name);
}

void smf_otel_establishment_set_int(smf_sess_t *sess,
        const char *key, int64_t value)
{
    ogs_assert(sess);
    ogs_otel_span_set_int(sess->establishment_span, key, value);
}

void smf_otel_establishment_end(smf_sess_t *sess, const char *outcome,
        int gsm_cause, int http_status, const char *description)
{
    smf_ue_t *smf_ue = NULL;
    ogs_otel_span_t *span = NULL;
    const char *type = NULL;
    char plmn_id[OGS_PLMNIDSTRLEN];
    char ue_ref[OGS_OTEL_UE_REF_LEN];
    char error_type[32];
    char sd[8];

    ogs_assert(sess);
    ogs_assert(outcome);

    span = sess->establishment_span;
    if (!span)
        return;

    /* Session attributes are only complete at the end of the procedure */
    ogs_otel_span_set_int(span, "5gs.pdu_session.id", sess->psi);
    if (sess->session.name)
        ogs_otel_span_set_str(span, "5gs.dnn", sess->session.name);

    type = pdu_session_type_name(sess->session.session_type);
    if (!type)
        type = pdu_session_type_name(sess->ue_session_type);
    if (type)
        ogs_otel_span_set_str(span, "5gs.pdu_session.type", type);
    if (sess->ue_ssc_mode)
        ogs_otel_span_set_int(span, "5gs.ssc_mode", sess->ue_ssc_mode);

    ogs_otel_span_set_int(span, "5gs.s_nssai.sst", sess->s_nssai.sst);
    if (sess->s_nssai.sd.v != OGS_S_NSSAI_NO_SD_VALUE) {
        /* hex, as in the configuration and 3GPP notation (e.g. 111111) */
        ogs_snprintf(sd, sizeof(sd), "%06x", (unsigned int)sess->s_nssai.sd.v);
        ogs_otel_span_set_str(span, "5gs.s_nssai.sd", sd);
    }
    ogs_otel_span_set_str(span, "5gs.plmn_id",
            ogs_plmn_id_to_string(&sess->serving_plmn_id, plmn_id));

    smf_ue = smf_ue_find_by_id(sess->smf_ue_id);
    if (smf_ue && ogs_otel_ue_ref(smf_ue->supi, ue_ref))
        ogs_otel_span_set_str(span, OGS_OTEL_ATTR_UE_REF, ue_ref);

    if (http_status)
        ogs_otel_span_set_int(span, "http.response.status_code", http_status);

    if (gsm_cause) {
        ogs_otel_span_set_int(span, "5gs.5gsm.cause", gsm_cause);
        ogs_snprintf(error_type, sizeof(error_type),
                "5gsm_cause_%d", gsm_cause);
        ogs_otel_span_set_str(span, "error.type", error_type);
    } else if (http_status) {
        ogs_snprintf(error_type, sizeof(error_type),
                "http_%d", http_status);
        ogs_otel_span_set_str(span, "error.type", error_type);
    } else if (strcmp(outcome, OGS_OTEL_OUTCOME_SUCCESS)) {
        ogs_otel_span_set_str(span, "error.type", outcome);
    }

    ogs_otel_span_finish(&sess->establishment_span, outcome, description);
}
