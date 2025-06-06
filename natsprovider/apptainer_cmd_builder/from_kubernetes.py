import os.path
import base64
import re

from kubernetes import client as k8s
from typing import Dict, Any, List, Mapping, Optional, Union, Literal
from pprint import pprint

from kubernetes.client import V1Container, V1KeyToPath

from natsprovider.apptainer_cmd_builder import (
    ApptainerCmdBuilder,
    ContainerSpec,
    volumes,
    BuildConfig,
)
from natsprovider.apptainer_cmd_builder.volumes import BaseVolume
from natsprovider.interlink import deserialize_kubernetes

StaticVolKey = Literal['volume_name', 'items']

def _create_static_volume_dict(
        volume_source_by_name: Dict[str, Dict[StaticVolKey, Any]],
        volume_definitions: List[Union[k8s.V1ConfigMap, k8s.V1Secret]],
        build_config: BuildConfig,
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
            **build_config.base_volume_config(),
            config={
                _resolve_key2path(volume_source_by_name[vol.metadata.name]['items'], k):
                    volumes.AsciiFileSpec(content=v)
                for k, v in get_data(vol, 'string').items()
            },
            binaries={
                _resolve_key2path(volume_source_by_name[vol.metadata.name]['items'], k):
                    volumes.BinaryFileSpec(content=base64.b64decode(v.encode('ascii')))
                for k, v in get_data(vol, 'binary').items()
            },
        )
        for vol in volume_definitions if vol.metadata.name in volume_source_by_name.keys()
    }

def _make_pod_volume_struct(
        pod: k8s.V1Pod,
        containers_raw: List[Dict[str, Any]],
        build_config: BuildConfig
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

    pprint(volumes_counts)

    #empty_dirs = [v for c in containers_raw for v in (c if c is not None else []).get('emptyDirs') or []]
    empty_dirs = [
        v.name for v in pod.spec.volumes if v.empty_dir is not None
    ]
    pprint(empty_dirs)

    empty_dirs = {
        k:  volumes.ScratchArea(**build_config.base_volume_config()) if volumes_counts.get(k, 0) <= 1
            else volumes.make_empty_dir(build_config)
        for k in set(empty_dirs)
    }

    # Create a mapping for configmaps from the pod.spec.volumes structure: {cfgmap.name: cfgmap}
    config_maps = _create_static_volume_dict(
        volume_source_by_name={
            str(v.config_map.name): {"volume_name": v.name, "items": v.config_map.items}
            for v in (pod.spec.volumes or []) if v is not None and v.config_map is not None
        },
        volume_definitions=[
            deserialize_kubernetes(cm_raw, 'V1ConfigMap')
            for container in containers_raw
            for cm_raw in container.get('configMaps') or []
        ],
        build_config=build_config,
    )

    # Create a mapping for configmaps from the pod.spec.volumes structure: {secret.name: secret}
    secrets = _create_static_volume_dict(
        volume_source_by_name={
            str(v.secret.secret_name): {"volume_name": v.name, "items": v.secret.items}
            for v in (pod.spec.volumes or []) if v is not None and v.secret is not None
        },
        volume_definitions=[
            deserialize_kubernetes(cm_raw, 'V1Secret')
            for container in containers_raw
            for cm_raw in container.get('secrets') or []
        ],
        build_config=build_config,
    )

    fuse_vol = {
        vol_name: volumes.FuseVolume(fuse_mount_script=ann_val, **build_config.base_volume_config())
        for ann_key, ann_val in (pod.metadata.annotations or {}).items()
        for vol_name in re.findall("fuse.vk.io/([\w-]+)", ann_key)
    }

    cvmfs = {
        vol_name: volumes.BaseVolume(host_path_override="/cvmfs", **build_config.base_volume_config())
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
        build_config: BuildConfig,
        containers: Optional[List[V1Container]] = None,
        pod_volumes: Optional[Mapping[str, BaseVolume]] = None,
        use_fake_volumes: bool = False,
        is_init_container: bool = False,
        user_cache: Optional[str] = None,
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

        cache_volume = volumes.make_empty_dir(build_config) if user_cache is None else volumes.BaseVolume(
            init_script="mkdir %(host_path)s",
            host_path_override=user_cache,
            **build_config.base_volume_config()
        )

        return sum([
                *[
                    pod_volumes.get(vm.name, volumes.ScratchArea()).mount(
                        vm.mount_path,
                        sub_path=vm.sub_path,
                        read_only=vm.read_only if vm.read_only is not None else False
                    )
                    for vm in getattr(container, 'volume_mounts')
                ],
                cache_volume.mount(mount_path="/cache", read_only=False),
            ], [])

    prefix = "init-" if is_init_container else "run-"
    return [
        ContainerSpec(
            uid=prefix+c.name,
            entrypoint=c.command[0] if c.command is not None and len(c.command) else None,
            args=(c.command[1:] if c.command and len(c.command) else []) + (c.args if c.args is not None else []),
            image=c.image,
            volume_binds=_volumes_for_container(c),
            environment={env.name: env.value for env in (c.env or []) if env.value is not None},
            **build_config.container_spec_config(),
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
        build_config: BuildConfig = None,
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

    :param build_config:
        defines options to perform the script building

    :return:
        An instance of ApptainerCmdBuilder representing the pod
    """
    if 'kind' in pod_raw.keys() and 'apiVersion' in pod_raw.keys():
        if pod_raw['kind'] != 'Pod' and pod_raw['apiVersion'] != 'v1':
            raise ValueError("Invalid pod description")

    _clean_keys_of_none_values(pod_raw)

    if build_config is None:
        build_config = BuildConfig()

    pod = deserialize_kubernetes(pod_raw, 'V1Pod')
    pod_volumes = _make_pod_volume_struct(pod, containers_raw if containers_raw is not None else [], build_config)

    # Special paths
    scratch_area = os.path.join(build_config.volumes.scratch_area, f".interlink.{pod.metadata.uid}")
    user_cache = (
        os.path.join(build_config.volumes.apptainer_cachedir, pod.metadata.labels['user'])
        if 'user' in pod.metadata.labels.keys() else None
    )

    return ApptainerCmdBuilder(
        uid=pod.metadata.name,
        init_containers=_make_container_list(
            build_config=build_config,
            containers=pod.spec.init_containers,
            pod_volumes=pod_volumes,
            use_fake_volumes=use_fake_volumes,
            is_init_container=True,
            user_cache=user_cache,
        ),
        containers=_make_container_list(
            build_config=build_config,
            containers=pod.spec.containers,
            pod_volumes=pod_volumes,
            use_fake_volumes=use_fake_volumes,
            is_init_container=False,
            user_cache=user_cache,
        ),
        scratch_area=scratch_area,
        additional_directories_in_path=build_config.volumes.additional_directories_in_path,
        cachedir=build_config.volumes.apptainer_cachedir,
    )

