"""Use fastaudio, a python wrapper for PortAudio.

Apparently this means it'll work on 'Windows, Macintosh (8,9,X),
Unix (OSS), SGI, and BeOS'.
Requires fastaudio.tar.gz and PortAudio available from
http://www.freenet.org.nz/python/pyPortAudio/
"""

# system imports
import fastaudio

# sibling imports
import interfaces


class AudioFile:

    __implements__ = (interfaces.IAudioReader, interfaces.IAudioWriter)

    def __init__(self, stream):
        self.stream = stream
        self.stream.open()
        self.buffer = ""
    
    def __del__(self):
        self.stream.stop()
        self.stream.close()
        del self.stream

    def write(self, data):
        self.stream.write(data)

    def read(self, length):
        self.buffer += self.stream.read()
        result, self.buffer = self.buffer[:length], self.buffer[length:]
        return result


def getAudioDevice(mode):
    # we ignore mode, result can always both read and write
    # XXX This isn't correct. It's audio format is 'int8', whatever
    # that might be. We need ULAW.
    return AudioFile(fastaudio.stream(8000, 1, 'int8'))
