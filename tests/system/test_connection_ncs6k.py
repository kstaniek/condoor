from tests.system.common import CondoorTestCase, StopTelnetSrv, StartTelnetSrv
from tests.dmock.dmock import NCS6KHandler
from tests.utils import remove_cache_file

import condoor


class TestNCS6KConnection(CondoorTestCase):
    @StartTelnetSrv(NCS6KHandler, 10033)
    def setUp(self):
        CondoorTestCase.setUp(self)

    @StopTelnetSrv()
    def tearDown(self):
        pass

    def test_NCS6K_1_discovery(self):

        remove_cache_file()

        urls = ["telnet://admin:admin@127.0.0.1:10033"]
        conn = condoor.Connection("host", urls, log_session=self.log_session, log_level=self.log_level)
        self.conn = conn
        conn.connect(self.logfile_condoor)
        print(conn.device_info)

        self.assertEqual(conn.is_discovered, True, "Not discovered properly")
        self.assertEqual(conn.hostname, "sdr_1", "Wrong Hostname: {}".format(conn.hostname))
        self.assertEqual(conn.family, "NCS6K", "Wrong Family: {}".format(conn.family))
        self.assertEqual(conn.platform, "PROTO-L-CHASSIS", "Wrong Platform: {}".format(conn.platform))
        self.assertEqual(conn.os_type, "eXR", "Wrong OS Type: {}".format(conn.os_type))
        self.assertEqual(conn.os_version, "6.5.1.09I", "Wrong Version: {}".format(conn.os_version))
        self.assertEqual(conn.udi['name'], "Rack 0", "Wrong Name: {}".format(conn.udi['name']))
        self.assertEqual(conn.udi['description'], "Cisco PANINI Proto 8-Slots Line Card Chassis",
                         "Wrong Description: {}".format(conn.udi['description']))
        self.assertEqual(conn.udi['pid'], "PROTO-L-CHASSIS", "Wrong PID: {}".format(conn.udi['pid']))
        self.assertEqual(conn.udi['vid'], "V01", "Wrong VID: {}".format(conn.udi['vid']))
        self.assertEqual(conn.udi['sn'], "FMP12180270", "Wrong S/N: {}".format(conn.udi['sn']))
        self.assertEqual(conn.prompt, "RP/0/RP0/CPU0:sdr_1#", "Wrong Prompt: {}".format(conn.prompt))
        with self.assertRaises(condoor.CommandSyntaxError):
            conn.send("wrongcommand")

        conn.disconnect()

    def test_NCS6K_2_rediscovery(self):

        urls = ["telnet://admin:admin@127.0.0.1:10033"]
        conn = condoor.Connection("host", urls, log_session=self.log_session, log_level=self.log_level)
        self.conn = conn
        conn.connect(self.logfile_condoor)
        print(conn.device_info)

        self.assertEqual(conn.is_discovered, True, "Not discovered properly")
        self.assertEqual(conn.hostname, "sdr_1", "Wrong Hostname: {}".format(conn.hostname))
        self.assertEqual(conn.family, "NCS6K", "Wrong Family: {}".format(conn.family))
        self.assertEqual(conn.platform, "PROTO-L-CHASSIS", "Wrong Platform: {}".format(conn.platform))
        self.assertEqual(conn.os_type, "eXR", "Wrong OS Type: {}".format(conn.os_type))
        self.assertEqual(conn.os_version, "6.5.1.09I", "Wrong Version: {}".format(conn.os_version))
        self.assertEqual(conn.udi['name'], "Rack 0", "Wrong Name: {}".format(conn.udi['name']))
        self.assertEqual(conn.udi['description'], "Cisco PANINI Proto 8-Slots Line Card Chassis",
                         "Wrong Description: {}".format(conn.udi['description']))
        self.assertEqual(conn.udi['pid'], "PROTO-L-CHASSIS", "Wrong PID: {}".format(conn.udi['pid']))
        self.assertEqual(conn.udi['vid'], "V01", "Wrong VID: {}".format(conn.udi['vid']))
        self.assertEqual(conn.udi['sn'], "FMP12180270", "Wrong S/N: {}".format(conn.udi['sn']))
        self.assertEqual(conn.prompt, "RP/0/RP0/CPU0:sdr_1#", "Wrong Prompt: {}".format(conn.prompt))
        with self.assertRaises(condoor.CommandSyntaxError):
            conn.send("wrongcommand")

        conn.disconnect()

    def test_NCS6K_3_connection_wrong_user(self):
        urls = ["telnet://root:admin@127.0.0.1:10033"]
        self.conn = condoor.Connection("host", urls, log_session=self.log_session, log_level=self.log_level)

        with self.assertRaises(condoor.ConnectionAuthenticationError):
            self.conn.connect(self.logfile_condoor)

        self.conn.disconnect()


if __name__ == '__main__':
    from unittest import main
    main()
