# -*- coding: UTF-8 -*-
#   Copyright 2012-2018 Oli Schacher
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from postomaat.shared import DUNNO, Suspect, DEFER, REJECT, DISCARD
import logging
import sys
import traceback
import time
import socket


class SessionHandler(object):

    """thread handling one message"""

    def __init__(self, incomingsocket, config, plugins):
        self.incomingsocket = incomingsocket
        self.logger = logging.getLogger("%s.SessionHandler" % __package__)
        self.action = DUNNO
        self.arg = ""
        self.config = config
        self.plugins = plugins
        self.workerthread = None
    
    def set_threadinfo(self, status):
        if self.workerthread is not None:
            self.workerthread.threadinfo = status
    
    def handlesession(self, workerthread=None):
        self.workerthread = workerthread
        sess = None
        try:
            self.set_threadinfo('receiving message')
            sess = PolicydSession(self.incomingsocket, self. config)
            success = sess. getrequest()
            if not success:
                self.logger.error('incoming request did not finish')
                sess.closeconn()

            values = sess.values
            suspect = Suspect(values)

            # store incoming port to tag, could be used to disable plugins
            # based on port
            try:
                port = sess.socket . getsockname()[1]
                if port is not None:
                    suspect.tags['incomingport'] = port
            except Exception as e:
                self.logger.warning('Could not get incoming port: %s' % str(e))

            self.set_threadinfo("Handling message %s" % suspect)
            starttime = time.time()
            self.run_plugins(suspect, self.plugins)

            # how long did it all take?
            difftime = time.time() - starttime
            suspect.tags['postomaat.scantime'] = "%.4f" % difftime

            # checks done.. print out suspect status
            self.logger.debug(suspect)
            self.set_threadinfo("Finishing message %s" % suspect)

        except KeyboardInterrupt:
            sys.exit(0)
        except ValueError:
            # Error in envelope send/receive address
            try:
                address_compliance_fail_action = self.config.get('main','address_compliance_fail_action').lower()
            except Exception:
                address_compliance_fail_action = "defer"

            try:
                message = self.config.get('main','address_compliance_fail_message')
            except Exception:
                message = "invalid sender or recipient address"

            if address_compliance_fail_action   == "defer":
                self.action = DEFER
            elif address_compliance_fail_action == "reject":
                self.action = REJECT
            elif address_compliance_fail_action == "discard":
                self.action = DISCARD
            else:
                self.action = DEFER
            self.arg = message

        except Exception as e:
            self.logger.exception(e)
        finally:
            if sess is not None:
                sess.endsession(self.action, self.arg)
            self.logger.debug('Session finished')

    def run_plugins(self, suspect, pluglist):
        """Run scannerplugins on suspect"""
        for plugin in pluglist:
            try:
                self.logger.debug('Running plugin %s' % plugin)
                self.set_threadinfo(
                    "%s : Running Plugin %s" % (suspect, plugin))
                ans = plugin.examine(suspect)
                arg = None
                if isinstance(ans, tuple):
                    result, arg = ans
                else:
                    result = ans

                if result is None:
                    result = DUNNO
                else:
                    result = result.strip().lower()
                self.action = result
                self.arg = arg
                suspect.tags['decisions'].append((str(plugin), result))
                self.logger.debug('Plugin sez: %s (arg=%s)' % (result, arg))

                if result != DUNNO:
                    self.logger.debug(
                        'Plugin makes a decision other than DUNNO - not running any other plugins')
                    break

            except Exception:
                exc = traceback. format_exc()
                self.logger.error('Plugin %s failed: %s' % (str(plugin), exc))


class PolicydSession(object):

    def __init__(self, socket, config):
        self.config = config

        self.socket = socket
        self.logger = logging.getLogger("%s.policysession" % __package__)
        self.file = self.socket.makefile('r')
        self.values = {}

    def endsession(self, action, arg):
        ret = action
        if arg is not None and arg.strip() != "":
            ret = "%s %s" % (action, arg.strip())
        self.socket.send(('action=%s\n\n' % ret).encode())
        self.closeconn()

    def closeconn(self):
        try:
            self.socket.shutdown(socket.SHUT_RDWR)
        except (OSError, socket.error):
            pass
        finally:
            self.socket.close()

    def getrequest(self):
        """return true if mail got in, false on error Session will be kept open"""
        while True:
            line = self.file.readline()
            line = line.strip()
            if line == '':
                return True
            try:
                key, val = line.split('=', 1)
                self.values[key] = val
            except Exception:
                self.logger.error('Invalid Protocol line: %s' % line)
                break

        return False
