from twisted.internet.protocol import DatagramProtocol
from twisted.internet import reactor
rdev = open('/dev/urandom', 'rb')
class PrintSender(DatagramProtocol):
    def startProtocol(self):
        host, port = "127.0.0.1", 22001
        self.transport.connect(host, port)

    def datagramReceived(self, data, (host, port)):
        print "received %r from %s:%d" % (data, host, port)
        self.transport.write(data)

reactor.listenUDP(22000, PrintSender())
reactor.run()