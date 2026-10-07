import uvicorn
from fastapi import FastAPI

from risk_service.api import create_router, install_exception_handlers
from risk_service.data import build_initial_snapshot
from risk_service.store import SnapshotStore

app = FastAPI(title="portfolio-risk-service", version="0.1.0")

# 进程级单例存储；初始快照来自本地确定性模拟数据。
store = SnapshotStore(build_initial_snapshot())
app.include_router(create_router(store))
install_exception_handlers(app)


@app.get("/")
def read_root():
    return {"service": "portfolio-risk-service", "snapshot_version": store.get().version}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
