"""
Script to obtain and install a refresh token to be used for tests.

Instructions:

IAM_ISSUER=https://... \
IAM_CLIENT_ID=... \
IAM_CLIENT_SECRET=... \
python3 ./_auth.py

The script generate a .env file that must be passed to pytest to define the variables needed to authenticate
the user against the CNAF Tier-1 to the purpose of running the tests.
"""

import requests
import textwrap
import time
import os
import base64

IAM_ISSUER = os.environ.get("IAM_ISSUER")
IAM_CLIENT_ID = os.environ.get("IAM_CLIENT_ID")
IAM_CLIENT_SECRET = os.environ.get("IAM_CLIENT_SECRET")

SCOPES = [
        'openid',
        'profile',
        'offline_access',
        'wlcg.groups',
        'wlcg',
        'compute.create',
        'compute.modify',
        'compute.read',
        'compute.cancel',
    ]

response = requests.post(
    os.environ["IAM_ISSUER"] + '/devicecode',
    data=dict(
        client_id=IAM_CLIENT_ID,
        scope=" ".join(SCOPES)
    )
)

print (response.text)

response_data = response.json()
print(f"Visit {response_data['verification_uri_complete']}")

while True:
    token_response = requests.post(
        os.environ["IAM_ISSUER"] + "/token",
        data=dict(
            grant_type="urn:ietf:params:oauth:grant-type:device_code",
            device_code=response.json()['device_code'],
        ),
        headers={
            'Accept': 'application/json',
            'Content-type': 'application/x-www-form-urlencoded',
        },
        auth=(os.environ.get('IAM_CLIENT_ID'), os.environ.get('IAM_CLIENT_SECRET'))
        )

    print (token_response)

    if token_response.status_code == 400 and token_response.json().get('error', '') == 'authorization_pending':
        time.sleep(1)
    elif token_response.status_code == 200:
        refresh_token = token_response.json().get('refresh_token')
        break
    else:
        token_response.raise_for_status()


with open("secret.env", "w") as f:
    print(textwrap.dedent(f"""
        IAM_ISSUER={IAM_ISSUER}
        IAM_CLIENT_ID={IAM_CLIENT_ID}
        IAM_CLIENT_SECRET={IAM_CLIENT_SECRET}
        REFRESH_TOKEN={refresh_token}
    """), file=f)