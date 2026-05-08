import json
import os

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()

app = FastAPI(title="Web Scraping Agent")

AVAILABLE_MODELS = [
    {"id": "anthropic/claude-3.5-sonnet",        "label": "Claude 3.5 Sonnet",   "provider": "Anthropic"},
    {"id": "anthropic/claude-3.5-haiku",          "label": "Claude 3.5 Haiku",    "provider": "Anthropic"},
    {"id": "anthropic/claude-3-opus",             "label": "Claude 3 Opus",       "provider": "Anthropic"},
    {"id": "openai/gpt-4o",                       "label": "GPT-4o",              "provider": "OpenAI"},
    {"id": "openai/gpt-4o-mini",                  "label": "GPT-4o Mini",         "provider": "OpenAI"},
    {"id": "google/gemini-flash-1.5",             "label": "Gemini Flash 1.5",    "provider": "Google"},
    {"id": "meta-llama/llama-3.1-70b-instruct",   "label": "Llama 3.1 70B",       "provider": "Meta"},
]
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

    model = body.get("model") or None

    from agent import ScrapingAgent
    agent = ScrapingAgent(model=model)

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


@app.get("/api/models")
async def get_models():
    default = os.getenv("OPENROUTER_MODEL", "anthropic/claude-3.5-sonnet")
    return {"default": default, "models": AVAILABLE_MODELS}


@app.get("/health")
async def health():
    return {"status": "ok"}
