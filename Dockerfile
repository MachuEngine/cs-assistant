# FastAPI 백엔드. DESIGN.md 13절(배포) — Next.js는 별도 컨테이너(frontend/Dockerfile).
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# app/ 코드, prompts/(버전관리 대상, 런타임에 pathlib로 읽음), scripts/(entrypoint가
# 합성 데이터/RAG 인덱스를 만드는 데 씀), data/synthetic/policies/(정책 문서 원본,
# git 추적 대상 — *.db·tickets.jsonl은 생성물이라 .dockerignore로 제외하고
# entrypoint가 컨테이너 안에서 만든다)를 복사한다.
COPY app/ app/
COPY prompts/ prompts/
COPY scripts/ scripts/
COPY data/synthetic/policies/ data/synthetic/policies/
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
