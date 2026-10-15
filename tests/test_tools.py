"""Synthetic fixtures only; tests never open a physical serial port."""
from contextlib import contextmanager, redirect_stdout, redirect_stderr
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from bfc50_tools import cli
from bfc50_tools.memory import (Channel, IMAGE_SIZE, PMR_HZ, RADIO_ID, bcd_encode,
    build_image, changed_blocks, describe_image, frequency_hz, load_plan, load_profile,
    save_plan, sha256)
from bfc50_tools.protocol import Protocol, RadioError, program


def baseline_image():
    image = bytearray(b"\xff" * IMAGE_SIZE)
    for index in range(16):
        frequency = bcd_encode((454225000 + index * 100000) // 10, 4)
        image[index * 16:(index + 1) * 16] = frequency * 2 + bytes.fromhex("93 06 93 06 09 ab cd ef")
    image[0x630:0x650] = bytes(range(32))
    return bytes(image)


class FakeRadio:
    def __init__(self, image, echo=False, write_echo=False, bad_ack=None, corrupt=False):
        self.image = bytearray(image)
        self.echo = echo
        self.write_echo = write_echo
        self.bad_ack = bad_ack
        self.corrupt = corrupt
        self.buffer = bytearray()
        self.writes = []
        self.commands = []
        self.mode = None

    def write(self, command):
        self.commands.append(command)
        if self.echo or (self.write_echo and command[:1] == b"W"):
            self.buffer.extend(command)
        if command == b"\x02PROGRAL":
            self.buffer.extend(b"\x06")
        elif command == b"\x02":
            self.buffer.extend(RADIO_ID)
        elif command == b"\x06":
            self.buffer.extend(b"\x06")
        elif command == b"E":
            self.mode = None
        elif command[:1] == b"R":
            if self.mode == "write":
                raise AssertionError("Read requires a new session after writes")
            self.mode = "read"
            address = int.from_bytes(command[1:3], "big")
            self.buffer.extend(b"W" + command[1:] + self.image[address:address + command[3]])
        elif command[:1] == b"W":
            if self.mode == "read":
                raise AssertionError("Write requires a new session after reads")
            self.mode = "write"
            address = int.from_bytes(command[1:3], "big")
            if len(command) != 20 or command[3] != 16:
                raise AssertionError("Bad write framing")
            self.writes.append(address)
            self.image[address:address + 16] = command[4:]
            if address == self.bad_ack:
                self.bad_ack = None
                self.buffer.extend(b"\x00")
            else:
                self.buffer.extend(b"\x06")
            if self.corrupt and address == 0xf0:
                self.corrupt = False
                self.image[0] ^= 1
        else:
            raise AssertionError(f"Unexpected command: {command.hex()}")

    def read(self, count):
        result = bytes(self.buffer[:count])
        del self.buffer[:count]
        return result

    def reset_input_buffer(self):
        self.buffer.clear()


def protocol_for(radio):
    protocol = Protocol(radio, sleep=lambda _: None)
    protocol.enter()
    return protocol


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.before = baseline_image()
        self.profile = load_profile(cli.default_profile())
        self.target = build_image(self.before, self.profile)

    def test_preset_is_exactly_eight_open_channels(self):
        self.assertEqual([c.frequency for c in self.profile], list(PMR_HZ))
        self.assertEqual([c.number for c in self.profile], list(range(1, 9)))
        self.assertTrue(all(c.rx_ctcss is None and c.tx_ctcss is None for c in self.profile))
        self.assertEqual(Path("profiles/pmr446-8.csv").read_bytes(), cli.default_profile().read_bytes())

    def test_independent_decode_and_preservation(self):
        for index, expected in enumerate(PMR_HZ):
            raw = self.target[index * 16:(index + 1) * 16]
            self.assertEqual(int(raw[:4][::-1].hex()) * 10, expected)
            self.assertEqual(raw[:4], raw[4:8])
            self.assertEqual(raw[8:12], b"\xff" * 4)
            self.assertEqual(raw[12:], bytes.fromhex("0d ab cd ef"))
        self.assertEqual(self.target[0x80:0x100], b"\xff" * 128)
        self.assertEqual(self.target[0x100:], self.before[0x100:])

    def test_unused_preserve(self):
        target = build_image(self.before, self.profile, unused="preserve")
        self.assertEqual(target[0x80:], self.before[0x80:])

    def test_asymmetric_custom_ctcss(self):
        from decimal import Decimal
        custom = [Channel(3, "group", PMR_HZ[2], Decimal("91.5"), Decimal("123.0"))]
        target = build_image(self.before, custom, unused="preserve")
        self.assertEqual(target[0x28:0x2c], bytes.fromhex("30 12 15 09"))
        self.assertEqual(changed_blocks(self.before, target), [0x20])

    def test_empty_channel_requires_active_template(self):
        empty = self.target
        with self.assertRaisesRegex(ValueError, "empty template"):
            build_image(empty, [Channel(9, "open", PMR_HZ[0], None, None)])
        target = build_image(empty, [Channel(9, "open", PMR_HZ[0], None, None, 1)], unused="preserve")
        self.assertEqual(target[0x80:0x90], empty[:0x10])

    def test_vhf_nan_infinity_and_sub_10hz_rejected(self):
        for value in ("145.500", "NaN", "sNaN", "Infinity", "446.006251", "470.000010", "abc"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                frequency_hz(value)

    def test_profile_validation(self):
        header = "Channel,Name,Frequency,TxCTCSS,RxCTCSS,Mode,Template\n"
        rows = ["1,x,446.006250,OFF,OFF,NFM,\n1,y,446.018750,OFF,OFF,NFM,\n",
                "17,x,446.006250,OFF,OFF,NFM,\n", "1,x,446.006250,91.6,OFF,NFM,\n",
                "1,x,446.006250,OFF,OFF,FM,\n", "1,x,446.006250,OFF,OFF,NFM,17\n",
                "1,x,446.006250,OFF,OFF,NFM\n", ""]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.csv"
            for row in rows:
                path.write_text(header + row)
                with self.subTest(row=row), self.assertRaises(ValueError):
                    load_profile(path)

    def test_rejects_invalid_image_lengths(self):
        for size in (0, 256, IMAGE_SIZE - 1, IMAGE_SIZE + 1):
            with self.assertRaises(ValueError):
                build_image(b"\xff" * size, self.profile)

    def test_plan_roundtrip_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            prefix = Path(directory) / "nested/plan"
            path, _ = save_plan(self.before, self.target, prefix, "disable")
            _, before, target = load_plan(path)
            self.assertEqual((before, target), (self.before, self.target))
            with self.assertRaises(ValueError):
                save_plan(self.before, self.target, prefix, "disable")

    def test_tampered_plan_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path, metadata = save_plan(self.before, self.target, Path(directory) / "plan", "disable")
            target_path = path.parent / metadata["target"]
            target = bytearray(target_path.read_bytes())
            target[0x630] ^= 1
            target_path.write_bytes(target)
            with self.assertRaisesRegex(ValueError, "checksum"):
                load_plan(path)
            metadata["target_sha256"] = sha256(target)
            path.write_text(json.dumps(metadata))
            with self.assertRaisesRegex(ValueError, "outside channels"):
                load_plan(path)

    def test_plan_path_traversal_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path, metadata = save_plan(self.before, self.target, Path(directory) / "plan", "disable")
            metadata["baseline"] = "../outside.bin"
            path.write_text(json.dumps(metadata))
            with self.assertRaisesRegex(ValueError, "plain filenames"):
                load_plan(path)

    def test_invalid_plan_json_shape_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plan.json"
            path.write_text("[]")
            with self.assertRaisesRegex(ValueError, "Unsupported plan"):
                load_plan(path)

    def test_inspect_original_and_disabled_records(self):
        original = describe_image(self.before)
        self.assertEqual(original[0]["rx_tone"], "69.3")
        result = describe_image(self.target)
        self.assertEqual(result[0]["rx_tone"], "OFF")
        self.assertTrue(result[8]["empty"])


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.before = baseline_image()
        self.target = build_image(self.before, load_profile(cli.default_profile()))

    def run_program(self, radio, baseline=None, target=None):
        record, snapshots = {}, {}
        program(protocol_for(radio), baseline or self.before, target or self.target,
                record, lambda label, data: snapshots.update({label: data}))
        return record, snapshots

    def test_complete_transaction_no_echo_full_echo_and_write_echo(self):
        for options in ({}, {"echo": True}, {"write_echo": True}):
            with self.subTest(options=options):
                radio = FakeRadio(self.before, **options)
                record, snapshots = self.run_program(radio)
                self.assertEqual(record["result"], "verified")
                self.assertEqual(bytes(radio.image), self.target)
                self.assertEqual(snapshots["before"], self.before)
                self.assertEqual(snapshots["readback"], self.target)
                self.assertEqual(radio.writes, list(range(0, 0x100, 16)))

    def test_only_changed_records_written(self):
        target = build_image(self.before, [Channel(9, "x", PMR_HZ[0], None, None)], unused="preserve")
        radio = FakeRadio(self.before)
        self.run_program(radio, target=target)
        self.assertEqual(radio.writes, [0x80])

    def test_live_baseline_mismatch_never_writes(self):
        changed = bytearray(self.before)
        changed[0x645] ^= 1
        radio = FakeRadio(changed)
        with self.assertRaisesRegex(RadioError, "no writes made"):
            self.run_program(radio)
        self.assertEqual(radio.writes, [])

    def test_lost_ack_rolls_back_uncertain_block(self):
        radio = FakeRadio(self.before, bad_ack=0x20)
        record, snapshots = {}, {}
        with self.assertRaisesRegex(RadioError, "write ACK"):
            program(protocol_for(radio), self.before, self.target, record,
                    lambda label, data: snapshots.update({label: data}))
        self.assertEqual(record["rollback"], "verified")
        self.assertEqual(radio.writes, [0, 16, 32, 0, 16, 32])
        self.assertEqual(snapshots["rollback"], self.before)

    def test_readback_mismatch_rolls_back(self):
        radio = FakeRadio(self.before, corrupt=True)
        record = {}
        with self.assertRaisesRegex(RadioError, "Full readback"):
            program(protocol_for(radio), self.before, self.target, record, lambda *_: None)
        self.assertEqual(record["rollback"], "verified")
        self.assertEqual(bytes(radio.image), self.before)

    def test_noop_never_writes(self):
        radio = FakeRadio(self.target)
        record, _ = self.run_program(radio, baseline=self.target)
        self.assertEqual(record["result"], "unchanged")
        self.assertEqual(radio.writes, [])

    def test_write_bounds_enforced_before_sending(self):
        radio = FakeRadio(self.before)
        protocol = Protocol(radio)
        for address, size in ((0x630, 16), (-16, 16), (1, 16), (0, 15)):
            with self.assertRaises(ValueError):
                protocol.write_block(address, b"\x00" * size)
        self.assertEqual(radio.commands, [])

    def test_rollback_failure_reported(self):
        radio = FakeRadio(self.before, bad_ack=0x20)
        original = radio.write
        def failing_restore(command):
            original(command)
            if command[:1] == b"W" and len(radio.writes) == 4:
                radio.buffer[-1] = 0
        radio.write = failing_restore
        record = {}
        with self.assertRaisesRegex(RadioError, "write ACK"):
            program(protocol_for(radio), self.before, self.target, record, lambda *_: None)
        self.assertEqual(record["rollback"], "failed")
        self.assertIn("write ACK", record["rollback_error"])

    def test_wrong_read_address_rejected(self):
        radio = FakeRadio(self.before)
        original = radio.write
        def corrupt_header(command):
            original(command)
            if command[:1] == b"R":
                radio.buffer[2] ^= 1
        radio.write = corrupt_header
        with self.assertRaisesRegex(RadioError, "Invalid read response"):
            protocol_for(radio).read_image()
        self.assertEqual(radio.writes, [])

    def test_malformed_echo_rejected(self):
        radio = FakeRadio(self.before, echo=True)
        original = radio.write
        def corrupt_echo(command):
            original(command)
            if command == b"\x02PROGRAL":
                radio.buffer[3] ^= 1
        radio.write = corrupt_echo
        with self.assertRaisesRegex(RadioError, "entry ACK"):
            Protocol(radio).enter()
        self.assertEqual(radio.writes, [])

    def test_unknown_radio_id_rejected(self):
        radio = FakeRadio(self.before)
        original = radio.write
        def wrong_id(command):
            original(command)
            if command == b"\x02":
                radio.buffer[:] = b"UNKNOWN!"
        radio.write = wrong_id
        protocol = Protocol(radio)
        with self.assertRaisesRegex(RadioError, "Untested radio ID"):
            protocol.enter()
        self.assertTrue(protocol.entered)
        protocol.exit()
        self.assertFalse(protocol.entered)

    def test_missing_response_times_out_without_followup_commands(self):
        class Silent:
            commands = []
            def write(self, data): self.commands.append(data)
            def read(self, _): return b""
        tick = iter(i * 0.1 for i in range(30))
        radio = Silent()
        protocol = Protocol(radio, clock=lambda: next(tick))
        with self.assertRaisesRegex(RadioError, "entry ACK"):
            protocol.enter()
        self.assertEqual(radio.commands, [b"\x02PROGRAL"])


class CLITests(unittest.TestCase):
    def invoke(self, arguments):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return cli.main(arguments)

    def make_plan(self, directory):
        baseline = baseline_image()
        target = build_image(baseline, load_profile(cli.default_profile()))
        path, _ = save_plan(baseline, target, Path(directory) / "plan", "disable")
        return path, baseline, target

    def test_offline_init_profile_and_plan(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(cli, "connect") as connection:
            directory = Path(directory)
            profile = directory / "own.csv"
            self.assertEqual(self.invoke(["init-profile", "--output", str(profile)]), 0)
            self.assertEqual(self.invoke(["init-profile", "--output", str(profile)]), 2)
            baseline = directory / "backup.bin"
            baseline.write_bytes(baseline_image())
            self.assertEqual(self.invoke(["plan", "--baseline", str(baseline), "--profile", str(profile),
                                          "--output", str(directory / "plan")]), 0)
            connection.assert_not_called()

    def test_write_without_apply_never_opens_port(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(cli, "connect") as connection:
            plan, _, _ = self.make_plan(directory)
            self.assertEqual(self.invoke(["write", "--port", "unused", "--plan", str(plan)]), 0)
            connection.assert_not_called()

    def test_existing_backup_rejected_before_port_open(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(cli, "connect") as connection:
            path = Path(directory) / "backup.bin"
            path.write_bytes(b"existing")
            self.assertEqual(self.invoke(["read", "--port", "unused", "--output", str(path)]), 2)
            connection.assert_not_called()

    def test_cli_applied_write_and_log(self):
        with tempfile.TemporaryDirectory() as directory:
            plan, before, target = self.make_plan(directory)
            radio = FakeRadio(before)
            @contextmanager
            def connection(_, events):
                protocol = Protocol(radio, events, sleep=lambda _: None)
                protocol.enter()
                try: yield protocol
                finally: protocol.exit()
            with patch.object(cli, "connect", connection):
                self.assertEqual(self.invoke(["write", "--port", "fake", "--plan", str(plan),
                                              "--apply", "--log-dir", str(Path(directory) / "logs")]), 0)
            self.assertEqual(bytes(radio.image), target)
            log = json.loads(next((Path(directory) / "logs").glob("*.json")).read_text())
            self.assertEqual(log["result"], "verified")

    def test_read_cli_saves_backup_and_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            image = baseline_image()
            @contextmanager
            def connection(_, events):
                protocol = Protocol(FakeRadio(image), events)
                protocol.enter()
                try: yield protocol
                finally: protocol.exit()
            output = Path(directory) / "backup.bin"
            with patch.object(cli, "connect", connection):
                self.assertEqual(self.invoke(["read", "--port", "fake", "--output", str(output),
                                              "--log-dir", str(Path(directory) / "logs")]), 0)
            self.assertEqual(output.read_bytes(), image)
            self.assertEqual(json.loads(output.with_suffix(".json").read_text())["sha256"], sha256(image))

    def test_verify_reports_channel_match_when_selected_channel_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            plan, _, target = self.make_plan(directory)
            actual = bytearray(target)
            actual[0x645] ^= 1
            @contextmanager
            def connection(_, events):
                protocol = Protocol(FakeRadio(actual), events)
                protocol.enter()
                try: yield protocol
                finally: protocol.exit()
            logs = Path(directory) / "logs"
            with patch.object(cli, "connect", connection):
                self.assertEqual(self.invoke(["verify", "--port", "fake", "--plan", str(plan),
                                              "--log-dir", str(logs)]), 1)
            record = json.loads(next(logs.glob("*.json")).read_text())
            self.assertTrue(record["channels_match"])
            self.assertEqual(record["mismatches"], [0x645])


if __name__ == "__main__":
    unittest.main()
