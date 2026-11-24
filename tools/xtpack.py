#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "cryptography>=41.0.0",
#   "pillow>=10.0.0",
# ]
# ///
"""Pack a mini-app folder into a signed/encrypted XTOS Licorice .xtapp package.

Usage:
  python3 tools/xtpack.py [APP_DIR] [-o OUT] [--image FIRMWARE]
  python3 tools/xtpack.py --info FILE.xtapp [--image FIRMWARE]
  python3 tools/xtpack.py --derive-key FIRMWARE

Defaults:
  APP_DIR defaults to app/office (relative to this repository).
  OUT     defaults to dist/<app_id>.xtapp.

APP_DIR holds manifest.json, index.lua (or the manifest's "entry"), any additional
*.lua files (e.g. lockscreen.lua, excluding data/ and src/), an optional 96x96
black-on-white icon.png (packed into the cleartext XTAB/XIC icons section), and an
optional data/ folder of *.txt / *.tsv / *.csv files (packed into the encrypted XTDT
data section).

How signing/encryption works on XTOS Licorice:
  The stock firmware installer verifies every .xtapp by unwrapping a per-package
  16-byte AES-128 DEK via P256-ECDH-HKDF-SHA256-AESKW using the firmware's fixed
  device ECDH key, verifying encrypt-then-MAC HMAC-SHA256 tags on encrypted sections
  (code/data), and decrypting sections with AES-128-CTR at launch.

  Only the PUBLIC half of the firmware key (DEVICE_PUBKEY_HEX) is embedded here, so
  no firmware dump is needed to build packages. If a future firmware update rotates
  the key (causing "Install failed" on the reader), pass --image with either a
  Licorice app partition image or a raw 16 MB ESP32-S3 flash dump to derive the new
  public key automatically.

Format and protocol based on kycube/x4pro-emu (tools/xtapp.py, MIT) and
omer-faruq-xteink/touch-licorice-apps (xtpack.py & tools/netprobe/pack.py, MIT).
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import hmac
import json
import os
import re
import struct
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_APP_DIR = os.path.join(REPO_ROOT, 'app', 'office')
DEFAULT_DIST_DIR = os.path.join(REPO_ROOT, 'dist')

# XTAP container constants
MAGIC = b'XTAP'
VERSION = 1
HEADER_SIZE = 0x80
MANIFEST_OFF = 0x80
MANIFEST_MAX = 0x2000
MAX_DIR = 190

# XTOS Licorice crypto & wrap constants
LICORICE_ALG = 'P256-ECDH-HKDF-SHA256-AESKW'
LICORICE_KEY_ID = '36036010c7bd05af'
LICORICE_WRAP_INFO = b'xteink.xtapp.appdek.wrap.v2'
LICORICE_FALLBACK_SCALAR_VA = 0x3C32C674  # build 261013 fallback
_P256_N = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551

# Device ECDH public key Q = d*G (uncompressed SEC1, 65 bytes), from XTOS Licorice 26102301+
DEVICE_PUBKEY_HEX = (
    '04085a8cf979b14b0792abdb3fd97c6a347ff6cd968e33475b73d6daa4fceee7ef'
    '245b28a54ba0e310f04f227d380be89a38f54bf5516bcb518614291a569799ff'
)

# Section layout: (name, header offset of <II (off, len), feature mask bit)
LICORICE_SECTIONS = (
    ('code', 0x18, 0x02),
    ('assets', 0x20, 0x04),
    ('unused', 0x28, 0),
    ('icons', 0x30, 0),
    ('data', 0x38, 0x08),
    ('fonts', 0x40, 0x10),
)
FEATURE_CODE = 0x02
FEATURE_DATA = 0x08
LICORICE_INMEM_MAX = 0x80000  # 512 KB in-RAM code section limit

# XTCO code section table
XTCO_MAGIC = b'XTCO'
XTCO_HEAD = 16
XTCO_ENTRY = 104
XTCO_NAME = 96
XTCO_MAX_FILES = 32

# XTDT data section table
XTDT_MAGIC = b'XTDT'
XTDT_HEAD = 16
XTDT_ENTRY = 80
XTDT_NAME = 64
XTDT_MAX_FILES = 128
XTDT_NAME_RE = re.compile(r'^[A-Za-z0-9._-]{5,64}$')

# XTAB / XIC icons section (cleartext, 1-bpp)
XTAB_MAGIC = b'XTAB'
XTAB_HEAD = 64
XTAB_ENTRY = 40
XTAB_NAME = 24
XTAB_MAX_ICONS = 8
XIC_MAGIC = b'XIC\0'
ICON_INK_BIT = 1  # set bit = black pixel on device

# Manifest validation rules
API08_VERSIONS = ('1.0', '0.8', '0.7')
API08_PERMISSIONS = (
    'sys.battery', 'sys.status', 'net.http', 'net.https', 'storage.app',
    'storage.user_file', 'device.status', 'device.keep_awake', 'bluetooth',
)
API2_PERMISSIONS = ('net', 'reader', 'ble')
GENERATED_FIELDS = (
    'dek_wrap_alg', 'dek_wrap_key_id', 'dek_wrap_ephemeral_pub', 'wrapped_dek_pack',
    'iv_code', 'hmac_code', 'iv_assets', 'hmac_assets',
    'iv_data', 'hmac_data', 'iv_fonts', 'hmac_fonts',
)

# Stock ESP32-S3 OTA partition slots (16 MB flash)
FLASH_SLOTS = (0x10000, 0x7F0000)
FLASH_SLOT_LEN = 0x7E0000


class XtappError(ValueError):
    """Raised when a manifest, firmware image, or .xtapp container is invalid."""


def _import_crypto():
    try:
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.primitives.keywrap import aes_key_unwrap, aes_key_wrap
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    except ImportError:
        raise XtappError(
            'missing dependency: `cryptography` is required for P-256 ECDH / AES-KW / AES-CTR.\n'
            'Install it with:  pip install cryptography   (or run with: uv run tools/xtpack.py)'
        ) from None
    return ec, Encoding, PublicFormat, aes_key_wrap, aes_key_unwrap, Cipher, algorithms, modes


# ---------------------------------------------------------------- firmware key extraction
def _app_desc(img: bytes) -> tuple[str | None, str | None]:
    """Read (project_name, version) from esp_app_desc_t at ESP app image offset 0x20."""
    if len(img) < 0x120 or img[0] != 0xE9:
        return None, None
    magic = struct.unpack_from('<I', img, 0x20)[0]
    if magic != 0xABCD5432:
        return None, None
    ver = img[0x30:0x50].split(b'\0', 1)[0].decode('ascii', 'replace')
    name = img[0x50:0x70].split(b'\0', 1)[0].decode('ascii', 'replace')
    return name, ver


def load_licorice_app_image(path: str) -> tuple[bytes, str | None, str | None]:
    """Load a Licorice ESP app image from either an app partition binary or a 16 MB flash dump.

    Returns (app_image_bytes, project_name, version).
    """
    try:
        data = open(path, 'rb').read()
    except OSError as e:
        raise XtappError(str(e)) from None

    if len(data) < 0x120:
        raise XtappError(f'{path}: too small ({len(data)} bytes) to be an ESP32 firmware image')

    # Direct ESP app image
    if data[0] == 0xE9 and len(data) <= FLASH_SLOT_LEN:
        name, ver = _app_desc(data)
        return data, name, ver

    # Raw flash dump: inspect stock app0 / app1 OTA slots
    candidates = []
    for off in FLASH_SLOTS:
        if off + 0x120 <= len(data) and data[off] == 0xE9:
            name, ver = _app_desc(data[off:off + 0x120])
            candidates.append((off, name, ver))

    licorice = [c for c in candidates if c[1] == 'dragon_example' or 'Licorice' in (c[2] or '')]
    if not licorice:
        summary = ', '.join(f'@{off:#x}({n!r}/{v!r})' for off, n, v in candidates) or 'none'
        raise XtappError(
            f'{path}: no XTOS Licorice slot (project dragon_example) found in flash dump '
            f'(slots seen: {summary})'
        )
    off, name, ver = licorice[-1]
    return data[off:off + FLASH_SLOT_LEN], name, ver


def _esp_segment_read(img: bytes, va: int, length: int) -> bytes:
    """Read `length` bytes at virtual address `va` from an ESP app image's segment table."""
    if len(img) < 24 or img[0] != 0xE9:
        raise XtappError(f'not an ESP app image (magic 0x{img[:1].hex()})')
    off = 24
    seg_count = img[1]
    for _ in range(seg_count):
        if off + 8 > len(img):
            break
        la, ln = struct.unpack_from('<II', img, off)
        if la <= va and va + length <= la + ln:
            pos = off + 8 + (va - la)
            return img[pos:pos + length]
        off += 8 + ln
    raise XtappError(f'virtual address {va:#x} (+{length}) is not in any ESP image segment')


