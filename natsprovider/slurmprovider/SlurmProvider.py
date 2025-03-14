import asyncio
import os
from datetime import datetime, timedelta
from copy import copy
from textwrap import dedent
from pathlib import Path
import shutil
import subprocess
import re
from enum import IntEnum
from typing import Union

from .. import interlink
from ..utils import  compute_pod_resource, JobStatus, Resources
from ..BaseNatsProvider import BaseNatsProvider
from ..apptainer_cmd_builder import BuildConfig

SLURM_PENDING_STATUSES = (
    'CONFIGURING',
    'PENDING',
    'RESV_DEL_HOLD',
    'REQUEUE_FED',
    'REQUEUE_HOLD',
    'RESIZING',
    'STAGE_OUT',
)

SLURM_RUNNING_STATUSES = (
    'COMPLETING',
    'RUNNING',
    'SIGNALING',
    'MIGRATING',
    'SUSPENDED',
    'STOPPED',
    'REVOKED',
)

SLURM_FAILED_STATUSES = (
    'BOOT_FAILED',
    'CANCELLED',
    'DEADLINE',
    'COMPLETING',
    'DEADLINE',
    'OUT_OF_MEMORY',
    'SPECIAL_EXIT',
    'STAGE_OUT',
    'CANCELLED',
    'TIMEOUT',
)

SLURM_COMPLETED_STATUSES = (
    'COMPLETED',
)

