# A credentials cache
# Copyright (C) 2004 Anthony Baxter


class CredCache:
    "Naive credentials cache"
    def __init__(self, app):
        self.app = app
        self._cred = {}

    def encodeSavedCred(self, user, password):
        # Could be over-ridden for a more secure version?
        from base64 import encodestring
        return encodestring('%s\000%s'%(user, password))

    def decodeSavedCred(self, value):
        from base64 import decodestring
        dec = decodestring(value)
        user, password = dec.split('\000', 1)
        return user, password

    def loadCreds(self, creds):
        for o in creds:
            realm = o.getName()
            user, password = self.decodeSavedCred(o.getValue())
            self._cred[realm] = (user, password)

    def getCred(self, realm):
        if realm in self._cred:
            return self._cred.get(realm)
        else:
            return None

    def addCred(self, realm, user, password, save=False):
        from shtoom.Options import StringOption
        self._cred[realm] = (user, password)
        opt = StringOption(realm, 'cred for %s'%realm)
        if not save:
            opt.setDynamic(True)
        opt.setValue(self.encodeSavedCred(user, password))
        cred = self.app.getPref('credentials')
        cred.addOption(opt)
        if save:
            self.app.updateOptions({}, forceSave=True)
