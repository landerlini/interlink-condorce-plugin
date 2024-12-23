import os
from io import StringIO
import json
from typing import List, Literal
from pydantic import BaseModel, Field


class BuildConfig(BaseModel):
    class Volumes(BaseModel, extra='forbid'):
        """
        Volumes and directories configuring access to executor-local data
        """
        scratch_area: str = Field(
            default_factory=lambda: os.environ.get("SCRATCH_AREA", "/tmp"),
            description="Area in the executor filesystem to be used for temporary files",
        )
        apptainer_cachedir: str = Field(
            default_factory=lambda: os.environ.get("APPTAINER_CACHEDIR", "/tmp/cache/apptainer"),
            description="Location to cache singularity images in the executor filesystem",
        )
        image_dir: str = Field(
            default_factory=lambda: os.environ.get("IMAGE_DIR", "/opt/exp_software/opssw/budda"),
            description="Location where to look for pre-built images",
        )
        additional_directories_in_path: List[str] = Field(
            default_factory=lambda: os.environ.get(
                "ADDITIONAL_DIRECTORIES_IN_PATH",
                "/opt/exp_software/opssw/mabarbet/bin",
            ).split(":"),
            description="Colon-separated list of directories to include in $PATH to look for executables"
        )

    class ApptainerOptions(BaseModel, extra='forbid'):
        """
        Options configuring the behavior of apptainer runtime
        """
        executable: str = Field(
            default="/usr/bin/apptainer",
            description="Relative or absolute path to apptainer, singularity or other compatible replacements"
        )
        fakeroot: bool = Field(
            default_factory=lambda: os.environ.get("APPTAINER_FAKEROOT", "no").lower() in ["true", "yes", "y"],
            description="Enables --fakeroot in apptainer exec/run commands"
        )
        containall: bool = Field(
            default_factory=lambda: os.environ.get("APPTAINER_FAKEROOT", "no").lower() in ["true", "yes", "y"],
            description="Enables --containall flag in apptainer exec/run commands",
        )
        fuse_mode: Literal["container", "host", "host-privileged"] = Field(
            default_factory=lambda: os.environ.get("FUSE_MODE", "container"),
            description="""Define the technique to mount fuse volumes to adopt on the host.
             * host: assumes the executable is available in the host, but uses the unprivileged /dev/fdX trick to mount 
             * container: assumes the executable is available in the container
             * host-privileged: assumes the executable is available in the host and that can be run by submitter user
             
             The different modes should be preferred in various scenarios as all of them may fail if the submitted 
             container expects something different. `host` is probably the most secure option, `container` the most 
             flexible for your user (but with great power...).
            """
        )
        no_init: bool = Field(
            default=False,
            description="Do not propagate umask to the container, set default 0022 umask",
        )
        no_home: bool = Field(
            default=True,
            description="Do not bind home by default"
        )
        no_privs: bool = Field(
            default=True,
            description="Drop all privileges from root user in container"
        )
        nvidia_support: bool = Field(
            default=False,
            description="Enable nVidia GPU support",
        )
        cleanenv: bool = Field(
            default=True,
            description="Clean the environment of the spawned container"
        )

        @property
        def fuse_enabled_on_host(self):
            """True if the submitter has sufficient privileges on the host to mount fuse volumes on the host itself"""
            return self.fuse_mode in ("host-privileged", "container-privileged")

    class SingularityHubProxy(BaseModel, extra='forbid'):
        """
        Proxy to download pre-build Docker Images instead of rebuilding at each execution
        """
        server: str = Field(
            default_factory=lambda: os.environ.get("SHUB_PROXY", ""),
            description="shub proxy instance used to retrieve cached singularity images. Without protocol!",
        )
        master_token: str = Field(
            default_factory=lambda: os.environ.get("SHUB_PROXY_MASTER_TOKEN", ""),
            description="Master token of the proxy used to request and generate client tokens",
        )
        cache_validity_seconds: int = Field(
            default = 600,
            description="""Automatically cached images are invalidated after this time (expressed in seconds). 
            Note that image hash is not checked, and cache is based on tag only. 
            Expiration is the only cache invalidation mechanism.
            """,
        )


    volumes: Volumes = Field(
        default=Volumes(),
        description=Volumes.__doc__
    )
    apptainer: ApptainerOptions = Field(
        default=ApptainerOptions(),
        description=ApptainerOptions.__doc__
    )
    shub_proxy: SingularityHubProxy = Field(
        default=SingularityHubProxy(),
        description=SingularityHubProxy.__doc__
    )

    def __str__(self):
        ret = StringIO()
        schema = self.model_json_schema()
        defs = schema.get("$defs", {})
        properties = schema.get("properties", {})
        for section_name, section in properties.items():
            if section_name.startswith("$"):
                continue
            ref = section['$ref'].split("/")[-1]
            section_description = defs.get(ref, {}).get("description")
            if section_description is not None:
                section_description = "\n".join([f"# {line}" for line in section_description.split("\n") if len(line)])
                print(section_description, file=ret)

            print(f"[{section_name}]", file=ret)

            for entry, data in defs.get(ref, {}).get("properties", {}).items():
                for line in data.get("description", "").split('\n'):
                    print("\n### ", line, file=ret)
                value = self.model_dump().get(section_name, {}).get(entry)
                print(f"{entry} = {json.dumps(value)}", file=ret)
            data = defs.get(ref, {}).get("properties", {})
            print("\n" * 2, file=ret)

        ret.seek(0)
        return ret.read()

    def apptainer_cmd_builder_config(self):
        """Provide kwargs to configure BaseVolume according to the options defined in ApptainerCmdBuilderConfig"""
        return dict(
            scratch_area=self.volumes.scratch_area,
            cachedir=self.volumes.apptainer_cachedir,
            additional_directories_in_path=self.volumes.additional_directories_in_path,
        )

    def base_volume_config(self):
        """Provide kwargs to configure BaseVolume according to the options defined in BaseVolume"""
        return dict(
            fuse_mode=self.apptainer.fuse_mode,
            fuse_enabled_on_host=self.apptainer.fuse_enabled_on_host,
            scratch_area=self.volumes.scratch_area,
        )

    def container_spec_config(self):
        """Provide kwargs to configure BaseVolume according to the options defined in ContainerSpec"""
        return dict(
            executable=self.apptainer.executable,
            scratch_area=self.volumes.scratch_area,
            cachedir=self.volumes.apptainer_cachedir,
            shub_proxy_server=self.shub_proxy.server,
            shub_proxy_master_token=self.shub_proxy.master_token,
            shub_cache_seconds=self.shub_proxy.cache_validity_seconds,
            readonly_image_dir=self.volumes.image_dir,
            fakeroot=self.apptainer.fakeroot,
            containall=self.apptainer.containall,
            no_init=self.apptainer.no_init,
            no_home=self.apptainer.no_home,
            no_privs=self.apptainer.no_privs,
            nvidia_support=self.apptainer.nvidia_support,
            cleanenv=self.apptainer.cleanenv,
        )
