from earcrate.core.deps import *
from earcrate.core.deps import _dt
from earcrate.core.util import *
def _riff_even(data: bytes) -> bytes:
    return data if len(data) % 2 == 0 else data + b"\x00"


def write_wav_info_chunk(path: Path, info: Dict[str, Any]) -> None:
    """Append a small RIFF LIST/INFO chunk to a WAV written by soundfile."""
    try:
        raw = path.read_bytes()
        if len(raw) < 12 or raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
            return
        fields = {
            "IENG": str(info.get("engine_version") or ENGINE_VERSION),
            "ISBJ": str(info.get("arrangement_sha") or ""),
            "ICMT": json.dumps(info, ensure_ascii=False, sort_keys=True)[:4000],
        }
        payload = b"INFO"
        for key, value in fields.items():
            data = value.encode("utf-8", "replace") + b"\x00"
            payload += key.encode("ascii")[:4].ljust(4, b" ") + len(data).to_bytes(4, "little") + _riff_even(data)
        chunk = b"LIST" + len(payload).to_bytes(4, "little") + payload
        new = raw + _riff_even(chunk)
        riff_size = len(new) - 8
        new = new[:4] + riff_size.to_bytes(4, "little") + new[8:]
        path.write_bytes(new)
    except Exception:
        return


def read_wav_info_chunk(path: Path) -> Dict[str, str]:
    try:
        raw = path.read_bytes()
        if len(raw) < 12 or raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
            return {}
        out: Dict[str, str] = {}
        i = 12
        while i + 8 <= len(raw):
            cid = raw[i:i+4]
            size = int.from_bytes(raw[i+4:i+8], "little")
            body = raw[i+8:i+8+size]
            if cid == b"LIST" and body[:4] == b"INFO":
                j = 4
                while j + 8 <= len(body):
                    kid = body[j:j+4].decode("ascii", "ignore").strip()
                    ksz = int.from_bytes(body[j+4:j+8], "little")
                    val = body[j+8:j+8+ksz].rstrip(b"\x00").decode("utf-8", "replace")
                    out[kid] = val
                    j += 8 + ksz + (ksz % 2)
            i += 8 + size + (size % 2)
        return out
    except Exception:
        return {}


