import logging
from typing import List
import os
import signal

from fastapi import FastAPI, HTTPException
import interlink

from condorprovider import CondorProvider
from condorprovider import configuration as cfg
from condorprovider.CondorConfiguration import HTCondorException

# Initialize FastAPI app
app = FastAPI()

# Please Take my provider and handle the interLink REST layer for me
condor_provider = CondorProvider()

log_format = '%(asctime)-22s %(name)-10s %(levelname)-8s %(message)-90s'
logging.basicConfig(
    format=log_format,
    level=logging.DEBUG if cfg.DEBUG else logging.INFO,
)
logging.debug("Enabled debug mode.")

@app.post("/create")
async def create_pod(pods: List[interlink.Pod]) -> interlink.CreateStruct:
    if len(pods) != 1:
        raise HTTPException(402, f"Can only treat one pod creation at once. {len(pods)} were requested.")

    pod = pods[0].pod
    container = pods[0].container

    logging.info(f"Creating pod {pod.metadata.namespace}/{pod.metadata.name}")
    return interlink.CreateStruct(
        PodUID=pod.metadata.uid,
        PodJID=await condor_provider.create_job(pod, container)
    )

@app.post("/delete")
async def delete_pod(pod: interlink.PodRequest) -> str:
    await condor_provider.delete_pod(pod)
    return "Pod deleted"

@app.get("/status")
async def get_pod_status(pods: List[interlink.PodRequest]) -> List[interlink.PodStatus]:
    logging.info(f"Requested status, number of pods: {len(pods)}")
    try:
        retrieved_states = [await condor_provider.get_pod_status(pod) for pod in pods]
    except HTCondorException as e:
        logging.error(str(e))
        raise HTTPException(404, str(e))

    return [state for state in retrieved_states if state is not None]


@app.get("/getLogs")
async def get_pod_logs(req: interlink.LogRequest) -> str:
    return await condor_provider.get_pod_logs(req)


@app.post("/shutdown")
async def shutdown() -> str:
    logging.info("Shutting down")
    os.kill(os.getpid(), signal.SIGTERM)
    return "Shutting down"

@app.get("/healthz")
async def healtz() -> bool:
    logging.debug("Health tested: ok.")
    return True
