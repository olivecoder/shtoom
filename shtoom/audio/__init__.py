"""
"""

from twisted.python import log
from shtoom.audio.converters import MediaLayer



def findAudioInterface():
    from __main__ import app
    # Ugh. Circular import hell
    from shtoom.avail import audio as av_audio
    audioOptions = { 'oss': av_audio.ossaudio,
                     'alsa': av_audio.alsaaudio,
                     'fast': av_audio.fastaudio,
                     'port': av_audio.fastaudio,
                     'osx': av_audio.osxaudio,
                     'core': av_audio.osxaudio,
                     'file': av_audio.fileaudio,
                   }
    allAudioOptions = [
                        av_audio.alsaaudio,
                        av_audio.ossaudio,
                        av_audio.fastaudio,
                        av_audio.osxaudio
                      ]

    audioPref = attempts = None

    if app is not None:
        files = app.getPref('audio_infile') or app.getPref('audio_outfile')
        if files is not None:
            audioPref = 'file'
        else:
            audioPref = app.getPref('audio')

    if audioPref:
        audioint = audioOptions.get(audioPref)
        if not audioint:
            log.msg("requested oss audio interface unavailable")

    for audioint in allAudioOptions:
        if audioint:
            return audioint

_device = None

def getAudioDevice(_testAudioInt=None):
    from shtoom.exceptions import NoAudioDevice
    global _device
    if _testAudioInt is not None:
        return MediaLayer(_testAudioInt.Device())

    if _device is None:
        audioint = findAudioInterface()
        if audioint is None:
            raise NoAudioDevice("no working audio interface found")
        dev = audioint.Device()
        _device = MediaLayer(dev)
    return _device
