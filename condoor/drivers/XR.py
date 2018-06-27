"""This is IOS XR Classic driver implementation."""

from functools import partial
import re
import time
from condoor.drivers.generic import Driver as Generic
from condoor import pattern_manager, TIMEOUT, EOF, ConnectionAuthenticationError, ConnectionError, \
    CommandSyntaxError, ConfigurationErrors, ConnectionStandbyConsole
from condoor.fsm import FSM
from condoor.actions import a_reload_na, a_send, a_send_boot, a_reconnect, a_send_username, a_send_password,\
    a_message_callback, a_return_and_reconnect, a_not_committed, a_send_line, a_capture_show_configuration_failed, \
    a_configuration_inconsistency
from condoor.config import CONF


_C = CONF['driver']['XR']


class Driver(Generic):
    """This is a Driver class implementation for IOS XR Classic."""

    platform = 'XR'
    inventory_cmd = 'admin show inventory chassis'
    users_cmd = 'show users'
    target_prompt_components = ['prompt_dynamic', 'prompt_default', 'rommon', 'xml', 'qnx']
    prepare_terminal_session = ['terminal exec prompt no-timestamp', 'terminal len 0', 'terminal width 0']
    reload_cmd = 'admin reload location all'
    commit_cmd = 'commit label {}'
    rollback_cmd = 'rollback configuration {}'
    families = {
        "ASR9K": "ASR9K",
        "ASR-9": "ASR9K",
        "CRS": "CRS",
        "12": "XR12K",
        "XRv": "IOSXRv",
    }

    def __init__(self, device):
        """Initialize the IOS XR Classic driver object."""
        super(Driver, self).__init__(device)

    def update_driver(self, prompt):
        """Return driver name based on prompt analysis."""
        return pattern_manager.platform(prompt, ['XR', 'QNX'])

    def reload(self, reload_timeout, save_config):
        """Reload the device."""
        PROCEED = re.compile(re.escape("Proceed with reload? [confirm]"))
        CONTINUE = re.compile(re.escape("Do you wish to continue?[confirm(y/n)]"))
        DONE = re.compile(re.escape("[Done]"))
        CONFIGURATION_COMPLETED = re.compile("SYSTEM CONFIGURATION COMPLETED")
        CONFIGURATION_IN_PROCESS = re.compile("SYSTEM CONFIGURATION IN PROCESS")

        # CONSOLE = re.compile("ios con[0|1]/RS?P[0-1]/CPU0 is now available")
        CONSOLE = re.compile("con[0|1]/(?:RS?P)?[0-1]/CPU0 is now available")
        CONSOLE_STBY = re.compile("con[0|1]/(?:RS?P)?[0-1]/CPU0 is in standby")
        RECONFIGURE_USERNAME_PROMPT = "[Nn][Oo] root-system username is configured"
        ROOT_USERNAME_PROMPT = "Enter root-system username\: "
        ROOT_PASSWORD_PROMPT = "Enter secret( again)?\: "

        # BOOT=disk0:asr9k-os-mbi-6.1.1/0x100305/mbiasr9k-rsp3.vm,1; \
        # disk0:asr9k-os-mbi-5.3.4/0x100305/mbiasr9k-rsp3.vm,2;
        # Candidate Boot Image num 0 is disk0:asr9k-os-mbi-6.1.1/0x100305/mbiasr9k-rsp3.vm
        # Candidate Boot Image num 1 is disk0:asr9k-os-mbi-5.3.4/0x100305/mbiasr9k-rsp3.vm
        CANDIDATE_BOOT_IMAGE = "Candidate Boot Image num 0 is .*vm"
        NOT_COMMITTED = re.compile(re.escape("Some active software packages are not yet committed. Proceed?[confirm]"))
        RELOAD_NA = re.compile("Reload to the ROM monitor disallowed from a telnet line")
        #           0          1      2                3                   4                  5
        events = [RELOAD_NA, DONE, PROCEED, CONFIGURATION_IN_PROCESS, self.rommon_re, self.press_return_re,
                  #   6               7                   8                           9
                  CONSOLE, CONFIGURATION_COMPLETED, RECONFIGURE_USERNAME_PROMPT, ROOT_USERNAME_PROMPT,
                  #    10                    11              12     13          14           15
                  ROOT_PASSWORD_PROMPT, self.username_re, TIMEOUT, EOF, self.reload_cmd, CANDIDATE_BOOT_IMAGE,
                  #   16            17
                  NOT_COMMITTED, CONSOLE_STBY, CONTINUE]

        transitions = [
            (RELOAD_NA, [0], -1, a_reload_na, 0),
            (CONTINUE, [0], 0, partial(a_send, "y\r"), 0),
            # temp for testing
            (NOT_COMMITTED, [0], -1, a_not_committed, 10),
            (DONE, [0], 2, None, 120),
            (PROCEED, [2], 3, partial(a_send, "\r"), reload_timeout),
            # this needs to be verified
            (self.rommon_re, [0, 3], 3, partial(a_send_boot, "boot"), 600),
            (CANDIDATE_BOOT_IMAGE, [0, 3], 4, a_message_callback, 600),
            (CONSOLE, [0, 1, 3, 4], 5, None, 600),
            # This is required. Otherwise nothing more is displayed on the console
            (self.press_return_re, [5], 6, partial(a_send, "\r"), 300),
            # configure root username and password the same as used for device connection.
            (RECONFIGURE_USERNAME_PROMPT, [6, 7, 10], 8, None, 10),
            (ROOT_USERNAME_PROMPT, [8], 9, partial(a_send_username, self.device.node_info.username), 1),
            (ROOT_PASSWORD_PROMPT, [9], 9, partial(a_send_password, self.device.node_info.password), 1),
            (CONFIGURATION_IN_PROCESS, [6, 9], 10, None, 1200),
            (CONFIGURATION_COMPLETED, [10], -1, a_reconnect, 0),
            (CONSOLE_STBY, [4], -1, ConnectionStandbyConsole("Standby Console"), 0),
            (self.username_re, [7, 9], -1, a_return_and_reconnect, 0),
            (TIMEOUT, [0, 1, 2], -1, ConnectionAuthenticationError("Unable to reload"), 0),
            (EOF, [0, 1, 2, 3, 4, 5], -1, ConnectionError("Device disconnected"), 0),
            (TIMEOUT, [6], 7, partial(a_send, "\r"), 180),
            (TIMEOUT, [7], -1, ConnectionAuthenticationError("Unable to reconnect after reloading"), 0),
            (TIMEOUT, [10], -1, a_reconnect, 0),

        ]

        fsm = FSM("RELOAD", self.device, events, transitions, timeout=600)
        return fsm.run()

    def config(self, config_text, plane):
        """Apply config."""
        NO_CONFIGURATION_CHANGE = re.compile("No configuration changes to commit")
        CONFIGURATION_FAILED = re.compile("show configuration failed")
        CONFIGURATION_INCONSITENCY = re.compile("No configuration commits for this SDR will be allowed until "
                                                "a 'clear configuration inconsistency' command is performed.")

        self.enter_plane(plane)

        nol = config_text.count('\n')
        config_lines = iter(config_text.splitlines())
        events = [self.prompt_re, self.syntax_error_re]
        transitions = [
            (self.prompt_re, [0], 0, partial(a_send_line, config_lines), 10),
            (self.syntax_error_re, [0], -1, CommandSyntaxError("Configuration syntax error."), 0)
        ]
        self.device.ctrl.send_command(self.config_cmd)
        fsm = FSM("CONFIG", self.device, events, transitions, timeout=10, max_transitions=nol + 5)
        fsm.run()

        events = [self.prompt_re, NO_CONFIGURATION_CHANGE, CONFIGURATION_FAILED]
        transitions = [
            (NO_CONFIGURATION_CHANGE, [0], -1, ConfigurationErrors("No configuration changes to commit."), 0),
            (CONFIGURATION_FAILED, [0], 2, a_capture_show_configuration_failed, 10),
            (CONFIGURATION_INCONSITENCY, [0], 2, a_configuration_inconsistency, 10),
            (self.prompt_re, [0], 1, partial(a_send_line, 'end'), 60),
            (self.prompt_re, [1], -1, None, 0)
        ]

        label = 'condoor-{}'.format(int(time.time()))
        self.device.ctrl.send_command(self.commit_cmd.format(label))
        fsm = FSM("COMMIT", self.device, events, transitions, timeout=120, max_transitions=5)
        fsm.run()
        self.exit_plane()
        return label

    def rollback(self, label, plane):
        """Rollback config."""
        cm_label = 'condoor-{}'.format(int(time.time()))
        self.device.send(self.rollback_cmd.format(label), timeout=120)
        return cm_label
