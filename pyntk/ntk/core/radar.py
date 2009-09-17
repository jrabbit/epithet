##
# This file is part of Netsukuku
# (c) Copyright 2007 Alberto Santini <alberto@unix-monk.com>
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
#
# The radar sends in broadcast a bouquet of MAX_BOUQUET packets and waits
# for the reply of the alive nodes. It then recollects the replies and builds
# a small statistic.
# By watching the previous statistics, it can deduces if a change occurred or
# not. If it is, one of the following events is fired:
#        'NEIGH_NEW', 'NEIGH_DELETED', 'NEIGH_REM_CHGED'
# In this way, the other modules of pyntk will be noticed.
#
# A radar is fired periodically by Radar.run(), which is started as a
# microthread.
#
# TODO: the current statistic is based on the RTT (Round Trip Time)
# of the packets. However, more refined way can be used and shall be
# implemented: first of all consider the number of bouquet packets lost, then
# see NTK_RFC 0002  http://lab.dyne.org/Ntk_bandwidth_measurement
#
##



from random import randint

import time
import ntk.lib.rpc as rpc
import ntk.wrap.xtime as xtime

import ntk.lib.rpc as rpc
from ntk.config import settings
from ntk.core.route import DeadRem, Rtt
from ntk.lib.event import Event
from ntk.lib.log import logger as logging
from ntk.lib.micro import micro, micro_block, microfunc, Channel
from ntk.network.inet import ip_to_str, str_to_ip
from ntk.lib.log import get_stackframes


class Neigh(object):
    """This class simply represent a neighbour"""

    __slots__ = ['devs', 'bestdev', 'ip', 'nip', 'id', 'rem', 'ntkd', 'netid']

    def __init__(self, bestdev, devs, idn=None,
                 ip=None, netid=None, ntkd=None):
        """
        ip: neighbour's ip;
        netid: network id of the node
              ip + netid = unique key.
        nip: neighbour's nip;
        ntkd: neighbour's ntk remote instance
        idn: neighbour's id; use Neighbour.key_to_id to create it
        devs: a dict which maps a device to the average rtt
        bestdev: a pair (d, avg_rtt), where devs[d] is the best element of
                devs.
        """

        self.devs = devs
        self.bestdev = bestdev

        self.ip = ip
        self.nip = None   # TODO: refactoring Neigh
        self.id = idn
        if self.bestdev:
            # TODO(low): support the other metrics
            self.rem = Rtt(self.bestdev[1])
        else:
            self.rem = DeadRem() # The neighbour is dead
        self.ntkd = ntkd
        self.netid = netid

    def __cmp__(self, b):
        stacktrace = get_stackframes(back=1)
        logging.warning('Neigh.__cmp__ called at ' + stacktrace)
        return (self.ip > b.ip) - (self.ip < b.ip)

    def __repr__(self):
        if not self.ip: return object.__repr__(self)
        if not self.rem: return '<Neighbour(%s): No rem>' % (ip_to_str(self.ip))
        return '<Neighbour(%s):%s>' % (ip_to_str(self.ip), self.rem)

    def values(self):
        '''Returns a dict rappresentation of the neighbour

        self.ntkd is excluded: it's a TCPClient so is useless to perform
                               checks
        '''
        v = [(name, getattr(self, name)) for name in self.__slots__
             if name != 'ntkd']
        return dict(v)

