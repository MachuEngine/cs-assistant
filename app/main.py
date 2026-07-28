from dotenv import load_dotenv
from fastapi import FastAPI

# 다른 모듈이 os.getenv로 LLM_BACKEND/API 키 등을 읽기 전에 .env를 로드한다
# (app.common.llm 팩토리 등은 호출 시점에 지연 조회하므로 여기서 한 번만 하면 된다).
load_dotenv()

app = FastAPI(title="CS 티켓 어시스턴트")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
