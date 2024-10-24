from typing import Union, Collection
import asyncio
import logging
import tarfile

from fastapi import HTTPException
import interlink

from . import CondorConfiguration, CondorSubmit, CondorJobStatus
from ..apptainer_cmd_builder import from_kubernetes
from ..utils import  compute_pod_resource, get_readable_jobid, JobStatus

CondorConfiguration.initialize_htcondor()

from ..BaseNatsProvider import BaseNatsProvider

class CondorProvider(BaseNatsProvider):
    def __init__(self):
        BaseNatsProvider.__init__(self)
        self.condor = CondorConfiguration()

    async def create_pod(self, job_name: str, job_sh: str, pod: interlink.PodRequest) -> str:
        """
        Submit the job to condor CE
        """
        condor_options = CondorSubmit(
            job_name=job_name,
            transfer_output_files=['logs'],
            request_cpus=compute_pod_resource(pod, resource='cpu'),
            request_memory=compute_pod_resource(pod, resource='memory'),
        )
        await self.condor.submit(job_sh, condor_options)

        return job_name

    async def delete_pod(self, job_name: str) -> None:
        await self.condor.delete_by_name(job_name)

    async def get_pod_status_and_logs(self, job_name: str) -> JobStatus:
        status = await self.condor.status_by_name(job_name)
        if status == CondorJobStatus.held:
            return JobStatus(phase="pending")
        elif status == CondorJobStatus.idle:
            return JobStatus(phase="pending")
        elif status == CondorJobStatus.running:
            return JobStatus(phase="running")
        elif status == CondorJobStatus.completed:
            output_struct = await self.condor.retrieve_by_name(job_name, cleanup=False)
            return JobStatus(
                phase="succeeded",
                logs_tarball=output_struct['logs'],
            )

        return JobStatus(phase="unknown")


    async def get_pod_logs(self, log_request: interlink.LogRequest) -> str:
        self.logger.info(f"Log of pod {log_request.PodName}.{log_request.Namespace} [{log_request.PodUID}]")
        job_name = get_readable_jobid(log_request)
        status = await self.condor.status_by_name(job_name)

        if status in [CondorJobStatus.idle, CondorJobStatus.held]:
            return ""

        if status == CondorJobStatus.running:
            return "Unfortunately the log cannot retrieved for a running job... "

        if status != CondorJobStatus.completed:
            return f"Error. Cannot return log for job status '{status}'"

        output_struct = await self.condor.retrieve_by_name(job_name, cleanup=False)
        if 'logs' not in output_struct.keys():
            self.logger.error(f"Requested a log for job {job_name}, but log is not available in condor output_files.")
            return f"Error. Log was not stored or could not be retrieved."

        with tarfile.open(fileobj=output_struct['logs'], mode='r:*') as tar:
            for member in tar.getmembers():
                if member.isfile():
                    print (f"Pod has log for container {member.name}, requested {log_request.ContainerName}.log")
                    if member.name in [log_request.ContainerName + ".log", log_request.ContainerName + ".log.init"]:
                        full_log = tar.extractfile(member).read().decode('utf-8')

        if log_request.Opts.Tail is not None:
            return "\n".join(full_log.split('\n')[-log_request.Opts.Tail:])

        return full_log
