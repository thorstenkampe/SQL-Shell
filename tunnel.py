# https://sshtunnel.readthedocs.io/en/latest/

import configparser, getpass, logging, pathlib, sys
import sshtunnel
import toolbox as tb

ini_file = 'tunnel.ini'
defaults = {
    'proxy_port':  '22',
    'proxy_user':  getpass.getuser(),
    'remote_host': 'localhost'
}

config = configparser.ConfigParser(defaults=defaults)

logger = sshtunnel.create_logger(loglevel='INFO')
logger.handlers[0].setFormatter(logging.Formatter('! %(message)s'))

class MockTunnel:
    def __init__(self, local_bind_host, local_bind_port):
        self.local_bind_host = local_bind_host
        self.local_bind_port = local_bind_port

    def __enter__(self):        # for `with tunnel:`
        return self

    def __exit__(self, *args):  # for `with tunnel:`
        pass

def tunnel(remote_host, remote_port, local_port=0):  # `0` means random port
    if tb.is_pyinstaller():
        # https://pyinstaller.readthedocs.io/en/stable/runtime-information.html
        script_dir = sys.executable
    else:
        script_dir = __file__
    # config file in same directory as this file
    config_file = pathlib.Path(script_dir).with_name(ini_file)
    config.read(config_file)

    try:
        section = config[remote_host]
    except KeyError:
        # remote host is not in ini file so we don't need a real tunnel
        tunnel = MockTunnel(
            local_bind_host = remote_host,
            local_bind_port = remote_port
        )
    else:
        tunnel = sshtunnel.SSHTunnelForwarder(
            ssh_address_or_host = section['proxy_host'],
            ssh_config_file     = None,
            ssh_port            = int(section['proxy_port']),
            ssh_username        = section['proxy_user'],
            remote_bind_address = (section['remote_host'], int(remote_port)),
            local_bind_address  = ('localhost', local_port)
        )

    return tunnel
