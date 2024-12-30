# KueueNats 
## Updating `ResourceFlavors` and quotas through NATS
KueueNats is a simple Kubernetes operator to manage your 
ClusterQueues through NATS.
NATS-controlled flavors (`natsFlavor`s) are configured to query 
via NATS some remote resource provider to retrieve the 
amount of available resources and creates (or updates) the 
Kueue ResourceFlavors and patches the owned `ClusterQueue`
correspondingly.

## Example

```yaml
apiVersion: vk.io/v1
kind: MasterQueue
metadata:
  name: some-q
spec:
  cohort: aiinfn
  flavors:
    - natsFlavor:
        name: htcondor
        natsConnector: wss://someip/nats
        pools:
          - condor
    - localFlavor:
        name: cpu-only
        nominalQuota:
          cpu: 8
          memory: 32Gi
          pods: 16
          nvidia.com/gpu: 1
    - natsFlavor:
        name: opportunistic
        natsConnector: wss://someip/nats
        poolRegExp: "opp-.*"
```