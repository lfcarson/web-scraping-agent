import io
import json
import os

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()

app = FastAPI(title="Web Scraping Agent")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")), name="static")


@app.get("/")
async def root():
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "index.html"))


@app.post("/api/chat")
async def chat(request: Request):
    body = await request.json()
    messages = body.get("messages", [])

    from agent import ScrapingAgent
    agent = ScrapingAgent()

    async def generate():
        try:
            async for event in agent.run(messages):
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'RUN_ERROR', 'error': str(e)})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.post("/api/export")
async def export_data(request: Request):
    body = await request.json()
    data = body.get("data", [])
    columns = body.get("columns", [])
    filename = body.get("filename", "scraped_data.xlsx")

    from exporter import create_excel_bytes
    excel_bytes = create_excel_bytes(data, columns)

    return Response(
        content=excel_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/health")
async def health():
    return {"status": "ok"}
