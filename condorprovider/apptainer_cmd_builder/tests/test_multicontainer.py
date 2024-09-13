import textwrap

from sepolgen.interfaces import type_rule_extract_params

from condorprovider.apptainer_cmd_builder.volumes import ScratchArea
from condorprovider.utils import generate_uid
from condorprovider.apptainer_cmd_builder import ApptainerCmdBuilder, ContainerSpec, volumes
from condorprovider.apptainer_cmd_builder.tests._boilerplate import container_output


def test_init_container():
    """
    Writes a token in one container and read it from exec container.
    :return:
    """
    token = generate_uid()
    scratch_area = volumes.make_empty_dir()

    builder = ApptainerCmdBuilder(
        init_containers=[
            ContainerSpec(
                entrypoint=f"echo {token} > /mnt/shared/token",
                image='ubuntu:latest',
                volume_binds=scratch_area.mount('/mnt/shared'),
            ),
        ],
        containers=[
            ContainerSpec(
                entrypoint='cat /mnt/shared/token',
                image='ubuntu:latest',
                volume_binds=scratch_area.mount('/mnt/shared'),
            ),
        ],
    )

    with container_output(builder, test_name='scratch_area') as output:
        assert token in output


def test_init_containers():
    """
    Writes a token in one container and read it from a second init container.
    :return:
    """
    token1 = generate_uid()
    token2 = generate_uid()
    scratch_area = volumes.make_empty_dir()

    builder = ApptainerCmdBuilder(
        init_containers=[
            ContainerSpec(
                entrypoint=f"echo -n {token1} > /mnt/shared/token",
                image='ubuntu:latest',
                volume_binds=scratch_area.mount('/mnt/shared'),
            ),
            ContainerSpec(
                entrypoint=f"echo -n {token2} >> /mnt/shared/token",
                image='ubuntu:latest',
                volume_binds=scratch_area.mount('/mnt/shared'),
            ),
        ],
        containers=[
            ContainerSpec(
                entrypoint='cat /mnt/shared/token',
                image='ubuntu:latest',
                volume_binds=scratch_area.mount('/mnt/shared'),
            ),
        ],
    )

    with container_output(builder, test_name='scratch_area') as output:
        assert token1 + token2 in output


def test_loopback_network():
    """
    Test network communication between two containers of the same application.

    Creates two containers, a http server showing a local directory with randomly named subdir
    and a client downloading and displaying that page.
    The test is successful if the random name of the directory is found in the output
    """
    token = generate_uid()
    scratch_area = volumes.ScratchArea()

    server = textwrap.dedent(f"""
        #!/bin/bash
        cd /mnt/
        timeout 2s python3 -m http.server 7890
        """)

    client = textwrap.dedent(f"""
        #!/bin/bash
        sleep 1
        wget http://127.0.0.1:7890 -O output.html
        cat output.html
        """)


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

    with container_output(builder, test_name='scratch_area') as output:
        assert token in output
