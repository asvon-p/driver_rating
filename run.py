import argparse
import importlib.util
import os
import shutil
import socket
import subprocess
import sys
import threading
from pathlib import Path

from local_runtime import ROOT, STATE, database_start, environment, pg_ctl


def main():
    parser = argparse.ArgumentParser(description="Driver Rating local server")
    parser.add_argument("--port", type=int, default=8017)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    mode.add_argument("--stop", action="store_true", help="Stop this project's running server")
    mode.add_argument("--demo", action="store_true", help="Share this session via Tailscale Funnel on HTTPS port 10000")
    args = parser.parse_args()
    environment()
    stop_file = STATE / "stop.request"
    if args.stop:
        stop_file.touch()
        print("Local stop requested.")
        return
    if args.worker:
        from driver.worker import run_worker

        run_worker()
        return
    tailscale = shutil.which("tailscale") if args.demo else None
    if args.demo and not tailscale:
        parser.error("Tailscale CLI is required for --demo.")
    # Hold a project-wide lock across startup and shutdown, including PostgreSQL.
    instance_lock = (STATE / "server.lock").open("a+b")
    try:
        if os.name == "nt":
            import msvcrt

            instance_lock.seek(0)
            msvcrt.locking(instance_lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(instance_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        parser.error("Driver Rating is already running. See .runtime/server-url.txt.")
    stop_file.unlink(missing_ok=True)
    dependencies = (
        "torch",
        "torchvision",
        "PIL",
        "numpy",
        "fastapi",
        "av",
        "psycopg",
        "uvicorn",
        "python_multipart",
    )
    missing = [name for name in dependencies if importlib.util.find_spec(name) is None]
    if missing:
        parser.error(
            f"Missing {', '.join(missing)} in {sys.executable}. "
            "Activate driver_rating and install requirements.txt as documented in README.md."
        )
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", args.port))
        except OSError:
            parser.error(f"Local port {args.port} is occupied. Try start.ps1 -Port 8010.")
    os.environ["DRIVER_PUBLIC_SESSION"] = "1" if args.demo else "0"
    own_pg = database_start()
    child = None
    funnel = None
    demo_error = None
    try:
        from driver.db import connect, initialize

        initialize()
        with connect() as db:
            db.execute("DELETE FROM worker_status")
        import uvicorn

        from driver.web import app

        # The worker's lifetime is tied to this server, even if the parent exits abruptly.
        os.environ["DRIVER_PARENT_PID"] = str(os.getpid())

        def start_worker():
            with (STATE / "logs" / "worker.log").open("a", encoding="utf-8") as log:
                return subprocess.Popen(
                    [sys.executable, "-B", "-X", "utf8", str(Path(__file__).resolve()), "--worker"],
                    cwd=ROOT,
                    stdin=subprocess.PIPE,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )

        child = start_worker()
        print(f"Driver Rating: http://127.0.0.1:{args.port}", flush=True)
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=args.port, access_log=False)
        )
        (STATE / "server-url.txt").write_text(f"http://127.0.0.1:{args.port}", encoding="ascii")
        closing = threading.Event()

        def supervise():
            nonlocal child, funnel, demo_error
            restarts = 0
            while not closing.wait(0.5) and not server.should_exit:
                if stop_file.exists():
                    server.should_exit = True
                    return
                if tailscale and server.started:
                    if funnel is None:
                        try:
                            funnel = subprocess.Popen(
                                [tailscale, "funnel", "--https=10000", f"http://127.0.0.1:{args.port}"],
                                stdin=subprocess.DEVNULL,
                            )
                        except OSError as exc:
                            demo_error = f"Could not start Tailscale Funnel: {exc}"
                    elif funnel.poll() is not None:
                        demo_error = f"Tailscale Funnel exited (code {funnel.returncode})."
                    if demo_error:
                        server.should_exit = True
                        return
                if child.poll() is not None:
                    if restarts == 0:
                        child.stdin.close()
                        child = start_worker()
                        restarts += 1
                    elif restarts == 1:
                        with connect() as db:
                            db.execute("""UPDATE worker_status SET state='error', updated=now(),
                                          error='워커가 중단되었습니다. worker.log를 확인하고 서버를 다시 시작해 주세요.'""")
                        restarts += 1

        monitor = threading.Thread(target=supervise, daemon=True)
        monitor.start()
        try:
            server.run()
        finally:
            closing.set()
            monitor.join()
        if demo_error:
            raise SystemExit(demo_error)
    finally:
        if funnel is not None and funnel.poll() is None:
            funnel.terminate()
            try:
                funnel.wait(timeout=5)
            except subprocess.TimeoutExpired:
                funnel.kill()
                funnel.wait()
        if child is not None:
            child.stdin.close()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.terminate()
                child.wait(timeout=15)
        if own_pg:
            pg_ctl("stop", "-w", "-m", "fast")
        (STATE / "server-url.txt").unlink(missing_ok=True)
        stop_file.unlink(missing_ok=True)
        instance_lock.close()


if __name__ == "__main__":
    main()
