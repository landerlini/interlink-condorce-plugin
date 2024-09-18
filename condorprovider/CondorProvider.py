from typing import Union, Collection
import logging
import uuid
from pprint import pformat

from fastapi import HTTPException
import interlink

from . import configuration as cfg, CondorConfiguration
from .apptainer_cmd_builder import from_kubernetes

CondorConfiguration.initialize_htcondor()

class CondorProvider(interlink.provider.Provider):
    def __init__(self):
        super().__init__(None)
        self.logger = logging.getLogger(self.__class__.__name__)
        self.condor = CondorConfiguration()
        
        self.logger.info("Starting CondorProvider")

    @staticmethod
    def get_readable_uid(pod: Union[interlink.PodRequest, interlink.LogRequest]):
        """Internal. Return a readable unique id used to name the pod."""
        if isinstance(pod, interlink.PodRequest):
            name = pod.metadata.name
            namespace = pod.metadata.namespace
            uid = pod.metadata.uid
        elif isinstance(pod, interlink.LogRequest):
            name = pod.PodName
            namespace = pod.Namespace
            uid = pod.PodUID
        else:
            raise HTTPException(500, f"Unexpected pod or log request of type {type(pod)}")

        short_name = '-'.join((namespace, name))[:20]
        return '-'.join((short_name, uid))

    async def create_job(self, pod: interlink.PodRequest, volumes: Collection[interlink.Volume]) -> str:
        """
        Submit the job to condor CE
        """
        self.logger.info(f"Create pod {pod.metadata.name}.{pod.metadata.namespace} [{pod.metadata.uid}]")
        builder = from_kubernetes(pod.model_dump(), [volume.model_dump() for volume in volumes])
        job_name = CondorProvider.get_readable_uid(pod)
        await self.condor.submit(builder.dump(), job_name=job_name)

        return job_name

    async def delete_pod(self, pod: interlink.PodRequest) -> None:
        await self.condor.delete_by_name(CondorProvider.get_readable_uid(pod))

    @staticmethod
    def create_container_states(container_state: V1ContainerState) -> interlink.ContainerStates:
        return interlink.ContainerStates(
            waiting=interlink.StateWaiting(
                message="Pending",
                reason="Unknown",
            )
        )

    @staticmethod
    async def _is_job_suspended(job_name: str) -> bool:
        """
        Return True if the job.spec.suspend is true. If true, kueue scheduled the job.
        """
        # async with kubernetes_api('batch', ignored_statuses=[404]) as k8s:
        #     job = await k8s.read_namespaced_job(
        #         namespace=cfg.NAMESPACE,
        #         name=job_name
        #     )

        #     logging.getLogger("is_job_suspended").debug(
        #         f"job.spec.suspend: {job.spec.suspend} (boolean: {job.spec.suspend == True})"
        #     )

        #     return job.spec.suspend


    async def get_pod_status(self, pod: interlink.PodRequest) -> Union[interlink.PodStatus, None]:
        self.logger.info(f"Status of pod {pod.metadata.name}.{pod.metadata.namespace} [{pod.metadata.uid}]")

        container_statuses = []

        self.logger.debug(f"Container statuses: " + pformat(container_statuses))

        return interlink.PodStatus(
            name=pod.metadata.name,
            UID=pod.metadata.uid,
            namespace=pod.metadata.namespace,
            containers=[
                interlink.ContainerStatus(
                    name=cs.name,
                    state=self.create_container_states(cs.state),
                ) for cs in container_statuses
            ]
        )

    async def get_pod_logs(self, log_request: interlink.LogRequest) -> str:
        self.logger.info(f"Log of pod {log_request.PodName}.{log_request.Namespace} [{log_request.PodUID}]")

        return "This condor backend does not allow accessing logs of running jobs."



