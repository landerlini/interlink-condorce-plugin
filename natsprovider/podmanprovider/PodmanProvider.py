from contextlib import asynccontextmanager

import podman

from copy import copy
from .. import interlink
from ..utils import  compute_pod_resource, JobStatus
from . import configuration as cfg
from ..BaseNatsProvider import BaseNatsProvider
from ..apptainer_cmd_builder import BuildConfig

class PodmanProvider(BaseNatsProvider):
    def __init__(self, nats_server: str, nats_queue: str, build_config: BuildConfig, interactive_mode: bool):
        self._volumes = copy(build_config.volumes)
        build_config.volumes.scratch_area = "/scratch"
        build_config.volumes.apptainer_cachedir = "/cache"
        build_config.volumes.image_dir = "/images"

        BaseNatsProvider.__init__(
            self,
            nats_server=nats_server,
            nats_queue=nats_queue,
            interactive_mode=interactive_mode,
            build_config=build_config,
        )
        self._podman_base_url = cfg.PODMAN_BASE_URL

        if len(build_config.volumes.additional_directories_in_path):
            self.logger.warning(
                "Build configuration additional_directories_in_path will be ignored. Define a CUSTOM_PILOT instead."
            )

        with podman.PodmanClient(base_url=self._podman_base_url) as client:
            if not client.ping():
                raise IOError("Cannot contact podman service. Please make sure it is available.")


    @asynccontextmanager
    async def podman(self):
        with podman.PodmanClient(base_url=self._podman_base_url) as client:
            yield client


    async def create_pod(self, job_name: str, job_sh: str, pod: interlink.PodRequest) -> str:
        """
        Submit the job to Podman as a container
        """
        async with self.podman() as client:
            pilot = client.containers.run(
                name=job_name,
                image=cfg.CUSTOM_PILOT,
                command=["/bin/bash", "-c", job_sh],
                detach=True,
                privileged=True,
                mem_limit=compute_pod_resource(pod, resource='memory'),
                cpu_shares=compute_pod_resource(pod, resource='millicpu'),
                volumes={
                    self._volumes.image_dir: dict(bind=self._build_config.volumes.image_dir, mode='ro'),
                    self._volumes.apptainer_cachedir: dict(bind=self._build_config.volumes.apptainer_cachedir),
                    self._volumes.scratch_area: dict(bind=self._build_config.volumes.scratch_area),
                }
            )

        return pilot.id