class Neighbour(object):
    """ This class manages all neighbours """

    __slots__ = ['ntkd',
                 'max_neigh',
                 'rtt_variation_threshold',
                 'ip_netid_table',
                 'ntk_client',
                 'translation_table',
                 'events',
                 'remotable_funcs',
                 'xtime',
                 'netid',
                 'increment_wait_time',
                 'number_of_scan_before_deleting',
                 'missing_neighbour_keys',
                 'channels']

    def __init__(self, ntkd, max_neigh=settings.MAX_NEIGH, xtimemod=xtime):
        """  max_neigh: maximum number of neighbours we can have """
        self.ntkd = ntkd

        self.max_neigh = max_neigh
        # variation on neighbours' rtt greater than this will be notified
        # TODO changed to do less variations in netkit environment
        #self.rtt_variation_threshold = 0.1
        self.rtt_variation_threshold = 0.9
        # ip_netid_table
        self.ip_netid_table = {}
        # Remote client instances table
        self.ntk_client = {}  # ip : rpc.TCPClient(ipstr)
        # (ip, netid) => ID translation table
        self.translation_table = {}
        # TODO remove:
        ## IP => netid
        #self.netid_table = {}
        # the events we raise
        self.events = Event(['NEIGH_NEW', 'NEIGH_DELETED', 'NEIGH_REM_CHGED'])
        # time module
        self.xtime = xtimemod
        # channels
        self.channels = [None] * max_neigh

        # Our netid. It's a random id used to detect network collisions.
        self.netid = -1

        # To be certain, before deleting a neighbour, check a few times with
        # a greater delay.
        self.increment_wait_time = 1000
        self.number_of_scan_before_deleting = 3
        # This is a dict. The key is the neigh id, the value is missing_scans.
        # e.g. {2:4} means neighbour 2 has not replied for 4 consecutive
        # scans.
        self.missing_neighbour_keys = {}

        self.remotable_funcs = [self.ip_netid_change,
                                self.ip_netid_change_udp,
                                self.reply_ip_netid_change_udp]

    def neigh_list(self, in_my_network=False, out_of_my_network=False,
                         in_this_netid=None, out_of_this_netid=None):
        """ Returns the list of neighbours.
            If in_my_network == True, then returns only nodes
              that are in my network.
            Else, if out_of_my_network == True, then returns only nodes
              that are NOT in my network.
            Else, if in_this_netid is not None, then returns only nodes
              that are in the network with this netid.
            Else, if out_of_this_netid is not None, then returns only nodes
              that are NOT in the network with this netid.
            Else all the neighbours are returned.
            netid == -1 is a special case. It is not in any network.
            So, in_this_netid=-1 is a non-sense, but will simply return
            a void list.
            On the other hand, out_of_this_netid=-1 will return all the
            neighbours.
        """

        def in_my_network_requirement(netid):
            if netid == -1:
                return False
            return netid == self.netid
        def out_of_my_network_requirement(netid):
            if netid == -1:
                return True
            return netid != self.netid
        def in_this_netid_requirement(netid):
            if netid == -1:
                return False
            return netid == in_this_netid
        def out_of_this_netid_requirement(netid):
            if netid == -1:
                return True
            return netid != in_this_netid
        def no_requirements(netid):
            return True

        requirement = no_requirements
        if in_my_network:
            requirement = in_my_network_requirement
        elif out_of_my_network:
            requirement = out_of_my_network_requirement
        elif in_this_netid is not None:
            requirement = in_this_netid_requirement
        elif out_of_this_netid is not None:
            requirement = out_of_this_netid_requirement

        nlist = []
        for key, val in self.ip_netid_table.items():
            ip, netid = key
            if not requirement(netid):
                # this one is not wanted
                continue
            logging.log(logging.ULTRADEBUG, 'neigh_list: preparing Neigh for ' + ip_to_str(ip))
            nlist.append(Neigh(bestdev=val.bestdev,
                               devs=val.devs,
                               idn=self.translation_table[key],
                               ip=ip,
                               netid=netid,
                               ntkd=self.ntk_client[ip]))
        return nlist

    def key_to_id(self, key):
        """ key: pair ip, netid
            if key is in the translation table, return the associated id;
            if it isn't, insert it into the translation table assigning a new id,
            if the table isn't full
        """

        if key in self.translation_table:
            return self.translation_table[key]
        new_id = self._find_hole_in_tt()
        if new_id:
            self.translation_table[key] = new_id
            return new_id
        else:
            return False

    #############################################################
    ######## Synchronization gateway <-> nodes
    # Abstract:
    #
    # When Neighbour notice a new neighbour (gateway) it calls announce_gw.
    # Then, Neighbour emits event NEIGH_NEW which in turn does other
    # things...
    # When KrnlRoute actually has added the route towards the gateway,
    # it calls announce_gw_added.
    #
    # When Neighbour notice a dead link towards a neighbour it calls
    # announce_gw_removing.
    # Then, Neighbour emits event NEIGH_DELETED which in turn does other
    # things...
    # When MapRoute.routeneigh_del has finished deleting all the
    # routes passing through it, it calls announce_gw_removable.
    #
    # Before actually adding a route through a gateway we must
    # call waitfor_gw_added.
    # Before actually removing the route towards a gateway we
    # must call waitfor_gw_removable.
    #
    # This way we ensure to add a gateway before adding routes
    # through it, and to remove routes through a gateway before
    # removing the gateway itself.

    def announce_gw(self, gwid):
        self.channels[gwid-1] = Channel(prefer_sender=True)

    def waitfor_gw_added(self, gwid):
        channel = self.channels[gwid-1]
        if channel is None: return
        channel.recv()

    def announce_gw_added(self, gwid):
        channel = self.channels[gwid-1]
        if channel is None: return
        channel.bcast_send('')
        micro_block()
        self.channels[gwid-1] = None

    def announce_gw_removing(self, gwid):
        self.channels[gwid-1] = Channel(prefer_sender=True)

    def waitfor_gw_removable(self, gwid):
        channel = self.channels[gwid-1]
        if channel is None: return
        channel.recv()

    def announce_gw_removable(self, gwid):
        channel = self.channels[gwid-1]
        if channel is None: return
        channel.bcast_send('')
        micro_block()
        self.channels[gwid-1] = None

    ##
    #############################################################

    def key_to_neigh(self, key):
        """ key: neighbour's key, that is the pair ip, netid
            return a Neigh object from its ip and netid
        """
        if key not in self.translation_table:
            return None
        else:
            ip, netid = key
            return Neigh(bestdev=self.ip_netid_table[key].bestdev,
                         devs=self.ip_netid_table[key].devs,
                         idn=self.translation_table[key],
                         ip=ip,
                         netid=netid,
                         ntkd=self.ntk_client[ip])

    # TODO remove:
    def id_to_ip(self, id):
        """Returns the IP associated to `id'.
        If not found, returns None
        """
        stacktrace = get_stackframes(back=1)
        logging.warning('Neighbour.id_to_ip called at ' + stacktrace)
        for key in self.translation_table:
            if self.translation_table[key] == id:
                ip, netid = key
                return ip
        return None

    def id_to_key(self, id):
        """Returns the key (ip, netid) associated to `id'.
        If not found, returns None
        """
        for key in self.translation_table:
            if self.translation_table[key] == id:
                return key
        return None

    def id_to_neigh(self, id):
        """Returns a Neigh object from an id"""
        return self.key_to_neigh(self.id_to_key(id))

    def _truncate(self, ip_netid_table):
        """ip_netid_table: an {(ip, netid) => Neigh};
        we want the best (with the lowest rtt) max_neigh nodes only to
        remain in the table
        """

        # auxiliary function, to take rtt from {(ip, netid) => Neigh}
        def interesting(x):
            return x[1].bestdev[1]

        # the new table, without truncated rows
        ip_netid_table_trunc = {}

        counter = 0

        # we're cycling through ip_netid_table, ordered by rtt
        for key, val in sorted(ip_netid_table.items(), reverse=False, key=interesting):
            # if we haven't still reached max_neigh entries in the new ip_netid_table
            if counter < self.max_neigh:
                # add the current row into ip_netid_table
                ip_netid_table_trunc[key] = val
                counter += 1
            else:
                break

        # return the new ip_netid_table
        return ip_netid_table_trunc

    def _find_hole_in_tt(self):
        """Find the first available index in translation_table"""
        for i in xrange(1, self.max_neigh + 1):
            if i not in self.translation_table.values():
                return i
        return False

    # TODO remove:
    #def set_netid(self, ip, new_netid):
    #    """Updates our neighbour's network id."""
    #    if ip in self.netid_table:
    #        self.netid_table[ip] = new_netid

    def store(self, ip_netid_table):
        """Substitute the old ip_netid_table with the new and notify about the
        changes

        ip_netid_table: the new ip_netid_table;
        """

        ip_netid_table = self._truncate(ip_netid_table)

        # remove from missing_neighbour_keys the detected neighbours
        for key in ip_netid_table:
            if key in self.missing_neighbour_keys:
                del self.missing_neighbour_keys[key]

        # first of all we cycle through the old ip_netid_table
        # looking for nodes that aren't in the new one
        for key in self.ip_netid_table.keys():
            # if we find a row that isn't in the new ip_netid_table
            if not key in ip_netid_table:
                # It is a missing neigh. For how many scan is it missing?
                if key in self.missing_neighbour_keys:
                    times = self.missing_neighbour_keys[key] + 1
                else:
                    times = 1
                self.missing_neighbour_keys[key] = times

                if times > self.number_of_scan_before_deleting:
                    # now, we assume it is really dead.
                    self.delete(key)
                    del self.missing_neighbour_keys[key]
                else:
                    # pretend it is still there, for the moment.
                    ip_netid_table[key] = self.ip_netid_table[key]

        # now, update the ip_netid_table
        old_ip_netid_table = self.ip_netid_table
        self.ip_netid_table = ip_netid_table
        # first, for new neighs, update translation_table and ntk_client
        for key in self.ip_netid_table:
            if not key in old_ip_netid_table:
                # insert neigh id into translation_table
                self.key_to_id(key)
                # create a TCPClient which will be able to connect to the
                # neighbour
                ip, netid = key
                self.ntk_client[ip] = rpc.TCPClient(ip_to_str(ip))

        # now we cycle through the new ip_netid_table
        # looking for nodes who weren't in the old one
        # or whose rtt has sensibly changed
        for key in self.ip_netid_table:
            ip, netid = key
            # if a node has been added
            if not key in old_ip_netid_table:
                # info
                logging.info('Change in our LAN: new neighbour ' + ip_to_str(ip))
                self.add(key, already_in_ntk_client=True)
            else:
                # otherwise (if the node already was in old ip_netid_table) check if
                # its rtt has changed more than rtt_variation
                new_rtt = self.ip_netid_table[key].bestdev[1]
                old_rtt = old_ip_netid_table[key].bestdev[1]
                # using the following algorithm, we accomplish this:
                #  e.g. rtt_variation_threshold = .5, we want to be warned when
                #       rtt become more than double of previous or less than half of previous.
                #  from 200 to 400+ we get warned, because (400 - 200) / 400 = 0.5
                #  from 400 to 200- we get warned, because (400 - 200) / 400 = 0.5
                rtt_variation = 0
                if new_rtt > old_rtt:
                    rtt_variation = (new_rtt - old_rtt) / float(new_rtt)
                else:
                    rtt_variation = (old_rtt - new_rtt) / float(old_rtt)
                if rtt_variation > self.rtt_variation_threshold:
                    # info
                    logging.info('Change in our LAN: changed REM for neighbour ' + ip_to_str(ip))
                    # send a message notifying the node's rtt changed
                    self.events.send('NEIGH_REM_CHGED',
                                     (Neigh(bestdev=self.ip_netid_table[key].bestdev,
                                            devs=self.ip_netid_table[key].devs,
                                            idn=self.translation_table[key],
                                            ip=ip,
                                            netid=netid,
                                            ntkd=self.ntk_client[ip]), 
                                      Rtt(old_rtt)))
                else:
                    self.ip_netid_table[key] = old_ip_netid_table[key]
                # TODO better handling of different devs to reach the same neighbour

        # returns an indication for next wait time to the radar
        if not self.missing_neighbour_keys:
            return 0
        return max(self.missing_neighbour_keys.values()) * self.increment_wait_time

    def readvertise(self):
        """Sends a NEIGH_NEW event for each stored neighbour"""
        # info
        logging.info('Readvertising all my neighbours')
        for key in self.ip_netid_table:
            idn = self.translation_table[key]
            logging.debug('ANNOUNCE: gw ' + str(idn) + ' detected.')
            self.announce_gw(idn)
            ip, netid = key
            self.events.send('NEIGH_NEW',
                             (Neigh(bestdev=self.ip_netid_table[key].bestdev,
                                    devs=self.ip_netid_table[key].devs,
                                    idn=idn,
                                    ip=ip,
                                    netid=netid,
                                    ntkd=self.ntk_client[ip]),))

    def reset_ntk_clients(self):
        """Reset connected TCPClients. To be used after hooking, to avoid
           using invalid sockets."""
        for key in self.ip_netid_table:
            ip, netid = key
            if self.ntk_client[ip]:
                if self.ntk_client[ip].connected:
                    self.ntk_client[ip].close()

    def add(self, key, already_in_ntk_client=True):
        """Sends event for a new neighbour.
        The entry already exists in ip_netid_table.
        The entry might already exist in translation_table and ntk_client."""

        ip, netid = key
        logging.info("Adding neighbour %s", ip_to_str(ip))

        # insert/recover neigh id into translation_table
        idn = self.key_to_id(key)
        if not already_in_ntk_client:
            # create a TCPClient which will be able to connect to the
            # neighbour
            self.ntk_client[ip] = rpc.TCPClient(ip_to_str(ip))
        # send a message notifying we added a node
        logging.debug('ANNOUNCE: gw ' + str(idn) + ' detected.')
        self.announce_gw(idn)
        self.events.send('NEIGH_NEW',
                         (Neigh(bestdev=self.ip_netid_table[key].bestdev,
                                devs=self.ip_netid_table[key].devs,
                                idn=idn,
                                ip=ip,
                                netid=netid,
                                ntkd=self.ntk_client[ip]),))

    def delete(self, key):
        """Sends event for a dead neighbour.
        Removes its entry from the ip_netid_table."""

        ip, netid = key
        logging.info("Deleting neighbour %s", ip_to_str(ip))

        old_bestdev = self.ip_netid_table[key].bestdev
        old_devs = self.ip_netid_table[key].devs
        del self.ip_netid_table[key]
        logging.log(logging.ULTRADEBUG, 'removed from ip_netid_table ' + \
                    ip_to_str(ip) + ', ' + str(netid))

        # close the connection ( if any )
        if self.ntk_client[ip].connected:
            self.ntk_client[ip].close()

        # delete the entry from the translation table
        old_id = self.translation_table.pop(key)
        # info
        logging.info('Change in our LAN: removed neighbour ' + ip_to_str(ip))
        # send a message notifying we deleted the entry
        logging.debug('ANNOUNCE: gw ' + str(old_id) + ' removing.')
        self.announce_gw_removing(old_id)
        self.events.send('NEIGH_DELETED',
                         (Neigh(bestdev=old_bestdev,
                                devs=old_devs,
                                idn=old_id,
                                ip=ip,
                                netid=netid,
                                ntkd=self.ntk_client[ip]),))

        del self.ntk_client[ip]

    @microfunc()
    def ip_netid_change(self, oldip, oldnetid, newip, newnetid):
        """Adds `newip' in the Neighbours as a copy of `oldip', then it removes
        `oldip'. The relative events are raised."""

        oldkey = (oldip, oldnetid)
        newkey = (newip, newnetid)
        if not oldkey in self.ip_netid_table:
            # probably our radar did not observed previously the ip that is changing,
            # then leave this work to the next radar scan
            return

        # info
        logging.info('Change in our LAN: new neighbour ' + ip_to_str(newip) + ' in ' + str(newnetid))
        logging.info('                   replacing an old one... ' + ip_to_str(oldip) + ' in ' + str(oldnetid))
        # copy values from old ip_netid_table
        copy_devs = self.ip_netid_table[oldkey]
        # recover (prior to delete) the id of the gateway
        old_id = self.translation_table[oldkey]
        # delete old ip gateway
        logging.log(logging.ULTRADEBUG, 'ip_netid_change: deleting...')
        self.delete(oldkey)
        # Attention: this removes "oldkey" from self.ip_netid_table. But current radar
        # scan might already have put this "oldkey" in bcast_arrival_time. So...
        if oldkey in self.ntkd.radar.bcast_arrival_time:
            logging.log(logging.ULTRADEBUG, 'ip_netid_change: removing from scan...')
            del self.ntkd.radar.bcast_arrival_time[oldkey]
        # wait for the removing of routes to be completed
        logging.log(logging.ULTRADEBUG, 'ip_netid_change: waiting routemap.routeneigh_del...')
        self.waitfor_gw_removable(old_id)
        # copy values into new ip_netid_table
        self.ip_netid_table[newkey] = copy_devs
        # add new ip gateway
        logging.log(logging.ULTRADEBUG, 'ip_netid_change: adding...')
        self.add(newkey, already_in_ntk_client=False)
        logging.log(logging.ULTRADEBUG, 'ip_netid_change: done.')

    def call_ip_netid_change_udp(self, neigh, oldip, oldnetid, newip, newnetid):
        """Use BcastClient to call ip_netid_change"""
        devs = [neigh.bestdev[0]]
        nip = self.ntkd.maproute.ip_to_nip(neigh.ip)
        netid = neigh.netid
        return rpc.UDP_call(nip, netid, devs, 'neighbour.ip_netid_change_udp', (oldip, oldnetid, newip, newnetid))

    def ip_netid_change_udp(self, _rpc_caller, caller_id, callee_nip, callee_netid, oldip, oldnetid, newip, newnetid):
        """Returns the result of ip_netid_change to remote caller.
           caller_id is the random value generated by the caller for this call.
            It is replied back to the LAN for the caller to recognize a reply destinated to it.
           callee_nip is the NIP of the callee;
           callee_netid is the netid of the callee.
            They are used by the callee to recognize a request destinated to it.
           """
        if self.ntkd.maproute.me == callee_nip and self.netid == callee_netid:
            self.ip_netid_change(oldip, oldnetid, newip, newnetid)
            # Since it is micro, I will reply None
            rpc.UDP_send_reply(_rpc_caller, caller_id, 'neighbour.reply_ip_netid_change_udp', None)

    def reply_ip_netid_change_udp(self, _rpc_caller, caller_id, ret):
        """Receives reply from ip_netid_change_udp."""
        rpc.UDP_got_reply(_rpc_caller, caller_id, ret)

    def is_neigh_in_my_network(self, neigh):
        """Returns True if the passed Neigh is in my network."""
        return neigh.netid == self.netid


