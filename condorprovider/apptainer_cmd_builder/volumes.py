import os.path

from interlink import Volume
from pydantic import BaseModel, Field
from typing import Dict, Optional, Literal
import textwrap

from ..utils import embed_ascii_file, embed_binary_file, make_uid_numeric, sanitize_uid
from . import configuration as cfg

from condorprovider.utils import generate_uid

MountType = Literal['bind', 'scratch', 'fuse']

class AsciiFileSpec(BaseModel, extra="forbid"):
    content: str = Field(description="Content of the file")
    executable: bool = Field(default=False, description="Marks the file as executable (chmod +x)")

class BinaryFileSpec(BaseModel, extra="forbid"):
    content: bytes = Field(description="Content of the file")
    executable: bool = Field(default=False, description="Marks the file as executable (chmod +x)")



class BaseVolume (BaseModel, extra="forbid"):
    uid: str = Field(
        default_factory=generate_uid,
        description="Unique identifier for the volume",
    )

    host_path: str = Field(
        default_factory=lambda: os.path.join(cfg.SCRATCH_AREA, f".acb.volume.{generate_uid()}"),
        description="Path where the volume is mounted in the host (if any)"
    )

    init_script: Optional[str] = Field(
        default=None,
        description="A script to run outside of the container to populate or prepare the container",
    )

    cleanup_script: Optional[str] = Field(
        default=None,
        description="A script to cleanup the runtime environment at the end of the job (or to upload volume data)"
    )

    def _parse_script(self, script: Optional[str] = None):
        if script is None:
            return None

        return textwrap.dedent(script) % dict(
            host_path=self.host_path,
            uid=self.uid,
        )

    def __hash__(self):
        return make_uid_numeric(self.uid)

    @property
    def parsed_init_script(self):
        return self._parse_script(self.init_script)

    @property
    def parsed_cleanup_script(self):
        return self._parse_script(self.cleanup_script)

    def initialize(self) -> str:
        if self.parsed_init_script:
            return '\n' + textwrap.dedent(self.parsed_init_script)

        return ""

    def finalize(self) -> str:
        if self.parsed_cleanup_script:
            return '\n' + textwrap.dedent(self.parsed_cleanup_script)

        return ""

    def mount(self, mount_path: str, sub_path: Optional[str] = None, read_only: bool = False):
        return [
            VolumeBind(
                volume=self,
                container_path=mount_path,
                read_only=read_only,
                sub_path=sub_path,
            )
        ]


class ScratchArea (BaseVolume, extra="forbid"):
    def mount(self, mount_path: str, sub_path: Optional[str] = None, read_only: bool = False):
        return [
            VolumeBind(
                volume=self,
                container_path=mount_path,
                read_only=read_only,
                mount_type='scratch',
                sub_path=sub_path,
            )
        ]

