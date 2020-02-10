#!/usr/bin/python

"""
HLDSWatch (v2.0)
HLDS monitoring and restarter script

This script is released under the GPL (http://www.gnu.org/licenses/gpl.html)
Copyright 2012 blitzbite

Contact: http://steamcommunity.com/id/blitzbite
"""

import ConfigParser
import os.path, re, socket
from os import geteuid, devnull
from subprocess import call
from sys import argv, exit, version_info
from time import strftime, sleep


# --- User preferences ---

# Delay (in sec) for status checks
check_interval = 300

# File logging
# To change log file location, enter the full path of desired location
# in log_filename eg /var/www/hldswatch.log
log_to_file    = 1
log_filename   = "hldswatch.log"

# --- End of user preferences ---


# Query constants
HEADER           = "\xFF\xFF\xFF\xFF"
A2A_PING         = "\x69\x00"
A2S_INFO         = "TSource Engine Query\x00"

QUERY_TIMEOUT    = 3
QUERY_RETRY      = 3
QUERY_RETRY_WAIT = 5


class HLDSWatch(object):

    def __init__(self, conf):
        if os.path.isfile(conf):
            self.c = ConfigParser.SafeConfigParser()
            self.c.readfp(open(conf))

        else:
            exit("Error: Config file given does not exist!")

        # Hold all server settings
        self.serverconfig = {}

        # Remember script's working dir
        self.cwdir = os.getcwd()

        # validate and cache all server settings
        self.validate_config()

    """Message logging"""
    def printlog(self, msg):
        cur_time = strftime("%m-%d %H:%M:%S")
        log_msg = "%s -> %s" % (cur_time, msg)

        print "%s" % log_msg

        if log_to_file and log_filename:
            with open(log_filename, 'a') as f:
                f.write(log_msg + '\n')

            f.close()


    """Parse and validate options in config file"""
    def validate_config(self):
        for sec in self.c.sections():
            if not re.match("^([0-9]{1,3}\.){3}[0-9]{1,3}:[0-9]+$", sec):
                exit('Error: "[%s]" is invalid section name. All section names must be in [<ip>:<port>] form' % sec)
            else:
                # engine value
                val_engine = self.c.get(sec, "engine")
                if val_engine == "goldsource":
                    val_engine = "goldsrc"

                if not val_engine:
                    exit("Error: [%s] 'engine' type is left out" % sec)
                elif not re.match("^(?:goldsrc|source)$", val_engine):
                    exit("Error: [%s] 'engine' is unknown and not supported" % sec)

                # autorestart value
                val_autorestart = self.c.get(sec, "autorestart")
                if not val_autorestart:
                    exit("Error: [%s] 'autorestart' is left out" % sec)
                elif re.match("^[Yy1]", val_autorestart[0]):
                    val_autorestart = True

                    # screen value
                    val_screen = self.c.get(sec, "screen")
                    if not val_screen:
                        exit("Error: [%s] 'screen' is required and cannot be left out when autorestart is enabled" % sec)
                    elif not re.match("^[A-Za-z0-9_]+$", val_screen):
                        exit("Error: [%s] 'screen' must contain only alphanumeric and underscore character" % sec)

                    # startdir value
                    val_startdir = self.c.get(sec, "startdir")
                    if not val_startdir:
                        exit("Error: [%s] 'startdir' is required and cannot be left out when autorestart is enabled" % sec)
                    elif not os.path.isdir(val_startdir):
                        exit("Error: [%s] 'startdir' path doesn't exist" % sec)
                    else:
                        if val_engine == "goldsrc" and not os.path.isfile(val_startdir + '/hlds_run'):
                            exit("Error: [%s] Can't find hlds_run in 'startdir'" % sec)
                        elif val_engine == "source" and not os.path.isfile(val_startdir + '/srcds_run'):
                            exit("Error: [%s] Can't find srcds_run in 'startdir'" % sec)
                else:
                    val_autorestart = False
                    val_screen, val_startdir = None, None

                # command value
                val_command = self.c.get(sec, "command")
                if not val_command:
                    if val_autorestart:
                        exit("Error: [%s] 'command' is required and cannot be left out when autorestart is enabled" % sec)
                    else:
                        val_command = None

            # Cache all server specific configs
            self.serverconfig[sec] = {'engine' : val_engine,
                                 'autorestart' : val_autorestart,
                                      'screen' : val_screen,
                                     'command' : val_command,
                                    'startdir' : val_startdir}


    """Server status check"""
    def is_up(self, ip, port, engine):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(float(QUERY_TIMEOUT))

        if engine == "goldsrc":
            packet = HEADER + A2A_PING
        else:
            packet = HEADER + A2S_INFO

        retry, status = 0, 0
        reply = None
        while retry < QUERY_RETRY:
            try:
                # Send ping to server
                s.sendto(packet, (ip, int(port)))
                reply = s.recv(4096)

                if len(reply) > 4:
                    if engine == "goldsrc":
                        if reply[4] == "j":
                            status = 1
                            break
                    else:
                        if reply[4] == "I":
                            status = 1
                            break

            except socket.timeout:
                pass

            retry += 1
            sleep (QUERY_RETRY_WAIT)

        s.close()
        return status == 1


    """Restart server"""
    def relaunch(self, addr):
        # Get all settings that we need to restart
        screenname = self.serverconfig[addr]['screen']
        startcmd = self.serverconfig[addr]['command']
        path = self.serverconfig[addr]['startdir']

        # Screen command params
        screencmd = "-dmS %s " % screenname

        # CD into server dir
        try:
            os.chdir(path)
        except OSError:
            self.printlog("* Unable to cd into server dir '%s'" % path)
            return

        with open(os.devnull, "w") as blackhole:
            # In case server process is unresponsive or hung and doesn't quit itself after crashed
            call("screen -S %s -X quit" % screenname, stdout=blackhole, stderr=blackhole, shell=True)

            # Restart server process
            call("screen " + screencmd + startcmd, stdout=blackhole, stderr=blackhole, shell=True)

        # Go back to where we were
        os.chdir(self.cwdir)


    """Monitor servers"""
    def watch(self):
        # Here we go
        self.printlog("HLDSWatch started")
        self.printlog("Monitoring %i servers" % len(self.serverconfig))

        try:
            # Loop forever
            while True:
                for addr in self.serverconfig:
                    ip, port = addr.split(':')
                    if not self.is_up(ip, port, self.serverconfig[addr]['engine']):
                        self.printlog("%s is down" % addr)

                        # if autorestart enabled, restart the server
                        if self.serverconfig[addr]['autorestart'] == True:
                            # Try restart
                            self.relaunch(addr)

                            # Give some time for server to start up
                            sleep (5)

                            # Did it come back up?
                            if self.is_up(ip, port, self.serverconfig[addr]['engine']):
                                self.printlog("* Server restarted fine")
                            else:
                                self.printlog("* Attempt to restart failed")
                        elif self.serverconfig[addr]['command']:
                            # Execute user's custom command
                            with open(devnull, "w") as blackhole:
                                call(self.serverconfig[addr]['command'], stdout=blackhole, stderr=blackhole, shell=True)

                sleep(check_interval)

        except KeyboardInterrupt:
            self.printlog("HLDSWatch terminated")


if __name__ == '__main__':
    # Idiot check..never run hlds/srcds as root!
    if os.geteuid() == 0:
        exit('Error: I have a bad feeling about this')

    # We need at least python 2.6 to run this script
    if version_info[:2] < (2,6):
        exit('Error: Your python version is too old! This script requires at least python 2.6.x or newer')

    # Must give a config file
    if len(argv) != 2:
        exit("Usage: ./hldswatch.py <configfile>")
    else:
        hlds = HLDSWatch(argv[1])
        hlds.watch()




# vim: tabstop=4:softtabstop=4:shiftwidth=4:expandtab
