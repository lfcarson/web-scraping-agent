import asyncio
import json
import os
import re
import sys

import httpx
from openai import AsyncOpenAI

# Allow importing scrapling from parent directory when running locally
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_EXTRACTION_PROMPT = """You are a web scraping expert. Extract structured data from the HTML below.

Fields to extract: {fields}
{instructions_block}
Rules:
- Extract ALL matching records (e.g. every product, every row, every listing).
- Return a JSON object with EXACTLY these keys:
  - "columns": array of column names matching the requested fields
  - "data": array of row objects keyed by column name (use null for missing values)
  - "count": integer — total rows extracted
  - "notes": one sentence describing what was found (e.g. "Found 24 products.")
- If nothing relevant exists in the HTML, return data: [] and explain in notes.
- Return ONLY valid JSON. No markdown fences, no extra text.

HTML:
{html}"""


def _clean_html(html: str, max_chars: int = 45000) -> str:
    """Strip scripts/styles/SVG and truncate to keep prompt size manageable."""
    html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<svg[^>]*>.*?</svg>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)
    if len(html) > max_chars:
        html = html[:max_chars] + "\n<!-- [truncated] -->"
    return html


class ScraperTool:
    def __init__(self):
        self.llm = AsyncOpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=os.environ["OPENROUTER_API_KEY"],
        )
        self.extraction_model = os.getenv(
            "EXTRACTION_MODEL",
            os.getenv("OPENROUTER_MODEL", "anthropic/claude-3.5-sonnet"),
        )

    async def check_url(self, url: str) -> dict:
        try:
            async with httpx.AsyncClient(
                timeout=15,
                follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0 (compatible; ScrapingAgent/1.0)"},
            ) as client:
                resp = await client.head(url)
                return {
                    "accessible": resp.status_code < 400,
                    "status_code": resp.status_code,
                    "url": str(resp.url),
                    "content_type": resp.headers.get("content-type", "unknown"),
                }
        except httpx.TimeoutException:
            return {"accessible": False, "error": "Request timed out", "url": url}
        except Exception as e:
            return {"accessible": False, "error": str(e), "url": url}

    async def fetch_and_extract(self, url: str, fields: list[str], instructions: str = "") -> dict:
        html = await self._fetch_html(url)
        if html is None:
            return {"error": "Failed to fetch page — site may be blocking requests or require login.", "data": [], "columns": fields}

        cleaned = _clean_html(html)
        instructions_block = f"Extra instructions: {instructions}\n" if instructions else ""
        prompt = _EXTRACTION_PROMPT.format(
            fields=", ".join(f'"{f}"' for f in fields),
            instructions_block=instructions_block,
            html=cleaned,
        )

        try:
            resp = await self.llm.chat.completions.create(
                model=self.extraction_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
            )
            raw = resp.choices[0].message.content or ""
            # Strip markdown fences if present
            raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
            raw = re.sub(r"\s*```$", "", raw)
            result = json.loads(raw)
        except json.JSONDecodeError as e:
            return {"error": f"LLM returned invalid JSON: {e}", "data": [], "columns": fields}
        except Exception as e:
            return {"error": str(e), "data": [], "columns": fields}

        result.setdefault("data", [])
        result.setdefault("columns", fields)
        result.setdefault("count", len(result["data"]))
        return result

    async def _fetch_html(self, url: str) -> str | None:
        # Try Scrapling first (better bot-detection bypass)
        try:
            from scrapling.fetchers import AsyncFetcher
            fetcher = AsyncFetcher(auto_match=False)
            response = await fetcher.get(url, stealthy_headers=True, timeout=30)
            if response is not None:
                html = str(response.html_content)
                if html and len(html) > 100:
                    return html
        except Exception:
            pass

        # Fallback: plain httpx request
        try:
            async with httpx.AsyncClient(
                timeout=30,
                follow_redirects=True,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"
                    ),
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.5",
                },
            ) as client:
                resp = await client.get(url)
                if resp.status_code < 400:
                    return resp.text
        except Exception:
            pass

        return None
