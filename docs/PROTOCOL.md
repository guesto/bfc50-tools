# Protocol notes / Протокол

Verified with earlier scripts on two user-owned BF-C50 units on 2026-10-15, Ubuntu 26.04.1, Python 3.14.4, PySerial 3.5, PL2303 `067b:2303`. The CLI in this repository is a refactoring of that work. Hardware testing of an earlier implementation does not replace acceptance testing of subsequent changes.

## Framing

9600 8N1, no flow control. DTR/RTS are requested false before opening. Some USB drivers may still change control lines during opening.

```text
TX 02 50 52 4f 47 52 41 4c    02 + PROGRAL
RX 06
TX 02
RX 50 33 31 30 37 f7 00 00
TX 06
RX 06

Read:  TX 52 <address BE16> <length>
       RX 57 <address BE16> <length> <data>
       TX 06
       RX 06
Write: TX 57 <address BE16> <length> <data>
       RX 06
Exit:  TX 45
```

Advance immediately after a complete handshake response. An initial diagnostic probe that waited for more bytes after the entry ACK received no ID. Read/write block size: 16 bytes. Downloaded area: `0x0000..0x065f`, 1632 bytes. Writable scope enforced by this project: channel records `0x0000..0x00ff` only.

On the tested units, a write following a download in the same session returned the old addressed block instead of ACK. Separate sessions resolved it. Exit, wait 2 seconds, reset the serial input buffer, then enter again before changing direction. A 0.1-second post-upload pause failed; the minimum sufficient delay is unknown. These are observed behaviors, not a proven firmware state-machine specification.

## Memory record

Record for slot N starts at `(N - 1) * 16`:

| Offset | Bytes | Meaning |
|---|---:|---|
| 0 | 4 | RX frequency, little-endian BCD in 10 Hz units |
| 4 | 4 | TX frequency, same encoding |
| 8 | 2 | RX tone: CTCSS in 0.1 Hz units; OFF = `ff ff` |
| 10 | 2 | TX tone, same encoding |
| 12 | 1 | Flags; mask `0x04` selects the tested narrow-band configuration |
| 13 | 3 | Unknown parameters, preserved |

446.006250 MHz → `25 06 60 44`. 91.5 Hz → `15 09`. An empty channel record is 16 `ff` bytes.

Existing channel flags and unknown bytes are preserved, except the NFM bit. For an explicitly selected template, those parameters are copied from that baseline record. Global memory is preserved. Actual transmit power cannot be inferred reliably from these unmeasured flags and is not offered as an editable setting.

Inspection can display the researched DCS encoding, but this release deliberately exposes only CTCSS writing. Neither every global setting nor every channel flag was independently mapped. Byte `0x0645` changed with the selected channel during ordinary usage; it is preserved and not exempted from strict full-image verification.

## Русское пояснение

Команды чтения и записи выполняются в отдельных сеансах с двухсекундной паузой. Все подтверждения и ответы сохраняются в журнал. Перед записью требуется точное совпадение исходной копии с текущей памятью. После записи проверяется вся прочитанная область в 1632 байта. При ошибке предпринимается восстановление затронутых ячеек с отдельной проверкой результата.

Протокол проверен на двух экземплярах предыдущей реализацией. Новую упаковку и любые изменения нужно отдельно проверять на аппаратуре, начиная с чтения. Пользовательские резервные копии и журналы не включены в репозиторий. См. [источники](../NOTICE.md).
