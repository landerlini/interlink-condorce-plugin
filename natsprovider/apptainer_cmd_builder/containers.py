from datetime import datetime
from textwrap import dedent

from pydantic import BaseModel, Field
import os.path
from typing import Dict, List, Literal, Union, Optional
from pathlib import Path
import shlex

import requests

from . import configuration as cfg
from .configuration import SHUB_PROXY
from ..utils import generate_uid, embed_ascii_file, make_uid_numeric, sanitize_uid
from .volumes import VolumeBind

_GLOBAL_SHUB_PROXY_TOKEN = None

ImageFormat = Literal[  # See https://apptainer.org/docs/user/main/cli/apptainer_exec.html#synopsis
    "docker",
    "none",
    "library",
    "instance",
    "shub",
    "oras",
]

class ContainerSpec(BaseModel, extra="forbid"):
    uid: str = Field(
        default_factory=generate_uid,
        description="Unique identifier of the container, mainly used to retrieve logs and status"
    )

    entrypoint: Union[str, Path, None] = Field(
        default=None,
        description="Entrypoint of the job to be executed within the container"
    )

    args: List[str] = Field(
        default = [],
        description="Arguments to be passed to the command defined in the entrypoint"
    )

    executable: Path = Field(
        default=Path("/usr/bin/apptainer"),
        description="Relative or absolute path to apptainer, singularity or other compatible replacements"
    )

    image: str = Field(
        description="Singularity Image or docker image to run the job"
    )

    default_format: ImageFormat = Field(
        default="docker",
        description="Default image format, if not provided"
    )

    environment: Dict[str, str] = Field(
        default=dict(),
        description="Environment variables {key:value} to be passed to the contained process"
    )

    volume_binds: List[VolumeBind] = Field(
        default=(),
        description="List of volumes to be bound to the container at runtime"
    )

    scratch_area: str = Field(
        default=cfg.SCRATCH_AREA,
        description="Directory to be used for temporary data related to this container set",
    )

    shub_proxy_server: Optional[str] = Field(
        default=cfg.SHUB_PROXY,
        description="SingularityHub proxy server without protocol"
    )

    cvmfs_unpacked_path: Optional[str] = Field(
        default=None,
        description="SingularityHub proxy server without protocol"
    )

    shub_proxy_master_token: Optional[str] = Field(
        default=cfg.SHUB_PROXY_MASTER_TOKEN,
        description="SingularityHub proxy master token used to generate client tokens"
    )

    shub_cache_seconds: Optional[int] = Field(
        default=600,
        description="Time before pre-built docker image cache is invalidated. DEPRECATED and IGNORED.",
    )

    return_code: Optional[int] = Field(
        default=None,
        description="Return code of the container, once completed. None otherwise."
    )

    log: Optional[str] = Field(
        default=None,
        description="Log of the container, once completed. None otherwise."
    )

    readonly_image_dir: str = Field(
        default=cfg.IMAGE_DIR,
        description="""Some frequently used image might be cached locally in this read-only directory.
        Image will be looked up as os.path.join(readonly_image_dir, image.replace(":", "_")).
        """,
    )

    cachedir: str = Field(
        default=cfg.SCRATCH_AREA,
        description="""Fast writable area shared among multiple instances to store built images"""
    )

    ################################################################################
    # Flags
    writable_tmpfs: bool = Field(
        default=True,
        description="Enable an in-memory OverlayFS to mock image file system editing",
        json_schema_extra=dict(arg='--writable-tmpfs'),
    )

    fakeroot: bool = Field(
        default=cfg.APPTAINER_FAKEROOT,
        description="Enable --fakeroot option in apptainer",
        json_schema_extra=dict(arg='--fakeroot'),
    )

    userns: bool = Field(
        default=False,
        description="Enable --userns option in apptainer",
        json_schema_extra=dict(arg='--userns'),
    )

    sharens: bool = Field(
        default=False,
        description="Enable --sharens option in apptainer",
        json_schema_extra=dict(arg='--sharens'),
    )

    uts: bool = Field(
        default=False,
        description="Enable --uts option in apptainer",
        json_schema_extra=dict(arg='--uts'),
    )

    containall: bool = Field(
        default=cfg.APPTAINER_CONTAINALL,
        description="Contain not only file systems, but also PID, IPC, and environment",
        json_schema_extra=dict(arg='--containall'),
    )

    no_init: bool = Field(
        default=True,
        description="Do NOT start shim process with --pid",
        json_schema_extra=dict(arg='--no-init'),
    )

    no_umask: bool = Field(
        default=True,
        description="Do not propagate umask to the container, set default 0022 umask",
        json_schema_extra=dict(arg='--no-umask'),
    )

    no_eval: bool = Field(
        default=True,
        description="Do not shell evaluate env vars or OCI container CMD/ENTRYPOINT/ARGS",
        json_schema_extra=dict(arg='--no-eval'),
    )

    no_home: bool = Field(
        default=True,
        description="Do not bind home by default",
        json_schema_extra=dict(arg='--no-home'),
    )


    no_privs: bool = Field(
        default=True,
        description="Drop all privileges from root user in container",
        json_schema_extra=dict(arg='--no-privs'),
    )

    nvidia_support: bool = Field(
        default=False,
        description="Enable nVidia support",
        json_schema_extra=dict(arg='--nv'),
    )

    memory: int = Field(
        default=None,
        description="Memory limit in bytes",
        json_schema_extra = dict(arg='--memory %d', requires=['cgroups']),
    )

    memory_reservation: int = Field(
        default=None,
        description="Memory reservation in bytes",
        json_schema_extra = dict(arg='--memory-reservation %d', requires=['cgroups']),
    )

    working_dir: str = Field(
        default=None,
        description="Initial working directory for payload process inside the container",
        json_schema_extra = dict(arg='--cwd %s'),
    )

    cleanenv: bool = Field(
        default=True,
        description="Clean the environment of the spawned container",
        json_schema_extra = dict(arg='--cleanenv'),
    )

    unsquash: bool = Field(
        default=False,
        description="Convert SIF file to temporary sandbox before running",
        json_schema_extra = dict(arg='--unsquash'),
    )

    tmp_dir_mode: Literal['bind', 'scratch', 'none'] = Field(
        default='scratch',
        description="Technique to make /tmp and /var/tmp available to the contained application",
    )

    @property
    def tmp_dir(self):
        return os.path.join(self.scratch_area, "tmp", self.uid)

    @property
    def var_tmp_dir(self):
        return os.path.join(self.scratch_area, "var-tmp", self.uid)

    @property
    def workdir(self):
        return os.path.join(self.scratch_area, f".acb.cnt.{self.uid}")

    @property
    def log_path(self):
        return os.path.join(self.workdir, f'{self.uid}.out')

    @property
    def return_code_path(self):
        return os.path.join(self.workdir, f'{self.uid}.status')

    @property
    def env_file_path(self):
        return os.path.join(self.workdir, '.env')

    @property
    def executable_path(self):
        return os.path.join(self.workdir, 'run')



    def __hash__(self):
        return make_uid_numeric(self.uid)

    @property
    def shub_token(self):
        global _GLOBAL_SHUB_PROXY_TOKEN
        if self.shub_proxy_server is None or self.shub_proxy_server == "":
            return None

        if _GLOBAL_SHUB_PROXY_TOKEN is not None and (datetime.now() - _GLOBAL_SHUB_PROXY_TOKEN[0]).seconds > 300:
            _GLOBAL_SHUB_PROXY_TOKEN = None   # Expired!

        if _GLOBAL_SHUB_PROXY_TOKEN is None and _GLOBAL_SHUB_PROXY_TOKEN != "":
            response = requests.get(f"http://{self.shub_proxy_server}/token", auth=("admin", self.shub_proxy_master_token))
            response.raise_for_status()

            _GLOBAL_SHUB_PROXY_TOKEN = (datetime.now(), response.text)

        return  _GLOBAL_SHUB_PROXY_TOKEN[1]


    @property
    def formatted_image(self):
        if '://' in self.image or self.default_format in ['none']:
            return self.image

        return f"{self.default_format}://{self.image}"

    @property
    def flags(self):
        ret = []

        # Configurations
        for prop_name, prop_schema in self.model_json_schema()['properties'].items():
            if 'arg' in prop_schema.keys():
                if prop_schema['type'] == 'boolean' and getattr(self, prop_name):
                    ret.append(prop_schema['arg'])
                elif prop_schema['type'] in ('integer', 'string') and getattr(self, prop_name) is not None:
                    ret.append(prop_schema['arg'] % getattr(self, prop_name))

        # Environment
        ret += [f'--env-file {self.env_file_path}']

        # Volumes
        if self.tmp_dir_mode == 'bind':
            ret += [f'--bind {self.tmp_dir}:/tmp', f'--bind {self.var_tmp_dir}:/var/tmp']
        elif self.tmp_dir_mode == 'scratch':
            ret += [f'--scratch /tmp', f'--scratch /var/tmp', f'--workdir {self.tmp_dir}']
        elif self.tmp_dir_mode == 'none':
            pass

        ret += [str(vb) for vb in set(self.volume_binds)]

        # Executable
        if self.entrypoint:
            ret += [f'--bind {self.executable_path}:/mnt/apptainer_cmd_builder/run']

        return ret

    def exec(self):
        uid = sanitize_uid(self.uid).upper()
        if self.entrypoint is not None:
            # Execute a custom entrypoint
            return " \\\n    ".join([
                str(self.executable),
                "exec",
                *self.flags,
                f"$IMAGE_{uid}",
                '/mnt/apptainer_cmd_builder/run &> ',
                self.log_path
                ])
        else:
            # Execute the default entrypoint
            return " \\\n    ".join([
                str(self.executable),
                "run",
                *self.flags,
                f"$IMAGE_{uid}",
                ' '.join(['"%s"' % a for a in self.args]) + ' &> ',
                self.log_path
            ])


    def initialize(self):
        uid = sanitize_uid(self.uid).upper()
        env_dict = dict(
            GENERATED_WITH='ApptainerCmdBuilder',
            ACB_UID=self.uid,
            **self.environment,
        )

        ret = [
            embed_ascii_file(
                path=self.env_file_path,
                file_content='\n'.join([f'{k}="{v}"' for k, v in env_dict.items()]),
                executable=False,
            ),
        ]

        ret += [
            f"mkdir -p {self.tmp_dir}",
            f"mkdir -p {self.var_tmp_dir}",
        ]

        if self.entrypoint:
            ret += [
                embed_ascii_file(
                    path=self.executable_path,
                    file_content='\n'.join([
                        '#!/bin/sh',
                        self.entrypoint + ' ' + ' '.join([shlex.quote(arg) for arg in self.args])
                    ]),
                    executable=True,
                )
            ]

        local_image = os.path.join(self.readonly_image_dir, self.image.replace(":", "_"))
        cached_image = os.path.join(self.cachedir, self.image.replace(":", "_"))
        cvmfs_enable, cvmfs_image = (
            (1, os.path.join(self.cvmfs_unpacked_path, self.image))
            if self.cvmfs_unpacked_path is not None else
            (0, None)
        )

        rndid = generate_uid()
        if self.shub_token is not None and self.formatted_image.startswith("docker"):
            ret += [dedent(f"""
                REMOTE_IMAGE_MD5=$(curl -Lvs {self.shub_proxy_server}/get-docker-md5/{self.image} -H \"X-Token: {self.shub_token}\" )
                if [ -f {local_image} ]; then
                    echo "Using local static image from {local_image}"
                    IMAGE_{uid}={local_image}
                elif [ {cvmfs_enable} -eq 1 ] && [ -d {cvmfs_image} ]; then
                    echo "Using cvmfs image from {cvmfs_image}"
                    IMAGE_{uid}={cvmfs_image}
                elif [ -f {cached_image} ] && [[ "$REMOTE_IMAGE_MD5" == "$(md5sum {cached_image} | cut -d ' ' -f 1)" ]]; then
                    IMAGE_{uid}={cached_image}
                else
                    if [ -f {cached_image} ]; then
                        touch {cached_image}  # Avoid concurrent jobs to update the spoiled image
                    fi
                    mkdir -p {os.path.dirname(cached_image)}
                    HTTP_STATUS=$(curl -Lo {cached_image}-{rndid}.tmp \\
                        -w \"%{{http_code}}\" \\
                        -H \"X-Token: {self.shub_token}\" \\
                        {self.shub_proxy_server}/get-docker/{self.image}) 
                    if [[ $HTTP_STATUS -ge 200 && $HTTP_STATUS -lt 300 ]] \\
                            && [[ "$REMOTE_IMAGE_MD5" == "$(md5sum {cached_image}-{rndid}.tmp | cut -d ' ' -f 1)" ]]; then 
                        mv {cached_image} {cached_image}-{rndid}.rm     # Replace image with metadata operation
                        mv {cached_image}-{rndid}.tmp {cached_image} 
                        rm -f {cached_image}-{rndid}.rm                 # Clean the old image
                        IMAGE_{uid}={cached_image} 
                        echo "Successfully obtained and cached image in {cached_image}"
                    else 
                        echo "Could not retrieve image from remote cache (error $HTTP_STATUS), will rebuild."
                        rm -f {cached_image}-{rndid}.tmp                    # Clean the corrupted image
                        IMAGE_{uid}={self.formatted_image} 
                    fi 
                fi
                """
            )]
        else:
            ret += [
                f"if [ -f {local_image} ]; then",
                f"  echo Using local image from {local_image}",
                f"  IMAGE_{uid}={local_image}",
                f"elif [ {cvmfs_enable} -eq 1 ] && [ -f {cvmfs_image} ]; then",
                f"  echo Using cvmfs image from {cvmfs_image}",
                f"  IMAGE_{uid}={cvmfs_image}",
                f"else",
                f"  IMAGE_{uid}={self.formatted_image}",
                f"fi",
            ]

        return '\n'+'\n'.join(ret)
