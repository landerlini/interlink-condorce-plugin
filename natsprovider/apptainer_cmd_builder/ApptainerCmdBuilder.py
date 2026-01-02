import os.path
import tarfile

from pydantic import BaseModel, Field
from typing import List, BinaryIO
from . import version
import textwrap

from . import configuration as cfg
from .NetworkConfig import NetworkConfig
from .volumes import BaseVolume, FuseVolume
from .containers import ContainerSpec
from ..utils import generate_uid

class ApptainerCmdBuilder(BaseModel, extra='forbid'):
    uid: str = Field(
        default_factory=generate_uid,
        description="Unique identifier for the set of containers",
    )

    containers: List[ContainerSpec] = Field(
        description="List of containers representing processes at initialization"
    )

    init_containers: List[ContainerSpec] = Field(
        default=[],
        description="List of containers representing processes to be executed simultaneously"
    )

    additional_volumes: List[BaseVolume] = Field(
        default=[],
        description="List of volumes to be mounted or bound to the apptainer container"
    )

    additional_directories_in_path: List[str] = Field(
        default=cfg.ADDITIONAL_DIRECTORIES_IN_PATH,
        description="Additional directories in PATH"
    )

    scratch_area: str = Field(
        default=cfg.SCRATCH_AREA,
        description="Directory to be used for temporary data related to this container set",
    )

    description: str = Field(
        default="",
        description="User-defined description of the job"
    )

    cachedir: str = Field(
        default=cfg.APPTAINER_CACHEDIR,
        description="Scratch area to store apptainer images"
    )

    error_code_for_return_code_not_found: int = Field(
        default=-404,
        description="Numerical error code to assign in case of failure at retrieving the error code for a container.",
    )

    fuse_sleep_seconds: int = Field(default=5, description="Time in seconds between fuse mount and job execution.")

    network_config: NetworkingConfig = Field(
        default_factory=lambda: NetworkingConfig(enabled=False),
        description="Configuration of the network infrastructure"
    )

    @property
    def volumes(self) -> List[BaseVolume]:
        all_containers = self.containers + self.init_containers
        return list(set([vb.volume for c in all_containers for vb in c.volume_binds] + self.additional_volumes))

    @property
    def workdir(self):
        return os.path.join(self.scratch_area, f".acb.cluster.{self.uid}")

    def build_environment_files(self):
        return '\n'.join([container.initialize() for container in self.init_containers + self.containers])

    def exec_init_containers(self):
        ret = []
        for container in self.init_containers:
            ret += [
                container.exec(),
                "echo -n $? > %s" % container.return_code_path,
                "cp %s %s" % (container.log_path, container.log_path),
            ]

        return '\n'.join(ret)

    def exec_containers(self):
        ret = [
            "pid=()",
        ]

        for i_container, container in enumerate(self.containers):
            ret += [
                container.exec() + " &",
                f"pids[{i_container}]=$!",
            ]

        ret += [
            "wait ${pids[%d]}; echo -n $? > %s" % (i_container, container.return_code_path)
            for i_container, container in enumerate(self.containers)
        ]

        ret += [
            f"echo '=== Output of container {container.uid} ==='\ncat {container.log_path}"
            for container in self.containers
        ]

        ret += [
            "tar cvf $SANDBOX/logs " + ' '.join([
                *[f"-C {c.workdir} {os.path.basename(c.log_path)}" for c in self.init_containers],
                *[f"-C {c.workdir} {os.path.basename(c.return_code_path)}" for c in self.init_containers],
                *[f"-C {c.workdir} {os.path.basename(c.log_path)}" for c in self.containers],
                *[f"-C {c.workdir} {os.path.basename(c.return_code_path)}" for c in self.containers],
            ])
        ]
        return '\n'.join(ret)

    # def cleanup_environment_files(self):
    #     return '\n'.join([container.finalize() for container in self.init_containers + self.container])

    def build_volume_files(self):
        ret = '\n'.join([volume.initialize() for volume in self.volumes])
        return ret

    def export_of_additional_directories_in_path(self):
        if len(self.additional_directories_in_path) == 0:
            return ""

        ret = ["\n## Creating symbolic links to mask symbols (e.g. colon) in PATH"]
        link = f"{os.path.join(self.workdir, 'bin')}%02d"
        links = []
        for i_path, path in enumerate(self.additional_directories_in_path):
            ret.append(f"ln -s {path} {link % i_path}")
            links.append(link % i_path)

        ret.append(f"export PATH={':'.join(links)}:$PATH")

        return '\n'.join(ret)



    def cleanup_volume_files(self):
        ret = []
        if any([v.fuse_enabled_on_host and isinstance(v, FuseVolume) for v in self.volumes]):
            ret += ["sleep %d;\n" % self.fuse_sleep_seconds]

        # Clean-up temporary data
        for container in (self.containers + self.init_containers):
            ret += [f"rm -rf {container.tmp_dir}", f"rm -rf {container.var_tmp_dir}"]

        return '\n'.join(ret + [volume.finalize() for volume in self.volumes])

    def dump(self):
        script = textwrap.dedent("""
        #!/bin/sh
        
        ################################################################################
        ## Code generated by ApptainerCmdBuilder version %(version)s
        %(docs)s
        
        
        ## Setting umask for creating new files read-writable from all
        umask 0011
        
        export STARTING_DIR=$PWD
        export SANDBOX=${SANDBOX:-$PWD}
        touch $SANDBOX/logs 
        
        rm -rf %(workdir)s
        mkdir -p %(workdir)s
        cd %(workdir)s
        
        export APPTAINER_CACHEDIR=%(apptainer_cachedir)s
        export SINGULARITY_CACHEDIR=$APPTAINER_CACHEDIR
        mkdir -p $APPTAINER_CACHEDIR
        %(export_of_additional_directories_in_path)s
        
        ################################################################################
        ## Environment variables
        %(environment_files)s
        
        ################################################################################
        ## Networking (if enabled)
        ## Tunnel initialization
        %(initialize_network)s
        
        ## Connection 
        %(connect_network)s
        
        ################################################################################
        ## Defines and register the callback for cleaning volumes up upon job termination
        cleanup() {
        echo "Cleaning up volumes"
        %(cleanup_volumes)s
        cd $STARTING_DIR 
        rm -rf %(workdir)s
        }
        trap cleanup SIGTERM SIGKILL EXIT
        
        ## Volumes settings
        %(volume_files)s
        
        ################################################################################
        ## Initialization section
        %(exec_init_containers)s
        
        ################################################################################
        ## Execution section
        %(exec_containers)s
        
        ################################################################################
        ## Output retrival through stdout 
        echo "==== OUTPUT BEGIN %(application_token)s ===="
        echo $(gzip -c $SANDBOX/logs | base64)
        echo "==== OUTPUT END %(application_token)s ===="
        
        %(finalize_network)s
        """) % dict(
            version=version,
            docs=''.join(["## " + line for line in self.description.splitlines()]),
            workdir=self.workdir,
            apptainer_cachedir=self.cachedir,
            environment_files=self.build_environment_files(),
            volume_files=self.build_volume_files(),
            exec_init_containers=self.exec_init_containers(),
            exec_containers=self.exec_containers(),
            cleanup_volumes=self.cleanup_volume_files(),
            export_of_additional_directories_in_path=self.export_of_additional_directories_in_path(),
            application_token=cfg.APPLICATION_TOKEN,
        )

        script_lines = script.split('\n')
        script_lines = [
            next_line
            for line, next_line in zip(script_lines[:-1], script_lines[1:])
            if len(line) + len(next_line)
        ]

        # if cfg.DEBUG:
        #    print ('\n'.join(script_lines))

        return '\n'.join(script_lines)

    def process_logs(self, tar_file: BinaryIO):
        """
        Convert the content of a tar file containing ascii files into a dictionary {file_name: file_content}.
        Then loops over the keys to assign log and return codes to the containers.
        """
        file_contents = {}

        with tarfile.open(fileobj=tar_file, mode='r:*') as tar:
            for member in tar.getmembers():
                if member.isfile():
                    file_content = tar.extractfile(member).read().decode('utf-8')
                    file_contents[member.name] = file_content

        for container in self.containers:
            container.log = (
                    file_contents[os.path.basename(container.log_path)]
                    if os.path.basename(container.log_path) in file_contents.keys()
                    else f"Error retrieving log from {container.log_path}"
            )
            container.return_code = int(
                file_contents[os.path.basename(container.return_code_path)]
                if os.path.basename(container.return_code_path) in file_contents.keys()
                else self.error_code_for_return_code_not_found
            )

        for init_container in self.init_containers:
            init_container.log = (
                file_contents[os.path.basename(init_container.log_path)]
                if (os.path.basename(init_container.log_path)) in file_contents.keys()
                else f"Error retrieving log from {init_container.log_path} (init-container)"
            )
            init_container.return_code = int(
                file_contents[os.path.basename(init_container.return_code_path)]
                if (os.path.basename(init_container.return_code_path)) in file_contents.keys()
                else self.error_code_for_return_code_not_found
            )

