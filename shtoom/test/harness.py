from twisted.internet import defer, stdio
from twisted.protocols import basic
from twisted.python import log

from time import time

class TestCall:
    "A fake Call object"
    def __init__(self, d, sip):
        self.d = d
        self.sip = sip

    def getLocalSIPAddress(self):
        return ( '127.0.0.1', 5060)

    def getSTUNState(self): 
        return False

    def startFakeInbound(self):
        from shtoom.sip import Dialog
        self.dialog = Dialog()
        self.dialog.setDirection(inbound=True)
        d = self.sip.app.acceptCall(call=self)
        d.addCallbacks(self.acceptedFakeCall, 
                       self.rejectedFakeCall).addErrback(log.err)

    def startFakeOutbound(self, uri):
        from shtoom.sip import Dialog
        self.dialog = Dialog()
        self.dialog.setDirection(outbound=True)
        d = self.sip.app.acceptCall(call=self)
        d.addCallbacks(self.acceptedFakeCall, 
                       self.rejectedFakeCall).addErrback(log.err)

    def acceptedFakeCall(self, cookie):
        print "accepted, got %r"%(cookie,)
        from shtoom.multicast.SDP import rtpPTDict
        self.cookie = cookie
        d, self.d = self.d, None
        self.sip.app.selectFormat(self.cookie,
                                    ((rtpPTDict[('PCMU', 8000, 1)],'PCMU'), )
                                 )
        self.sip.app.startCall(self.cookie, ('127.0.0.1', 9876), d.callback)

    def rejectedFakeCall(self, e):
        print "rejected, got %r"%(e,)
        d, self.d = self.d, None
        d.errback(e)

    def dropCall(self):
        print "drop"
        if hasattr(self,'cookie'):
            self.sip.app.endCall(self.cookie)
        else:
            print "'cancelling' non-started call"
        self.sip.callEndedRestartChecking()


    def terminateCall(self):
        print "remote end closed call"
        self.sip.app.endCall(self.cookie)
        self.sip.callEndedRestartChecking()


class TestSip:
    callFile = 'call.txt'

    "Just like shtoom.sip.SipPhone, only not"
    def __init__(self, app):
        from twisted.internet import reactor
        self.app = app
        self.lastCall = time()
        reactor.callLater(5, self.checkForCallFile)

    def checkForCallFile(self):
        import os
        from twisted.internet import reactor
        reactor.callLater(5, self.checkForCallFile)
        if os.path.exists(self.callFile):
            s = os.stat(self.callFile)
            if s.st_mtime > self.lastCall:
                self.lastCall = time()
                if hasattr(self, 'c'):
                    print "'remote' end closing connection"
                    self.dropFakeInbound('foo')
                else:
                    print "new incoming call starting"
                    self.fakeInbound()
        

    def callEndedRestartChecking(self):
        from twisted.internet import reactor
        print "restarting callfile checking"
        del self.c
        reactor.callLater(5, self.checkForCallFile)

    def placeCall(self, uri):
        d = defer.Deferred()
        self.c = TestCall(d, self)
        self.c.startFakeOutbound(uri)
        return d

    def dropCall(self, cookie):
        print "Sip.dropCall"
        self.c.dropCall()
        del self.c

    def register(self):
        pass

    def fakeInbound(self):
        d = defer.Deferred()
        self.c = TestCall(d, self)
        self.c.startFakeInbound()
        return d

    def dropFakeInbound(self, result):
        print "remote bye"
        self.c.terminateCall()


from shtoom.multicast.SDP import rtpPTDict

class EchoRTP:
    "A fake RTP layer that just repeats back any data sent to it"
    PT_pcmu = rtpPTDict[('PCMU', 8000, 1)]
    PT_gsm = rtpPTDict[('GSM', 8000, 1)]

    actions = []
    def __init__(self, app, cookie):
        self.cookie = cookie
        self.app = app
        self.go = False

    def createRTPSocket(self, ip, stun):
        self.actions.append('create')
        return defer.succeed(self.cookie)

    def startSendingAndReceiving(self, remote):
        from twisted.internet.task import LoopingCall
        self.actions.append('start')
        self.go = True
        self.echo = ''
        self.LC = LoopingCall(self.nextpacket)
        self.LC.start(0.020)

    def stopSendingAndReceiving(self):
        self.actions.append('stop')
        self.LC.stop()
        self.echo = ''

    def nextpacket(self):
        from twisted.internet import reactor
        self.echo = self.app.giveRTP(self.cookie)
        if self.echo is not None and self.echo[1] is not None:
            PT, data = self.echo
            reactor.callLater(0, lambda : self.app.receiveRTP(self.cookie, 
                                                              PT, data))

def main():
    from shtoom.app.phone import Phone
    import sys
    p = Phone()
    p._rtpProtocolClass = EchoRTP
    p.boot()
    p.sipListener.stopListening()
    del p.sip, p.sipListener
    p.sip = TestSip(p)
    p.start()
    sys.exit(0)

if __name__ == "__main__":
    main()