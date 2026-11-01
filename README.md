# riggrade

RigGrade is an independent GPU rig grading service. Whoever holds access to a rig runs an open, pinned benchmark container on it. The grading endpoint grades the result against fixed, published bars for the rig's peer class and workload class, and returns a label and an evidence pack. Grades are labels of measured quality, not promises.

Status (1 Nov 2026): live at https://riggrade-674098696181.us-central1.run.app/riggrade/v1 with the sale on. Agents can read `/.well-known/agent.json`, `/llms.txt` and `/riggrade/v1/openapi.json` on that host. Prices: 3 USDC per workload class for consumer cards, 5 USDC for data-centre cards, paid by x402 (USDC on Base). While a GPU class has no reference yet, each rig's first session there is free.

- [Grading spec, v0.1](docs/grading-spec.md)
- [Sample grading of three fictional rigs](docs/sample-grading.md)
- [Preflight check](docs/preflight.md): the acceptance test big buyers run before paying for GPUs, for everyone else. Free one-minute check of a rented or hosted GPU box (throttling, foreign processes, driver vs CUDA, PCIe link, download and disk speed). `python3 -m runner.preflight`. Not a grade.
- [GPU index method, v0 draft](docs/index-method.md): daily listed price per GPU-hour by peer class, and price per unit of measured work once graded rigs exist. Method published before any value. Recorder: `python3 -m pricewatch.record --out PATH`.
- [Cluster acceptance test, spec v0 draft](docs/cluster-acceptance.md): checks a rented multi-node cluster against its provider's own claims (inventory, node health, stragglers, inter-node fabric, storage) and writes an evidence pack. Not on sale.
- MCP server for agents: `python3 -m riggrade.mcp` exposes `gpu_preflight` and `riggrade_service_info` over stdio.
- [Payments](docs/payments.md): x402 through keyless public facilitators with an automatic backup; payer screened before settle. The sale is off.
- [Reconstructed price history method, v0 draft](docs/index-reconstructed-method.md): a separate, labelled series from third-party records, never joined to the live index; no reconstructed grades.
- [Smoke test from Google Drive](notebooks/smoke_test_drive.ipynb): the same free GPU test, reading the code from Drive after checking it against a manifest (`scripts/stage_for_drive.py`) and writing both reports back to Drive.
- [Smoke test notebook](notebooks/smoke_test.ipynb): checks the GPU code paths on a free Colab or Kaggle GPU. Not a grade: small model, short run, local endpoint with the sale off. Command: `python3 -m runner.smoke --minutes 2`.

## Use the MCP server

Standard library only, stdio, no secrets. Two tools: `gpu_preflight` (free, runs
on the box the server runs on) and `riggrade_service_info`.

Claude Desktop or Claude Code (`claude mcp add riggrade -- python3 -m riggrade.mcp`),
or in any MCP client config:

```json
{"mcpServers": {"riggrade": {
  "command": "python3", "args": ["-m", "riggrade.mcp"],
  "env": {"RIGGRADE_ENDPOINT": "https://riggrade-674098696181.us-central1.run.app/riggrade/v1"}}}}
```

With `pip install git+https://github.com/nnyycc77-ship-it/riggrade` the commands
`riggrade-mcp` and `riggrade-preflight` are on the path.

## Running a graded session

The grading server is deployed from a separate private operations
repository; the code it runs is this one at `main`.


The benchmark container is public: `ghcr.io/nnyycc77-ship-it/riggrade-bench`,
pinned by digest in `container/bench-image.env`. The server accepts result
bundles only from a listed digest, so run it by digest, never by tag:

```bash
. container/bench-image.env
docker run --rm --gpus all -e RIGGRADE_IMAGE_DIGEST=$RIGGRADE_IMAGE_DIGESTS \
  -e RIGGRADE_ENDPOINT=https://riggrade-674098696181.us-central1.run.app/riggrade/v1 \
  $BENCH_IMAGE --help
```

The container carries the runner and the spec only. The grading server,
payment code and screen list never ship in it.

## Licence

Apache License 2.0; see `LICENSE`. The benchmark is open so that anyone can
inspect and rerun the test behind a grade.

## Rules for this repo

- Never commit secrets, keys, wallet private material or credentials.
- Never commit customer data: evidence packs, host identities or wallet-linked results.
- One branch and one pull request per meaningful piece of work. No automated loops that open or merge pull requests.
- GitHub Actions run tests only.
