"""Remnant 2 save packer/unpacker (format per AndrewSav/lib.remnant2.saves).

Compressed file: uint32 crc32, int32 decompressed_size, int32 version,
then Unreal chunks (tag 0x222222229E2A83C1, zlib, 0x20000 bytes each).

The unpacked ("full") form is: crc32, size, version, then the save body.
CRC32 covers everything after the first 4 bytes.

  python r2save.py unpack profile.sav profile.raw
  python r2save.py pack   profile.raw profile.sav   (size + crc are recomputed)
"""
import struct
import sys
import zlib

TAG = 0x222222229E2A83C1
CHUNK = 0x20000
CHUNK_HDR = "<QQBQQQQ"


def unpack(data):
    crc, size, version = struct.unpack_from("<IiI", data, 0)
    pos, stream = 12, bytearray()
    while pos < len(data):
        tag, _, comp, csize, *_ = struct.unpack_from(CHUNK_HDR, data, pos)
        assert tag == TAG and comp == 3, f"bad chunk at {pos:#x}"
        pos += struct.calcsize(CHUNK_HDR)
        stream += zlib.decompress(data[pos:pos + csize])
        pos += csize
    full = struct.pack("<IiI", crc, size, version) + bytes(stream[4:])
    if zlib.crc32(full[4:]) & 0xFFFFFFFF != crc:
        raise ValueError("crc32 mismatch")
    return full


def pack(full):
    full = bytearray(full)
    version = struct.unpack_from("<i", full, 8)[0]
    size = len(full)
    struct.pack_into("<i", full, 4, size)
    crc = zlib.crc32(full[4:]) & 0xFFFFFFFF
    out = bytearray(struct.pack("<IiI", crc, size, version))
    stream = struct.pack("<i", size - 12) + bytes(full[12:])
    for i in range(0, len(stream), CHUNK):
        piece = stream[i:i + CHUNK]
        c = zlib.compress(piece)
        out += struct.pack(CHUNK_HDR, TAG, CHUNK, 3, len(c), len(piece), len(c), len(piece)) + c
    return bytes(out)


if __name__ == "__main__":
    cmd, src, dst = sys.argv[1:4]
    data = open(src, "rb").read()
    if cmd == "unpack":
        open(dst, "wb").write(unpack(data))
    elif cmd == "pack":
        open(dst, "wb").write(pack(data))
    print(cmd, "ok ->", dst)
