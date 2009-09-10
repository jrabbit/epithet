##
# This file is part of Netsukuku
# (c) Copyright 2008 Andrea Lo Pumo aka AlpT <alpt@freaknet.org>
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

import sys
sys.path.append('../../')

from ntk.config import settings
settings.IP_VERSION = 4

import traceback
import random
from random import randint
import pdb

from ntk.sim.net import Net
import ntk.sim.sim as sim
from ntk.sim.wrap.sock import Sock
from socket import AF_INET, SOCK_DGRAM, SOCK_STREAM, SO_REUSEADDR, SOL_SOCKET
import ntk.sim.wrap.xtime as xtime
from ntk.lib.micro import micro, microfunc, allmicro_run
from ntk.network.inet import ip_to_str

random.seed(1)

N=Net()
N.net_file_load('net1')
N.net_dot_dump(open('net1.dot', 'w'))

@microfunc()
def echo_srv():
    """Standard echo server example"""
    host = ''
    port = 51423
    socket=Sock(N, N.net[0])
    s0=socket.socket(AF_INET, SOCK_DGRAM)
    s0.setsockopt(SOL_SOCKET, SO_REUSEADDR, 1)
    s0.bind((host, port))
    while 1:
            try:
                message, address = s0.recvfrom(8192)
                print "t:", xtime.time(), "Got data from %s: %s"%(address, message)
                s0.sendto('yo there ('+message+')', address)
            except:
                traceback.print_exc()

@microfunc()
def echo_client():
    socket=Sock(N, N.net[1])
    s1=socket.socket(AF_INET, SOCK_DGRAM)
    ip = ip_to_str(0)
    r=randint(0, 256)
    print "t:", xtime.time(), ("sending data %d to "+ip)%(r)
    s1.sendto('hey '+str(r), (ip, 51423))
    message, address = s1.recvfrom(8192)
    print "t:", xtime.time(), "got reply from %s: %s"%(address, message)

@microfunc(1)
def echo_client_II():
    socket=Sock(N, N.net[1])
    s1=socket.socket(AF_INET, SOCK_DGRAM)
    ip = ip_to_str(0)

    r=randint(0, 256)
    print "t:", xtime.time(), ("II sending data %d to "+ip)%(r)
    s1.connect((ip, 51423))
    s1.send('hey '+str(r))
    message = s1.recv(8192)
    print "t:", xtime.time(), "got reply from %s: %s"%(ip, message)


@microfunc(True)
def tcp_handler(conn, addr):
    print "t:", xtime.time(),"Got connection from",str(addr)
    while 1:
        try:
#               message = conn.recv(1)
                message = conn.recv(8192)
                if not message: break
                print "t:", xtime.time(), "Got data message: %s"%message
                conn.send('ACK')
        except:
                traceback.print_exc()
                raise
    print "t:", xtime.time(), addr, "connection closed"

@microfunc()
def tcp_echo_srv():
    """Standard tcp echo server example"""
    host = ''
    port = 51423
    socket=Sock(N, N.net[0])
    s0=socket.socket(AF_INET, SOCK_STREAM)
    s0.setsockopt(SOL_SOCKET, SO_REUSEADDR, 1)
    s0.bind((host, port))
    s0.listen(1)
    while 1: tcp_handler(*s0.accept())

@microfunc()
def tcp_echo_client():
    socket=Sock(N, N.net[1])
    s1=socket.socket(AF_INET, SOCK_STREAM)
    ip = ip_to_str(0)
    print "t:", xtime.time(), "waiting"
    xtime.swait(70)
    print "t:", xtime.time(), "connecting to "+ip
    s1.connect((ip, 51423))

    r=randint(0, 256)
    s1.send('Hallo, '+str(r))
    s1.send('How are you?')
    message = s1.recv(8192)
    print "t:", xtime.time(), "tcp server replied:", message


sim.sim_activate()

echo_srv()
echo_client()
echo_client_II()
echo_client()
echo_client()

tcp_echo_srv()
tcp_echo_client()
tcp_echo_client()

sim.sim_run()
allmicro_run()
