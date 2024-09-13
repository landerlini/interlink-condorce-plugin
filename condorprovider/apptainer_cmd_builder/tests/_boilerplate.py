import os
import subprocess
from contextlib import contextmanager

from condorprovider.utils import generate_uid

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


