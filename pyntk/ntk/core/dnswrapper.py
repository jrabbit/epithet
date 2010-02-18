##
# This file is part of Netsukuku
# (c) Copyright 2010 Luca Dionisi aka lukisi <luca.dionisi@gmail.com>
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

##
# This is a fake DNS server. It receives requests made by classic DNS
# resolvers. It distinguishes the realm intended by the user (.ntk
# suffix is for netsukuku, the rest is for internet)
# Then it translates the DNS request in a ANDNS request, and forwards
# it to AndnsServer.
##

import andns.andns as andns
import dns.message
import dns.rrset
from random import randint

from ntk.wrap.sock import Sock
from ntk.network.inet import ip_to_str
from ntk.lib.micro import microfunc
from ntk.lib.log import logger as logging
from ntk.lib.log import log_exception_stacktrace
from ntk.core.andna import NULL_SERV_KEY, make_serv_key
from ntk.core.andnsserver import AndnsRequest, AndnsReverseRequest
from ntk.core.snsd import AndnaResolvedRecord
from ntk.wrap.xtime import swait
from ntk.network.inet import str_to_ip

class DnsWrapper(object):
    def __init__(self, maproute, andnsserver=None):
        self.maproute = maproute
        self.andnsserver = andnsserver
        # not None means use in-process method, None means use libandns.so
        self.run()

    @microfunc(True, keep_track=1)
    def run(self):
        socket = Sock(None, None)
        self.s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.s.bind(('', 53))
        logging.debug('DnsWrapper: binded to UDP port 53.')
        self.serving_ids = []

        while True:
            logging.debug('DnsWrapper: waiting requests.')
            message, address = self.s.recvfrom(1024)
            logging.debug('DnsWrapper: serving a request.')
            self.requestHandler(address, message)

    @microfunc(True, keep_track=1)
    def requestHandler(self, address, message):
        resp = None
        try:
            message_id = ord(message[0]) * 256 + ord(message[1])
            logging.debug('DnsWrapper: msg id = ' + str(message_id))
            if message_id in self.serving_ids:
                # the request is already taken, drop this message
                logging.debug('DnsWrapper: I am already serving this request.')
                return
            self.serving_ids.append(message_id)
            self.cleanup_serving_id(message_id)
            try:
                msg = dns.message.from_wire(message)
                try:
                    op = msg.opcode()
                    if op == 0:
                        # standard and inverse query
                        qs = msg.question
                        if len(qs) > 0:
                            q = qs[0]
                            logging.debug('DnsWrapper: request is ' + str(q))
                            if q.rdtype == dns.rdatatype.A:
                                resp = self.std_qry(msg)
                            elif q.rdtype == dns.rdatatype.PTR or \
                                    q.name.to_text()[-14:].upper() == '.IN-ADDR.ARPA.':
                                resp = self.inv_qry(msg)
                            elif q.rdtype == dns.rdatatype.SRV:
                                # TODO interpret requests of type SRV records (andns_req.serv_key)
                                pass
                            else:
                                # not implemented
                                resp = self.make_response(qry=msg, RCODE=4)   # RCODE =  4    Not Implemented
                    else:
                        # not implemented
                        resp = self.make_response(qry=msg, RCODE=4)   # RCODE =  4    Not Implemented

                except Exception, e:
                    logging.info('DnsWrapper: got ' + repr(e))
                    log_exception_stacktrace(e)
                    resp = self.make_response(qry=msg, RCODE=2)   # RCODE =  2    Server Error
                    logging.debug('DnsWrapper: resp = ' + repr(resp.to_wire()))

            except Exception, e:
                logging.info('DnsWrapper: got ' + repr(e))
                log_exception_stacktrace(e)
                resp = self.make_response(id=message_id, RCODE=1)   # RCODE =  1    Format Error
                logging.debug('DnsWrapper: resp = ' + repr(resp.to_wire()))

        except Exception, e:
            # message was crap, not even the ID
            logging.info('DnsWrapper: got ' + repr(e))
            log_exception_stacktrace(e)

        if resp:
            self.s.sendto(resp.to_wire(), address)

    @microfunc(True)
    def cleanup_serving_id(self, message_id):
        swait(20000)
        self.serving_ids.remove(message_id)

    def std_qry(self, msg):
        ''' Handles a standard DNS query. Returns the Message to use as response.

        @param msg: A standard DNS query
        @type msg: dns.message.Message object
        @rtype: dns.message.Message object'''

        qs = msg.question
        logging.debug('DnsWrapper: ' + str(len(qs)) + ' questions.')

        answers = []
        nxdomain = False
        for q in qs:
            qname = q.name.to_text()[:-1]
            logging.debug('DnsWrapper: q name = ' + qname)
            # Transform DNS request in ANDNS request
            andns_req = AndnsRequest()
            andns_req.ntk_bit = andns.INET_REALM
            andns_req.hostname = qname
            # Netsukuku or Internet?
            if andns_req.hostname[-4:].upper() == '.NTK':
                andns_req.hostname = andns_req.hostname[:-4]
                andns_req.ntk_bit = andns.NTK_REALM
            logging.debug('DnsWrapper: andns_req = ' + str(andns_req))

            ret = None
            if self.andnsserver:
                logging.debug('DnsWrapper: andnsserver.resolve...')
                res, ret = self.andnsserver.resolve(andns_req)
                logging.debug('DnsWrapper: andnsserver.resolve returns ' + str(ret))
                if res != 'OK':
                    logging.debug('DnsWrapper: returning error because ret: ' + str(ret))
                    ret = None
                    nxdomain = True
                    break
            else:
                # TODO: call libandns
                logging.debug('DnsWrapper: call libandns not implemented yet.')
                nxdomain = True
                break

            if ret is not None:
                if isinstance(ret, AndnaResolvedRecord):
                    # ret is an instance of AndnaResolvedRecord
                    if ret.records is None:
                        logging.debug('DnsWrapper: not found.')
                        break
                    else:
                        # Since in DNS, records A have no priority or weight, we do like this:
                        # choose max priority records
                        max_prio = -1
                        for rec in ret.records:
                            if max_prio == -1 or max_prio > rec.priority:
                                max_prio = rec.priority
                        records = [rec for rec in ret.records if rec.priority == max_prio]
                        # return them all
                        for rec in records:
                            # from a nip to a ip string if realm is NTK,
                            # otherwise with INET realm it is yet a string
                            ipstr = ip_to_str(self.maproute.nip_to_ip(rec.record)) \
                                    if andns_req.ntk_bit == andns.NTK_REALM else rec.record

                            logging.debug('DnsWrapper: returns: ' + str(ipstr))
                            rrset = dns.rrset.from_text(q.name, ret.get_ttl() / 1000,
                                   dns.rdataclass.IN, dns.rdatatype.A, ipstr)
                            answers.append(rrset)
                else:
                    # ret is an instance of AndnsAnswer ??
                    # TODO
                    pass

        if not nxdomain:
            resp = self.make_response(qry=msg)
            for rrset in answers:
                resp.answer.append(rrset)
            logging.debug('DnsWrapper: resp = ' + repr(resp.to_wire()))
        else:
            # TODO what has to do a dns server if some of the questions is not found?
            logging.info('DnsWrapper: some question not answered.')
            resp = self.make_response(qry=msg, RCODE=3)   # RCODE =  3    Name Error
            logging.debug('DnsWrapper: resp = ' + repr(resp.to_wire()))

        return resp

    def inv_qry(self, msg):
        ''' Handles a inverse DNS query. Returns the Message to use as response.

        @param msg: A inverse DNS query
        @type msg: dns.message.Message object
        @rtype: dns.message.Message object'''

        # TODO

        qs = msg.question
        logging.debug('DnsWrapper: ' + str(len(qs)) + ' questions that are:\n' + str(qs))

        answers = []
        nxdomain = False
        for q in qs:
            logging.debug('DnsWrapper: q.name = ' + str(q.name))
            if q.name.to_text()[-14:].upper() == '.IN-ADDR.ARPA.':
                revip = q.name.to_text()[:-14]
            else:
                revip = q.name[:-1]
            logging.debug('DnsWrapper: revip = ' + str(revip))
            ipstr = '.'.join(revip.split('.')[::-1])
            logging.debug('DnsWrapper: manipulated q.name = ' + ipstr)

            # Transform DNS request in ANDNS request
            andns_req = AndnsReverseRequest()
            # Netsukuku or Internet?
            # TODO temporary hack
            if ipstr[:3] == '10.':
                andns_req.nip = self.maproute.ip_to_nip(str_to_ip(ipstr))
                andns_req.ntk_bit = andns.NTK_REALM
            else:
                andns_req.ip = ipstr
                andns_req.ntk_bit = andns.INET_REALM

            ret = None
            if self.andnsserver:
                logging.debug('DnsWrapper: andnsserver.reverse_resolve...')
                res, ret = self.andnsserver.reverse_resolve(andns_req)
                logging.debug('DnsWrapper: andnsserver.reverse_resolve returns ' + str(ret))
                if res != 'OK':
                    logging.debug('DnsWrapper: returning error because ret: ' + str(ret))
                    ret = None
                    nxdomain = True
                    break
                if ret is None or hasattr(ret, '__iter__') and not any(ret):
                    logging.debug('DnsWrapper: returning error because ret: ' + str(ret))
                    ret = None
                    nxdomain = True
                    break
            else:
                # TODO: call libandns
                logging.debug('DnsWrapper: call libandns not implemented yet.')
                nxdomain = True
                break

            if ret is not None:
                if isinstance(ret, list):
                    if len(ret) > 0:
                        # ret is a list got by counter.reverse_resolution
                        for hostname, ttl in ret:
                            logging.debug('DnsWrapper: returns: ' + hostname)
                            rrset = dns.rrset.from_text(q.name, ttl / 1000,
                                   dns.rdataclass.IN, dns.rdatatype.PTR, hostname + '.NTK.')
                            answers.append(rrset)
                    else:
                        logging.debug('DnsWrapper: returning error because ret: ' + str(ret))
                        nxdomain = True
                        break
                elif isinstance(ret, AndnaResolvedRecord):
                    resolved = ret.records
                    ttl = ret.get_ttl()
                    if len(resolved) > 0:
                        for snsd_record in resolved:
                            logging.debug('DnsWrapper: returns: ' + str(snsd_record))
                            rrset = dns.rrset.from_text(q.name, ttl / 1000,
                                      dns.rdataclass.IN, dns.rdatatype.PTR, str(snsd_record.record))
                            answers.append(rrset)
                    else:
                        logging.debug('DnsWrapper: returning error because ret: ' + str(ret))
                        nxdomain = True
                        break
                        
        if not nxdomain:
            resp = self.make_response(qry=msg)
            for rrset in answers:
                resp.answer.append(rrset)
            logging.debug('DnsWrapper: resp = ' + repr(resp.to_wire()))
        else:
            # TODO what has to do a dns server if some of the questions is not found?
            logging.info('DnsWrapper: some question not answered.')
            resp = self.make_response(qry=msg, RCODE=3)   # RCODE =  3    Name Error
            logging.debug('DnsWrapper: resp = ' + repr(resp.to_wire()))

        return resp

    def make_response(self, qry=None, id=None, AA=True, RA=True, RCODE=0):
        ''' Makes a response, from a query or just an ID.

        @rtype: dns.message.Message object'''

        if qry is None and id is None:
            raise Exception, 'bad use of make_response'
        if qry is None:
            resp = dns.message.Message(id)
            # QR = 1
            resp.flags |= dns.flags.QR
            if RCODE != 1:
                raise Exception, 'bad use of make_response'
        else:
            resp = dns.message.make_response(qry)
        if AA:
            resp.flags |= dns.flags.AA
        else:
            resp.flags &= 0xffff - dns.flags.AA
        if RA:
            resp.flags |= dns.flags.RA
        else:
            resp.flags &= 0xffff - dns.flags.RA
        resp.set_rcode(RCODE)
        return resp
