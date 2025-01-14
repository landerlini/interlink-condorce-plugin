import json
from ..configuration import *

# NAMESPACE is the target namespace where the new pods will be executed. Default: "default"
NAMESPACE = os.environ.get("NAMESPACE", "default")

# ANNOTATIONS indicates the annotations to the pod
ANNOTATIONS = json.loads(os.environ.get("ANNOTATIONS", "[]"))

# TOLERATIONS indicates the list of tolerations to be added to the executed container. JSON format.
TOLERATIONS = json.loads(os.environ.get("TOLERATIONS", "[]"))

# NODE_SELECTOR is a mapping of {label: value} used to select the nodes where the pod can run. JSON format.
NODE_SELECTOR = json.loads(os.environ.get("NODE_SELECTOR", "{}"))

# START_SUSPENDED if true starts the job in suspended state to let Kueue (or other schedulers to launch it)
START_SUSPENDED = os.environ.get("START_SUSPENDED", 'yes').lower() in ['y', 'yes', 'true']

# CUSTOM_PILOT is the pilot image to be run with podman
CUSTOM_PILOT = os.environ.get("CUSTOM_PILOT", "docker.io/landerlini/interlink-pilot:v0")

# PRIVILEGED is true, the pilot is executed in a privileged container. If false, fuse volume handling may fail.
PRIVILEGED = os.environ.get("PRIVILEGED", 'yes').lower() in ['y', 'yes', 'true']