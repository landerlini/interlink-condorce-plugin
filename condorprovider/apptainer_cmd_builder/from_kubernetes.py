from dataclasses import dataclass
import json
import os.path
from distutils.command.config import config
from string import ascii_uppercase

from pprint import pprint
from kubernetes import client as k8s
from typing import Dict, Any, List, Mapping, Optional

from kubernetes.client import V1Container, V1KeyToPath

from condorprovider.apptainer_cmd_builder import ApptainerCmdBuilder, ContainerSpec, volumes
from condorprovider.apptainer_cmd_builder.volumes import make_empty_dir, VolumeBind, BaseVolume
from condorprovider.utils import deserialize_kubernetes

def _make_container_list(
        containers: Optional[List[V1Container]] = None,
        pod_volumes: Optional[Mapping[str, BaseVolume]] = None
    ):
    if containers is None:
        return []

    return [
        ContainerSpec(
            entrypoint=c.command[0],
            args=c.command[1:] + (c.args if c.args is not None else []),
            image=c.image,
            volume_binds=[] if c.volume_mounts is None else sum([
                *[pod_volumes[vm.name].mount(vm.mount_path) for vm in getattr(c, 'volume_mounts')],
            ], []),
        ) for c in containers
    ]

def _resolve_key2path(key2paths: List[V1KeyToPath], key: str) -> str:
    for item in key2paths:
        if item.key == key:
            return item.path


def _make_pod_volume_struct(
        pod: k8s.V1Pod,
        containers_raw: List[Dict[str, Any]]
    ):
    # Count the number of times volumes appear, this is mainly relevant to emptyDirs
    volumes_counts = {}
    for container in (pod.spec.containers or []) + (pod.spec.init_containers or []):
        for volume_mount in (container.volume_mounts or []):
            if volume_mount.name not in volumes_counts:
                volumes_counts[volume_mount.name] = 0
            volumes_counts[volume_mount.name] += 1

    empty_dirs = [v for c in containers_raw for v in (c if c is not None else []).get('empty_dirs', [])]
    empty_dirs = {
        k: volumes.ScratchArea() if volumes_counts.get(k, 0) <= 1 else volumes.make_empty_dir()
        for k in set(empty_dirs)
    }

    # Create a mapping for configmaps from the pod.spec.volumes structure: {cfgmap.name: cfgmap}
    config_maps = {
        v.config_map.name: v.config_map
        for v in (pod.spec.volumes or []) if v is not None and v.config_map is not None
    }

    config_maps = {
        cm.metadata.name: volumes.StaticVolume(
           config={
               _resolve_key2path(config_maps[cm.metadata.name].items, k): volumes.AsciiFileSpec(content=v)
               for k, v in (cm.data or {}).items()
           },
           binaries={
                _resolve_key2path(config_maps[cm.metadata.name].items, k): volumes.AsciiFileSpec(content=v)
                for k, v in (cm.binary_data or {}).items()
            }
        )
        for container in containers_raw
        for cm in [deserialize_kubernetes(cm_raw, 'V1ConfigMap') for cm_raw in container.get('config_maps', [])]
    }

    return {
        **empty_dirs,
        **config_maps,
    }


def from_kubernetes(pod_raw: Dict[str, Any], containers_raw: List[Dict[str, Any]]):
    if pod_raw['kind'] != 'Pod' and pod_raw['apiVersion'] != 'v1':
        pprint(pod_raw)
        raise ValueError("Invalid pod description")

    #pod = k8s.V1Pod(**{_to_snakecase(k): v for k, v in pod_raw.items()}).to_dict()
    pod = deserialize_kubernetes(pod_raw, 'V1Pod')
    pod_volumes = _make_pod_volume_struct(pod, containers_raw)

    app_pod = ApptainerCmdBuilder(
        init_containers=_make_container_list(pod.spec.init_containers, pod_volumes),
        containers=_make_container_list(pod.spec.containers, pod_volumes),
    )

    return app_pod




