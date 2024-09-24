import os.path
import base64
import re
from pprint import pprint
from kubernetes import client as k8s
from typing import Dict, Any, List, Mapping, Optional, Union, Literal

from kubernetes.client import V1Container, V1KeyToPath

from condorprovider.apptainer_cmd_builder import ApptainerCmdBuilder, ContainerSpec, volumes, configuration as cfg
from condorprovider.apptainer_cmd_builder.volumes import BaseVolume
from condorprovider.utils import deserialize_kubernetes

def _create_static_volume_dict(
        volume_source_by_name: Dict[str, Dict[Literal['volume_name', 'items'], Any]],
        volume_definitions: List[Union[k8s.V1ConfigMap, k8s.V1Secret]],
):
    """
    Internal. Creates a dictionary mapping the name of each volume to a StaticVolume object that can then
    be mounted in containers with .mount(<path>).

    volume_source_by_name: maps the volume name to the VolumeSource (the pointer to the Volume in the PodSpec)
    volume_definitions: List of configMap or secret objects

    """
    # Service function to resolve the correct key to retrieve either binary or ascii data from ConfigMaps and Secrets
    def get_data(vol, dtype: Literal['string', 'binary']):
        if vol is None: return {}
        if isinstance(vol, k8s.V1ConfigMap):
            return dict(string=vol.data, binary=vol.binary_data)[dtype] or {}
        if isinstance(vol, k8s.V1Secret):
            return dict(string=vol.string_data, binary=vol.data)[dtype] or {}
        raise TypeError(f"Expected volume of type {type(vol)}")

    # Service function to resolve the KeyToPath data structure by name
    def _resolve_key2path(key2paths: List[V1KeyToPath], key: str) -> str:
        for item in key2paths:
            if item.key == key:
                return item.path

    return {
        volume_source_by_name[vol.metadata.name]['volume_name']: volumes.StaticVolume(
            config={
                _resolve_key2path(volume_source_by_name[vol.metadata.name]['items'], k):
                    volumes.AsciiFileSpec(content=v)
                for k, v in get_data(vol, 'string').items()
            },
            binaries={
                _resolve_key2path(volume_source_by_name[vol.metadata.name]['items'], k):
                    volumes.BinaryFileSpec(content=base64.b64decode(v.encode('ascii')))
                for k, v in get_data(vol, 'binary').items()
            }
        )
        for vol in volume_definitions if vol.metadata.name in volume_source_by_name.keys()
    }

def _make_pod_volume_struct(
        pod: k8s.V1Pod,
        containers_raw: List[Dict[str, Any]]
    ):
    """
    Internal. Create a dictionary mapping the volume name to a BaseVolume object that can then be mounted
    in containers with .mount(<path>).
    """
    # Count the number of times volumes appear, this is mainly relevant to emptyDirs
    volumes_counts = {}
    for container in (pod.spec.containers or []) + (pod.spec.init_containers or []):
        for volume_mount in (container.volume_mounts or []):
            if volume_mount.name not in volumes_counts:
                volumes_counts[volume_mount.name] = 0
            volumes_counts[volume_mount.name] += 1

    pprint (volumes_counts)
    empty_dirs = [v for c in containers_raw for v in (c if c is not None else []).get('emptyDirs') or []]
    empty_dirs = {
        k: volumes.ScratchArea() if volumes_counts.get(k, 0) <= 1 else volumes.make_empty_dir()
        for k in [os.path.split(dir_path)[-1] for dir_path in set(empty_dirs)]
    }

    # Create a mapping for configmaps from the pod.spec.volumes structure: {cfgmap.name: cfgmap}
    config_maps = _create_static_volume_dict(
        volume_source_by_name={
            v.config_map.name: dict(volume_name=v.name, items=v.config_map.items)
            for v in (pod.spec.volumes or []) if v is not None and v.config_map is not None
        },
        volume_definitions=[
            deserialize_kubernetes(cm_raw, 'V1ConfigMap')
            for container in containers_raw
            for cm_raw in container.get('configMaps') or []
        ]
    )

    # Create a mapping for configmaps from the pod.spec.volumes structure: {secret.name: secret}
    secrets = _create_static_volume_dict(
        volume_source_by_name={
            v.secret.secret_name: dict(volume_name=v.name, items=v.secret.items)
            for v in (pod.spec.volumes or []) if v is not None and v.secret is not None
        },
        volume_definitions=[
            deserialize_kubernetes(cm_raw, 'V1Secret')
            for container in containers_raw
            for cm_raw in container.get('secrets') or []
        ],
    )

    fuse_vol = {
        vol_name: volumes.FuseVolume(fuse_mount_script=ann_val)
        for ann_key, ann_val in (pod.metadata.annotations or {}).items()
        for vol_name in re.findall("fuse.vk.io/([\w-]+)", ann_key)
    }

    cvmfs = {
        vol_name: volumes.BaseVolume(host_path="/cvmfs")
        for ann_key, ann_val in (pod.metadata.annotations or {}).items()
        for vol_name in re.findall("cvmfs.vk.io/([\w-]+)", ann_key)
    }

    return {
        **empty_dirs,
        **config_maps,
        **secrets,
        **fuse_vol,
        **cvmfs,
    }

