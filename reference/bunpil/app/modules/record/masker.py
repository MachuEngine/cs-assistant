"""하위 호환 import. 공통 개인정보 로직은 app.common.privacy에 있다."""

from app.common.privacy import mask_pii

__all__ = ["mask_pii"]
