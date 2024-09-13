from pydantic import BaseModel, Field
from typing import List

from condorprovider.apptainer_cmd_builder import ApptainerCmdBuilder


class AppCompose (BaseModel, extra='forbid'):
    init_containers: List[ApptainerCmdBuilder]
    containers: List[ApptainerCmdBuilder]
