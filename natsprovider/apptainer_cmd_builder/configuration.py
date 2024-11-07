from ..configuration import *

# DEBUG enhances the verbosity of the server. Default: false
DEBUG = bool(os.environ.get("DEBUG", "false").lower() in ["true", "yes", "y"])

# SCRATCH_AREA is a node-local directory where to store temporary file to manage the job (default: /tmp)
SCRATCH_AREA = os.environ.get("SCRATCH_AREA", "/tmp")

# APPTAINER_CACHEDIR defines the directory where apptainer puts images (default: /cache/apptainer)
APPTAINER_CACHEDIR = os.environ.get("APPTAINER_CACHEDIR", "/tmp/cache/apptainer")

# IMAGE_DIR defines a directory where to look for pre-built images
IMAGE_DIR = os.environ.get("IMAGE_DIR", "/opt/exp_software/opssw/budda")

# ADDITIONAL_DIRECTORIES_IN_PATH defines colon-separated directories in PATH to look for executables (e.g. apptainer)
ADDITIONAL_DIRECTORIES_IN_PATH = os.environ.get(
    "ADDITIONAL_DIRECTORIES_IN_PATH",
    "/opt/exp_software/opssw/mabarbet/bin"
).split(":")

# APPTAINER_FAKEROOT enables --fakeroot flag in apptainer exec
APPTAINER_FAKEROOT = os.environ.get("APPTAINER_FAKEROOT", "no").lower() in ["true", "yes", "y"]

# APPTAINER_CONTAINALL enables --fakeroot flag in apptainer exec
APPTAINER_CONTAINALL = os.environ.get("APPTAINER_CONTAINALL", "no").lower() in ["true", "yes", "y"]

# FUSE_ENABLED_ON_HOST defines whether the host enables users to use fuse or not
FUSE_ENABLED_ON_HOST = os.environ.get("FUSE_ENABLED_ON_HOST", "yes").lower() in ["true", "yes", "y"]

# SHUB_PROXY defines the Singularity Hub proxy instance building and caching OCI images. Without protocol (http).
SHUB_PROXY = os.environ.get("SHUB_PROXY")

# SHUB_PROXY_MASTER_TOKEN is the master token used to generate client tokens
SHUB_PROXY_MASTER_TOKEN = os.environ.get("SHUB_PROXY_MASTER_TOKEN")

# Minimal input validation
if SHUB_PROXY is None and SHUB_PROXY_MASTER_TOKEN is not None:
    raise ValueError("Provided SHUB_PROXY_MASTER_TOKEN with no SHUB_PROXY")

if SHUB_PROXY is not None and SHUB_PROXY_MASTER_TOKEN is None:
    raise ValueError("Provided SHUB_PROXY but no SHUB_PROXY_MASTER_TOKEN")
