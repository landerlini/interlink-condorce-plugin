from os import environ

# DEBUG enhances the verbosity of the server. Default: false
DEBUG = bool(environ.get("DEBUG", "false").lower() in ["true", "yes", "y"])

# SCRATCH_AREA is a node-local directory where to store temporary file to manage the job (default: /tmp)
SCRATCH_AREA = environ.get("SCRATCH_AREA", "/tmp")

# APPTAINER_CACHEDIR defines the directory where apptainer puts images (default: /cache/apptainer)
APPTAINER_CACHEDIR = environ.get("APPTAINER_CACHEDIR", "/tmp/cache/apptainer")
