import os

# DEBUG enhances the verbosity of the server. Default: false
DEBUG = bool(os.environ.get("DEBUG", "false").lower() in ["true", "yes", "y"])

# NATS_SERVER is the NATS server used to decouple job script generation from submission
NATS_SERVER = os.environ.get("NATS_SERVER", "nats://nats:4222")

# NATS_SUBJECT is the NATS subject for this plugin (recommended: `interlink.<queue>`, e.g. `interlink.condor`)
NATS_SUBJECT = os.environ.get("NATS_SUBJECT", "interlink.condor")

# NATS_TIMEOUT_SECONDS is the timeout configured for NATS requests, in seconds. Default: 5 seconds.
NATS_TIMEOUT_SECONDS = float(os.environ.get("NATS_TIMEOUT_SECONDS", "5"))


