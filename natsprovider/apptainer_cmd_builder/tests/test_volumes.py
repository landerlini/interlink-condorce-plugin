from natsprovider.utils import generate_uid
from natsprovider.apptainer_cmd_builder import ApptainerCmdBuilder, ContainerSpec, volumes
from natsprovider.apptainer_cmd_builder.tests._boilerplate import container_output


def test_scratch_area():
    token = generate_uid()
    scratch_area = volumes.ScratchArea()

    builder = ApptainerCmdBuilder(
        containers=[
            ContainerSpec(
                entrypoint='ls /',
                image='ubuntu:latest',
                volume_binds=scratch_area.mount(f"/scratch-{token}")
            ),
        ],
    )

    with container_output(builder, test_name='scratch_area') as output:
        assert token in output


def test_env():
    token = generate_uid()
    builder = ApptainerCmdBuilder(
        containers = [
            ContainerSpec(
                entrypoint="echo $TEST_ENV",
                environment={'TEST_ENV': token},
                image="ubuntu:latest",
            )
        ]
    )

    with container_output(builder, test_name='env') as output:
        assert token in output


def test_empty_dir():
    token = generate_uid()
    empty_dir = volumes.make_empty_dir()

    builder = ApptainerCmdBuilder(
        containers=[
            ContainerSpec(
                entrypoint='ls /',
                image='ubuntu:latest',
                volume_binds=empty_dir.mount(f"/scratch-{token}")
            ),
        ],
    )

    with container_output(builder, test_name='empty_dir') as output:
        assert token in output


def test_git_repo():
    token = generate_uid()
    git_repo = volumes.clone_git_repo('https://github.com/octocat/Hello-World.git')

    builder = ApptainerCmdBuilder(
        containers=[
            ContainerSpec(
                entrypoint=f'cat /git-{token}/README',
                image='ubuntu:latest',
                volume_binds=git_repo.mount(f"/scratch-{token}")
            ),
        ],
    )

    with container_output(builder, test_name='empty_dir') as output:
        assert token in output


def test_ascii_file():
    token = generate_uid()
    configmap = volumes.StaticVolume(config={'token.cfg': volumes.AsciiFileSpec(content=token)})

    builder = ApptainerCmdBuilder(
        containers=[
            ContainerSpec(
                entrypoint=f'cat /my-configuration/token.cfg',
                image='ubuntu:latest',
                volume_binds=configmap.mount(f"/my-configuration")
            ),
        ],
    )

    with container_output(builder, test_name='ascii_file') as output:
        assert token in output


def test_single_ascii_file():
    token = generate_uid()
    configmap = volumes.StaticVolume(config={'token.cfg': volumes.AsciiFileSpec(content=token)})

    builder = ApptainerCmdBuilder(
        containers=[
            ContainerSpec(
                entrypoint=f'cat /my-configuration/the-token',
                image='ubuntu:latest',
                volume_binds=configmap.mount(mount_path=f"/my-configuration/the-token", key='token.cfg')
            ),
        ],
    )

    with container_output(builder, test_name='single_ascii_file') as output:
        assert token in output


def test_binary_file():
    token = generate_uid()
    secret = volumes.StaticVolume(binaries={'token.cfg': volumes.BinaryFileSpec(content=token.encode('ascii'))})


    builder = ApptainerCmdBuilder(
        containers=[
            ContainerSpec(
                entrypoint=f'cat /my-configuration/token.cfg',
                image='ubuntu:latest',
                volume_binds=secret.mount(f"/my-configuration")
            ),
        ],
    )

    with container_output(builder, test_name='binary_file') as output:
        assert token in output


def test_single_binary_file():
    token = generate_uid()
    secret = volumes.StaticVolume(binaries={'token.cfg': volumes.BinaryFileSpec(content=token.encode('ascii'))})

    builder = ApptainerCmdBuilder(
        containers=[
            ContainerSpec(
                entrypoint=f'cat /my-configuration/the-token',
                image='ubuntu:latest',
                volume_binds=secret.mount(mount_path=f"/my-configuration/the-token", key='token.cfg')
            ),
        ],
    )

    with container_output(builder, test_name='single_binary_file') as output:
        assert token in output


def test_fuse_volume():
    """
    Creates a file containing a random token in a random directory.
    Then mount that random directory into another directory using fuse.
    The test is successful if the token can be read via fuse from the second directory.
    """
    token = generate_uid()
    rnd_dir = generate_uid()
    configmap = volumes.StaticVolume(config={'token.cfg': volumes.AsciiFileSpec(content=token)})

    rclone_volume = volumes.FuseVolume(
        fuse_mount_script="""
            #!/bin/sh

            cat << EOS > /tmp/rclone.conf
            [example]
            type = local
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

    with container_output(builder, test_name='fuse_volume') as output:
        assert token in output.lower()
