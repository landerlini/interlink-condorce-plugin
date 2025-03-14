import random
from datetime import datetime
import math
import os.path
import string
import textwrap
from base64 import b64encode
from contextlib import contextmanager
from typing import Any, Dict, Literal, Union, Optional
import zlib
from io import BytesIO
import pickle

from pydantic import BaseModel, Field
from kubernetes.utils.quantity import parse_quantity
from fastapi import HTTPException
import requests

from . import interlink


class NatsResponse(BaseModel, extra="forbid"):
    """
    Encode a NATS response ready to become an HTTP response, with its status_code and body
    """
    status_code: int = Field(description="HTTP Status code representing the outcome of the response")
    data: Any = Field(default=b'', description="Body of the response")
    headers: Dict[str, str] = Field(default={}, description="Headers of the reponse")

    @classmethod
    def from_nats(cls, nats_response):
        payload = zlib.decompress(nats_response.data)
        return cls(**pickle.loads(payload))

    def to_nats(self):
        """
        Dump and compress the status code and the data
        """
        payload = BytesIO()
        pickle.dump(self.model_dump(), payload)
        payload.seek(0)
        return zlib.compress(payload.read())

    def raise_for_status(self):
        """
        If status code is not 2xx it raises an HTTPException for FastAPI to return appropriate error code to the client
        """
        if self.status_code < 200 or self.status_code >= 300:
            raise HTTPException(self.status_code, str(self.data), headers=self.headers)

    @property
    def text(self) -> str:
        return str(self.data)


class JobStatus(BaseModel, extra="forbid"):
    phase: Literal["pending", "running", "succeeded", "failed", "unknown"] = Field(
        default="unknown",
        description="Phase of the pod. Lowercase.",
    )
    logs_tarball: bytes  = Field(
        default=b'',
        description="Content of the tarball generated by job_sh encoding ret-code and logs of containers. Can be empty."
    )
    reason: Optional[str] = Field(
        default=None,
        description="An additional field detailing a sub-status, used for example to motivate pending."
    )


class Resources(BaseModel, extra="forbid"):
    cpu: Optional[str] = None
    memory: Optional[str] = None
    gpus: Optional[int] = None
    pods: Optional[int] = None

    def to_kubernetes(self):
        full_dict = {
            'cpu': self.cpu,
            'memory': self.memory,
            'pods': self.pods,
            'nvidia.com/gpu': self.gpus,
        }

        return {k: v for k, v in full_dict.items()}

    @classmethod
    def from_kubernetes(cls, resources: Dict[Literal['cpu', 'memory', 'pods', 'nvidia.com/gpu'], Union[str, int]]):
        return cls(
            cpu=resources.get('cpu'),
            memory=resources.get('memory'),
            pods=resources.get('pods'),
            gpus=resources.get('nvidia.com/gpu'),
        )


def get_readable_jobid(pod: Union[interlink.PodRequest, interlink.LogRequest]):
    """
    Return a readable unique id used to name the pod from either the pod or the log requests.
    """
    if isinstance(pod, interlink.PodRequest):
        name = pod.metadata['name']
        namespace = pod.metadata['namespace']
        uid = pod.metadata['uid']
    elif isinstance(pod, interlink.LogRequest):
        name = pod.PodName
        namespace = pod.Namespace
        uid = pod.PodUID
    else:
        raise HTTPException(500, f"Unexpected pod or log request of type {type(pod)}")

    short_name = '-'.join((namespace, name))[:20]
    return '-'.join((short_name, f"{make_uid_numeric(uid):x}"))

def generate_uid(length: int = 16, allow_uppercase: bool = False) -> str:
    """
    Generates a random string of length *length* including lowercase letters, digits and (possibly) uppercase letters.
    :param length: Length of the generated string
    :param allow_uppercase: If True, uppercase letters will be included.
    :return: randomly generated string
    """
    letters = string.ascii_letters if allow_uppercase else string.ascii_lowercase
    first_char = random.choice(letters)
    other_chars = letters + string.digits

    return first_char + ''.join(random.choice(other_chars) for _ in range(length-1))


def embed_binary_file(path: str, file_content: bytes, executable: bool = False, token: str = "EOF") -> str:
    """
    Embed a binary file in a bash script using the base64 encoding and the syntax cat<<EOF > path

    The binary file is base64-encoded and dumped in the main script as an ASCII, temporary file.
    The code to decode it is embedded in the script to generate the desired file at evaluation time.

    :param path: path of the file
    :param file_content: binary content of the file
    :param executable: if True, marks the file as executable (chmod +x)
    :param token: token identifying the end of the stream (useful for nesting)

    :return: bash code embedding an ascii file in a script
    """
    chunk_len = 80
    encoded_data = str(b64encode(file_content), "ascii")
    encoded_data = "\n".join([encoded_data[i: i+chunk_len] for i in range(0, len(encoded_data), chunk_len)])

    ret = [
        f"mkdir -p {os.path.dirname(path)}",
        f"cat <<{token} | base64 --decode > {path}",
        textwrap.dedent(encoded_data),
        token+"\n",
        ]

    if executable:
        ret.append(f"chmod +x {path}")

    return '\n'+'\n'.join(ret)

