# Interlink all-in-one chart

InterLink is a Virtual Kubelet provider designed to abstract the container lifecycle 
using REST APIs. Plugins are developed to respond to the VK queries.
Usually, the virtual kubelet is deployed in a cluster and the interlink API server and its plugin are
placed on an edge node of a computing center where it can submit payloads to computing queues or 
container orchestrators. 

This chart deploys all the components (virtual kubelet, API server and plugin) in the same cluster,
to avoid authentication issues between the parties. The deployed plugin is based on NATS to 
distribute jobs to the actual batch systems that connect to the main server via a web socket 
or an SSH tunnel.

## Prerequirements
 * You should [install kueue](https://kueue.sigs.k8s.io/docs/installation/#install-a-released-version)
 * if this chart has to manage the TLS termination, make sure [cert-manager is installed](https://cert-manager.io/docs/installation/kubectl/).
 
## Installing the chart
Here is the minimal `values.yaml` to deploy the chart

```yaml

# hostname is the FQDN of the master node and it is used to configure the Ingress
hostname: <insert the FQDN of your setup, without protocol!>

# natsUsers is a list of mappings, each mapping has keys "username" and "password". Requires nats-server --signal reload to take effect.
natsUsers: 
  - username: <a username you like>
    password: <a randomly generated high entropy secret>

# redisDefaultPassword is the password of the default user
redisDefaultPassword: < a randomly generated token>

# redisNodeName is the name of the node where to place redis (relevant to persistence)
redisNodeRole: database # then don't forget to `kubectl label node <your node> node-role.kubernetes.io/database=true`

# natsClusterIssuerEmail is the email of the user taking care of the certification
natsClusterIssuerEmail: 
```

