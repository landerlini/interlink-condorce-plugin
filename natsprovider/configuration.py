import os

# DEBUG enhances the verbosity of the server. Default: false
DEBUG = bool(os.environ.get("DEBUG", "false").lower() in ["true", "yes", "y"])

# NATS_SERVER is the NATS server used to decouple job script generation from submission
NATS_SERVER = os.environ.get("NATS_SERVER", "nats://nats:4222")

# NATS_SUBJECT is the NATS subject for this plugin (recommended: `interlink`)
NATS_SUBJECT = os.environ.get("NATS_SUBJECT", "interlink")

# NATS_TIMEOUT_SECONDS is the timeout configured for NATS requests, in seconds. Default: 5 seconds.
NATS_TIMEOUT_SECONDS = float(os.environ.get("NATS_TIMEOUT_SECONDS", "60"))

# DEFAULT_ALLOCATABLE_CPU is the number of allocatable CPUs if not specified by either the provider or via CLI
DEFAULT_ALLOCATABLE_CPU = os.environ.get("DEFAULT_ALLOCATABLE_CPU", "1")

# DEFAULT_ALLOCATABLE_MEMORY is the amount of allocatable RAM if not specified by either the provider or via CLI
DEFAULT_ALLOCATABLE_MEMORY = os.environ.get("DEFAULT_ALLOCATABLE_MEMORY", "2Gi")

# DEFAULT_ALLOCATABLE_PODS is the number of allocatable pods if not specified by either the provider or via CLI
DEFAULT_ALLOCATABLE_PODS = int(os.environ.get("DEFAULT_ALLOCATABLE_PODS", "10"))

# DEFAULT_ALLOCATABLE_GPUS is the number of allocatable nVidia GPUs if not specified by either the provider or via CLI
DEFAULT_ALLOCATABLE_GPUS = int(os.environ.get("DEFAULT_ALLOCATABLE_GPUS", "0"))


