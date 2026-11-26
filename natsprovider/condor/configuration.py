from ..configuration import *

# BEARER_TOKEN_PATH is the path where an access token is stored
BEARER_TOKEN_PATH = os.environ.get("BEARER_TOKEN_PATH", None)

# REFRESH_TOKEN or identity token used to authenticate against the condor backend
REFRESH_TOKEN = os.environ.get("REFRESH_TOKEN", None)

# TOKEN_VALIDITY_SECONDS is the time awaited before triggering a refresh of the bearer token
TOKEN_VALIDITY_SECONDS = int(os.environ.get("TOKEN_VALIDITY_SECONDS", 1200))

# CONDOR_POOL is the remote collector of HTCondor passed as value for argument "-pool" in condor CLI
CONDOR_COLLECTOR_HOST = os.environ.get(
    "_CONDOR_COLLECTOR_HOST", "ce01t-htc.cr.cnaf.infn.it:9618"
)

# _CONDOR_SCHEDD_NAME is the value of the argument to the "-name" in the condor CLI
CONDOR_SCHEDD_NAME = os.environ.get("_CONDOR_SCHEDD_NAME", "ce01t-htc.cr.cnaf.infn.it")

# IAM_ISSUER is the issuer of OIDC tokens
IAM_ISSUER = os.environ.get("IAM_ISSUER", "")

# IAM_CLIENT_ID is the ID of the IAM Client defined through the IAM_ISSUER
IAM_CLIENT_ID = os.environ.get("IAM_CLIENT_ID", "")

# IAM_CLIENT_SECRET is the SECRET of the IAM Client defined through the IAM_ISSUER
IAM_CLIENT_SECRET = os.environ.get("IAM_CLIENT_SECRET", "")


print("CONDOR_COLLECTOR_HOST:", CONDOR_COLLECTOR_HOST)
print("CONDOR_SCHEDD_NAME:", CONDOR_SCHEDD_NAME)
