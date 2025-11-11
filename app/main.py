from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from .config import OUTPUT_DIR, PROJECT_ID
from .routers import projects, assets, timeline

app = FastAPI(title="Google AI GenMedia Video Engine API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/output", StaticFiles(directory=OUTPUT_DIR), name="output")

app.include_router(projects.router)
app.include_router(assets.router)
app.include_router(timeline.router)

@app.get("/health")
async def health_check():
    return {"status": "ok", "project": PROJECT_ID}
