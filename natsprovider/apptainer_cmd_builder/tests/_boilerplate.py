import os
import sys
import subprocess
from contextlib import contextmanager

from typing import List, Dict, Any
from pydantic import BaseModel, Field

from natsprovider.apptainer_cmd_builder import BuildConfig
from natsprovider.utils import generate_uid, to_snakecase

build_config_for_tests = BuildConfig(
    apptainer=BuildConfig.ApptainerOptions(
        fuse_mode="host",
    )
)

@contextmanager
def container_output(builder, test_name: str = "test"):
    filename = f"/tmp/text_{test_name}.{generate_uid()}.sh"
    try:
        script = builder.dump()
        print (script)
        with open(filename, "w") as f:
            f.write(script)

        yield str(subprocess.check_output(["/bin/sh", filename]), 'ascii')
    finally:
        try:
            os.remove(filename)
        except FileNotFoundError:
            pass


class ValidationStruct(BaseModel, extra='forbid'):
    in_log: List[str] = Field(default=[], description="Strings that must be in the log to pass the test")
    not_in_log: List[str] = Field(default=[], description="Strings that make the test fail if found")

    @classmethod
    def from_dict(cls, d: Dict[str, Any]):
        return cls(**{to_snakecase(k): v for k, v in d.items()})

    def raise_on_conditions(self, log: str):
        for required_string in self.in_log:
            if required_string in log:
                print(f"Validation PASSED: {required_string} found in log:", file=sys.stderr)
                print("\n".join([line for line in log.split('\n') if required_string in line]), file=sys.stderr)
            else:
                print(f"Validation FAILED: {required_string} not found in log:", file=sys.stderr)

            assert required_string in log

        for forbidden_string in self.not_in_log:
            if forbidden_string in log:
                print(f"Validation FAILED: {forbidden_string} found in log:", file=sys.stderr)
                print("\n".join([line for line in log.split('\n') if forbidden_string in line]), file=sys.stderr)
            else:
                print (f"Validation PASSED: {forbidden_string} not found in log.", file=sys.stderr)

            assert forbidden_string not in log


