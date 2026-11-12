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

/*
 * opentelemetry-cpp backed implementation of ogs-otel.h (traces only).
 * Every entry point is noexcept: exceptions never cross into C code.
 */

#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <new>
#include <string>

#include "opentelemetry/exporters/otlp/otlp_http_exporter_factory.h"
#include "opentelemetry/exporters/otlp/otlp_http_exporter_options.h"
#include "opentelemetry/sdk/resource/resource.h"
#include "opentelemetry/sdk/trace/batch_span_processor_factory.h"
#include "opentelemetry/sdk/trace/batch_span_processor_options.h"
#include "opentelemetry/sdk/trace/tracer_provider.h"
#include "opentelemetry/trace/span.h"
#include "opentelemetry/trace/tracer.h"

#include "ogs-otel.h"

namespace nostd = opentelemetry::nostd;
namespace otlp = opentelemetry::exporter::otlp;
namespace resource = opentelemetry::sdk::resource;
namespace trace_api = opentelemetry::trace;
namespace trace_sdk = opentelemetry::sdk::trace;

#define OGS_OTEL_SCOPE_NAME "open5gs"

struct ogs_otel_span_s {
    nostd::shared_ptr<trace_api::Span> span;
    char trace_id[2 * trace_api::TraceId::kSize + 1];
    char span_id[2 * trace_api::SpanId::kSize + 1];
};

static std::shared_ptr<trace_sdk::TracerProvider> provider;
static nostd::shared_ptr<trace_api::Tracer> tracer;

static bool env_is_set(const char *name)
{
    const char *value = std::getenv(name);
    return value && value[0];
}

int ogs_otel_init(const char *service_name, const char *service_version)
{
    try {
        const char *disabled = std::getenv("OTEL_SDK_DISABLED");

        if (disabled && !strcmp(disabled, "true"))
            return 0;
        if (!env_is_set("OTEL_EXPORTER_OTLP_ENDPOINT") &&
            !env_is_set("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"))
            return 0;

        /* Defaults read the OTEL_EXPORTER_OTLP_* environment variables */
        otlp::OtlpHttpExporterOptions exporter_options;
        exporter_options.content_type = otlp::HttpRequestContentType::kBinary;

        trace_sdk::BatchSpanProcessorOptions processor_options;
        processor_options.max_queue_size = 2048;
        processor_options.max_export_batch_size = 256;
        processor_options.schedule_delay_millis =
            std::chrono::milliseconds(1000);

        auto processor = trace_sdk::BatchSpanProcessorFactory::Create(
                otlp::OtlpHttpExporterFactory::Create(exporter_options),
                processor_options);

        /* Explicit attributes win over OTEL_RESOURCE_ATTRIBUTES */
        resource::ResourceAttributes attributes;
        if (!env_is_set("OTEL_SERVICE_NAME") && service_name)
            attributes.SetAttribute("service.name", service_name);
        if (service_version)
            attributes.SetAttribute("service.version", service_version);
        attributes.SetAttribute("service.namespace", "open5gs");

        provider = std::make_shared<trace_sdk::TracerProvider>(
                std::move(processor), resource::Resource::Create(attributes));
        tracer = provider->GetTracer(OGS_OTEL_SCOPE_NAME,
                service_version ? service_version : "");

        std::fprintf(stderr, "[otel] tracing enabled [service:%s]\n",
                service_name ? service_name : "");
        return 0;
    } catch (const std::exception &e) {
        std::fprintf(stderr, "[otel] init failed: %s\n", e.what());
    } catch (...) {
        std::fprintf(stderr, "[otel] init failed\n");
    }

    tracer = nullptr;
    provider = nullptr;
    return -1;
}

void ogs_otel_final(void)
{
    try {
        if (provider) {
            provider->ForceFlush(std::chrono::milliseconds(2000));
            provider->Shutdown();
        }
    } catch (...) {
    }
    tracer = nullptr;
    provider = nullptr;
}

/* Lower-case/upper-case hex to bytes; false on any invalid character */
static bool hex_to_bytes(const char *hex, size_t nbytes, uint8_t *out)
{
    for (size_t i = 0; i < 2 * nbytes; i++) {
        char c = hex[i];
        int v;
        if (c >= '0' && c <= '9') v = c - '0';
        else if (c >= 'a' && c <= 'f') v = c - 'a' + 10;
        else if (c >= 'A' && c <= 'F') v = c - 'A' + 10;
        else return false;
        if (i % 2 == 0) out[i / 2] = (uint8_t)(v << 4);
        else out[i / 2] |= (uint8_t)v;
    }
    return true;
}

static bool is_zero(const uint8_t *b, size_t n)
{
    for (size_t i = 0; i < n; i++)
        if (b[i]) return false;
    return true;
}

/* "00-<32 hex>-<16 hex>-<2 hex>" (version 00 only) */
static bool parse_traceparent(const char *tp,
        uint8_t trace_id[16], uint8_t span_id[8], uint8_t *flags)
{
    if (!tp || std::strlen(tp) < 55)
        return false;
    if (tp[0] != '0' || tp[1] != '0' || tp[2] != '-' ||
        tp[35] != '-' || tp[52] != '-')
        return false;
    if (!hex_to_bytes(tp + 3, 16, trace_id) ||
        !hex_to_bytes(tp + 36, 8, span_id) ||
        !hex_to_bytes(tp + 53, 1, flags))
        return false;
    return !is_zero(trace_id, 16) && !is_zero(span_id, 8);
}

