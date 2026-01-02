from pydantic import BaseModel, Field
from typing import Optional

class NetworkConfig(BaseModel, extra='forbid'):
    initialization: str = Field(
        default="",
        description="Preparatory statements executed early in the job, "
            "usually defining bash functions",
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
        """Preparatory statements"""
        if self.enabled:
            return self.initialization

        return ""

    def connect(self) -> str:
        """Minimal connection string"""
        if self.enabled:
            return "\n".join([
                self.connection + " &",
                "WSTUNNEL_PID=$!"
                ])
        return ""

    def proxy(self) -> Optional[str]:
        """Proxy prefix to be executed before apptainer"""
        if self.enabled:
            return self.proxy_cmd

        return None

    def finalize(self) -> str:
        """Finalizer"""
        if self.enabled:
            return "\n".join([
                self.finalization,
                "kill $WSTUNNEL_PID",
                ])

        return ""
