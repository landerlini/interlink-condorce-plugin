import time
import io
from pydantic import BaseModel, Field
import subprocess
import os
import textwrap
from typing import Optional, Literal, Dict,  Union, List
import re
import htcondor

import requests
import asyncio

from pydantic.json_schema import DEFAULT_REF_TEMPLATE

from condorprovider.utils import generate_uid

from enum import Enum
class JobStatus(Enum):
    unexpanded = 0
    idle = 1
    running = 2
    removed = 3
    completed = 4
    held = 5
    error = 6

    def __str__ (self):
        return Enum.__str__(self).split('.')[1]

CONDOR_ATTEMPTS = 3

class HTCondorException(IOError):
    pass

async def _shell(cmd: str):
    proc = await asyncio.create_subprocess_exec(
        "/bin/bash", '-c', cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode > 0:
        raise subprocess.CalledProcessError(returncode=proc.returncode, cmd="/bin/bash", stderr=str(stderr))

    return str(stdout)

class CondorSubmit(BaseModel):
    job_name: Optional[str] = Field(
        default=None,
        description="JobBatchName representing an external job_id, used for job lookup with *_by_name methods",
    )

    executable: Optional[str] = Field(
        default="/bin/bash",
        description="Path to the executable"
    )

    log: str = Field(
        default="output.log",
        description="Log file"
    )

    stdout: str = Field(
        default="stdout.txt",
        description="Standard output stream"
    )

    stderr: str = Field(
        default="stderr.txt",
        description="Standard error stream"
    )

    when_to_transfer_output: Literal["ON_EXIT", "ON_EXIT_OR_EVICT", "NEVER"] = Field(
        default="ON_EXIT",
        description="Define the policy for returning data file"
    )

    should_transfer_files: Literal["Yes", "No"] = Field(
        default="Yes",
        description="Enable transferring data files"
    )

    transfer_output_files: List[str] = Field(
        default=[],
        description="List of files to be downloaded at the end of the job (basename, without path)"
    )

    def submit_file(self, command: str, condor_dir: str):
        return textwrap.dedent(f"""
            scitokens_file = /dev/null
            +Owner = undefined
            +JobBatchName               = "{self.job_name}"
            
            executable                  = {self.executable}
            arguments                   = {os.path.basename(command)}
            log                         = {os.path.join(condor_dir, self.log)}
            error                       = {os.path.join(condor_dir, self.stderr)}
            output                      = {os.path.join(condor_dir, self.stdout)}
            
            when_to_transfer_output     = {self.when_to_transfer_output}
            should_transfer_files       = {self.should_transfer_files}
            transfer_input_files        = {os.path.abspath(command)}
            transfer_output_files       = {','.join(self.transfer_output_files)}
            
            queue
        """)


class CondorConfiguration(BaseModel):
    pool: str = Field(
        default=os.environ.get("CONDOR_POOL", "ce01t-htc.cr.cnaf.infn.it:9619"),
        description="Schedd central manager (e.g. ce01t-htc.cr.cnaf.infn.it:9619)",
    )

    scheduler_name: str = Field(
        default=os.environ.get("CONDOR_SCHEDULER_NAME", "ce01t-htc.cr.cnaf.infn.it"),
        description="Name of the scheduler (e.g. ce01t-htc.cr.cnaf.infn.it)",
    )

    verbose: bool = Field(
        default=False,
        description="If true, debug messages are printed.",
    )

    @staticmethod
    def initialize_htcondor():
        os.environ['CONDOR_CONFIG'] = os.environ.get('CONDOR_CONFIG', '/dev/null')
        os.environ['_condor_SEC_CLIENT_AUTHENTICATION_METHODS'] = os.environ.get(
            '_condor_SEC_CLIENT_AUTHENTICATION_METHODS', 'SCITOKENS'
        )

        if 'BEARER_TOKEN' not in os.environ.keys():
            response = requests.post(
                os.environ["IAM_ISSUER"] + '/token',
                data={'grant_type': 'refresh_token', 'refresh_token': os.environ["REFRESH_TOKEN"]},
                auth=(os.environ.get('IAM_CLIENT_ID'), os.environ.get('IAM_CLIENT_SECRET'))
            )
            if response.status_code / 100 != 2:
                print(response.text)
            response.raise_for_status()
            os.environ['BEARER_TOKEN'] = response.json().get("access_token")

        htcondor.param['SEC_TOKEN'] = os.environ['BEARER_TOKEN']
        htcondor.param['SEC_CLIENT_AUTHENTICATION_METHODS'] = 'SCITOKENS'

        htcondor.reload_config()

    async def get_schedd(self):
        if not hasattr(self, '_schedd'):
            collector = htcondor.Collector(self.pool)
            for attempt in range(CONDOR_ATTEMPTS+1):
                try:
                    schedd_ad = collector.locate(htcondor.DaemonTypes.Schedd, self.scheduler_name)
                    setattr(self, '_schedd', htcondor.Schedd(schedd_ad))
                except htcondor.HTCondorIOError as e:
                    if attempt == CONDOR_ATTEMPTS:
                        raise e
                    await asyncio.sleep(0.1)
                    continue

        return self._schedd

    async def query(self, job_id: Optional[int] = None):
        for attempt in range(CONDOR_ATTEMPTS+1):
            try:
                schedd = await self.get_schedd()
                ret = {int(j['ClusterId']): j for j in schedd.query()}
                if job_id is None:
                    return ret
                return ret[job_id]

            except htcondor.HTCondorIOError as e:
                if attempt == CONDOR_ATTEMPTS:
                    raise e
                time.sleep(0.1)
                continue

    async def query_by_name(self, job_name: str):
        for attempt in range(CONDOR_ATTEMPTS+1):
            try:
                schedd = await self.get_schedd()
                ret = schedd.query(constraint=f'JobBatchName == "{job_name}"')
                if len(ret) == 0:
                    raise HTCondorException(f"Job {job_name} not found.")
                if len(ret) > 1:
                    raise HTCondorException(
                        f"Ambiguous job name {job_name} selecting jobs {', '.join([j['ClusterId'] for j in ret])}."
                    )

                return ret[0]

            except htcondor.HTCondorIOError as e:
                if attempt == CONDOR_ATTEMPTS:
                    raise e
                time.sleep(0.1)
                continue

    async def submit(self, job: str, submit: Optional[CondorSubmit] = None):
        htcondor.param['COLLECTOR_HOST'] = self.pool
        if submit is None:
            submit = CondorSubmit()

        uid = generate_uid()
        submit.job_name = submit.job_name if submit.job_name is not None else uid
        condor_dir = f"/tmp/.condor.{uid}"
        await _shell(f"mkdir -p {condor_dir}; chmod a+w {condor_dir}")
        submit_file_path = os.path.join(condor_dir, "condor.sub")
        script_file_path = os.path.join(condor_dir, "command.sh")

        with open(submit_file_path, "w") as submit_file:
            print (submit.submit_file(script_file_path, condor_dir=condor_dir), file=submit_file)

        with open(script_file_path, "w") as script_file:
            print (job, file=script_file)

        ret = await _shell(f"cd {condor_dir}\n" + ' '.join([
            "condor_submit",
            f"-pool {self.pool}",
            f"-name {self.scheduler_name}",
            f"-spool {submit_file_path}",
            ])
        )

        job_ids = [
            int(job_id)
            for job_id in ', '.join(re.findall(r"submitted to cluster ([\d, ]+).", ret)).split(", ")
        ]

        if len(job_ids) > 0:
            return job_ids[0]

        print (ret)
        raise HTCondorException("Failed to submit job to cluster")

    async def status(self, job_id: int):
        jobs = await self.query()
        return JobStatus(jobs[job_id]['JobStatus'])

    async def status_by_name(self, job_name: str):
        job = await self.query_by_name(job_name)
        return JobStatus(job['JobStatus'])

    async def retrieve_by_name(self, job_name: str, cleanup: bool = True):
        job = await self.query_by_name(job_name)
        files = await self._retrieve_job_output(job, cleanup=cleanup)
        if cleanup:
            await self.delete_by_name(job_name)
        return files

    async def retrieve(self, job_id: int, cleanup: bool = True):
        job = await self.query(job_id)
        files = await self._retrieve_job_output(job, cleanup=cleanup)
        if cleanup:
            await self.delete(job_id)
        return files

    async def _retrieve_job_output(self, job, cleanup: bool):
        for attempt in range(CONDOR_ATTEMPTS+1):
            try:
                await _shell(f"condor_transfer_data -pool {self.pool} -name {self.scheduler_name} {job['ClusterId']}")
            except subprocess.CalledProcessError as e:
                if attempt == CONDOR_ATTEMPTS:
                    raise e
                else:
                    await asyncio.sleep(1)
                    continue
            else:
                break

        files = {f.split("=")[0]: open(f.split("=")[1]).read() for f in job['SUBMIT_TransferOutputRemaps'].split(";")}
        if self.verbose:
            for lfn, content in files.items():
                print(f"""=== {lfn} ===\n{content}\n=============""")

        if 'TransferOutput' in job.keys():
            job_dir = job['SUBMIT_Iwd']
            files.update({
                k: io.BytesIO(open(os.path.join(job_dir, k), 'rb').read())
                for k in job['TransferOutput'].split(',')
            })

        if cleanup:
            await _shell(f"rm -rf {os.path.dirname(job['JobSubmitFile'])}")

        return files

    async def delete(self, job_id: int):
        for attempt in range(CONDOR_ATTEMPTS+1):
            try:
                return await _shell(f"condor_rm -pool {self.pool} -name {self.scheduler_name} {job_id:d}")
            except subprocess.CalledProcessError as e:
                if attempt == CONDOR_ATTEMPTS:
                    raise e
                else:
                    await asyncio.sleep(0.2)
                    continue


    async def delete_by_name(self, job_name: str):
        return await _shell(
            ' '.join([
                "condor_rm",
                f"-pool {self.pool}",
                f"-name {self.scheduler_name}",
                f"""-constraint 'JobBatchName == "{job_name}"'""",
            ])
        )


CondorConfiguration.initialize_htcondor()
