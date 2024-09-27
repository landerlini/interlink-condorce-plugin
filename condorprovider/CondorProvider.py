from typing import Union, Collection
import math
import logging
import tarfile

from fastapi import HTTPException
import interlink

from . import CondorConfiguration, CondorSubmit, CondorJobStatus
from .apptainer_cmd_builder import from_kubernetes
from .utils import make_uid_numeric, compute_pod_resource

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
        return '-'.join((short_name, f"{make_uid_numeric(uid):x}"))

    async def create_job(self, pod: interlink.PodRequest, volumes: Collection[interlink.Volume]) -> str:
        """
        Submit the job to condor CE
        """
        self.logger.info(f"Create pod {pod.metadata.name}.{pod.metadata.namespace} [{pod.metadata.uid}]")
        builder = from_kubernetes(pod.model_dump(), [volume.model_dump() for volume in volumes])
        job_name = CondorProvider.get_readable_uid(pod)
        condor_options = CondorSubmit(
            job_name=job_name,
            transfer_output_files=['logs'],
            request_cpus=compute_pod_resource(pod, resource='cpu'),
            request_memory=compute_pod_resource(pod, resource='memory'),
        )
        await self.condor.submit(builder.dump(), condor_options)

        return job_name

    async def delete_pod(self, pod: interlink.PodRequest) -> None:
        await self.condor.delete_by_name(CondorProvider.get_readable_uid(pod))

    async def get_pod_status(self, pod: interlink.PodRequest) -> Union[interlink.PodStatus, None]:
        self.logger.info(f"Status of pod {pod.metadata.name}.{pod.metadata.namespace} [{pod.metadata.uid}]")
        job_name = CondorProvider.get_readable_uid(pod)
        status = await self.condor.status_by_name(job_name)

        container_statuses = []

        if status == CondorJobStatus.held:
            container_statuses += [
                interlink.ContainerStatus(
                    name=cs.name,
                    state=interlink.ContainerStates(
                        waiting=interlink.StateWaiting(
                            message="Spooling",
                            reason="Job in Held status"
                        )
                    )
                ) for cs in (pod.spec.containers or []) + (pod.spec.initContainers or [])
            ]

        elif status == CondorJobStatus.idle:
            container_statuses += [
                interlink.ContainerStatus(
                    name=cs.name,
                    state=interlink.ContainerStates(
                        waiting=interlink.StateWaiting(
                            message="HTCondor queue",
                            reason="Pending"
                        )
                    )
                ) for cs in (pod.spec.containers or []) + (pod.spec.initContainers or [])
            ]

        elif status == CondorJobStatus.running:
            container_statuses += [
                interlink.ContainerStatus(
                    name=cs.name,
                    state=interlink.ContainerStates(
                        running=interlink.StateRunning()
                    )
                ) for cs in (pod.spec.containers or []) + (pod.spec.initContainers or [])
            ]

        elif status == CondorJobStatus.removed:
            self.logger.error(f"Requested status for a removed job: {job_name}. Returning exitCode 404.")
            container_statuses += [
                interlink.ContainerStatus(
                    name=cs.name,
                    state=interlink.ContainerStates(
                        terminated=interlink.StateTerminated(
                            exitCode=404,
                            reason="Failed",
                        )
                    )
                ) for cs in (pod.spec.containers or []) + (pod.spec.initContainers or [])
            ]

        elif status == CondorJobStatus.completed:
            builder = from_kubernetes(pod.model_dump(), use_fake_volumes=True)
            output_struct = await self.condor.retrieve_by_name(job_name, cleanup=False)
            builder.process_logs(output_struct['logs'])
            container_statuses += [
                interlink.ContainerStatus(
                    name=cs.name,
                    state=interlink.ContainerStates(
                        terminated=interlink.StateTerminated(
                            exitCode=builder.containers[i_container].return_code,
                            reason="Failed" if builder.containers[i_container].return_code else "Completed",
                        )
                    )
                ) for i_container, cs in enumerate(pod.spec.containers or [])
            ]
            container_statuses += [
                interlink.ContainerStatus(
                    name=cs.name,
                    state=interlink.ContainerStates(
                        terminated=interlink.StateTerminated(
                            exitCode=builder.init_containers[i_container].return_code,
                            reason="Failed" if builder.init_containers[i_container].return_code else "Completed",
                        )
                    )
                ) for i_container, cs in enumerate(pod.spec.initContainers or [])
            ]

        return interlink.PodStatus(
            name=pod.metadata.name,
            UID=pod.metadata.uid,
            namespace=pod.metadata.namespace,
            containers=container_statuses
        )


    async def get_pod_logs(self, log_request: interlink.LogRequest) -> str:
        self.logger.info(f"Log of pod {log_request.PodName}.{log_request.Namespace} [{log_request.PodUID}]")
        job_name = CondorProvider.get_readable_uid(log_request)
        status = await self.condor.status_by_name(job_name)

        if status in [CondorJobStatus.idle, CondorJobStatus.held]:
            return ""

        if status == CondorJobStatus.running:
            return "Unfortunately the log cannot retrieved for a running job... "

        if status != CondorJobStatus.completed:
            return f"Error. Cannot return log for job status '{status}'"

        output_struct = await self.condor.retrieve_by_name(job_name, cleanup=False)
        if 'logs' not in output_struct.keys():
            self.logger.error(f"Requested a log for job {job_name}, but log is not available in condor output_files.")
            return f"Error. Log was not stored or could not be retrieved."

        with tarfile.open(fileobj=output_struct['logs'], mode='r:*') as tar:
            for member in tar.getmembers():
                if member.isfile():
                    print (f"Pod has log for container {member.name}, requested {log_request.ContainerName}.log")
                    if member.name in [log_request.ContainerName + ".log", log_request.ContainerName + ".log.init"]:
                        full_log = tar.extractfile(member).read().decode('utf-8')

        if log_request.Opts.Tail is not None:
            return "\n".join(full_log.split('\n')[-log_request.Opts.Tail:])

        return full_log
