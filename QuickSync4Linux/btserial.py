#!/usr/bin/env python3

import socket
import select
import time
import fcntl
import struct
import termios

from serial import SerialTimeoutException


class BluetoothSerial:
    """Drop-in replacement for serial.Serial backed by an RFCOMM socket.
    Implements only the subset of pyserial's API used by quicksync:
    name, write, read, in_waiting."""

    def __init__(self, address, channel=1, write_timeout=None):
        self.name = f'bt:{address}@{channel}'
        self._write_timeout = write_timeout
        self._sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
        self._sock.connect((address, channel))
        self._sock.setblocking(False)

    @property
    def in_waiting(self):
        buf = fcntl.ioctl(self._sock.fileno(), termios.FIONREAD, b'\x00\x00\x00\x00')
        return struct.unpack('I', buf)[0]

    def write(self, data):
        total = 0
        deadline = time.monotonic() + self._write_timeout if self._write_timeout else None
        while total < len(data):
            remaining = (deadline - time.monotonic()) if deadline is not None else None
            if remaining is not None and remaining <= 0:
                raise SerialTimeoutException('Write timeout')
            _, w, _ = select.select([], [self._sock], [], remaining)
            if not w:
                raise SerialTimeoutException('Write timeout')
            try:
                sent = self._sock.send(data[total:])
            except BlockingIOError:
                continue
            if sent == 0:
                raise OSError('Bluetooth socket closed by peer')
            total += sent
        return total

    def read(self, n):
        if n <= 0:
            return b''
        try:
            return self._sock.recv(n)
        except BlockingIOError:
            return b''

    def close(self):
        self._sock.close()


BT_ADDR_RE = __import__('re').compile(r'^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}(@\d+)?$')

def isBluetoothAddress(s):
    return BT_ADDR_RE.match(s) is not None

def parseBluetoothAddress(s):
    """Returns (mac, channel) where channel defaults to 1."""
    if '@' in s:
        mac, ch = s.rsplit('@', 1)
        return mac, int(ch)
    return s, 1
