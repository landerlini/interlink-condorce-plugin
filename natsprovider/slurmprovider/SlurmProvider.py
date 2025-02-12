from textwrap import dedent
from pathlib import Path
import shutil
import subprocess
import re

from .. import interlink
from ..utils import  compute_pod_resource, JobStatus, Resources
from ..BaseNatsProvider import BaseNatsProvider
from ..apptainer_cmd_builder import BuildConfig


class SlurmProvider(BaseNatsProvider):
    def __init__(
            self,
            build_config: BuildConfig,
            **kwargs
    ):
        BaseNatsProvider.__init__(self, build_config=build_config, **kwargs)

    async def create_pod(self, job_name: str, job_sh: str, pod: interlink.PodRequest) -> str:
        """
        Submit the job to Slurm
        """        
    
        sandbox = Path(self.build_config.slurm.sandbox) / job_name
        scratch_area = Path(self.build_config.volumes.scratch_area) / job_name
        job_script_path = sandbox / "job_script.sh"  # Define the job script path

        # Write job_sh to a script file
        with open(job_script_path, "w") as f:
            f.write(job_sh)

        # Make sure the script is executable
        job_script_path.chmod(0o755)

        self.logger.info(f"Start creation of slurm script for job {job_name}")

        if self._build_config.slurm is None:
            self.logger.info("No slurm configuration specified in the build config. Using default values.")

        # Default Slurm output and error log paths
        sbatch_output_flag = f"#SBATCH --output={sandbox}/stdout.log"
        sbatch_error_flag = f"#SBATCH --error={sandbox}/stderr.log"

        # Generate Slurm sbatch flags dynamically
        slurm_config = self._build_config.slurm

        # sbatch flags
        sbatch_flags = []
        for prop_name, prop_schema in slurm_config.model_json_schema()['properties'].items():
            if 'arg' in prop_schema.keys():
                if prop_schema['type'] == 'boolean' and getattr(slurm_config, prop_name):
                    sbatch_flags.append("#SBATCH " + prop_schema['arg'])
                elif prop_schema['type'] in ('integer', 'string') and getattr(slurm_config, prop_name) is not None:
                    sbatch_flags.append("#SBATCH " + prop_schema['arg'] % getattr(slurm_config, prop_name))
                elif prop_schema['type'] == 'array' and getattr(slurm_config, prop_name) is not None:
                    for value in getattr(slurm_config, prop_name):
                        sbatch_flags.append("#SBATCH " + prop_schema['arg'] % value)

        # Create the Slurm script
        slurm_script = "#!/bin/bash\n" + dedent("""
            #SBATCH --job-name=%(job_name)s
            %(flags)s
            
            export SANDBOX=%(sandbox)s

            %(bash_executable)s %(job_script_path)s 
            """
        )%dict(
            bash_executable=slurm_config.bash_executable,
            job_name=job_name,
            flags='\n'.join([sbatch_output_flag, sbatch_error_flag] + sbatch_flags),
            sandbox=sandbox,
            job_script_path=job_script_path
        )

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
                [f"{slurm_config.sbatch_executable}", str(slurm_script_path)],
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
        
        squeue_executable = "/usr/bin/squeue"
        slurm_config = self._build_config.slurm

        if slurm_config:
            squeue_executable = slurm_config.squeue_executable or squeue_executable

        try:
            # Get job status from Slurm
            result = subprocess.run(
                [squeue_executable, "--name", job_name, "--noheader", "-o", "%T"],
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
            with open(Path(self.build_config.slurm.sandbox) / job_name / "logs", "rb") as logs_file:
                logs = logs_file.read()
        except FileNotFoundError:
            self.logger.error(f"Failed retrieving stdout log for job {job_name}")
            return JobStatus(phase="failed")
        
        return JobStatus(phase="succeeded", logs_tarball=logs)

    async def delete_pod(self, job_name: str) -> None:
        """
        Delete the Slurm job by its name and remove the associated sandbox directory.
        """
        sandbox = Path(self.build_config.slurm.sandbox) / job_name
        
        self.logger.info(f"Attempting to delete Slurm job: {job_name}")
        
        try:
            # Get the job ID using squeue
            result = subprocess.run(
                # WIP: here the absolute path to squeue should be taken from the build config
                ["squeue", "--name", job_name, "--noheader", "-o", "%A"],
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
                self.logger.info(f"No active Slurm jobs found with name {job_name}")
        except subprocess.CalledProcessError as e:
            self.logger.error(f"Failed to query or cancel Slurm job {job_name}: {e.stderr}")
        
        # Cleanup the sandbox directory in the host
        try:
            shutil.rmtree(sandbox)
            self.logger.info(f"Successfully deleted sandbox for job {job_name}")
        except FileNotFoundError:
            self.logger.warning(f"Trying to delete job {job_name}, but no sandbox volume is found.")
        except OSError as e:
            self.logger.critical(f"Failed deleting sandbox for job {job_name}: {sandbox}")
            self.logger.critical(e, exc_info=True)

        # Cleanup the scratch area in the host
        scratch_area = Path(self.build_config.volumes.scratch_area) / job_name
        try:
            shutil.rmtree(scratch_area)
            self.logger.info(f"Successfully deleted scratch area for job {job_name}")
        except FileNotFoundError:
            self.logger.warning(f"Trying to delete job {job_name}, but no scratch area volume is found.")
        except OSError as e:
            self.logger.critical(f"Failed deleting scratch area for job {job_name}: {scratch_area}")
            self.logger.critical(e, exc_info=True)

