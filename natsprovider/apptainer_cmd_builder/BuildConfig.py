import os
import logging
import traceback
from io import StringIO
import json
from typing import Collection, Dict, List, Literal, Union, Optional
from pydantic import BaseModel, Field

from tomli import load as toml_load, TOMLDecodeError

from ..utils import Resources

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
        cvmfs_unpacked_path: Optional[str] = Field(
            default=None,
            description="Optional path for the unpacked repository in cvmfs (used to docker images lookup)"
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

    class NodeOptions(BaseModel, extra='forbid'):
        """
        Options configuring the node with labels and taints to allow affinity and node selection mechanics.
        """
        labels: List[str] = Field(
            default=[],
            description="List of labels in format key=value to configure the Kueue ResourceFlavor",
        )
        taints: List[str] = Field(
            default=[],
            description="List of taints in format key=value:effect to configure the Kueue ResourceFlavor",
        )

    class SlurmOptions(BaseModel, extra='forbid'):
        """
        Options configuring the behavior of SLURM runtime.
        """
        class SlurmFlavor(BaseModel, extra='forbid'):
            """
            Define a flavor (partition and qos) for a slurm job, and criteria on the Kubernetes-defined
            resources to be matched.
            """
            account: Optional[str] = Field(
                default=None,
                description="Slurm account (as for --account flag)",
                json_schema_extra=dict(arg='--account %s'),
            )
            partition: Optional[str] = Field(
                default=None,
                description="Slurm partition (as for --partition flag)",
                json_schema_extra=dict(arg='--partition %s'),
            )
            qos: Optional[str] = Field(
                default=None,
                description="Slurm quality of service (as for --qos flag)",
                json_schema_extra=dict(arg='--qos %s'),
            )
            generic_resources: List[str] = Field(
                default=[],
                description="List of generic resources (SLURM flag: --gres)",
                json_schema_extra=dict(arg='--gres %s'),
            )
            max_time_seconds: Optional[int] = Field(
                default=None,
                description="Maximum duration of a pod (as for activeDeadlineSeconds) for being assigned.",
                json_schema_extra=dict(arg='--time %d'),
            )
            max_resources: Dict[Literal['cpu', 'memory', 'nvidia.com/gpu'], Union[str, int]] = Field(
                default={},
                description="Maximum (extended) resources of the pod for being assigned to this flavor."
            )

            memory: Optional[str] = Field(
                default=None,
                description="Default value of the RAM memory to assign to the job.",
                json_schema_extra=dict(arg='--mem %sB'),
            )
            cpu: Optional[int] = Field(
                default=None,
                description="Number of CPUs per task",
                json_schema_extra=dict(arg='--cpu-per-task %d'),
            )
            ntasks: Optional[int] = Field(
                default=None,
                description="Number of tasks to be run in parallel (SLURM flag: --ntasks or -n)",
                json_schema_extra=dict(arg='--ntasks %d'),
            )
            reservation: Optional[str] = Field(
                default=None,
                description="Specify a reservation in the slurm cluster",
                json_schema_extra = dict(arg='--reservation %s'),
            )
            nodelist: Optional[str] = Field(
                default=None,
                description="Specify a list of node in the slurm cluster",
                json_schema_extra = dict(arg='--nodelist %s'),
            )


        flavors: Optional[List[SlurmFlavor]] = Field(
            default=None,
            description="""List of flavors that can be matched by incoming pods based on the Kubernetes-defined 
                resources. Flavors are tested for compatibility in order, the first eligible is assigned."""
        )
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
        sacct_executable: str = Field(
            default="/usr/local/bin/sacct",
            description="Relative or absolute path to sacct",
        )
        nodes: int = Field(
            default=1,
            description="Number of tasks to be run in parallel (SLURM flag: --ntasks or -n)",
            json_schema_extra=dict(arg='--nodes %d'),
        )
        cpus_per_task: Optional[int] = Field(
            default=None,
            description="Number of CPUs per task (SLURM flag: --cpus-per-task)",
            json_schema_extra=dict(arg='--cpu-per-task %s'),
        )
        mem_per_cpu: Optional[str] = Field(
            default=None,
            description="Memory per CPU (SLURM flag: --mem-per-cpu)",
            json_schema_extra=dict(arg='--mem-per-cpu %s'),
        )
        partition: Optional[str] = Field(
            default=None,
            description="Partition to submit the job to (SLURM flag: --partition or -p)",
            json_schema_extra=dict(arg='--partition %s'),
        )
        time: Optional[int] = Field(
            default=None,
            description="Time limit for the job in hours (SLURM flag: --time or -t)",
            json_schema_extra=dict(arg='--time %d:00:00'),
        )
        qos: Optional[str] = Field(
            default=None,
            description="Quality of service (SLURM flag: --qos)",
            json_schema_extra=dict(arg='--qos %s'),
        )
        max_time_seconds: Optional[int] = Field(
            default=None,
            description="Maximum duration of a pod (as for activeDeadlineSeconds) for being assigned.",
            json_schema_extra=dict(arg='--time %d'),
        )
        account: Optional[str] = Field(
            default=None,
            description="Account to charge the job to (SLURM flag: --account or -A)",
            json_schema_extra=dict(arg='--account %s'),
        )
        job_name: Optional[str] = Field(
            default=None,
            description="Name of the job (SLURM flag: --job-name or -J)",
            json_schema_extra=dict(arg='--job-name %s'),
        )
        mail_user: Optional[str] = Field(
            default=None,
            description="Email address to send notifications to (SLURM flag: --mail-user)",
            json_schema_extra=dict(arg='--mail-user %s'),
        )
        mail_type: Optional[str] = Field(
            default=None,
            description="Type of notifications to send (SLURM flag: --mail-type)",
            json_schema_extra=dict(arg='--mail-type %s'),
        )
        sandbox: str = Field(
            default=os.environ.get("LOCAL_SANDBOX", "/tmp"),
            description="Directory shared between the compute and login nodes used to transfer the logs",
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
        memory: Optional[str] = Field(
            default="4G",
            description="Default value of the RAM memory to assign to the job.",
            json_schema_extra=dict(arg='--mem %s'),
        )
        cpu: Optional[int] = Field(
            default=1,
            description="Number of CPUs per task",
            json_schema_extra=dict(arg='--cpus-per-task %d'),
        )
        ntasks: Optional[int] = Field(
            default=1,
            description="Number of tasks to be run in parallel (SLURM flag: --ntasks or -n)",
            json_schema_extra=dict(arg='--ntasks %d'),
        )
        reservation: Optional[str] = Field(
            default=None,
            description="Specify a reservation in the slurm cluster",
            json_schema_extra=dict(arg='--reservation %s'),
        )
        nodelist: Optional[str] = Field(
            default=None,
            description="Specify a list of node in the slurm cluster",
            json_schema_extra=dict(arg='--nodelist %s'),
        )
        generic_resources: List[str] = Field(
            default=[],
            description="List of generic resources (SLURM flag: --gres)",
            json_schema_extra=dict(arg='--gres=%s'),
        )
        flags: List[str] = Field(
            default_factory=lambda: os.environ.get("SLURM_FLAGS", "").split(":"),
            description="Colon-separated list of additional SLURM flags (Can include any SLURM command-line options)",
        )
        header: str = Field(
            default="",
            description="Bash lines to be prepended to the job script, usually setting the environment."
        )
        footer: str = Field(
            default="",
            description="Bash lines to be appended to the job script, usually setting the environment."
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
        userns: bool = Field(
            default=False,
            description="Enables --userns (user namespace) in apptainer exec/run commands"
        )
        uts: bool = Field(
            default=False,
            description="Enables --uts (hostname and nis domain namespace) in apptainer exec/run commands"
        )
        sharens: bool = Field(
            default=False,
            description="Share the namespace and image with other containers launched from the same parent process"
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
        tmp_dir_mode: Literal['bind', 'scratch', 'none'] = Field(
            default='scratch',
            description="Technique to make /tmp and /var/tmp available to the contained application",
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


    class NetworkOptions(BaseModel, extra='forbid'):
        """
        Proxy to download pre-build Docker Images instead of rebuilding at each execution
        """
        allowed: bool = Field(
            default=True,
            description="Allow using port forwarding for enabling networking with the origin cluster"
        )

        wstunnel_binary: str = Field(
            default="$HOME/bin/wstunnel",
            description="Local path of the wstunnel executable"
        )

        slirp4netns_binary: str = Field(
            default="$HOME/bin/slirp4netns",
            description="Local path to slirp4netns binary"
        )

        dynamic_fwd_port: int = Field(
            default=54006,
            description="Port of the netns used for dynamic port forwarding",
        )

        tmp_resolv_conf: str = Field(
            default="$(pwd)/.resolv.$RANDOM$RANDOM.conf",
            description="Location for the generated resolv.conf file with nameserver config"
        )

        tap_cidr: str = Field(
            default="172.18.2.0/24",
            description="Network CIDR for the TAP device"
        )

        tap_mtu: int = Field(
            default=1280,
            description="Maximum transfer unit of the TAP device"
        )

        tun_ip: str = Field(
            default="172.18.3.1",
            description="Static IP configuration for the TUN network interface"
        )

        tun2socks_binary: str = Field(
            default="$HOME/bin/tun2socks",
            description="Local path to slirp4netns binary"
        )

        slirp4netns_binary: str = Field(
            default="$HOME/bin/slirp4netns",
            description="Local path to slirp4netns binary"
        )

        proxy_cmd: str = Field(
            default="interlink_proxy_cmd",
            description="How to start the proxy command (if any) in front of apptainer",
        )

        tunnel_finalization: str = Field(
            default="interlink_cleanup",
            description="Finalize the tunnel",
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
    resources: Resources = Field(
        default=Resources(),
        description="Computing resources made available by the pool (not by the single submitter!)"
    )
    node: NodeOptions = Field(
        default=NodeOptions(),
        description=NodeOptions.__doc__
    )
    network: NetworkOptions = Field(
        default=NetworkOptions(),
        description=NetworkOptions.__doc__
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
            additional_directories_in_path=self.volumes.additional_directories_in_path,
            cachedir=self.volumes.apptainer_cachedir,
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
            cvmfs_unpacked_path=self.volumes.cvmfs_unpacked_path,
            fakeroot=self.apptainer.fakeroot,
            userns=self.apptainer.userns,
            sharens=self.apptainer.sharens,
            uts=self.apptainer.uts,
            containall=self.apptainer.containall,
            no_init=self.apptainer.no_init,
            no_home=self.apptainer.no_home,
            no_privs=self.apptainer.no_privs,
            nvidia_support=self.apptainer.nvidia_support,
            cleanenv=self.apptainer.cleanenv,
            unsquash=self.apptainer.unsquash,
            tmp_dir_mode=self.apptainer.tmp_dir_mode,
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


    @staticmethod
    def check_type(config: BaseModel, key: str, types: Collection[str]):
        properties = config.model_json_schema()['properties']
        if key not in properties.keys():
            return False

        if 'type' in properties[key].keys():
            return properties[key]['type'] in types

        if 'anyOf' in properties[key].keys():
            for alt_dict in properties[key]['anyOf']:
                if 'type' in alt_dict.keys() and alt_dict['type'] in types:
                    return True

        return False
