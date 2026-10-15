"""Serial exchange and verified writes. SPDX-License-Identifier: GPL-3.0-or-later."""
from contextlib import contextmanager
import os
import struct
import time

from .memory import BLOCK_SIZE, IMAGE_SIZE, RADIO_ID, changed_blocks, sha256


class RadioError(RuntimeError):
    pass


class Protocol:
    def __init__(self, port, events=None, timeout=1.2, settle=2.0, sleep=None, clock=None):
        self.port = port
        self.events = events if events is not None else []
        self.timeout = timeout
        self.settle = settle
        self.sleep = sleep or time.sleep
        self.clock = clock or time.monotonic
        self.echo = False
        self.entered = False

    def exchange(self, payload, expected=1, detect_echo=False):
        self.port.write(payload)
        raw = bytearray()
        deadline = self.clock() + self.timeout
        while expected and self.clock() < deadline:
            chunk = self.port.read(1)
            if not chunk:
                continue
            raw.extend(chunk)
            echoed = self.echo or (detect_echo and raw[:1] == payload[:1])
            needed = expected + (len(payload) if echoed else 0)
            if len(raw) >= needed:
                break
        raw = bytes(raw)
        has_echo = (self.echo or detect_echo) and raw.startswith(payload)
        answer = raw[len(payload):] if has_echo else raw
        self.events.append({"tx": payload.hex(" "), "rx": raw.hex(" "), "echo": has_echo})
        return answer

    def enter(self):
        self.echo = False
        if self.exchange(b"\x02PROGRAL", detect_echo=True) != b"\x06":
            raise RadioError("No entry ACK 06; check power, cable seating and port")
        self.entered = True
        self.echo = self.events[-1]["echo"]
        ident = self.exchange(b"\x02", expected=8)
        if ident != RADIO_ID:
            raise RadioError(f"Untested radio ID: {ident.hex(' ')}")
        if self.exchange(b"\x06") != b"\x06":
            raise RadioError("Missing final handshake ACK")
        return ident

    def exit(self):
        try:
            self.exchange(b"E", expected=0)
        finally:
            self.entered = False

    def restart(self):
        self.exit()
        self.sleep(self.settle)
        self.port.reset_input_buffer()
        self.enter()

    def read_image(self):
        memory = bytearray()
        for address in range(0, IMAGE_SIZE, BLOCK_SIZE):
            command = struct.pack(">cHB", b"R", address, BLOCK_SIZE)
            response = self.exchange(command, expected=BLOCK_SIZE + 4)
            if len(response) != BLOCK_SIZE + 4 or response[:4] != b"W" + command[1:]:
                raise RadioError(f"Invalid read response at 0x{address:04x}")
            memory.extend(response[4:])
            if self.exchange(b"\x06") != b"\x06":
                raise RadioError(f"Missing read ACK at 0x{address:04x}")
        return bytes(memory)

    def write_block(self, address, data):
        if address % 16 or not 0 <= address < 0x100 or len(data) != 16:
            raise ValueError("Writes are restricted to aligned channel records 0x0000..0x00ff")
        command = struct.pack(">cHB", b"W", address, 16) + data
        if self.exchange(command, detect_echo=True) != b"\x06":
            raise RadioError(f"Missing write ACK at 0x{address:04x}")


@contextmanager
def connect(port_name, events):
    import serial
    options = {"exclusive": True} if os.name == "posix" else {}
    port = serial.Serial(port=None, baudrate=9600, timeout=0.05, write_timeout=1,
                         rtscts=False, dsrdtr=False, xonxoff=False, **options)
    protocol = Protocol(port, events)
    try:
        port.dtr = port.rts = False
        port.port = port_name
        port.open()
        time.sleep(0.3)
        port.reset_input_buffer()
        protocol.enter()
        yield protocol
    finally:
        try:
            if protocol.entered:
                protocol.exit()
        finally:
            port.close()


def program(protocol, baseline, target, record, save_snapshot):
    addresses = changed_blocks(baseline, target)
    current = protocol.read_image()
    save_snapshot("before", current)
    if current != baseline:
        raise RadioError("Live memory differs from this plan's baseline; no writes made. Read and plan again.")
    record["planned_blocks"] = addresses
    if not addresses:
        record.update(result="unchanged", readback_sha256=sha256(current))
        save_snapshot("readback", current)
        return
    attempted = []
    try:
        protocol.restart()
        for address in addresses:
            attempted.append(address)
            protocol.write_block(address, target[address:address + 16])
        protocol.restart()
        readback = protocol.read_image()
        save_snapshot("readback", readback)
        if readback != target:
            record["mismatches"] = [i for i, (a, b) in enumerate(zip(readback, target)) if a != b]
            raise RadioError("Full readback differs from the plan")
        record.update(result="verified", readback_sha256=sha256(readback))
    except BaseException:
        if attempted:
            record["rollback"] = "attempted"
            try:
                protocol.restart()
                for address in attempted:
                    protocol.write_block(address, baseline[address:address + 16])
                protocol.restart()
                restored = protocol.read_image()
                save_snapshot("rollback", restored)
                if restored != baseline:
                    raise RadioError("Rollback readback mismatch")
                record["rollback"] = "verified"
            except BaseException as exc:
                record.update(rollback="failed", rollback_error=str(exc))
        raise