def locate_scalar_va(img: bytes) -> int | None:
    """Locate the 32-byte device ECDH scalar in DROM via the unique AES-KW default IV (0xa6 * 8).

    In XTOS Licorice builds (261013 at 0x3c32c674, 26102301 at 0x3c3610f0), the 32-byte scalar
    sits immediately after the 8-byte RFC 3394 default IV in DROM (0x3C000000..0x3D000000).
    """
    if len(img) < 24 or img[0] != 0xE9:
        return None
    off, hits = 24, []
    for _ in range(img[1]):
        if off + 8 > len(img):
            break
        la, ln = struct.unpack_from('<II', img, off)
        if 0x3C000000 <= la < 0x3D000000:
            seg = img[off + 8:off + 8 + ln]
            idx = seg.find(b'\xa6' * 8)
            while idx != -1:
                hits.append(la + idx + 8)
                idx = seg.find(b'\xa6' * 8, idx + 8)
        off += 8 + ln
    return hits[0] if len(hits) == 1 else None


def derive_device_key_from_image(image_path: str) -> tuple[int, bytes, int, str | None, str | None]:
    """Derive (scalar_int, pubkey_sec1_65b, scalar_va, project_name, version) from firmware."""
    ec, Encoding, PublicFormat, *_ = _import_crypto()
    img, name, ver = load_licorice_app_image(image_path)
    va = locate_scalar_va(img)
    if va is None:
        va = LICORICE_FALLBACK_SCALAR_VA
        print(
            f'WARNING: unique AES-KW IV marker not found in DROM; falling back to {va:#x}',
            file=sys.stderr,
        )
    scalar = int.from_bytes(_esp_segment_read(img, va, 32), 'big') % _P256_N
    if scalar == 0:
        raise XtappError(f'{image_path}: derived scalar at {va:#x} is zero')
    pub = ec.derive_private_key(scalar, ec.SECP256R1()).public_key()
    pub_bytes = pub.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    return scalar, pub_bytes, va, name, ver


