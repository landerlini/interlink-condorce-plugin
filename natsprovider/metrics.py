from prometheus_client import Counter, Gauge, Histogram, Summary
from typing import List
from pydantic import BaseModel

PREFIX = "interlink_nats_plugin_"

class MetricSpec(BaseModel):
    name: str
    description: str
    labels: List[str]


class MetricStore:
    def __init__(self, metric_class, entries: List[MetricSpec]):
        self.metric_class = metric_class
        self._data = {e.name: metric_class(PREFIX+e.name, e.description, e.labels) for e in entries}

    def __getitem__(self, metric):
        if metric not in self._data:
           self._data[metric] = self.metric_class(PREFIX+metric, PREFIX+metric)

        return self._data[metric]


################################################################################

counters = MetricStore(
    metric_class=Counter,
    entries=[
        MetricSpec(
            name="api_call",
            description="Number of calls",
            labels=['api'],
        ),
        MetricSpec(
            name="nats_errors",
            description="Number of NATS errors",
            labels=['type'],
        ),
        MetricSpec(
            name="build_config_updates",
            description="Updates from resource pools",
            labels=['pool'],
        ),
        MetricSpec(
            name="opened_nats",
            description="Incremented when a new nats connection is open",
            labels=[],
        ),
        MetricSpec(
            name="closed_nats",
            description="Incremented when a nats connection is closed",
            labels=[],
        ),
        MetricSpec(
            name="pod_transitions",
            description="Transition of the state of a pod. Transitions can be: started, succeeded, failed, cleared, or lost",
            labels=['transition'],
        ),
        MetricSpec(
            name="status_retrival_errors",
            description="Errors while retrieving pod status",
            labels=['status_code'],
        ),
    ]
)

gauges = MetricStore(
    metric_class=Gauge,
    entries=[
        MetricSpec(
            name="pod_status",
            description="Status of the pods",
            labels=['type'],
        ),
        MetricSpec(
            name="status_retrival_attempts",
            description="Number of attempts to retrieve status",
            labels=[],
        ),
    ]
)

histograms = MetricStore(
    metric_class=Histogram,
    entries=[],
)

summaries = MetricStore(
    metric_class=Summary,
    entries=[
        MetricSpec(
            name="nats_response_time",
            description="NATS response time in nanoseconds",
            labels=[],
        ),
        MetricSpec(
            name="nats_response_time_per_subject",
            description="NATS response time in nanoseconds",
            labels=['subject', 'pool'],
        ),
    ]
)
