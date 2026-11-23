"""Private socketpair messages with anonymous, read-only-mapped image payloads."""

import json
import os
import socket


def send(sock, message, data=None):
    packet = json.dumps(message).encode()
    if data is None:
        sock.send(packet)
        return
    with os.fdopen(os.memfd_create("chalkak-image", os.MFD_CLOEXEC), "wb", buffering=0) as file:
        if file.write(data) != len(data):
            raise OSError("선택기 이미지 전체를 전송할 수 없습니다.")
        socket.send_fds(sock, [packet], [file.fileno()])


def receive(sock):
    packet, fds, flags, _ = socket.recv_fds(sock, 32768, 1, socket.MSG_CMSG_CLOEXEC)
    try:
        if not packet:
            raise EOFError
        if flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC):
            raise ValueError("선택기 메시지가 너무 큽니다.")
        for fd in fds:
            os.set_inheritable(fd, False)
        message = json.loads(packet)
        return message, fds.pop() if fds else None
    finally:
        for fd in fds:
            os.close(fd)
