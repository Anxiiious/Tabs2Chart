"""Tests for the BCFZ/BCFS reader.

These use hand-built byte streams rather than a real .gpx file (we don't
have a sample one in this repo yet — see SHRED2CHART_GAMEPLAN.md M0).
They confirm the bit-level decompression and sector-scanning logic match
the documented algorithm, independent of whether that algorithm turns
out to match real Sheet Happens files.
"""
from __future__ import annotations

import io
import struct
import zipfile

import pytest

from shred2chart.gpx_reader import (
    SECTOR_SIZE,
    GpxFormatError,
    decompress_bcfz,
    read_gpx_bytes,
    unpack_bcfs,
)


class _BitWriter:
    """Inverse of gpx_reader._BitReader, for building test fixtures."""

    def __init__(self):
        self._bits: list[int] = []

    def write_bits_msb(self, value: int, count: int) -> None:
        for i in range(count - 1, -1, -1):
            self._bits.append((value >> i) & 1)

    def write_bits_lsb(self, value: int, count: int) -> None:
        for i in range(count):
            self._bits.append((value >> i) & 1)

    def to_bytes(self) -> bytes:
        bits = self._bits + [0] * (-len(self._bits) % 8)
        out = bytearray()
        for i in range(0, len(bits), 8):
            byte = 0
            for bit in bits[i:i + 8]:
                byte = (byte << 1) | bit
            out.append(byte)
        return bytes(out)


def test_decompress_bcfz_literal_and_backreference():
    # Encode "abcabcabc": a 3-byte literal run "abc", then a back-reference
    # (offset=3, length=6) that repeats it twice more via overlapping copy.
    w = _BitWriter()
    w.write_bits_msb(0, 1)  # flag: uncompressed chunk
    w.write_bits_lsb(3, 2)  # literal length = 3
    for byte in b"abc":
        w.write_bits_msb(byte, 8)
    w.write_bits_msb(1, 1)  # flag: compressed chunk
    w.write_bits_msb(4, 4)  # word_size = 4 bits
    w.write_bits_lsb(3, 4)  # offset = 3
    w.write_bits_lsb(6, 4)  # length = 6

    payload = struct.pack("<i", 9) + w.to_bytes()
    assert decompress_bcfz(payload) == b"abcabcabc"


def test_decompress_bcfz_rejects_bad_offset():
    w = _BitWriter()
    w.write_bits_msb(1, 1)
    w.write_bits_msb(4, 4)
    w.write_bits_lsb(9, 4)  # offset larger than anything produced so far
    w.write_bits_lsb(1, 4)
    payload = struct.pack("<i", 1) + w.to_bytes()
    with pytest.raises(GpxFormatError):
        decompress_bcfz(payload)


def _build_bcfs_payload(entries: dict[str, bytes]) -> bytes:
    """Build a raw BCFS payload (everything after the 'BCFS' magic)
    containing the given {filename: content} entries."""
    sectors = [bytearray(SECTOR_SIZE)]  # sector 0: unused superblock
    next_block = 1  # sector 0 is reserved, data blocks start at sector 1

    for name, content in entries.items():
        blocks = []
        for i in range(0, len(content), SECTOR_SIZE) or [0]:
            chunk = content[i:i + SECTOR_SIZE].ljust(SECTOR_SIZE, b"\0")
            sectors.append(bytearray(chunk))
            blocks.append(len(sectors) - 1)

        index_sector = bytearray(SECTOR_SIZE)
        struct.pack_into("<i", index_sector, 0, 2)
        name_bytes = name.encode("utf-8")[:126]
        index_sector[4:4 + len(name_bytes)] = name_bytes
        struct.pack_into("<i", index_sector, 0x8C, len(content))
        for i, block in enumerate(blocks):
            struct.pack_into("<i", index_sector, 0x94 + 4 * i, block)
        struct.pack_into("<i", index_sector, 0x94 + 4 * len(blocks), 0)
        sectors.append(index_sector)

    return b"".join(sectors)


def test_unpack_bcfs_single_file():
    content = b"<GPIF>hello</GPIF>"
    payload = _build_bcfs_payload({"score.gpif": content})
    files = unpack_bcfs(payload)
    assert len(files) == 1
    assert files[0].name == "score.gpif"
    assert files[0].data == content


def test_unpack_bcfs_multiple_files_and_multi_sector_file():
    big_content = b"X" * (SECTOR_SIZE + 500)
    payload = _build_bcfs_payload({
        "score.gpif": b"<GPIF/>",
        "misc.xml": big_content,
    })
    files = {f.name: f.data for f in unpack_bcfs(payload)}
    assert files["score.gpif"] == b"<GPIF/>"
    assert files["misc.xml"] == big_content


def test_read_gpx_bytes_uncompressed_bcfs():
    payload = _build_bcfs_payload({"score.gpif": b"<GPIF>ok</GPIF>"})
    files = read_gpx_bytes(b"BCFS" + payload)
    assert files[0].name == "score.gpif"
    assert files[0].data == b"<GPIF>ok</GPIF>"


def test_read_gpx_bytes_rejects_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Content/score.gpif", "<GPIF/>")
    with pytest.raises(GpxFormatError, match="zip"):
        read_gpx_bytes(buf.getvalue())


def test_read_gpx_bytes_rejects_unknown_magic():
    with pytest.raises(GpxFormatError):
        read_gpx_bytes(b"\xde\xad\xbe\xef" + b"\0" * 20)
