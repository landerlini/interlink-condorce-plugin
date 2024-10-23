import logging
import interlink
from typing import Collection, Union

class BaseNatsProvider:
    """
    Base class implementing the logic to respond to NATS request by a submitter
    """
    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.logger.info(f"Starting {self.__class__.__name__}")

    async def create_job(self, pod: interlink.PodRequest, volumes: Collection[interlink.Volume]) -> str:
        raise NotImplementedError

    async def delete_pod(self, pod: interlink.PodRequest) -> None:
        raise NotImplementedError

    async def get_pod_status(self, pod: interlink.PodRequest) -> Union[interlink.PodStatus, None]:
        raise NotImplementedError

    async def get_pod_logs(self, log_request: interlink.LogRequest) -> str:
        raise NotImplementedError

