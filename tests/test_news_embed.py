#!/usr/bin/env python3
"""news_embed.py: gateway call shape (fallbacks off, model recorded) and vector formatting. No network, no DB.

    python3 tests/test_news_embed.py
"""
import io
import json
import os
import sys
import types
import unittest
from pathlib import Path

sys.modules.setdefault("psycopg", types.ModuleType("psycopg"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "manifests" / "llm"))
import news_embed as ne  # noqa: E402


class Resp(io.BytesIO):
    def __init__(self, body, headers):
        super().__init__(json.dumps(body).encode())
        self.headers = headers

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Embed(unittest.TestCase):
    def setUp(self):
        os.environ.update(LLM_BASE_URL="http://gw/v1", LLM_API_KEY="k")
        self.sent = []
        self._orig = ne.urllib.request.urlopen

    def tearDown(self):
        ne.urllib.request.urlopen = self._orig

    def fake(self, headers, n=2):
        def opener(req, timeout):
            self.sent.append(json.loads(req.data))
            return Resp({"data": [{"index": i, "embedding": [0.1 * i, 0.2]} for i in reversed(range(n))]}, headers)
        ne.urllib.request.urlopen = opener

    def test_fallbacks_disabled_and_model_recorded_in_order(self):
        self.fake({"x-litellm-model-name": "prov/test-embed-1b"})
        model, vecs = ne.embed(["a", "b"])
        self.assertTrue(self.sent[0]["disable_fallbacks"])
        self.assertEqual(model, "test-embed-1b")
        self.assertEqual(vecs[0], [0.0, 0.2])                     # sorted by index

    def test_unknown_model_is_refused(self):
        self.fake({})
        with self.assertRaises(RuntimeError):
            ne.embed(["a", "b"])

    def test_rate_limit_is_waited_out_and_other_errors_are_not(self):
        calls, slept = [], []

        def flaky(texts):
            calls.append(1)
            if len(calls) < 3:
                raise RuntimeError('unavailable: {"error":{"message":"litellm.RateLimitError: No deployments available"}}')
            return "m", [[0.0]] * len(texts)
        self.assertEqual(ne.embed_with_retry(flaky, ["a"], sleep=slept.append)[0], "m")
        self.assertEqual(slept, [65.0, 65.0])

        def gone(texts):
            raise RuntimeError("unavailable: 410 Gone")
        with self.assertRaises(RuntimeError):
            ne.embed_with_retry(gone, ["a"], sleep=slept.append)
        self.assertEqual(slept, [65.0, 65.0])          # a retired model is not retried

    def test_partial_progress_is_reported_when_the_gateway_stops(self):
        class Cur:
            def __init__(self, conn): self.c = conn
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def execute(self, sql, params=None):
                if sql.startswith("SELECT"):
                    self.rows = [(i, None, "h") for i in range(self.c.next, self.c.next + 2)]
                elif sql.startswith("INSERT"):
                    self.c.next += 1
            def fetchall(self): return self.rows

        class Tx:
            def __enter__(self): return self
            def __exit__(self, *a): return False

        class Conn:
            next = 0
            def cursor(self): return Cur(self)
            def transaction(self): return Tx()
        n = []

        def embedder(texts):
            n.append(1)
            if len(n) == 2:
                raise RuntimeError("unavailable: 410 Gone")
            return "m", [[0.0]] * len(texts)
        with self.assertRaises(ne.EmbedStopped) as cm:
            ne.embed_pending(Conn(), embedder, batch=2, pause=0, max_batches=5, sleep=lambda s: None)
        self.assertEqual(cm.exception.done, 2)

    def test_vector_literal(self):
        self.assertEqual(ne.vec_literal([0.5, -1e-7, 2]), "[0.5,-1e-07,2]")


if __name__ == "__main__":
    unittest.main(verbosity=1)