def resolve_device_pubkey(image: str | None = None, pubkey_hex: str | None = None) -> bytes:
    """Resolve the 65-byte uncompressed SEC1 device public key."""
    if image and pubkey_hex:
        raise XtappError('pass either --image or --pubkey, not both')
    if pubkey_hex:
        try:
            pub = bytes.fromhex(pubkey_hex.strip())
        except ValueError as e:
            raise XtappError(f'invalid --pubkey hex: {e}') from None
        if len(pub) != 65 or pub[0] != 0x04:
            raise XtappError(f'--pubkey must be a 65-byte uncompressed P-256 SEC1 point (04...), got {len(pub)} bytes')
        return pub
    if not image:
        return bytes.fromhex(DEVICE_PUBKEY_HEX)
    _, pub, va, name, ver = derive_device_key_from_image(image)
    print(f'  firmware: project={name!r} version={ver!r} scalar_va={va:#x}')
    if pub.hex() != DEVICE_PUBKEY_HEX:
        print(f'  NOTE: key derived from {image} differs from the embedded key; using derived key')
        print(f'  derived pubkey: {pub.hex()}')
    return pub


# ---------------------------------------------------------------- crypto primitives
def licorice_kek(shared_x: bytes, key_id: str = LICORICE_KEY_ID) -> bytes:
    """Derive the 16-byte AES-KW KEK via HKDF-SHA256 from the P-256 ECDH shared X coordinate."""
    salt = ('xteink.xtapp.wrap.' + key_id).encode('ascii')
    prk = hmac.new(salt, bytes(shared_x), hashlib.sha256).digest()
    okm = hmac.new(prk, LICORICE_WRAP_INFO + b'\x01', hashlib.sha256).digest()
    return okm[:16]


def licorice_wrap_dek(
    pubkey: bytes,
    dek: bytes | None = None,
    key_id: str = LICORICE_KEY_ID,
) -> tuple[bytes, bytes, bytes]:
    """Wrap a 16-byte DEK for `pubkey`. Returns (dek, ephemeral_pub[65], wrapped_dek_pack[24])."""
    ec, Encoding, PublicFormat, aes_key_wrap, *_ = _import_crypto()
    dek = bytes(dek) if dek is not None else os.urandom(16)
    if len(dek) != 16:
        raise XtappError(f'DEK must be 16 bytes, got {len(dek)}')
    q_pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), bytes(pubkey))
    ephem = ec.generate_private_key(ec.SECP256R1())
    ephem_pub = ephem.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    shared = ephem.exchange(ec.ECDH(), q_pub)
    wrapped = aes_key_wrap(licorice_kek(shared, key_id), dek)
    return dek, ephem_pub, wrapped


def licorice_unwrap_dek(
    scalar: int,
    ephemeral_pub: bytes,
    wrapped: bytes,
    key_id: str = LICORICE_KEY_ID,
) -> bytes:
    """Device-side DEK unwrap (for verification/tests)."""
    ec, _, _, _, aes_key_unwrap, *_ = _import_crypto()
    priv = ec.derive_private_key(int(scalar) % _P256_N, ec.SECP256R1())
    e_pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), bytes(ephemeral_pub))
    shared = priv.exchange(ec.ECDH(), e_pub)
    return aes_key_unwrap(licorice_kek(shared, key_id), bytes(wrapped))


def licorice_section_hmac(dek: bytes, ciphertext: bytes) -> bytes:
    """Encrypt-then-MAC integrity tag: HMAC-SHA256(DEK, section_ciphertext)."""
    return hmac.new(bytes(dek), bytes(ciphertext), hashlib.sha256).digest()


def licorice_ctr(key: bytes, iv: bytes, data: bytes, offset: int = 0) -> bytes:
    """AES-128-CTR (128-bit big-endian counter, matching mbedtls_aes_crypt_ctr)."""
    *_, Cipher, algorithms, modes = _import_crypto()
    key, iv = bytes(key), bytes(iv)
    if len(key) != 16 or len(iv) != 16:
        raise XtappError(f'AES-128-CTR requires 16-byte key and IV, got {len(key)} / {len(iv)}')
    ctr = (int.from_bytes(iv, 'big') + (offset >> 4)) % (1 << 128)
    enc = Cipher(algorithms.AES(key), modes.CTR(ctr.to_bytes(16, 'big'))).encryptor()
    if offset & 15:
        enc.update(bytes(offset & 15))
    return enc.update(bytes(data)) + enc.finalize()


