from ..configuration import *

# PODMAN_BASE_URL is the url of the podman socket
PODMAN_BASE_URL = os.environ.get("PODMAN_BASE_URL", "http+unix:///run/podman/podman.sock")

# CUSTOM_PILOT is the pilot image to be run with podman
CUSTOM_PILOT = os.environ.get("CUSTOM_PILOT", "docker.io/landerlini/interlink-pilot:v0")

# LOCAL_SANDBOX is the directory where the logs and statuses are stored
LOCAL_SANDBOX = os.environ.get("LOCAL_SANDBOX", "/tmp/interlink-nats-plugin")