# Image name: landerlini/interlink-virtual-node-authenticator:latest
FROM python:alpine

RUN pip install kubernetes requests && mkdir /app

COPY ./authenticator.py /app/create_refresh_token

RUN chmod +x /app/create_refresh_token && \
    echo "Execute ./create_refresh_token to obtain a refresh token and store it in Kubernetes." \
    > /app/README

WORKDIR /app