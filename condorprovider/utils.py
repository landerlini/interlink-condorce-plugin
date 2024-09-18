import random
import json
import os.path
import string
import textwrap
from base64 import b64encode

from kubernetes.client.api_client import ApiClient as K8sApiClient

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


def embed_ascii_file(path: str, file_content: str, executable: bool = False, token: str = "EOF") -> str:
    """
    Embed an ascii file in a bash script using the syntax cat<<EOF > path

    :param path: path of the file
    :param file_content: multi-line content of the file
    :param executable: if True, marks the file as executable (chmod +x)
    :param token: token identifying the end of the stream (useful for nesting)

    :return: bash code embedding an ascii file in a script
    """
    file_content = '\n'.join([line for line in file_content.split('\n') if len(line.replace(' ', '')) > 0])
    ret = [
        f"mkdir -p {os.path.dirname(os.path.abspath(path))}",
        f"cat <<{token} > {path}",
        textwrap.dedent(file_content.replace("$", "\$")),
        token+"\n",
    ]

    if executable:
        ret.append(f"chmod +x {path}")

    return '\n'+'\n'.join(ret)

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
    path_tmp = path+".tmp"
    ret = [
        embed_ascii_file(path_tmp, str(b64encode(file_content), 'ascii'), executable=False, token=token),
        f"cat {path_tmp} | base64 --decode &> {path}",
        f"rm -f {path_tmp}",
    ]

    if executable:
        ret.append(f"chmod +x {path}")

    return '\n'+'\n'.join(ret)

def make_uid_numeric(uid: str) -> int:
    """Efficient hashing for the unique id into a 64bit integer"""
    return int('0o' + ''.join([
        f'{ord(c)-ord("0") if c in string.digits else ord(c)-ord("A")+10:o}'
        for c in uid
    ]), 8) % 0x7FFF_FFFF_FFFF_FFFF

def deserialize_kubernetes(data, klass):
    """
    Boilerplate to deserialize a dictionary into a Kubernetes object. Not very efficient.
    """
    class JsonWrapper:
        def __init__(self, json_data):
            self.data = json.dumps(json_data)

    return K8sApiClient().deserialize(JsonWrapper(data), klass)

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



