# RigGrade benchmark container (spec sections 3 and 14).
#
# The base image carries the driver-facing stack (CUDA, torch, vLLM,
# transformers). It is deliberately not chosen here: the exact base and its
# digest are pinned at step 2, and that digest becomes part of the
# bench_version and of RIGGRADE_IMAGE_DIGESTS on the endpoint. Build with:
#
#   docker build --build-arg BASE_IMAGE=<registry/image@sha256:...> \
#       -f container/Dockerfile .
#
ARG BASE_IMAGE
FROM ${BASE_IMAGE}

WORKDIR /opt/riggrade
COPY riggrade/ riggrade/
COPY runner/ runner/
COPY spec/ spec/

# The digest of this image, filled in by the build pipeline at step 2.
ENV RIGGRADE_IMAGE_DIGEST=unpinned
ENTRYPOINT ["python3", "-m", "runner.main"]
