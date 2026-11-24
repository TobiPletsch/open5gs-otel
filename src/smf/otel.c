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

    /*
     * SERVER span, child of the AMF's Create SM Context CLIENT span when
     * the request carried a traceparent; otherwise a root span as before.
     */
    sess->establishment_span = ogs_otel_span_start_ex(
            "smf.pdu_session.establishment", OGS_OTEL_KIND_SERVER,
            NULL, ogs_otel_incoming_traceparent(), NULL, NULL);
    if (!sess->establishment_span)
        return;

    ogs_otel_span_set_str(sess->establishment_span,
            OGS_OTEL_ATTR_NF, "SMF");
    ogs_otel_span_set_str(sess->establishment_span,
            OGS_OTEL_ATTR_PROCEDURE, "pdu_session_establishment");

    /* for the duration histogram (metrics) */
    sess->establishment_start = ogs_get_monotonic_time();

    ogs_otel_span_activate(sess->establishment_span);
}

/*
 * Metrics (manual instrumentation, OTel metrics SDK):
 *
 *   open5gs.smf.pdu_session.establishment.attempts  counter   {attempt}
 *   open5gs.smf.pdu_session.establishment.duration  histogram ms
 *
 * Recorded once per attempt, where the span ends, with the SAME attribute
 * values as the span (5gs.outcome, error.type, 5gs.dnn). That is what makes
 * the drill-down from a metric data point to its traces exact:
 * "failures with error.type=X in minute M" = the spans with
 * error.type=X that ended in minute M.
 */
static void record_attempt(smf_sess_t *sess,
        const char *outcome, const char *error_type)
{
    static ogs_otel_counter_t *attempts;
    static ogs_otel_histogram_t *duration;
    ogs_otel_attr_t attrs[3];
    size_t n = 0;

    if (!attempts) {
        attempts = ogs_otel_counter_create(
                "open5gs.smf.pdu_session.establishment.attempts",
                "PDU session establishment attempts by outcome and cause "
                "(as reported by the SMF)", "{attempt}");
        duration = ogs_otel_histogram_create(
                "open5gs.smf.pdu_session.establishment.duration",
                "Duration of PDU session establishment attempts", "ms");
    }
    if (!attempts)
        return;                 /* tracing/metrics disabled */

    attrs[n].key = OGS_OTEL_ATTR_OUTCOME; attrs[n++].value = outcome;
    attrs[n].key = "error.type";
    attrs[n++].value = error_type ? error_type : "none";
    attrs[n].key = "5gs.dnn";
    attrs[n++].value = sess->session.name ? sess->session.name : "unknown";

    ogs_otel_counter_add(attempts, 1, attrs, n);
    if (sess->establishment_start)
        ogs_otel_histogram_record(duration,
                (double)(ogs_get_monotonic_time() -
                    sess->establishment_start) / 1000.0,   /* us -> ms */
                attrs, 1);  /* outcome only: keep the histogram small */
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

    /* The modify request is answered when the establishment ends */
    if (sess->update_span) {
        ogs_otel_span_t *update_span = sess->update_span;
        sess->update_span = NULL;
        ogs_otel_span_end(update_span,
                strcmp(outcome, OGS_OTEL_OUTCOME_SUCCESS) ?
                    OGS_OTEL_STATUS_ERROR : OGS_OTEL_STATUS_UNSET,
                description);
    }
    if (sess->pfcp_span) {
        ogs_otel_span_t *pfcp_span = sess->pfcp_span;
        sess->pfcp_span = NULL;
        ogs_otel_span_end(pfcp_span, OGS_OTEL_STATUS_ERROR, "procedure ended");
    }

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
        record_attempt(sess, outcome, error_type);
    } else if (http_status) {
        ogs_snprintf(error_type, sizeof(error_type),
                "http_%d", http_status);
        ogs_otel_span_set_str(span, "error.type", error_type);
        record_attempt(sess, outcome, error_type);
    } else if (strcmp(outcome, OGS_OTEL_OUTCOME_SUCCESS)) {
        ogs_otel_span_set_str(span, "error.type", outcome);
        record_attempt(sess, outcome, outcome);
    } else {
        record_attempt(sess, outcome, NULL);
    }

    ogs_otel_span_finish(&sess->establishment_span, outcome, description);
}