def embed_ascii_file(path: str, file_content: str, executable: bool = False, token: str = "EOF") -> str:
    """
    Embed a text file in a bash script using the base64 encoding and the syntax cat<<EOF > path

    The text is base64-encoded and dumped in the main script as an ASCII, temporary file.
    The code to decode it is embedded in the script to generate the desired file at evaluation time, this prevents
    special characters in the file to be interpreted at runtime by the shell.

    :param path: path of the file
    :param file_content: string content of the file
    :param executable: if True, marks the file as executable (chmod +x)
    :param token: token identifying the end of the stream (useful for nesting)

    :return: bash code embedding an ascii file in a script
    """
    return embed_binary_file(path, file_content.encode('utf-8'), executable=executable, token=token)


def make_uid_numeric(uid: str) -> int:
    """Efficient hashing for the unique id into a 64bit integer"""
    return int('0o' + ''.join([
        f'{ord(c)-ord("0") if c in string.digits else ord(c)-ord("A")+10:o}'
        for c in uid.replace('-','')
    ]), 8) % 0x7FFF_FFFF_FFFF_FFFF

def to_snakecase(s: str):
    """
    Convert camelCase, PascalCase and kebab-case into snake_case.
    """
    ret = [s[0].lower()]
    for char in s[1:]:
        if char in string.ascii_uppercase:
            ret += ['_', char.lower()]
        elif char in ['-']:
            ret.append('_')
        else:
            ret.append(char)

    return ''.join(ret)



def compute_pod_resource(pod: interlink.PodRequest, resource: str):
    """
    Loops over the containers of a pod to sum resource requests/limits.

    Return an integer representing:
     - 'cpu': number of cpus (obtained ceiling the kubernetes cpu request)
     - 'millicpu': number of cpus multiplied by 1000
     - 'memory': number of bytes
    """
    _resource = 'cpu' if resource == 'millicpu' else resource

    # check if the pod has requested an nvidia.com/gpu resource
    if resource == 'nvidia.com/gpu':
        return sum([
            int(parse_quantity(((c.get('resources') or {}).get('requests') or {}).get('nvidia.com/gpu') or "0"))
            for c in pod.spec['containers']
        ])

    return int(
        math.ceil(
            (1000 if resource == 'millicpu' else 1) * sum([
                max(
                    parse_quantity(((c.get('resources') or {}).get('requests') or {}).get(_resource) or "1"),
                    parse_quantity(((c.get('resources') or {}).get('limit') or {}).get(_resource) or "1"),
                ) for c in pod.spec['containers'] ]
            )
        )
    )

def sanitize_uid(uid: str):
    """
    Return a sanitized version of a string, only including letters and digits
    """
    return ''.join([ch for ch in uid if ch in (string.ascii_letters + string.digits)])


class TokenManager:
    """
    TokenManager refreshes OIDC tokens or load them from a POSIX path if refreshed by an external agent.

    Example with an external manager.
    ```python
    token_manager = TokenManager(bearer_token_path="/interlink/token")
    ...
    os.environ['BEARER_TOKEN'] = token_manager.token
    ```

    Example with a refresh token
    ```python
    token_manager = TokenManager(
        iam_issuer=os.environ['IAM_ISSUER'],
        refresh_token=os.environ['REFRESH_TOKEN'],
        iam_client_id=os.environ['IAM_CLIENT_ID'],
        iam_client_secret=os.environ['IAM_CLIENT_SECRET'],
        token_validity_seconds=600
    )
    ...
    os.environ['BEARER_TOKEN'] = token_manager.token
    ```
    """

    def __init__(
            self,
            iam_issuer: Optional[str] = None,
            refresh_token: Optional[str] = None,
            iam_client_id: Optional[str] = None,
            iam_client_secret: Optional[str] = None,
            token_validity_seconds: Optional[int] = None,
            bearer_token_path: Optional[str] = None,
    ):
        self.iam_issuer = iam_issuer
        self.refresh_token = refresh_token
        self.iam_client_id = iam_client_id
        self.iam_client_secret = iam_client_secret
        self.token_validity_seconds = token_validity_seconds
        self.bearer_token_path = bearer_token_path

        self._last_token_refresh: Union[datetime, None] = None
        self._token: Union[str, None] = None

        ## Validation
        if self.bearer_token_path is not None:
            self.token_validity_seconds = 0
            return

        fields = ('iam_issuer', 'refresh_token', 'iam_client_id', 'iam_client_secret', 'token_validity_seconds')
        null_fields = [f for f in fields if getattr(self, f) is None]
        if len(null_fields):
            raise ValueError(
                f"`bearer_token_path` was not set, but the following fields were found None: {', '.join(null_fields)}"
            )

    def _refresh_token(self):
        if self.bearer_token_path is not None:
            with open(self.bearer_token_path) as f:
                return f.read()

        response = requests.post(
            self.iam_issuer + '/token',
            data={'grant_type': 'refresh_token', 'refresh_token': self.refresh_token},
            auth=(self.iam_client_id, self.iam_client_secret),
            )
        if response.status_code / 100 != 2:
            print(response.text)
        response.raise_for_status()

        access_token = response.json().get("access_token")
        print (access_token, file=open(".token", "w"))
        return access_token

    @property
    def token(self) -> str:
        """
        Return a cached version of the token, taking care of refreshing the cache as needed.
        """
        ltr = self._last_token_refresh
        if self._token is None or ltr is None or (datetime.now() - ltr).seconds > self.token_validity_seconds:
            self._last_token_refresh = datetime.now()
            self._token = self._refresh_token()

        return self._token

