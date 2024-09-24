from os import environ

# DEBUG enhances the verbosity of the server. Default: false
DEBUG = bool(environ.get("DEBUG", "false").lower() in ["true", "yes", "y"])

# SCRATCH_AREA is a node-local directory where to store temporary file to manage the job (default: /tmp)
SCRATCH_AREA = environ.get("SCRATCH_AREA", "/tmp")

# APPTAINER_CACHEDIR defines the directory where apptainer puts images (default: /cache/apptainer)
APPTAINER_CACHEDIR = environ.get("APPTAINER_CACHEDIR", "/tmp/cache/apptainer")

# IMAGE_DIR defines a directory where to look for pre-built images
IMAGE_DIR = environ.get("IMAGE_DIR", "/opt/exp_software/opssw/budda")

# APPTAINER_FAKEROOT enables --fakeroot flag in apptainer exec
APPTAINER_FAKEROOT = environ.get("APPTAINER_FAKEROOT", "yes").lower() in ["true", "yes", "y"]

# FUSE_ENABLED_ON_HOST defines whether the host enables users to use fuse or not
FUSE_ENABLED_ON_HOST = environ.get("FUSE_ENABLED_ON_HOST", "yes").lower() in ["true", "yes", "y"]

