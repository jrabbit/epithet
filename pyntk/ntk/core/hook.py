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

import time

from random import choice, randint

import ntk.lib.rpc as rpc
import ntk.wrap.xtime as xtime

from ntk.core.qspn import etp_exec_dispatcher_token
from ntk.lib.log import logger as logging
from ntk.lib.log import log_exception_stacktrace
from ntk.lib.micro import microfunc, Channel, micro_block
from ntk.lib.event import Event
from ntk.network.inet import ip_to_str, valid_ids
from ntk.core.status import ZombieException 

class Hook(object):

    def __init__(self, ntkd_status, radar, maproute, etp, coordnode, nics): 

        self.ntkd_status = ntkd_status
        self.radar = radar
        self.neigh = radar.neigh
        self.maproute = maproute
        self.etp = etp
        self.coordnode= coordnode
        self.nics = nics

        self.chan_replies = Channel()

        self.events = Event(['HOOKED', 'HOOKED2'])

        self.neigh.events.listen('TIME_TICK', self.communicating_vessels)
        self.etp.events.listen('TIME_TICK', self.communicating_vessels)
        self.etp.events.listen('NET_COLLISION', self.hook)

        self.events.listen('HOOKED2', self.neigh.readvertise_local)
        # This should be placed in module radar. TODO refactor.

        self.remotable_funcs = [self.communicating_vessels,
                                self.communicating_vessels_udp,
                                self.highest_free_nodes,
                                self.highest_free_nodes_udp]

    def call_communicating_vessels_udp(self, neigh):
        """Use BcastClient to call etp_exec"""
        devs = [neigh.bestdev[0]]
        nip = self.maproute.ip_to_nip(neigh.ip)
        netid = neigh.netid
        return rpc.UDP_call(nip, netid, devs, 
                            'hook.communicating_vessels_udp')

    def communicating_vessels_udp(self, _rpc_caller, caller_id, callee_nip, 
                                  callee_netid):
        """Returns the result of communicating_vessels to remote caller.
           caller_id is the random value generated by the caller for this 
           call.
            It is replied back to the LAN for the caller to recognize a 
            reply destinated to it.
           callee_nip is the NIP of the callee;
           callee_netid is the netid of the callee.
            They are used by the callee to recognize a request destinated 
            to it.
           """
        if self.maproute.me == callee_nip and \
           self.neigh.netid == callee_netid:
            self.communicating_vessels()
            # Since it is micro, I will reply None
            rpc.UDP_send_reply(_rpc_caller, caller_id, None)

    @microfunc()
    def communicating_vessels(self, old_node_nb=None, cur_node_nb=None):
        '''Detects and handle the cases of Communicating Vessels and
           Gnode Splitting.'''

        # Note: old_node_nb and cur_node_nb are valorized only when we get
        # called locally as a handler of event TIME_TICK. They are None when
        # we get called directly by a neighbour.

        # Implements "zombie" status
        if self.ntkd_status.zombie: raise ZombieException('I am a zombie.')

        logging.debug('Coomunicating vessels microfunc started')
        # Get only the neighbours of my network.
        current_nr_list = self.neigh.neigh_list(in_my_network=True)
        
        if old_node_nb != None and self.gnodes_split(old_node_nb, 
                                                     cur_node_nb):
                # The gnode has splitted and we have rehooked.
                # Nothing to do anymore.
                return

        if cur_node_nb and old_node_nb and cur_node_nb[0] == old_node_nb[0]:
                # No new or dead node in level 0
                logging.debug('Coomunicating vessels: No new or dead node'
                              ' in level 0')
                return

        candidates=[]   # List of (neigh, fnb) elements. neigh is a
                        # Neigh instance; fnb is the number of free
                        # of the level 0 of neigh
        inv_candidates=[]
        def cand_cmp((a1, a2), (b1, b2)):
            return cmp(a2, b2)

        for nr in current_nr_list:
                nrnip=self.maproute.ip_to_nip(nr.ip)
                if self.maproute.nip_cmp(self.maproute.me, nrnip) <= 0:
                        # we're interested in external neighbours
                        continue
                # TODO Handle exceptions (eg zombie)
                fnb = self.maproute.call_free_nodes_nb_udp(nr, 0)
                logging.log(logging.ULTRADEBUG, str(nr) + ' has replied its '
                            'gnode has ' + str(fnb) + ' free nodes.')
                if fnb+1 < self.maproute.free_nodes_nb(0):
                        inv_candidates.append((nr, fnb))
                elif self.maproute.free_nodes_nb(0)+1 < fnb:
                        candidates.append((nr, fnb))

        if inv_candidates:
                inv_candidates.sort(cmp=cand_cmp)
                # tell our neighbour, which is bigger than us, to launch 
                # its communicating vessels system
                self.call_communicating_vessels_udp(inv_candidates[0][0])

        if candidates:
                candidates.sort(cmp=cand_cmp, reverse=1)
                # We've found some neighbour gnodes smaller than us. 
                # Let's rehook
                logging.info('Hook: Communicating vessels found a smaller '
                             'GNode. We rehook.')
                # If some other thing has caused a rehook, never mind.
                if self.ntkd_status.hooked:
                    self.ntkd_status.gonna_hook = True
                    self.hook([nr for (nr, fnb) in candidates], [], True,
                                    candidates[0][1])

                    ##TODO: Maybe self.hook() should be done BEFORE forwarding the
                    #       ETP that has generated the TIME_TICK event.

        logging.debug('Coomunicating vessels microfunc end')

    @microfunc()
    def hook(self, passed_neigh_list=[], forbidden_neighs=[], condition=False,
             gfree_new=0):
        """Lets the current node become a hooking node.

        If passed_neigh_list!=[], it tries to hook among the given neighbours
        list, otherwise neigh_list is generated from the Radar.

        forbidden_neighs is a list [(lvl,nip), ...]. All the neighbours nr 
        with a NIP matching nip up to lvl levels are excluded, 
        that is:
                NIP is excluded <==> nip_cmp(nip, NIP) < lvl
        
        Note: the following is used only by communicating_vessels()
        
        If condition is True, the re-hook takes place only if the following
        is true:
                gfree_new > gfree_old_coord
                gfree_new_coord > gfree_old_coord
        where "gfree" is the number of free nodes of the gnode; "new" refers
        to the gnode where we are going to re-hook, "old" to the current one
        where we belong; the suffix "_coord" means that is calculated by the
        Coordinator node.
        gfree_new is passed to the function as a parameter. We have obtained
        it from our neighbour with the remotable method 
        maproute.free_nodes_nb(0).
        In contrast, we request the other values to the Coordinator.
        """

        try:
            # The method 'hook' is called in various situations. How do we
            # detect them?
            # 1. hook called at bootstrap. We are in a private gnode 
            #    (i.e. 192.168..).
            #    We get called without any argument.
            # 2. hook called for a gnode splitting. We have some
            #    "forbidden_neighs".
            # 3. hook called for a communicating vessels. We have some
            #    "candidates" in "passed_neigh_list". We have a "condition" to
            #    satisfy too.
            # 4. hook called for a network collision. We have some nodes of 
            #    the new network in "passed_neigh_list". But, we don't have 
            #    any "condition" to satisfy.
            called_for_bootstrap = 1
            called_for_gnode_splitting = 2
            called_for_communicating_vessels = 3
            called_for_network_collision = 4
            called_for = None
            if forbidden_neighs:
                called_for = called_for_gnode_splitting
            elif not passed_neigh_list:
                called_for = called_for_bootstrap
            elif condition:
                called_for = called_for_communicating_vessels
            else:
                called_for = called_for_network_collision

            self.ntkd_status.hooking = True

            we_are_alone = False
            netid_to_join = None
            neigh_list = passed_neigh_list
            if neigh_list == []:
                    # We are in bootstrap or gnode_splitting. In any case
                    # we want only nodes in my network.
                    neigh_list = self.neigh.neigh_list(in_my_network=True)
            if neigh_list == []:
                    we_are_alone = True
            else:
                    netid_to_join = neigh_list[0].netid

            logging.info('Hooking procedure started.')
            previous_netid = self.radar.neigh.netid
            oldnip = self.maproute.me[:]
            oldip = self.maproute.nip_to_ip(oldnip)
            self.radar.neigh.change_netid(-1)
            if previous_netid != -1:
                logging.info('We previously had got a network id = ' + 
                             str(previous_netid))
                logging.log(logging.ULTRADEBUG, 'Hook: warn neighbours of' + \
                        ' my change netid from ' + str(previous_netid) + 
                        ' to ' + str(self.neigh.netid))
                self.neigh.call_ip_netid_change(oldip, 
                                              previous_netid, 
                                              oldip, 
                                              self.neigh.netid)
                logging.log(logging.ULTRADEBUG, 'Hook: called ip_netid_change '
                                                'on broadcast.')
            logging.info('We haven\'t got any network id, now.')
            suitable_neighbours = []

            hfn = []
            ## Find all the highest non saturated gnodes

            if called_for == called_for_bootstrap and we_are_alone:
                hfn = [(self.maproute.me, 
                        None, 
                        self.highest_free_nodes_in_empty_network())]
                logging.info('Hook: highest non saturated gnodes in new '
                             'empty network: ' + str(hfn))

            def is_neigh_forbidden(nrnip):
                    for lvl, fnr in forbidden_neighs:
                            if self.maproute.nip_cmp(nrnip, fnr) < lvl:
                                    return True
                    return False

            for nr in neigh_list:
                    nrnip=self.maproute.ip_to_nip(nr.ip)

                    if is_neigh_forbidden(nrnip):
                            # We don't want forbidden neighbours
                            continue

                    suitable_neighbours.append(nr)

            has_someone_responded = False
            for nr in suitable_neighbours:
                # We must consider that a node could not respond. E.g. it is 
                # doing a hook, so it has no more the same netid we thought.
                try:
                    nrnip = self.maproute.ip_to_nip(nr.ip)
                    nr_hfn = self.call_highest_free_nodes_udp(nr)
                    has_someone_responded = True
                    hfn.append((nrnip, nr, nr_hfn))
                    logging.info('Hook: highest non saturated gnodes that ' + 
                                 str(nrnip) + ' knows: ' + str(nr_hfn))
                except Exception, e:
                    logging.log(logging.ULTRADEBUG, 'Exception trying to '
                                                    'get hfn from a '
                                                    'neighbour. ' + str(e))
            if not has_someone_responded:
                if called_for != called_for_bootstrap:
                    # We must try with a "bootstrap"-style hook. We schedule 
                    # hook (it is a microfunc with dispatcher) and we exit 
                    # immediately.
                    logging.info('Hook: No one in the new network. We try a '
                                 'new hook from start.')
                    self.hook()
                    return
            ##

            ## Find all the hfn elements with the highest level and
            ## remove all the lower ones
            hfn2 = []
            hfn_lvlmax = -1
            for h in hfn:
                    if h[2][0] > hfn_lvlmax:
                            hfn_lvlmax = h[2][0]
                            hfn2=[]
                    if h[2][0] == hfn_lvlmax:
                            hfn2.append(h)
            hfn = hfn2
            ##

            ## Find the list with the highest number of elements
            lenmax = 0
            for h in hfn:
                if h[2][1] is not None:
                    l = len(h[2][1])
                    if l > lenmax:
                            lenmax=l
                            H=h
            ##

            if lenmax == 0:
                    raise Exception, "Netsukuku is full"

            ## Generate part of our new IP
            newnip = list(H[0])
            neigh_respond = H[1]
            lvl = H[2][0]
            if neigh_respond is None:
                logging.log(logging.ULTRADEBUG, 'Hook: the best is level ' + 
                            str(lvl) + ' known by myself')
            else:
                logging.log(logging.ULTRADEBUG, 'Hook: the best is level ' + 
                            str(lvl) + ' known by ' + str(H[0]) \
                        + ' with netid ' + str(neigh_respond.netid))
            fnl = H[2][1]
            newnip[lvl] = choice(fnl)
            logging.log(logging.ULTRADEBUG, 'Hook: we choose ' + 
                        str(newnip[lvl]))
            for l in reversed(xrange(lvl)): 
                newnip[l] = choice(valid_ids(l, newnip))
            logging.log(logging.ULTRADEBUG, 'Hook: our option is ' + 
                        str(newnip))

            # If we are alone, let's generate our netid
            if we_are_alone:
                self.radar.neigh.change_netid(randint(0, 2**32-1))
                logging.info("Generated our network id: %s", 
                             self.radar.neigh.netid)
                # and we don't need to contact coordinator node...

            else:
                # Contact the coordinator node.
                # If something goes wrong we'd better to retry from start.
                try:
                    # If we are called for bootstrap, we could
                    # have neighbours in different networks (netid).
                    # And we for sure are not the coordinator node for
                    # the gnode we will enter.
                    #
                    # If we are called for network collision, we will contact
                    # neighbours in a different network.
                    # And we for sure are not the coordinator node for
                    # the gnode we will enter.
                    #
                    # In these 2 cases we want to reach the coordinator node 
                    # for the gnode we will enter, passing through the 
                    # neighbour which we asked for the hfn.
                    if called_for == called_for_bootstrap or \
                       called_for == called_for_network_collision:
                        neigh = neigh_respond

                    # In the other situations, we don't
                    # have neighbours in different networks (netid).
                    # And we have the right knowledge of the network.
                    # So, the p2p module knows the best neighbour to use to 
                    # reach the coordinator node for the gnode we will enter.
                    else:
                        neigh = None

                    # TODO We must handle the case of error in contacting the
                    #   coordinator node. The coordinator itself may die.

                    if lvl > 0:
                        # If we are going to create a new gnode, it's useless 
                        # to pose any condition
                        condition=False
                        # TODO Do we have to tell to the old Coord that we're 
                        # leaving?

                    if condition:
                        # <<I'm going out>>
                        logging.log(logging.ULTRADEBUG, 'Hook: going_out, in '
                                    'order to join a gnode which has ' + 
                                    str(gfree_new) + ' free nodes.')
                        co = self.coordnode.peer(key=(1, self.maproute.me), 
                                                 neigh=neigh)
                        # get gfree_old_coord and check that 
                        # gfree_new > gfree_old_coord
                        gfree_old_coord = co.going_out(0, self.maproute.me[0],
                                                       gfree_new)
                        if gfree_old_coord is None:
                            # nothing to be done
                            logging.info('Hooking procedure canceled by our '
                                         'Coord. Our network id = ' + 
                                         str(previous_netid) + ' is back.')
                            self.radar.neigh.change_netid(previous_netid)
                            return

                        # <<I'm going in, can I?>>
                        logging.log(logging.ULTRADEBUG, 'Hook: going_in with '
                                                        'gfree_old_coord = ' +
                                                        str(gfree_old_coord))
                        co2 = self.coordnode.peer(key = (lvl+1, newnip), 
                                                  neigh=neigh)
                        # ask if we can get in and if 
                        # gfree_new_coord > gfree_old_coord, and get our 
                        # new IP
                        newnip=co2.going_in(lvl, gfree_old_coord)

                        if newnip:
                            # we've been accepted
                            co.going_out_ok(0, self.maproute.me[0])
                        else:
                            raise Exception, "Netsukuku is full"

                        # TODO do we need to implement a close?
                        #co.close()
                        #co2.close()

                    else:
                        # <<I'm going in, can I?>>
                        logging.log(logging.ULTRADEBUG, 'Hook: going_in '
                                                        'without' + \
                                                        ' condition.')
                        co2 = self.coordnode.peer(key = (lvl+1, newnip), 
                                                  neigh=neigh)
                        # ask if we can get in, get our new IP
                        logging.info('Hook: contacting coordinator node...')
                        newnip=co2.going_in(lvl)
                        logging.info('Hook: contacted coordinator node, '
                                     'assigned nip = ' + str(newnip))
                        if newnip is None:
                            raise Exception, "Netsukuku is full"
                        # TODO do we need to implement a close?
                        #co2.close()
                except Exception, e:
                    # We must try again with a "bootstrap"-style hook. 
                    # We schedule hook (it is a microfunc with dispatcher)
                    # and we exit immediately.
                    logging.info('Hook: something wrong in contacting Coord. '
                                 'We retry hook from start.')
                    log_exception_stacktrace(e)
                    self.hook()
                    return
            ##

            logging.log(logging.ULTRADEBUG, 'Hook: completing hook...')

            # close the ntkd sessions
            self.neigh.reset_ntk_clients()

            # change the IPs of the NICs
            newnip_ip = self.maproute.nip_to_ip(newnip)
            self.nics.activate(ip_to_str(newnip_ip))

            # reset the map
            self.maproute.map_reset()
            logging.info('MapRoute cleaned because NICs have been flushed')
            self.maproute.me_change(newnip[:])
            self.coordnode.mapcache.me_change(newnip[:], silent=True)

            # warn our neighbours
            logging.log(logging.ULTRADEBUG, 'Hook: warn neighbours of' + \
                    ' my change from %s on -1 to %s on %s.' \
                    % (ip_to_str(oldip), ip_to_str(newnip_ip), 
                       str(self.radar.neigh.netid)))
            self.neigh.call_ip_netid_change(oldip, -1, 
                                            newnip_ip, 
                                            self.radar.neigh.netid)
            logging.log(logging.ULTRADEBUG, 'Hook: called ip_netid_change '
                                            'on broadcast.')

            if not we_are_alone:
                # We will wait some seconds to receive ETPs before
                # being able to reply to some requests.
                wait_id = randint(0, 2**32-1)
                self.ntkd_status.set_hooked_waiting_id(wait_id)
                self.hooked_after_delay(10000, wait_id)

                self.radar.neigh.change_netid(netid_to_join)
                logging.info('We now have got a network id = ' + 
                             str(netid_to_join))
                # warn our neighbours again
                logging.log(logging.ULTRADEBUG, 'Hook.hook warn neighbours of'
                                                ' my change from netid -1 to '
                                                + str(netid_to_join))
                self.neigh.call_ip_netid_change(newnip_ip, -1, 
                                              newnip_ip, 
                                              netid_to_join)
                logging.log(logging.ULTRADEBUG, 'Hook.hook: called '
                                                'ip_netid_change on '
                                                'broadcast.')
            else:
                self.ntkd_status.hooking = False

            # we've done our part
            logging.info('Hooking procedure completed.')
            self.events.send('HOOKED', (oldip, newnip[:]))
            ##
        except Exception, e:
            # We must try with a "bootstrap"-style hook. We schedule hook 
            # (it is a microfunc with dispatcher) and we exit immediately.
            logging.info('Hook: something wrong. We retry hook from start.')
            log_exception_stacktrace(e)
            self.ntkd_status.gonna_hook = True
            self.hook()

    @microfunc(True)
    def hooked_after_delay(self, delay, wait_id):
        xtime.swait(delay)
        if self.ntkd_status.hooked_waiting:
            self.ntkd_status.unset_hooked_waiting_id(wait_id)
            self.events.send('HOOKED2', ())

    def highest_free_nodes_in_empty_network(self):
        """Returns (lvl, fnl), where fnl is a list of free node IDs of
           level `lvl'."""
        fnl = self.maproute.free_nodes_list_in_empty_network()
        if fnl:
            logging.log(logging.ULTRADEBUG, 
                        'highest_free_nodes_in_empty_network: returning...')
            return (self.maproute.levels - 1, fnl)
        logging.log(logging.ULTRADEBUG, 
                    'highest_free_nodes_in_empty_network: returning NONE ...')
        return (-1, None)

    def highest_free_nodes(self):
        """Returns (lvl, fnl), where fnl is a list of free node IDs of
           level `lvl'."""

        # Implements "zombie" status
        if self.ntkd_status.zombie: raise ZombieException('I am a zombie.')

        logging.log(logging.ULTRADEBUG, 'highest_free_nodes: start.')
        if etp_exec_dispatcher_token.executing:
            logging.log(logging.ULTRADEBUG, 
                    'highest_free_nodes: delayed because etp is executing.')
            while etp_exec_dispatcher_token.executing:
                # I'm not ready to interact.
                time.sleep(0.001)
                micro_block()
            logging.log(logging.ULTRADEBUG, 
                        'highest_free_nodes: now we can go on.')
        for lvl in reversed(xrange(self.maproute.levels)):
                fnl = self.maproute.free_nodes_list(lvl)
                if fnl:
                        logging.log(logging.ULTRADEBUG, 
                                    'highest_free_nodes: returning...')
                        return (lvl, fnl)
        logging.log(logging.ULTRADEBUG, 
                    'highest_free_nodes: returning NONE ...')
        return (-1, None)

    def call_highest_free_nodes_udp(self, neigh):
        """Use BcastClient to call highest_free_nodes"""
        devs = [neigh.bestdev[0]]
        nip = self.maproute.ip_to_nip(neigh.ip)
        netid = neigh.netid
        return rpc.UDP_call(nip, netid, devs, 'hook.highest_free_nodes_udp')

    def highest_free_nodes_udp(self, _rpc_caller, caller_id, callee_nip, 
                               callee_netid):
        """Returns the result of highest_free_nodes to remote caller.
           caller_id is the random value generated by the caller for this 
           call.
            It is replied back to the LAN for the caller to recognize a 
            reply destinated to it.
           callee_nip is the NIP of the callee;
           callee_netid is the netid of the callee.
            They are used by the callee to recognize a request destinated 
            to it.
           """
        if self.maproute.me == callee_nip and \
           self.neigh.netid == callee_netid:
            ret = None
            rpc.UDP_send_keepalive_forever_start(_rpc_caller, caller_id)
            try:
                logging.log(logging.ULTRADEBUG, 
                            'calling highest_free_nodes...')
                ret = self.highest_free_nodes()
                logging.log(logging.ULTRADEBUG, 'returning ' + str(ret))
            except Exception as e:
                ret = ('rmt_error', e.message)
                logging.warning('highest_free_nodes_udp: returning exception '
                                + str(ret))
            finally:
                rpc.UDP_send_keepalive_forever_stop(caller_id)
            logging.log(logging.ULTRADEBUG, 'calling UDP_send_reply...')
            rpc.UDP_send_reply(_rpc_caller, caller_id, ret)

    def gnodes_split(self, old_node_nb, cur_node_nb):
        """Handles the case of gnode splitting

           Returns True if we have to rehook"""

        logging.log(logging.ULTRADEBUG, 'gnodes_split: old_node_nb = ' + \
                  str(old_node_nb) + ', cur_node_nb = ' + str(cur_node_nb))
        gnodesplitted = False
        for lvl in reversed(xrange(self.maproute.levels)):
            diff = old_node_nb[lvl] - cur_node_nb[lvl]
            if diff > 0 and diff >= cur_node_nb[lvl]:
                level = lvl + 1
                gnodesplitted = True
                break # We stop at the highest level
        if not gnodesplitted:
            return False

        # Our gnode of level `level' has become broken, and we are in the
        # smallest part of the two. Let's rehook.
        # We will become 'zombie' for some seconds to be sure that in the meantime
        # the nodes close to us become aware of the split and do the same.
        # Then, we'll rehook.
        logging.info('Hook: a split occurred; we become zombie for 20 sec.')
        zombie_id = randint(0, 2**32-1)
        self.ntkd_status.set_zombie_id(zombie_id)
        self.rehook_after_delay(20000, zombie_id)
        return True

    @microfunc(True)
    def rehook_after_delay(self, delay, zombie_id):
        xtime.swait(delay)
        logging.debug('Hook: a split occurred 20 sec ago; are we still zombie?')
        if self.ntkd_status.zombie:
            logging.debug('Hook: we are zombie; let us wakeup.')
            self.ntkd_status.unset_zombie_id(zombie_id)
            logging.info('Hook: a split occurred 20 sec ago; now we rehook.')
            self.ntkd_status.gonna_hook = True
            self.hook()

