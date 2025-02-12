import os
import logging
import traceback
from io import StringIO
import json
from typing import List, Literal, Union, Optional
from pydantic import BaseModel, Field

from tomli import load as toml_load, TOMLDecodeError

MISSING_BUILD_CONFIG_ERROR_CODE = 127


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
        fuse_sleep_seconds: int = Field(
            default_factory=lambda: int(os.environ.get("FUSE_SLEEP_WAIT", "5")),
            description="""Time in seconds to wait before executing the job payload in case fuse volumes are used.
            Since the mount command is run in a detached processes, the job may start to look for contents in the 
            wannabe-mounted volume before fuse actually managed to provide POSIX access. 
            A sleep command is introduced between the mount command and the actual execution of the job to reduce 
            the probability that the job starts before the volumes are available. 
            """
        )

    class SlurmOptions(BaseModel, extra='forbid'):
        """
        Options configuring the behavior of SLURM runtime.
        """
        bash_executable: str = Field(
            default="/usr/local/bin/sbatch",
            description="Relative or absolute path to bash",
        )
        sbatch_executable: str = Field(
            default="/usr/local/bin/sbatch",
            description="Relative or absolute path to sbatch",
        )
        squeue_executable: str = Field(
            default="/usr/local/bin/squeue",
            description="Relative or absolute path to squeue",
        )
        singularity_executable: str = Field(
            default=None,
            description="Relative or absolute path to singularity",
        )
        nodes: int = Field(
            default=1,
            description="Number of tasks to be run in parallel (SLURM flag: --ntasks or -n)",
            json_schema_extra=dict(arg='--nodes %d'),
        )
        ntasks: int = Field(
            default=1,
            description="Number of tasks to be run in parallel (SLURM flag: --ntasks or -n)",
            json_schema_extra=dict(arg='--ntasks %d'),
        )
        cpus_per_task: int = Field(
            default=None,
            description="Number of CPUs per task (SLURM flag: --cpus-per-task)",
            json_schema_extra=dict(arg='--cpu-per-task %s'),
        )
        mem_per_cpu: str = Field(
            default=None,
            description="Memory per CPU (SLURM flag: --mem-per-cpu)",
            json_schema_extra=dict(arg='--mem-per-cpu %s'),
        )
        partition: str = Field(
            default=None,
            description="Partition to submit the job to (SLURM flag: --partition or -p)",
            json_schema_extra=dict(arg='--partition %s'),
        )
        time: int = Field(
            default=None,
            description="Time limit for the job in hours (SLURM flag: --time or -t)",
            json_schema_extra=dict(arg='--time %d:00:00'),
        )
        qos: str = Field(
            default=None,
            description="Quality of service (SLURM flag: --qos)",
            json_schema_extra=dict(arg='--qos %s'),
        )
        account: str = Field(
            default=None,
            description="Account to charge the job to (SLURM flag: --account or -A)",
            json_schema_extra=dict(arg='--account %s'),
        )
        job_name: str = Field(
            default=None,
            description="Name of the job (SLURM flag: --job-name or -J)",
            json_schema_extra=dict(arg='--job-name %s'),
        )
        mail_user: str = Field(
            default=None,
            description="Email address to send notifications to (SLURM flag: --mail-user)",
            json_schema_extra=dict(arg='--mail-user %s'),
        )
        mail_type: str = Field(
            default=None,
            description="Type of notifications to send (SLURM flag: --mail-type)",
            json_schema_extra=dict(arg='--mail-type %s'),
        )
        output: str = Field(
            default="%(sandbox)s/stdout.log",
            description="Output file (SLURM flag: --output or -o)",
            json_schema_extra=dict(arg='--output %s'),
        )
        error: str = Field(
            default="%(sandbox)s/stderr.log",
            description="Error file (SLURM flag: --error or -e)",
            json_schema_extra=dict(arg='--error %s'),
        )
        generic_resources: List[str] = Field(
            default=[],
            description="List of generic resources (SLURM flag: --gres)",
            json_schema_extra=dict(arg='--gres %s'),
        )
        log_dir: str = Field(
            default=None,
            description="Directory where to store logs (No direct SLURM flag, but can be used in paths for output/error logs)",
        )
        flags: List[str] = Field(
            default_factory=lambda: os.environ.get("SLURM_FLAGS", "").split(":"),
            description="Colon-separated list of additional SLURM flags (Can include any SLURM command-line options)",
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
            description="Clean the environment of the spawned container",
        )
        unsquash: bool = Field(
            default=False,
            description="Convert SIF file to temporary sandbox before running",
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
            DEPRECATED and IGNORED: replaced with a check on the image hash.
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
    slurm: SlurmOptions = Field(
        default=SlurmOptions(),
        description=SlurmOptions.__doc__
    )

    input_toml_filename: Optional[str] = Field(
        default=None,
        exclude=True,
        description="Internal. Stores the filename from which the config was taken to enable reloading."
    )

    input_toml_filename: Optional[str] = Field(
        default=None,
        exclude=True,
        description="Internal. Stores the filename from which the config was taken to enable reloading."
    )

    def __str__(self):
        ret = StringIO()
        schema = self.model_json_schema()
        defs = schema.get("$defs", {})
        properties = schema.get("properties", {})
        for section_name, section in properties.items():
            if section_name.startswith("$") or '$ref' not in section:
                continue
            ref = section['$ref'].split("/")[-1]
            section_description = defs.get(ref, {}).get("description")
            if section_description is not None:
                section_description = "\n".join([f"# {line}" for line in section_description.split("\n") if line])
                print(section_description, file=ret)

            print(f"[{section_name}]", file=ret)

            for entry, data in defs.get(ref, {}).get("properties", {}).items():
                for line in data.get("description", "").split('\n'):
                    print("\n### ", line, file=ret)
                value = self.model_dump().get(section_name, {}).get(entry)
                print(f"{entry} = {json.dumps(value)}", file=ret)
            
            print("\n" * 2, file=ret)

        ret.seek(0)
        return ret.read()

    def apptainer_cmd_builder_config(self):
        """Provide kwargs to configure BaseVolume according to the options defined in ApptainerCmdBuilderConfig"""
        return dict(
            scratch_area=self.volumes.scratch_area,
            cachedir=self.volumes.apptainer_cachedir,
            additional_directories_in_path=self.volumes.additional_directories_in_path,
            fuse_sleep_seconds=self.volumes.fuse_sleep_seconds,
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
            unsquash=self.apptainer.unsquash,
        )

    @property
    def logger(self):
        return logging.getLogger(self.__class__.__name__)


    @classmethod
    def from_file(cls, filename: Union[str, None]):
        """
        Loads the build config from a TOML file.
        """
        logger = logging.getLogger(cls.__name__)
        tolerate_missing_file = (filename is None)
        build_config_file = filename if filename is not None else '/etc/interlink/build.conf'

        if not os.path.exists(build_config_file):
            if tolerate_missing_file:
                logger.warning(
                    f"Build configuration file {build_config_file} does not exist. Using default configuration."
                    )
                return cls()
            else:
                logger.critical(f"Build configuration file {build_config_file} does not exist.")
                exit(MISSING_BUILD_CONFIG_ERROR_CODE)
        else:
            with open(build_config_file, 'rb') as input_file:
                return cls(**toml_load(input_file), input_toml_filename=build_config_file)

    def reload(self):
        """
        Reload a BuildConfig from file.

        Usage:
            build_config = build_config.reload()
        """
        if self.input_toml_filename is None:
            self.logger.warning(f"Cannot reload BuildConfig for unknown input file.")
            return self

        try:
            return BuildConfig.from_file(self.input_toml_filename)
        except (IOError, FileNotFoundError, TOMLDecodeError) as e:
            logging.error(f"Update of BuildConfig failed.\n" + traceback.format_exc())
            return self

