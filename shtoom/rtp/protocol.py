# Copyright (C) 2004 Anthony Baxter

# $Id: rtp.py,v 1.40 2004/03/07 14:41:39 anthony Exp $
#

import struct, random, os, md5, socket
from time import sleep, time

from twisted.internet import reactor, defer
from twisted.internet.protocol import DatagramProtocol
from twisted.internet.task import LoopingCall
from twisted.python import log

from shtoom.rtp.formats import SDPGenerator, PT_CN, PT_xCN, PT_NTE
from shtoom.rtp.packets import RTPPacket, parse_rtppacket

TWO_TO_THE_16TH = 2<<16
TWO_TO_THE_32ND = 2<<32

from shtoom.rtp.packets import NTE

class RTPProtocol(DatagramProtocol):
    """Implementation of the RTP protocol.

    Also manages a RTCP instance.
    """

    _stunAttempts = 0

    _cbDone = None

    rtpParser = None

    def __init__(self, app, cookie, *args, **kwargs):
        self.app = app
        self.cookie = cookie
        self._pendingDTMF = []
        #DatagramProtocol.__init__(self, *args, **kwargs)

    def getSDP(self, othersdp=None):
        sdp = SDPGenerator().getSDP(self)
        if othersdp:
            sdp.intersect(othersdp)
        self.setSDP(sdp)
        return sdp

    def setSDP(self, sdp):
        "This is the canonical SDP for the call"
        self.app.selectDefaultFormat(self.cookie, sdp)
        rtpmap = sdp.getMediaDescription('audio').rtpmap
        self.ptdict = {}
        for pt, (text, marker) in rtpmap.items():
            self.ptdict[pt] = marker
            self.ptdict[marker] = pt

    def createRTPSocket(self, locIP, needSTUN=False):
        """ Start listening on UDP ports for RTP and RTCP.

            Returns a Deferred, which is triggered when the sockets are
            connected, and any STUN has been completed. The deferred
            callback will be passed (extIP, extPort). (The port is the RTP
            port.) We don't guarantee a working RTCP port, just RTP.
        """
        self.needSTUN=needSTUN
        d = defer.Deferred()
        self._socketCompleteDef = d
        self._socketCreationAttempt(locIP)
        return d

    def _socketCreationAttempt(self, locIP=None):
        from twisted.internet.error import CannotListenError
        from shtoom.rtp import rtcp
        self.RTCP = rtcp.RTCPProtocol()

        # RTP port must be even, RTCP must be odd
        # We select a RTP port at random, and try to get a pair of ports
        # next to each other. What fun!
        # Note that it's kinda pointless when we're behind a NAT that
        # rewrites ports. We can at least send RTCP out in that case,
        # but there's no way we'll get any back.
        rtpPort = self.app.getPref('force_rtp_port')
        if not rtpPort:
            rtpPort = 11000 + random.randint(0, 9000)
        if (rtpPort % 2) == 1:
            rtpPort += 1
        while True:
            try:
                self.rtpListener = reactor.listenUDP(rtpPort, self)
            except CannotListenError:
                rtpPort += 2
                continue
            else:
                break
        rtcpPort = rtpPort + 1
        while True:
            try:
                self.rtcpListener = reactor.listenUDP(rtcpPort, self.RTCP)
            except CannotListenError:
                # Not quite right - if it fails, re-do the RTP listen
                self.rtpListener.stopListening()
                rtpPort = rtpPort + 2
                rtcpPort = rtpPort + 1
                continue
            else:
                break
        #self.rtpListener.stopReading()
        if self.needSTUN is False:
            # The pain can stop right here
            self._extRTPPort = rtpPort
            self._extIP = locIP
            d = self._socketCompleteDef
            del self._socketCompleteDef
            d.callback(self.cookie)
        else:
            # If the NAT is doing port translation as well, we will just
            # have to try STUN and hope that the RTP/RTCP ports are on
            # adjacent port numbers. Please, someone make the pain stop.
            self.natMapping()

    def getVisibleAddress(self):
        ''' returns the local IP address used for RTP (as visible from the
            outside world if STUN applies) as ( 'w.x.y.z', rtpPort)
        '''
        return (self._extIP, self._extRTPPort)

    def natMapping(self):
        ''' Uses STUN to discover the external address for the RTP/RTCP
            ports. deferred is a Deferred to be triggered when STUN is
            complete.
        '''
        # See above comment about port translation.
        # We have to do STUN for both RTP and RTCP, and hope we get a sane
        # answer.
        from shtoom.nat import getMapper
        d = getMapper()
        d.addCallback(self._cb_gotMapper)
        return d

    def unmapRTP(self):
        from shtoom.nat import getMapper
        # Currently removing an already-fired trigger doesn't hurt,
        # but this seems likely to change.
        try:
            reactor.removeSystemEventTrigger(self._shutdownHook)
        except:
            pass
        d = getMapper()
        d.addCallback(self._cb_unmap_gotMapper)
        return d

    def _cb_unmap_gotMapper(self, mapper):
        rtpDef = mapper.unmap(self.transport)
        rtcpDef = mapper.unmap(self.RTCP.transport)
        dl = defer.DeferredList([rtpDef, rtcpDef])
        return dl

    def _cb_gotMapper(self, mapper):
        rtpDef = mapper.map(self.transport)
        rtcpDef = mapper.map(self.RTCP.transport)
        self._shutdownHook =reactor.addSystemEventTrigger('before', 'shutdown',
                                                          self.unmapRTP)
        dl = defer.DeferredList([rtpDef, rtcpDef])
        dl.addCallback(self.setStunnedAddress).addErrback(log.err)

    def setStunnedAddress(self, results):
        ''' Handle results of the rtp/rtcp STUN. We have to check that
            the results have the same IP and usable port numbers
        '''
        log.msg("got NAT mapping back! %r"%(results), system='rtp')
        rtpres, rtcpres = results
        if rtpres[0] != defer.SUCCESS or rtcpres[0] != defer.SUCCESS:
            # barf out.
            log.msg("uh oh, stun failed %r"%(results), system='rtp')
        else:
            # a=RTCP might help for wacked out RTCP/RTP pairings
            # format is something like "a=RTCP:AUDIO 16387"
            # See RFC 3605
            code1, rtp = rtpres
            code2, rtcp = rtcpres
            if rtp[0] != rtcp[0]:
                print "stun gave different IPs for rtp and rtcp", results
            # We _should_ try and see if we have working rtp and rtcp, but
            # this seems almost impossible with most firewalls. So just try
            # to get a working rtp port (an even port number is required).
            elif False and ((rtp[1] % 2) != 0):
                log.msg("stun: unusable RTP/RTCP ports %r, retry #%d"%
                                            (results, self._stunAttempts),
                                            system='rtp')
                # XXX close connection, try again, tell user
                if self._stunAttempts > 8:
                    # XXX
                    print "Giving up. Made %d attempts to get a working port"%(
                        self._stunAttempts)
                self._stunAttempts += 1
                defer.maybeDeferred(
                            self.rtpListener.stopListening).addCallback(
                                    lambda x:self.rtcpListener.stopListening()
                                                          ).addCallback(
                                    lambda x:self._socketCreationAttempt()
                                                          )
                #self.rtpListener.stopListening()
                #self.rtcpListener.stopListening()
                #self._socketCreationAttempt()
            else:
                # phew. working NAT
                log.msg("stun: sane NAT for RTP/RTCP", system='rtp')
                self._extIP, self._extRTPPort = rtp
                self._stunAttempts = 0
                d = self._socketCompleteDef
                del self._socketCompleteDef
                d.callback(self.cookie)

    def connectionRefused(self):
        log.err("RTP got a connection refused, ending call")
        self.Done = True
        self.app.dropCall(self.cookie)

    def whenDone(self, cbDone):
        self._cbDone = cbDone

    def stopSendingAndReceiving(self):
        self.Done = 1
        d = self.unmapRTP()
        d.addCallback(lambda x: self.rtpListener.stopListening())
        d.addCallback(lambda x: self.rtcpListener.stopListening())

    def _send_packet(self, pt, data, xhdrtype=None, xhdrdata=''):
        packet = RTPPacket(self.ssrc, self.seq, self.ts, data, pt=pt, xhdrtype=xhdrtype, xhdrdata=xhdrdata)

        self.seq += 1
        self.transport.write(packet.netbytes(), self.dest)

    def _send_cn_packet(self):
        # PT 13 is CN.
        if self.ptdict.has_key(PT_CN):
            cnpt = PT_CN.pt
        elif self.ptdict.has_key(PT_xCN):
            cnpt = PT_xCN.pt
        else:
            # We need to send SOMETHING!?!
            cnpt = 0

        log.msg("sending CN(%s) to seed firewall to %s:%d"%(cnpt, self.dest[0], self.dest[1]), system='rtp')

        self._send_packet(cnpt, chr(127))

    def startSendingAndReceiving(self, dest, fp=None):
        self.dest = dest
        self.prevInTime = self.prevOutTime = time()
        self.sendFirstData()

    def sendFirstData(self):
        self.seq = self.genRandom(bits=16)
        self.ts = self.genInitTS()
        self.ssrc = self.genSSRC()
        self.sample = None
        self.packets = 0
        self.Done = 0
        self.sent = 0
        try:
            self.sample = self.app.giveRTP(self.cookie)
        except IOError: # stupid sound devices
            self.sample = None
            pass
        self.LC = LoopingCall(self.nextpacket)
        self.LC.start(0.020)
        # Now send a single CN packet to seed any firewalls that might
        # need an outbound packet to let the inbound back.
        self._send_cn_packet()
        if hasattr(self.transport, 'connect'):
            self.transport.connect(*self.dest)

    def datagramReceived(self, datagram, addr):
        # XXX check for late STUN packets here. see, e.g datagramReceived in sip.py
        packet = parse_rtppacket(datagram)
        packet.header.ct = self.ptdict[packet.header.pt]
        if packet:
            self.app.receiveRTP(self.cookie, packet)

    def genSSRC(self):
        # Python-ish hack at RFC1889, Appendix A.6
        m = md5.new()
        m.update(str(time()))
        m.update(str(id(self)))
        if hasattr(os, 'getuid'):
            m.update(str(os.getuid()))
            m.update(str(os.getgid()))
        m.update(str(socket.gethostname()))
        hex = m.hexdigest()
        nums = hex[:8], hex[8:16], hex[16:24], hex[24:]
        nums = [ long(x, 17) for x in nums ]
        ssrc = 0
        for n in nums: ssrc = ssrc ^ n
        ssrc = ssrc & (2**32 - 1)
        return ssrc

    def genInitTS(self):
        # Python-ish hack at RFC1889, Appendix A.6
        m = md5.new()
        m.update(str(self.genSSRC()))
        m.update(str(time()))
        hex = m.hexdigest()
        nums = hex[:8], hex[8:16], hex[16:24], hex[24:]
        nums = [ long(x, 16) for x in nums ]
        ts = 0
        for n in nums: ts = ts ^ n
        ts = ts & (2**32 - 1)
        return ts

    def startDTMF(self, digit):
        print "startSending", digit
        self._pendingDTMF.append(NTE(digit, self.ts))

    def stopDTMF(self, digit):
        print "stopSending", digit
        if self._pendingDTMF[-1].getKey() == digit:
            self._pendingDTMF[-1].end()

    def genRandom(self, bits):
        """Generate up to 128 bits of randomness."""
        if os.path.exists("/dev/urandom"):
            hex = open('/dev/urandom').read(16).encode("hex")
        else:
            m = md5.new()
            m.update(str(time()))
            m.update(str(random.random()))
            m.update(str(id(self.dest)))
            hex = m.hexdigest()
        return int(hex[:bits//4],16)

    def nextpacket(self, n=None, f=None, pack=struct.pack):
        if self.Done:
            self.LC.stop()
            if self._cbDone:
                self._cbDone()
            return
        self.ts += 160
        self.packets += 1
        # We need to keep track of whether we were in silence mode or not -
        # when we go from silent->talking, set the marker bit. Other end
        # can use this as an excuse to adjust playout buffer.
        if self.sample is not None:
            pt = self.ptdict[self.sample.header.ct]
            self._send_packet(pt, self.sample.data)
            self.sent += 1
            self.sample = None
        elif (self.packets - self.sent) % 100 == 0:
            self._send_cn_packet()

        # Now send any pending DTMF keystrokes
        if self._pendingDTMF:
            payload = self._pendingDTMF[0].getPayload(self.ts)
            if payload:
                ntept = self.ptdict.get(PT_NTE)
                if ntept is not None:
                    self._send_packet(pt=ntept, data=payload)
                if self._pendingDTMF[0].isDone():
                    self._pendingDTMF = self._pendingDTMF[1:]
        try:
            self.sample = self.app.giveRTP(self.cookie)
        except IOError:
            pass

        # Wrapping
        if self.seq >= TWO_TO_THE_16TH:
            self.seq = self.seq - TWO_TO_THE_16TH

        if self.ts >= TWO_TO_THE_32ND:
            self.ts = self.ts - TWO_TO_THE_32ND
