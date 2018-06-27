"""Provides the main Connection class."""
import re
import os
import sys
import time
import shelve
import logging
from hashlib import md5

from collections import deque
from condoor.chain import Chain
from condoor.exceptions import ConnectionError, ConnectionTimeoutError, ConnectionStandbyConsole
from condoor.utils import FilteredFile, normalize_urls
from condoor.log import FileLogger
from condoor.version import __version__

logger = logging.getLogger(__name__)


_CACHE_FILE = "/tmp/condoor." + __version__


class Connection(object):
    """Connection class providing the condoor API.

    Main API class providing the basic API to the physical devices.
    It implements the following methods:

        - connect
        - reconnect
        - disconnect
        - send
        - reload
        - enable
        - run_fsm

    """

    def __init__(self, name, urls=[], log_dir=None, log_level=logging.DEBUG, log_session=True):
        """Initialize the :class:`condoor.Connection` object.

        Args:
            name (str): The connection name.

            urls (str or list): This argument may be a string or list of strings or list of list of strings.
             When **urls** type is string it must be valid URL in the following format::

                urls = "<protocol>://<user>:<password>@<host>:<port>/<enable_password>

             Example::

                urls = "telnet://cisco:cisco@192.168.1.1"

             The *<port>* can be omitted and default port for protocol will be used.
             When **urls** type is list of strings it can provide multiple intermediate hosts (jumphosts) with
             their credentials before making the final connection the target device. Example::

                urls = ["ssh://admin:secretpass@jumphost", "telnet://cisco:cisco@192.168.1.1"]

                urls = ["ssh://admin:pass@jumphost1", "ssh://admin:pass@jumphost2",
                        "telnet://cisco:cisco@192.168.1.1"]

             The **urls** can be list of list of strings. In this case the multiple connection chains can be provided
             to the target device. This is used when device has two processor cards with console connected to
             both of them. Example::

                urls = [["ssh://admin:pass@jumphost1", "telnet://cisco:cisco@termserv:2001"],
                        ["ssh://admin:pass@jumphost1", "telnet://cisco:cisco@termserv:2002"]]

            log_dir (str): The path to the directory when *session.log* and *condoor.log* is stored. If *None*
             the condoor log and session log is redirected to *stdout*

            log_level (int): The condoor logging level.

            log_session (Bool): If **True** the terminal session is logged.


        """
        self._discovered = False
        self._last_chain_index = 0
        self._msg_callback = None
        self._error_msg_callback = None
        self._warning_msg_callback = None
        self._info_msg_callback = None

        self._log_dir = log_dir

        self.log_session = log_session
        self.session_fd = None
        self._external_session_fd = True

        self._trace_fd = None
        self._external_trace_fd = True

        self.log = None

        self._urls = urls
        self.connection_chains = None

    def finalize(self):
        """Clean up the object.

        After calling this method the object can't be used anymore.
        This will be reworked when changing the logging model.
        """
        self.pause_session_logging()
        self._disable_logging()
        self._msg_callback = None
        self._error_msg_callback = None
        self._warning_msg_callback = None
        self._info_msg_callback = None

    def _make_session_fd(self):
        session_fd = None
        if self._log_dir is not None:
            try:
                # FIXME: take pattern from pattern manager
                session_fd = FilteredFile(os.path.join(self._log_dir, 'session.log'),
                                          mode="a", pattern=re.compile("s?[f|s][t|c]p://.*:(.*)@"))
            except IOError:
                print("Unable to create session log file")

        else:
            if self.log_session:
                session_fd = sys.stdout

        return session_fd

    def _make_trace_fd(self):
        trace_fd = None
        if self._log_dir is not None:
            try:
                trace_fd = FilteredFile(os.path.join(self._log_dir, 'condoor.log'), mode="a",
                                        pattern=re.compile("s?[f|s][t|c]p://.*:(.*)@"))
            except IOError:
                print("Unable to create log file")
        else:
            trace_fd = sys.stderr
        return trace_fd

    def _enable_logging(self, logfile, tracefile):
        if self.session_fd is None:
            if self.log_session:
                if logfile:
                    self.session_fd = logfile
                    self._external_session_fd = True
                else:
                    self.session_fd = self._make_session_fd()
                    self._external_session_fd = False
            else:
                self.session_fd = None

        if self._trace_fd is None:
            if tracefile:
                self._trace_fd = tracefile
                self._external_trace_fd = True
            else:
                self._trace_fd = self._make_trace_fd()
                self._external_trace_fd = False

        self.log = FileLogger(self._trace_fd)

    def _disable_logging(self):
        if self._trace_fd:
            self.log("Closing logs")
        if not self._external_trace_fd and self._trace_fd:
            if not self._trace_fd.closed:
                self._trace_fd.flush()
                # do not close if stderr
                if self._trace_fd is not sys.stderr:
                    self._trace_fd.close()
                    self.log("Trace log closed")
            self._trace_fd = None

        if not self._external_session_fd and self.session_fd:
            if not self.session_fd.closed:
                self.session_fd.flush()
                # do not close if stdout
                if self.session_fd is not sys.stdout:
                    self.session_fd.close()
                    self.log("Session log closed")
            self.session_fd = None

    def _get_key(self):
        key = md5()
        key.update(str(self.connection_chains))
        self.log("Cache key: {}".format(self.connection_chains))
        return key.hexdigest()

    def _cache_open(self, mode='r'):
        try:
            cache = shelve.open(_CACHE_FILE, mode)
        except Exception:
            self.log("Unable to open a cache file for read.")
            return None
        return cache

    def _write_cache(self):
        key = self._get_key()
        cache = self._cache_open(mode='c')
        if cache is not None:
            cache[key] = self.description_record
            cache.close()
            self.log("Connection information cached: {}".format(key))

    def _read_cache(self):
        key = self._get_key()
        cache = self._cache_open(mode='r')
        if cache is not None:
            try:
                self.description_record = cache[key]
                self.log("Read cached information.")
            except KeyError:
                self.log("Connection cache missed: {}.".format(key))
            finally:
                cache.close()

    def _clear_cache(self):
        # key = self._get_key()
        self.log("Clearing connection cache")
        self._read_cache()
        self.description_record = None
        self.log("Description record: {}".format(self.description_record))
        self._write_cache()

    def _chain_indices(self):
        """Get the deque of chain indices starting with last successful index."""
        chain_indices = deque(range(len(self.connection_chains)))
        chain_indices.rotate(self._last_chain_index)
        return chain_indices

    def connect(self, logfile=None, force_discovery=False, tracefile=None):
        """Connect to the device.

        Args:
            logfile (file): Optional file descriptor for session logging. The file must be open for write.
                The session is logged only if ``log_session=True`` was passed to the constructor.
                If *None* then the default *session.log* file is created in `log_dir`.

            force_discovery (Bool): Optional. If True the device discover process will start after getting connected.

        Raises:
            ConnectionError: If the discovery method was not called first or there was a problem with getting
                the connection.

            ConnectionAuthenticationError: If the authentication failed.

            ConnectionTimeoutError: If the connection timeout happened.

        """
        self.reconnect(logfile=logfile, max_timeout=120, force_discovery=force_discovery, tracefile=tracefile,
                       retry=False)

        return

    def reconnect(self, logfile=None, max_timeout=360, force_discovery=False, tracefile=None, retry=True):
        """Reconnect to the device.

        It can be called when after device reloads or the session was
        disconnected either by device or jumphost. If multiple jumphosts are used then `reconnect` starts from
        the last valid connection.

        Args:
            logfile (file): Optional file descriptor for session logging. The file must be open for write.
                The session is logged only if ``log_session=True`` was passed to the constructor.
                It the parameter is None the default *session.log* file is created in `log_dir`.

            max_timeout (int): This is the maximum amount of time during the session tries to reconnect. It may take
                longer depending on the TELNET or SSH default timeout.

            force_discovery (Bool): Optional. If True the device discover process will start after getting connected.

            tracefile (file): Optional file descriptor for condoor logging. The file must be open for write.
                It the parameter is None the default *condoor.log* file is created in `log_dir`.

            retry (bool): Optional parameter causing the connnection to retry until timeout


        Raises:
            ConnectionError: If the discovery method was not called first or there was a problem with getting
             the connection.

            ConnectionAuthenticationError: If the authentication failed.

            ConnectionTimeoutError: If the connection timeout happened.

        """
        self._enable_logging(logfile, tracefile)

        self.log("-" * 20)
        self.log("Condoor Version {}".format(__version__))
        self.log("Cache filename: {}".format(_CACHE_FILE))

        self.connection_chains = [Chain(self, url_list) for url_list in normalize_urls(self._urls)]
        self.log("Connecting")

        self._clear_cache() if force_discovery else self._read_cache()

        excpt = ConnectionError("Could not (re)connect to the device")
        chains = len(self.connection_chains)

        chain_indices = self._chain_indices()
        for index, chain in enumerate(self.connection_chains, start=1):
            self.emit_message("Connection chain {}/{}: {}".format(index, chains, str(chain)), log_level=logging.INFO)

        self.emit_message("Trying to (re)connect within {} seconds".format(max_timeout), log_level=logging.INFO)
        sleep_time = 0
        begin = time.time()
        attempt = 1
        elapsed = 0

        while max_timeout - elapsed > 0:
            # there is no reason to wait for another chain.
            if (attempt - 1) % len(self.connection_chains) != 0:
                sleep_time = 2

            if sleep_time > 0:
                self.emit_message("Waiting {:.0f}s before next connection attempt".format(sleep_time), log_level=logging.INFO)
                time.sleep(sleep_time)

            elapsed = time.time() - begin
            try:
                index = chain_indices[0]
                self.emit_message("Connection chain/attempt [{}/{}]".format(index + 1, attempt),
                                  log_level=logging.INFO)

                chain = self.connection_chains[index]
                self._last_chain_index = index
                if chain.connect():
                    break
            except (ConnectionTimeoutError, ConnectionError) as e:  # pylint: disable=invalid-name
                chain.disconnect()
                self.emit_message("Connection error: {}".format(e), log_level=logging.INFO)
                chain_indices.rotate(-1)
                excpt = e

            except Exception as e:
                self.log("Exception hit: {}".format(str(e)))
                chain.disconnect()
                self.emit_message("Connection error: {}".format(e), log_level=logging.INFO)
                chain_indices.rotate(-1)
                excpt = e

            finally:
                # TODO: Make a configuration parameter
                elapsed = time.time() - begin
                sleep_time = min(30, max_timeout - elapsed)
                self.emit_message("Time elapsed {:.0f}s/{:.0f}s".format(elapsed, max_timeout), log_level=logging.INFO)

            attempt += 1
            if attempt > len(self.connection_chains) and not retry:
                self.emit_message("Unable to (re)connect within {:.0f}s".format(elapsed), log_level=logging.ERROR)
                self._clear_cache()
                self._disable_logging()
                raise excpt
        else:
            self.emit_message("Unable to (re)connect within {:.0f}s".format(elapsed), log_level=logging.ERROR)
            self._clear_cache()
            self._disable_logging()
            raise excpt

        self._write_cache()
        self.emit_message("Target device connected in {:.0f}s.".format(elapsed), log_level=logging.INFO)
        self.log("-" * 20)

    def pause_session_logging(self):
        """Pause session logging."""
        self._chain.ctrl.set_session_log(None)
        self.log("Session logging paused")

    def resume_session_logging(self):
        """Resume session logging."""
        self._chain.ctrl.set_session_log(self.session_fd)
        self.log("Session logging resumed")

    def send(self, cmd="", timeout=300, wait_for_string=None, password=False):
        """Send the command to the device and return the output.

        Args:
            cmd (str): Command string for execution. Defaults to empty string.
            timeout (int): Timeout in seconds. Defaults to 300 sec (5 min)
            wait_for_string (str): This is optional string that driver
            waits for after command execution. If none the detected
            prompt will be used.
            password (bool): If true cmd representing password is not logged
                and condoor waits for noecho.

        Returns:
            A string containing the command output.

        Raises:
            ConnectionError: General connection error during command execution
            CommandSyntaxError: Command syntax error or unknown command.
            CommandTimeoutError: Timeout during command execution

        """
        return self._chain.send(cmd, timeout, wait_for_string, password)

    def config(self, configlet=None, plane='sdr', **attributes):
        """Configure the device.

        This method applies configuration to the device.

        Args:
            configlet (text): The configuration template.
            plane (text): sdr or admin
            attributes (dict): The dictionary of attributes used in template.

        Returns:
            A string with commit label or None

        """
        begin = time.time()
        label = self._chain.target_device.config(configlet, plane, **attributes)
        elapsed = time.time() - begin
        if label:
            self.emit_message("Configuration change last {:.0f}s. Label: {}".format(elapsed, label),
                              log_level=logging.INFO)
        else:
            self.emit_message("Configuration failed.", log_level=logging.WARNING)

        return label

    def rollback(self, label=None, plane='sdr'):
        """Rollback the configuration.

        This method rolls back the configuration on the device.

        Args:
            label (text): The configuration label ID
            plane: (text): sdr or admin

        Returns:
            A string with commit label or None

        """
        begin = time.time()
        rb_label = self._chain.target_device.rollback(label=label, plane=plane)
        elapsed = time.time() - begin
        if label:
            self.emit_message("Configuration rollback last {:.0f}s. Label: {}".format(elapsed, rb_label),
                              log_level=logging.INFO)
        else:
            self.emit_message("Configuration failed.", log_level=logging.WARNING)

        return rb_label

    def disconnect(self):
        """Disconnect the session from the device and all the jumphosts in the path."""
        self._chain.disconnect()
        self._disable_logging()

    def discovery(self, logfile=None, tracefile=None):
        """Discover the device details.

        This method discover several device attributes.

        Args:
            logfile (file): Optional file descriptor for session logging. The file must be open for write.
                The session is logged only if ``log_session=True`` was passed to the constructor.
                It the parameter is not passed then the default *session.log* file is created in `log_dir`.

        """
        self._enable_logging(logfile=logfile, tracefile=tracefile)

        self.log("'discovery' method is deprecated. Please 'connect' with force_discovery=True.")
        self.log("Device discovery process started")
        self.connect(logfile=logfile, force_discovery=True, tracefile=tracefile)
        self.disconnect()

    def enable(self, enable_password=None):
        """Change the device mode to privileged.

        If device does not support privileged mode the the informational message to the log will be posted.

        Args:
            enable_password (str): The privileged mode password. This is optional parameter. If password is not
                provided but required the password from url will be used. Refer to :class:`condoor.Connection`
        """
        self._chain.target_device.enable(enable_password)

    def reload(self, reload_timeout=300, save_config=True, no_reload_cmd=False):
        """Reload the device and wait for device to boot up.

        Returns False if reload was not successful.
        """
        begin = time.time()
        self._chain.target_device.clear_info()
        result = False
        try:
            result = self._chain.target_device.reload(reload_timeout, save_config, no_reload_cmd)

        except ConnectionStandbyConsole as exc:
            message = exc.message
            self.log("Active RP became standby: {}".format(message))
            self.disconnect()
            # self._conn_status.clear()
            result = self.reconnect()

        # connection error caused by device booting up
        except ConnectionError as exc:
            message = exc.message
            self.log("Connection error: {}".format(message))
            self.disconnect()
            result = self.reconnect()

        if result:
            elapsed = time.time() - begin
            self.emit_message("Target device reload last {:.0f}s.".format(elapsed), log_level=logging.INFO)
        else:
            self.emit_message("Target device not reloaded.", log_level=logging.ERROR)
        return result

    def run_fsm(self, name, command, events, transitions, timeout, max_transitions=20):
        """Instantiate and run the Finite State Machine for the current device connection.

        Here is the example of usage::

            test_dir = "rw_test"
            dir = "disk0:" + test_dir
            REMOVE_DIR = re.compile(re.escape("Remove directory filename [{}]?".format(test_dir)))
            DELETE_CONFIRM = re.compile(re.escape("Delete {}/{}[confirm]".format(filesystem, test_dir)))
            REMOVE_ERROR = re.compile(re.escape("%Error Removing dir {} (Directory doesnot exist)".format(test_dir)))

            command = "rmdir {}".format(dir)
            events = [device.prompt, REMOVE_DIR, DELETE_CONFIRM, REMOVE_ERROR, pexpect.TIMEOUT]
            transitions = [
                (REMOVE_DIR, [0], 1, send_newline, 5),
                (DELETE_CONFIRM, [1], 2, send_newline, 5),
                # if dir does not exist initially it's ok
                (REMOVE_ERROR, [0], 2, None, 0),
                (device.prompt, [2], -1, None, 0),
                (pexpect.TIMEOUT, [0, 1, 2], -1, error, 0)

            ]
            if not conn.run_fsm("DELETE_DIR", command, events, transitions, timeout=5):
                return False

        This FSM tries to remove directory from disk0:

        Args:
            name (str): Name of the state machine used for logging purposes. Can't be *None*
            command (str): The command sent to the device before FSM starts
            events (list): List of expected strings or pexpect.TIMEOUT exception expected from the device.
            transitions (list): List of tuples in defining the state machine transitions.
            timeout (int): Default timeout between states in seconds.
            max_transitions (int): Default maximum number of transitions allowed for FSM.

        The transition tuple format is as follows::

            (event, [list_of_states], next_state, action, timeout)

        Where:

        - **event** (str): string from the `events` list which is expected to be received from device.
        - **list_of_states** (list): List of FSM states that triggers the action in case of event occurrence.
        - **next_state** (int): Next state for FSM transition.
        - **action** (func): function to be executed if the current FSM state belongs to `list_of_states`
          and the `event` occurred. The action can be also *None* then FSM transits to the next state
          without any action. Action can be also the exception, which is raised and FSM stops.

        The example action::

            def send_newline(ctx):
                ctx.ctrl.sendline()
                return True

            def error(ctx):
                ctx.message = "Filesystem error"
                return False

            def readonly(ctx):
                ctx.message = "Filesystem is readonly"
                return False

        The ctx object description refer to :class:`condoor.fsm.FSM`.

        If the action returns True then the FSM continues processing. If the action returns False then FSM stops
        and the error message passed back to the ctx object is posted to the log.


        The FSM state is the integer number. The FSM starts with initial ``state=0`` and finishes if the ``next_state``
        is set to -1.

        If action returns False then FSM returns False. FSM returns True if reaches the -1 state.

        """
        return self._chain.target_device.run_fsm(name, command, events, transitions, timeout, max_transitions)

    def emit_message(self, message, log_level):
        """Call the msg callback function with the message."""
        self.log(message)
        if log_level == logging.ERROR:
            if self._error_msg_callback:
                self._error_msg_callback(message)
                return

        if log_level == logging.WARNING:
            if self._warning_msg_callback:
                self._warning_msg_callback(message)
                return

        if log_level == logging.INFO:
            if self._info_msg_callback:
                self._info_msg_callback(message)
                return

        if self._msg_callback:
            self._msg_callback(message)

    @property
    def msg_callback(self):
        """Return the message callback."""
        return self._msg_callback

    @msg_callback.setter
    def msg_callback(self, callback):
        """Set the message callback."""
        if callable(callback):
            self._msg_callback = callback
        else:
            self._msg_callback = None

    @property
    def error_msg_callback(self):
        """Return the error message callback."""
        return self._error_msg_callback

    @error_msg_callback.setter
    def error_msg_callback(self, callback):
        """Set the error message callback."""
        if callable(callback):
            self._error_msg_callback = callback
        else:
            self._error_msg_callback = None

    @property
    def warning_msg_callback(self):
        """Return the warning message callback."""
        return self._warning_msg_callback

    @warning_msg_callback.setter
    def warning_msg_callback(self, callback):
        """Set the warning message callback."""
        if callable(callback):
            self._warning_msg_callback = callback
        else:
            self._warning_msg_callback = None

    @property
    def info_msg_callback(self):
        """Return the info message callback."""
        return self._info_msg_callback

    @info_msg_callback.setter
    def info_msg_callback(self, callback):
        """Set the info message callback."""
        if callable(callback):
            self._info_msg_callback = callback
        else:
            self._info_msg_callback = None

    @property
    def _chain(self):
        try:
            return self.connection_chains[self._last_chain_index]
        except TypeError:
            raise ConnectionError("Device not connected")

    @property
    def is_connected(self):
        """Return if target device is connected."""
        return self._chain.is_connected

    @property
    def is_discovered(self):
        """Return if target device is discovered."""
        return self._chain.is_discovered

    @property
    def is_console(self):
        """Return if target device is connected via console."""
        return self._chain.is_console

    @property
    def prompt(self):
        """Return target device prompt."""
        return self._chain.target_device.prompt

    @property
    def hostname(self):
        """Return target device hostname."""
        return self._chain.target_device.hostname

    @property
    def os_type(self):
        """Return the string representing the target device OS type.

        For example: IOS, XR, eXR. If not detected returns *None*
        """
        return self._chain.target_device.os_type

    @property
    def os_version(self):
        """Return the string representing the target device OS version.

        For example 5.3.1. If not detected returns *None*
        """
        return self._chain.target_device.os_version

    @property
    def family(self):
        """Return the string representing hardware platform family.

        For example: ASR9K, ASR900, NCS6K, etc.
        """
        return self._chain.target_device.family

    @property
    def platform(self):
        """Return the string representing hardware platform model.

        For example: ASR-9010, ASR922, NCS-4006, etc.
        """
        return self._chain.target_device.platform

    @property
    def mode(self):
        """Return the sting representing the current device mode.

        For example: Calvados, Windriver, Rommon.
        """
        return self._chain.target_device.driver.platform

    @property
    def name(self):
        """Return the chassis name."""
        return self._chain.target_device.udi['name']

    @property
    def description(self):
        """Return the chassis description."""
        return self._chain.target_device.udi['description']

    @property
    def pid(self):
        """Return the chassis PID."""
        return self._chain.target_device.udi['pid']

    @property
    def vid(self):
        """Return the chassis VID."""
        return self._chain.target_device.udi['vid']

    @property
    def sn(self):  # pylint: disable=invalid-name
        """Return the chassis SN."""
        return self._chain.target_device.udi['sn']

    @property
    def udi(self):
        """Return the dict representing the udi hardware record.

        Example::

            {
            'description': 'ASR-9904 AC Chassis',
            'name': 'Rack 0',
            'pid': 'ASR-9904-AC',
            'sn': 'FOX1830GT5W ',
            'vid': 'V01'
            }

        """
        return self._chain.target_device.udi

    @property
    def device_info(self):
        """Return the dict representing the target device info record.

        Example::

            {
            'family': 'ASR9K',
            'os_type': 'eXR',
            'os_version': '6.1.0.06I',
            'platform': 'ASR-9904'
            }

        """
        return self._chain.target_device.device_info

    @property
    def description_record(self):
        """Return dict describing :class:`condoor.Connection` object.

        Example::

            {'connections': [{'chain': [{'driver_name': 'eXR',
                             'family': 'ASR9K',
                             'hostname': 'vkg3',
                             'is_console': True,
                             'is_target': True,
                             'mode': 'global',
                             'os_type': 'eXR',
                             'os_version': '6.1.2.06I',
                             'platform': 'ASR-9904',
                             'prompt': 'RP/0/RSP0/CPU0:vkg3#',
                             'udi': {'description': 'ASR-9904 AC Chassis',
                                     'name': 'Rack 0',
                                     'pid': 'ASR-9904-AC',
                                     'sn': 'FOX2024GKDE ',
                                     'vid': 'V01'}}]},
                 {'chain': [{'driver_name': 'generic',
                             'family': None,
                             'hostname': '172.27.41.52:2045',
                             'is_console': None,
                             'is_target': True,
                             'mode': None,
                             'os_type': None,
                             'os_version': None,
                             'platform': None,
                             'prompt': None,
                             'udi': None}]}],
            'last_chain': 0}

        """
        if self.connection_chains:
            return {
                'connections': [{'chain': [device.device_info for device in chain.devices]}
                                for chain in self.connection_chains],
                'last_chain': self._last_chain_index,
            }
        else:
            raise ConnectionError("Device not connected")

    @description_record.setter
    def description_record(self, cdr):
        if cdr is None:
            cdr = self.description_record
            for chain, data in zip(self.connection_chains, cdr['connections']):
                chain.update(None)

            self.log("Connection information cleared")
            return

        try:
            for chain, data in zip(self.connection_chains, cdr['connections']):
                chain.update(data['chain'])
            self._last_chain_index = cdr['last_chain']
        except KeyError:
            self.log("Invalid connection information")

        self.log("Connection information updated from cache.")
