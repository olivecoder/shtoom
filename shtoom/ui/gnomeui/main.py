"""Gnome interface to shtoom."""

import gtk
import gtk.glade

from twisted.python import util, log
from twisted.internet import reactor

from shtoom.ui.base import ShtoomBaseUI


class ShtoomWindow(ShtoomBaseUI):

    def __init__(self):
        self.cookie = False
        self.xml = gtk.glade.XML(util.sibpath(__file__, "shtoom.glade"))
        self.xml.signal_autoconnect(self)
        self.xml.get_widget("callwindow").connect("destroy", lambda w: reactor.stop())
        self.address = self.xml.get_widget("address")
        self.callButton = self.xml.get_widget("call")
        self.hangupButton = self.xml.get_widget("hangup")
        self.hangupButton.set_sensitive(0)
        self.status = self.xml.get_widget("appbar").get_children()[0].get_children()[0]
        self.acceptDialog = self.xml.get_widget("acceptdialog")
        self.incoming = []

    # GUI callbacks
    def on_call_clicked(self, w):
        self.statusMessage("Calling...")
        sipURL = self.address.get_text()
        if not sipURL.startswith('sip:'):
            sipURL = "sip:" + sipURL
            self.address.prepend_text("sip:")
        self.hangupButton.set_sensitive(1)
        self.callButton.set_sensitive(0)
        self.address.set_sensitive(0)
        deferred = self.app.placeCall(sipURL)
        deferred.addCallbacks(self.callConnected, self.callFailed).addErrback(log.err)

    def on_hangup_clicked(self, w):
        self.app.dropCall(self.cookie)
        self.callButton.set_sensitive(1)
        self.address.set_sensitive(1)
        self.hangupButton.set_sensitive(0)
        self.statusMessage("")
        self.cookie = None

    def on_acceptdialog_response(self, widget, code):
        self.incoming[0].approved(code == gtk.RESPONSE_OK)

    def on_copy_activate(self, widget):
        self.address.copy_clipboard()

    def on_cut_activate(self, widget):
        self.address.cut_clipboard()

    def on_paste_activate(self, widget):
        self.address.paste_clipboard()

    def on_clear_activate(self, widget):
        self.address.set_text("")

    def on_preferences_activate(self, widget):
        self.statusMessage("Preferences are not supported yet.")

    def on_quit_activate(self, widget):
        reactor.stop()

    def on_about_activate(self, widget):
        self.xml.get_widget("about").show()

    # event callbacks
    def callConnected(self, cookie):
        self.cookie = cookie
        self.hangupButton.set_sensitive(1)

    def callDisconnected(self, cookie, reason):
        self.cookie = None
        self.hangupButton.set_sensitive(0)
        self.callButton.set_sensitive(1)
        print "closed: ", reason

    def callFailed(self, reason):
        self.statusMessage("Call failed: %s" % reason.value)
        self.hangupButton.set_sensitive(0)
        self.callButton.set_sensitive(1)
        self.address.set_sensitive(1)

    def incomingCall(self, description, cookie, defresp):
        from shtoom.exceptions import CallRejected
        # XXX multiple incoming calls won't work
        self.incoming.append(Incoming(self, cookie, description, defresp))
        if len(self.incoming) == 1:
            self.incoming[0].show()

    def _cbAcceptDone(self, result):
        """Called when user accepts/denies call."""
        del self.incoming[0]
        if self.incoming:
            self.incoming[0].show()
        return result

    def debugMessage(self, msg):
        log.msg(msg)

    def statusMessage(self, msg):
        self.status.set_text(msg)


class Incoming:

    def __init__(self, main, cookie, description, deferredResponse):
        self.main = main
        self.cookie = cookie
        self.description = description
        self.deferredResponse = deferredResponse
        self.timeoutID = reactor.callLater(30, self._cbTimeout)
        self.current = False

    def show(self):
        """Display the dialog."""
        self.current = True
        self.main.xml.get_widget("acceptlabel").set_text("Accept call from %s?" % self.description)
        self.main.acceptDialog.show()

    def approved(self, answer):
        from shtoom.exceptions import CallRejected
        self.timeoutID.cancel()
        if answer:
            if self.main.cookie:
                self.main.on_hangup_clicked(None)
            self.main.cookie = self.cookie
            self.main.callButton.set_sensitive(0)
            self.main.address.set_sensitive(0)
            self.main.acceptDialog.hide()
            self.deferredResponse.callback('yes')
        else:
            self.main.acceptDialog.hide()
            self.deferredResponse.errback(CallRejected)
        del self.deferredResponse
        del self.main

    def _cbTimeout(self):
        """User didn't answer, same response as user not accepting call."""
        from shtoom.exceptions import CallNotAnswered
        if self.current:
            self.main.acceptDialog.hide()
        self.deferredResponse.errback(CallNotAnswered)
        del self.deferredSetup
