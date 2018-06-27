"""Provide the exception classes."""


class GeneralError(Exception):
    """General error."""

    def __init__(self, message=None, host=None):
        """Initialize the GeneralError object.

        Args:
            message (str): Custom message to be passed to the exceptions. Defaults to *None*.
                If *None* then the general class *__doc__* is used.
            host (str): Custom string which can be used to enhance the exception message by adding the "`host`: "
                prefix to the message string. Defaults to *None*. If `host` is *None* then message stays unchanged.
        """
        self.message = message
        self.hostname = str(host) if host else None

    def __str__(self):
        """Return string representing the exception."""
        message = self.message or self.__class__.__doc__
        return "{}: {}".format(self.hostname, message) if self.hostname else message


class InvalidHopInfoError(GeneralError):
    """Invalid device connection parameters."""


class ConnectionError(GeneralError):
    """General connection error."""


class ConnectionAuthenticationError(ConnectionError):
    """Connection authentication error."""


class ConnectionTimeoutError(ConnectionError):
    """Connection timeout error."""


class ConnectionStandbyConsole(ConnectionError):
    """Connected to standby console."""


class CommandError(GeneralError):
    """Command execution error."""

    def __init__(self, message=None, host=None, command=None):
        """Initialize CommandError object.

        Args:
            message (str): Custom message to be passed to the exceptions. Defaults to *None*.
                If *None* then the general class *__doc__* is used.
            host (str): Custom string which can be used to enhance the exception message by adding the "`host`: "
                prefix to the message string. Defaults to *None*. If `host` is *None* then message stays unchanged.
            command (str): Custom string which can be used enhance the exception message by adding the
                "`command`" suffix to the message string. Defaults to *None*. If `command` is *None* then the message
                stays unchanged.
        """
        GeneralError.__init__(self, message, host)
        self.command = command

    def __str__(self):
        """Return string representative of the exception."""
        message = self.message or self.__class__.__doc__
        message = "{}: '{}'".format(message, self.command) \
            if self.command else message
        message = "{}: {}".format(self.hostname, message) \
            if self.hostname else message
        return message


class CommandSyntaxError(CommandError):
    """Command syntax error."""

    pass


class CommandTimeoutError(CommandError):
    """Command timeout error."""

    pass


class ConfigurationErrors(GeneralError):
    """Configuration errors."""

    pass


class ConfigurationSemanticErrors(GeneralError):
    """Configuration semantic errors."""

    pass