static void fill_ids(ogs_otel_span_t *span)
{
    trace_api::SpanContext context = span->span->GetContext();
    context.trace_id().ToLowerBase16(
        nostd::span<char, 2 * trace_api::TraceId::kSize>(
            span->trace_id, 2 * trace_api::TraceId::kSize));
    context.span_id().ToLowerBase16(
        nostd::span<char, 2 * trace_api::SpanId::kSize>(
            span->span_id, 2 * trace_api::SpanId::kSize));
    span->trace_id[2 * trace_api::TraceId::kSize] = '\0';
    span->span_id[2 * trace_api::SpanId::kSize] = '\0';
}

ogs_otel_span_t *ogs_otel_span_start(const char *name)
{
    /* Always a new trace: procedures are independent root spans */
    return ogs_otel_span_start_ex(name, OGS_OTEL_KIND_INTERNAL,
            NULL, NULL, NULL, NULL);
}

ogs_otel_span_t *ogs_otel_span_start_ex(const char *name,
        ogs_otel_kind_e kind, ogs_otel_span_t *parent,
        const char *traceparent,
        const char *link_trace_id, const char *link_span_id)
{
    if (!tracer || !name)
        return NULL;

    try {
        trace_api::StartSpanOptions options;
        uint8_t tid[16], sid[8], flags = 0;

        switch (kind) {
        case OGS_OTEL_KIND_SERVER:
            options.kind = trace_api::SpanKind::kServer;
            break;
        case OGS_OTEL_KIND_CLIENT:
            options.kind = trace_api::SpanKind::kClient;
            break;
        default:
            options.kind = trace_api::SpanKind::kInternal;
        }

        options.parent = trace_api::SpanContext::GetInvalid();
        if (parent && parent->span) {
            options.parent = parent->span->GetContext();
        } else if (parse_traceparent(traceparent, tid, sid, &flags)) {
            options.parent = trace_api::SpanContext(
                    trace_api::TraceId(nostd::span<const uint8_t, 16>(tid, 16)),
                    trace_api::SpanId(nostd::span<const uint8_t, 8>(sid, 8)),
                    trace_api::TraceFlags(flags), true);
        }

        ogs_otel_span_t *span = new ogs_otel_span_t();

        if (link_trace_id && link_span_id &&
            std::strlen(link_trace_id) >= 32 && std::strlen(link_span_id) >= 16 &&
            hex_to_bytes(link_trace_id, 16, tid) &&
            hex_to_bytes(link_span_id, 8, sid) &&
            !is_zero(tid, 16) && !is_zero(sid, 8)) {
            using attrs_t = std::initializer_list<
                std::pair<nostd::string_view, opentelemetry::common::AttributeValue>>;
            trace_api::SpanContext link(
                    trace_api::TraceId(nostd::span<const uint8_t, 16>(tid, 16)),
                    trace_api::SpanId(nostd::span<const uint8_t, 8>(sid, 8)),
                    trace_api::TraceFlags(trace_api::TraceFlags::kIsSampled),
                    true);
            attrs_t no_attributes = {};
            std::initializer_list<std::pair<trace_api::SpanContext, attrs_t>>
                links = {{link, attrs_t{}}};
            span->span = tracer->StartSpan(name, no_attributes, links, options);
        } else {
            span->span = tracer->StartSpan(name, options);
        }

        fill_ids(span);
        return span;
    } catch (...) {
        return NULL;
    }
}

void ogs_otel_span_set_str(ogs_otel_span_t *span,
        const char *key, const char *value)
{
    if (!span || !key || !value)
        return;
    try {
        span->span->SetAttribute(key, nostd::string_view(value));
    } catch (...) {
    }
}

void ogs_otel_span_set_int(ogs_otel_span_t *span,
        const char *key, int64_t value)
{
    if (!span || !key)
        return;
    try {
        span->span->SetAttribute(key, value);
    } catch (...) {
    }
}

void ogs_otel_span_add_event(ogs_otel_span_t *span, const char *name)
{
    if (!span || !name)
        return;
    try {
        span->span->AddEvent(name);
    } catch (...) {
    }
}

void ogs_otel_span_end(ogs_otel_span_t *span,
        ogs_otel_status_e status, const char *description)
{
    if (!span)
        return;
    try {
        if (status == OGS_OTEL_STATUS_OK)
            span->span->SetStatus(trace_api::StatusCode::kOk);
        else if (status == OGS_OTEL_STATUS_ERROR)
            span->span->SetStatus(trace_api::StatusCode::kError,
                    description ? description : "");
        span->span->End();
    } catch (...) {
    }
    delete span;
}

const char *ogs_otel_span_trace_id(ogs_otel_span_t *span)
{
    return span ? span->trace_id : NULL;
}

const char *ogs_otel_span_span_id(ogs_otel_span_t *span)
{
    return span ? span->span_id : NULL;
}
