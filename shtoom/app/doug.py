# Copyright (C) 2004 Anthony Baxter

# The Message app. Accepts all calls, plays a message, then hangs up.

from shtoom.app.interfaces import Application
from shtoom.app.base import BaseApplication
from twisted.internet import defer
from twisted.python import log
from twisted.protocols import sip as tpsip
from shtoom.exceptions import CallFailed
import sys, traceback

from shtoom.rtp.formats import PT_PCMU, PT_GSM, PT_SPEEX, PT_DVI4

nteMap = { 0: '0',  1: '1',  2: '2',  3: '3',  4: '4',  5: '5',  6: '6',
           7: '7',  8: '8',  9: '9', 10: '*', 11: '#', 12: 'A', 13: 'B',
          14: 'C', 15: 'D', 16: 'flash' }

class DougApplication(BaseApplication):
    __implements__ = ( Application, )

    configFileName = '.dougrc'

    def __init__(self, voiceapp, ui=None, audio=None):
        # Mapping from callcookies to rtp object
        self._rtp = {}
        # Mapping from callcookies to call objects
        self._calls = {}
        # Mapping from callcookies to voiceapp instances
        self._voiceapps = {}
        self._voiceappClass = voiceapp
        self._voiceappArgs = {}

    def boot(self, options=None):
        from shtoom.opts import buildOptions
        if options is None:
            options = buildOptions(self)
        self.initOptions(options)
        if not self.getPref('logfile'):
            print "logging to stdout"
            log.startLogging(sys.stdout)
        else:
            file = open(self.getPref('logfile'), 'aU')
            print "logging to file", file
            log.startLogging(file)
        BaseApplication.boot(self)

    def start(self):
        "Start the application."
        from twisted.internet import reactor
        vargs = self.getPref('dougargs')
        if vargs:
            kwargs = [x.split('=') for x in vargs.split(',') ]
            self._voiceappArgs = dict(kwargs)

        register_uri = self.getPref('register_uri')
        if register_uri is not None:
            d = self.sip.register()
            d.addCallback(log.err).addErrback(log.err)
        reactor.run()

    def initVoiceapp(self, callcookie):
        print "creating voiceapp", self._voiceappClass
        d = defer.Deferred()
        d.addCallbacks(lambda x: self.acceptResults(callcookie,x),
                       lambda x: self.acceptErrors(callcookie,x))
        try:
            v = self._voiceappClass(d, self, callcookie, **self._voiceappArgs)
            v.va_start()
        except:
            ee,ev,et = sys.exc_info()
            print "voiceapp error", ee, ev, traceback.extract_tb(et)
            v = None
        if v:
            print "new voiceapp", v
            self._voiceapps[callcookie] = v

    def acceptResults(self, callcookie, results):
        print "callcookie %s ended with result %s"%(callcookie, results)
        self.dropCall(callcookie)

    def acceptErrors(self, callcookie, error):
        print "callcookie %s ended with ERROR %r"%(callcookie, error)
        self.dropCall(callcookie)

    def startVoiceApp(self):
        "Start a voiceapp (without an inbound leg)"
        cookie = self.getCookie()
        self.initVoiceapp(cookie)
        self._voiceapps[cookie].va_callstart(None)
        print self._voiceapps.keys()

    def acceptCall(self, call):
        from shtoom.doug.leg import Leg
        print "dialog is", call.dialog
        calltype = call.dialog.getDirection()
        if call.cookie is None:
            cookie = self.getCookie()
        else:
            cookie = call.cookie
        self._calls[cookie] = call
        d = self._createRTP(cookie,
                            call.getLocalSIPAddress()[0],
                            call.getSTUNState())
        if calltype == 'outbound':
            # Outbound call, trigger the callback immediately
            d.addCallback(lambda x: cookie)
        elif calltype == 'inbound':
            # Otherwise we chain callbacks
            self.initVoiceapp(cookie)
            d.addErrback(lambda x: self.rejectedCall(cookie, x))
            ad = defer.Deferred()
            inbound = Leg(cookie, call.dialog)
            inbound.incomingCall(ad)
            self._voiceapps[cookie].va_callstart(inbound)
            d.addCallback(lambda x, ad=ad: ad)
        else:
            raise ValueError, "unknown call type %s"%(calltype)
        return d

    def rejectedCall(self, callcookie, reason):
        print "rejectedCall", callcookie, reason
        del self._calls[callcookie]
        del self._voiceapps[callcookie]
        return reason

    def _createRTP(self, cookie, fromIP, withSTUN):
        from shtoom.rtp.protocol import RTPProtocol
        rtp = RTPProtocol(self, cookie)
        self._rtp[cookie] = rtp
        d = rtp.createRTPSocket(fromIP,withSTUN)
        return d

    def selectDefaultFormat(self, callcookie, sdp):
        md = sdp.getMediaDescription('audio')
        rtpmap = md.rtpmap
        v = self._voiceapps.get(callcookie)
        ptlist = [ x[1] for x in  rtpmap.values() ]
        v.va_selectDefaultFormat(ptlist)

    def getSDP(self, callcookie, othersdp=None):
        rtp = self._rtp[callcookie]
        sdp = rtp.getSDP(othersdp)
        return sdp

    def startCall(self, callcookie, remoteSDP, cb):
        # create an inboundLeg
        from shtoom.doug.leg import Leg
        md = remoteSDP.getMediaDescription('audio')
        ipaddr = md.ipaddr or remoteSDP.ipaddr
        remoteAddr = (ipaddr, md.port)
        self._rtp[callcookie].startSendingAndReceiving(remoteAddr)
        call = self._calls[callcookie]
        if call.dialog.getDirection() == "inbound":
            self._voiceapps[callcookie].va_callanswered()
        log.msg("call %s connected"%callcookie, system='doug')
        cb(callcookie)

    def endCall(self, callcookie, reason=''):
        log.msg("call %s disconnected"%callcookie, reason, system='doug')
        if self._rtp.get(callcookie):
            rtp = self._rtp[callcookie]
            rtp.stopSendingAndReceiving()
            del self._rtp[callcookie]
        if self._calls.get(callcookie):
            del self._calls[callcookie]
        if self._voiceapps.get(callcookie):
            self._voiceapps[callcookie].va_abort()
            del self._voiceapps[callcookie]

    def receiveRTP(self, callcookie, packet):
        from shtoom.rtp.formats import PT_NTE
        v = self._voiceapps[callcookie]
        if packet.pt is PT_NTE:
            data = packet.data
            key = ord(data[0])
            start = (ord(data[1]) & 128) and True or False
            print "got dtmf", key, start
            if start:
                v.va_startDTMFevent(nteMap[key])
            else:
                v.va_stopDTMFevent(nteMap[key])
            return
        try:
            self._voiceapps[callcookie].va_receiveRTP(packet)
        except IOError:
            pass

    def giveRTP(self, callcookie):
        v = self._voiceapps[callcookie]
        packet = v.va_giveRTP()
        return packet

    def placeCall(self, cookie, sipURL, fromURI=None):
        ncookie = self.getCookie()
        self._voiceapps[ncookie] = self._voiceapps[cookie]
        print "connecting %s to %s"%(ncookie, cookie), self._voiceapps.keys()
        d = self.sip.placeCall(sipURL, fromURI, cookie=ncookie)
        d.addCallbacks(
            lambda x: self.outboundCallConnected(cookie, x),
            lambda x: self.outboundCallFailed(cookie, ncookie, x)).addErrback(log.err)
        return d

    def outboundCallConnected(self, voiceappCookie, outboundCookie):
        from shtoom.doug.leg import Leg
        print "outbound connected!", voiceappCookie, outboundCookie
        call = self._calls[outboundCookie]
        outbound = Leg(outboundCookie, call.dialog)
        outbound.outgoingCall()
        self._voiceapps[voiceappCookie].va_callanswered(outbound)

    def outboundCallFailed(self, voiceappCookie, outboundCookie, exc):
        from shtoom.doug.leg import Leg
        print "outbound failed!", voiceappCookie, outboundCookie
        call = self._calls[outboundCookie]
        outbound = Leg(outboundCookie, call.dialog)
        outbound.outgoingCall()
        self._voiceapps[voiceappCookie].va_callrejected(outbound)

    def dropCall(self, cookie):
        print "dropCall", cookie
        call = self._calls.get(cookie)
        if not call:
            log.err("Couldn't find cookie %s, have %r, %r"%(cookie, self._calls.keys(), self._voiceapps.keys(), ))
            return
        d = call.dropCall()

    def statusMessage(self, message):
        log.msg("STATUS: "+message, system='doug')

    def debugMessage(self, message):
        log.msg(message, system='doug')

    def appSpecificOptions(self, opts):
        import os.path

        from shtoom.Options import OptionGroup, StringOption, ChoiceOption
        app = OptionGroup('doug', 'doug')
        app.addOption(StringOption('logfile','log to this file'))
        app.addOption(StringOption('dougargs',
                                'pass these arguments to the voiceapp'))
        opts.addGroup(app)
        opts.setOptsFile(self.configFileName)

    def authCred(self, method, uri, realm='unknown', retry=False):
        "Place holder for now"
        user = self.getPref('register_authuser')
        passwd = self.getPref('register_authpasswd')
        if user is not None and passwd is not None and retry is False:
            return defer.succeed((self.getPref('register_authuser'),
                                 self.getPref('register_authpasswd')))
        else:
            raise defer.fail(CallFailed("No auth available"))

    def startDTMF(self, cookie, digit):
        rtp = self._rtp.get(cookie)
        if rtp:
            rtp.startDTMF(digit)

    def stopDTMF(self, cookie, digit):
        rtp = self._rtp.get(cookie)
        if rtp:
            rtp.stopDTMF(digit)
