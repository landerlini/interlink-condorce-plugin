from natsprovider.utils import generate_uid
from natsprovider.apptainer_cmd_builder import ApptainerCmdBuilder, ContainerSpec, volumes
from natsprovider.apptainer_cmd_builder.tests._boilerplate import container_output

def test_zero():
    scratch_area = volumes.ScratchArea()

    builder = ApptainerCmdBuilder(
        containers=[
            ContainerSpec(
                entrypoint='ls /',
                image='ubuntu:latest',
                volume_binds=scratch_area.mount("/scratch")
            ),
        ],
    )

    print (builder.dump())

def test_exec():
    token = generate_uid()
    builder = ApptainerCmdBuilder(
        containers = [
            ContainerSpec(
                entrypoint=f'echo {token}',
                image = 'ubuntu:latest',
            )
        ]
    )

    with container_output(builder, test_name='exec') as output:
        assert token in output


