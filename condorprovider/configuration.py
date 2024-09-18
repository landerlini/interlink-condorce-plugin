import os

# DEBUG enhances the verbosity of the server. Default: false
DEBUG = bool(os.environ.get("DEBUG", "false"))

# BEARER_TOKEN_PATH is the path where an access token is stored
BEARER_TOKEN_PATH = os.environ.get("BEARER_TOKEN_PATH", None)

# TOKEN_VALIDITY_SECONDS is the time awaited before triggering a refresh of the bearer token
TOKEN_VALIDITY_SECONDS = int(os.environ.get("TOKEN_VALIDITY_SECONDS", 1200))




