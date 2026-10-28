"""Meta Model API client (Responses endpoint) behind the budget guard, plus a scripted mock.

Both backends speak the same JSON shapes, so the agent loop cannot tell them apart.
"""

import json
import os
import re
import time
from pathlib import Path

import httpx

from .budget import DEFAULT_IMAGE_TOKENS, Ledger, Usage

API = "https://api.meta.ai/v1"
ROOT = Path(__file__).resolve().parent.parent
RETRY_STATUS = {429, 500, 502, 503, 504}


def api_key():
    key = os.environ.get("MODEL_API_KEY")
    if not key and (ROOT / ".env").exists():
        for line in (ROOT / ".env").read_text().splitlines():
            name, _, value = line.partition("=")
            if name.strip() == "MODEL_API_KEY":
                key = value.strip().strip('"').strip("'")
    if not key:
        raise RuntimeError("MODEL_API_KEY is not set (env or .env)")
    return key


def estimate_tokens(value):
    """Rough count of the new input in a request: ~3 chars per token, fixed cost per image."""
    if isinstance(value, dict):
        if value.get("type") in ("input_image", "computer_screenshot"):
            return DEFAULT_IMAGE_TOKENS
        return sum(estimate_tokens(v) for v in value.values())
    if isinstance(value, list):
        return sum(estimate_tokens(v) for v in value)
    if isinstance(value, str):
        return 0 if value.startswith("data:") else len(value) // 3 + 1
    return 0


class HttpBackend:
    mock = False

    def __init__(self, base_url=API, timeout=300.0, tries=5):
        self.http = httpx.Client(base_url=base_url, timeout=timeout,
                                 headers={"Authorization": f"Bearer {api_key()}"})
        self.tries = tries

    def _send(self, method, path, **kw):
        for attempt in range(self.tries):
            r = self.http.request(method, path, **kw)
            # Participants report transient 404 model_not_found on valid models: retry those too.
            transient = r.status_code in RETRY_STATUS or (
                r.status_code == 404 and "model_not_found" in r.text)
            if not transient or attempt == self.tries - 1:
                break
            time.sleep(min(2 ** attempt, 20))
        if r.is_error:
            raise RuntimeError(f"{method} {path} -> {r.status_code}: {r.text[:500]}")
        return r.json()

    def create(self, payload):
        return self._send("POST", "/responses", json=payload)

    def upload(self, path, expires_days=7):
        with open(path, "rb") as f:
            return self._send("POST", "/files", data={
                "purpose": "user_data", "expires_after[anchor]": "created_at",
                "expires_after[seconds]": str(expires_days * 86400)},
                files={"file": (Path(path).name, f)})


class MetaClient:
    """Prices every request before sending it and records what it really cost."""

    def __init__(self, backend, ledger: Ledger):
        self.backend = backend
        self.ledger = ledger
        self._context = {}  # response id -> tokens the server will replay on a chained call

    def create(self, payload, tag="", extra_input_tokens=0):
        """`extra_input_tokens` covers inputs the text estimate cannot see, such as a video."""
        payload = {"max_output_tokens": 4096, **payload}
        model = payload["model"]
        est = estimate_tokens(payload.get("input")) + estimate_tokens(payload.get("instructions"))
        est += extra_input_tokens
        est += self._context.get(payload.get("previous_response_id"), 0)
        if not self.backend.mock:
            self.ledger.check(model, est, payload["max_output_tokens"])
        response = self.backend.create(payload)
        usage = Usage.from_response(response.get("usage"))
        self._context[response["id"]] = usage.input_tokens + usage.output_tokens
        self.ledger.record(model, usage, mock=self.backend.mock, tag=tag,
                           response_id=response["id"], est_input_tokens=est)
        return response


def output_items(response, kind):
    return [item for item in response.get("output", []) if item.get("type") == kind]


def output_text(response):
    return "\n".join(part.get("text", "") for item in output_items(response, "message")
                     for part in item.get("content", []) if part.get("type") == "output_text")


class ScriptedBackend:
    """Replays a hand-written computer-use trajectory per tutorial step, without the network.

    `scripts` maps a step id to a list of attempts; an attempt is a list of turns, and a turn
    is either {"actions": [...]} (becomes a computer_call) or {"say": "..."} (ends the turn).
    The step is read from the "STEP <id>" marker that the agent puts in every step prompt,
    exactly as the real model would read it.
    """

    mock = True

    def __init__(self, scripts, latency=0.3):
        self.scripts = scripts
        self.latency = latency
        self._state = {}  # response id -> (step, attempt, next turn, context tokens)
        self._n = 0

    def create(self, payload):
        time.sleep(self.latency)
        prev = self._state.get(payload.get("previous_response_id"))
        items = payload.get("input", [])
        is_tool_result = any(i.get("type") == "computer_call_output" for i in items)
        if prev and is_tool_result:
            step, attempt, turn, ctx = prev
        else:
            step = re.search(r"STEP ([\w-]+)", json.dumps(items)).group(1)
            same = prev and prev[0] == step
            attempt, turn, ctx = (prev[1] + 1 if same else 0), 0, (prev[3] if prev else 0)
        attempts = self.scripts.get(step, [[{"say": f"I have no script for step {step}."}]])
        turns = attempts[min(attempt, len(attempts) - 1)]
        spec = turns[turn] if turn < len(turns) else {"say": "Done."}

        self._n += 1
        rid, call_id = f"resp_mock_{self._n}", f"call_mock_{self._n}"
        if "actions" in spec:
            output = [{"type": "computer_call", "id": f"cc_mock_{self._n}", "call_id": call_id,
                       "actions": spec["actions"], "pending_safety_checks": [],
                       "status": "completed"}]
        else:
            output = [{"type": "message", "role": "assistant",
                       "content": [{"type": "output_text", "text": spec["say"]}]}]
        ctx += estimate_tokens(items) + 120
        self._state[rid] = (step, attempt, turn + 1, ctx)
        return {"id": rid, "status": "completed", "output": output,
                "usage": {"input_tokens": ctx, "output_tokens": 120,
                          "input_tokens_details": {"cached_tokens": max(ctx - 2600, 0)}}}