# ---------------------------------------------------------------- section builders & readers
def build_xtco(files: list[tuple[str, bytes]]) -> bytes:
    """Build the XTCO code section plaintext from [(rel_path, bytes)]."""
    files = [(str(n), bytes(b)) for n, b in files]
    if not 1 <= len(files) <= XTCO_MAX_FILES:
        raise XtappError(f'code section holds 1..{XTCO_MAX_FILES} files, got {len(files)}')
    seen: set[str] = set()
    table, blobs, off = bytearray(), [], 0
    for name, blob in files:
        raw = name.encode('utf-8')
        if not raw or b'\0' in raw or len(raw) >= XTCO_NAME:
            raise XtappError(f'code file name {name!r}: must be 1..{XTCO_NAME - 1} UTF-8 bytes without NUL')
        if name in seen:
            raise XtappError(f'duplicate code file {name!r}')
        seen.add(name)
        table += raw.ljust(XTCO_NAME, b'\0') + struct.pack('<II', off, len(blob))
        blobs.append(blob)
        off += len(blob)
    table_end = XTCO_HEAD + XTCO_ENTRY * len(files)
    head = XTCO_MAGIC + struct.pack('<HHII', 1, 0, len(files), table_end)
    return head + bytes(table) + b''.join(blobs)


def read_xtco(blob: bytes, where: str = 'code section') -> list[tuple[str, bytes]]:
    """Parse a decrypted XTCO code section into [(name, bytes)]."""
    blob = bytes(blob)
    if len(blob) < XTCO_HEAD or blob[:4] != XTCO_MAGIC:
        raise XtappError(f'{where}: not an XTCO section (magic {blob[:4]!r})')
    version, _, count, end = struct.unpack_from('<HHII', blob, 4)
    if version != 1:
        raise XtappError(f'{where}: XTCO version {version}, expected 1')
    if not 1 <= count <= XTCO_MAX_FILES or end != XTCO_HEAD + XTCO_ENTRY * count or end > len(blob):
        raise XtappError(f'{where}: invalid XTCO header ({count} files, table_end {end:#x})')
    out = []
    for i in range(count):
        e = XTCO_HEAD + XTCO_ENTRY * i
        name = blob[e:e + XTCO_NAME].split(b'\0')[0]
        off, length = struct.unpack_from('<II', blob, e + XTCO_NAME)
        if not name or end + off + length > len(blob):
            raise XtappError(f'{where}: entry {i} ({name!r}) out of bounds')
        out.append((name.decode('utf-8'), blob[end + off:end + off + length]))
    return out


def build_xtdt(files: list[tuple[str, bytes]]) -> bytes:
    """Build the XTDT data section plaintext from [(name, bytes)]."""
    files = [(str(n), bytes(b)) for n, b in files]
    if not 1 <= len(files) <= XTDT_MAX_FILES:
        raise XtappError(f'data section holds 1..{XTDT_MAX_FILES} files, got {len(files)}')
    table_end = XTDT_HEAD + XTDT_ENTRY * len(files)
    head = XTDT_MAGIC + struct.pack('<HHII', 1, 0, len(files), table_end)
    entries, blobs, off = bytearray(), bytearray(), 0
    for name, blob in files:
        if not XTDT_NAME_RE.match(name) or '..' in name or not name.endswith(('.txt', '.tsv')):
            raise XtappError(
                f'data file name {name!r}: must be 5..64 chars [A-Za-z0-9._-] ending in .txt or .tsv'
            )
        nb = name.encode('ascii')
        entries += nb.ljust(XTDT_NAME, b'\0') + struct.pack('<II', off, len(blob)) + bytes(XTDT_ENTRY - XTDT_NAME - 8)
        blobs += blob
        off += len(blob)
    return head + bytes(entries) + bytes(blobs)


def read_xtdt(blob: bytes, where: str = 'data section') -> list[tuple[str, bytes]]:
    """Parse a decrypted XTDT data section into [(name, bytes)]."""
    blob = bytes(blob)
    if len(blob) < XTDT_HEAD or blob[:4] != XTDT_MAGIC:
        raise XtappError(f'{where}: not an XTDT section (magic {blob[:4]!r})')
    count, table_end = struct.unpack_from('<II', blob, 8)
    if count > XTDT_MAX_FILES or table_end != XTDT_HEAD + XTDT_ENTRY * count or len(blob) < table_end:
        raise XtappError(f'{where}: invalid XTDT header')
    out = []
    for i in range(count):
        e = XTDT_HEAD + XTDT_ENTRY * i
        name = blob[e:e + XTDT_NAME].split(b'\0')[0].decode('ascii')
        off, ln = struct.unpack_from('<II', blob, e + XTDT_NAME)
        if not name or table_end + off + ln > len(blob):
            raise XtappError(f'{where}: entry {i} out of bounds')
        out.append((name, blob[table_end + off:table_end + off + ln]))
    return out


