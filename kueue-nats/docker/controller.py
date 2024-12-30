import os
import re
import json
from typing import Dict, List, Any, Literal, Union, Optional, Self, Tuple
from datetime import datetime
import logging

import kopf
import kubernetes as k8s
import nats
from kubernetes.utils import parse_quantity
from nats.aio.msg import Msg
from pydantic import BaseModel, Field

ResourceKey = Literal['cpu', 'memory', 'pods', 'nvidia.com/gpu']
RESOURCES_KEYS: Tuple[ResourceKey] = ('cpu', 'memory', 'pods', 'nvidia.com/gpu')

logging.getLogger("kopf.objects").setLevel(logging.WARNING)

################################################################################
# Setup kubernetes and connection to the cluster
if os.environ.get("KUBECONFIG", "") == "":
    k8s.config.load_incluster_config()
else:
    k8s.config.load_kube_config()

###
def get_list_of_flavors():
    """
    Return the list of Kueue ResourceFlavor resources defined in the cluster.
    """
    api = k8s.client.CustomObjectsApi()
    return api.list_cluster_custom_object(
        group='kueue.x-k8s.io',
        version='v1beta1',
        plural='resourceflavors',
    ).get('items', [])

class Flavor(BaseModel):
    master_queue_name: str
    name: str
    nominal_quota: Dict[ResourceKey, Union[int, str]]
    allocatable_quota: Dict[str, Dict[ResourceKey, Union[int, str]]] = Field(default={})

    async def on_tick(self):
        pass

    def get_flavor_uid(self) -> str:
        return f"{self.__class__.__name__}:{self.master_queue_name}-{self.name}"

    def compute_quota(self, pool: str = None):
        # Set by administrator as maximum allowed
        nml = self.nominal_quota

        if pool is None:
            return nml

        # Set by flavor logics as what actually available for deployments
        alctbl = self.allocatable_quota[pool] or self._default_quota

        return {
            k: alctbl.get(k, 0)
            if k not in nml.keys() or parse_quantity(alctbl.get(k, 0)) < parse_quantity(nml[k]) else
            nml.get(k, 0)
            for k in RESOURCES_KEYS
        }

class LocalFlavor(Flavor):
    @property
    def _default_quota(self):
        return self.nominal_quota

    async def on_tick(self):
        resource_flavors = [rf.get('metadata', {}).get('name', '?') for rf in get_list_of_flavors()]
        if self.name not in resource_flavors:
            logging.getLogger(self.name).critical(f"Local ResourceFlavor `{self.name}` not defined in this cluster.")

    @property
    def flavors(self):
        quota = self.compute_quota()
        return [
            dict(
                name=self.name,
                resources=[dict(name=k, nominalQuota=quota[k]) for k in RESOURCES_KEYS]
            )
        ]