class Radar(object):
    
    __slots__ = [ 'ntkd', 'bouquet_numb', 'bcast_send_time', 'xtime',
                  'bcast_arrival_time', 'max_bouquet', 'wait_time', 
                  'broadcast', 'neigh', 'events', 'do_reply', 
                  'remotable_funcs', 'ntkd_id', 'radar_id', 'max_neigh',
                  'increment_wait_time', 'stopping', 'running_instances']

    def __init__(self, ntkd, broadcast, xtime):
        """
            broadcast: an instance of the RPCBroadcast class to manage broadcast
            sending xtime: a wrap.xtime module
        """
        self.ntkd = ntkd

        self.xtime = xtime
        self.broadcast = broadcast

        # how many bouquet we have already sent
        self.bouquet_numb = 0
        # when we sent the broadcast packets
        self.bcast_send_time = 0
        # when the replies arrived
        self.bcast_arrival_time = {}
        # max_bouquet: how many packets does each bouquet contain?
        self.max_bouquet = settings.MAX_BOUQUET
        # wait_time: the time we wait for a reply, in seconds
        self.wait_time = settings.RADAR_WAIT_TIME
        # max_neigh: maximum number of neighbours we can have
        self.max_neigh = settings.MAX_NEIGH
        # our neighbours
        self.neigh = Neighbour(self.ntkd, self.max_neigh, self.xtime)
        # Do I have to wait longer? in millis
        self.increment_wait_time = 0

        # Send a SCAN_DONE event each time a sent bouquet has been completely
        # collected
        self.events = Event(['SCAN_DONE'])

        # If set to True, this module will reply to radar queries sent by our
        # neighbours.
        self.do_reply = False

        self.stopping = False
        self.running_instances = []

        self.remotable_funcs = [self.reply, self.time_register]

        # this is needed to avoid to answer to myself
        self.ntkd_id = randint(0, 2**32-1)

    def run(self):
        # Before launching the microfunc,
        # make sure to note down that radar.run has been launched.

        # There should always be just one instance
        if self.running_instances:
            raise Exception('An instance of Radar is already running')

        self.running_instances.append(1)
        self._run()

    @microfunc(True)
    def _run(self):
        while not self.stopping:
            self.radar()
        self.running_instances.remove(1)
        if not self.running_instances:
            self.stopping = False

    def stop(self, event_wait = None):
        """ Stop the radar scanner """
        if self.running_instances:
            self.stopping = True
            while self.stopping:
                time.sleep(0.005)
                micro_block()

    def radar(self):
        """ Send broadcast packets and store the results in neigh """

        self.radar_id = randint(0, 2**32-1)
        logging.debug('radar scan %s' % self.radar_id)
        logging.debug('My netid is ' + str(self.neigh.netid))
        # TODO remove:
        #logging.debug('I know these netids ' + str(self.neigh.netid_table))

        # we're sending the broadcast packets NOW
        self.bcast_send_time = self.xtime.time()

        # send all packets in the bouquet
        for i in xrange(self.max_bouquet):
            self.broadcast.radar.reply(self.ntkd_id, self.radar_id)

        # then wait
        self.xtime.swait(self.wait_time * 1000 + self.increment_wait_time)

        # test wether we are stopping
        if not self.stopping:
            # update the neighbours' ip_netid_table
            # Note: the neighbour manager will tell me if I have to wait
            #       longer than usual at the next scan.
            self.increment_wait_time = self.neigh.store(self.get_all_avg_rtt())

            # Send the event
            self.bouquet_numb += 1
            self.events.send('SCAN_DONE', (self.bouquet_numb,))

        # We're done. Reset.
        self.radar_reset()

    def radar_reset(self):
        ''' Clean the objects needed by radar()'''
        # Clean some stuff
        self.bcast_arrival_time = {}

        # Reset the broadcast sockets
        self.broadcast.reset()

    def reply(self, _rpc_caller, ntkd_id, radar_id):
        """ As answer we'll return our netid """
        if self.do_reply and ntkd_id != self.ntkd_id:
            bcc = rpc.BcastClient(devs=[_rpc_caller.dev], xtimemod=self.xtime)
            try:
                bcc.radar.time_register(radar_id, self.neigh.netid)
            except:
                logging.log(logging.ULTRADEBUG, 'Radar: Reply: BcastClient ' + str(bcc) + ' with dispatcher ' + repr(bcc.dev_sk[_rpc_caller.dev].dispatcher) + ' error in rpc execution. Ignored.')

    def time_register(self, _rpc_caller, radar_id, netid):
        """save each node's rtt"""

        if radar_id != self.radar_id:
            # drop. It isn't a reply to our current bouquet
            return

        ip = str_to_ip(_rpc_caller.ip)
        net_device = _rpc_caller.dev

        # this is the rtt
        time_elapsed = int((self.xtime.time() - self.bcast_send_time) / 2)
        # let's store it in the bcast_arrival_time table
        if (ip, netid) in self.bcast_arrival_time:
            if net_device in self.bcast_arrival_time[(ip, netid)]:
                self.bcast_arrival_time[(ip, netid)][net_device].append(time_elapsed)
            else:
                self.bcast_arrival_time[(ip, netid)][net_device] = [time_elapsed]
        else:
            self.bcast_arrival_time[(ip, netid)] = {}
            self.bcast_arrival_time[(ip, netid)][net_device] = [time_elapsed]
            logging.debug("Radar: IP %s from network %s detected", ip_to_str(ip), str(netid))

        # TODO remove: self.neigh.netid_table[ip] = netid


    def get_avg_rtt(self, ip, netid):
        """ ip: ip of the neighbour;
            netid: netid of the neighbour;
            Calculates the average rtt of the neighbour for each device

            Returns the ordered list [(dev, avgrtt)], the first element has
            the best average rtt.
        """

        devlist = []

        # for each NIC
        for dev in self.bcast_arrival_time[(ip, netid)]:
            avg = sum(self.bcast_arrival_time[(ip, netid)][dev]) / len(self.bcast_arrival_time[(ip, netid)][dev])
            devlist.append( (dev, avg) )

        # sort the devices, the best is the first
        def second_element((x,y)): return y
        devlist.sort(key=second_element)

        return devlist

    def get_all_avg_rtt(self):
        """ Calculate the average rtt of all the neighbours """

        all_avg = {}
        # for each (ip, netid)
        for (ip, netid) in self.bcast_arrival_time:
            devs = self.get_avg_rtt(ip, netid)
            all_avg[(ip, netid)] = Neigh(bestdev=devs[0], devs=dict(devs), ip=ip, netid=netid)
        return all_avg
