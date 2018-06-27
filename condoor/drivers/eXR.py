"""This is IOS XR 64 bit driver implementation."""

from functools import partial
import re
import pexpect

from condoor.exceptions import CommandSyntaxError, CommandTimeoutError, ConnectionError,\
    ConnectionAuthenticationError, CommandError, ConnectionStandbyConsole
from condoor.actions import a_connection_closed, a_expected_prompt, a_stays_connected, a_unexpected_prompt, a_send, \
    a_store_cmd_result, a_message_callback, a_send_line, a_send_boot, a_return_and_reconnect, \
    a_send_username, a_send_password
from condoor.utils import pattern_to_str
from condoor.fsm import FSM
from condoor.drivers.XR import Driver as XRDriver
from condoor import pattern_manager, EOF
from condoor.config import CONF

_C = CONF['driver']['eXR']


class Driver(XRDriver):
    """This is a Driver class implementation for IOS XR 64 bit."""

    platform = 'eXR'
    target_prompt_components = ['prompt_dynamic', 'prompt_default', 'rommon', 'xml']
    reload_cmd = 'admin hw-module location all reload'
    inventory_cmd = 'show inventory chassis'
    families = {
        "ASR9K": "ASR9K",
        "ASR-9": "ASR9K",
        "ASR9": "ASR9K",
        "NCS-6": "NCS6K",
        "NCS-4": "NCS4K",
        "NCS-50": "NCS5K",
        "NCS-55": "NCS5500",
        "NCS-54": "NCS540",
        "NCS-56": "NCS560",
        "NCS-1001": "NCS1001",
        "NCS-1": "NCS1K",
        "NCS1": "NCS1K",
        "IOS-XRv 9000": "IOSXRv-9K",
        "IOS XRv x64": "IOSXRv-X64",
    }

    def __init__(self, device):
        """Initialize the XR 64 bit Driver object."""
        super(Driver, self).__init__(device)
        self.calvados_re = pattern_manager.pattern(self.platform, 'calvados')
        self.calvados_connect_re = pattern_manager.pattern(self.platform, 'calvados_connect')
        self.calvados_term_length = pattern_manager.pattern(self.platform, 'calvados_term_length')

    def update_driver(self, prompt):
        """Return driver name based on prompt analysis."""
        return pattern_manager.platform(prompt, ['eXR', 'Calvados', 'Windriver'])

    def get_version_text(self):
        """Return version information text."""
        version_text = None
        try:
            version_text = self.device.send("show version", timeout=120)
        except CommandError as exc:
            exc.command = 'show version'
            raise exc

        return version_text

    def get_inventory_text(self):
        """Return the inventory information from the device."""
        inventory_text = None
        if self.inventory_cmd:
            try:
                # get to the admin plane
                self.enter_plane('admin')
                inventory_text = self.device.send(self.inventory_cmd, timeout=120)
                self.exit_plane()
                self.log('Inventory collected')
            except CommandError:
                self.log('Unable to collect inventory')
        else:
            self.log('No inventory command for {}'.format(self.platform))
        return inventory_text

    def wait_for_string(self, expected_string, timeout=60):
        """Wait for string FSM for XR 64 bit."""
        ADMIN_USERNAME_PROMPT = re.compile("Admin Username:")
        ADMIN_PASSWORD_PROMPT = re.compile("Password:")

        # Big thanks to calvados developers for make this FSM such complex ;-)
        #                    0                         1                        2                        3
        events = [self.syntax_error_re, self.connection_closed_re, self.authentication_error_re, expected_string,
                  #        4                  5                 6             7               8
                  self.press_return_re, self.more_re, pexpect.TIMEOUT, pexpect.EOF, self.calvados_re,
                  #     9                             10                  11                     12
                  self.calvados_connect_re, self.calvados_term_length, ADMIN_USERNAME_PROMPT, ADMIN_PASSWORD_PROMPT]

        # add detected prompts chain
        events += self.device.get_previous_prompts()  # without target prompt

        self.log("Expecting: {}".format(pattern_to_str(expected_string)))
        self.log("Calvados prompt: {}".format(pattern_to_str(self.calvados_re)))

        transitions = [
            (ADMIN_USERNAME_PROMPT, [0], 6, partial(a_send_username, self.device.node_info.username), 5),
            (ADMIN_PASSWORD_PROMPT, [0, 6], 0, partial(a_send_password, self.device.node_info.password), 5),
            (self.authentication_error_re, [0], -1,
             ConnectionAuthenticationError("Admin plane authentication failed", self.device.hostname), 0),
            (self.syntax_error_re, [0], -1, CommandSyntaxError("Command unknown", self.device.hostname), 0),
            (self.connection_closed_re, [0], 1, a_connection_closed, 10),
            (pexpect.TIMEOUT, [0, 2], -1, CommandTimeoutError("Timeout waiting for prompt", self.device.hostname), 0),
            (pexpect.EOF, [0, 1], -1, ConnectionError("Unexpected device disconnect", self.device.hostname), 0),
            (self.more_re, [0], 0, partial(a_send, " "), 10),
            (expected_string, [0, 1], -1, a_expected_prompt, 0),
            (self.calvados_re, [0], -1, a_expected_prompt, 0),
            (self.press_return_re, [0], -1, a_stays_connected, 0),
            (self.calvados_connect_re, [0], 2, None, 0),
            # admin command to switch to calvados
            (self.calvados_re, [2], 3, None, _C['calvados_term_wait_time']),
            # getting the prompt only
            (pexpect.TIMEOUT, [3], 0, partial(a_send, "\r\r"), timeout),
            # term len
            (self.calvados_term_length, [3], 4, None, 0),
            # ignore for command start
            (self.calvados_re, [4], 5, None, 0),
            # ignore for command start
            (self.calvados_re, [5], 0, a_store_cmd_result, 0),
        ]

        for prompt in self.device.get_previous_prompts():
            transitions.append((prompt, [0, 1], 0, a_unexpected_prompt, 0))

        fsm = FSM("WAIT-4-STRING", self.device, events, transitions, timeout=timeout)
        return fsm.run()

    def reload(self, reload_timeout, save_config):
        """Reload the device."""
        MAX_BOOT_TIME = 1800  # 30 minutes - TODO(klstanie): move to config
        RELOAD_PROMPT = re.compile(re.escape("Reload hardware module ? [no,yes]"))

        START_TO_BACKUP = re.compile("Status report.*START TO BACKUP")
        BACKUP_IN_PROGRESS = re.compile("Status report.*BACKUP INPROGRESS")

        BACKUP_HAS_COMPLETED_SUCCESSFULLY = re.compile("Status report.*BACKUP HAS COMPLETED SUCCESSFULLY")
        DONE = re.compile(re.escape("[Done]"))

        STAND_BY = re.compile("Please stand by while rebooting the system")
        CONSOLE = re.compile("con[0|1]/(?:RS?P)?[0-1]/CPU0 is now available")
        CONSOLE_STBY = re.compile("con[0|1]/(?:RS?P)?[0-1]/CPU0 is in standby")
        CONFIGURATION_COMPLETED = re.compile("SYSTEM CONFIGURATION COMPLETED")
        CONFIGURATION_IN_PROCESS = re.compile("SYSTEM CONFIGURATION IN PROCESS")
        BOOTING = re.compile("Booting IOS-XR 64 bit Boot previously installed image")

        #           0                 1              2                         3                          4     5
        events = [RELOAD_PROMPT, START_TO_BACKUP, BACKUP_IN_PROGRESS, BACKUP_HAS_COMPLETED_SUCCESSFULLY, DONE, BOOTING,
                  #   6                  7               8                     9                         10
                  CONSOLE, self.press_return_re, CONFIGURATION_COMPLETED, CONFIGURATION_IN_PROCESS, self.username_re,
                  # 11          12           13           14        15
                  EOF, pexpect.TIMEOUT, self.rommon_re, STAND_BY, CONSOLE_STBY]

        transitions = [
            # do I really need to clean the cmd
            (RELOAD_PROMPT, [0], 1, partial(a_send_line, "yes"), MAX_BOOT_TIME),
            (START_TO_BACKUP, [0, 1], 2, a_message_callback, 60),
            (BACKUP_IN_PROGRESS, [0, 1, 2], 2, a_message_callback, 90),
            (BACKUP_HAS_COMPLETED_SUCCESSFULLY, [0, 1, 2], 3, a_message_callback, 10),
            (DONE, [1, 2, 3], 4, None, MAX_BOOT_TIME),
            (STAND_BY, [2, 3, 4], 5, a_message_callback, MAX_BOOT_TIME),
            (self.rommon_re, [0, 4], 5, partial(a_send_boot, "boot"), MAX_BOOT_TIME),
            (BOOTING, [0, 1, 2, 3, 4], 5, a_message_callback, MAX_BOOT_TIME),
            (CONSOLE, [0, 1, 2, 3, 4, 5], 6, None, 600),
            (self.press_return_re, [6], 7, partial(a_send, "\r"), 300),
            (CONFIGURATION_IN_PROCESS, [7], 8, None, 180),
            (CONFIGURATION_COMPLETED, [8], -1, a_return_and_reconnect, 0),
            (CONSOLE_STBY, [5], -1, ConnectionStandbyConsole("Standby Console"), 0),
            (self.username_re, [9], -1, a_return_and_reconnect, 0),
            (EOF, [0, 1, 2, 3, 4, 5], -1, ConnectionError("Device disconnected"), 0),
            (pexpect.TIMEOUT, [7], 9, partial(a_send, "\r"), 180),
            (pexpect.TIMEOUT, [1, 5, 8], -1, ConnectionError("Boot process took more than {}s".format(MAX_BOOT_TIME)), 0),
            (pexpect.TIMEOUT, [9], -1, ConnectionAuthenticationError("Unable to reconnect after reloading"), 0)
        ]

        fsm = FSM("RELOAD", self.device, events, transitions, timeout=600)
        return fsm.run()
