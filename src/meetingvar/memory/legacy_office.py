"""Pure-Python text extraction for legacy Word/PowerPoint binary files.

The desktop app must work on a clean Windows machine.  It therefore cannot
assume LibreOffice (or any other office suite) is installed.  These parsers
cover the common Word 97-2003 (.doc) and PowerPoint 97-2003 (.ppt) containers:

* a small Compound File Binary (CFB/OLE2) reader;
* Word's piece table (CLX) for real document text;
* PowerPoint's TextCharsAtom / TextBytesAtom records.

They are intentionally read-only and bounded.  Unknown or corrupt files return
``None`` so callers can fall back to a metadata-only preview.
"""
from __future__ import annotations

import re
import struct
from pathlib import Path


_CFB_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_END_OF_CHAIN = 0xFFFFFFFE
_MAX_STREAM_BYTES = 64 * 1024 * 1024
_MAX_CHAIN_STEPS = 1_000_000


class CFBError(ValueError):
    pass


def _u16(data: bytes, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def _u64(data: bytes, offset: int) -> int:
    return struct.unpack_from("<Q", data, offset)[0]


class CompoundFile:
    """Minimal CFB reader: directory entries, FAT and mini-stream."""

    def __init__(self, path: Path):
        self.data = Path(path).read_bytes()
        if len(self.data) < 512 or self.data[:8] != _CFB_SIGNATURE:
            raise CFBError("not a compound file")
        self.sector_size = 1 << _u16(self.data, 0x1E)
        self.mini_sector_size = 1 << _u16(self.data, 0x20)
        if self.sector_size not in {512, 4096} or self.mini_sector_size != 64:
            raise CFBError("unsupported sector size")
        self.num_fat = _u32(self.data, 0x2C)
        self.dir_start = _u32(self.data, 0x30)
        self.mini_cutoff = _u32(self.data, 0x38)
        self.minifat_start = _u32(self.data, 0x3C)
        self.num_minifat = _u32(self.data, 0x40)
        self.difat_start = _u32(self.data, 0x44)
        self.num_difat = _u32(self.data, 0x48)
        self._fat = self._load_fat()
        directory = self._read_chain(self.dir_start, self._fat, self.sector_size)
        self.entries = self._parse_directory(directory)
        root = next((e for e in self.entries if e["type"] == 5), None)
        if root is None:
            raise CFBError("missing root entry")
        self._mini_stream = b""
        if root["start"] < _END_OF_CHAIN and root["size"]:
            raw = self._read_chain(root["start"], self._fat, self.sector_size)
            self._mini_stream = raw[: min(root["size"], _MAX_STREAM_BYTES)]
        self._minifat = []
        if self.minifat_start < _END_OF_CHAIN and self.num_minifat:
            raw = self._read_chain(self.minifat_start, self._fat, self.sector_size)
            self._minifat = [_u32(raw, i) for i in range(0, len(raw) - 3, 4)]

    def _sector(self, sector_id: int) -> bytes:
        if sector_id >= 0xFFFFFFFA:
            raise CFBError("invalid sector")
        offset = (sector_id + 1) * self.sector_size
        if offset < 0 or offset + self.sector_size > len(self.data):
            raise CFBError("sector outside file")
        return self.data[offset:offset + self.sector_size]

    def _load_fat(self) -> list[int]:
        difat = [_u32(self.data, 0x4C + i * 4) for i in range(109)]
        sector = self.difat_start
        seen = set()
        while sector < 0xFFFFFFFA and len(difat) < max(self.num_fat, 109):
            if sector in seen or len(seen) > _MAX_CHAIN_STEPS:
                break
            seen.add(sector)
            block = self._sector(sector)
            difat.extend(_u32(block, i) for i in range(0, self.sector_size - 4, 4))
            sector = _u32(block, self.sector_size - 4)
        fat: list[int] = []
        for sector_id in difat[: min(self.num_fat, len(difat))]:
            if sector_id >= 0xFFFFFFFA:
                continue
            block = self._sector(sector_id)
            fat.extend(_u32(block, i) for i in range(0, self.sector_size, 4))
        return fat

    def _read_chain(self, start: int, table: list[int], unit: int) -> bytes:
        output = bytearray()
        sector = start
        seen = set()
        while sector < 0xFFFFFFFA and sector < len(table):
            if sector in seen or len(seen) > _MAX_CHAIN_STEPS:
                raise CFBError("cyclic chain")
            seen.add(sector)
            if unit == self.sector_size:
                output.extend(self._sector(sector))
            else:
                offset = sector * unit
                if offset + unit > len(self._mini_stream):
                    raise CFBError("mini sector outside stream")
                output.extend(self._mini_stream[offset:offset + unit])
            if len(output) > _MAX_STREAM_BYTES:
                raise CFBError("stream too large")
            sector = table[sector]
        return bytes(output)

    @staticmethod
    def _parse_directory(data: bytes) -> list[dict]:
        entries = []
        for offset in range(0, len(data) - 127, 128):
            entry_type = data[offset + 66]
            name_len = _u16(data, offset + 64)
            if entry_type not in {1, 2, 5} or name_len < 2 or name_len > 64:
                continue
            name = data[offset:offset + name_len - 2].decode("utf-16-le", "ignore")
            start = _u32(data, offset + 116)
            size = _u64(data, offset + 120)
            if size > _MAX_STREAM_BYTES:
                size &= 0xFFFFFFFF
            entries.append({"name": name, "type": entry_type,
                            "start": start, "size": min(size, _MAX_STREAM_BYTES)})
        return entries

    def stream(self, name: str) -> bytes:
        entry = next((e for e in self.entries if e["type"] == 2 and e["name"] == name), None)
        if entry is None:
            raise CFBError(f"stream not found: {name}")
        size = entry["size"]
        if size == 0:
            return b""
        if size < self.mini_cutoff:
            raw = self._read_chain(entry["start"], self._minifat, self.mini_sector_size)
        else:
            raw = self._read_chain(entry["start"], self._fat, self.sector_size)
        return raw[:size]

    def has_stream(self, name: str) -> bool:
        return any(e["type"] == 2 and e["name"] == name for e in self.entries)


def _decode_ansi(data: bytes) -> str:
    """Decode Word/PPT ANSI text without guessing a machine locale."""
    if not data:
        return ""
    if data.count(b"\x00") > len(data) // 3:
        try:
            return data.decode("utf-16-le", "ignore")
        except UnicodeDecodeError:
            pass
    try:
        return data.decode("gbk")
    except UnicodeDecodeError:
        return data.decode("cp1252", "replace")


def _clean_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(ch if ch in "\n\t" or ord(ch) >= 0x20 else "" for ch in text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _doc_piece_text(word: bytes, table: bytes) -> str | None:
    if len(word) < 0x1AC or word[:2] != b"\xec\xa5":
        return None
    flags = _u16(word, 0x0A)
    table_name = "1Table" if flags & 0x0200 else "0Table"
    # FibRgFcLcb97: fcClx/lcbClx are pair 33, starting at 0x9A.
    fc_clx, lcb_clx = _u32(word, 0x1A2), _u32(word, 0x1A6)
    if not lcb_clx or fc_clx + lcb_clx > len(table):
        return None
    clx = table[fc_clx:fc_clx + lcb_clx]
    pos = 0
    plc = None
    while pos < len(clx):
        kind = clx[pos]
        pos += 1
        if kind == 0x01:  # Prc: grpprl
            if pos + 2 > len(clx):
                return None
            size = _u16(clx, pos)
            pos += 2 + size
        elif kind == 0x02:  # Pcdt
            if pos + 4 > len(clx):
                return None
            size = _u32(clx, pos)
            pos += 4
            plc = clx[pos:pos + size]
            break
        else:
            return None
    if not plc or len(plc) < 16:
        return None
    # PlcPcd: (n+1) CPs followed by n PCDs, so (len-4) % 12 == 0.
    count = (len(plc) - 4) // 12
    if count <= 0 or 4 * (count + 1) + 8 * count > len(plc):
        return None
    parts: list[str] = []
    for index in range(count):
        cp_start = _u32(plc, index * 4)
        cp_end = _u32(plc, (index + 1) * 4)
        pcd = 4 * (count + 1) + index * 8
        fc = _u32(plc, pcd + 2)
        compressed = bool(fc & 0x40000000)
        offset = fc & 0x3FFFFFFF
        length = max(0, cp_end - cp_start)
        if compressed:
            raw = word[offset // 2:offset // 2 + length]
            parts.append(_decode_ansi(raw))
        else:
            raw = word[offset:offset + length * 2]
            parts.append(raw.decode("utf-16-le", "replace"))
    return _clean_text("".join(parts)) or None


def _oversized(path: Path) -> bool:
    try:
        return path.stat().st_size > 128 * 1024 * 1024
    except OSError:
        return True


def extract_doc_text(path: Path) -> str | None:
    """Extract text from Word 97-2003 (.doc).  Returns None on unknown input."""
    path = Path(path)
    if _oversized(path):
        return None
    header = path.read_bytes()[:8] if path.exists() else b""
    if header.startswith(b"{\\rtf"):
        return _read_rtf(path)
    try:
        cfb = CompoundFile(path)
        word = cfb.stream("WordDocument")
    except (OSError, CFBError):
        return None
    flags = _u16(word, 0x0A) if len(word) >= 0x0C else 0
    table_name = "1Table" if flags & 0x0200 else "0Table"
    table = b""
    if cfb.has_stream(table_name):
        try:
            table = cfb.stream(table_name)
        except CFBError:
            table = b""
    text = _doc_piece_text(word, table)
    if text:
        return text
    # Corrupt/very old documents: use the declared text range as a last resort.
    if len(word) >= 0x20:
        start, end = _u32(word, 0x18), _u32(word, 0x1C)
        if 0 <= start < end <= len(word):
            raw = word[start:end]
            decoded = _decode_ansi(raw)
            return _clean_text(decoded) or None
    return None


def _ppt_records(data: bytes):
    offset = 0
    size = len(data)
    while offset + 8 <= size:
        ver_instance, rec_type, length = struct.unpack_from("<HHI", data, offset)
        body_start = offset + 8
        body_end = body_start + length
        if length > size - body_start:
            break
        if body_end > body_start:
            yield ver_instance, rec_type, data[body_start:body_end]
        # Container records contain child records.  Parse them in place.
        if (ver_instance & 0xF) == 0xF:
            yield from _ppt_records(data[body_start:body_end])
        offset = body_end


def _ppt_text(data: bytes) -> str:
    parts: list[str] = []
    for _ver, rec_type, body in _ppt_records(data):
        if rec_type == 0x0FA0:  # TextCharsAtom
            text = body.decode("utf-16-le", "ignore")
        elif rec_type == 0x0FA8:  # TextBytesAtom
            text = _decode_ansi(body)
        else:
            continue
        text = text.strip()
        if not text or text == "*" or re.match(r"(?i)^click to edit master\b", text):
            continue
        parts.append(text)
    return _clean_text("\n".join(parts))


def extract_ppt_text(path: Path) -> str | None:
    """Extract text atoms from PowerPoint 97-2003 (.ppt)."""
    if _oversized(path):
        return None
    try:
        cfb = CompoundFile(path)
    except (OSError, CFBError):
        return None
    for name in ("PowerPoint Document", "PP40"):
        if not cfb.has_stream(name):
            continue
        try:
            data = cfb.stream(name)
        except CFBError:
            continue
        text = _ppt_text(data)
        if text:
            return text
    return None


def _xls_sst_strings(data: bytes) -> list[str]:
    if len(data) < 8:
        return []
    total, unique = struct.unpack_from("<II", data, 0)
    count = min(unique, 100_000)
    pos = 8
    values = []
    for _ in range(count):
        if pos + 3 > len(data):
            break
        cch = _u16(data, pos)
        flags = data[pos + 2]
        pos += 3
        runs = 0
        ext = 0
        if flags & 0x08:
            if pos + 2 > len(data):
                break
            runs = _u16(data, pos)
            pos += 2
        if flags & 0x04:
            if pos + 4 > len(data):
                break
            ext = _u32(data, pos)
            pos += 4
        if flags & 0x01:
            end = pos + cch * 2
            if end > len(data):
                break
            value = data[pos:end].decode("utf-16-le", "replace")
        else:
            end = pos + cch
            if end > len(data):
                break
            value = _decode_ansi(data[pos:end])
        pos = end + runs * 4 + ext
        if value.strip():
            values.append(value)
    return values


def extract_xls_text(path: Path) -> str | None:
    """Extract shared strings from Excel 97-2003 (.xls) BIFF workbooks."""
    if _oversized(path):
        return None
    try:
        cfb = CompoundFile(path)
    except (OSError, CFBError):
        return None
    data = b""
    for name in ("Workbook", "Book"):
        if cfb.has_stream(name):
            try:
                data = cfb.stream(name)
                break
            except CFBError:
                continue
    if len(data) < 8:
        return None
    values: list[str] = []
    pos = 0
    while pos + 4 <= len(data):
        rec_type, length = struct.unpack_from("<HH", data, pos)
        body_start = pos + 4
        body_end = body_start + length
        if body_end > len(data):
            break
        body = data[body_start:body_end]
        if rec_type == 0x00FC:  # SST
            values.extend(_xls_sst_strings(body))
        elif rec_type == 0x0204 and len(body) >= 8:  # BIFF5 LABEL
            values.append(_decode_ansi(body[8:]))
        pos = body_end
    clean = []
    seen = set()
    for value in values:
        value = value.strip()
        if value and value not in seen:
            seen.add(value)
            clean.append(value)
    return _clean_text("\n".join(clean[:5000])) or None


def extract_rtf_text(path: Path) -> str | None:
    return _read_rtf(path)


def _read_rtf(path: Path) -> str | None:
    path = Path(path)
    if _oversized(path):
        return None
    raw = path.read_bytes()
    try:
        text = raw.decode("latin-1")
    except UnicodeDecodeError:
        return None
    text = re.sub(r"\\'([0-9a-fA-F]{2})", lambda m: bytes([int(m.group(1), 16)]).decode("cp1252", "ignore"), text)
    text = re.sub(r"\\u(-?\d+)\??", lambda m: chr(int(m.group(1)) % 65536), text)
    text = re.sub(r"\\par[d]?\b", "\n", text)
    text = re.sub(r"\\tab\b", "\t", text)
    text = re.sub(r"\\[a-zA-Z]+-?\d* ?", "", text)
    text = text.replace("\\{", "{").replace("\\}", "}").replace("\\\\", "\\")
    text = re.sub(r"[{}]", "", text)
    return _clean_text(text) or None