class SlurmProvider(BaseNatsProvider):
    def __init__(
            self,
            build_config: BuildConfig,
            **kwargs
    ):
        self._cached_squeue = dict()
        self._cached_squeue_time: Union[datetime, str, None] = None
        BaseNatsProvider.__init__(self, build_config=build_config, **kwargs)

    async def _retrieve_job_status(self, job_name: str) -> Union[str, None]:
        # Try to wait for previous sacct request to reply for up to 5 seconds
        for _ in range(50):
            if self._cached_squeue_time == "processing":
                await asyncio.sleep(0.1)
            else:
                break

        if (
            self._cached_squeue_time != "processing" and (
                self._cached_squeue_time is None or
                (datetime.now() - self._cached_squeue_time).total_seconds() > 10 or
                job_name not in self._cached_squeue.keys()
            )
        ):
            # Cache miss
            last_check_timestamp = copy(self._cached_squeue_time)
            self._cached_squeue_time = "processing"
            sacct_executable = "/usr/bin/sacct"
            slurm_config = self._build_config.slurm

            if slurm_config:
                sacct_executable = slurm_config.sacct_executable or sacct_executable

            try:
                username = os.environ.get("USER", os.environ.get("LOGNAME"))
                # Get job status from Slurm for completed jobs
                selectors = ['--user', username] if username is not None else []
                if last_check_timestamp is not None:
                    endtime_threshold = last_check_timestamp - timedelta(minutes=10)
                    selectors += ['--starttime', endtime_threshold.strftime('%Y-%m-%dT%H:%M:%S')]
                else:
                    selectors += ['--starttime', "now-1day"]

                # sacct --starttime now-3hour --user $USER --noheader --format=JobName,State --parsable2
                sacct_command = [
                    sacct_executable, *selectors, "--noheader", '--format=JobName,State', '--parsable2',
                ]

                proc = await asyncio.create_subprocess_exec(
                    *sacct_command,
                    stdout = asyncio.subprocess.PIPE,
                    stderr = asyncio.subprocess.PIPE,
                )

                sacct_stdout, sacct_stderr = await proc.communicate()
                sacct_stdout, sacct_stderr = str(sacct_stdout, 'utf-8'), str(sacct_stderr, 'utf-8')
                if len(sacct_stderr.replace(" ", "").replace("\n", "")):
                    self.logger.error(sacct_stderr)

                lines = sacct_stdout.split('\n')

                statuses = {}
                for line in lines:
                    if '|' in line:
                        job_name, slurm_status = line.split('|')
                        self._cached_squeue[job_name] = slurm_status
                        statuses[slurm_status] = statuses.get(slurm_status, 0) + 1

                self.logger.info(
                    f"Retrieved {len(lines)} jobs: {', '.join([f'{n} {k}' for k, n in statuses.items()])}"
                )

            except subprocess.CalledProcessError as e:
                self.logger.critical(f"Failed to query Slurm for job {job_name}: {e.stderr}")
                return None

            finally:
                self._cached_squeue_time = datetime.now()

        return self._cached_squeue.get(job_name)

    async def create_pod(self, job_name: str, job_sh: str, pod: interlink.PodRequest) -> str:
        """
        Submit the job to Slurm
        """        
    
        sandbox = Path(self.build_config.slurm.sandbox) / job_name
        apptainer_cachedir = Path(self.build_config.volumes.apptainer_cachedir)
        scratch_area = Path(self.build_config.volumes.scratch_area) / job_name
        job_script_path = sandbox / "job_script.sh"  # Define the job script path

        # Ensure directories exist
        for dirname in (apptainer_cachedir, scratch_area, sandbox):
            self.logger.info(f"Creating directory {dirname}")
            Path(dirname).mkdir(parents=True, exist_ok=True)
            

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

        scfg = self._build_config.slurm

        selected_flavor = None
        if hasattr(scfg, 'flavors') and scfg.flavors:
            required_gpu = compute_pod_resource(pod, 'nvidia.com/gpu')
            self.logger.info(f"Required GPU: {required_gpu}")
            for flavor in scfg.flavors:
                flavor_gpu_limit = flavor.max_resources.get('nvidia.com/gpu', 0)
                if flavor_gpu_limit >= required_gpu:
                    selected_flavor = flavor
                    self.logger.info(f"Selected flavor: partition={flavor.partition}, account={flavor.account}")
                    break

        if selected_flavor:
            scfg.account = selected_flavor.account
            scfg.partition = selected_flavor.partition
            scfg.qos = selected_flavor.qos
        else:
            self.logger.info("No suitable flavor found. Using default slurm configuration.")

        keywords = dict(sandbox=sandbox, job_name=job_name)

        # sbatch flags
        sbatch_flags = []
        for prop_name, prop_schema in scfg.model_json_schema()['properties'].items():
            if 'arg' in prop_schema.keys():
                if BuildConfig.check_type(scfg, prop_name, ['boolean']) and getattr(scfg, prop_name):
                    sbatch_flags.append("#SBATCH " + prop_schema['arg'])
                elif BuildConfig.check_type(scfg, prop_name, ['integer']) and getattr(scfg, prop_name) is not None:
                    sbatch_flags.append(
                        "#SBATCH " + prop_schema['arg'] % getattr(scfg, prop_name)
                    )
                elif BuildConfig.check_type(scfg, prop_name, ['string']) and getattr(scfg, prop_name) is not None:
                    sbatch_flags.append(
                        "#SBATCH " + prop_schema['arg'] % (getattr(scfg, prop_name) % keywords)
                    )
                elif BuildConfig.check_type(scfg, prop_name, ['array']) and getattr(scfg, prop_name) is not None:
                    for value in getattr(scfg, prop_name):
                        sbatch_flags.append("#SBATCH " + prop_schema['arg'] % (value % keywords) )

        if selected_flavor:
            for prop_name, prop_schema in selected_flavor.model_json_schema()['properties'].items():
                if not hasattr(scfg, prop_name):
                    self.logger.warning(f"Flavor property {prop_name} not found in slurm configuration. Skipping.")
                    continue
                if 'arg' in prop_schema and 'type' in prop_schema:
                    value = getattr(selected_flavor, prop_name)
                    if value is not None:
                        if prop_schema['type'] == 'boolean' and value:
                            flag = "#SBATCH " + prop_schema['arg']
                        elif prop_schema['type'] == 'integer':
                            flag = "#SBATCH " + prop_schema['arg'] % value
                        elif prop_schema['type'] == 'string':
                            formatted_value = value % keywords if '%' in value else value
                            flag = "#SBATCH " + prop_schema['arg'] % formatted_value
                        elif prop_schema['type'] == 'array':
                            for v in value:
                                formatted_value = v % keywords if '%' in v else v
                                flag = "#SBATCH " + prop_schema['arg'] % formatted_value
                                if flag not in sbatch_flags:
                                    sbatch_flags.append(flag)
                            continue  # Skip the rest of the loop for arrays.
                        if flag not in sbatch_flags:
                            sbatch_flags.append(flag)


        # Create the Slurm script
        slurm_script = "#!/bin/bash\n" + dedent("""
            #SBATCH --job-name=%(job_name)s
            %(flags)s
            
            export SANDBOX=%(sandbox)s
            
            %(header)s

            %(bash_executable)s %(job_script_path)s 
            
            %(footer)s
            """
        )%dict(
            bash_executable=scfg.bash_executable,
            job_name=job_name,
            flags='\n'.join(sbatch_flags),
            sandbox=sandbox,
            job_script_path=job_script_path,
            header=scfg.header,
            footer=scfg.footer,
        )

        # log the slurm script
        self.logger.info(f"Slurm script for job {job_name}:\n{slurm_script}")

        # Write the Slurm script
        slurm_script_path = sandbox / "job.sh"
        with open(slurm_script_path, "w") as f:
            f.write(slurm_script)

        # Submit the job
        self.logger.info(f"Submitting job {job_name} to Slurm")

        # Submit the job using sbatch
        try:
            proc = await asyncio.create_subprocess_exec(
                scfg.sbatch_executable, str(slurm_script_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await proc.communicate()
            stdout, stderr = str(stdout, 'utf-8'), str(stderr, 'utf-8')

            # Parse job ID from sbatch output
            match = re.search(r"Submitted batch job (\d+)", stdout)
            if match:
                job_id = match.group(1)
                self.logger.info(f"Job {job_name} submitted with Job ID: {job_id}")
            else:
                self.logger.error(f"Failed to extract job ID from sbatch output:\n{stdout}\n{stderr}")
                job_id = None

        except subprocess.CalledProcessError as e:
            self.logger.error(f"Failed to submit job {job_name}: {e.stderr}")
            job_id = None

        return job_id

    async def get_pod_status_and_logs(self, job_name: str) -> JobStatus:
        """
        Get the Slurm job status and retrieve logs.
        """

        job_status = await self._retrieve_job_status(job_name)

        if job_status is None:
            return JobStatus(phase="unknown")
        elif any([s in job_status for s in SLURM_FAILED_STATUSES]):
            return JobStatus(phase="failed", reason=job_status)
        elif any([s in job_status for s in SLURM_PENDING_STATUSES]):
            return JobStatus(phase="pending")
        elif any([s in job_status for s in SLURM_RUNNING_STATUSES]):
            return JobStatus(phase="running")
        elif any([s in job_status for s in SLURM_COMPLETED_STATUSES]):
            # Retrieve logs if job has completed
            logs = b""
            remaining_attempts = 5
            while True:
                try:
                    with open(Path(self.build_config.slurm.sandbox) / job_name / "logs", "rb") as logs_file:
                        log_data = logs_file.read()
                        if len(log_data) > 0:
                            return JobStatus(phase="succeeded", logs_tarball=log_data)
                except (FileNotFoundError, IOError) as e:
                    if remaining_attempts > 0:
                        remaining_attempts -= 1
                        await asyncio.sleep(1)
                    else:
                        self.logger.error(f"Failed retrieving stdout log for job {job_name}")
                        return JobStatus(phase="failed", reason=str(e))


        self.logger.critical(f"Unhandled slurm status {job_status}")
        return JobStatus(phase="unknown", reason=job_status)

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

