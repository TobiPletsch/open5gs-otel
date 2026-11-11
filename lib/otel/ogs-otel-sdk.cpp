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

ogs_otel_span_t *ogs_otel_span_start(const char *name)
{
    if (!tracer || !name)
        return NULL;

    try {
        trace_api::StartSpanOptions options;
        options.kind = trace_api::SpanKind::kInternal;
        /* Always a new trace: procedures are independent root spans */
        options.parent = trace_api::SpanContext::GetInvalid();

        ogs_otel_span_t *span = new ogs_otel_span_t();
        span->span = tracer->StartSpan(name, options);

        trace_api::SpanContext context = span->span->GetContext();
        context.trace_id().ToLowerBase16(
            nostd::span<char, 2 * trace_api::TraceId::kSize>(
                span->trace_id, 2 * trace_api::TraceId::kSize));
        context.span_id().ToLowerBase16(
            nostd::span<char, 2 * trace_api::SpanId::kSize>(
                span->span_id, 2 * trace_api::SpanId::kSize));
        span->trace_id[2 * trace_api::TraceId::kSize] = '\0';
        span->span_id[2 * trace_api::SpanId::kSize] = '\0';

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
