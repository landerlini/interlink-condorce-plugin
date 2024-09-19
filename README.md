# interlink-condorce-plugin 🦤 🐍
A helm chart deploying an interlink plugin redirecting jobs to a remote condor CE

Enabling offloading Kubernetes tasks to HTCondor is of critical importance to unload the CPUs of machines providing
GPUs and FPGAs from High Throughput Computing tasks, that only require CPUs to be performed.
In this repository we provide an implementation of an InterLink plugin for a remote HTCondor Computing EntryPoint 
(condor-ce). 

The remote condor-ce is authenticated using IAM tokens, however, in general, the IAM issuer used to 
authenticate HTC resources may differ from the IAM used for Kubernetes, so this plugin define two different IAM 
issuer for authenticating the incoming requests from a remote Kubernetes cluster and the outgoing HTCondor calls.

## Chart structure
The logical flow of an incoming computation request is as follows:
1. This cluster defines a TLS-certified HTTPS ingress to the general Internet
   (see [cert-manager.yaml](templates/cert-manager.yaml)); 
2. The remote Kubernetes cluster gets authenticated via Bearer token by an **OAuth2Proxy** instance
   (see [server.yaml](templates/server.yaml));
3. The authenticated request is passed to an **InterLink API server** (see [server.yaml](templates/server.yaml));
4. A specialized **InterLink plugin** for condor (see [plugin.yaml](templates/plugin.yaml)), 
   authenticated against the HTCondor backend
   (see [authenticator.yaml](templates/authenticator.yaml)), converts and submit the requests.

### Minimal `values.yaml`

```yaml
# hostname
hostname: <insert here the fully qualified domain name, without protocol>

# IAM authenticating incoming requests
oauth2ProxyIamIssuer: <iam-issuer>
iamClientId: <client-id>
iamClientSecret: <client-secret>
oauth2ProxyCookieSecret: <a random string of 16 chars to be used as encryption key>

# IAM authenticating outgoind requests
backendIamIssuer: <iam-issuer>
backendIamClientId: <client-id>
backendIamClientSecret: <client-secret>

# Condor configuration
pluginCondorPool: <value one would pass to condor -pool argument, with port>
pluginCondorScheduler: <value one would pass to condor -name argument>

# hostname
certManagerEmailAddress: <your e-mail>
```

### Dependencies 
 * helm
 * cert-manager: https://cert-manager.io/docs/installation/

## Specialized plugin
The plugin is composed of a general package to convert a Kubernetes Pod and a set of volumes into a bash script 
relying on apptainer (or singularity) for the container runtime interface 
(see [apptainer_cmd_builder](condorprovider/apptainer_cmd_builder)). 
A condor-specific machinery to submit generic payloads to a remote condor backend is coded in the 
[CondorConfiguration module](condorprovider/CondorConfiguration.py).
Finally, [CondorProvider](condorprovider/CondorProvider.py) defines methods to convert Kubernetes Pods into 
bash scripts and submit them to the condor backend. 

The CondorProvider is wrapped in a FastAPI application defined in [main.py](main.py) exposing InterLink plugin APIs.

### Warning on scalability
The CondorProvider is intended as a stateless application with all the information relative to the jobs stored in 
either the remote Kubernetes cluster or in the HTCondor backend, however the authentication token is stored and 
refreshed automatically. 
The chart at its current version does not support running multiple replicas of the server because each of them
would try to refresh the access token independently, invalidating the token of others. 
The FastAPI application supports sharing the access token among replicas if the environment variable `BEARER_TOKEN_PATH`
is defined, but the refresh mechanism should be brought to a sidecar 
(one could use for example the image `ghcr.io/intertwin-eu/interlink/virtual-kubelet-inttw-refresh:latest` as in 
[landerlini/interlink-virtual-node](https://github.com/landerlini/interlink-virtual-node)).

### Dependencies 
The full list of dependencies of the plugin can be found in the [Dockerfile](docker/Dockerfile). 

They include the htcondor CLI, oidc-agent and Python. 
Apptainer is used for local testing. 

The plugin application depends on the following Python packages 
 * uvicorn
 * fastapi
 * pyyaml
 * jinja2
 * pytest
 * kubernetes
 * htcondor
 * https://github.com/intertwin-eu/interLink.git#egg=interlink&subdirectory=example
