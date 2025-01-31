from contextlib import asynccontextmanager
from traceback import format_exc
import tarfile
from pathlib import Path
import shutil
import os.path
import subprocess
import re

from copy import copy
from .. import interlink
from ..utils import  compute_pod_resource, JobStatus, Resources
from . import configuration as cfg
from ..BaseNatsProvider import BaseNatsProvider
from ..apptainer_cmd_builder import BuildConfig

from .volumes import BindVolume, TmpFS

class SlurmProvider(BaseNatsProvider):
    def __init__(
            self,
            build_config: BuildConfig,
            **kwargs
    ):
        self._volumes = copy(build_config.volumes)
        build_config.volumes.scratch_area = "/scratch"
        build_config.volumes.apptainer_cachedir = "/cache"
        build_config.volumes.image_dir = "/images"
        self._sandbox = cfg.LOCAL_SANDBOX

        BaseNatsProvider.__init__(self, build_config=build_config, **kwargs)

        # log the build config
        self.logger.info(f"Build config: {build_config}")

    async def create_pod(self, job_name: str, job_sh: str, pod: interlink.PodRequest) -> str:
        """
        Submit the job to Slurm
        """

        self.logger.info(f"Creating pod {job_name}")

        sandbox = Path(self._sandbox) / job_name
        scratch_area = Path(self._volumes.scratch_area) / job_name
        job_script_path = sandbox / "job_script.sh"  # Define the job script path

        # Ensure directories exist
        for dirname in (self._volumes.apptainer_cachedir, scratch_area, sandbox):
            self.logger.info(f"Creating directory {dirname}")
            Path(dirname).mkdir(parents=True, exist_ok=True)

        # Write job_sh to a script file
        with open(job_script_path, "w") as f:
            f.write(job_sh)

        # Make sure the script is executable
        job_script_path.chmod(0o755)

        self.logger.info(f"Start creation of slurm script for job {job_name}")

        # Create the Slurm script
        slurm_script = f"""#!/bin/bash
#SBATCH --job-name={job_name}
#SBATCH --output={sandbox}/stdout.log
#SBATCH --error={sandbox}/stderr.log

singularity --quiet --silent exec \\
    --pwd /sandbox \\
    --bind {self._volumes.apptainer_cachedir}:{self._build_config.volumes.apptainer_cachedir} \\
    --bind {scratch_area}:{self._build_config.volumes.scratch_area} \\
    --bind {sandbox}:/sandbox \\
    --bind {job_script_path}:/sandbox/job_script.sh \\
    {"--bind " + cfg.CVMFS_MOUNT_POINT + ":/cvmfs" if cfg.CVMFS_MOUNT_POINT else ""} \\
    {"--bind " + self._volumes.image_dir + ":" + self._build_config.volumes.image_dir if os.path.exists(self._volumes.image_dir) else ""} \\
    {cfg.CUSTOM_PILOT} /bin/bash /sandbox/job_script.sh
"""

        self.logger.info(f"Slurm script for job {job_name}:\n{slurm_script}")

        # Write the Slurm script
        slurm_script_path = sandbox / "job.sh"
        with open(slurm_script_path, "w") as f:
            f.write(slurm_script)

        # Submit the job
        self.logger.info(f"Submitting job {job_name} to Slurm")

        # Submit the job using sbatch
        try:
            result = subprocess.run(
                ["sbatch", str(slurm_script_path)], # WIP: here the absolute path to sbatch should be taken from the build config
                capture_output=True,
                text=True,
                check=True
            )

            # Parse job ID from sbatch output
            match = re.search(r"Submitted batch job (\d+)", result.stdout)
            if match:
                job_id = match.group(1)
                self.logger.info(f"Job {job_name} submitted with Job ID: {job_id}")
            else:
                self.logger.error(f"Failed to extract job ID from sbatch output: {result.stdout}")
                job_id = None

        except subprocess.CalledProcessError as e:
            self.logger.error(f"Failed to submit job {job_name}: {e.stderr}")
            job_id = None

        return job_id

    async def get_pod_status_and_logs(self, job_name: str) -> JobStatus:
        """
        Get the Slurm job status and retrieve logs.
        """
        self.logger.info(f"Checking status for job {job_name}")
        
        try:
            # Get job status from Slurm
            result = subprocess.run(
                ["squeue", "--name", job_name, "--noheader", "-o", "%T"],
                capture_output=True, text=True, check=True
            )
            self.logger.debug(f"Slurm query result: {result.stdout}")
            job_status = result.stdout.strip()
            
        except subprocess.CalledProcessError as e:
            self.logger.critical(f"Failed to query Slurm for job {job_name}: {e.stderr}")
            return JobStatus(phase="unknown")
        
        self.logger.info(f"Retrieved job {job_name} with status {job_status}")
        
        if job_status in ["PENDING"]:
            return JobStatus(phase="pending")
        elif job_status in ["RUNNING"]:
            return JobStatus(phase="running")
        
        # Retrieve logs if job has completed
        logs = b""
        try:
            with open(Path(self._sandbox) / job_name / "logs", "rb") as logs_file:
                logs = logs_file.read()
        except FileNotFoundError:
            self.logger.error(f"Failed retrieving stdout log for job {job_name}")
            return JobStatus(phase="failed")
        
        return JobStatus(phase="succeeded", logs_tarball=logs)

    async def delete_pod(self, job_name: str) -> None:
        """
        Delete the Slurm job by its name and remove the associated sandbox directory.
        """
        sandbox = Path(self._sandbox) / job_name
        
        self.logger.info(f"Attempting to delete Slurm job: {job_name}")
        
        try:
            # Get the job ID using squeue
            result = subprocess.run(
                ["squeue", "--name", job_name, "--noheader", "-o", "%A"], # WIP: here the absolute path to squeue should be taken from the build config
                capture_output=True, text=True, check=True
            )
            
            job_ids = result.stdout.strip().split()

            # job ids is a list of job ids that should contain only one element
            self.logger.info(f"Found job IDs: {job_ids}")        

            if job_ids:
                for job_id in job_ids:
                    self.logger.info(f"Canceling Slurm job {job_id} (name: {job_name})")
                    subprocess.run(["scancel", job_id], check=True)
            else:
                self.logger.warning(f"No active Slurm jobs found with name {job_name}")
        except subprocess.CalledProcessError as e:
            self.logger.error(f"Failed to query or cancel Slurm job {job_name}: {e.stderr}")
        
        # Cleanup
        try:
            shutil.rmtree(sandbox)
            self.logger.info(f"Successfully deleted sandbox for job {job_name}")
        except FileNotFoundError:
            self.logger.warning(f"Trying to delete job {job_name}, but no sandbox volume is found.")
        except OSError as e:
            self.logger.critical(f"Failed deleting sandbox for job {job_name}: {sandbox}")
            self.logger.critical(e, exc_info=True)

