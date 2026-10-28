import io
import json
import tempfile
import unittest
from unittest import mock

from PIL import Image

from apprentice.agent import Apprentice, Run
from apprentice.meta import ApiError

LOST = '{"error":{"message":"referenced response not found or expired"}}'


def png():
    buf = io.BytesIO()
    Image.new("RGB", (8, 8)).save(buf, "PNG")
    return buf.getvalue()


class Desktop:
    def screenshot(self):
        return png()


class LosingClient:
    """Loses every chained response; answers fresh conversations."""

    def __init__(self):
        self.payloads = []

    def create(self, payload, tag=""):
        self.payloads.append(payload)
        if "previous_response_id" in payload:
            raise ApiError("400", 400, LOST)
        return {"id": "resp_fresh", "output": []}


class ContextLossTest(unittest.TestCase):
    def test_restarts_the_step_from_the_screen(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch("apprentice.agent.time.sleep"):
            client = LosingClient()
            agent = Apprentice(client, Desktop(), None, Run(tmp), say=lambda *_: None)
            agent.restart = [{"type": "input_text", "text": "STEP s2: add a sphere"}]
            response = agent.request([{"type": "function_call_output"}], previous="resp_old", tag="s2")
            self.assertEqual(response["id"], "resp_fresh")
            self.assertEqual(len(client.payloads), 4)  # three tries on the chain, then a fresh start
            fresh = client.payloads[-1]
            self.assertNotIn("previous_response_id", fresh)
            self.assertEqual(fresh["input"][0]["content"][0]["text"], "STEP s2: add a sphere")
            events = [json.loads(line) for line in open(f"{tmp}/events.jsonl")]
            self.assertIn("context_lost", [e["kind"] for e in events])

    def test_other_errors_propagate(self):
        class Broken(LosingClient):
            def create(self, payload, tag=""):
                raise ApiError("500", 500, "boom")
        with tempfile.TemporaryDirectory() as tmp:
            agent = Apprentice(Broken(), Desktop(), None, Run(tmp), say=lambda *_: None)
            with self.assertRaises(ApiError):
                agent.request([], previous="resp_old")


if __name__ == "__main__":
    unittest.main()
