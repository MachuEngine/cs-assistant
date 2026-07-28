from chromadb.telemetry.product import ProductTelemetryClient, ProductTelemetryEvent
from overrides import override


class NoOpProductTelemetry(ProductTelemetryClient):
    """ChromaDB 제품 텔레메트리 이벤트를 폐기한다.

    ProductTelemetryClient가 EnforceOverrides 메타클래스를 쓰기 때문에
    @override 데코레이터 없이 capture()를 재정의하면 클래스 정의 시점에
    TypeError가 난다 — 선택적 스타일이 아니라 실제로 필요하다.
    """

    @override
    def capture(self, event: ProductTelemetryEvent) -> None:
        return None
