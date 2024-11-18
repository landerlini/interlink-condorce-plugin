# This file has been imported and extended from
# https://github.com/intertwin-eu/interLink.git@0.3.0#egg=interlink&subdirectory=example

import datetime
import json
import kubernetes.client as k8s
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_serializer, field_validator
from kubernetes.client.api_client import ApiClient as K8sApiClient

def deserialize_kubernetes(data, klass):
    """
    Boilerplate to deserialize a dictionary into a Kubernetes object. Not very efficient.
    """
    class JsonWrapper:
        def __init__(self, json_data):
            self.data = json.dumps(json_data)

    return K8sApiClient().deserialize(JsonWrapper(data), klass)


class Metadata(BaseModel):
    name: Optional[str] = None
    namespace: Optional[str] = None
    uid: Optional[str] = None
    annotations: Optional[Dict[str, str]] = Field({})
    labels: Optional[Dict[str, str]] = Field({})
    generateName: Optional[str] = None

class PodRequest(BaseModel, arbitrary_types_allowed=True):
    metadata: k8s.V1ObjectMeta
    spec: k8s.V1PodSpec

    @field_validator('metadata')
    @classmethod
    def deserialize_metadata(cls, metadata: Dict[str, Any]) -> k8s.V1ObjectMeta:
        return deserialize_kubernetes(metadata, "V1ObjectMeta")

    @field_validator('spec')
    @classmethod
    def deserialize_spec(cls, spec: Dict[str, Any]) -> k8s.V1PodSpec:
        return deserialize_kubernetes(spec, "V1PodSpec")

    @field_serializer('metadata')
    def serialize_metadata(self, metadata: k8s.V1ObjectMeta, _info):
        return metadata.to_dict()

    @field_serializer('spec')
    def serialize_spec(self, spec: k8s.V1PodSpec, _info):
        return spec.to_dict()


class ConfigMap(BaseModel):
    metadata: Metadata
    data: Optional[dict]
    binaryData: Optional[dict] = None
    type: Optional[str] = None
    immutable: Optional[bool] = None


class Secret(BaseModel):
    metadata: Metadata
    data: Optional[dict] = None
    stringData: Optional[dict] = None
    type: Optional[str] = None
    immutable: Optional[bool] = None


class Volume(BaseModel):
    name: str
    configMaps: Optional[List[ConfigMap]] = None
    secrets: Optional[List[Secret]] = None
    emptyDirs: Optional[List[str]] = None


class Pod(BaseModel):
    pod: PodRequest
    container: List[Volume]


class StateTerminated(BaseModel):
    exitCode: int
    reason: Optional[str] = None


class StateRunning(BaseModel):
    startedAt: Optional[str] = None


class StateWaiting(BaseModel):
    message: Optional[str] = None
    reason: Optional[str] = None


class ContainerStates(BaseModel):
    terminated: Optional[StateTerminated] = None
    running: Optional[StateRunning] = None
    waiting: Optional[StateWaiting] = None


class ContainerStatus(BaseModel):
    name: str
    state: ContainerStates


class PodStatus(BaseModel):
    name: str
    UID: str
    namespace: str
    containers: List[ContainerStatus]


class LogOpts(BaseModel):
    Tail: Optional[int] = None
    LimitBytes: Optional[int] = None
    Timestamps: Optional[bool] = None
    Previous: Optional[bool] = None
    SinceSeconds: Optional[int] = None
    SinceTime: Optional[datetime.datetime] = None


class LogRequest(BaseModel):
    Namespace: str
    PodUID: str
    PodName: str
    ContainerName: str
    Opts: LogOpts

class CreateStruct(BaseModel):
    PodUID: str
    PodJID: str