def _make_container_list(
        containers: Optional[List[V1Container]] = None,
        pod_volumes: Optional[Mapping[str, BaseVolume]] = None,
        use_fake_volumes: bool = False,
        scratch_area: str = cfg.SCRATCH_AREA,
        is_init_container: bool = False,
) -> List[ContainerSpec]:
    """
    Internal. Creates a list of ContainerSpec objects, mounting the volumes defined by pod_volumes.

    :param containers: Kubernetes container objects
    :param pod_volumes: ApptainerCmdBuild volumes
    :return: a list of ContainerSpec
    """
    if containers is None:
        return []

    def _volumes_for_container(container):
        # NB: Uses pod_volumes and use_fake_volumes from outer scope
        if container.volume_mounts is None:
            return []

        if use_fake_volumes:
            return sum([
                    *[volumes.ScratchArea().mount(vm.mount_path) for vm in getattr(container, 'volume_mounts')],
                ], [])

        return sum([
                *[
                    pod_volumes.get(vm.name, volumes.ScratchArea()).mount(
                        vm.mount_path,
                        sub_path=vm.sub_path,
                        read_only=vm.read_only if vm.read_only is not None else False
                    )
                    for vm in getattr(container, 'volume_mounts')
                ],
                volumes.ScratchArea().mount(mount_path="/cache", read_only=False),
            ], [])

    prefix = "init-" if is_init_container else "run-"
    return [
        ContainerSpec(
            uid=prefix+c.name,
            entrypoint=c.command[0],
            args=c.command[1:] + (c.args if c.args is not None else []),
            image=c.image,
            volume_binds=_volumes_for_container(c),
            environment={env.name: env.value for env in (c.env or []) if env.value is not None},
            scratch_area=scratch_area,
        ) for c in containers
    ]


def _clean_keys_of_none_values(dictionary):
    """
    Service function to remove all keys corresponding to None values.
    """
    keys_to_drop = []
    for key, value in dictionary.items():
        if value is None:
            keys_to_drop.append(key)
        if isinstance(value, dict):
            _clean_keys_of_none_values(value)
        if isinstance(value, list):
            for entry in value:
                if isinstance(entry, dict):
                    _clean_keys_of_none_values(entry)

    for key in keys_to_drop:
        del dictionary[key]


def from_kubernetes(
        pod_raw: Dict[str, Any],
        containers_raw: Optional[List[Dict[str, Any]]] = None,
        use_fake_volumes: bool = False,
) -> ApptainerCmdBuilder:
    """
    :param pod_raw:
        the definition of the pod as a raw Python dictionary

    :param containers_raw:
        the list of containers as packed by InterLink, a list of one dictionary per container with
        keys for the container name, the empty dirs, the configmaps and the secrets. Dictionary values are
        the volume definition (and not the volume sources).
        Example:
        ```yaml
        raw_containers:
          - name: main
          - emptyDirs: 123
          - configMaps:
              - metadata:
                  name: my-cfg
                data:
                  file: |
                    hello world
          - secrets:
              - metadata:
                  name: my-scrt
                stringData:
                  another_file: |
                    hello world
        ```

    :param use_fake_volumes:
        replace all volumes with scratch area, this is mainly used to enable recreating the container structure
        for asynchronous processing of the return code and log that may happen after volumes have been cleaned up.

    :return:
        An instance of ApptainerCmdBuilder representing the pod
    """
    if 'kind' in pod_raw.keys() and 'apiVersion' in pod_raw.keys():
        if pod_raw['kind'] != 'Pod' and pod_raw['apiVersion'] != 'v1':
            pprint(pod_raw)
            raise ValueError("Invalid pod description")

    _clean_keys_of_none_values(pod_raw)

    pod = deserialize_kubernetes(pod_raw, 'V1Pod')
    pod_volumes = _make_pod_volume_struct(pod, containers_raw if containers_raw is not None else [])

    print ("::: From interlink :::")
    pprint (containers_raw)
    print ("::: To kubernetes :::")
    pprint(pod_volumes)

    scratch_area = os.path.join(cfg.SCRATCH_AREA, f".interlink.{pod.metadata.uid}")
    return ApptainerCmdBuilder(
        uid=pod.metadata.name,
        init_containers=_make_container_list(
            containers=pod.spec.init_containers,
            pod_volumes=pod_volumes,
            use_fake_volumes=use_fake_volumes,
            scratch_area=scratch_area,
            is_init_container=True,
        ),
        containers=_make_container_list(
            containers=pod.spec.containers,
            pod_volumes=pod_volumes,
            use_fake_volumes=use_fake_volumes,
            scratch_area=scratch_area,
            is_init_container=False,
        ),
        scratch_area=scratch_area
    )