def build_xic_1bpp(img, ink_bit: int = ICON_INK_BIT) -> bytes:
    """Encode a Pillow image into the firmware's 24-byte XIC header + 1-bpp MSB-first rows."""
    from PIL import Image

    # Composite transparent images onto white so alpha never turns black
    if img.mode in ('RGBA', 'LA') or (img.mode == 'P' and 'transparency' in img.info):
        rgba = img.convert('RGBA')
        bg = Image.new('RGBA', rgba.size, (255, 255, 255, 255))
        img = Image.alpha_composite(bg, rgba).convert('L')
    else:
        img = img.convert('L')

    w, h = img.size
    stride = (w + 7) // 8
    px = img.load()
    rows = bytearray(stride * h)
    for y in range(h):
        for x in range(w):
            if (px[x, y] < 128) == bool(ink_bit):
                rows[y * stride + x // 8] |= 0x80 >> (x % 8)
    head = bytearray(24)
    head[0:4] = XIC_MAGIC
    struct.pack_into('<HHBBB', head, 4, w, h, 1, 1, 1)
    struct.pack_into('<I', head, 16, len(rows))
    return bytes(head) + bytes(rows)


def build_xtab(entries: list[tuple[str, bytes]]) -> bytes:
    """Build the cleartext XTAB icons section (grid reads app_icon_s, else app_icon_l)."""
    if not 1 <= len(entries) <= XTAB_MAX_ICONS:
        raise XtappError(f'icons section holds 1..{XTAB_MAX_ICONS} entries, got {len(entries)}')
    start = XTAB_HEAD + XTAB_ENTRY * len(entries)
    table, blobs = bytearray(), bytearray()
    for name, blob in entries:
        w, h = struct.unpack_from('<HH', blob, 4)
        fmt = blob[9]
        table += name.encode('ascii').ljust(XTAB_NAME, b'\0') + struct.pack('<IIHHB', len(blobs), len(blob), w, h, fmt) + bytes(3)
        blobs += blob
    head = bytearray(XTAB_HEAD)
    head[0:4] = XTAB_MAGIC
    struct.pack_into('<HHIIII', head, 4, 1, 0, len(entries), XTAB_HEAD, start, start + len(blobs))
    return bytes(head) + bytes(table) + bytes(blobs)


# ---------------------------------------------------------------- manifest & inputs
def load_manifest(path: str) -> collections.OrderedDict:
    try:
        raw = open(path, 'rb').read()
    except OSError as e:
        raise XtappError(str(e)) from None
    try:
        obj = json.loads(raw.decode('utf-8'), object_pairs_hook=collections.OrderedDict)
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise XtappError(f'{path}: invalid UTF-8 JSON ({e})') from None
    if not isinstance(obj, dict):
        raise XtappError(f'{path}: manifest must be a JSON object')
    return obj


def validate_manifest(obj: collections.OrderedDict, where: str = 'manifest.json') -> collections.OrderedDict:
    """Validate manifest.json for either API 2.x or API 0.8 on XTOS Licorice."""
    for key in ('app_id', 'display_name'):
        if not isinstance(obj.get(key), str) or not obj[key].strip():
            raise XtappError(f'{where}: "{key}" must be a non-empty string')

    app_id = obj['app_id']
    if not re.fullmatch(r'[A-Za-z]+', app_id):
        raise XtappError(f'{where}: app_id must be ASCII letters only, got {app_id!r}')
    if len(app_id.encode('utf-8')) > MAX_DIR:
        raise XtappError(f'{where}: app_id exceeds {MAX_DIR} bytes')

    for field in GENERATED_FIELDS:
        if field in obj:
            raise XtappError(f'{where}: remove {field!r}; xtpack.py generates it at pack time')

    entry = obj.get('entry', 'index.lua')
    if not isinstance(entry, str) or not 1 <= len(entry.encode('utf-8')) <= 31:
        raise XtappError(f'{where}: "entry" must be 1..31 UTF-8 bytes, got {entry!r}')

    is_api2 = bool(re.fullmatch(r'2\.\d+', str(obj.get('api', ''))))
    perms = obj.get('permissions', [])
    if not isinstance(perms, list):
        raise XtappError(f'{where}: "permissions" must be a list')

    if is_api2:
        bad = [p for p in perms if p not in API2_PERMISSIONS]
        if bad:
            raise XtappError(
                f'{where}: API 2.x permissions must be drawn from {API2_PERMISSIONS}, got {bad!r}'
            )
        lock = obj.get('lockscreen')
        if lock is not None:
            if not isinstance(lock, dict) or not isinstance(lock.get('entry', 'lockscreen.lua'), str):
                raise XtappError(
                    f'{where}: "lockscreen" must be an object like '
                    f'{{"enabled": true, "entry": "lockscreen.lua", "interval_sec": 3600}}'
                )
    else:
        ver = obj.get('version', '0.8')
        if ver not in API08_VERSIONS:
            raise XtappError(f'{where}: API 0.8 version must be one of {API08_VERSIONS}, got {ver!r}')
        bad = [p for p in perms if p not in API08_PERMISSIONS]
        if bad:
            raise XtappError(
                f'{where}: API 0.8 permissions must be drawn from {API08_PERMISSIONS}, got {bad!r}'
            )
    return obj


def collect_code_files(app_dir: str, entry: str = 'index.lua') -> list[tuple[str, bytes]]:
    """Collect all *.lua files under app_dir (excluding data/, src/, hidden dirs), entry first."""
    code_files: list[tuple[str, bytes]] = []
    for root, dirs, fns in os.walk(app_dir):
        dirs[:] = sorted(d for d in dirs if d not in ('data', 'src', '__pycache__') and not d.startswith('.'))
        for fn in sorted(fns):
            if fn.endswith('.lua') and not fn.startswith('.'):
                full = os.path.join(root, fn)
                rel = os.path.relpath(full, app_dir).replace(os.sep, '/')
                code_files.append((rel, open(full, 'rb').read()))
    code_files.sort(key=lambda t: (t[0] != entry, t[0]))
    if not code_files or code_files[0][0] != entry:
        raise XtappError(f'{entry} not found in {app_dir}')
    return code_files


def collect_data_files(app_dir: str) -> list[tuple[str, bytes]]:
    """Collect and normalise *.txt / *.tsv / *.csv from APP_DIR/data/ plus generated banks.tsv."""
    ddir = os.path.join(app_dir, 'data')
    if not os.path.isdir(ddir):
        return []
    files: list[tuple[str, bytes]] = []
    index: list[str] = []
    for fn in sorted(os.listdir(ddir)):
        if not fn.lower().endswith(('.txt', '.tsv', '.csv')):
            continue
        name = fn if fn.endswith(('.txt', '.tsv')) else os.path.splitext(fn)[0] + '.txt'
        raw = open(os.path.join(ddir, fn), 'rb').read().decode('utf-8', 'surrogateescape')
        lines = [ln.rstrip('\r') for ln in raw.split('\n')]
        if lines and lines[-1] == '':
            lines.pop()
        if not lines:
            print(f'  data/{fn}: empty, skipped')
            continue
        body = [ln for ln in lines if ln and not ln.startswith('#') and not ln.lower().startswith(('quizzes', 'puzzle'))]
        mode_len, mode_n = collections.Counter(len(ln) for ln in body).most_common(1)[0] if body else (0, 0)
        if body and mode_len > 0 and mode_n >= 0.9 * len(body):
            keep = [ln for ln in body if len(ln) == mode_len]
            if len(keep) != len(body):
                print(f'  data/{fn}: dropped {len(body) - len(keep)} lines != length {mode_len}')
            stride = mode_len + 1
        else:
            keep, stride = lines, 0
        blob = ('\n'.join(keep) + '\n').encode('utf-8', 'surrogateescape')
        files.append((name, blob))
        index.append(f'{name}\t{len(keep)}\t{stride}\t{os.path.splitext(fn)[0]}')
        kind = f'records, stride {stride}' if stride else 'lines, kept as-is'
        print(f'  data/{name}: {len(keep)} {kind}, {len(blob)} bytes')
    if files:
        files.insert(0, ('banks.tsv', ('\n'.join(index) + '\n').encode('utf-8')))
    return files


def collect_icons(app_dir: str) -> bytes | None:
    """Pack APP_DIR/icon.png as the cleartext 1-bpp XTAB icons section (app_icon_s), if present."""
    path = os.path.join(app_dir, 'icon.png')
    if not os.path.isfile(path):
        return None
    try:
        from PIL import Image
    except ImportError:
        raise XtappError(
            f'{path} is present, which requires Pillow to encode as 1-bpp XIC.\n'
            f'Install it with:  pip install pillow   (or remove icon.png to pack without an icon)'
        ) from None
    img = Image.open(path)
    print(f'  icon.png: {img.size[0]}x{img.size[1]}, 1 bpp')
    return build_xtab([('app_icon_s', build_xic_1bpp(img))])


# ---------------------------------------------------------------- container build & read
def build_container(
    manifest_obj: collections.OrderedDict,
    pubkey: bytes,
    code_files: list[tuple[str, bytes]],
    data_files: list[tuple[str, bytes]] | None = None,
    icons: bytes | None = None,
    *,
    dek: bytes | None = None,
    iv_code: bytes | None = None,
    iv_data: bytes | None = None,
    key_id: str = LICORICE_KEY_ID,
) -> tuple[bytes, bytes]:
    """Build a complete XTOS Licorice .xtapp container. Returns (container_bytes, dek)."""
    obj = collections.OrderedDict(manifest_obj)
    dek, ephem_pub, wrapped = licorice_wrap_dek(pubkey, dek=dek, key_id=key_id)
    obj['dek_wrap_alg'] = LICORICE_ALG
    obj['dek_wrap_key_id'] = key_id
    obj['dek_wrap_ephemeral_pub'] = ephem_pub.hex()
    obj['wrapped_dek_pack'] = wrapped.hex()

    # Code section (encrypted + HMAC'd)
    iv_c = bytes(iv_code) if iv_code is not None else os.urandom(16)
    plain_code = build_xtco(code_files)
    if len(plain_code) > LICORICE_INMEM_MAX:
        raise XtappError(
            f'code section {len(plain_code)} bytes exceeds the in-RAM limit ({LICORICE_INMEM_MAX} bytes)'
        )
    code_ct = licorice_ctr(dek, iv_c, plain_code)
    obj['iv_code'] = iv_c.hex()
    obj['hmac_code'] = licorice_section_hmac(dek, code_ct).hex()
    mask = FEATURE_CODE

    # Optional data section (encrypted + HMAC'd)
    data_ct = b''
    if data_files:
        iv_d = bytes(iv_data) if iv_data is not None else os.urandom(16)
        data_ct = licorice_ctr(dek, iv_d, build_xtdt(data_files))
        obj['iv_data'] = iv_d.hex()
        obj['hmac_data'] = licorice_section_hmac(dek, data_ct).hex()
        mask |= FEATURE_DATA

    # Compact manifest JSON
    man_blob = json.dumps(obj, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
    if not 1 <= len(man_blob) <= MANIFEST_MAX:
        raise XtappError(f'manifest is {len(man_blob)} bytes; firmware limit is 1..{MANIFEST_MAX}')

    # 16-byte aligned section offsets
    code_off = HEADER_SIZE + len(man_blob) + (-(HEADER_SIZE + len(man_blob)) % 16)
    data_off = (code_off + len(code_ct) + (-(code_off + len(code_ct)) % 16)) if data_ct else 0
    total = (data_off + len(data_ct)) if data_ct else (code_off + len(code_ct))
    icons_off = (total + (-total % 16)) if icons else 0
    if icons:
        total = icons_off + len(icons)

    # 0x80-byte XTAP header
    head = bytearray(HEADER_SIZE)
    head[0:4] = MAGIC
    struct.pack_into('<HH', head, 4, VERSION, HEADER_SIZE)
    struct.pack_into('<I', head, 0x08, total)
    struct.pack_into('<I', head, 0x0C, mask)
    struct.pack_into('<II', head, 0x10, MANIFEST_OFF, len(man_blob))
    struct.pack_into('<II', head, 0x18, code_off, len(code_ct))
    if data_ct:
        struct.pack_into('<II', head, 0x38, data_off, len(data_ct))
    if icons:
        struct.pack_into('<II', head, 0x30, icons_off, len(icons))

    out = bytearray(head)
    out += man_blob
    out += bytes(code_off - len(out))
    out += code_ct
    if data_ct:
        out += bytes(data_off - len(out))
        out += data_ct
    if icons:
        out += bytes(icons_off - len(out))
        out += icons
    return bytes(out), dek


def read_container(data: bytes, where: str = 'app.xtapp') -> tuple[dict, collections.OrderedDict, dict]:
    """Parse and bounds-check a Licorice .xtapp container. Returns (header, manifest, sections)."""
    data = bytes(data)
    if len(data) < HEADER_SIZE:
        raise XtappError(f'{where}: {len(data)} bytes, shorter than {HEADER_SIZE}-byte header')
    if data[:4] != MAGIC:
        raise XtappError(f'{where}: bad magic {data[:4]!r}, expected {MAGIC!r}')
    version, header_size = struct.unpack_from('<HH', data, 4)
    total, feature_mask = struct.unpack_from('<II', data, 0x08)
    man_off, man_len = struct.unpack_from('<II', data, 0x10)
    if version != VERSION or header_size != HEADER_SIZE:
        raise XtappError(f'{where}: version {version} / header_size {header_size:#x}, expected 1 / 0x80')
    if man_off != MANIFEST_OFF or not 1 <= man_len <= MANIFEST_MAX or man_off + man_len > len(data):
        raise XtappError(f'{where}: invalid manifest bounds ({man_off:#x}+{man_len})')
    if total != len(data):
        raise XtappError(f'{where}: header total_size ({total}) != file size ({len(data)})')

    sections = {}
    for name, hdr_off, _ in LICORICE_SECTIONS:
        off, ln = struct.unpack_from('<II', data, hdr_off)
        if ln and (off < HEADER_SIZE or off + ln > total):
            raise XtappError(f'{where}: section {name!r} ({off:#x}+{ln}) out of bounds ({total})')
        sections[name] = (off, ln)

    try:
        manifest = json.loads(
            data[man_off:man_off + man_len].decode('utf-8'),
            object_pairs_hook=collections.OrderedDict,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise XtappError(f'{where}: invalid manifest JSON ({e})') from None

    header = {
        'magic': MAGIC.decode(),
        'version': version,
        'header_size': header_size,
        'total_size': total,
        'feature_mask': feature_mask,
        'manifest_offset': man_off,
        'manifest_length': man_len,
    }
    return header, manifest, sections


def verify_container_with_scalar(data: bytes, scalar: int, where: str = 'app.xtapp') -> dict:
    """Full device-side verification: unwrap DEK, verify HMACs, decrypt XTCO & XTDT."""
    head, man, sections = read_container(data, where)
    if man.get('dek_wrap_alg') != LICORICE_ALG:
        raise XtappError(f'{where}: unexpected dek_wrap_alg {man.get("dek_wrap_alg")!r}')
    key_id = man.get('dek_wrap_key_id', LICORICE_KEY_ID)
    dek = licorice_unwrap_dek(
        scalar,
        bytes.fromhex(man['dek_wrap_ephemeral_pub']),
        bytes.fromhex(man['wrapped_dek_pack']),
        key_id=key_id,
    )

    result: dict = {'header': head, 'manifest': man, 'sections': sections, 'dek_hex': dek.hex()}

    # Verify & decrypt code section
    c_off, c_len = sections['code']
    if not c_len:
        raise XtappError(f'{where}: missing code section')
    code_raw = data[c_off:c_off + c_len]
    if head['feature_mask'] & FEATURE_CODE:
        want = bytes.fromhex(man.get('hmac_code', ''))
        if not hmac.compare_digest(want, licorice_section_hmac(dek, code_raw)):
            raise XtappError(f'{where}: hmac_code mismatch')
        code_raw = licorice_ctr(dek, bytes.fromhex(man['iv_code']), code_raw)
    result['code_files'] = read_xtco(code_raw, where)

    # Verify & decrypt data section if present
    d_off, d_len = sections['data']
    if d_len:
        data_raw = data[d_off:d_off + d_len]
        if head['feature_mask'] & FEATURE_DATA:
            want = bytes.fromhex(man.get('hmac_data', ''))
            if not hmac.compare_digest(want, licorice_section_hmac(dek, data_raw)):
                raise XtappError(f'{where}: hmac_data mismatch')
            data_raw = licorice_ctr(dek, bytes.fromhex(man['iv_data']), data_raw)
        result['data_files'] = read_xtdt(data_raw, where)
    else:
        result['data_files'] = []

    return result


# ---------------------------------------------------------------- high-level commands
def pack(
    app_dir: str,
    out: str | None = None,
    image: str | None = None,
    pubkey_hex: str | None = None,
    key_id: str = LICORICE_KEY_ID,
) -> str:
    """Pack `app_dir` into a signed/encrypted .xtapp file and return the output path."""
    mpath = os.path.join(app_dir, 'manifest.json')
    man = validate_manifest(load_manifest(mpath), mpath)
    entry = man.get('entry', 'index.lua')
    code_files = collect_code_files(app_dir, entry)

    lock = man.get('lockscreen')
    if isinstance(lock, dict):
        lock_entry = lock.get('entry', 'lockscreen.lua')
        if lock_entry not in [name for name, _ in code_files]:
            raise XtappError(f'lockscreen entry {lock_entry!r} not found in {app_dir}')

    data_files = collect_data_files(app_dir)
    icons = collect_icons(app_dir)
    pubkey = resolve_device_pubkey(image=image, pubkey_hex=pubkey_hex)
    pkg, _ = build_container(man, pubkey, code_files, data_files, icons, key_id=key_id)

    if out is None:
        out = os.path.join(DEFAULT_DIST_DIR, man['app_id'] + '.xtapp')
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, 'wb') as f:
        f.write(pkg)

    head, _, sections = read_container(pkg, out)
    code_names = ', '.join(n for n, _ in code_files)
    print(
        f'{out}: {len(pkg)} bytes | app_id={man["app_id"]} "{man["display_name"]}" '
        f'(api {man.get("api", "0.8")}, mask={head["feature_mask"]:#x})'
    )
    print(
        f'  sections: manifest {head["manifest_length"]} B, '
        f'code {sections["code"][1]} B ({len(code_files)}: {code_names}), '
        f'data {sections["data"][1]} B ({len(data_files)}), '
        f'icons {sections["icons"][1]} B'
    )
    return out


def inspect_package(path: str, image: str | None = None) -> None:
    """Print container metadata, and perform full crypto verification if --image is given."""
    raw = open(path, 'rb').read()
    head, man, sections = read_container(raw, path)
    print(f'{path}: {head["total_size"]} bytes (XTAP v{head["version"]}, feature_mask={head["feature_mask"]:#x})')
    print(f'  app_id:       {man.get("app_id")}')
    print(f'  display_name: {man.get("display_name")}')
    print(f'  version:      {man.get("version")}  api: {man.get("api", "0.8 (default)")}')
    print(f'  entry:        {man.get("entry", "index.lua")}  permissions: {man.get("permissions", [])}')
    if 'lockscreen' in man:
        print(f'  lockscreen:   {json.dumps(man["lockscreen"])}')
    print(f'  dek_wrap:     {man.get("dek_wrap_alg")} (key_id={man.get("dek_wrap_key_id")})')
    for name, (off, ln) in sections.items():
        if ln:
            print(f'  section {name:6s}: offset={off:#x} length={ln} B')
    if image:
        scalar, _, va, proj, ver = derive_device_key_from_image(image)
        rep = verify_container_with_scalar(raw, scalar, path)
        print(f'  VERIFIED against {image} (project={proj!r}, version={ver!r}, scalar_va={va:#x}):')
        print(f'    DEK unwrapped + HMAC-SHA256 valid')
        for name, blob in rep['code_files']:
            print(f'    code: {name} ({len(blob)} B)')
        for name, blob in rep['data_files']:
            print(f'    data: {name} ({len(blob)} B)')


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('app_dir', nargs='?', help=f'app directory to pack (default: {os.path.relpath(DEFAULT_APP_DIR, REPO_ROOT)})')
    ap.add_argument('-o', '--out', help='output .xtapp path (default: dist/<app_id>.xtapp)')
    ap.add_argument('--image', help='Licorice app image or 16 MB ESP32-S3 flash dump to derive the device key')
    ap.add_argument('--pubkey', help='override device P-256 public key (65-byte uncompressed SEC1 hex)')
    ap.add_argument('--key-id', default=LICORICE_KEY_ID, help=f'override dek_wrap_key_id (default: {LICORICE_KEY_ID})')
    ap.add_argument('--info', metavar='FILE.xtapp', help='inspect an existing .xtapp (add --image to verify crypto)')
    ap.add_argument('--derive-key', metavar='FIRMWARE', help='extract and print the device public key from a firmware image or flash dump')
    args = ap.parse_args(argv)

    try:
        if args.derive_key:
            _, pub, va, name, ver = derive_device_key_from_image(args.derive_key)
            print(f'project:   {name!r}')
            print(f'version:   {ver!r}')
            print(f'scalar_va: {va:#x}')
            print(f'pubkey:    {pub.hex()}')
            print(f'matches embedded key: {pub.hex() == DEVICE_PUBKEY_HEX}')
            return 0

        if args.info:
            inspect_package(args.info, image=args.image)
            return 0

        app_dir = args.app_dir or DEFAULT_APP_DIR
        pack(app_dir, out=args.out, image=args.image, pubkey_hex=args.pubkey, key_id=args.key_id)
        print('Install: put the X4 Pro in USB mode, copy the .xtapp to the SD card root, eject, and tap it under All Files.')
        return 0
    except XtappError as e:
        print(f'error: {e}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
