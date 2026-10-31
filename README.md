# riggrade

RigGrade is an independent GPU rig grading service. Whoever holds access to a rig runs an open, pinned benchmark container on it. The grading endpoint grades the result against fixed, published bars for the rig's peer class and workload class, and returns a label and an evidence pack. Grades are labels of measured quality, not promises.

Status: step 1 (spec, sample grades, container and endpoint, built and tested with the sale switched off). Nothing here is live.

- [Grading spec, v0.1](docs/grading-spec.md)
- [Sample grading of three fictional rigs](docs/sample-grading.md)
- [Smoke test notebook](notebooks/smoke_test.ipynb): checks the GPU code paths on a free Colab or Kaggle GPU. Not a grade: small model, short run, local endpoint with the sale off. Command: `python3 -m runner.smoke --minutes 2`.

## Rules for this repo

- Never commit secrets, keys, wallet private material or credentials.
- Never commit customer data: evidence packs, host identities or wallet-linked results.
- One branch and one pull request per meaningful piece of work. No automated loops that open or merge pull requests.
- GitHub Actions run tests only.
