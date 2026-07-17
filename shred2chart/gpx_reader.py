"""Reader for Guitar Pro container files — both the legacy GP6 `.gpx`
format and the modern GP7/8 `.gp` format — down to their contained
`score.gpif` XML (the actual notation data: notes, tempo automations, etc).

Two containers in the wild, handled transparently here:

- **GP7/8 `.gp`**: a plain zip archive (`Content/score.gpif` inside).
  Real Sheet Happens tabs turned out to be this format — see
  SHRED2CHART_GAMEPLAN.md's Current State for how that was confirmed.
  GP8 files can have an *encrypted* score.gpif; we detect that case and
  raise a clear error rather than silently returning garbage.
- **GP6 `.gpx`**: NOT a zip. Either a raw BCFS "virtual filesystem" or
  that same filesystem compressed with a proprietary scheme called BCFZ.
  This half of the reader follows the format as reverse-engineered by
  the rust-gpx-reader project (github.com/Antti/rust-gpx-reader), the
  most complete public writeup we found, and is unit-tested against
  hand-built fixtures but has not been exercised on a real `.gpx` file
  (we haven't encountered one yet — every real sample so far has been
  the GP7 zip format above).

  One correction versus that reference: its back-reference copy loop used
  `min(length, offset)`, which truncates the classic LZ77 case where
  length > offset (an overlapping run, e.g. "repeat the last byte 10
  times"). We copy byte-by-byte instead, which handles overlap correctly.
"""
from __future__ import annotations

import io
import struct
import zipfile
from dataclasses import dataclass
from pathlib import Path

SECTOR_SIZE = 0x1000
MAGIC_BCFS = b"BCFS"
MAGIC_BCFZ = b"BCFZ"


class GpxFormatError(ValueError):
    """Raised when a .gpx file doesn't match the expected BCFS/BCFZ layout."""


@dataclass
class ContainedFile:
    name: str
    data: bytes


class _BitReader:
    """Reads individual bits from a byte string, MSB-first within each byte."""

    def __init__(self, data: bytes):
        self._data = data
        self._pos = 0  # bit offset from the start of data

    def read_bit(self) -> int:
        byte_index, bit_index = divmod(self._pos, 8)
        if byte_index >= len(self._data):
            raise GpxFormatError("BCFZ bitstream ended before decompression finished")
        self._pos += 1
        return (self._data[byte_index] >> (7 - bit_index)) & 1

    def read_bits_msb(self, count: int) -> int:
        """Assemble `count` bits into an int, first bit read = most significant."""
        value = 0
        for _ in range(count):
            value = (value << 1) | self.read_bit()
        return value

    def read_bits_lsb(self, count: int) -> int:
        """Assemble `count` bits into an int, first bit read = least significant."""
        value = 0
        for i in range(count):
            value |= self.read_bit() << i
        return value


def decompress_bcfz(payload: bytes) -> bytes:
    """Decompress the body of a BCFZ file (payload = everything after the
    4-byte "BCFZ" magic)."""
    if len(payload) < 4:
        raise GpxFormatError("BCFZ payload too short to contain a length header")

    expected_len = struct.unpack_from("<i", payload, 0)[0]
    if expected_len < 0:
        raise GpxFormatError(f"BCFZ declares a negative decompressed length: {expected_len}")

    reader = _BitReader(payload[4:])
    out = bytearray()
    while len(out) < expected_len:
        flag = reader.read_bit()
        if flag == 0:
            # Literal run: next 2 bits (LSB-first) give the byte count (0-3),
            # then that many raw bytes follow, each read MSB-first.
            length = reader.read_bits_lsb(2)
            for _ in range(length):
                out.append(reader.read_bits_msb(8))
        else:
            # Back-reference: 4 bits (MSB-first) give the bit-width used for
            # the offset/length fields that follow (both LSB-first).
            word_size = reader.read_bits_msb(4)
            offset = reader.read_bits_lsb(word_size)
            length = reader.read_bits_lsb(word_size)
            if offset == 0 or offset > len(out):
                raise GpxFormatError(
                    f"invalid back-reference offset={offset} at output length={len(out)}"
                )
            source = len(out) - offset
            for i in range(length):
                # Byte-by-byte (not a bulk slice copy) so overlapping runs
                # where length > offset repeat correctly.
                out.append(out[source + i])

    return bytes(out)


