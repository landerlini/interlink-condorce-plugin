from condorprovider import CondorConfiguration
import textwrap
import os
from condorprovider.apptainer_cmd_builder import ApptainerCmdBuilder, ContainerSpec, volumes
import asyncio

from condorprovider.CondorConfiguration import JobStatus, HTCondorException
from condorprovider.utils import generate_uid


CondorConfiguration.initialize_htcondor()

async def _condor_submit_and_retrieve(command: str):
    """
    Test function submitting a job to condor, retrieving the output and returning the standard error stream
    """
    my_condor = CondorConfiguration()
    job_id = await my_condor.submit(command)

    while True:
        await asyncio.sleep(1)
        status = await CondorConfiguration().status(job_id)
        if status in [JobStatus.unexpanded, JobStatus.running, JobStatus.idle]:
            continue
        elif status in [JobStatus.completed]:
            break
        else:
            print (await my_condor.query(job_id))
            raise HTCondorException(f"Unexpected job status: {str(status)}")

    output = await my_condor.retrieve(job_id)
    return output.get("_condor_stdout", "")

def test_ensure_bearer_token():
    assert os.environ['BEARER_TOKEN'] not in [None, '']

def test_condor_connection():
    for job_id, job in asyncio.run(CondorConfiguration().query()).items():
        print (str(job))

def test_condor_submit():
    output = asyncio.run(_condor_submit_and_retrieve("echo hello world"))
    assert "hello world" in output

def test_condor_submit_multiline():
    output = asyncio.run(_condor_submit_and_retrieve(textwrap.dedent(f"""
        MYSTRING="hello world"
        echo $MYSTRING
    """)))
    assert "hello world" in output

def test_condor_with_apptainer():
    scratch_area = volumes.ScratchArea()

    builder = ApptainerCmdBuilder(
        containers=[
            ContainerSpec(
                entrypoint='ls /',
                image='ubuntu:latest',
                volume_binds=scratch_area.mount("/hello-world")
            ),
        ],
    )

    output = asyncio.run(_condor_submit_and_retrieve(builder.dump()))
    assert "hello-world" in output

def test_condor_loopback_network():
    token = generate_uid()
    scratch_area = volumes.ScratchArea()

    server = textwrap.dedent(
        f"""
        #!/bin/bash
        cd /mnt/
        timeout 4s python3 -m http.server 7890
        """
        )

    client = textwrap.dedent(
        f"""
        #!/bin/bash
        sleep 2
        wget http://127.0.0.1:7890 -O output.html
        cat output.html
        """
        )

    builder = ApptainerCmdBuilder(
        containers=[
            ContainerSpec(
                entrypoint=server,
                image='python:alpine',
                volume_binds=scratch_area.mount(f'/mnt/{token}'),
            ),
            ContainerSpec(
                entrypoint=client,
                image='python:alpine',
            ),
        ],
    )

    output = asyncio.run(_condor_submit_and_retrieve(builder.dump()))
    assert token in output

def test_condor_fuse_volume():
    token = generate_uid()
    rnd_dir = generate_uid()
    configmap = volumes.StaticVolume(config={'token.cfg': volumes.AsciiFileSpec(content=token)})

    rclone_volume = volumes.FuseVolume(
        fuse_mount_script="""
            #!/bin/sh

            cat << EOS > /tmp/rclone.conf
            [example]
            type = local
            nounc = true
            EOS

            rclone mount2 \\
                --config /tmp/rclone.conf \\
                --allow-non-empty \\
                example:/mnt/%(rnd_dir)s $1
            """ % dict(rnd_dir=rnd_dir)
    )

    builder = ApptainerCmdBuilder(
        containers=[
            ContainerSpec(
                entrypoint=f'cat /discovery_path/token.cfg',
                image="rclone/rclone:latest",
                volume_binds=[
                    *configmap.mount(mount_path=f"/mnt/{rnd_dir}"),
                    *rclone_volume.mount(mount_path=f"/discovery_path")
                ]
            ),
        ],
    )

    output = asyncio.run(_condor_submit_and_retrieve(builder.dump()))
    assert token in output
