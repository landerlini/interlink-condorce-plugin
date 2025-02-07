from prometheus_client import Counter, Gauge, Histogram
from typing import List
from pydantic import BaseModel

class MetricSpec(BaseModel):
    name: str
    description: str
    labels: List[str]


class MetricStore:
    def __init__(self, metric_class, entries: List[MetricSpec]):
        self.metric_class = metric_class
        self._data = {e.name: Counter(e.name, e.description) for e in entries}

    def __getitem__(self, metric):
        if metric not in self._data:
           self._data[metric] = self.metric_class(metric, metric)

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
    ]
)

histograms = MetricStore(
    metric_class=Histogram,
    entries=[
        MetricSpec(
            name="nats_response_time",
            description="NATS response time",
            labels=[],
        ),
    ]
)
