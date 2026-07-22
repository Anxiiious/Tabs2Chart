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
    _MAX_GPIF_SIZE,
    decompress_bcfz,
    extract_gpif,
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


def test_read_gpx_bytes_reads_zip_container():
    # Real Sheet Happens tabs turned out to be this format (GP7's plain-zip
    # ".gp", not the legacy GP6 BCFS ".gpx") — see SHRED2CHART_GAMEPLAN.md.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Content/score.gpif", '<?xml version="1.0"?><GPIF>hi</GPIF>')
        zf.writestr("VERSION", "7.0")
    files = {f.name: f.data for f in read_gpx_bytes(buf.getvalue())}
    assert files["Content/score.gpif"] == b'<?xml version="1.0"?><GPIF>hi</GPIF>'
    assert files["VERSION"] == b"7.0"


def test_extract_gpif_from_zip_container(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Content/score.gpif", '<?xml version="1.0"?><GPIF>hi</GPIF>')
    gp_file = tmp_path / "song.gp"
    gp_file.write_bytes(buf.getvalue())
    assert extract_gpif(gp_file) == '<?xml version="1.0"?><GPIF>hi</GPIF>'


def test_read_gpx_bytes_rejects_unknown_magic():
    with pytest.raises(GpxFormatError):
        read_gpx_bytes(b"\xde\xad\xbe\xef" + b"\0" * 20)


def test_unpack_bcfs_rejects_out_of_range_block_index():
    # A single-sector payload (sector 0 only) whose index sector claims a
    # block far beyond what the payload actually contains.
    payload = bytearray(2 * SECTOR_SIZE)  # sector 0 (superblock) + sector 1 (index)
    index_off = SECTOR_SIZE
    struct.pack_into("<i", payload, index_off, 2)  # marker: index sector
    name = b"score.gpif"
    payload[index_off + 4:index_off + 4 + len(name)] = name
    struct.pack_into("<i", payload, index_off + 0x8C, SECTOR_SIZE)  # file_size
    struct.pack_into("<i", payload, index_off + 0x94, 999999)  # bogus block index
    with pytest.raises(GpxFormatError, match="out-of-range block"):
        unpack_bcfs(bytes(payload))


def test_decompress_bcfz_rejects_implausible_declared_length():
    # A tiny payload declaring an absurdly large decompressed size must be
    # rejected up front rather than driving an unbounded allocation.
    payload = struct.pack("<i", 10**9) + b"\x00" * 8
    with pytest.raises(GpxFormatError, match="implausible"):
        decompress_bcfz(payload)


def test_extract_gpif_rejects_oversized_zip_entry(tmp_path, monkeypatch):
    # Build a real (tiny) zip so zipfile.is_zipfile() and the outer magic
    # sniff succeed, then fake out just the ZipFile class the extractor
    # uses internally so its declared entry size is implausibly large —
    # this checks the size guard fires *before* any read/decompress of
    # the entry, without needing an actual oversized fixture on disk.
    import shred2chart.gpx_reader as gpx_reader_module

    class _FakeInfo:
        filename = "Content/score.gpif"
        file_size = _MAX_GPIF_SIZE + 1

        def is_dir(self):
            return False

    class _FakeZipFile:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def infolist(self):
            return [_FakeInfo()]

        def read(self, info):
            raise AssertionError("must not read an entry whose declared size failed the guard")

    real_buf = io.BytesIO()
    with zipfile.ZipFile(real_buf, "w") as zf:
        zf.writestr("Content/score.gpif", "<GPIF/>")
    gp_file = tmp_path / "song.gp"
    gp_file.write_bytes(real_buf.getvalue())

    monkeypatch.setattr(gpx_reader_module.zipfile, "ZipFile", _FakeZipFile)
    with pytest.raises(GpxFormatError, match="safety limit"):
        extract_gpif(gp_file)
