import traceback
from contextlib import asynccontextmanager
from traceback import format_exc
import tarfile
from pathlib import Path
import shutil
import os.path

import podman
import podman.errors


from copy import copy

from fastapi import HTTPException

from .. import interlink
from ..utils import  compute_pod_resource, JobStatus, Resources
from . import configuration as cfg
from ..BaseNatsProvider import BaseNatsProvider
from ..apptainer_cmd_builder import BuildConfig

from .volumes import BindVolume, TmpFS

class PodmanProvider(BaseNatsProvider):
    def __init__(self, **kwargs):
        BaseNatsProvider.__init__(self, **kwargs)

        self._sandbox = cfg.LOCAL_SANDBOX
        self._podman_base_url = cfg.PODMAN_BASE_URL
        self._podman_client = podman.PodmanClient(base_url=self._podman_base_url)

        if not self._podman_client.ping():
            raise IOError("Cannot contact podman service. Please make sure it is available.")
        self.logger.info(f"Pulling image {cfg.CUSTOM_PILOT}")
        self._podman_client.images.pull(*(cfg.CUSTOM_PILOT.split(":")))


    def customize_build_config(self, build_config: BuildConfig):
        build_config = build_config.model_copy(deep=True)
        build_config.volumes.scratch_area = "/scratch"
        build_config.volumes.apptainer_cachedir = "/cache"
        build_config.volumes.image_dir = "/images"

        if len(build_config.volumes.additional_directories_in_path):
            self.logger.warning(
                "Build configuration additional_directories_in_path will be ignored. Define a CUSTOM_PILOT instead."
            )

        return build_config

    @asynccontextmanager
    async def podman(self):
        yield self._podman_client

    async def create_pod(self, job_name: str, job_sh: str, pod: interlink.PodRequest) -> str:
        """
        Submit the job to Podman as a container
        """

        sandbox = Path(self._sandbox) / job_name
        apptainer_cachedir = Path(self.build_config.volumes.apptainer_cachedir)
        scratch_area = Path(self.build_config.volumes.scratch_area) / job_name

        target_build_config = self.customize_build_config(self.build_config)


        # Ensure directories exists
        for dirname in apptainer_cachedir, scratch_area, sandbox:
            Path(dirname).mkdir(parents=True, exist_ok=True)

        async with self.podman() as client:
            try:
                pilot = client.containers.get(job_name)
            except podman.errors.exceptions.NotFound:
                pass
            else:
                self.logger.error(f"Pod {job_name} has been submitted already. Ignored.")
                return job_name

            try:
                pilot = client.containers.run(
                    name=job_name,
                    image=cfg.CUSTOM_PILOT,
                    command=["/bin/bash", "-c", job_sh],
                    detach=True,
                    privileged=True,
                    mem_limit=compute_pod_resource(pod, resource='memory'),
                    cpu_shares=compute_pod_resource(pod, resource='millicpu'),
                    workdir="/sandbox",
                    mounts=[
                        BindVolume(
                            source=str(apptainer_cachedir),
                            target=target_build_config.volumes.apptainer_cachedir,
                        ).model_dump(),
                        BindVolume(
                            source=str(scratch_area),
                            target=target_build_config.volumes.scratch_area,
                        ).model_dump(),
                       BindVolume(
                           source=str(sandbox),
                           target="/sandbox",
                       ).model_dump(),
                    ] + (
                        [
                            BindVolume(
                                source=cfg.CVMFS_MOUNT_POINT,
                                target="/cvmfs",
                                read_only=True,
                            ).model_dump(),
                        ] if cfg.CVMFS_MOUNT_POINT else []
                    ) + (
                        # image_dir is only mounted if it exists
                        [
                            BindVolume(
                                source=self.build_config.volumes.image_dir,
                                target=target_build_config.volumes.image_dir,
                                read_only=True
                            ).model_dump(),
                        ] if os.path.exists(self.build_config.volumes.image_dir) else []
                    )
                )
                self.logger.info(f"Created podman container with ID {pilot.id}")
            except Exception as e:
                self.logger.critical(f"Failed creating pod {job_name}.")
                self.logger.critical(format_exc())
                raise HTTPException(502, str(e))

        return job_name

    async def get_pod_status_and_logs(self, job_name: str) -> JobStatus:
        async with self.podman() as client:
            try:
                pilot = client.containers.get(job_name)
            except podman.errors.exceptions.NotFound:
                if job_name in self.subscribed_jobs:
                    self.logger.warning(f"No container found for job {job_name} not found. Maybe it is pending?")
                    return JobStatus(phase="pending")
                self.logger.critical(f"Requested status for {job_name}, not among the jobs managed by this instance")
                return JobStatus(phase="unknown")
            except Exception as e:
                self.logger.critical(format_exc())
                return JobStatus(phase="unknown")

            self.logger.info(f"Retrieved job {job_name} running in container {pilot.id}")
            if pilot.status == "unknown":
                return JobStatus(phase="pending")

            if pilot.status == 'running':
                return JobStatus(phase="running")

            try:
                with open(Path(self._sandbox) / job_name / "logs", "rb") as logs_file:
                    logs = logs_file.read()
            except FileNotFoundError:
                if pilot.status == "running":
                    logs = b""
                else:
                    self.logger.error(f"Failed retrieving output structure for job {job_name}")
                    return JobStatus(phase="failed")

            if pilot.status in ['exited', 'stopped']:
                return JobStatus(phase="succeeded", logs_tarball=logs)

    async def delete_pod(self, job_name: str) -> None:
        async with self.podman() as client:
            try:
                pilot = client.containers.get(job_name)
            except podman.errors.exceptions.NotFound:
                self.logger.warning(f"Trying to delete job {job_name}, but no container is found.")
            else:
                pilot.remove(force=True)

        sandbox = Path(self._sandbox) / job_name
        try:
            shutil.rmtree(sandbox)
        except FileNotFoundError:
            self.logger.warning(f"Trying to delete job {job_name}, but no sandbox volume is found.")
        except OSError as e:
            self.logger.critical(f"Failed deleting sandbox for job {job_name}: {sandbox}")
            self.logger.critical(e, exc_info=True)

