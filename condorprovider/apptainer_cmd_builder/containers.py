import string
from datetime import datetime

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

    entrypoint: Union[str, Path] = Field(
        description="Entrypoint of the job to be executed within the container"
    )

    args: List[str] = Field(
        default = [],
        description="Arguments to be passed to the command defined in the entrypoint"
    )

    executable: Path = Field(
        default=Path("/usr/bin/apptainer").resolve(),
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


    @property
    def workdir(self):
        return os.path.join(self.scratch_area, f".acb.cnt.{self.uid}")

    @property
    def log_path(self):
        return os.path.join(self.workdir, f'{self.uid}.log')

    @property
    def return_code_path(self):
        return os.path.join(self.workdir, f'{self.uid}.ret')

    @property
    def env_file_path(self):
        return os.path.join(self.workdir, '.env')

    @property
    def executable_path(self):
        return os.path.join(self.workdir, 'run')

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


    def __hash__(self):
        return make_uid_numeric(self.uid)

    @property
    def shub_token(self):
        global _GLOBAL_SHUB_PROXY_TOKEN
        if cfg.SHUB_PROXY is None:
            return None

        if _GLOBAL_SHUB_PROXY_TOKEN is not None and (datetime.now() - _GLOBAL_SHUB_PROXY_TOKEN[0]).seconds > 300:
            _GLOBAL_SHUB_PROXY_TOKEN = None   # Expired!

        if _GLOBAL_SHUB_PROXY_TOKEN is None:
            response = requests.get(f"http://{cfg.SHUB_PROXY}/token", auth=("admin", cfg.SHUB_PROXY_MASTER_TOKEN))
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
        ret += [str(vb) for vb in set(self.volume_binds)]

        # Executable
        ret += [f'--bind {self.executable_path}:/mnt/apptainer_cmd_builder/run']

        return ret

    def exec(self):
        uid = sanitize_uid(self.uid).upper()
        return " \\\n    ".join([
            str(self.executable.resolve()),
            "exec",
            *self.flags,
            f"$IMAGE_{uid}",
            '/mnt/apptainer_cmd_builder/run &> ',
            self.log_path
            ])


    def initialize(self):
        uid = sanitize_uid(self.uid).upper()
        env_dict = dict(
            GENERATED_WITH='ApptainerCmdBuilder',
            ACB_UID=self.uid,
            **self.environment,
        )

        entry_point_file = '\n'.join([
            '#!/bin/sh',
            self.entrypoint + ' ' + ' '.join([shlex.quote(arg) for arg in self.args])
        ])

        ret = [
            embed_ascii_file(
                path=self.env_file_path,
                file_content='\n'.join([f'{k}="{v}"' for k, v in env_dict.items()]),
                executable=False,
            ),

            embed_ascii_file(
                path=self.executable_path,
                file_content=entry_point_file,
                executable=True,
            )
        ]

        local_image = os.path.join(self.readonly_image_dir, self.image.replace(":", "_"))
        if self.shub_token is not None and self.formatted_image.startswith("docker"):
            ret += [
                f"if [ -f {local_image} ]; then",
                f"  IMAGE_{uid}={local_image}",
                f"else",
                f"  HTTP_STATUS=$(curl -Lo {os.path.join(self.scratch_area, f'.img.{uid}')} \\"
                f"      -w \"%{{http_code}}\" \\",
                f"      -H \"X-Token: {self.shub_token}\" \\",
                f"      {SHUB_PROXY}/get-docker/{self.image}) ",
                f"  if [[ $HTTP_STATUS -ge 200 && $HTTP_STATUS -lt 300 ]]; then ",
                f"    IMAGE_{uid}={os.path.join(self.scratch_area, f'.img.{uid}')} ",
                f"  else ",
                f"    IMAGE_{uid}={self.formatted_image} ",
                f"  fi ",
                f"fi",
            ]
        else:
            ret += [
                f"if [ -f {local_image} ]; then",
                f"  IMAGE_{uid}={local_image}",
                f"else",
                f"  IMAGE_{uid}={self.formatted_image}",
                f"fi",
            ]

        return '\n'+'\n'.join(ret)
