# Copyright 2017, David Wilson
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice,
# this list of conditions and the following disclaimer.
#
# 2. Redistributions in binary form must reproduce the above copyright notice,
# this list of conditions and the following disclaimer in the documentation
# and/or other materials provided with the distribution.
#
# 3. Neither the name of the copyright holder nor the names of its contributors
# may be used to endorse or promote products derived from this software without
# specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

import sys
import threading

import mitogen.core
import mitogen.master
from mitogen.core import LOG


class Service(object):
    #: If ``None``, a handle is dynamically allocated, otherwise the fixed
    #: integer handle to use.
    handle = None
    max_message_size = 0

    def __init__(self, router):
        self.router = router
        self.recv = mitogen.core.Receiver(router, self.handle)
        self.recv.service = self
        self.handle = self.recv.handle
        self.running = True

    def validate_args(self, args):
        return True

    def dispatch_one(self, msg):
        if len(msg.data) > self.max_message_size:
            LOG.error('%r: larger than permitted size: %r', self, msg)
            msg.reply(mitogen.core.CallError('Message size exceeded'))
            return

        args = msg.unpickle(throw=False)
        if  (args == mitogen.core._DEAD or
             isinstance(args, mitogen.core.CallError) or
             not self.validate_args(args)):
            LOG.warning('Received junk message: %r', args)
            return

        try:
            msg.reply(self.dispatch(args, msg))
        except Exception, e:
            LOG.exception('While invoking %r.dispatch()', self)
            msg.reply(mitogen.core.CallError(e))

    def run_once(self):
        try:
            msg = self.recv.get()
        except mitogen.core.ChannelError, e:
            # Channel closed due to broker shutdown, exit gracefully.
            LOG.debug('%r: channel closed: %s', self, e)
            self.running = False
            return

        self.dispatch_one(msg)

    def run(self):
        while self.running:
            self.run_once()


class Pool(object):
    def __init__(self, router, services, size=1):
        self.router = router
        self.services = list(services)
        self.size = size
        self._select = mitogen.master.Select(
            receivers=[
                service.recv
                for service in self.services
            ],
            oneshot=False,
        )
        self._threads = []
        for x in xrange(size):
            thread = threading.Thread(
                name='mitogen.service.Pool.%x.worker-%d' % (id(self), x,),
                target=self._worker_main,
            )
            thread.start()
            self._threads.append(thread)

    def stop(self):
        self._select.close()
        for th in self._threads:
            th.join()

    def _worker_run(self):
        while True:
            try:
                msg = self._select.get()
            except (mitogen.core.ChannelError, mitogen.core.LatchError):
                e = sys.exc_info()[1]
                LOG.error('%r: channel or latch closed, exitting: %s', self, e)
                return

            service = msg.receiver.service
            try:
                service.dispatch_one(msg)
            except Exception:
                LOG.exception('While handling %r using %r', msg, service)

    def _worker_main(self):
        try:
            self._worker_run()
        except Exception:
            th = threading.currentThread()
            LOG.exception('%r: worker %r crashed', self, th.name)
            raise

    def __repr__(self):
        th = threading.currentThread()
        return 'mitogen.service.Pool(%#x, size=%d, th=%r)' % (
            id(self),
            self.size,
            th.name,
        )


def call(context, handle, obj):
    msg = mitogen.core.Message.pickled(obj, handle=handle)
    recv = context.send_async(msg)
    return recv.get().unpickle()
