"""Workload rulers (spec section 3). Need the pinned image on a CUDA card;
not exercised by the CPU test suite.

Inference: Qwen2.5-7B-Instruct (Apache-2.0) served by vLLM's OpenAI
compatible server, measured from streamed completions with the stdlib.
Training: GPT-2 124M (transformers GPT2Config defaults), random weights,
synthetic random tokens, so nothing is downloaded but the inference model.
"""

import json
import os
import re
import subprocess
import tempfile
import threading
import time
import urllib.request

INFERENCE_MODEL = "Qwen/Qwen2.5-7B-Instruct"
SERVER_PORT = 8811
ERROR_LINE = re.compile(r"\b\w*(Error|Exception)\b|CUDA out of memory|not supported|"
                        r"less than desired GPU memory", re.IGNORECASE)


def _capability(device=0):
    try:
        import torch
        if torch.cuda.is_available():
            return torch.cuda.get_device_capability(device)
    except Exception:  # noqa: BLE001 - no torch or no GPU: nothing to correct
        return None
    return None


class InferenceServer:
    """vLLM's server as a subprocess. Its output goes to a log file, and a
    failed start reports the tail of that log, so the reason (for example an
    unsupported dtype) is in the report instead of a bare timeout. A server
    that exits during start-up is reported at once, not after the timeout."""

    def __init__(self, model=INFERENCE_MODEL, port=SERVER_PORT, dtype="bfloat16",
                 extra_args=(), log_path=None, startup_timeout=900,
                 popen=subprocess.Popen, health=None, sleep=time.sleep,
                 capability=None):
        self.model, self.port, self.proc = model, port, None
        self.dtype, self.extra_args = dtype, list(extra_args)
        self.dtype_corrected = False
        # vLLM refuses bf16 below compute capability 8.0. Correct it here so no
        # caller, graded or not, can start the server with it on such a card.
        if dtype == "bfloat16":
            cap = capability() if capability else _capability()
            if cap is not None and tuple(cap)[0] < 8:
                self.dtype, self.dtype_corrected = "float16", True
        self._log = None
        self.log_path = log_path or os.path.join(tempfile.gettempdir(), "riggrade-vllm.log")
        self.startup_timeout = startup_timeout
        self._popen, self._sleep = popen, sleep
        self._health = health or (lambda: urllib.request.urlopen(
            "http://127.0.0.1:%d/health" % self.port, timeout=5))

    def log_tail(self, lines=20):
        try:
            with open(self.log_path, encoding="utf-8", errors="replace") as fh:
                return "".join(fh.readlines()[-lines:]).strip()
        except OSError:
            return "(no server log)"

    def log_errors(self, limit=8):
        """Distinct error lines from the whole log. vLLM's last lines often say
        only 'see root cause above', so the cause has to be found earlier."""
        found = []
        try:
            with open(self.log_path, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if ERROR_LINE.search(line) and "See root cause above" not in line \
                            and line not in found:
                        found.append(line[:300])
        except OSError:
            return []
        return found[:limit]

    def _failure(self, head):
        errors = self.log_errors()
        return RuntimeError("%s\nError lines in the server log:\n%s\nLast log lines:\n%s"
                            % (head, "\n".join(errors) or "(none found)", self.log_tail()))

    def __enter__(self):
        log = self._log = open(self.log_path, "w", encoding="utf-8")
        self.proc = self._popen(
            ["vllm", "serve", self.model, "--dtype", self.dtype,
             "--port", str(self.port), "--tensor-parallel-size", "1"] + self.extra_args,
            stdout=log, stderr=subprocess.STDOUT)
        deadline = time.time() + self.startup_timeout
        while time.time() < deadline:
            code = self.proc.poll()
            if code is not None:
                log.close()
                raise self._failure("inference server exited during start-up (code %s)." % code)
            try:
                self._health()
                return self
            except OSError:
                self._sleep(5)
        self.proc.terminate()
        log.close()
        raise self._failure("inference server did not start within %d s." % self.startup_timeout)

    def __exit__(self, *exc):
        if self.proc:
            self.proc.terminate()
            self.proc.wait(timeout=60)
        if self._log and not self._log.closed:
            self._log.close()

    def _stream_one(self, prompt_tokens, output_tokens):
        """One streamed request. Returns (output tokens, time to first token,
        per-token gaps)."""
        body = {"model": self.model, "prompt": "hello " * prompt_tokens,
                "max_tokens": output_tokens, "min_tokens": output_tokens,
                "temperature": 0, "stream": True, "ignore_eos": True}
        req = urllib.request.Request(
            "http://127.0.0.1:%d/v1/completions" % self.port,
            data=json.dumps(body).encode(), method="POST",
            headers={"Content-Type": "application/json"})
        t0 = time.perf_counter()
        stamps = []
        with urllib.request.urlopen(req, timeout=600) as resp:
            for line in resp:
                line = line.strip()
                if not line.startswith(b"data:") or line == b"data: [DONE]":
                    continue
                stamps.append(time.perf_counter())
        if not stamps:
            return 0, None, []
        gaps = [b - a for a, b in zip(stamps, stamps[1:])]
        return len(stamps), stamps[0] - t0, gaps

    def run_pass(self, params):
        """Concurrent requests per the drawn parameters. Returns
        (output tokens per second, time-to-first-token list, token gaps)."""
        p = params["inference"]
        results = [None] * p["concurrency"]

        def worker(i):
            results[i] = self._stream_one(p["prompt_tokens"], p["output_tokens"])
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(p["concurrency"])]
        t0 = time.perf_counter()
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        dt = time.perf_counter() - t0
        tokens = sum(r[0] for r in results)
        return tokens / dt, [r[1] for r in results if r[1] is not None], \
            [g for r in results for g in r[2]]


def training_pass(params, seconds=60, device=0, dtype="bfloat16"):
    """GPT-2 124M on synthetic tokens. Returns training tokens per second
    per card on one card."""
    import torch
    from transformers import GPT2Config, GPT2LMHeadModel
    p = params["training"]
    model = GPT2LMHeadModel(GPT2Config()).to(device).to(getattr(torch, dtype))
    opt = torch.optim.AdamW(model.parameters(), lr=1e-4)
    batch, seq = p["micro_batch"], p["sequence_length"]
    tokens = 0
    for _ in range(3):  # warm-up
        _step(model, opt, batch, seq, device)
    torch.cuda.synchronize(device)
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < seconds:
        _step(model, opt, batch, seq, device)
        tokens += batch * seq
    torch.cuda.synchronize(device)
    return tokens / (time.perf_counter() - t0)


def _step(model, opt, batch, seq, device):
    import torch
    ids = torch.randint(0, 50257, (batch, seq), device=device)
    loss = model(input_ids=ids, labels=ids).loss
    loss.backward()
    opt.step()
    opt.zero_grad(set_to_none=True)
