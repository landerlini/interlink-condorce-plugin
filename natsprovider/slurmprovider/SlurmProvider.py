import asyncio
import os
from datetime import datetime
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
    'TIMEOUT',
)

class SlurmProvider(BaseNatsProvider):
    def __init__(
            self,
            build_config: BuildConfig,
            **kwargs
    ):
        self._cached_squeue = dict()
        self._cached_squeue_time = None
        BaseNatsProvider.__init__(self, build_config=build_config, **kwargs)

    async def _retrieve_job_status(self, job_name: str) -> Union[str, None]:
        if (
            self._cached_squeue_time is not "processing" and (
                self._cached_squeue_time is None or
                (datetime.now() - self._cached_squeue_time).total_seconds() > 10 or
                job_name not in self._cached_squeue.keys()
            )
        ):
            # Cache miss
            self._cached_squeue_time = "processing"
            squeue_executable = "/usr/bin/squeue"
            slurm_config = self._build_config.slurm

            if slurm_config:
                squeue_executable = slurm_config.squeue_executable or squeue_executable

            try:
                username = os.environ.get("USER", os.environ.get("LOGNAME"))
                selectors = ['--user', username] if username is None else []

                # Get job status from Slurm
                proc = await asyncio.create_subprocess_exec(
                    squeue_executable, *selectors, "--noheader", "-o", "%j:%T",
                    stdout = asyncio.subprocess.PIPE,
                    stderr = asyncio.subprocess.PIPE,
                )
                stdout, stderr = await proc.communicate()
                stdout, stderr = str(stdout, 'utf-8'), str(stderr, 'utf-8')
                lines = stdout.split('\n')

                statuses = {}
                for line in lines:
                    if ':' not in line:
                        continue

                    job_name, slurm_status = line.split(':')
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
        scratch_area = Path(self.build_config.volumes.scratch_area) / job_name

        # Ensure directories exist
        self.logger.info(f"Creating directory {sandbox}")
        Path(sandbox).mkdir(parents=True, exist_ok=True)

        self.logger.info(f"Start creation of slurm script for job {job_name}")

        if self._build_config.slurm is None:
            self.logger.info("No slurm configuration specified in the build config. Using default values.")

        # Default Slurm output and error log paths
        sbatch_output_flag = f"#SBATCH --output={sandbox}/stdout.log"
        sbatch_error_flag = f"#SBATCH --error={sandbox}/stderr.log"

        # Generate Slurm sbatch flags dynamically
        scfg = self._build_config.slurm

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
                else:
                    self.logger.warning(f"Ignored {prop_name} with schema {prop_schema} ")
            elif 'arg' in prop_schema.keys() and 'type' not in prop_schema.keys():
                self.logger.warning(f"Property {prop_name} has no schema type {prop_schema}")

        job_sh_lines = job_sh.split('\n')
        slurm_script = '\n'.join([
            job_sh_lines[0],
            f'#SBATCH --job-name={job_name}',
            sbatch_output_flag,
            sbatch_error_flag,
            *sbatch_flags,
            '',
            f'export SANDBOX={sandbox}\n',
            scfg.header,
            *(job_sh_lines[1:]),
            scfg.footer,
        ])

        self.logger.info(f"Slurm script for job {job_name}:\n{sbatch_flags}")

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
        elif job_status in SLURM_PENDING_STATUSES:
            return JobStatus(phase="pending")
        elif job_status in SLURM_RUNNING_STATUSES:
            return JobStatus(phase="running")
        elif job_status in SLURM_FAILED_STATUSES:
            return JobStatus(phase="failed", reason=job_status)

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