void smf_otel_update_start(smf_sess_t *sess)
{
    ogs_assert(sess);

    if (!sess->establishment_span || sess->update_span)
        return;

    sess->update_span = ogs_otel_span_start_ex(
            "POST nsmf-pdusession/sm-contexts/{id}/modify",
            OGS_OTEL_KIND_SERVER, NULL, ogs_otel_incoming_traceparent(),
            NULL, NULL);
    if (sess->update_span)
        ogs_otel_span_set_str(sess->update_span, OGS_OTEL_ATTR_NF, "SMF");
}

void smf_otel_pfcp_start(smf_sess_t *sess,
        ogs_pfcp_xact_t *xact, const char *name)
{
    ogs_otel_span_t *parent = NULL;

    ogs_assert(sess);
    ogs_assert(xact);
    ogs_assert(name);

    if (!sess->establishment_span)
        return;

    if (sess->pfcp_span) {
        ogs_otel_span_t *old = sess->pfcp_span;
        sess->pfcp_span = NULL;
        ogs_otel_span_end(old, OGS_OTEL_STATUS_ERROR, "superseded");
    }

    parent = sess->update_span ? sess->update_span : sess->establishment_span;
    sess->pfcp_span = ogs_otel_span_start_ex(
            name, OGS_OTEL_KIND_CLIENT, parent, NULL, NULL, NULL);
    if (!sess->pfcp_span)
        return;

    sess->pfcp_span_xact_id = xact->id;
    ogs_otel_span_set_str(sess->pfcp_span, "5gs.pfcp.message", name);

    /* trace context to the UPF (enterprise-specific IE, lib/pfcp/xact.c) */
    ogs_assert(sizeof(xact->otel_traceparent) >= OGS_OTEL_TRACEPARENT_LEN);
    if (!ogs_otel_span_traceparent(sess->pfcp_span, xact->otel_traceparent))
        xact->otel_traceparent[0] = 0;
    ogs_otel_span_set_int(sess->pfcp_span, "5gs.pfcp.xid", xact->xid);
}

void smf_otel_pfcp_end(smf_sess_t *sess, ogs_pfcp_xact_t *xact, uint8_t cause)
{
    ogs_otel_span_t *span = NULL;
    char desc[48];

    ogs_assert(sess);
    ogs_assert(xact);

    if (!sess->pfcp_span || sess->pfcp_span_xact_id != xact->id)
        return;

    span = sess->pfcp_span;
    sess->pfcp_span = NULL;

    if (cause == OGS_PFCP_CAUSE_REQUEST_ACCEPTED) {
        ogs_otel_span_set_int(span, "5gs.pfcp.cause", cause);
        ogs_otel_span_end(span, OGS_OTEL_STATUS_UNSET, NULL);
    } else if (cause) {
        ogs_otel_span_set_int(span, "5gs.pfcp.cause", cause);
        ogs_snprintf(desc, sizeof(desc), "PFCP cause %d", cause);
        ogs_otel_span_set_str(span, "error.type", "pfcp_rejected");
        ogs_otel_span_end(span, OGS_OTEL_STATUS_ERROR, desc);
    } else {
        ogs_otel_span_set_str(span, "error.type", "no_response");
        ogs_otel_span_end(span, OGS_OTEL_STATUS_ERROR, "no PFCP response");
    }
}

void smf_otel_pfcp_retransmit(smf_sess_t *sess,
        ogs_pfcp_xact_t *xact, int retransmission)
{
    char name[48];

    ogs_assert(sess);
    ogs_assert(xact);

    if (!sess->pfcp_span || sess->pfcp_span_xact_id != xact->id)
        return;

    ogs_snprintf(name, sizeof(name), "pfcp_retransmission_%d", retransmission);
    ogs_otel_span_add_event(sess->pfcp_span, name);
    ogs_otel_span_set_int(sess->pfcp_span,
            "5gs.pfcp.retransmissions", retransmission);
}

ogs_otel_span_t *smf_otel_sbi_parent(smf_sess_t *sess)
{
    ogs_assert(sess);
    return sess->update_span ? sess->update_span : sess->establishment_span;
}
