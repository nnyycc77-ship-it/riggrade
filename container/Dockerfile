# RigGrade benchmark container (spec sections 3 and 14).
#
# The base image carries the driver-facing stack (CUDA, torch, vLLM,
# transformers). Owner decision (1 Nov 2026): the official vLLM image,
# pinned to an exact release by digest. v0.32.0 is the version all three
# free T4 test runs passed on (31 Oct and 1 Nov 2026). The index digest
# below was read from registry-1.docker.io on 1 Nov 2026. A different base
# is a bench_version change. Build with:
#
#   docker build -f container/Dockerfile .
#
ARG BASE_IMAGE=vllm/vllm-openai:v0.32.0@sha256:befccf0e7e4089aa23926bb6f887a85c0d46c2a99e6de5c5ffdf5cb7957c5b34
FROM ${BASE_IMAGE}

WORKDIR /opt/riggrade
COPY riggrade/ riggrade/
COPY runner/ runner/
COPY spec/ spec/

# The digest of this image, filled in by the build pipeline at step 2.
ENV RIGGRADE_IMAGE_DIGEST=unpinned
ENTRYPOINT ["python3", "-m", "runner.main"]
