# bfc50-tools

[Русская инструкция](README.ru.md) · English

Experimental Python tools for backing up, inspecting and programming **Baofeng BF-C50 channel memory**. Application firmware is not replaced.

This work was motivated by the incomplete BF-C50 support described in [CHIRP issue #10176](https://chirpmyradio.com/issues/10176). It provides a standalone approach and documented protocol findings; it does not modify CHIRP itself.

The serial protocol and channel layout were exercised with earlier scripts on **two BF-C50 units** using a Prolific PL2303 cable on Ubuntu. This packaged CLI is a refactoring of that work, with a serial-radio simulator and offline tests. Other hardware revisions are unverified. This is an independent project, not an official Baofeng product or a CHIRP driver.

The installed package also completed a hardware acceptance test on one connected unit: backup, a temporary CTCSS change in channel 16, complete readback, restoration of the original configuration and independent verification. All original bytes were restored. See [validation](docs/VALIDATION.md).

## Default profile

The bundled `profiles/pmr446-8.csv` contains the first eight analogue PMR446 frequencies from the requested Russian-use channel plan. All channels are **simplex NFM, with TX and RX CTCSS OFF**.

| Channel | Frequency, MHz | TX CTCSS | RX CTCSS |
|---|---:|---|---|
| 1 | 446.006250 | OFF | OFF |
| 2 | 446.018750 | OFF | OFF |
| 3 | 446.031250 | OFF | OFF |
| 4 | 446.043750 | OFF | OFF |
| 5 | 446.056250 | OFF | OFF |
| 6 | 446.068750 | OFF | OFF |
| 7 | 446.081250 | OFF | OFF |
| 8 | 446.093750 | OFF | OFF |

By default, unlisted slots **9–16 are disabled**. `plan --unused preserve` instead retains unlisted records. The program preserves the radio's global settings and existing channel power bits. A frequency profile does not establish RF compliance: actual transmit power, deviation and emissions were not measured, and current national operating requirements must be checked separately. PMR446 background: [CEPT ECC/DEC/(15)05](https://docdb.cept.org/document/448).

## Install

Python **3.10+** and a USB programming cable are required. Linux is the hardware-tested platform; Windows/macOS serial support is not hardware-tested here.

From a downloaded/cloned checkout:

```bash
cd bfc50-tools
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
bfc50 --help
```

On Windows activate with `.venv\Scripts\Activate.ps1` in PowerShell and use a port such as `COM3`. Commands can also be invoked as `python -m bfc50_tools`.

## Connect and back up

With the radio switched off, fully seat both programming plugs, then switch it on. Do not press PTT during programming.

On the two tested units, connecting the programming cable removed the usual audible/visible power cues. Hold the power button for **about 2 seconds** for each intended power on/off operation; a silent radio does not establish which state it is in. Use `probe` to check communication. Do not press PTT, and do not toggle power during an active read or write.

```bash
bfc50 ports
# Linux example; replace the port with the one actually listed:
sudo setfacl -m "u:$(id -un):rw" /dev/ttyUSB0
bfc50 probe --port /dev/ttyUSB0
bfc50 read --port /dev/ttyUSB0 --output backups/radio1.bin
bfc50 inspect --image backups/radio1.bin
```

`setfacl` is Linux-specific. Device permissions may need to be granted again after USB reconnection. A `/dev/serial/by-id/...` path can be used instead of `/dev/ttyUSB0`. `probe` enters programming mode and checks the tested ID `50 33 31 30 37 f7 00 00`; that ID is not a unique model or serial-number guarantee. Only use this tool with a BF-C50 whose layout you have checked.

`read` saves a **1632-byte raw configuration backup**, a sibling JSON with SHA256, and a serial exchange log. It refuses to overwrite an existing backup. Keep each radio's own backup.

## Prepare, review, write, verify

First prepare an offline plan with the bundled eight-channel profile:

```bash
bfc50 plan --baseline backups/radio1.bin --output plans/radio1-pmr
```

This creates three files:

- `plans/radio1-pmr.json`: target channel settings, changed channel records and hashes.
- `plans/radio1-pmr.baseline.bin`: a copy of the chosen baseline.
- `plans/radio1-pmr.bin`: the intended complete readback image.

Keep those three files together. Review the printed settings and JSON. Preparing a plan never opens a serial port.

```bash
# A further dry run; this does not open the port:
bfc50 write --port /dev/ttyUSB0 --plan plans/radio1-pmr.json

# Perform the reviewed write:
bfc50 write --port /dev/ttyUSB0 --plan plans/radio1-pmr.json --apply

# Optional independent readback verification:
bfc50 verify --port /dev/ttyUSB0 --plan plans/radio1-pmr.json
```

Before any write, live memory must exactly match the plan's baseline. Only changed, aligned 16-byte records inside `0x0000..0x00ff` can be written. Each block must be acknowledged, then the complete 1632-byte image is read and compared. On a write/readback error, the tool attempts to restore affected records and reports whether restoration was verified. A failed rollback requires investigation; do not assume an error means the memory is unchanged.

Logs and before/readback/rollback snapshots go to `logs/`; `--log-dir` selects another directory. Successful output includes `VERIFIED: full memory matches the plan`. A no-change plan reports `UNCHANGED` and writes no channel records.

If live memory differs, read a new backup and prepare a new plan. Do not reuse one radio's baseline for another. Merely switching channels can change byte `0x0645`; strict verification after normal use may then fail even if `channels_match` is true in the log. Review the reported differences rather than replacing the radio's state blindly.

After completion, disconnect the cable, restart the radio and test communication. For a second radio, repeat from its own `read` command using new output names.

## Make your own settings

Create an editable copy of the built-in profile, even outside the checkout:

```bash
bfc50 init-profile --output my-channels.csv
```

Edit CSV with a text editor or spreadsheet application, keeping **UTF-8, commas and decimal points**. This project uses its own small CSV schema; it is not a CHIRP CSV import format.

```csv
Channel,Name,Frequency,TxCTCSS,RxCTCSS,Mode,Template
1,Group,446.006250,91.5,91.5,NFM,
2,Open,446.018750,OFF,OFF,NFM,
9,Open-copy-1,446.006250,OFF,OFF,NFM,1
```

| Field | Meaning |
|---|---|
| `Channel` | Unique slot 1–16; rows may be sparse or reordered |
| `Name` | Label for your CSV, not written to the screenless radio |
| `Frequency` | RX and TX frequency in MHz; simplex only; whole 10 Hz units |
| `TxCTCSS` | `OFF`, blank, or a standard CTCSS tone in Hz |
| `RxCTCSS` | Independently selected RX tone, or `OFF`/blank |
| `Mode` | `NFM`; other modes are not exposed by this release |
| `Template` | Blank preserves this slot's original flags/unknown bytes. To activate an empty slot, specify an active channel from the **baseline**, e.g. `1`. Its power bits/other unknown parameters are copied. |

The parser accepts UHF frequencies 400–470 MHz based on the researched driver range; those limits do not establish RF performance or permission to transmit. VHF 145 MHz, DCS writing, repeater shifts and power adjustment are not implemented. CTCSS filtering does not make a channel private.

Prepare another offline plan after editing:

```bash
bfc50 plan --baseline backups/radio1.bin --profile my-channels.csv \
  --unused preserve --output plans/radio1-custom
bfc50 write --port /dev/ttyUSB0 --plan plans/radio1-custom.json --apply
```

Use `--unused preserve` when the profile contains only the slots you want to update. Omit it to disable every unlisted slot. `Template` never refers to another newly defined CSV row: it refers to the saved baseline record. Plan files are generated artifacts; edit the CSV and regenerate instead of hand-editing their images or hashes.

## Tests and development

```bash
python -m unittest discover -s tests -v
python -m compileall -q bfc50_tools
python -m bfc50_tools --help
```

The test fixtures are synthetic. Tests cover profile validation, binary encoding, global-data preservation, separate clone sessions, echo variants, wrong baselines, lost ACKs, rollback and offline CLI behavior. They never open a physical port. GitHub Actions runs tests on Python 3.10, 3.12 and 3.14.

For hardware changes, first test `probe` and `read`, then a reviewed minimal channel update with its own backup. Report model label, ID, OS, cable type, exact commands, result and relevant log. Remove unrelated local paths before posting logs.

## Project and publishing

- [Русская инструкция](README.ru.md)
- [Protocol and tested scope](docs/PROTOCOL.md)
- [Publishing instructions / Публикация](docs/PUBLISH.md)
- [Sources and acknowledgements](NOTICE.md)

License: [GPL-3.0-or-later](LICENSE). Code was developed with AI assistance; physical operations and functional radio-to-radio checks were performed by the user. No vendor firmware is distributed.
