from collections import deque
from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional


class _Submission(BaseModel, extra='forbid'):
    """
    Represent the time marker of a single submission
    """
    job_name: str = Field(description="Name or unique IF of the job (for the backend)")
    submission_timestamp: datetime = Field(default_factory=datetime.now, description="Submission time")

    @property
    def seconds_since_submission(self) -> float:
        return (self.submission_timestamp - datetime.now()).total_seconds()

class SubmissionRecord:
    """
    A simple helper class to track submission times and check whether a submission is possibly in the pipeline

    Arguments.
        max_length (int) - default 0xFFFF
            Length of the memory buffer where job names are stored.

        timeout_seconds (int) - default 300
            Default timeout before returning False at `is_being_submitted` considering the job submission failed.
    """
    def __init__(self, max_length: int = 0xFFFF, timeout_seconds: int = 300):
        self._max_length = max_length
        self._timeout_seconds = timeout_seconds
        self._queue = deque(maxlen=self._max_length)


    @property
    def max_length(self):
        """Maximum length of the memory"""
        return self._max_length

    @property
    def timeout_seconds(self):
        """Default timeout before considering the job submission failed"""
        return self._timeout_seconds

    def mark_job_as_submitted(self, job_name: str, submission_timestamp: Optional[datetime] = None):
        """Helper function to invoke as soon as the request to submit a job is received, marking the timestamp"""
        if submission_timestamp is None:
            submission_timestamp = datetime.now()

        self._queue.append(_Submission(job_name=job_name, submission_timestamp=submission_timestamp))

    def is_being_submitted(self, job_name: str, timeout_seconds: Optional[int] = None) -> bool:
        """Helper function to check whether a job has been submitted in the last `timeout_seconds` seconds."""
        timeout = timeout_seconds if timeout_seconds is not None else self.timeout_seconds
        return job_name in [s.job_name for s in self._queue if s.seconds_since_submission < timeout]
