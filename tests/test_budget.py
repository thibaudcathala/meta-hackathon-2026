import tempfile
import unittest
from pathlib import Path

from footfall.budget import BudgetExceeded, Ledger, Usage
from footfall.meta import MetaClient, ScriptedBackend


class NoNetwork:
    mock = False

    def create(self, payload):
        raise AssertionError("the guard let a paid call through")


def request(text="hello"):
    return {"model": "muse-wonder-1.0",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": text}]}]}


class BudgetTest(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "ledger.jsonl"

    def test_zero_budget_refuses_before_sending(self):
        client = MetaClient(NoNetwork(), Ledger(self.path, budget_usd=0))
        with self.assertRaises(BudgetExceeded):
            client.create(request())
        self.assertFalse(self.path.exists())

    def test_worst_case_counts_full_output_allowance(self):
        ledger = Ledger(self.path, budget_usd=1.05, margin_usd=1.0)
        with self.assertRaises(BudgetExceeded):  # 4096 output tokens at $20/M is $0.08
            MetaClient(NoNetwork(), ledger).create(request())

    def test_unknown_model_is_refused(self):
        with self.assertRaises(BudgetExceeded):
            Ledger(self.path, budget_usd=100).check("muse-image-1.0", 10, 10)

    def test_spend_adds_up_and_mock_is_free(self):
        ledger = Ledger(self.path, budget_usd=10, margin_usd=1)
        ledger.record("muse-wonder-1.0", Usage(1_000_000, 500_000, 100_000))
        ledger.record("muse-wonder-1.0", Usage(1_000_000, 0, 0), mock=True)
        self.assertAlmostEqual(ledger.spent(), 0.5 * 4 + 0.5 * 0.2 + 0.1 * 20)
        self.assertAlmostEqual(ledger.remaining(), 10 - 1 - 4.1)


class ScriptedBackendTest(unittest.TestCase):
    def test_turns_attempts_and_steps(self):
        backend = ScriptedBackend({"s1": [[{"actions": [{"type": "wait"}]}, {"say": "first"}],
                                          [{"say": "second"}]]}, latency=0)
        r1 = backend.create(request("STEP s1: do it"))
        self.assertEqual(r1["output"][0]["type"], "computer_call")
        r2 = backend.create({"previous_response_id": r1["id"], "input": [
            {"type": "computer_call_output", "call_id": r1["output"][0]["call_id"]}]})
        self.assertEqual(r2["output"][0]["content"][0]["text"], "first")
        r3 = backend.create({"previous_response_id": r2["id"], **request("STEP s1 failed")})
        self.assertEqual(r3["output"][0]["content"][0]["text"], "second")


if __name__ == "__main__":
    unittest.main()
