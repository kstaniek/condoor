"""Init file for condoor."""

from condoor.connection import Connection
from condoor.config import CONF
from condoor.patterns import YPatternManager as PatternManager

from condoor.exceptions import CommandTimeoutError, ConnectionError, ConnectionTimeoutError, CommandError, \
    CommandSyntaxError, ConnectionAuthenticationError, GeneralError, InvalidHopInfoError, ConfigurationErrors, \
    ConfigurationSemanticErrors, ConnectionStandbyConsole
from version import __version__

from pexpect import TIMEOUT, EOF


pattern_manager = PatternManager()

"""
This is a python module providing access to Cisco devices over Telnet and SSH.

"""

__all__ = ('Connection', 'TIMEOUT', 'EOF', 'pattern_manager', 'CONF', 'InvalidHopInfoError',
           'CommandTimeoutError', 'ConnectionError', 'ConnectionTimeoutError', 'CommandError',
           'CommandSyntaxError', 'ConnectionAuthenticationError', 'GeneralError', 'ConfigurationErrors',
           'ConfigurationSemanticErrors', 'ConnectionStandbyConsole', '__version__')
