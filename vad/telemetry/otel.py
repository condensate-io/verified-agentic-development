from opentelemetry import trace
from opentelemetry import metrics
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, ConsoleSpanExporter
from opentelemetry.sdk.metrics.export import ConsoleMetricExporter, PeriodicExportingMetricReader

import logging

def setup_telemetry(service_name: str = "vad-service"):
    resource = Resource(attributes={"service.name": service_name})
    
    # Tracing
    tracer_provider = TracerProvider(resource=resource)
    trace.set_tracer_provider(tracer_provider)
    tracer_provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))

    # Metrics
    metric_reader = PeriodicExportingMetricReader(ConsoleMetricExporter())
    meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
    metrics.set_meter_provider(meter_provider)

    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)
    logger.info(f"Telemetry initialized for {service_name}.")

def get_tracer(module_name: str):
    return trace.get_tracer(module_name)

def get_meter(module_name: str):
    return metrics.get_meter(module_name)