class NatsFlavor(Flavor):
    nats_connector: str
    nats_subject: str
    virtual_node: str
    pools: Optional[List[str]] = None
    pool_reg_exp: Optional[str] = None
    timeout_seconds: int

    _connection: Any = None
    _subscription: Any = None
    _pool_timestamps: Dict[str, datetime] = None

    def get_flavor_uid(self) -> str:
        return (
            Flavor.get_flavor_uid(self)
            + f"[{self.nats_connector}?subject={self.nats_subject}&node={self.virtual_node}]"
        )

    @property
    def _default_quota(self):
        return {k: 0 for k in RESOURCES_KEYS}

    @property
    def flavors(self):
        if self._pool_timestamps is None:
            return []

        pools = [pool for pool in self._pool_timestamps.keys()]
        quota_per_pool = [self.compute_quota(pool) for pool in pools]
        ret = [
            dict(
                name='-'.join((self.master_queue_name, self.name, pool)),
                resources=[dict(name=k, nominalQuota=quota[k]) for k in RESOURCES_KEYS]
            )
            for pool, quota in zip(pools, quota_per_pool)
        ]
        return ret

    async def subscribe(self) -> Self:
        if self._connection is None:
            self._connection = await nats.connect(self.nats_connector)

        subject = ".".join((self.nats_subject, "*"))
        if self._subscription is None:
            logging.getLogger(self.name).info(f"Subscribing to subject `{subject}`")
            self._subscription = await self._connection.subscribe(subject, cb=self.update_resources_cb)

        return self

    async def update_resources_cb(self, msg: Msg):
        pool = msg.subject.split(".")[-1]
        logging.getLogger(self.name).info(f"Received resource update for pool `{pool}`")
        if pool in self.pools or (self.pool_reg_exp is not None and len(re.findall(self.pool_reg_exp, pool)) > 0):
            resource_struct = json.loads(msg.data)
            logging.getLogger(self.name).info(f"Processing resource update for pool `{pool}`")
            self.allocatable_quota[pool] = resource_struct
            if self._pool_timestamps is None:
                self._pool_timestamps = {}
            self._pool_timestamps[pool] = datetime.now()

    async def ensure_resource_flavors(self):
        resource_flavors = [rf.get('metadata', {}).get('name', '?') for rf in get_list_of_flavors()]
        if self._pool_timestamps is not None:
            for pool, last_update in self._pool_timestamps.items():
                flavor_name = '-'.join((self.master_queue_name, self.name, pool))

                node_labels = {'type': 'virtual-kubelet'}
                if self.virtual_node is not None:
                    node_labels.update({'kubernetes.io/hostname': self.virtual_node})

                body = dict(
                    apiVersion="kueue.x-k8s.io/v1beta1",
                    kind="ResourceFlavor",
                    metadata=dict(name=flavor_name),
                    spec=dict(
                        nodeLabels=node_labels,
                        nodeTaints=[
                            dict(key='virtual-node.interlink/no-schedule', value='true', effect='NoSchedule'),
                        ],
                        tolerations=[
                            dict(key='pool.vk.io', value=pool, effect='NoSchedule')
                        ],
                    )
                )

                kopf.adopt(body)

                api = k8s.client.CustomObjectsApi()
                if flavor_name in resource_flavors:
                    api.patch_cluster_custom_object(
                        group='kueue.x-k8s.io',
                        version='v1beta1',
                        plural='resourceflavors',
                        name=flavor_name,
                        body=body
                    )
                else:
                    logging.getLogger(self.name).debug(
                        f"Creating flavor: {flavor_name} (already available: {', '.join(resource_flavors)})"
                    )
                    api.create_cluster_custom_object(
                        group='kueue.x-k8s.io',
                        version='v1beta1',
                        plural='resourceflavors',
                        body=body
                    )


    async def on_tick(self):
        if self._pool_timestamps is not None:
            for flavor_id, (pool, last_update) in enumerate(self._pool_timestamps.items()):
                if (datetime.now() - last_update).total_seconds() >= self.timeout_seconds:
                    logging.getLogger(self.master_queue_name).warning(
                        f"Flavor {self.name} is dropping pool {pool} for inactivity. Last update: {last_update}."
                    )
            self._pool_timestamps = {
                k:t for k,t in self._pool_timestamps.items() if (datetime.now() - t).total_seconds() < self.timeout_seconds
            }
        await self.ensure_resource_flavors()


class MasterQueue(BaseModel):
    name: str
    template: Dict[str, Any]
    flavors: List[Flavor]

    def register(self, name):
        global __MASTER_QUEUES__
        __MASTER_QUEUES__[name] = self

    @classmethod
    def from_memory(cls, name) -> Self:
        global __MASTER_QUEUES__
        return __MASTER_QUEUES__[name]

    @classmethod
    async def from_kubernetes(cls, k8s_resource: Dict[str, Any]):
        template = k8s_resource.get('spec', {}).get('template', {})
        flavors = []
        for flavor_spec in k8s_resource.get('spec', {}).get('flavors', []):
            if 'localFlavor' in flavor_spec.keys():
                flavors.append(
                    LocalFlavor(
                        master_queue_name=k8s_resource.get('metadata', {}).get("name"),
                        name=flavor_spec['localFlavor'].get("name", "error-flavor-name-is-missing"),
                        nominal_quota=flavor_spec['localFlavor'].get("nominalQuota", {}),
                    )
                )
            elif 'natsFlavor' in flavor_spec.keys():
                flavors.append(
                    await NatsFlavor(
                        master_queue_name=k8s_resource.get('metadata', {}).get("name"),
                        name=flavor_spec['natsFlavor'].get("name", "error-flavor-name-is-missing"),
                        nominal_quota=flavor_spec['natsFlavor'].get("nominalQuota", {}),
                        nats_connector=flavor_spec['natsFlavor'].get("natsConnector", "nats://nats.nats:4222"),
                        virtual_node=flavor_spec['natsFlavor'].get("virtualNode"),
                        nats_subject=flavor_spec['natsFlavor'].get("natsSubject", 'interlink.resources'),
                        pools=flavor_spec['natsFlavor'].get("pools", []),
                        pool_reg_exp=flavor_spec['natsFlavor'].get("poolRegExp"),
                        timeout_seconds=flavor_spec['natsFlavor'].get("poolTimeout", 60),
                    ).subscribe()
                )

        return cls(
            name=k8s_resource.get('metadata', {}).get("name"),
            template=template,
            flavors=flavors,
        )

    def get_cluster_queue_body(self):
        ret = dict(
            apiVersion="kueue.x-k8s.io/v1beta1",
            kind="ClusterQueue",
            metadata=dict(
                name=self.name,
            ),
            spec=dict(
                **self.template,
                resourceGroups=[
                    dict(
                        coveredResources=list(RESOURCES_KEYS),
                        flavors=sum([flavor.flavors for flavor in self.flavors], []),
                    )
                ]
            )
        )
        kopf.adopt(ret)
        return ret

