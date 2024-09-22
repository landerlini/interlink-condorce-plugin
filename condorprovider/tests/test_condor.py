from condorprovider import CondorConfiguration, CondorSubmit
import uuid
import time
import textwrap
import os
from condorprovider.apptainer_cmd_builder import ApptainerCmdBuilder, ContainerSpec, volumes
import asyncio

from condorprovider.CondorConfiguration import JobStatus, HTCondorException
from condorprovider.utils import generate_uid


CondorConfiguration.initialize_htcondor()

async def _condor_submit_and_retrieve(command: str, **submit_kwargs):
    """
    Test function submitting a job to condor, retrieving the output and returning the standard error stream
    """
    my_condor = CondorConfiguration()
    submit = CondorSubmit(**submit_kwargs)
    job_id = await my_condor.submit(command,  submit)

    while True:
        await asyncio.sleep(1)
        status = await my_condor.status(job_id)
        if status in [JobStatus.unexpanded, JobStatus.running, JobStatus.idle]:
            continue
        elif status in [JobStatus.completed]:
            break
        else:
            print (await my_condor.query(job_id))
            raise HTCondorException(f"Unexpected job status: {str(status)}")

    output = await my_condor.retrieve(job_id)
    return (
        output.get("_condor_stdout", ""),
        output.get("_condor_stderr"),
        *[output.get(f, None) for f in submit.transfer_output_files]
    )

def test_ensure_bearer_token():
    assert os.environ['BEARER_TOKEN'] not in [None, '']

def test_condor_connection():
    for job_id, job in asyncio.run(CondorConfiguration().query()).items():
        print (str(job))

def test_condor_submit():
    stdout, stderr, *_ = asyncio.run(_condor_submit_and_retrieve("echo hello world"))
    assert "hello world" in stdout

def test_condor_named_submit():
    """
    Tests the _by_name lookup workflow, submitting a randomly-named job and retrieving its output by name
    """
    my_condor = CondorConfiguration()
    job_name = str(uuid.uuid4())+str(uuid.uuid4())
    submit = CondorSubmit(job_name=job_name)
    job_id = asyncio.run(my_condor.submit("echo hello world", submit))

    while True:
        time.sleep(1)
        status = asyncio.run(CondorConfiguration().status_by_name(job_name))
        if status in [JobStatus.unexpanded, JobStatus.running, JobStatus.idle]:
            continue
        elif status in [JobStatus.completed]:
            break
        else:
            print (asyncio.run(my_condor.query(job_id)))
            raise HTCondorException(f"Unexpected job status: {str(status)}")

    output = asyncio.run(my_condor.retrieve_by_name(job_name)).get("_condor_stdout", "")
    assert "hello world" in output

def test_condor_submit_multiline():
    """
    Submit a multiline command
    """
    stdout, stderr, *_ = asyncio.run(_condor_submit_and_retrieve(textwrap.dedent(f"""
        MYSTRING="hello world"
        echo $MYSTRING
    """)))
    assert "hello world" in stdout

def test_condor_submit_requests():
    """
    Submit a multiline command
    """
    stdout, stderr, *_ = asyncio.run(
        _condor_submit_and_retrieve(
            textwrap.dedent(
                f"""
                    MYSTRING="hello world"
                    echo $MYSTRING
                """),
            request_cpu=2,
            request_memory=8000,
        )
    )
    assert "hello world" in stdout

def test_condor_with_apptainer():
    """
    Test a simple submission with apptainer
    """
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

    stdout, stderr, logs = asyncio.run(
        _condor_submit_and_retrieve(
            builder.dump(),
            job_name='apptainer',
            transfer_output_files=['logs'])
    )
    assert "hello-world" in stdout


def test_condor_logs_retrival():
    """
    Submit a simple job mounting a scratch area in /hello-world and printing ls /.
    The test succeeds if /hello-world is found in stdout.
    """
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

    stdout, stderr, logs = asyncio.run(
        _condor_submit_and_retrieve(
            builder.dump(),
            job_name='log-retrival-'+generate_uid(5),
            transfer_output_files=['logs']
        )
    )
    builder.process_logs(logs)
    assert builder.containers[0].return_code == 0
    assert "hello-world" in builder.containers[0].log


def test_condor_error_propagation():
    """
    Submit a container with a failing command and checks the appropriate log and ret codes are returned.
    """
    scratch_area = volumes.ScratchArea()

    builder = ApptainerCmdBuilder(
        containers=[
            ContainerSpec(
                entrypoint='ls /intentionally/not/existing',
                image='ubuntu:latest',
                volume_binds=scratch_area.mount("/hello-world")
            ),
        ],
    )

    stdout, stderr, logs = asyncio.run(
        _condor_submit_and_retrieve(
            builder.dump(),
            job_name='error-handling-'+generate_uid(5),
            transfer_output_files=['logs']
        )
    )
    builder.process_logs(logs)
    assert builder.containers[0].return_code == 2
    assert "No such file or directory" in builder.containers[0].log

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

    stdout, stderr, *_ = asyncio.run(_condor_submit_and_retrieve(builder.dump(), job_name='loopback'))
    assert token in stdout

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

    stdout, stderr, *_ = asyncio.run(_condor_submit_and_retrieve(builder.dump(), job_name='fuse-vol'))
    assert token in stdout
