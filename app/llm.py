"""Talks to the LLM. Two adapters behind one interface:

- AnthropicLLM: Claude via the Messages API.
- OpenAICompatLLM: anything that speaks the OpenAI Chat Completions format with tool calling.

The agent keeps one neutral transcript; each adapter converts it to its own format on every call:
  {"role": "user", "text": ...}
  {"role": "assistant", "text": ..., "tool_calls": [{"id", "name", "input"}]}
  {"role": "tool_results", "results": [{"id", "name", "content", "is_error"}]}
"""
import json
import re
from dataclasses import dataclass, field

import httpx

from . import config


class LLMError(RuntimeError):
    pass


@dataclass
class LLMReply:
    text: str = ""
    tool_calls: list = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0


def _error_message(resp: httpx.Response) -> str:
    try:
        body = resp.json()
        err = body.get("error", body)
        msg = err.get("message") if isinstance(err, dict) else str(err)
    except Exception:
        msg = resp.text[:300]
    hints = {401: "The API key was rejected. Check LLM_API_KEY.", 404: "Model not found. Check LLM_MODEL.",
             429: "Rate limit or quota reached on the LLM account."}
    return f"{hints.get(resp.status_code, 'LLM request failed')} ({resp.status_code}: {msg})"


def _parse_json(text: str):
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1)
    start = min([i for i in (text.find("{"), text.find("[")) if i >= 0], default=-1)
    end = max(text.rfind("}"), text.rfind("]"))
    if start < 0 or end < start:
        raise LLMError("The model did not return JSON.")
    return json.loads(text[start:end + 1])


class BaseLLM:
    provider = ""

    def __init__(self, api_key, model, base_url, timeout=config.LLM_TIMEOUT, transport=None):
        self.api_key, self.model, self.base_url = api_key, model, base_url
        self.http = httpx.Client(timeout=timeout, transport=transport)

    def chat(self, system, transcript, tools, force_tools=True, max_tokens=1024) -> LLMReply:
        raise NotImplementedError

    def complete_json(self, prompt, max_tokens=600):
        reply = self.chat("Reply with JSON only.", [{"role": "user", "text": prompt}], tools=[], force_tools=False,
                          max_tokens=max_tokens)
        return _parse_json(reply.text)


class AnthropicLLM(BaseLLM):
    provider = "anthropic"

    def _messages(self, transcript):
        out = []
        for t in transcript:
            if t["role"] == "user":
                out.append({"role": "user", "content": t["text"]})
            elif t["role"] == "assistant":
                blocks = [{"type": "text", "text": t["text"]}] if t.get("text") else []
                blocks += [{"type": "tool_use", "id": c["id"], "name": c["name"], "input": c["input"]}
                           for c in t.get("tool_calls", [])]
                out.append({"role": "assistant", "content": blocks or [{"type": "text", "text": "..."}]})
            elif t["role"] == "tool_results":
                out.append({"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": r["id"], "content": r["content"], "is_error": r.get("is_error", False)}
                    for r in t["results"]]})
        return out

    def chat(self, system, transcript, tools, force_tools=True, max_tokens=1024):
        body = {"model": self.model, "max_tokens": max_tokens, "system": system, "messages": self._messages(transcript)}
        if tools:
            body["tools"] = [{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]}
                             for t in tools]
            body["tool_choice"] = {"type": "any"} if force_tools else {"type": "auto"}
        try:
            resp = self.http.post(f"{self.base_url}/v1/messages", json=body, headers={
                "x-api-key": self.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
        except httpx.HTTPError as e:
            raise LLMError(f"Couldn't reach the LLM: {e}") from e
        if resp.status_code != 200:
            raise LLMError(_error_message(resp))
        data = resp.json()
        reply = LLMReply(input_tokens=data.get("usage", {}).get("input_tokens", 0),
                         output_tokens=data.get("usage", {}).get("output_tokens", 0))
        for block in data.get("content", []):
            if block.get("type") == "text":
                reply.text += block.get("text", "")
            elif block.get("type") == "tool_use":
                reply.tool_calls.append({"id": block["id"], "name": block["name"], "input": block.get("input") or {}})
        return reply


class OpenAICompatLLM(BaseLLM):
    provider = "openai"

    def _messages(self, system, transcript):
        out = [{"role": "system", "content": system}]
        for t in transcript:
            if t["role"] == "user":
                out.append({"role": "user", "content": t["text"]})
            elif t["role"] == "assistant":
                msg = {"role": "assistant", "content": t.get("text") or None}
                if t.get("tool_calls"):
                    msg["tool_calls"] = [{"id": c["id"], "type": "function",
                                          "function": {"name": c["name"], "arguments": json.dumps(c["input"])}}
                                         for c in t["tool_calls"]]
                out.append(msg)
            elif t["role"] == "tool_results":
                for r in t["results"]:
                    out.append({"role": "tool", "tool_call_id": r["id"], "content": r["content"]})
        return out

    def chat(self, system, transcript, tools, force_tools=True, max_tokens=1024):
        body = {"model": self.model, "messages": self._messages(system, transcript)}
        if tools:
            body["tools"] = [{"type": "function", "function": t} for t in tools]
            body["tool_choice"] = "required" if force_tools else "auto"
        try:
            resp = self.http.post(f"{self.base_url}/chat/completions", json=body,
                                  headers={"Authorization": f"Bearer {self.api_key}"})
        except httpx.HTTPError as e:
            raise LLMError(f"Couldn't reach the LLM: {e}") from e
        if resp.status_code != 200:
            raise LLMError(_error_message(resp))
        data = resp.json()
        try:
            msg = data["choices"][0]["message"]
        except (KeyError, IndexError) as e:
            raise LLMError("Unexpected reply from the LLM.") from e
        usage = data.get("usage") or {}
        reply = LLMReply(text=msg.get("content") or "", input_tokens=usage.get("prompt_tokens", 0),
                         output_tokens=usage.get("completion_tokens", 0))
        for call in msg.get("tool_calls") or []:
            fn = call.get("function", {})
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            reply.tool_calls.append({"id": call.get("id", ""), "name": fn.get("name", ""), "input": args})
        return reply


_override = None
_instance = None


def get_llm():
    """The configured LLM, or None when no key is set (the app then runs in retrieval-only mode)."""
    global _instance
    if _override is not None:
        return _override
    if not config.LLM_API_KEY:
        return None
    if _instance is None:
        cls = AnthropicLLM if config.LLM_PROVIDER == "anthropic" else OpenAICompatLLM
        _instance = cls(config.LLM_API_KEY, config.LLM_MODEL, config.LLM_BASE_URL)
    return _instance


def set_llm_for_tests(llm):
    global _override
    _override = llm
