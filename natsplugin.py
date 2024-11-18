import logging
from typing import Any, Dict, List, Literal
import os
import signal

from fastapi import FastAPI, HTTPException

from natsprovider import NatsGateway, interlink
from natsprovider import configuration as cfg
from pprint import pprint

# Initialize FastAPI app
app = FastAPI()

# Please Take my provider and handle the interLink REST layer for me
nats_provider = NatsGateway(
    nats_server=cfg.NATS_SERVER,
    nats_subject=cfg.NATS_SUBJECT,
    nats_timeout_seconds=cfg.NATS_TIMEOUT_SECONDS
)

log_format = '%(asctime)-22s %(name)-10s %(levelname)-8s %(message)-90s'
logging.basicConfig(
    format=log_format,
    level=logging.DEBUG if cfg.DEBUG else logging.INFO,
)
logging.debug("Enabled debug mode.")

@app.post("/create")
async def create_pod(pods: List[Dict[Literal['pod', 'container'], Any]]) -> interlink.CreateStruct:
    if len(pods) != 1:
        raise HTTPException(402, f"Can only treat one pod creation at once. {len(pods)} were requested.")

    pods = [
        interlink.Pod(
            pod=interlink.PodRequest(**(pod['pod'])),
            container=[interlink.Volume(**c) for c in pod['container']]
        )
        for pod in pods
    ]

    pod = pods[0].pod
    container = pods[0].container

    logging.info(f"Creating pod {pod.metadata.namespace}/{pod.metadata.name}")
    return interlink.CreateStruct(
        PodUID=pod.metadata.uid,
        PodJID=await nats_provider.create_job(pod, container)
    )

@app.post("/delete")
async def delete_pod(pod: Dict[Literal['pod', 'container'], Any]) -> str:
    pod = interlink.PodRequest(**pod)
    await nats_provider.delete_pod(pod)
    return "Pod deleted"

@app.get("/status")
async def get_pod_status(pods: List[Dict[str, Any]]) -> List[interlink.PodStatus]:
    logging.info(f"Requested status, number of pods: {len(pods)}")
    pprint (pods)
    pods = [interlink.PodRequest.from_dict(p) for p in pods]
    retrieved_states = [await nats_provider.get_pod_status(pod) for pod in pods]
    return [state for state in retrieved_states if state is not None]


@app.get("/getLogs")
async def get_pod_logs(req: interlink.LogRequest) -> str:
    return await nats_provider.get_pod_logs(req)


@app.post("/shutdown/{subject}")
async def shutdown(subject: str) -> str:
    logging.info("Shutting down")
    await nats_provider.shutdown(subject)
    os.kill(os.getpid(), signal.SIGTERM)
    return "Shutting down"

@app.get("/healthz")
async def healthz() -> bool:
    logging.debug("Health tested: ok.")
    return True
