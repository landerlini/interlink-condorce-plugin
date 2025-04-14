from pydantic import BaseModel
from typing import Any, Dict, List
from ..utils import to_snakecase

from .BuildConfig import BuildConfig

KNOWN_FIXES = dict(
    SingularityHubProxy="shub_proxy",
    ApptainerOptions="apptainer",
    VolumeOptions="volumes",
)

class BuildRestModel(BaseModel):
    pod: Dict[str, Any]
    container: List[Any]
    jobConfig: Dict[str, Any]

    def get_build_config(self):
        def _fix_keys(d: Dict[str, Any]):
            if d is None:
                d = self.jobConfig

            ret = dict()
            for key, value in d.items():
                key = KNOWN_FIXES.get(key, key)
                ret[to_snakecase(key)] = _fix_keys(value) if isinstance(value, dict) else value

            return ret

        return BuildConfig(**_fix_keys(self.jobConfig))



