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

/* No-op implementation used when built without -Dotel=true */

#include "ogs-otel.h"

int ogs_otel_init(const char *service_name, const char *service_version)
{
    (void)service_name;
    (void)service_version;
    return 0;
}

void ogs_otel_final(void)
{
}

ogs_otel_span_t *ogs_otel_span_start(const char *name)
{
    (void)name;
    return NULL;
}

ogs_otel_span_t *ogs_otel_span_start_ex(const char *name,
        ogs_otel_kind_e kind, ogs_otel_span_t *parent,
        const char *traceparent,
        const char *link_trace_id, const char *link_span_id)
{
    (void)name; (void)kind; (void)parent; (void)traceparent;
    (void)link_trace_id; (void)link_span_id;
    return NULL;
}

void ogs_otel_span_set_str(ogs_otel_span_t *span,
        const char *key, const char *value)
{
    (void)span; (void)key; (void)value;
}

void ogs_otel_span_set_int(ogs_otel_span_t *span,
        const char *key, int64_t value)
{
    (void)span; (void)key; (void)value;
}

void ogs_otel_span_add_event(ogs_otel_span_t *span, const char *name)
{
    (void)span; (void)name;
}

void ogs_otel_span_end(ogs_otel_span_t *span,
        ogs_otel_status_e status, const char *description)
{
    (void)span; (void)status; (void)description;
}

const char *ogs_otel_span_trace_id(ogs_otel_span_t *span)
{
    (void)span;
    return NULL;
}

const char *ogs_otel_span_span_id(ogs_otel_span_t *span)
{
    (void)span;
    return NULL;
}
