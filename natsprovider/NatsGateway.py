import asyncio
import json
import io
import tarfile
import logging
import time
from io import BytesIO
from typing import Collection, Dict, List, Union
from contextlib import asynccontextmanager

import kubernetes.client
import zlib

import orjson
from fastapi import HTTPException
import nats, nats.errors, nats.aio.msg
import redis

from . import interlink
from . import configuration as cfg
from . import metrics

# Local
from .utils import NatsResponse, get_readable_jobid, JobStatus
from .apptainer_cmd_builder import from_kubernetes, BuildConfig


class NatsGateway:
    def __init__(self, nats_server: str, nats_subject: str, nats_timeout_seconds: float):
        self.logger = logging.getLogger(self.__class__.__name__)
        self._nats_server = nats_server
        self._nats_subject = nats_subject
        self._nats_timeout_seconds = nats_timeout_seconds
        self._nats_subs = dict()
        self.logger.info("Starting NatsGateway")
        self._redis = redis.from_url(cfg.REDIS_CONNECTOR) if cfg.REDIS_CONNECTOR is not None else None
        self._build_configs: Dict[str, BuildConfig] = dict()
        if self._redis:
            self._build_configs = {
                str(q): BuildConfig(**(json.loads(bc))) for q, bc in self._redis.hgetall('build_configs').items()
            }
            self.logger.info(f"Recovered build_configs from DB for pools {', '.join(list(self._build_configs.keys()))}")


    async def configure_nats_callbacks(self):
        listener_nc = await nats.connect(servers=self._nats_server)
        config_subject = ".".join((self._nats_subject, "config", "*"))
        self._nats_subs['config'] = await listener_nc.subscribe(
            subject=config_subject,
            cb=self.config_callback,
        )
        self.logger.info(f"Subscribed to config subject {config_subject}")
        return listener_nc

    async def config_callback(self, msg: nats.aio.msg.Msg):
        pool = msg.subject.split(".")[-1]
        if pool in self._build_configs.keys():
            self.logger.info(f"Received configuration for a new pool: {pool}")

        metrics.counters['build_config_updates'].labels(pool).inc()

        self._build_configs[pool] = BuildConfig(**orjson.loads(msg.data))
        if self._redis is not None:
            self._redis.hset('build_configs', pool, self._build_configs[pool].model_dump_json())

        self.logger.info(f"Received updated configuration for pool {pool}")

    @asynccontextmanager
    async def nats_connection(self):
        """
        Simple context manager to define standard error management
        """
        nc = await nats.connect(servers=self._nats_server)
        try:
            start = time.monotonic_ns()
            yield nc
            stop = time.monotonic_ns()
            metrics.summaries['nats_response_time'].observe(stop - start)
        except nats.errors.NoRespondersError as e:
            self.logger.error(str(e))
            metrics.counters['nats_errors'].labels('No backend').inc()
            raise HTTPException(502, "No compute backend is configured to manage the request")
        except nats.errors.TimeoutError as e:
            self.logger.error(str(e))
            metrics.counters['nats_errors'].labels('Timeout').inc()
            raise HTTPException(504, "Compute backend timeout")
        finally:
            await nc.drain()

    def retrieve_pool_from_tolerations(self, tolerations: List[kubernetes.client.V1Toleration]):
        pools = [t.value for t in tolerations if t.key == 'pool.vk.io']
        if len(pools) == 0:
            self.logger.error("Toleration pool.vk.io=<pool>:NoSchedule is mandatory")
            raise HTTPException(400, "Toleration pool.vk.io=<pool>:NoSchedule is mandatory")
        if len(pools) > 1:
            self.logger.error("Multi-pool submission is not supported, yet.")
            raise HTTPException(400, "Multi-pool submission is not supported, yet.")

        return pools[0]

    async def create_job(self, pod: interlink.PodRequest, volumes: Collection[interlink.Volume]) -> str:
        """
        Create the singularity job and forward it to the submitter via NATS
        """
        v1pod = pod.deserialize()
        self.logger.info(f"Create pod {pod}")
        pool = self.retrieve_pool_from_tolerations(v1pod.spec.tolerations)
        if pool not in self._build_configs.keys():
            self.logger.error(f"Missing configuration for pool {pool}!")
        builder = from_kubernetes(
            pod.model_dump(),
            [volume.model_dump() for volume in volumes],
            build_config=self._build_configs.get(pool, BuildConfig()),
        )

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
            create_subject = ".".join((self._nats_subject, "create", pool, get_readable_jobid(pod)))
            self.logger.info(f"Submitting payload with subject: `{create_subject}`")
            create_response = NatsResponse.from_nats(
                await nc.request(
                    create_subject,
                    zlib.compress(orjson.dumps(nats_payload)),
                    timeout=self._nats_timeout_seconds,
                )
            )
            create_response.raise_for_status()

        self.logger.info(f"Payload `{create_subject}` submitted successfully")
        # create_response should be the job name for the compute backend
        return create_response.text

    async def delete_pod(self, pod: interlink.PodRequest) -> None:
        """
        Publish the request to delete jobs from the remote backend. No confirmation is expected by interlink protocol.
        """
        self.logger.info(f"Delete pod {pod} [{get_readable_jobid(pod)}]")
        async with self.nats_connection() as nc:
            delete_response = NatsResponse.from_nats(
                await nc.request(
                    ".".join((self._nats_subject, "delete", get_readable_jobid(pod))),
                    zlib.compress(orjson.dumps(pod.model_dump())),
                    timeout=self._nats_timeout_seconds,
                )
            )
            delete_response.raise_for_status()


    async def get_pod_status(self, pod: interlink.PodRequest) -> Union[interlink.PodStatus, None]:
        """
        Request through NATS the status of a pod.
        """
        job_name = get_readable_jobid(pod)
        self.logger.info(f"Query status of pod {pod} [{job_name}]")
        v1pod = pod.deserialize()
        pod_metadata = v1pod.metadata
        for i_attempt in range(cfg.NUMBER_OF_GETTING_STATUS_ATTEMPTS):
            if i_attempt > 0:
                await asyncio.sleep(cfg.MILLISECONDS_BETWEEN_GETTING_STATUS_ATTEMPTS * 1e-3)

            async with self.nats_connection() as nc:
                try:
                    status_response = NatsResponse.from_nats(
                        await nc.request(
                            ".".join((self._nats_subject, "status", job_name)),
                            zlib.compress(orjson.dumps(pod.model_dump())),
                            timeout=self._nats_timeout_seconds,
                        )
                    )
                except nats.errors.NoRespondersError as e:
                    self.logger.error(f"Failed to retrieve status for job {pod} [{job_name}]")
                    return None


            if ( # Conditions triggering a retrial: unknown phase, NotFound error and Server Internal Error
                    (status_response.data.get('phase', 'unknown') in ['unknown'])
                or  (status_response.status_code in [404, 500])
            ):
                continue
            break

        status_response.raise_for_status()
        job_status = JobStatus(**status_response.data)

        self.logger.info(
            f"Status of {pod} [{job_name}]: {job_status.phase} "
            f"[{'w/' if len(job_status.logs_tarball) else 'w/o'} logs]"
        )

        container_statuses = []
        init_container_statuses = []

        if job_status.phase == "pending":
            init_container_statuses += [
                interlink.ContainerStatus(
                    name=cs.name,
                    state=interlink.ContainerStates(
                        waiting=interlink.StateWaiting(
                            reason=job_status.reason if job_status.reason is not None else "Pending",
                            message="Pending"
                        )
                    )
                ) for cs in (v1pod.spec.init_containers or [])
            ]
            container_statuses += [
                interlink.ContainerStatus(
                    name=cs.name,
                    state=interlink.ContainerStates(
                        waiting=interlink.StateWaiting(
                            reason=job_status.reason if job_status.reason is not None else "Pending",
                            message="Pending"
                        )
                    )
                ) for cs in (v1pod.spec.containers or [])
            ]

        elif job_status.phase == "running":
            init_container_statuses += [
                interlink.ContainerStatus(
                    name=cs.name,
                    state=interlink.ContainerStates(
                        terminated=interlink.StateTerminated(
                            exitCode=0,
                            reason="Completed",
                            )
                        )
                ) for cs in (v1pod.spec.init_containers or [])
            ]
            container_statuses += [
                interlink.ContainerStatus(
                    name=cs.name,
                    state=interlink.ContainerStates(
                        running=interlink.StateRunning()
                    )
                ) for cs in (v1pod.spec.containers or []) 
            ]

        elif job_status.phase == "unknown":
            self.logger.error(f"Requested status for job: {job_name} unknown.")

            init_container_statuses += [
                interlink.ContainerStatus(
                    name=cs.name,
                    state=interlink.ContainerStates(
                        terminated=interlink.StateTerminated(
                            exitCode=404,
                            reason="Failed",
                        )
                    )
                ) for cs in (v1pod.spec.init_containers or [])
            ]

            container_statuses += [
                interlink.ContainerStatus(
                    name=cs.name,
                    state=interlink.ContainerStates(
                        terminated=interlink.StateTerminated(
                            exitCode=404,
                            reason="Failed",
                        )
                    )
                ) for cs in (v1pod.spec.containers or []) 
            ]

        elif job_status.phase in ['succeeded', 'failed'] and len(job_status.logs_tarball) == 0:
            self.logger.error(
                f"Requested status for job: {job_name}. Seems complete but no output is provided. Error 502."
            )

            init_container_statuses += [
                interlink.ContainerStatus(
                    name=cs.name,
                    state=interlink.ContainerStates(
                        terminated=interlink.StateTerminated(
                            exitCode=502,
                            reason="LostOutput",
                        )
                    )
                ) for cs in (v1pod.spec.init_containers or [])
            ]
            container_statuses += [
                interlink.ContainerStatus(
                    name=cs.name,
                    state=interlink.ContainerStates(
                        terminated=interlink.StateTerminated(
                            exitCode=502,
                            reason="LostOutput",
                        )
                    )
                ) for cs in (v1pod.spec.containers or []) 
            ]

        elif job_status.phase in ["succeeded", "failed"]:
            builder = from_kubernetes(pod.model_dump(), use_fake_volumes=True)
            builder.process_logs(BytesIO(job_status.logs_tarball))
            init_container_statuses += [
                interlink.ContainerStatus(
                    name=cs.name,
                    state=interlink.ContainerStates(
                        terminated=interlink.StateTerminated(
                            exitCode=builder.init_containers[i_container].return_code,
                            reason="Failed" if builder.init_containers[i_container].return_code else "Completed",
                        )
                    )
                ) for i_container, cs in enumerate(v1pod.spec.init_containers or [])
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
                ) for i_container, cs in enumerate(v1pod.spec.containers or [])
            ]

        if len(container_statuses) == 0:
            self.logger.critical("Could not retrieve the status of any container!")
            return None

        return interlink.PodStatus(
            name=pod_metadata.name,
            UID=pod_metadata.uid,
            namespace=pod_metadata.namespace,
            containers=container_statuses,
            initContainers=init_container_statuses
        )


    async def get_pod_logs(self, log_request: interlink.LogRequest) -> str:
        """
        Request through NATS the logs of a pod
        """
        job_name = get_readable_jobid(log_request)
        self.logger.info(f"Requested log of pod {log_request.PodName}.{log_request.Namespace} [{job_name}]")
        async with self.nats_connection() as nc:
            status_response = NatsResponse.from_nats(
                await nc.request(
                    ".".join((self._nats_subject, "status", job_name)),
                    zlib.compress(orjson.dumps(log_request.model_dump())),
                    timeout=self._nats_timeout_seconds,
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

        full_log = ""
        with tarfile.open(fileobj=io.BytesIO(job_status.logs_tarball), mode='r:*') as tar:
            for member in tar.getmembers():
                if member.isfile():
                    self.logger.debug(f"Pod has log for container {member.name}, requested {log_request.ContainerName}.log")
                    if member.name in [
                            "run-" + log_request.ContainerName + ".log",
                            "init-" + log_request.ContainerName + ".log",
                        ]:
                        full_log = tar.extractfile(member).read().decode('utf-8')

        if log_request.Opts.Tail is not None:
            return "\n".join(full_log.split('\n')[-log_request.Opts.Tail:])

        return full_log

    async def shutdown(self, subject: str):
        async with self.nats_connection() as nc:
            await nc.publish(".".join((self._nats_subject, "shutdown", subject)))