__MASTER_QUEUES__: Dict[str, MasterQueue] = dict()


@kopf.on.create('vk.io', 'v1', 'masterqueues')
@kopf.on.resume('vk.io', 'v1', 'masterqueues')
async def create_fn(body, **kwargs):
    name = body.get("metadata", {}).get("name", "")
    logging.info(f"A handler is called for MasterQueue `{name}` with body: {body}")
    master_queue = await MasterQueue.from_kubernetes(body)
    master_queue.register(name)
    logging.info(f"Defined flavors: {', '.join([f.name for f in master_queue.flavors])}")

    api = k8s.client.CustomObjectsApi()
    try:
        api.create_cluster_custom_object(
            group='kueue.x-k8s.io',
            version='v1beta1',
            plural='clusterqueues',
            body=master_queue.get_cluster_queue_body(),
        )
    except k8s.client.exceptions.ApiException as e:
        if e.status == 409: # Already exists
            pass
        else:
            raise e

@kopf.on.update('vk.io', 'v1', 'masterqueues')
async def update_fn(body, **kwargs):
    name = body.get("metadata", {}).get("name", "")
    logging.info(f"MasterQueue `{name}` was updated.")
    new_mq = await MasterQueue.from_kubernetes(body)
    try:
        old_mq = MasterQueue.from_memory(name)
    except KeyError:
        raise kopf.TemporaryError(f"MasterQueue {name} not initialized", delay=5)

    new_flavors = {f.get_flavor_uid(): f for f in new_mq.flavors}
    old_flavors = {f.get_flavor_uid(): f for f in old_mq.flavors}

    required_updates = []

    if new_mq.template != old_mq.template:
        logging.info(f"Updating ClusterQueue template for queue {name}")
        old_mq.template = new_mq.template
        required_updates += list(new_mq.template.keys())

    if new_flavors != old_flavors:
        logging.info(f"Updating Flavors for queue {name}")
        required_updates.append("resourceGroups")
        for flavor_uid, flavor in new_flavors.items():
            old_flavor = old_flavors.get(flavor_uid)
            if old_flavor is not None and isinstance(flavor, NatsFlavor):
                for field in 'allocatable_quota', '_connection', '_subscription', '_pool_timestamps':
                    setattr(flavor, field, getattr(old_flavor, field))
        new_mq.register(name)

    patch = dict(
        spec={
            k: v
            for k, v in new_mq.get_cluster_queue_body().get("spec", {}).items()
            if k in required_updates
        }
    )

    if len(patch['spec']):
        k8s.client.CustomObjectsApi().patch_cluster_custom_object(
            group='kueue.x-k8s.io',
            version='v1beta1',
            plural='clusterqueues',
            name=name,
            body=patch,
        )

@kopf.timer('vk.io', 'v1', 'masterqueues', interval=2, initial_delay=2)
async def tick_masterqueue(body, **kwargs):
    name = body.get("metadata", {}).get("name", "")
    try:
        mq = MasterQueue.from_memory(name)
    except KeyError:
        raise kopf.TemporaryError(f"MasterQueue `{name}` not initialized?", delay=5.)

    for flavor in mq.flavors:
        await flavor.on_tick()

    api = k8s.client.CustomObjectsApi()
    api.patch_cluster_custom_object(
        group='kueue.x-k8s.io',
        version='v1beta1',
        name=name,
        plural='clusterqueues',
        body=mq.get_cluster_queue_body(),
    )

    ### Rimangono da gestire almeno due casi importanti:
    ### 1. modifica delle quote in MasterQueue


