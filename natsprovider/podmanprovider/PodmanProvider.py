from code import interact

import podman

from .. import interlink
from ..utils import  compute_pod_resource, JobStatus
from ..BaseNatsProvider import BaseNatsProvider

class PodmanProvider(BaseNatsProvider):
    def __init__(self, nats_server: str, nats_queue: str, interactive_mode: bool):
        BaseNatsProvider.__init__(
            self,
            nats_server=nats_server,
            nats_queue=nats_queue,
            interactive_mode=interactive_mode
        )

        with podman.PodmanClient(base_url="http+unix://run/podman/podman.sock") as client:
            if not client.ping():
                raise IOError("Cannot contact podman service. Please make sure it is available.")