import logging
from symbol import subscript

import zlib
from pprint import pformat
from contextlib import asynccontextmanager

from fastapi import HTTPException
import nats.aio.msg
import interlink
import orjson
import nats

from .utils import NatsResponse, JobStatus


class BaseNatsProvider:
    """
    Base class implementing the logic to respond to NATS request by a submitter
    """
    def __init__(self, nats_server: str, nats_queue: str):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.logger.info(f"Starting {self.__class__.__name__}")

        self._nats_server = nats_server

        self._nats_subject = ".".join(('interlink', nats_queue))
        self._nats_queue = nats_queue
        self._nats_connection = None

        self._subscriptions = {}

    async def main_loop(self):
        """Main loop of the NATS responder"""
        create_subject = '.'.join((self._nats_subject, 'create', self._nats_queue, '*'))
        with self.nats_connection() as nc:
            self._subscriptions[create_subject] = await nc.subscribe(
                subject=create_subject,
                queue=self._nats_queue,
                cb=self.create_pod_callback
            )


    @asynccontextmanager
    async def nats_connection(self):
        """
        Simple context manager to define standard error management with singleton pattern
        """
        if self._nats_connection is None:
            self._nats_connection = await nats.connect(servers=self._nats_server)

            try:
                yield self._nats_connection
            finally:
                await self._nats_connection.drain()
                self._nats_connection = None
        else:
            yield self._nats_connection

    async def create_pod_callback(self, msg: nats.aio.msg.Msg):
        """Wrapper decompressing and parsing the nats body"""
        job_name = msg.subject.split(".")[-1]
        body = orjson.loads(zlib.decompress(msg.data))
        job_sh = body['job_sh']
        pod = interlink.PodRequest(**body.get('pod', dict()))

        with self.nats_connection() as nc:
            # Register status and logs callback
            status_subject = '.'.join((self._nats_subject, 'status', job_name))
            self._subscriptions[status_subject] = await nc.subscribe(
                subject=status_subject,
                cb=self.get_pod_status_and_logs_callback
            )
            # Register delete pod callback
            delete_subject = '.'.join((self._nats_subject, 'delete', job_name)),
            self._subscriptions[delete_subject] = await nc.subscribe(
                subject=delete_subject,
                cb=self.delete_pod_callback
            )

        try:
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
                subscription.unsubscribe()
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
            await msg.respond(
                NatsResponse(status_code=200, data=job_status.model_dump()).to_nats()
            )

    async def get_pod_status_and_logs(self, job_name: str) -> JobStatus:
        """Override me!"""
        raise NotImplementedError

