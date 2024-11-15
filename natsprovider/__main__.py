import string
import logging
import asyncio
from signal import SIGINT, SIGTERM
from argparse import ArgumentParser
from . import configuration as cfg
from .BaseNatsProvider import BaseNatsProvider
from .condor.CondorProvider import CondorProvider


class AllProviders:
    @staticmethod
    def condor(nats_server: str, nats_queue: str, interactive_mode: bool) -> CondorProvider:
        from .condor.CondorProvider import CondorProvider
        return CondorProvider(nats_server, nats_queue, interactive_mode)


parser = ArgumentParser(
    prog="natsprovider",
    description="Provide interLink computing resources through NATS",
    epilog="(c) Istituto Nazionale di Fisica Nucleare 2024. MIT Licence.",
)

parser.add_argument(
    "provider",
    choices=[k for k in AllProviders.__dict__.keys() if '_' not in k],
    help="Name of the provider to configure"
)

parser.add_argument(
    "--server", "-s",
    default=cfg.NATS_SERVER,
    help="NATS server with format `nats://<IP or FQDN>:<port>`",
)

parser.add_argument(
    "--queue", "-q",
    default=cfg.NATS_SUBJECT.split('.')[-1],
    help="NATS queue or ResourceFlavor defining the NATS subject",
)

parser.add_argument(
    "--verbose", "-v",
    default=cfg.DEBUG,
    help="Enhance verbosity of the log",
    action='store_true',
)

parser.add_argument(
    "--non-interactive", "-b",
    help="Exits immediately on Ctrl-C instead of printing statistics and requesting for confirmation",
    action='store_true',
)

args = parser.parse_args()

log_format = '%(asctime)-22s %(name)-10s %(levelname)-8s %(message)-90s'
logging.basicConfig(
    format=log_format,
    level=logging.DEBUG if args.verbose else logging.INFO,
)
logging.debug("Enabled debug mode.")

if any([letter not in string.ascii_lowercase for letter in args.queue]):
    raise ValueError(f"Invalid queue `{args.queue}`: queue names can only include lower-case letters.")

provider: BaseNatsProvider = getattr(AllProviders, args.provider)(
    nats_server=args.server,
    nats_queue=args.queue,
    interactive_mode=not args.non_interactive,
)

loop = asyncio.get_event_loop()
loop.add_signal_handler(SIGINT, provider.maybe_stop)
loop.add_signal_handler(SIGTERM, provider.maybe_stop)
loop.run_until_complete(provider.main_loop())
