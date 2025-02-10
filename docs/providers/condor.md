# Condor provider

Condor jobs are submitted through a Condor Compute Entrypoint authenticated through Javascript Web Tokens (JWT) 
provided by an Indigo IAM Instance. 

We report below recipes to configure the condor provider.

## Kubernetes

The reported configuration works for INFN-T1 site. Modifications might be needed (especially for paths) in other 
sites. Then at some point we will have cvmfs.

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: condor-sub-build-conf
data:
  interlink.conf: |
    # Volumes and directories configuring access to executor-local data
    [volumes]

    ### Area in the executor filesystem to be used for temporary files
    scratch_area = "/tmp"

    ### Location to cache singularity images in the executor filesystem
    apptainer_cachedir = "/tmp/cache/apptainer"

    ### Location where to look for pre-built images
    image_dir = "/opt/exp_software/opssw/budda"

    ### Colon-separated list of directories to include in $PATH to look for executables
    additional_directories_in_path = ["/opt/exp_software/opssw/mabarbet/bin"]



    # Options configuring the behavior of apptainer runtime
    [apptainer]

    ### Enables --fakeroot in apptainer exec/run commands
    fakeroot = false

    ### Enables --containall flag in apptainer exec/run commands
    containall = false

    ### Defines whether the host enables users to mount fuse volumes or not
    fuse_mode = "host-privileged"

    ### Do not propagate umask to the container, set default 0022 umask
    no_init = false

    ### Do not bind home by default
    no_home = true

    ### Drop all privileges from root user in container
    no_privs = true

    ### Enable nVidia GPU support
    nvidia_support = false

    ### Clean the environment of the spawned container
    cleanenv = true



    # Proxy to download pre-build Docker Images instead of rebuilding at each execution
    [shub_proxy]

    ### shub proxy instance used to retrieve cached singularity images. Without protocol!
    server = "<insert shub-proxy endpoint>"

    ### Master token of the proxy used to request and generate client tokens
    master_token = "<insert shub-proxy master token>"


  interlink.sh: |
    echo "Cloning the repo"
    git clone --quiet --branch main https://github.com/landerlini/interlink-condorce-plugin plugin &> log
    echo "Repo cloned"
    cd plugin

    mkdir -p /opt/interlink
    echo "Starting authentication procedure"
    python3 -u natsprovider/tests/_auth.py /opt/interlink/refresh_token
    export REFRESH_TOKEN=$(cat /opt/interlink/refresh_token)

    while true
    do
      git pull &>> log

      python3 -um natsprovider condor \
          --queue "infncnaf" \
          --shutdown-subject "*" \
          --non-interactive

    done


---

apiVersion: apps/v1
kind: Deployment
metadata:
  name: condor-submitter
  labels:
    app: interlink
    component: condor-submitter
spec:
  replicas: 1
  selector:
    matchLabels:
      app: interlink
      component: condor-submitter
  template:
    metadata:
      labels:
        app: interlink
        component: condor-submitter
    spec:
      securityContext:
        fsGroup: 101

      containers:
        - name: condorprovider
          image: "landerlini/interlink-condorce-plugin:v0.1.2"
          command: ["/bin/bash", "-u", "/etc/interlink/interlink.sh"]

          env:
            - name: DEBUG
              value: "true"
            - name: TOKEN_VALIDITY_SECONDS
              value: "1200"
            - name: _condor_CONDOR_HOST
              value: "ce01t-htc.cr.cnaf.infn.it:9619"
            - name: CONDOR_POOL
              value: "ce01t-htc.cr.cnaf.infn.it:9619"
            - name: _condor_SCHEDD_HOST
              value: "ce01t-htc.cr.cnaf.infn.it"
            - name: CONDOR_SCHEDULER_NAME
              value: "ce01t-htc.cr.cnaf.infn.it"
            - name: IAM_ISSUER
              value: "https://iam-t1-computing.cloud.cnaf.infn.it"
            - name: IAM_CLIENT_ID
              value: <iam client id>
            - name: IAM_CLIENT_SECRET
              value: <iam client secret>
            - name: NATS_SUBJECT
              value: "interlink"
            - name: NATS_SERVER
              value: <nats server connector>
            - name: NATS_TIMEOUT_SECONDS
              value: "60"
            - name: DEFAULT_ALLOCATABLE_CPU
              value: "1"
            - name: DEFAULT_ALLOCATABLE_MEMORY
              value: "2Gi"
            - name: DEFAULT_ALLOCATABLE_PODS
              value: "10"
            - name: DEFAULT_ALLOCATABLE_GPUS
              value: "0"

          volumeMounts:
            - name: condorplugin-conf
              mountPath: /etc/interlink
              readOnly: true


      volumes:
        - name: condorplugin-conf
          configMap:
            name: condor-sub-build-conf
            items:
              - key: interlink.conf
                path: build.conf
              - key: interlink.sh
                path: interlink.sh
```

