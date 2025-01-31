import json
import os
from copy import copy
import kubernetes as k8s
from kubernetes.client import V1SecurityContext

# Local
from natsprovider.BaseNatsProvider import BaseNatsProvider
from natsprovider.apptainer_cmd_builder import BuildConfig
from ..podmanprovider.volumes import BindVolume
from ..utils import  compute_pod_resource, JobStatus, Resources, TokenManager
from .. import interlink
from . import configuration as cfg

# Setup kubernetes and connection to the cluster

class KubernetesProvider(BaseNatsProvider):
    def __init__(
            self,
            build_config: BuildConfig,
            **kwargs,
    ):
        self._volumes = copy(build_config.volumes)
        build_config.volumes.scratch_area = "/scratch"
        build_config.volumes.apptainer_cachedir = "/cache"
        build_config.volumes.image_dir = "/images"

        if os.environ.get("KUBECONFIG", "") == "":
            k8s.config.load_incluster_config()
        else:
            k8s.config.load_kube_config()

        self._kubernetes = k8s.client.ApiClient()

        BaseNatsProvider.__init__(
            self,
            build_config=build_config,
            **kwargs,
        )

    async def close_connections_callback(self):
        await self._kubernetes.close()

    async def create_pod(self, job_name: str, job_sh: str, pod: interlink.PodRequest) -> str:
        job = k8s.client.V1Pod(
            api_version="v1",
            kind="Pod",
            metadata=k8s.client.V1ObjectMeta(
                name=job_name,

            ),
            spec=k8s.client.V1PodSpec(
                containers=[
                    k8s.client.V1Container(
                        name="pilot",
                        image=cfg.CUSTOM_PILOT,
                        command=["/bin/bash", "-c", job_sh],
                        security_context=V1SecurityContext(
                            privileged=cfg.PRIVILEGED,
                        ),
                        resources=k8s.client.V1ResourceRequirements(
                            requests=dict(
                                cpu=compute_pod_resource(pod, resource='cpu'),
                                memory=compute_pod_resource(pod, resource='memory'),
                            )
                        ),
                        working_dir="/sandbox",
                        volume_mounts=[
                            dict(name='cache', mount_path=self._build_config.apptainer_cachedir),
                            dict(name='scratch', mount_path=self._build_config.scratch_area),
                            dict(name='sandbox', mount_path="/sandbox"),
                        ]
                    )
                ],
                volumes=[
                    k8s.client.V1Volume(name="cache", empty_dir=k8s.client.V1EmptyDirVolumeSource()),
                    k8s.client.V1Volume(name="scratch", empty_dir=k8s.client.V1EmptyDirVolumeSource()),
                    k8s.client.V1Volume(name="medium", empty_dir=k8s.client.V1EmptyDirVolumeSource()),
                ]
            )
        )

        k8s.client.CoreV1Api(self._kubernetes).create_namespaced_pod(
            name=job_name,
            namespace=cfg.NAMESPACE,
            body=job,
        )

        return job_name

    async def delete_pod(self, job_name: str) -> None:
        k8s.client.CoreV1Api(self._kubernetes).delete_namespaced_pod(name=job_name, namespace=cfg.NAMESPACE)

    async def get_pod_status_and_logs(self, job_name: str) -> JobStatus:
        try:
            pod = k8s.client.CoreV1Api(self._kubernetes).read_namespaced_pod(name=job_name, namespace=cfg.NAMESPACE)
        except k8s.client.ApiException as e:
            if e.status == 404:
                if job_name in self.subscribed_jobs:
                    self.logger.warning(f"No container found for job {job_name} not found. Maybe it is pending?")
                    return JobStatus(phase="pending")
                self.logger.critical(f"Requested status for {job_name}, not among the jobs managed by this instance")
                return JobStatus(phase="unknown")
        else:
            if pod.status.phase.lower() in ("running", "pending", "unknown"):
                return JobStatus(phase=pod.status.phase.lower())
            elif pod.status.phase.lower() in ("succeeded", "failed"):
                log = k8s.client.CoreV1Api(self._kubernetes).read_namespaced_pod_log(
                    name=job_name,
                    namespace=cfg.NAMESPACE,
                    container='pilot'
                )
                return JobStatus(phase=pod.status.phase.lower())



        return JobStatus()


