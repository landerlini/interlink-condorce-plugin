from pydantic import BaseModel, Field
from typing import List, Literal, Optional

class BindVolume(BaseModel, extra="forbid"):
    target: str
    source: str
    type: Literal["bind"] = Field(default="bind")
    extended_mode: List[str] = Field(default=["rslave", "ro"])
    # relabel: str = Field(default="Z")


class TmpFS(BaseModel, extra="forbid"):
    target: str
    source: str = Field (default="tmpfs")
    type: Literal["tmpfs"] = Field(default="tmpfs")
    chown: bool = Field(default=True)
    size: str = Field(default="1Gi")

