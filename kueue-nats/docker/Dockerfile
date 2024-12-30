# Image name: landerlini/kueue-nats-controller
FROM python:3.12

RUN curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl" && \
    install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl



RUN pip install kopf nats-py kubernetes pydantic

RUN mkdir /app

RUN adduser kopf
USER kopf

COPY ./controller.py /app/controller.py

CMD ["kopf", "run", "--standalone", "--all-namespaces", "/app/controller.py"]