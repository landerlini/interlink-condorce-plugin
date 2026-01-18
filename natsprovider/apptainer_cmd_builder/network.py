from typing import Optional
import os

from pydantic import BaseModel, Field


class NetworkConfig(BaseModel, extra='forbid'):
    initialization: str = Field(
        default="",
        description="Preparatory statements executed early in the job, "
            "usually defining bash functions",
    )

    definition_file: str = Field(
        default=os.path.join(os.path.dirname(__file__), "network.sh"),
        description="Path to the network definition script",
    )

    connection: str = Field(
        default="",
        description="Connection string used to initialize the tunnel",
    )

    proxy_cmd: str = Field(
        default="",
        description="Proxy call prependended to singularity to enable "
                    "connectivity with in-cluster resources"
    )

    finalization: str = Field(
        default="",
        description="Finalization string used to stop the tunnel",
    )

    enabled: bool = True

    def initialize(self) -> str:
        """Configuration for the network cluster <-> remote site, independent of the pod"""
        if self.enabled:
            return self.initialization

        return ""

    def connect(self) -> str:
        """Minimal command establishing the connection with parameters of the pod"""
        if self.enabled:
            return "\n".join([
                self.connection + " &",
                "WSTUNNEL_PID=$!"
                ])
        return ""

    def proxy(self) -> Optional[str]:
        """Proxy command prependend to the container runtime execution"""
        if self.enabled:
            return self.proxy_cmd

        return None

    def finalize(self) -> str:
        """Finalization included in the cleanup function invoked by bash trap"""
        if self.enabled:
            return "\n".join([
                self.finalization,
                "kill $WSTUNNEL_PID",
                ])

        return ""
