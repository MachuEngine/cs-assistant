from fastapi import FastAPI

app = FastAPI(title="CS 티켓 어시스턴트")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
