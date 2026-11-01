"""
Source: https://github.com/Niko1221/Strata/blob/b2dbd9420d7860b79d553afd977ba2e70cff9d52/serve/server.py
"""
import shutil
import subprocess
import os
import tempfile
import queue
import threading
import base64
import hashlib
from pathlib import Path
import regex as re

from modules.strata.lifeline import popen
from modules.strata.strata_control import vision_reader
from modules.strata.utils import _timeout_env

VISION_READY_S = _timeout_env("STRATA_VISION_READY_S", 300.0)
VISION_ENCODE_S = _timeout_env("STRATA_VISION_ENCODE_S", 300.0)
ENGINE_READY_S = _timeout_env("STRATA_ENGINE_READY_S", 900.0)
IMAGE_URL_MAX = 32 << 20        # an image URL is read up to this (the web app attaches pictures of up to 20 MB)

def vision_start_error(line: str, log=None) -> str:
    """The message when the image encoder answered something other than READY.  #1445: a card in the Exclusive_Process
    compute mode (or one another process holds) refuses the encoder's CUDA context with "busy or unavailable"; that
    cause is named instead of leaving the user the first line of a traceback.  `log` is the encoder's stderr file, whose
    tail is searched too (the CUDA error is printed there)."""
    text = line.strip()
    tail = ""
    try:
        name = getattr(log, "name", None)
        if name:
            with open(name, "rb") as f:
                f.seek(0, 2)
                f.seek(max(0, f.tell() - 16384))
                tail = f.read().decode("utf-8", "replace")
    except (OSError, ValueError):
        pass
    if "busy or unavailable" in (text + tail).lower():
        return ("the vision encoder did not start: the GPU refused a second CUDA context (\"CUDA-capable device(s) "
                "is/are busy or unavailable\"). This is usually the compute mode Exclusive_Process: only one process "
                "may use the card, and the engine already holds it. Check with: nvidia-smi --query-gpu=compute_mode "
                "--format=csv; set it back with: sudo nvidia-smi -c DEFAULT (or run the encoder on another card). "
                "First line from the encoder: " + (text or "(none)"))
    return "the vision encoder did not start: " + text

def network_path(path: str) -> bool:
    r"""A path to another computer: \\host\share or //host/share (either slash), \\?\UNC\host\..., \\.\UNC\host\...
    or the NT form \??\UNC\host\...  Windows connects to that host at the first look at the path (os.path.isfile is
    enough) and signs in with the user's NTLM credentials.  \\?\ and \??\ name local files too; no client needs them."""
    p = path.replace("/", "\\")
    return p.startswith("\\\\") or p.startswith("\\??\\")

class ClientGone(Exception):
    """#1999: the client hung up while its request was being prepared (an image URL downloading, a picture encoding or
    waiting for its turn): nothing is left to answer."""

