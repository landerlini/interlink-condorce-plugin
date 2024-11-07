import logging
import zlib
from pprint import pformat

from fastapi import HTTPException
import nats.aio.msg
import interlink
import orjson

from .utils import NatsResponse, JobStatus


class BaseNatsProvider:
    """
    Base class implementing the logic to respond to NATS request by a submitter
    """
    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.logger.info(f"Starting {self.__class__.__name__}")

    async def create_pod_callback(self, msg: nats.aio.msg.Msg):
        """Wrapper decompressing and parsing the nats body"""
        job_name = msg.subject.split(".")[-1]
        body = orjson.loads(zlib.decompress(msg.data))
        job_sh = body['job_sh']
        pod = interlink.PodRequest(**body.get('pod', dict()))
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

