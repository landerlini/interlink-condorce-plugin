import asyncio
from contextlib import asynccontextmanager
from .BaseNatsProvider import BaseNatsProvider

import nats

class NatsSubmitter:
    def __init__(self, nats_server: str, nats_subject: str, provider: BaseNatsProvider):
        self._nats_server = nats_server
        self._nats_subject = nats_subject
        self._provider = provider

        self._subscriptions = []

    @asynccontextmanager
    async def nats_connection(self):
        """
        Simple context manager to define standard error management
        """
        nc = await nats.connect(servers=self._nats_server)
        try:
            yield nc
        finally:
            await nc.drain()

    async def main_loop(self):
        with self.nats_connection() as nc:
            self._subscriptions.append(
                await nc.subscribe(
                    '.'.join((self._nats_subject, 'create', '*')),
                    self._provider.create_pod_callback
                )
            )


