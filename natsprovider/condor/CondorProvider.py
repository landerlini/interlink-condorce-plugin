
from . import CondorConfiguration, CondorSubmit, CondorJobStatus
from .. import interlink
from ..utils import  compute_pod_resource, JobStatus
from ..BaseNatsProvider import BaseNatsProvider
from ..apptainer_cmd_builder import BuildConfig

CondorConfiguration.initialize_htcondor()

class CondorProvider(BaseNatsProvider):
    def __init__(self, nats_server: str, nats_queue: str, build_config: BuildConfig, interactive_mode: bool):
        BaseNatsProvider.__init__(
            self,
            nats_server=nats_server,
            nats_queue=nats_queue,
            interactive_mode=interactive_mode,
            build_config=build_config,
        )
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
