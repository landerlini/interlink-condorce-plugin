import string
from argparse import ArgumentParser
from . import configuration as cfg

class AllProviders:
    @staticmethod
    def condor(nats_server: str, nats_queue: str, **kwargs):
        from .condor.CondorProvider import CondorProvider
        return CondorProvider(nats_server, nats_queue)


parser = ArgumentParser(
    prog="natsprovider",
    description="Provide interLink computing resources through NATS",
    epilog="(c) Istituto Nazionale di Fisica Nucleare 2024. MIT Licence.",
)

parser.add_argument(
    "provider",
    nargs=1,
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

args = parser.parse_args()

if any([letter not in string.ascii_lowercase for letter in args.queue]):
    raise ValueError(f"Invalid queue `{args.queue}`: queue names can only include lower-case letters.")

provider = getattr(AllProviders, args.provider)(
    nats_server=args.server,
    nats_queue=args.queue,
)

