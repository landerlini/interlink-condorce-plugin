# Image name: landerlini/interlink-condorce-plugin:v0.0
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
  "pytest" \
  "kubernetes" \
  "htcondor" \
  "git+https://github.com/intertwin-eu/interLink.git@0.3.0#egg=interlink&subdirectory=example"

