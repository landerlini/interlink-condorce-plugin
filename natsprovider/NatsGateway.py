import io
import tarfile
import logging
from io import BytesIO
from typing import Collection, List, Union
from contextlib import asynccontextmanager

import kubernetes.client
import zlib

import orjson
from fastapi import HTTPException
import nats, nats.errors
from . import interlink

# Local
from .utils import NatsResponse, get_readable_jobid, JobStatus
from .apptainer_cmd_builder import from_kubernetes


class NatsGateway:
    def __init__(self, nats_server: str, nats_subject: str, nats_timeout_seconds: float):
        self.logger = logging.getLogger(self.__class__.__name__)
        self._nats_server = nats_server
        self._nats_subject = nats_subject
        self._nats_timeout_seconds = nats_timeout_seconds

        self.logger.info("Starting CondorProvider")

    @asynccontextmanager
    async def nats_connection(self):
        """
        Simple context manager to define standard error management
        """
        nc = await nats.connect(servers=self._nats_server)
        try:
            yield nc
        except nats.errors.NoRespondersError as e:
            self.logger.error(str(e))
            raise HTTPException(502, "No compute backend is configured to manage the request")
        except nats.errors.TimeoutError as e:
            self.logger.error(str(e))
            raise HTTPException(504, "Compute backend timeout")
        finally:
            await nc.drain()

    @staticmethod
    def retrieve_queue_from_tolerations(self, tolerations: List[kubernetes.client.V1Toleration]):
        queues = [t.value for t in tolerations if t.key == 'queue.vk.io']
        if len(queues) == 0:
            self.logger.error("Multi-queue submission is not supported, yet.")
            raise HTTPException(400, "Toleration queue.vk.io=<queue>:NoSchedule is mandatory")
        if len(queues) > 1:
            self.logger.error("Toleration queue.vk.io=<queue>:NoSchedule is mandatory")
            raise HTTPException(400, "Multi-queue submission is not supported, yet.")

        return queues[0]

    async def create_job(self, pod: interlink.PodRequest, volumes: Collection[interlink.Volume]) -> str:
        """
        Create the singularity job and forward it to the submitter via NATS
        """
        self.logger.info(f"Create pod {pod}")
        builder = from_kubernetes(pod.model_dump(), [volume.model_dump() for volume in volumes])
        queue = self.retrieve_queue_from_tolerations(pod.spec_.tolerations)

        nats_payload = dict(
            # job_sh is the single bash script running singularity/apptainer to mimic the pod behavior
            job_sh=builder.dump(),
            # pod is needed by the submitter to extract resource requests and limits, and backend-specific annotations
            pod=pod.model_dump(),
            # Volumes should not be needed by the submitter, as they are encoded in the job_sh script.
            # As they count against the 1 MB limits of NATS payload, they are dropped.
            # volumes=[v.model_dump() for v in volumes],
        )

        async with self.nats_connection() as nc:
            create_response = NatsResponse.from_nats(
                await nc.request(
                    ".".join((self._nats_subject, "create", queue, get_readable_jobid(pod))),
                    zlib.compress(orjson.dumps(nats_payload)),
                    timeout=self._nats_timeout_seconds,
                )
            )
            create_response.raise_for_status()

        # create_response should be the job name for the compute backend
        return create_response.text

    async def delete_pod(self, pod: interlink.PodRequest) -> None:
        """
        Publish the request to delete jobs from the remote backend. No confirmation is expected by interlink protocol.
        """
        self.logger.info(f"Delete pod {pod}")
        async with self.nats_connection() as nc:
            delete_response = await nc.publish(
                    ".".join((self._nats_subject, "delete", get_readable_jobid(pod))),
                    zlib.compress(orjson.dumps(pod.model_dump())),
                )

    async def get_pod_status(self, pod: interlink.PodRequest) -> Union[interlink.PodStatus, None]:
        """
        Request through NATS the status of a pod.
        """
        self.logger.info(f"Query status of pod {pod}")
        job_name = get_readable_jobid(pod)
        async with self.nats_connection() as nc:
            status_response = NatsResponse.from_nats(
                await nc.request(
                    ".".join((self._nats_subject, "status", job_name)),
                    zlib.compress(orjson.dumps(pod.model_dump())),
                )
            )

        status_response.raise_for_status()
        pod_metadata = pod.metadata_
        job_status = JobStatus(**status_response.data)

        container_statuses = []

        if job_status.phase == "pending":
            container_statuses += [
                interlink.ContainerStatus(
                    name=cs.name,
                    state=interlink.ContainerStates(
                        running=interlink.StateRunning()
                    )
                ) for cs in (pod.spec_.containers or []) + (pod.spec_.init_containers or [])
            ]

        elif job_status.phase == "unknown":
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
                ) for cs in (pod.spec_.containers or []) + (pod.spec_.init_containers or [])
            ]

        elif job_status.phase in ["succeeded", "failed"]:
            builder = from_kubernetes(pod.model_dump(), use_fake_volumes=True)
            builder.process_logs(BytesIO(job_status.logs_tarball))
            container_statuses += [
                interlink.ContainerStatus(
                    name=cs.name,
                    state=interlink.ContainerStates(
                        terminated=interlink.StateTerminated(
                            exitCode=builder.init_containers[i_container].return_code,
                            reason="Failed" if builder.init_containers[i_container].return_code else "Completed",
                        )
                    )
                ) for i_container, cs in enumerate(pod.spec_.init_containers or [])
            ]
            container_statuses += [
                interlink.ContainerStatus(
                    name=cs.name,
                    state=interlink.ContainerStates(
                        terminated=interlink.StateTerminated(
                            exitCode=builder.containers[i_container].return_code,
                            reason="Failed" if builder.containers[i_container].return_code else "Completed",
                        )
                    )
                ) for i_container, cs in enumerate(pod.spec_.containers or [])
            ]

        return interlink.PodStatus(
            name=pod_metadata.name,
            UID=pod_metadata.uid,
            namespace=pod_metadata.namespace,
            containers=container_statuses
        )


    async def get_pod_logs(self, log_request: interlink.LogRequest) -> str:
        """
        Request through NATS the logs of a pod
        """
        self.logger.info(f"Requested log of pod {log_request.PodName}.{log_request.Namespace} [{log_request.PodUID}]")
        job_name = get_readable_jobid(log_request)
        async with self.nats_connection() as nc:
            status_response = NatsResponse.from_nats(
                await nc.request(
                    ".".join((self._nats_subject, "status", job_name)),
                    zlib.compress(orjson.dumps(log_request.model_dump())),
                )
            )
            status_response.raise_for_status()

        job_status = JobStatus(**status_response.data)

        if job_status.phase in ["pending"]:
            return ""

        if job_status.phase in ["running"]:
            return "Unfortunately the log cannot retrieved for a running job... "

        if job_status.phase not in ["succeeded", "failed"]:
            return f"Error. Cannot return log for job status '{job_name}'"

        with tarfile.open(fileobj=io.BytesIO(job_status.logs_tarball), mode='r:*') as tar:
            for member in tar.getmembers():
                if member.isfile():
                    print (f"Pod has log for container {member.name}, requested {log_request.ContainerName}.log")
                    if member.name in [log_request.ContainerName + ".log", log_request.ContainerName + ".log.init"]:
                        full_log = tar.extractfile(member).read().decode('utf-8')

        if log_request.Opts.Tail is not None:
            return "\n".join(full_log.split('\n')[-log_request.Opts.Tail:])

        return full_log

    async def shutdown(self, subject: str):
        async with self.nats_connection() as nc:
            await nc.publish(".".join((self._nats_subject, "shutdown", subject)))
