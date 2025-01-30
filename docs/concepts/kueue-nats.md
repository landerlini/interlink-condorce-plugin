# Kueue integration (`MasterQueue`)

The bidirectional [messaging layer provided by NATS](./nats.md) enables feedbacks on the status of the 
resource pools on the resources they may provide. To enable the definition of policies on the resource usage defined 
at cluster level (rather than at node level) the InterLink resource pools are mapped into kueue 
[`ResourceFlavor`s](https://kueue.sigs.k8s.io/docs/concepts/resource_flavor/) and organized in 
[`ClusterQueue`s](https://kueue.sigs.k8s.io/docs/concepts/cluster_queue/).

A custom controller, named `kueue-nats`, connects to the NATS server and subscribe to the subjects under which 
resource providers publish the available resources. When receiving resource updates, the `kueue-nats` controller 
creates or updates `ResourceFlavor`s the `ClusterQueue`s accordingly. The configuration of the `kueue-nats` controller 
relies on a Kubernetes Custom Resource (CR) named `MasterQueue`.

A `MasterQueue` is supposed to be the cluster-level object collecting all the resource pools made available to the 
cluster either via InterLink or as local resources. The MasterQueue can then lend resources to groups of users or 
client applications using the [Kueue resource-borrowing mechanism](https://kueue.sigs.k8s.io/docs/concepts/cluster_queue/#cohort). 
All the `ClusterQueue`s spawned by a `MasterQueue` belong to the same *cohort*, named by the `MasterQueue` itself.

## Organizing Flavors
As mentioned, `ResourceFlavor`s map resource pools. Since multiple resource pools might be equivalent and 
interchangeable to the applications running in the cluster (think for example of two Kubernetes clusters running 
in different sites), `kueue-nats` introduces *groups* of `ResourceFlavor`s, mapping groups of equivalent resource
pools. 

Groups of resource flavors may fall in two categories: `natsFlavor`, managed by InterLink via the InterLink NATS plugin,
or `localFlavor`, managed by the cluster administrator directly via Kueue and made available to the MasterQueue.

### `natsFlavor`s 
A `natsFlavor` should indicate the following properties:
 * `natsConnector`: a string defining the connection to the NATS server. If deploying the NATS plugin with the 
   [*interlink-in-one*](../deploy.md), this string is provided by Helm itself when installing or updating the chart.
 * `natsSubject`: a string representing the NATS subject used by resource pools to publish updates on the available 
   resources
 * `virtualNode`: is the name of the virtual node where to address pods to be submitted with interlink
 * `poolTimeout`: an integer defining the timeout in seconds. If no update to the available resources is obtained 
   after this time interval, the corresponding resource flavor is dropped.

The names of pools part of the group can be listed explicitly using the `pools` keyword,

```yaml
pools:
  - podman-firenze
  - slurm-cineca
```

or it can be defined with a regular expression using the `poolRegExp`, for example

```yaml
poolRegExp: "podman-.*"
```

!!! note

    Note that the regular expression is managed with the `re` module in Python. 


## Minimal `MasterQueue` definition
A minimal example of a MasterQueue is defined below with annotations

```yaml
apiVersion: vk.io/v1
kind: MasterQueue
metadata:
  name: masterqueue  # This is the name of the MasterQueue 
spec:
  template:
    cohort: masterqueue  # This is the name of the cohort. It is suggested to use the same name as for the MasterQueue.

  flavors:   # Here we introduce the list of resource flavors organized in categories. RFs can be either `natsFlavor`s or `localFlavor`s.
    - name: interlink  # This is the name of the flavor category (not of the flavor itself!)
      natsFlavor:
        natsConnector: "nats://user:password@nats.nats:4222"
        natsSubject: interlink.resources
        virtualNode: interlink
        poolTimeout: 30
        poolRegExp: ".*"
      nominalQuota:
        pods: 100
      canLendTo:
        - queue: group1
          lendingLimit:
            pods: 30
```

This creates two `ClusterQueue` objects belonging to the same cohort `masterqueue`. One named `masterqueue` 
(as the `MasterQueue` object) has quotas based on the resources published by the various providers, the other, 
named `group1` has no quota, but a `borrowingLimit` for pods set to 30.

!!! warning

    The limits are specified *per resource pool*. In the example above, if two resource pools are available, the 
    ClusterQueue `group1` will be entitled to borrow 30 pods from the first one and 30 pods from the second. 


## Creating a `LocalQueue`
To connect a namespace to a `ClusterQueue`, the cluster admins must create `LocalQueue` objects. For example, to 
enable executing jobs in the `default` namespace in the `group1` ClusterQueue, one may define the `gr1` LocalQueue as

```yaml
apiVersion: kueue.x-k8s.io/v1beta1
kind: LocalQueue
metadata:
   namespace: default
   name: gr1
spec:
   clusterQueue: group1
```

## A test job
To test the setup, one may submit a test job in the `default` namespace through the queue `gr1`.
```yaml
apiVersion: batch/v1
kind: Job
metadata:
  generateName: sample-job-1
  namespace: default
  labels:
    kueue.x-k8s.io/queue-name: gr1
spec:
  parallelism: 1
  completions: 1
  suspend: true
  template:
    spec:
      containers:
      - name: dummy-job
        image:  ghcr.io/grycap/cowsay:latest
        command: ["/bin/sh", "-c"]
        args: 
         - |
                echo "Hello world!"
                sleep 30

        resources:
          requests:
            cpu: 1
            memory: "200Mi"
      tolerations:
        - key: virtual-node.interlink/no-schedule
          operator: Exists
          effect: NoSchedule
      restartPolicy: Never
```

Note in particular:
 * the label `kueue.x-k8s.io/queue-name: gr1` defining the local queue,
 * the spec `suspend: true` to let Kueue to submit the job once admitted, 
 * the toleration to the node taint `virtual-node.interlink/no-schedule` marking the job as suitable for offloading.

## Local flavors
To enable local execution of payloads not suitable for offloading, one may include a local flavor in the `MasterQueue`.
Local flavors are a simply links to the standard `ResourceFlavor` objects of the Kueue that must be already defined in 
the cluster and fall outside the range of action of the `kueue-nats` controller.

For example the following `yaml` manifest defines a `ResourceFlavor` and makes it available to the `group1`
`ClusterQueue` via the `MasterQueue`. 

```yaml
apiVersion: kueue.x-k8s.io/v1beta1
kind: ResourceFlavor
metadata:
  name: "local-cpu"  # <-- Note this name

---

apiVersion: vk.io/v1
kind: MasterQueue
metadata:
   name: masterqueue  
spec:
   template:
      cohort: masterqueue  

   flavors:   
      - name: local-cpu   # <-- This name should match the ResourceFlavor definition above
        localFlavor: {}
        nominalQuota:
           pods: 100
        canLendTo:
           - queue: group1
             lendingLimit:
                pods: 30
```