class StaticVolume(BaseVolume, extra="forbid"):
    config: Dict[str, AsciiFileSpec] = Field(
        default={},
        description="key:spec mapping of a configuration file to be used by the init_script or apptainer CLI",
    )

    binaries: Dict[str, BinaryFileSpec] = Field(
        default={},
        description="key:spec mapping of small binary files to make available from within the container (e.g. secrets)"
    )

    def initialize(self):
        base_path = os.path.abspath(self.host_path)
        ret = [
            f"rm -rf {base_path}",
            f"mkdir -p {base_path}",
            self.parsed_init_script or '',
        ]

        for key, config in self.config.items():
            host_filename = os.path.join(base_path, f"{key}.ascii")
            ret.append(embed_ascii_file(host_filename, config.content, config.executable))

        for key, binary in self.binaries.items():
            host_filename = os.path.join(base_path, f"{key}.binary")
            ret.append(embed_binary_file(host_filename, binary.content, binary.executable))

        return '\n'+'\n'.join(ret)


    def finalize(self):
        base_path = os.path.abspath(self.host_path)
        ret = [
            f"rm -rf {base_path}",
            self.parsed_cleanup_script or '',
        ]
        return '\n'+'\n'.join(ret)

    def mount(self, mount_path: str, sub_path: Optional[str] = None, read_only: bool = True, key: Optional[str] = None):
        if sub_path is not None:
            print("Warning sub_path for StaticVolumes is not implemented. Ignored.")

        if key is None:
            return (
                [   # Bind ascii files
                    VolumeBind(
                        volume=self,
                        host_path_override=f"{os.path.join(self.host_path, k)}.ascii",
                        container_path=f"{mount_path}/{k}",
                        read_only=read_only,
                    ) for k in self.config.keys()
                ] +
                [   # Bind binary files
                    VolumeBind(
                        volume=self,
                        host_path_override=f"{os.path.join(self.host_path, k)}.binary",
                        container_path=f"{mount_path}/{k}",
                        read_only=read_only,
                    ) for k in self.binaries.keys()
                ]
            )

        if key in self.config.keys():
            return [
                VolumeBind(
                    volume=self,
                    host_path_override=f"{os.path.join(self.host_path, key)}.ascii",
                    container_path=mount_path,
                    read_only=read_only,
                )
            ]

        if key in self.binaries.keys():
            return [
                VolumeBind(
                    volume=self,
                    host_path_override=f"{os.path.join(self.host_path, key)}.binary",
                    container_path=mount_path,
                    read_only=read_only,
                )
            ]

        raise KeyError(f"Key {key} not found in any collection (config, binaries)")


class FuseVolume(BaseVolume, extra="forbid"):
    """
    Define a volume using fuse, relying on a script passed as argument.

    The mount command implemented in the script should satisfy the following conditions:
     - it must entirely rely on software available *within* the container
     - it must not refer directly to the mount_path, but as the first argument (or "$1")
     - it must be blocking

    :param fuse_mount_script: The shell script implementing the fuse mount (e.g. sshfs -f or rclone mount2)
    :return: a BaseVolume instance describing the fuse volume

    """
    fuse_mount_script: str = Field(
        description="Script to configure the fuse volume with software distributed within the container"
    )

    @property
    def fuse_mount_script_host_path(self):
        base_path = os.path.abspath(self.host_path)
        return os.path.join(base_path, f"mount")

    @property
    def fuse_mount_script_container_path(self):
        return f"/mnt/apptainer_cmd_builder/fusemount-scripts/{self.uid}"

    def initialize(self):
        mount_script = textwrap.dedent(self.fuse_mount_script)

        envvars = [
            "export SUB_PATH=$1",
            "export MOUNT_POINT=$2",
        ]

        mount_script = "\n".join([line for line in mount_script.split('\n') if len(line)])
        if not mount_script.split('\n')[0].startswith("#!/"):
            mount_script = '\n'.join(["#!/bin/sh", *envvars, mount_script])
        else:
            mount_script = '\n'.join([mount_script.split('\n')[0], *envvars, mount_script])

        base_path = os.path.abspath(self.host_path)
        ret = [
            f"rm -rf {base_path}",
            f"mkdir -p {base_path}/cache",
            embed_ascii_file(self.fuse_mount_script_host_path, mount_script, executable=True),
            self.parsed_init_script or '',
        ]

        # If possible, will execute the fuse command on host, instead of inside the container
        if cfg.FUSE_ENABLED_ON_HOST:
            ret += [
                f"CACHEDIR={base_path}/cache " + self.fuse_mount_script_host_path + " \"\" " + self.host_path + " &",
                f"FUSE_{sanitize_uid(self.uid).upper()}_PID=$!"
            ]

        return '\n' + '\n'.join(ret)

    def finalize(self):
        base_path = os.path.abspath(self.host_path)
        ret = [f"rm -rf {base_path}"]

        if self.parsed_cleanup_script is not None:
            ret.append(self.parsed_cleanup_script)

        if cfg.FUSE_ENABLED_ON_HOST:
            ret += [  f"fusermount -u {self.host_path} || kill $FUSE_{sanitize_uid(self.uid).upper()}_PID" ]

        return '\n' + '\n'.join(ret)

    def mount(self, mount_path: str, sub_path: Optional[str] = None, read_only: bool = False):
        if read_only:
            raise NotImplementedError("Read-only constrain is fuse-lib specific. Implement it in mount script.")

        if cfg.FUSE_ENABLED_ON_HOST:
            return [
                VolumeBind(
                    volume=self,
                    container_path=mount_path,
                    mount_type='bind',
                    sub_path=sub_path,
                )
            ]

        return [
            VolumeBind(
                volume=self,
                host_path_override=self.fuse_mount_script_host_path,
                container_path=self.fuse_mount_script_container_path,
                read_only=False,
                mount_type='bind',
            ),
            VolumeBind(
                volume=self,
                container_path=mount_path,
                mount_type='fuse',
                sub_path=sub_path,
            )
        ]


