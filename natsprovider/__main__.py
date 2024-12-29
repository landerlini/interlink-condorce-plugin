import os
import string
import logging
import asyncio
from signal import SIGINT, SIGTERM
from argparse import ArgumentParser

from tomli import load as toml_load

from . import configuration as cfg
from .BaseNatsProvider import BaseNatsProvider
from .apptainer_cmd_builder import BuildConfig
from .utils import Resources

__version__ = "0.0.0"
MISSING_BUILD_CONFIG_ERROR_CODE = 127

class AllProviders:
    @staticmethod
    def condor(nats_server: str, nats_queue: str, build_config: BuildConfig, interactive_mode: bool):
        from .condor.CondorProvider import CondorProvider
        return CondorProvider(nats_server, nats_queue, build_config, interactive_mode)

    @staticmethod
    def podman(nats_server: str, nats_queue: str, build_config: BuildConfig, interactive_mode: bool):
        from .podmanprovider.PodmanProvider import PodmanProvider
        return PodmanProvider(nats_server, nats_queue, build_config, interactive_mode)

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
        "--queue", "-q",
        default="default-queue",
        help="NATS queue or ResourceFlavor defining the NATS subject",
    )

    parser.add_argument(
        "--shutdown-subject", "-k",
        default=None,
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
        "--cpus",
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

    if any([letter not in string.ascii_lowercase + '-' for letter in args.queue]):
        raise ValueError(f"Invalid queue `{args.queue}`: queue names can only include lower-case letters.")

    tolerate_missing_build_config = (args.build_config is None)
    build_config_file = args.build_config if args.build_config is not None else '/etc/interlink/build.conf'

    if not os.path.exists(build_config_file):
        if tolerate_missing_build_config:
            logging.warning(f"Build configuration file {build_config_file} does not exist. Using default configuration.")
            build_config = BuildConfig()
        else:
            logging.critical(f"Build configuration file {build_config_file} does not exist.")
            exit(MISSING_BUILD_CONFIG_ERROR_CODE)
    else:
        with open(build_config_file, 'rb') as input_file:
            build_config = BuildConfig(**toml_load(input_file))

    provider: BaseNatsProvider = getattr(AllProviders, args.provider)(
        nats_server=args.server,
        nats_queue=args.queue,
        build_config=build_config,
        interactive_mode=not args.non_interactive,
        resources=Resources(
            cpus=args.cpus,
            memory=args.memory,
            pods=args.pods,
            gpus=args.gpus,
        )
    )

    loop = asyncio.get_event_loop()
    loop.add_signal_handler(SIGINT, provider.maybe_stop)
    loop.add_signal_handler(SIGTERM, provider.maybe_stop)
    loop.run_until_complete(provider.main_loop())


if __name__ == '__main__':
    import sys
    sys.exit(main())
