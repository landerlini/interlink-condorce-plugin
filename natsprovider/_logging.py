import logging
from pprint import pformat
import os
import time
from typing import Literal, Any, Dict

from natsprovider import configuration as cfg


LAST_LOG_SCAN = None

def logging_setup():
    """
    Setup logging format and its verbosity based on *configuration*
    """
    log_format = '%(asctime)-22s %(name)-10s %(levelname)-8s %(message)-90s'
    logging.basicConfig(
        format=log_format,
        level=logging.DEBUG if cfg.DEBUG else logging.INFO,
    )
    logging.debug("Enabled debug mode.")



def log_pod(pod: Dict[Literal['pod', 'container', 'jobConfig'], Any]):
    global LAST_LOG_SCAN

    if cfg.LOGDIR is None:
        logging.debug("LOGDIR is not defined. Skip pod logging.")
        return 

    # Create the log for this pod
    os.makedirs(cfg.LOGDIR, exist_ok=True)
    with open(os.path.join(cfg.LOGDIR, str(pod['pod']['metadata']['name'])), "w") as f:
        f.write(pformat(pod))

    # Iterates over old logs, at most once per hour
    now = time.time()
    if LAST_LOG_SCAN is not None and LAST_LOG_SCAN - now < 3600:
        return 
    LAST_LOG_SCAN = now

    # logs older than a threshold are removed
    threshold = cfg.LOG_PERSISTENCY_HOURS * 3600 

    for filename in os.listdir(cfg.LOGDIR):
        file_path = os.path.join(cfg.LOGDIR, filename)
        if os.path.isfile(file_path):
            file_age = now - os.path.getmtime(file_path)
            if file_age > threshold:
                logging.debug(f"Cleaning log for {filename}")
                os.remove(file_path)
