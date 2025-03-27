import re
import asyncio
import logging
import os
from typing import Dict, Union, List

from datetime import datetime
import zlib
from pprint import pformat
from contextlib import asynccontextmanager

from fastapi import HTTPException
import nats.aio.msg
from . import interlink
import orjson
import nats
import nats.errors

from .utils import NatsResponse, JobStatus, Resources
from . import configuration as cfg

from .apptainer_cmd_builder import BuildConfig

class BaseNatsProvider:
    """
    Base class implementing the logic to respond to NATS request by a submitter
    """
    def __init__(
            self,
            nats_server: str,
            nats_pool: str,
            build_config: BuildConfig,
            provider_config_key: str,
            resources: Resources,
            interactive_mode: bool = True,
            shutdown_subject: str = None,
            leader: bool = False
    ):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.logger.info(f"Starting {self.__class__.__name__}")

        self._nats_server = nats_server

        self._nats_subject = cfg.NATS_SUBJECT
        self._nats_pool = nats_pool
        self._nats_connection = None
        self._interactive_mode = interactive_mode
        self._shutdown_subject = shutdown_subject if shutdown_subject is not None else nats_pool
        self._build_config = build_config
        self._provider_config_key = provider_config_key
        self.leader = leader

        self._subscriptions = {}
        self._running = True
        self._latest_tick = dict()

        self._declared_resources = resources
        self._warned_on_unset_resources = list()

    @property
    def config(self):
        if not hasattr(self._build_config, self._provider_config_key):
            raise KeyError(f"BuildConfig does not configure provider `{self._provider_config_key}`")
        return hasattr(self._build_config, self._provider_config_key)

    @property
    def build_config(self):
        return self._build_config

    def required_updates(self, timer_key: str, delay_seconds: int):
        if (
                timer_key not in self._latest_tick.keys() or
                (datetime.now() - self._latest_tick[timer_key]).total_seconds() > delay_seconds
        ):
            self._latest_tick[timer_key] = datetime.now()
            yield

    @property
    def censored_nats_server(self):
        password = re.findall(r"\w+://[\w\d-]+:([^@]+)@.*", self._nats_server)
        if len(password):
            return self._nats_server.replace(password[0], "***")
        return self._nats_server

    def customize_build_config(self, build_config: BuildConfig):
        """Override this function to fix the build config with provider-specific logics"""
        return build_config

    async def maybe_refresh_build_config(self):
        for _ in self.required_updates('build_config', 60):
            self._build_config = self._build_config.reload()
            config_subject = '.'.join((self._nats_subject, 'config', self._nats_pool))
            async with self.nats_connection() as nc:
                await nc.publish(
                    subject=config_subject,
                    payload=self.customize_build_config(self._build_config).model_dump_json().encode()
                )
                self.logger.info(f"Published build options on subject {config_subject}")

    async def _register_status_and_delete_callbacks(self, job_name: str):
        """
        Helper function to subscribe to create and get-status NATS subjects.
        """
        async with self.nats_connection() as nc:
                    # Register status and logs callback
                    status_subject = '.'.join((self._nats_subject, 'status', job_name))
                    self._subscriptions[status_subject] = await nc.subscribe(
                        subject=status_subject,
                        cb=self.get_pod_status_and_logs_callback
                    )
                    # Register delete pod callback
                    delete_subject = '.'.join((self._nats_subject, 'delete', job_name))
                    self._subscriptions[delete_subject] = await nc.subscribe(
                        subject=delete_subject,
                        cb=self.delete_pod_callback
                    )


    async def resync(self):
        """
        Request the list of pods assigned to the pool. Warning: may lead to errors if multi-responder setup.
        """
        max_attempts = 5
        while max_attempts:
            try:

                resync_subject = '.'.join((self._nats_subject, 'resync', self._nats_pool))
                async with self.nats_connection() as nc:
                    response = NatsResponse.from_nats(
                        await nc.request(
                            resync_subject,
                            timeout=120,
                        )
                    )
            except nats.errors.TimeoutError:
                max_attempts -= 1
                self.logger.error(
                    f"Failed to retrieve list of pods from remote. {max_attempts} attempt(s) remaining."
                )
                await asyncio.sleep(3)
            else:
                break

        response.raise_for_status()

        self.logger.info(f"Retrieved {len(response.data)} pods assigned to pool {self._nats_pool}.")
        for cr in [self._register_status_and_delete_callbacks(job_name) for job_name in response.data]:
            await cr


    async def main_loop(self, time_interval: float = 0.2):
        """Main loop of the NATS responder"""
        create_subject = '.'.join((self._nats_subject, 'create', self._nats_pool, '*'))
        shutdown_subject = '.'.join((self._nats_subject, 'shutdown', self._shutdown_subject))
        async with self.nats_connection() as nc:
            if self.leader:
                await self.maybe_refresh_build_config()
                await self.maybe_publish_resources()
                await self.resync()
            self._subscriptions[create_subject] = await nc.subscribe(
                subject=create_subject,
                queue=self._nats_pool,
                cb=self.create_pod_callback
            )
            self.logger.info(f"Subscribed to /create subject: {create_subject}")

            self._subscriptions[shutdown_subject] = await nc.subscribe(
                subject=shutdown_subject,
                cb=self.shutdown_callback,
            )
            self.logger.info(f"Subscribed to /shutdown subject: {shutdown_subject}")

            self.logger.info(f"Waiting for NATS payloads...")
            while self._running:
                await asyncio.sleep(time_interval)
                if self.leader:
                    await self.maybe_refresh_build_config()
                    await self.maybe_publish_resources()

        print ("Exiting.")
        await self.close_connections_callback()

    async def shutdown_callback(self, msg: nats.aio.msg.Msg):
        self.logger.warning(
            "Received shutdown request through NATS. This is usually meant to trigger an update. "
            f"Subject: {msg.subject}"
        )
        self._running = False

    def maybe_stop(self):
        for _ in self.required_updates('stop', 3):
            if self.leader:
                self.logger.info(f"""Periodic report of {self.__class__.__name__}.
                    NATS Server: {self.censored_nats_server}
                    Pool:       {self._nats_pool}
                    Active subscriptions: 
                        Total:  {len(self._subscriptions):>10d}
                        Status: {len([k for k, _ in self._subscriptions.items() if 'status' in k]):>10d}
                        Delete: {len([k for k, _ in self._subscriptions.items() if 'delete' in k]):>10d}
                """)

            if self._interactive_mode:
                if self.leader:
                    print("Press Ctrl+C again to exit. Or Ctrl+\\ to kill.")
                break
        else:
            # This is only executed if break is not executed: either two subsequent Ctrl+C or interactive_mode=false.
            self._running = False


    @asynccontextmanager
    async def nats_connection(self):
        """
        Simple context manager to define standard error management with singleton pattern
        """
        if self._nats_connection is None:
            self.logger.info(f"Connecting to {self.censored_nats_server}")
            self._nats_connection = await nats.connect(servers=self._nats_server)

            try:
                yield self._nats_connection
            finally:
                await self._nats_connection.drain()
                self._nats_connection = None
                self.logger.info(f"Disconnected from {self.censored_nats_server}")
        else:
            yield self._nats_connection

    async def create_pod_callback(self, msg: nats.aio.msg.Msg):
        """Wrapper decompressing and parsing the nats body"""
        job_name = msg.subject.split(".")[-1]
        body = orjson.loads(zlib.decompress(msg.data))
        job_sh = body['job_sh']
        pod = interlink.PodRequest(**body.get('pod', dict()))

        await self._register_status_and_delete_callbacks(job_name)

        if cfg.DEBUG:
            with open(f"/tmp/{job_name}", "w") as f:
                podspec = pformat(body.get('pod', dict()))
                podspec = "\n".join([f"# {line}" for line in podspec.split('\n')])
                print("\n".join((podspec, job_sh)), file=f)

        try:
            self.logger.info(f"Submitting job {job_name}")
            job_id_in_backend = await self.create_pod(job_name, job_sh, pod)
        except HTTPException as e:
            self.logger.critical(f"Failed creating job {job_name} \n{pformat(body)}")
            self.logger.critical(f"Returning error code: {e.status_code} ({e.detail})")
            await msg.respond(
                NatsResponse(status_code=e.status_code, data=e.detail.encode('utf-8')).to_nats()
            )
        else:
            await msg.respond(
                NatsResponse(status_code=200, data=job_id_in_backend.encode('ascii')).to_nats()
            )
        self.logger.info(f"Job submission procedure terminated")

    async def create_pod(self, job_name: str, job_sh: str, pod: interlink.PodRequest) -> str:
        """Override me!"""
        raise NotImplementedError

    async def delete_pod_callback(self, msg: nats.aio.msg.Msg):
        """Wrapper decompressing and parsing the nats body"""
        job_name = msg.subject.split(".")[-1]
        try:
            await self.delete_pod(job_name)
        except HTTPException as e:
            self.logger.critical(f"Failed deleting job {job_name}")
            self.logger.critical(f"Returning error code: {e.status_code} ({e.detail})")
            await msg.respond(
                NatsResponse(status_code=e.status_code, data=e.detail.encode('utf-8')).to_nats()
            )
        else:
            self.logger.info(f"Deleted job {job_name}")
            await msg.respond(
                NatsResponse(status_code=200).to_nats()
            )

            # Unsubscribe from all the subscriptions and delete entries from subscription hash table
            subscriptions_to_drop = {
                subject: subscription
                for subject, subscription in self._subscriptions.items()
                if subject.split(".")[-1] == job_name
            }
            for subject, subscription in subscriptions_to_drop.items():
                self.logger.info(f"Unsubscribe from subject {subject} and delete from subscriptions table")
                await subscription.unsubscribe()
                del self._subscriptions[subject]

    async def delete_pod(self, job_name: str) -> None:
        """Override me!"""
        raise NotImplementedError

    async def get_pod_status_and_logs_callback(self, msg: nats.aio.msg.Msg):
        """Wrapper decompressing and parsing the nats body"""
        job_name = msg.subject.split(".")[-1]
        try:
            job_status: JobStatus = await self.get_pod_status_and_logs(job_name)
        except HTTPException as e:
            self.logger.critical(f"Failed retrieving status and log of job {job_name}")
            self.logger.critical(f"Returning error code: {e.status_code} ({e.detail})")
            await msg.respond(
                NatsResponse(status_code=e.status_code, data=e.detail.encode('utf-8')).to_nats()
            )
        else:
            if job_status is not None:
                self.logger.info(f"Retrieved status of {job_name}: {job_status.phase}")
                await msg.respond(
                    NatsResponse(status_code=200, data=job_status.model_dump()).to_nats()
                )
            else:
                self.logger.error(f"Failed to retrieve status of {job_name}: job status not found.")
                NatsResponse(status_code=500, data=b"Job not found").to_nats()

    async def get_pod_status_and_logs(self, job_name: str) -> JobStatus:
        """Override me!"""
        raise NotImplementedError

    @property
    def subscribed_jobs(self) -> List[str]:
        """
        Return a list of job names for which a status subscription is found.
        """
        status_prefix = '.'.join((self._nats_subject, 'status'))
        return [sbj.split(':')[-1] for sbj in self._subscriptions.keys() if sbj.startswith(status_prefix)]


    async def maybe_publish_resources(self):
        for _ in self.required_updates('resources', 30):
            rsrc = self._build_config.resources or Resources()

            rsrc.cpu = rsrc.cpu or self._declared_resources.cpu or await self.get_allocatable_cpu()
            if rsrc.cpu is None:
                rsrc.cpu = cfg.DEFAULT_ALLOCATABLE_CPU
                if 'cpu' not in self._warned_on_unset_resources:
                    self._warned_on_unset_resources.append('cpu')
                    self.logger.warning(
                            f"{self.__class__.__name__} does not implement `get_allocatable_cpu`. " +
                            f"Specify allocatable cpu with --cpu argument. Using default: {rsrc.cpu}.",
                        )
            else:
                rsrc.cpu = str(rsrc.cpu)

            rsrc.memory = rsrc.memory or self._declared_resources.memory or await self.get_allocatable_memory()
            if rsrc.memory is None:
                rsrc.memory = cfg.DEFAULT_ALLOCATABLE_MEMORY
                if 'memory' not in self._warned_on_unset_resources:
                    self._warned_on_unset_resources.append('memory')
                    self.logger.warning(
                        f"{self.__class__.__name__} does not implement `get_allocatable_memory`. "
                        f"Specify allocatable memory with --memory argument. Using default: {rsrc.memory}."
                    )
            else:
                rsrc.memory = str(rsrc.memory)

            rsrc.pods = rsrc.pods or self._declared_resources.pods or await self.get_allocatable_pods()
            if rsrc.pods is None:
                rsrc.pods = cfg.DEFAULT_ALLOCATABLE_PODS
                if 'pods' not in self._warned_on_unset_resources:
                    self._warned_on_unset_resources.append('pods')
                    self.logger.warning(
                        f"{self.__class__.__name__} does not implement `get_allocatable_pods`. "
                        f"Specify allocatable number of pods with --pods argument. Using default: {rsrc.pods}."
                    )
            else:
                rsrc.pods = int(rsrc.pods)

            rsrc.gpus = rsrc.gpus or self._declared_resources.gpus or await self.get_allocatable_gpus()
            if rsrc.gpus is None:
                rsrc.gpus = cfg.DEFAULT_ALLOCATABLE_GPUS
            else:
                rsrc.gpus = int(rsrc.gpus)

            payload = dict(
                quotas=rsrc.to_kubernetes(),
                labels=self.build_config.node.labels,
                taints=self.build_config.node.taints,
            )

            resources_subject = '.'.join((self._nats_subject, 'resources', self._nats_pool))
            async with self.nats_connection() as nc:
                await nc.publish(
                    subject=resources_subject,
                    payload=orjson.dumps(payload)
                )
                self.logger.info(f"Published allocatable resources on subject {resources_subject}")


    async def get_allocatable_cpu(self) -> Union[int, str, None]:
        return None

    async def get_allocatable_memory(self) -> Union[int, str, None]:
        return None

    async def get_allocatable_pods(self) -> Union[int, None]:
        return None

    async def get_allocatable_gpus(self) -> Union[int, None]:
        return None

    async def close_connections_callback(self):
        pass




