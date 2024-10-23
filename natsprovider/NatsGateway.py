import logging
from typing import Collection, Union
from contextlib import asynccontextmanager

import zlib

import orjson
from fastapi import HTTPException
import nats, nats.errors
import interlink

# Local
from .utils import NatsResponse, get_readable_jobid
from .apptainer_cmd_builder import from_kubernetes


class NatsGateway(interlink.provider.Provider):
    def __init__(self, nats_server: str, nats_subject: str, nats_timeout_seconds: float):
        super().__init__(None)
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
            raise HTTPException(502, "No compute backend is configured to manage the request")
        except nats.errors.TimeoutError as e:
            raise HTTPException(504, "Compute backend timeout")
        finally:
            await nc.drain()

    async def create_job(self, pod: interlink.PodRequest, volumes: Collection[interlink.Volume]) -> str:
        """
        Create the singularity job and forward it to the submitter via NATS
        """
        self.logger.info(f"Create pod {pod.metadata.name}.{pod.metadata.namespace} [{pod.metadata.uid}]")
        builder = from_kubernetes(pod.model_dump(), [volume.model_dump() for volume in volumes])

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
                    ".".join((self._nats_subject, "create", get_readable_jobid(pod))),
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
        self.logger.info(f"Delete pod {pod.metadata.name}.{pod.metadata.namespace} [{pod.metadata.uid}]")
        async with self.nats_connection() as nc:
            await nc.publish(
                ".".join((self._nats_subject, "delete", get_readable_jobid(pod))),
                zlib.compress(orjson.dumps(pod.model_dump())),
            )

    async def get_pod_status(self, pod: interlink.PodRequest) -> Union[interlink.PodStatus, None]:
        """
        Request through NATS the status of a pod.
        """
        self.logger.info(f"Query status of pod {pod.metadata.name}.{pod.metadata.namespace} [{pod.metadata.uid}]")
        async with self.nats_connection() as nc:
            status_response = NatsResponse.from_nats(
                await nc.request(
                    ".".join((self._nats_subject, "status", get_readable_jobid(pod))),
                    zlib.compress(orjson.dumps(pod.model_dump())),
                )
            )
            status_response.raise_for_status()

        return interlink.PodStatus(**status_response.data)

    async def get_pod_logs(self, log_request: interlink.LogRequest) -> str:
        """
        Request through NATS the logs of a pod
        """
        self.logger.info(f"Requested log of pod {log_request.PodName}.{log_request.Namespace} [{log_request.PodUID}]")
        async with self.nats_connection() as nc:
            log_response = NatsResponse.from_nats(
                await nc.request(
                    ".".join((self._nats_subject, "status", get_readable_jobid(log_request))),
                    zlib.compress(orjson.dumps(log_request.model_dump())),
                )
            )
            log_response.raise_for_status()

        return log_response.text
