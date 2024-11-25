# Image name: landerlini/interlink-condorce-plugin:v0.1
FROM htcondor/mini:latest

RUN dnf install -y \
  git \
  python3 \
  python3-pip \
  oidc-agent \
  epel-release apptainer

RUN pip install \
  "uvicorn[standard]" \
  "fastapi" \
  "pyyaml" \
  "nats-py" \
  "orjson" \
  "pytest" \
  "kubernetes" \
  "htcondor" \
  "podman" \
  "pytest"