def make_empty_dir():
    return BaseVolume(
        init_script=textwrap.dedent(f"""
        rm -rf %(host_path)s
        mkdir -p %(host_path)s
        """),
        cleanup_script=textwrap.dedent(f"""
        rm -rf %(host_path)s
        """)
    )

def clone_git_repo(git_repo: str, apptainer_exec: str = "apptainer"):
    """
    An example of customization: clone a git repository in a directory.

    :param git_repo: https path to a git repo
    :param apptainer_exec:
    :return:
    """
    return BaseVolume(
        init_script=textwrap.dedent(f"""
        rm -rf %(host_path)s
        mkdir -p %(host_path)s
        {apptainer_exec} run --bind %(host_path)s:/mnt/repo docker://alpine/git:latest clone {git_repo} /mnt/repo
        """),
        cleanup_script=textwrap.dedent(f"""
        rm -rf %(host_path)s
        """)
    )


class VolumeBind(BaseModel, extra="forbid"):
    volume: BaseVolume = Field(description="The volume this bind refers to")
    container_path: str = Field(description="Path of the volume (or file) in the container file system")
    read_only: bool = Field(default=False)
    host_path_override: Optional[str] = Field(
        default=None,
        description=f"Specialize (or overrides) the host path with respect to the volume"
    )
    mount_type: MountType = Field(
        default='bind',
        description="Type of binding"
    )
    sub_path: Optional[str] = Field(
        default=None,
        description="Mount in mount_path a subPath of the volume instead of its root",
    )

    @property
    def host_path(self):
        ret_path = self.host_path_override if self.host_path_override is not None else self.volume.host_path
        return os.path.join(ret_path, self.sub_path) if self.sub_path is not None else ret_path

    def __str__(self):
        if self.mount_type in ['bind']:
            return f"--bind {self.host_path}:{self.container_path}:{'ro' if self.read_only else 'rw'}"
        elif self.mount_type in ['scratch']:
            return f"--scratch {self.container_path}"
        elif self.mount_type in ['fuse']:
            return f"--fusemount \"container:%(script)s %(sub_path)s %(mount_point)s %(fake_arg)s\"" % dict(
                sub_path=self.sub_path if self.sub_path is not None else "",
                script=self.volume.fuse_mount_script_container_path,
                mount_point=self.container_path,
                # fake_arg adds a fake last argument in case the host allows using fuse inside the container directly
                # without /dev/fd tricks. If the argument is not passed, then the last argument is intercepted by
                # apptainer and replaced with the /dev/fd device.
                fake_arg=self.container_path if cfg.FUSE_ENABLED_ON_HOST else '',
            )

        raise KeyError(f"Unexpected mount_type '{self.mount_type}', expect 'bind', 'scratch', or 'fuse'.")

    def __hash__(self):
        return self.__str__().__hash__()



