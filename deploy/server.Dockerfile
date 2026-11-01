# RigGrade grading server for Cloud Run. This is NOT the benchmark
# container (container/Dockerfile); it runs only the standard-library WSGI
# endpoint, so it needs no GPU stack. Build from the repo root:
#
#   gcloud builds submit --config deploy/cloudbuild.yaml .
#
# The base is a tag, not a digest: Docker Hub rate-limited the digest read
# on 1 Nov 2026. The server image does not affect any grade; pin the digest
# when it can be read.
FROM python:3.12-slim

WORKDIR /opt/riggrade
COPY riggrade/ riggrade/
COPY spec/ spec/
# The payer screen list, built from OFAC's SDN_ADVANCED.XML just before the
# image build (deploy workflow step "Build the payer screen list").
COPY deploy/screen/blocked-addresses.txt screen/blocked-addresses.txt
RUN useradd --system --uid 10001 riggrade
USER riggrade

ENV PYTHONUNBUFFERED=1 \
    RIGGRADE_STATE_DIR=/state
EXPOSE 8080
CMD ["python3", "-m", "riggrade.serve"]
