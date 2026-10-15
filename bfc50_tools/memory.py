"""Channel layout verified on two BF-C50 units.

SPDX-License-Identifier: GPL-3.0-or-later
Protocol/layout references are credited in docs/PROTOCOL.md and NOTICE.md.
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import csv
import hashlib
import json
from pathlib import Path

IMAGE_SIZE = 0x660
CHANNEL_END = 0x100
BLOCK_SIZE = 16
RADIO_ID = bytes.fromhex("50 33 31 30 37 f7 00 00")
PMR_HZ = tuple(446006250 + 12500 * i for i in range(8))
CTCSS = frozenset(Decimal(value) for value in (
    "67.0 69.3 71.9 74.4 77.0 79.7 82.5 85.4 88.5 91.5 94.8 97.4 "
    "100.0 103.5 107.2 110.9 114.8 118.8 123.0 127.3 131.8 136.5 "
    "141.3 146.2 151.4 156.7 159.8 162.2 165.5 167.9 171.3 173.8 "
    "177.3 179.9 183.5 186.2 189.9 192.8 196.6 199.5 203.5 206.5 "
    "210.7 218.1 225.7 229.1 233.6 241.8 250.3 254.1"
).split())


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def check_image(data):
    if len(data) != IMAGE_SIZE:
        raise ValueError(f"Expected {IMAGE_SIZE} bytes, got {len(data)}")


def bcd_encode(value, size):
    if not isinstance(value, int) or not 0 <= value < 10 ** (size * 2):
        raise ValueError("BCD value out of range")
    return bytes.fromhex(f"{value:0{size * 2}d}")[::-1]


def bcd_decode(data):
    digits = data[::-1].hex()
    if not digits.isdecimal():
        raise ValueError(f"Invalid BCD: {data.hex()}")
    return int(digits)


def frequency_hz(text):
    try:
        value = Decimal(text.strip()) * 1000000
    except (InvalidOperation, AttributeError) as exc:
        raise ValueError("Frequency must be a decimal MHz value") from exc
    if not value.is_finite() or not 400000000 <= value <= 470000000 or value % 10:
        raise ValueError("Frequency must be 400..470 MHz in whole 10 Hz units")
    return int(value)


def ctcss_value(text):
    if text.strip().upper() in {"", "OFF"}:
        return None
    try:
        value = Decimal(text.strip())
    except InvalidOperation as exc:
        raise ValueError("CTCSS must be OFF or a standard tone in Hz") from exc
    if not value.is_finite() or value not in CTCSS:
        raise ValueError("Unsupported CTCSS; use OFF or a standard tone such as 91.5")
    return value


@dataclass(frozen=True)
class Channel:
    number: int
    name: str
    frequency: int
    tx_ctcss: Decimal | None
    rx_ctcss: Decimal | None
    template: int | None = None


def load_profile(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        required = {"Channel", "Name", "Frequency", "TxCTCSS", "RxCTCSS", "Mode", "Template"}
        if not reader.fieldnames or set(reader.fieldnames) != required or len(reader.fieldnames) != len(required):
            raise ValueError("CSV header must be Channel,Name,Frequency,TxCTCSS,RxCTCSS,Mode,Template")
        result = []
        seen = set()
        for line, row in enumerate(reader, 2):
            try:
                if None in row or any(value is None for value in row.values()):
                    raise ValueError("Incorrect CSV column count")
                number = int(row["Channel"])
                if not 1 <= number <= 16 or number in seen:
                    raise ValueError("Channel must be unique and between 1 and 16")
                if row["Mode"].strip().upper() != "NFM":
                    raise ValueError("Only the tested NFM mode is supported")
                template = int(row["Template"]) if row["Template"].strip() else None
                if template is not None and not 1 <= template <= 16:
                    raise ValueError("Template must be an existing baseline channel 1..16")
                result.append(Channel(number, row["Name"].strip(), frequency_hz(row["Frequency"]),
                                      ctcss_value(row["TxCTCSS"]), ctcss_value(row["RxCTCSS"]), template))
                seen.add(number)
            except (ValueError, InvalidOperation) as exc:
                raise ValueError(f"CSV line {line}: {exc}") from exc
    if not result:
        raise ValueError("Profile is empty")
    return sorted(result, key=lambda channel: channel.number)


def record_is_empty(record):
    return record[:4] == b"\xff" * 4


def build_image(baseline, channels, unused="disable"):
    check_image(baseline)
    if unused not in {"disable", "preserve"}:
        raise ValueError("Unused policy must be disable or preserve")
    if not channels or len({c.number for c in channels}) != len(channels):
        raise ValueError("Profile needs unique channels")
    target = bytearray(baseline)
    configured = set()
    for channel in channels:
        if not 1 <= channel.number <= 16:
            raise ValueError("Channel outside 1..16")
        # Validate constructed Channel objects as well as CSV input.
        frequency_hz(str(Decimal(channel.frequency) / 1000000))
        for tone in (channel.tx_ctcss, channel.rx_ctcss):
            if tone is not None and tone not in CTCSS:
                raise ValueError("Unsupported CTCSS")
        template = channel.template or channel.number
        if not 1 <= template <= 16:
            raise ValueError("Template outside 1..16")
        source = baseline[(template - 1) * 16:template * 16]
        if record_is_empty(source):
            raise ValueError(f"Channel {channel.number}: empty template {template}; select an active baseline channel in Template")
        bcd_decode(source[:4])
        record = bytearray(source)
        freq = bcd_encode(channel.frequency // 10, 4)
        record[:8] = freq * 2  # Simplex only.
        record[8:10] = b"\xff\xff" if channel.rx_ctcss is None else bcd_encode(int(channel.rx_ctcss * 10), 2)
        record[10:12] = b"\xff\xff" if channel.tx_ctcss is None else bcd_encode(int(channel.tx_ctcss * 10), 2)
        record[12] |= 0x04
        target[(channel.number - 1) * 16:channel.number * 16] = record
        configured.add(channel.number)
    if unused == "disable":
        for number in set(range(1, 17)) - configured:
            target[(number - 1) * 16:number * 16] = b"\xff" * 16
    if target[CHANNEL_END:] != baseline[CHANNEL_END:]:
        raise ValueError("Attempt to change non-channel memory")
    return bytes(target)


def changed_blocks(baseline, target):
    check_image(baseline)
    check_image(target)
    if baseline[CHANNEL_END:] != target[CHANNEL_END:]:
        raise ValueError("Changes outside channels 1..16 are forbidden")
    return [address for address in range(0, CHANNEL_END, 16)
            if baseline[address:address + 16] != target[address:address + 16]]


def describe_image(data):
    check_image(data)
    result = []
    for number in range(1, 17):
        raw = data[(number - 1) * 16:number * 16]
        channel = {"channel": number, "empty": record_is_empty(raw)}
        if not channel["empty"]:
            try:
                channel.update(rx_hz=bcd_decode(raw[:4]) * 10,
                               tx_hz=None if raw[4:8] == b"\xff" * 4 else bcd_decode(raw[4:8]) * 10,
                               mode="NFM" if raw[12] & 4 else "FM",
                               flags=raw[12:].hex(" "))
                for label, tone in (("rx_tone", raw[8:10]), ("tx_tone", raw[10:12])):
                    if tone == b"\xff\xff":
                        channel[label] = "OFF"
                    elif tone[1] & 0x80:
                        value = bcd_decode(bytes((tone[0], tone[1] & 0x3f)))
                        channel[label] = f"D{value:03d}{'R' if tone[1] & 0x40 else 'N'}"
                    else:
                        channel[label] = f"{Decimal(bcd_decode(tone)) / 10:.1f}"
            except ValueError as exc:
                channel["decode_error"] = str(exc)
                channel["raw"] = raw.hex(" ")
        result.append(channel)
    return result


def save_plan(baseline, target, output, unused):
    addresses = changed_blocks(baseline, target)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    base_path = Path(f"{output}.baseline.bin")
    image_path = Path(f"{output}.bin")
    json_path = Path(f"{output}.json")
    if any(path.exists() for path in (base_path, image_path, json_path)):
        raise ValueError("Plan output already exists; choose a new output prefix")
    metadata = {"format": "bfc50-plan-v1", "radio_id": RADIO_ID.hex(),
                "baseline": base_path.name, "target": image_path.name,
                "baseline_sha256": sha256(baseline), "target_sha256": sha256(target),
                "unused": unused, "changed_blocks": addresses, "channels": describe_image(target)}
    base_path.write_bytes(baseline)
    image_path.write_bytes(target)
    json_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return json_path, metadata


def load_plan(path):
    path = Path(path)
    metadata = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(metadata, dict) or metadata.get("format") != "bfc50-plan-v1" or metadata.get("radio_id") != RADIO_ID.hex():
        raise ValueError("Unsupported plan format or radio ID")
    images = []
    for key in ("baseline", "target"):
        name = metadata.get(key)
        if not isinstance(name, str) or Path(name).name != name or name in {"", ".", ".."}:
            raise ValueError("Plan image references must be plain filenames")
        data = (path.parent / name).read_bytes()
        check_image(data)
        if sha256(data) != metadata.get(f"{key}_sha256"):
            raise ValueError(f"{key} image checksum mismatch")
        images.append(data)
    addresses = changed_blocks(*images)
    if addresses != metadata.get("changed_blocks"):
        raise ValueError("Plan change list does not match its images")
    # Every changed active record must be within supported encoding limits.
    for address in addresses:
        raw = images[1][address:address + 16]
        if not record_is_empty(raw):
            frequency_hz(str(Decimal(bcd_decode(raw[:4])) / 100000))
            if raw[:4] != raw[4:8] or not raw[12] & 4:
                raise ValueError("Changed channels must be simplex NFM")
            for value in (raw[8:10], raw[10:12]):
                if value != b"\xff\xff":
                    ctcss_value(str(Decimal(bcd_decode(value)) / 10))
        elif raw != b"\xff" * 16:
            raise ValueError("Disabled channel must contain 16 FF bytes")
    return metadata, images[0], images[1]