class Vision:
    """The resident image encoder: `strata-vision` (llama.cpp mtmd + the mmproj file) reads `ENC <image> <out>`
    lines and writes each image's embeddings; results are cached by the image's hash, so a conversation that
    sends the same picture again (every turn, with most clients) encodes it once."""

    @staticmethod
    def work_dir() -> Path:
        """#480: the directory for the encoder's files.  The engine reads the embeddings file named in a `GENI` line
        and the encoder the output named in an `ENC <image> <out>` line, both split at spaces, so a space in the path
        (a %TEMP% under a user name with a space) broke every image.  The system temp directory is used while its path
        has no space, then the working directory, then the system drive's root; the plain temp directory is the last
        resort (the encoder's own files are then named relative to it, see `_start`)."""
        for parent in (None, Path.cwd(), Path(os.environ.get("SystemDrive", "C:") + os.sep)):
            try:
                d = Path(tempfile.mkdtemp(prefix="strata-vision-", dir=parent))
            except OSError:
                continue                                       # not writable, or no such drive
            if " " not in str(d):
                return d
            try:
                d.rmdir()                                      # just created, so still empty
            except OSError:
                pass
        return Path(tempfile.mkdtemp(prefix="strata-vision-"))

    def __init__(self, cfg: dict, log=None, env: dict | None = None, lazy: bool = False):
        args = self.reader_args(cfg)
        self.dir = self.work_dir()
        self.spawn = (args, log, env)                   # to start it again after an unload
        # AMD: the GPU reader (strata-vision-hip) has the CPU reader (strata-vision, same program, no GPU runtime) to
        # fall back on when it does not start; _start switches to it by itself
        cpu = vision_reader.cpu_fallback(cfg)
        self.fallback = (self.reader_args(cpu), log, env) if cpu else None
        self.proc = None
        self.stopped = True
        self.lock = threading.Lock()
        self.cache: dict[str, tuple[Path, int]] = {}
        # opt-in (the vision section's "elastic": true, with the config's "vram_elastic"): the encoder starts with the
        # first picture and stops after elastic_idle_s without one; while it runs, the engine's expert cache keeps
        # elastic_mib of VRAM free for it (Service._vision_up / _vision_idle_down)
        self.elastic = cfg.get("elastic") is True
        self.elastic_mib = int(cfg.get("elastic_mib") or 2048)
        self.elastic_idle_s = float(cfg.get("elastic_idle_s") or 120)
        if not lazy and not self.elastic:
            self._start()

    @staticmethod
    def reader_args(cfg: dict) -> list:
        absolute = lambda p: os.path.abspath(p) if os.path.dirname(str(p)) else p   # the encoder runs in its own dir (#480)  # noqa: E731
        args = [absolute(cfg["exe"]), "--mmproj", absolute(cfg["mmproj"]), "--model", absolute(cfg["model"])]
        if cfg.get("gpu"):
            args.append("--gpu")
        if cfg.get("threads"):
            args += ["--threads", str(cfg["threads"])]
        if cfg.get("max_tokens"):
            args += ["--max-tokens", str(cfg["max_tokens"])]
        if cfg.get("min_tokens"):                       # #767: mtmd's image_min_tokens (a hand-edited key)
            args += ["--min-tokens", str(cfg["min_tokens"])]
        return args

    def _start(self):
        """Start the reader.  When a GPU reader that has a CPU reader to fall back on does not start (the GPU runtime does not
        load, the card is not in its build, the card is busy), the CPU reader is started instead, for good, with one log line
        and no question."""
        try:
            self._start_reader()
        except (RuntimeError, OSError) as e:
            fallback = getattr(self, "fallback", None)
            if fallback is None:
                raise
            why = " ".join(str(e).split())[:240]
            print(f"[strata] the GPU picture reader did not start ({why}); pictures are read on the CPU", flush=True)
            self.spawn, self.fallback = fallback, None
            self._start_reader()

    def _start_reader(self):
        args, log, env = self.spawn
        # cwd is the encoder's directory: the ENC lines name the files relative to it (#480: no space in the line)
        self.proc = popen("the image encoder", args, role="vision", stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=log or subprocess.DEVNULL, text=True, encoding="utf-8", bufsize=1, env=env,
                          cwd=self.dir)
        try:
            line = self._readline(VISION_READY_S, "the vision encoder did not start")
        except RuntimeError as e:                       # silent for VISION_READY_S: it was ended, and it is not left half-started
            self.proc, self.stopped = None, True
            raise RuntimeError(str(e)) from None
        if not line.startswith("READY"):
            proc, self.proc = self.proc, None
            self.stopped = True
            try:
                proc.kill()
                proc.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                pass
            raise RuntimeError(vision_start_error(line, self.spawn[1]))
        self.stopped = False

    def _readline(self, timeout: float, what: str) -> str:
        """One line from the encoder, waiting at most `timeout` s (#1317).  On a timeout the encoder is killed, so the
        next request starts a fresh one, and the read raises RuntimeError instead of holding the request turn."""
        proc = self.proc
        got: queue.Queue = queue.Queue()

        def read():
            try:
                got.put(proc.stdout.readline())
            except (OSError, ValueError):               # killed under the read
                got.put("")

        threading.Thread(target=read, daemon=True).start()
        try:
            return got.get(timeout=timeout)
        except queue.Empty:
            try:
                proc.kill()
            except OSError:
                pass
            raise RuntimeError(f"{what}: it said nothing for {timeout:.0f} s") from None

    def alive(self) -> bool:
        return self.proc is not None and not self.stopped and self.proc.poll() is None

    def unload(self):
        """Stop the encoder process (its VRAM or RAM goes back); the encoded images stay cached on disk."""
        self.close()
        self.stopped = True

    def restart(self):
        """Start the encoder again after an unload (or if it died); the cache of encoded images is kept."""
        if self.proc is not None:
            try:
                self.proc.kill()
            except OSError:
                pass
        self.proc = None
        self.stopped = True
        self._start()

    @staticmethod
    def load(source: str) -> bytes:
        if source.startswith("data:"):
            try:
                return base64.b64decode(source.split(",", 1)[1])
            except (IndexError, ValueError) as e:                # no comma, or not base64
                raise ValueError(f"the image's data: URL could not be read ({e})") from None
        path = source[7:] if source.startswith("file://") else source
        # refused before anything looks at the path: file://host/share/x.png is \\host\share\x.png ("file://C:/x.png",
        # a drive, is a client's spelling of file:///C:/x.png)
        host = re.split(r"[/\\]", path, maxsplit=1)[0] if source.startswith("file://") else ""
        if network_path(path) or host and not re.fullmatch(r"[A-Za-z]:|localhost", host, re.IGNORECASE):
            raise ValueError("an image file must be on this computer: network paths (\\\\host\\share, //host/share, "
                             "file://host/...) are not read")
        if path and os.path.isfile(path):
            try:
                return Path(path).read_bytes()
            except OSError as e:
                raise ValueError(f"the image could not be read from {path} ({e})") from None
        raise ValueError("an image must be a data: URL, an http(s) URL or a local file path")

    @staticmethod
    def _exif_rotated(data: bytes) -> bool:
        """A JPEG whose EXIF Orientation says it is stored turned (phones do this for portrait photos): the decoder
        (stb_image) ignores the tag.  False without Pillow, or when the tag is missing, 1 or unreadable (#1229)."""
        try:
            import io
            from PIL import Image
            with Image.open(io.BytesIO(data)) as im:
                return im.getexif().get(0x0112, 1) not in (None, 0, 1)
        except Exception:  # noqa: BLE001 - any failure leaves the image as it was sent
            return False

    @staticmethod
    def normalize(data: bytes) -> bytes:
        """The formats strata-vision's decoder (stb_image) reads pass through; anything else is converted to PNG.
        A JPEG with an EXIF Orientation other than 1 is turned upright first (needs Pillow)."""
        jpeg = data[:3] == b"\xff\xd8\xff"
        if (jpeg and not Vision._exif_rotated(data)) or data[:8] == b"\x89PNG\r\n\x1a\n" or data[:2] == b"BM" or \
                data[:6] in (b"GIF87a", b"GIF89a"):
            return data
        try:
            import io
            from PIL import Image
        except ImportError:
            raise ValueError("this image format needs Pillow (python -m pip install pillow); JPEG, PNG, BMP and "
                             "GIF work without it") from None
        try:
            im = Image.open(io.BytesIO(data))
            im.load()
            from PIL import ImageOps
            im = ImageOps.exif_transpose(im)             # the camera's orientation tag, applied (#1229)
        except Exception as e:
            raise ValueError(f"the image could not be read ({e})") from None
        if im.mode in ("RGBA", "LA", "P") and "transparency" in im.info or im.mode in ("RGBA", "LA"):
            im = im.convert("RGBA")
            bg = Image.new("RGB", im.size, (255, 255, 255))   # transparent areas become white, not black
            bg.paste(im, mask=im.split()[-1])
            im = bg
        elif im.mode != "RGB":
            im = im.convert("RGB")
        out = io.BytesIO()
        im.save(out, format="PNG")
        return out.getvalue()

    def encode_all(self, sources: list, cancel=None) -> list[tuple[Path, int]]:
        """encode() for every image of one request: the files of this request's earlier images are never evicted to
        make room for a later one (#1072: with more than 64 unique images the first was deleted before the request
        had read it).  `cancel` (#1999): a request whose client left does not encode its next pictures."""
        out: list[tuple[Path, int]] = []
        for src in sources:
            if cancel is not None and cancel.is_set():
                raise ClientGone()
            out.append(self.encode(src, keep=[p for p, _ in out]))
        return out

    def encode(self, source: str | bytes, keep=()) -> tuple[Path, int]:
        """-> (embeddings file, number of image tokens).  `source`: what load() reads, or the image's bytes.
        `keep`: files the caller still needs (never evicted by this call)."""
        data = self.normalize(source if isinstance(source, bytes) else self.load(source))
        key = hashlib.sha256(data).hexdigest()[:32]
        with self.lock:
            if key in self.cache:
                self.cache[key] = self.cache.pop(key)                  # most recently used last
                return self.cache[key]
            img, out = self.dir / f"{key}.img", self.dir / f"{key}.sve"
            img.write_bytes(data)
            try:
                self.proc.stdin.write(f"ENC {img.name} {out.name}\n")      # relative to the encoder's cwd (#480)
                self.proc.stdin.flush()
                try:
                    line = self._readline(VISION_ENCODE_S, "the image could not be read").strip()
                except RuntimeError as e:                 # silent for VISION_ENCODE_S: the encoder was ended (#1317)
                    self.stopped = True                   # alive() is False, so the next request starts it again
                    raise ValueError(str(e)) from None
            finally:                                                   # #352: also when the encoder's pipe is gone
                img.unlink(missing_ok=True)
            if not line.startswith("OK"):
                raise ValueError("the image could not be read: " + (line[4:] if line.startswith("ERR") else
                                                                     "the vision encoder stopped"))
            self.cache[key] = (out, int(line.split()[1]))
            held = {str(p) for p in keep} | {str(out)}
            for old in list(self.cache):                               # oldest first, but never one still in use
                if len(self.cache) <= 64:
                    break
                if str(self.cache[old][0]) not in held:
                    self.cache.pop(old)[0].unlink(missing_ok=True)
            return self.cache[key]

    def close(self):
        proc, self.proc = self.proc, None
        if proc is None:
            self.stopped = True
            return
        try:
            proc.stdin.write("QUIT\n")
            proc.stdin.flush()
            proc.wait(timeout=10)
        except Exception:
            try:
                proc.kill()
            except OSError:
                pass
            try:
                proc.wait(timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                pass
        self.stopped = True

    def shutdown(self):
        """#914: close() for good: the server ends, so the encoder's directory (one ~10 MB .sve per image, on a tmpfs
        /tmp that is RAM) goes with it instead of piling up with every start.  unload() keeps it for the restart."""
        self.close()
        shutil.rmtree(self.dir, ignore_errors=True)