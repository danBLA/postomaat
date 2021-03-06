#   Copyright 2009-2018 Oli Schacher
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
#
#
#
from __future__ import print_function
import postomaat.logtools as logtools
from postomaat.addrcheck import Addrcheck
import multiprocessing
import multiprocessing.queues

from postomaat.scansession import SessionHandler
import postomaat.core
import logging
import traceback
from postomaat.stats import Statskeeper, StatDelta
import threading
import pickle
import signal

class ProcManager(object):
    def __init__(self, logQueue, numprocs = None, queuesize=100, config = None):
        self._child_id_counter=0
        self._logQueue = logQueue
        self.manager = multiprocessing.Manager()
        self.shared_state = self._init_shared_state()
        self.config = config
        self.numprocs = numprocs
        self.workers = []
        self.queuesize = queuesize
        self.tasks = multiprocessing.Queue(queuesize)
        self.child_to_server_messages = multiprocessing.Queue()

        self.logger = logging.getLogger('%s.procpool' % __package__)
        self._stayalive = True
        self.name = 'ProcessPool'
        self.message_listener = MessageListener(self.child_to_server_messages)
        self.start()

    def _init_shared_state(self):
        shared_state = self.manager.dict()
        return shared_state

    @property
    def stayalive(self):
        return self._stayalive

    @stayalive.setter
    def stayalive(self, value):
        # procpool is shut down -> send poison pill to workers
        if self._stayalive and not value:
            self._stayalive = False
            self._send_poison_pills()
        self._stayalive = value

    def _send_poison_pills(self):
        """flood the queue with poison pills to tell all workers to shut down"""
        for _ in range(len(self.workers)):
            # tasks queue is FIFO queue. As long as nothing is added to the queue
            # anymore the poison pills will be the last elements taken from the queue
            self.tasks.put_nowait(None)

    def add_task(self, session):
        if self._stayalive:
            self.tasks.put(session)

    def _create_worker(self):
        self._child_id_counter +=1
        worker_name = "Worker-%s"%self._child_id_counter
        self.logger.debug("Creating worker: "+worker_name)
        worker = multiprocessing.Process(target=postomaat_process_worker, name=worker_name, args=(self.tasks, self.config, self.shared_state, self.child_to_server_messages,self._logQueue))
        return worker

    def start(self):
        for i in range(self.numprocs):
            worker = self._create_worker()
            worker.start()
            self.workers.append(worker)

        # Start the child-to-parent message listener
        self.message_listener.start()

    def shutdown(self):
        # setting stayalive equal to False
        # will send poison pills to all processors
        self.logger.debug("Shutdown procpool -> send poison pills")
        self.stayalive = False

        # add another poison pill for the ProcManager itself removing tasks...
        self.tasks.put_nowait(None)

        returnMessage = "Temporarily unavailable... Please try again later."
        markDeferCounter = 0
        while True:
            task = self.tasks.get()
            if task is None: # poison pill
                break
            markDeferCounter += 1
            sock, handler_modulename, handler_classname = fuglu_process_unpack(task)
            handler_class = getattr(importlib.import_module(handler_modulename), handler_classname)
            handler_instance = handler_class(sock, self.config)
            handler_instance.defer(returnMessage)
            #handler = SessionHandler(handler_instance, self.config,None, None, None)
            #handler._defer("temporarily not available")
        self.logger.info("Marked %s messages as '%s' to close queue" % (markDeferCounter,returnMessage))

        # join the workers
        self.logger.debug("Join workers")
        for worker in self.workers:
            worker.join(120)

        self.logger.debug("Join message listener")
        self.message_listener.stayalive = False
        # put poison pill into queue otherwise the process will not stop
        # since "stayalive" is only checked after receiving a message from the queue
        self.child_to_server_messages.put_nowait(None)
        self.message_listener.join(120)
        self.logger.debug("Close tasks queue")
        self.tasks.close()

        self.child_to_server_messages.close()
        self.logger.debug("Shutdown multiprocessing manager")
        self.manager.shutdown()
        self.logger.debug("done...")

class MessageListener(threading.Thread):
    def __init__(self, message_queue):
        threading.Thread.__init__(self)
        self.name = "Process Message Listener"
        self.message_queue = message_queue
        self.stayalive = True
        self.statskeeper = Statskeeper()
        self.daemon = True


    def run(self):
        while self.stayalive:
            message = self.message_queue.get()
            if message is None:
                break
            event_type = message['event_type']
            if event_type == 'statsdelta': # increase statistics counters
                try:
                    delta = StatDelta(**message)
                    self.statskeeper.increase_counter_values(delta)
                except:
                    print(traceback.format_exc())


def postomaat_process_worker(queue, config, shared_state,child_to_server_messages,logQueue):

    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    logging.basicConfig(level=logging.DEBUG)
    workerstate = WorkerStateWrapper(shared_state,'loading configuration')
    logger = logging.getLogger('postomaat.process')

    # Setup address compliance checker
    # -> Due to default linux forking behavior this should already
    #    have the correct setup but it's better not to rely on this
    try:
        address_check = config.get('main','address_compliance_checker')
    except Exception as e:
        # might happen for some tests which do not propagate defaults
        address_check = "Default"
    Addrcheck().set(address_check)

    # load config and plugins
    controller = postomaat.core.MainController(config,logQueue)
    controller.load_plugins()

    plugins = controller.plugins

    # forward statistics counters to parent process
    stats = Statskeeper()
    stats.stat_listener_callback.append(lambda event: child_to_server_messages.put(event.as_message()))

    try:
        while True:
            workerstate.workerstate = 'waiting for task'
            logger.debug("%s: Child process waiting for task" % logtools.createPIDinfo())
            task = queue.get()
            if task is None: # poison pill
                logger.debug("%s: Child process received poison pill - shut down" % logtools.createPIDinfo())
                try:
                    # it might be possible it does not work to properly set the workerstate
                    # since this is a shared variable -> prevent exceptions
                    workerstate.workerstate = 'ended'
                except Exception as e:
                    pass
                finally:
                    return
            workerstate.workerstate = 'starting scan session'

            # recreate socket
            sock = pickle.loads(task)
            handler = SessionHandler(sock, config, plugins)
            handler.handlesession(workerstate)
    except KeyboardInterrupt:
        workerstate.workerstate = 'ended'
    except:
        trb = traceback.format_exc()
        logger.error("Exception in child process: %s"%trb)
        print(trb)
        workerstate.workerstate = 'crashed'
    finally:
        controller.shutdown()


class WorkerStateWrapper(object):
    def __init__(self, shared_state_dict, initial_state='created', process=None):
        self._state = initial_state
        self.shared_state_dict = shared_state_dict
        self.process = process
        if not process:
            self.process = multiprocessing.current_process()

        self._publish_state()

    def _publish_state(self):
        try:
            self.shared_state_dict[self.process.name] = self._state
        except EOFError:
            pass

    @property
    def workerstate(self):
        return self._state

    @workerstate.setter
    def workerstate(self, value):
        self._state = value
        self._publish_state()
