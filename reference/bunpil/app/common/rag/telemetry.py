from chromadb.telemetry.product import ProductTelemetryClient, ProductTelemetryEvent
from overrides import override


class NoOpProductTelemetry(ProductTelemetryClient):
    """Discard Chroma product telemetry events at the source."""

    @override
    def capture(self, event: ProductTelemetryEvent) -> None:
        return None
