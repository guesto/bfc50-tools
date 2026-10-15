# Validation / Проверка

## Hardware, 2026-10-15

The package was built as a wheel and installed into a separate virtual environment. Its installed `bfc50` executable was invoked from outside the source checkout.

On one connected BF-C50 using Ubuntu 26.04.1, Python 3.14.4, PySerial 3.5 and a Prolific PL2303 cable:

1. Downloaded a 1632-byte baseline.
2. Prepared a minimal plan: only channel 16 TX/RX CTCSS changed from OFF to 91.5 Hz; all other records and global bytes preserved.
3. Ran `bfc50 write --apply`: received write acknowledgement and an exact full-image readback match.
4. Applied a separately prepared restoration plan: exact full-image readback match with the original baseline.
5. Ran an independent `bfc50 verify`: all 1632 original bytes matched.

The baseline and restored SHA256 were both `a109fd34ccce4c22985af864aae2838ff70fb47f52f4eb062ba3d82b74151faa`. The source radio's existing 16-channel configuration was preserved after the test. A machine-readable summary is in [hardware-validation.json](hardware-validation.json); raw user backups and serial logs are kept outside this repository.

An earlier entry attempt received no ACK; a subsequent legacy probe and the installed CLI read succeeded. A single no-response event does not establish its cause. Check power state, cable seating and logs if this occurs.

These tests confirm serial configuration exchange, not measured RF frequency, power, deviation or emitted tone. No PTT operation was requested during this acceptance test. Earlier scripts were used to configure two units, and the user confirmed radio-to-radio communication.

## Offline checks

The unit suite uses only synthetic memory images and a serial radio simulator. It covers CSV validation, BCD encoding, memory boundaries, wrong baselines, complete readback, echo variants, session changes, lost ACKs, successful/failed rollback, checksum checks and dry-run behavior. Run:

```bash
source .venv/bin/activate
python -m unittest discover -s tests -v
```

Local Python version: 3.14.4. The GitHub Actions matrix also requests 3.10 and 3.12; those environments are verified by CI after publication, not by claiming a local run.

## Русский

Проверен установленный wheel-пакет из отдельного окружения: чтение, временная запись CTCSS в единственный канал 16, полное обратное чтение, восстановление и отдельная команда `verify`. После теста все 1632 байта совпали с исходной копией. Рабочая пользовательская сетка сохранена. Первоначальный протокол ранее применён на двух рациях; новую упаковку аппаратно проверяли на одной.

Измерения RF-параметров не проводились. Локальные автоматические проверки выполняются на Python 3.14.4; остальные версии указаны как задания CI. Документация о двухсекундном нажатии кнопки и отсутствии признаков питания относится к наблюдениям на двух пользовательских экземплярах с подключённым кабелем.
