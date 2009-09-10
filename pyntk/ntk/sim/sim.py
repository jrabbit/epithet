##
# This file is part of Netsukuku
# (c) Copyright 2007 Andrea Lo Pumo aka AlpT <alpt@freaknet.org>
#
# This source code is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License as published 
# by the Free Software Foundation; either version 2 of the License,
# or (at your option) any later version.
#
# This source code is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
# Please refer to the GNU Public License for more details.
#
# You should have received a copy of the GNU Public License along with
# this source code; if not, write to:
# Free Software Foundation, Inc., 675 Mass Ave, Cambridge, MA 02139, USA.
##

from heapq import heappush, heappop
from ntk.lib.micro import Channel, microfunc

class SimEvent(object):
    __slots__ = [ 'exec_time', 'callf', 'callf_args', 'abs_time' ]

    def __init__(self, time, callf, callf_args):
        """time: how much time needs this event to be executed
           callf: the callback function called when the event is executed
           callf_args: arguments passed to callf"""
        self.callf     = callf
        self.callf_args= callf_args
        self.exec_time = time
        
        # absolute time: this is set when the event is added in the queue
        self.abs_time=0
    
    def __cmp__(self, other):
        return cmp(self.abs_time, other.abs_time)

class Simulator(object):
    """This is a Descrete Event Simulator
    
    *WARNING* 
    Inside a simulated function, do not directly use `channel.send' of
    python stackless, because you'll mess with the schedule of the simulator.
    Use instead @microfunc(true/false), or Channel(True) (see lib/micro.py)
    *WARNING* 
    """

    def __init__(self):
        self.curtime = 0
        self.queue = []

        self.looping = False
        self.simchan = Channel()
    
    def ev_add(self, ev):
        ev.abs_time = self.curtime+ev.exec_time
        heappush(self.queue, ev)
        if self.curtime > 0 and not self.looping:
                self.simchan.sendq(1)

    def ev_exec(self):
        ev = heappop(self.queue)
        self.curtime = ev.abs_time
        ev.callf(*ev.callf_args)

    def loop(self):
        self.looping = True
        while self.queue != []:
                self.ev_exec()
        self.looping = False

    def _wake_up_wait(self, chan):
        chan.send(())

    def wait(self, t):
        """Waits the specified number of time units"""
        chan = Channel()
        self.ev_add( SimEvent(t, self._wake_up_wait, (chan,)) )
        chan.recv()

    @microfunc(True)
    def run(self):
        while 1:
                self.loop()
                self.simchan.recvq()


#global instance of the simulator
cursim = None

def sim_activate():
    global cursim
    cursim = Simulator()
def sim_run():
    global cursim
    cursim.run()