def unpack_bcfs(payload: bytes) -> list[ContainedFile]:
    """Unpack a BCFS virtual filesystem (payload = everything after the
    4-byte "BCFS" magic) into its contained files."""
    files: list[ContainedFile] = []
    n_sectors = len(payload) // SECTOR_SIZE

    # Sector 0 is the BCFS superblock; file index sectors start at sector 1.
    for sector_index in range(1, n_sectors):
        sector_off = sector_index * SECTOR_SIZE
        marker = struct.unpack_from("<i", payload, sector_off)[0]
        if marker != 2:
            continue  # not an index sector

        name_off = sector_off + 0x4
        size_off = sector_off + 0x8C
        blocks_off = sector_off + 0x94

        raw_name = payload[name_off:name_off + 127]
        file_name = raw_name.split(b"\0", 1)[0].decode("utf-8", errors="replace")
        file_size = struct.unpack_from("<i", payload, size_off)[0]

        file_data = bytearray()
        block_count = 0
        while True:
            bptr = blocks_off + 4 * block_count
            if bptr + 4 > len(payload):
                raise GpxFormatError(f"truncated block list while reading {file_name!r}")
            block = struct.unpack_from("<i", payload, bptr)[0]
            if block == 0:
                break
            block_off = block * SECTOR_SIZE
            file_data.extend(payload[block_off:block_off + SECTOR_SIZE])
            block_count += 1

        if file_size > len(file_data):
            raise GpxFormatError(
                f"{file_name!r} claims size {file_size} but only "
                f"{len(file_data)} bytes were found across its blocks"
            )
        files.append(ContainedFile(name=file_name, data=bytes(file_data[:file_size])))

    return files


def _read_zip_container(data: bytes) -> list[ContainedFile]:
    """Read a GP7/8 '.gp' file, which is just a plain zip archive."""
    files = []
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            files.append(ContainedFile(name=info.filename, data=zf.read(info)))
    return files


def read_gpx_bytes(data: bytes) -> list[ContainedFile]:
    """Parse raw Guitar Pro container bytes (`.gp` or `.gpx`) into the
    files they contain."""
    if len(data) < 4:
        raise GpxFormatError("file too short to be a Guitar Pro container")

    if zipfile.is_zipfile(io.BytesIO(data)):
        return _read_zip_container(data)

    magic = data[:4]
    if magic == MAGIC_BCFZ:
        decompressed = decompress_bcfz(data[4:])
        if decompressed[:4] != MAGIC_BCFS:
            raise GpxFormatError(
                "decompressed BCFZ payload did not start with a BCFS marker — "
                "the reverse-engineered format assumed here may not match this file"
            )
        return unpack_bcfs(decompressed[4:])
    elif magic == MAGIC_BCFS:
        return unpack_bcfs(data[4:])
    else:
        raise GpxFormatError(
            f"unrecognized magic {magic!r} — not a zip, and not BCFZ/BCFS either. "
            "This doesn't look like a Guitar Pro .gp or .gpx file."
        )


def read_gpx(path: str | Path) -> list[ContainedFile]:
    """Parse a Guitar Pro container file (`.gp` or `.gpx`) on disk into
    the files it contains."""
    with open(path, "rb") as f:
        return read_gpx_bytes(f.read())


def extract_gpif(path: str | Path) -> str:
    """Extract and decode the score.gpif XML from a `.gp`/`.gpx` file."""
    for contained in read_gpx(path):
        if contained.name.lower().endswith(".gpif"):
            try:
                text = contained.data.decode("utf-8")
            except UnicodeDecodeError as e:
                raise GpxFormatError(
                    "score.gpif isn't valid UTF-8 text — if this is a Guitar Pro 8 "
                    "file, its content may be encrypted; this tool doesn't support "
                    "that yet"
                ) from e
            if "<GPIF" not in text[:200]:
                raise GpxFormatError(
                    "score.gpif doesn't look like GPIF XML (got unexpected content "
                    "where an XML header should be) — it may be encrypted or in an "
                    "unrecognized variant of the format"
                )
            return text
    raise GpxFormatError("no *.gpif file found inside this container")
