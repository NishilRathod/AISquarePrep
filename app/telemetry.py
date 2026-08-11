"""Tracing setup, and the tracer the hand-written spans use.

Off by default. Tracing turns on only when ``OTEL_EXPORTER_OTLP_ENDPOINT`` names
somewhere to send spans to; with it unset no ``TracerProvider`` is installed, the
API's no-op provider stands in, and every ``start_as_current_span`` below costs
an attribute lookup. That means the manual spans can be written inline in the
service code without guarding each one on a feature flag.

Configuration is read from the environment rather than through ``Settings`` for
the same reason ``app.main._cors_origins`` does: ``create_app()`` runs at import
time and ``Settings.openweather_api_key`` has no default, so reaching for
``get_settings()`` here would make importing ``app.main`` fail anywhere without
a .env.
"""

from __future__ import annotations

import logging
import os

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

logger = logging.getLogger(__name__)

DEFAULT_SERVICE_NAME = "weather-cache-service"

# Resolved lazily against whatever provider is global at the time a span starts,
# so importing this before (or without) configure_tracing is fine.
tracer = trace.get_tracer(DEFAULT_SERVICE_NAME)


def configure_tracing(app) -> bool:
    """Install the tracer provider and instrument the app. True if tracing is on.

    Must be called before the app serves its first request: ``instrument_app``
    adds ASGI middleware, and Starlette refuses new middleware once the
    middleware stack is built. That rules out doing this in ``lifespan``.
    """
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        return False

    provider = TracerProvider(
        resource=Resource.create(
            {"service.name": os.getenv("OTEL_SERVICE_NAME", DEFAULT_SERVICE_NAME)}
        )
    )
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{endpoint.rstrip('/')}/v1/traces"))
    )
    trace.set_tracer_provider(provider)

    FastAPIInstrumentor.instrument_app(app)
    # Both are global monkeypatches rather than per-instance, which is what makes
    # the sweep's httpx calls and Redis writes appear without threading a tracer
    # down through the client constructors.
    HTTPXClientInstrumentor().instrument()
    RedisInstrumentor().instrument()

    logger.info("Tracing enabled, exporting to %s", endpoint)
    return True
