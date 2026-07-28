#!/bin/sh
# 컨테이너 시작 시 합성 데이터/RAG 인덱스가 없으면 만든다. 두 스크립트 모두
# 이미 존재하는 산출물은 건너뛰는 멱등 스크립트다(scripts/build_synthetic_data.py,
# scripts/index_policies.py 자체 구현 참고) — 매번 재생성하지 않는다.
# CHROMA_PERSIST_DIR/SHOP_DB_PATH가 볼륨에 매핑돼 있으면(docker-compose.yml)
# 컨테이너를 재시작해도 다시 계산하지 않는다.
set -e

echo "[entrypoint] 합성 주문/고객 DB 확인..."
python scripts/build_synthetic_data.py

echo "[entrypoint] 정책 문서 RAG 인덱스 확인..."
python scripts/index_policies.py

echo "[entrypoint] 애플리케이션 시작"
exec "$@"
