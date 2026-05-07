import json
import os
import uuid
from typing import AsyncGenerator

from openai import AsyncOpenAI

SYSTEM_PROMPT = """You are an expert web scraping assistant. Your job is to help users extract structured data from websites.

When a user gives you a request:
1. If the URL is missing or ambiguous, ask for it clearly.
2. If the fields to extract are vague, ask for clarification (e.g. "Which specific fields: title, price, rating?").
3. Once you have a clear URL and field list, call check_url to verify accessibility, then fetch_and_extract to scrape.
4. Summarize what was extracted and how many records were found.
5. Let the user know they can export the data to Excel.

If a site is blocked or login-required, explain and suggest alternatives (e.g. using a different URL, API, or public dataset).
Be concise, professional, and helpful. Do not make up data — only report what was actually extracted."""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "check_url",
            "description": "Check whether a URL is accessible and retrieve basic page info (title, status code). Call this before scraping to confirm the page can be reached.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The full URL to check (must include http:// or https://)",
                    }
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_and_extract",
            "description": "Fetch a webpage and extract structured data fields from it using AI. Returns rows of data with the requested fields.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The full URL to scrape",
                    },
                    "fields": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of data fields to extract (e.g. ['product name', 'price', 'rating', 'url'])",
                    },
                    "instructions": {
                        "type": "string",
                        "description": "Optional extra instructions (e.g. 'only extract in-stock items', 'limit to 20 results')",
                    },
                },
                "required": ["url", "fields"],
            },
        },
    },
]


class ScrapingAgent:
    def __init__(self):
        self.client = AsyncOpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=os.environ["OPENROUTER_API_KEY"],
        )
        self.model = os.getenv("OPENROUTER_MODEL", "anthropic/claude-3.5-sonnet")

    async def run(self, user_messages: list[dict]) -> AsyncGenerator[dict, None]:
        from scraper import ScraperTool
        scraper = ScraperTool()

        run_id = str(uuid.uuid4())
        yield {"type": "RUN_STARTED", "runId": run_id}

        full_messages = [{"role": "system", "content": SYSTEM_PROMPT}] + user_messages

        while True:
            msg_id = str(uuid.uuid4())
            yield {"type": "TEXT_MESSAGE_START", "messageId": msg_id, "role": "assistant"}

            full_text = ""
            tool_calls_map: dict[int, dict] = {}
            finish_reason = None

            stream = await self.client.chat.completions.create(
                model=self.model,
                messages=full_messages,
                tools=TOOLS,
                tool_choice="auto",
                stream=True,
            )

            async for chunk in stream:
                choice = chunk.choices[0]
                if choice.finish_reason:
                    finish_reason = choice.finish_reason

                delta = choice.delta

                if delta.content:
                    full_text += delta.content
                    yield {"type": "TEXT_MESSAGE_CONTENT", "messageId": msg_id, "delta": delta.content}

                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = tc.index
                        if idx not in tool_calls_map:
                            tool_calls_map[idx] = {
                                "id": tc.id or str(uuid.uuid4()),
                                "name": tc.function.name if tc.function and tc.function.name else "",
                                "args": "",
                            }
                            if tc.function and tc.function.name:
                                yield {
                                    "type": "TOOL_CALL_START",
                                    "toolCallId": tool_calls_map[idx]["id"],
                                    "toolCallName": tc.function.name,
                                }
                        if tc.function and tc.function.arguments:
                            tool_calls_map[idx]["args"] += tc.function.arguments
                            yield {
                                "type": "TOOL_CALL_ARGS_DELTA",
                                "toolCallId": tool_calls_map[idx]["id"],
                                "delta": tc.function.arguments,
                            }

            yield {"type": "TEXT_MESSAGE_END", "messageId": msg_id}

            if not tool_calls_map:
                full_messages.append({"role": "assistant", "content": full_text})
                break

            assistant_msg: dict = {
                "role": "assistant",
                "content": full_text or None,
                "tool_calls": [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {"name": tc["name"], "arguments": tc["args"]},
                    }
                    for tc in tool_calls_map.values()
                ],
            }
            full_messages.append(assistant_msg)

            for tc in tool_calls_map.values():
                tool_id = tc["id"]
                tool_name = tc["name"]
                try:
                    args = json.loads(tc["args"])
                except json.JSONDecodeError:
                    args = {}

                try:
                    if tool_name == "check_url":
                        result = await scraper.check_url(args.get("url", ""))
                    elif tool_name == "fetch_and_extract":
                        result = await scraper.fetch_and_extract(
                            url=args.get("url", ""),
                            fields=args.get("fields", []),
                            instructions=args.get("instructions", ""),
                        )
                        if result.get("data"):
                            yield {
                                "type": "DATA_UPDATE",
                                "data": result["data"],
                                "columns": result.get("columns", args.get("fields", [])),
                                "count": result.get("count", len(result["data"])),
                            }
                    else:
                        result = {"error": f"Unknown tool: {tool_name}"}

                    yield {"type": "TOOL_CALL_END", "toolCallId": tool_id, "result": result}

                except Exception as e:
                    result = {"error": str(e)}
                    yield {"type": "TOOL_CALL_END", "toolCallId": tool_id, "result": result}

                full_messages.append({
                    "role": "tool",
                    "tool_call_id": tool_id,
                    "content": json.dumps(result),
                })

        yield {"type": "RUN_FINISHED", "runId": run_id}
