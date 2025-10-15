import os.path

from pydantic import BaseModel, Field
from typing import Dict, Optional, Literal, List
import textwrap

from ..utils import embed_ascii_file, embed_binary_file, make_uid_numeric, sanitize_uid
from . import configuration as cfg, BuildConfig

from natsprovider.utils import generate_uid

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

    scratch_area: str = Field(
        default=cfg.SCRATCH_AREA,
        description="Path where to store temporary data with the volume contents",
    )

    init_script: Optional[str] = Field(
        default=None,
        description="A script to run outside of the container to populate or prepare the container",
    )

    cleanup_script: Optional[str] = Field(
        default=None,
        description="A script to cleanup the runtime environment at the end of the job (or to upload volume data)"
    )

    fuse_mode: Literal['host', 'container', 'host-privileged'] = Field(
        default=cfg.FUSE_MODE,
        description="Indicate how fuse is mounted for the user. Refer to BuildConfig for the docs."
    )

    fuse_enabled_on_host: bool = Field(
        default=False,
        description="Indicate the executor is privileged enough to mount fuse volumes without userns tricks"
    )

    host_path_override: Optional[str] = Field(
        default=None,
        description="If set, overrides generation of a temporary volume for hosting volume data"
    )

    additional_directories_in_path: List[str] = Field(
        default=[],
        description="Used to configure site-specific software. Used by fuse in host mode, only.",
    )

    cachedir: str = Field(
        default="/tmp/cache/apptainer",
        description="Directory where to cache remote data. Used by fuse volumes, only.",
    )


    @property
    def host_path(self):
        """Path where the volume is mounted in the host (if any)"""
        if self.host_path_override is not None:
            return self.host_path_override
        return os.path.join(self.scratch_area, f".acb.volume.{self.uid}")

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
        base_path = self.host_path
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
        base_path = self.host_path
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
        base_path = self.host_path
        return os.path.join(base_path, f"mount")

    @property
    def fuse_mount_script_container_path(self):
        return f"/mnt/apptainer_cmd_builder/fusemount-scripts/{self.uid}"

    def initialize(self):
        mount_script = textwrap.dedent(self.fuse_mount_script)
        base_path = self.host_path
        host_path = os.path.join(self.host_path, "mnt")
        cache_path = self.cachedir

        envvars = [
            "export SUB_PATH=$1",
            "export MOUNT_POINT=$2",
        ]

        # executed by apptainer, but on the host filesystem: requires re-setting PATH
        if self.fuse_mode in ('host',):
            envvars += [
                f"export PATH={':'.join(self.additional_directories_in_path)}:$PATH",
                f"export CACHEDIR={cache_path}",
                ]

        mount_script = "\n".join([line for line in mount_script.split('\n') if len(line)])
        if not mount_script.split('\n')[0].startswith("#!/"):
            mount_script = '\n'.join(["#!/bin/sh", *envvars, mount_script])
        else:
            mount_script = '\n'.join([mount_script.split('\n')[0], *envvars, mount_script])

        ret = [
            f"rm -rf {base_path}",
            f"mkdir -p {host_path}",
            f"mkdir -p {cache_path}",
            embed_ascii_file(self.fuse_mount_script_host_path, mount_script, executable=True),
            self.parsed_init_script or '',
        ]

        # If possible, will execute the fuse command on host, instead of inside the container
        if self.fuse_enabled_on_host:
            ret += [
                f"ls /shared/home/anderlin/bin",
                f"which juicefs",
                f"/bin/bash -c env | grep PATH",
                f"CACHEDIR={cache_path}/cache /bin/bash " + self.fuse_mount_script_host_path + " \"\" " + host_path + " &",
                f"FUSE_{sanitize_uid(self.uid).upper()}_PID=$!",
                textwrap.dedent(
                    f"""
                    for attempt in $(seq 50); do
                        if [ $(stat -c %d {base_path}) -ne $(stat -c %d {host_path} 2> /dev/null) ]; then
                            echo "Fuse mount of volume {self.uid} complete"
                            break;
                        else
                            sleep 0.1
                        fi
                    done
                    """
                )
            ]

        return '\n' + '\n'.join(ret)

    def finalize(self):
        host_path = os.path.join(self.host_path, "mnt")

        ret = []

        if self.fuse_enabled_on_host:
            ret += [
                f"fusermount -u {host_path} "
                f" || ( sleep 5 && fusermount -u {host_path} ) "
                f" || ( sleep 5 && fusermount -u {host_path} ) "
                f" || kill $FUSE_{sanitize_uid(self.uid).upper()}_PID"
            ]

        # ret += [f"rm -rf {base_path}"]

        if self.parsed_cleanup_script is not None:
            ret.append(self.parsed_cleanup_script)


        return '\n' + '\n'.join(ret)

    def mount(self, mount_path: str, sub_path: Optional[str] = None, read_only: bool = False):
        if read_only:
            raise NotImplementedError("Read-only constrain is fuse-lib specific. Implement it in mount script.")

        if self.fuse_enabled_on_host:
            return [
                VolumeBind(
                    volume=self,
                    host_path_override=os.path.join(self.host_path, "mnt"),
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


def make_empty_dir(build_config: BuildConfig):
    return BaseVolume(
        init_script=textwrap.dedent(f"""
        rm -rf %(host_path)s
        mkdir -p %(host_path)s
        """),
        cleanup_script=textwrap.dedent(f"""
        rm -rf %(host_path)s
        """),
        **build_config.base_volume_config()
    )

def clone_git_repo(git_repo: str, build_config: BuildConfig, apptainer_exec: str = "apptainer"):
    """
    An example of customization: clone a git repository in a directory.

    :param git_repo: https path to a git repo
    :param apptainer_exec:
    :param build_config:
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
        """),
        **build_config.base_volume_config()
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
            return f"--fusemount \"%(fuse_mode)s:%(script)s %(sub_path)s %(mount_point)s %(fake_arg)s\"" % dict(
                fuse_mode='container' if self.volume.fuse_mode in ('container', 'container-privileged') else 'host',
                sub_path=self.sub_path if self.sub_path is not None else "",
                script=(
                    self.volume.fuse_mount_script_container_path
                    if self.volume.fuse_mode in ('container', 'container-privileged') else
                    self.volume.fuse_mount_script_host_path
                ),
                mount_point=self.container_path,
                # fake_arg adds a fake last argument in case the host allows using fuse inside the container directly
                # without /dev/fd tricks. If the argument is not passed, then the last argument is intercepted by
                # apptainer and replaced with the /dev/fd device.
                fake_arg=self.container_path if self.volume.fuse_enabled_on_host else '',
            )

        raise KeyError(f"Unexpected mount_type '{self.mount_type}', expect 'bind', 'scratch', or 'fuse'.")

    def __hash__(self):
        return self.__str__().__hash__()



