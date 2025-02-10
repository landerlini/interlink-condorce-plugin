# Podman provider
Podman provider is an option for taking control of Virtual Machines (or bare machines) through a remote Kubernetes
installation.

!!! warning

    Note! At the moment running with podman requires root privileges. 


To install Podman, please [refer to the official documentation](https://podman.io/docs/installation#installing-on-linux).

The simplest option to install the submitter is using pip
```yaml
pip install git+https://github.com/landerlini/interlink-condorce-plugin.git
```

The following script should work
```bash
#!/bin/bash
export PODMAN_BASE_URL=unix:///run/podman/podman.sock
export GITREPO="git+https://github.com/landerlini/interlink-condorce-plugin.git@main"

pip install --update --force-reinstall $GITREPO

while true
do
        pip install --force-reinstall --no-deps $GITREPO

        interlink podman \
                --build-config /data/interlink/podman-recas.cfg \
                --server wss://<your NATS server here> \
                --pool podman \
                --cpu 85 \
                --memory 1Ti \
                --pods 123 \
                --gpus 28 \
                --responders 1 \
                &> /data/interlink/logs/plugin.log
done;
```

For production environments, it is a good idea to replace `@main` with a tagged version of the repository.

## A configuration sample
This configuration has been used for Podman in a machine hosted at ReCaS Bari.
```yaml
--8<-- "configs/podman-recas.cfg"
```
