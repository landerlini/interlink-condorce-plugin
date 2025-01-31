import argparse
from multiprocessing import Process
import os
import string
import logging
import asyncio
from signal import SIGINT, SIGTERM
from argparse import ArgumentParser

from . import configuration as cfg
from .BaseNatsProvider import BaseNatsProvider
from .apptainer_cmd_builder import BuildConfig
from .utils import Resources

__version__ = "0.0.0"
MISSING_BUILD_CONFIG_ERROR_CODE = 127

class AllProviders:
    @staticmethod
    def condor(**kwargs):
        from .condor.CondorProvider import CondorProvider
        return CondorProvider(**kwargs)

    @staticmethod
    def podman(**kwargs):
        from .podmanprovider.PodmanProvider import PodmanProvider
        return PodmanProvider(**kwargs)

    @staticmethod
    def interlink(**kwargs):
        from .kubernetesprovider.KubernetesProvider import KubernetesProvider
        return KubernetesProvider(**kwargs)

def _create_and_operate_provider(args: argparse.Namespace, build_config: BuildConfig, leader: bool = False):
    """
    Internal. Simple wrapper initiating and executing a provider, based on the arguments.
    """
    provider: BaseNatsProvider = getattr(AllProviders, args.provider)(
        nats_server=args.server,
        nats_pool=args.pool,
        build_config=build_config,
        interactive_mode=not args.non_interactive,
        shutdown_subject=args.shutdown_subject,
        # The provider leader(s) is in charge of updating the interlink provider on pool build-config and resources
        leader=leader,
        resources=Resources(
            cpu=args.cpu,
            memory=args.memory,
            pods=args.pods,
            gpus=args.gpus,
        )
    )

    loop = asyncio.get_event_loop()
    loop.add_signal_handler(SIGINT, provider.maybe_stop)
    loop.add_signal_handler(SIGTERM, provider.maybe_stop)
    loop.run_until_complete(provider.main_loop())


def main():
    parser = ArgumentParser(
        prog="natsprovider",
        description="Provide interLink computing resources through NATS",
        epilog="(c) Istituto Nazionale di Fisica Nucleare 2024. MIT Licence.",
    )

    parser.add_argument(
        "--version", "-v",
        help="Prints the version and exits",
        action="store_true"
    )

    parser.add_argument(
        "--generate-config",
        help="Generate the build configuration dictionary with default values to stdout",
        action="store_true"
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
        "--pool", "-q",
        default="default-pool",
        help="NATS pool or ResourceFlavor defining the NATS subject",
    )

    parser.add_argument(
        "--shutdown-subject", "-k",
        default="*",
        help="NATS subject triggering a shutdown (and possibly a restart) of this service",
    )

    parser.add_argument(
        "--verbose", "-V",
        default=cfg.DEBUG,
        help="Enhance verbosity of the log",
        action='store_true',
    )

    parser.add_argument(
        "--non-interactive", "-b",
        help="Exits immediately on Ctrl-C instead of printing statistics and requesting for confirmation",
        action='store_true',
    )

    parser.add_argument(
        "--build-config", "-f",
        help="Path to a file defining the build config. Defaults to /etc/interlink/build.conf",
        default=None,
    )

    parser.add_argument(
        "--cpu",
        type=str,
        help="Number of CPUs made available through the interlink protocol, overrides provider assessment (if any).",
        default=None,
    )

    parser.add_argument(
        "--memory",
        type=str,
        help="RAM memory available through the interlink protocol, overrides provider assessment (if any).",
        default=None,
    )

    parser.add_argument(
        "--pods",
        type=str,
        help="Max number of pods executable, overrides provider assessment (if any).",
        default=None,
    )

    parser.add_argument(
        "--gpus",
        type=str,
        help="Max number of nVidia GPUs available through interlink, overrides provider assessment (if any).",
        default=None,
    )

    parser.add_argument(
        "--responders",
        type=int,
        help="Number of nats responders (processes) to execute for load balancing.",
        default=1,
    )

    args = parser.parse_args()

    if args.version:
        print(__version__)
        exit(0)

    if args.generate_config:
        print(BuildConfig())
        exit(0)


    log_format = '%(asctime)-22s %(name)-10s %(levelname)-8s %(message)-90s'
    logging.basicConfig(
        format=log_format,
        level=logging.DEBUG if args.verbose else logging.INFO,
    )
    logging.debug("Enabled debug mode.")

    if any([letter not in string.ascii_lowercase + '-' for letter in args.pool]):
        raise ValueError(f"Invalid pool `{args.pool}`: pool names can only include lower-case letters.")

    build_config = BuildConfig.from_file(args.build_config)

    additional_responders = []
    if args.responders > 1:
        for i_responder in range(args.responders - 1):
            p = Process(target=_create_and_operate_provider, args=(args, build_config))
            additional_responders.append(p)
            p.start()

    # Main instance
    _create_and_operate_provider(args, build_config, leader=True)

    for p in additional_responders:
        p.join()



if __name__ == '__main__':
    import sys
    sys.exit(main())
