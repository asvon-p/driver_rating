"""Project-local directories and a loopback-only PostgreSQL cluster for Windows."""

import hashlib
import io
import os
import secrets
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE = ROOT / ".runtime"
PG = STATE / "postgres" / "bin"
DATA = STATE / "pgdata"
PG_PORT = 15432
PG_VERSION = "17.12.0"
PG_SHA = "bebadbe868ed53adb1c12033a381e41005915205ea9fedaa2349f5467fa7016b"
PG_URL = (
    "https://repo.maven.apache.org/maven2/io/zonky/test/postgres/"
    f"embedded-postgres-binaries-windows-amd64/{PG_VERSION}/"
    f"embedded-postgres-binaries-windows-amd64-{PG_VERSION}.jar"
)
_DLL_HANDLES = []


def environment():
    sys.dont_write_bytecode = True
    for name in ("tmp", "logs", "uploads", "downloads"):
        (STATE / name).mkdir(parents=True, exist_ok=True)
    for key in ("TEMP", "TMP", "TMPDIR"):
        os.environ[key] = str(STATE / "tmp")
    tempfile.tempdir = str(STATE / "tmp")
    os.environ.update(
        PYTHONDONTWRITEBYTECODE="1",
        CUDA_CACHE_DISABLE="1",
        TORCH_HOME=str(STATE / "torch"),
        PIP_CACHE_DIR=str(STATE / "pip-cache"),
        XDG_CACHE_HOME=str(STATE / "cache"),
        MPLCONFIGDIR=str(STATE / "matplotlib"),
        TORCHINDUCTOR_CACHE_DIR=str(STATE / "inductor"),
        TRITON_CACHE_DIR=str(STATE / "triton"),
    )
    dirs = [PG, Path(sys.prefix) / "Library" / "bin"]
    for directory in dirs:
        if directory.is_dir() and os.name == "nt":
            _DLL_HANDLES.append(os.add_dll_directory(str(directory)))
    os.environ["PATH"] = os.pathsep.join([*(str(d) for d in dirs), os.environ["PATH"]])
    if PG.is_dir():
        os.environ["PSYCOPG_IMPL"] = "python"


def install_postgres():
    if (PG / "pg_ctl.exe").is_file():
        return
    if sys.platform != "win32":
        raise RuntimeError("Set DRIVER_DATABASE_URL to an existing PostgreSQL database.")
    archive = STATE / "downloads" / f"postgres-{PG_VERSION}.jar"
    if not archive.exists():
        temp = archive.with_suffix(".part")
        urllib.request.urlretrieve(PG_URL, temp)
        temp.replace(archive)
    with archive.open("rb") as archive_file:
        digest = hashlib.file_digest(archive_file, "sha256").hexdigest()
    if digest != PG_SHA:
        raise RuntimeError(f"PostgreSQL archive checksum mismatch: {archive}")
    with tempfile.TemporaryDirectory(dir=STATE / "tmp") as stage:
        unpacked = Path(stage) / "postgres"
        with (
            zipfile.ZipFile(archive) as bundle,
            tarfile.open(fileobj=io.BytesIO(bundle.read("postgres-windows-x86_64.txz"))) as tar,
        ):
            tar.extractall(unpacked, filter="data")
        unpacked.rename(PG.parent)
    os.environ["PSYCOPG_IMPL"] = "python"
    _DLL_HANDLES.append(os.add_dll_directory(str(PG)))


def pg_ctl(*args, check=True):
    return subprocess.run(
        [str(PG / "pg_ctl.exe"), "-D", str(DATA), *args],
        check=check,
        stdout=subprocess.DEVNULL if not check else None,
    )


def database_start():
    if os.getenv("DRIVER_DATABASE_URL"):
        return False
    install_postgres()
    password_file = STATE / "db-password"
    if not (DATA / "PG_VERSION").exists():
        if not password_file.exists():
            with password_file.open("x", encoding="ascii") as handle:
                handle.write(secrets.token_urlsafe(32))
        subprocess.run(
            [
                str(PG / "initdb.exe"),
                "-D",
                str(DATA),
                "-U",
                "driver_app",
                "--pwfile",
                str(password_file),
                "--auth=scram-sha-256",
                "--no-locale",
                "--encoding=UTF8",
            ],
            check=True,
        )
    password = password_file.read_text("ascii").strip()
    os.environ["DRIVER_DATABASE_URL"] = (
        f"postgresql://driver_app:{password}@127.0.0.1:{PG_PORT}/postgres"
    )
    if pg_ctl("status", check=False).returncode == 0:
        return False
    pg_ctl(
        "start",
        "-w",
        "-t",
        "40",
        "-l",
        str(STATE / "logs" / "postgres.log"),
        "-o",
        f"-p {PG_PORT} -h 127.0.0.1",
    )
    return True
