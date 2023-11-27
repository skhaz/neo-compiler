import base64
import functools
import hashlib
import json
import os
import subprocess
from http import HTTPStatus
from tempfile import TemporaryDirectory

from flask import Flask
from flask import Response
from flask import abort
from flask import request
from google.cloud.storage import Client as StorageClient
from requests import Session
from wasmtime import Config
from wasmtime import Engine
from wasmtime import ExitTrap
from wasmtime import Func
from wasmtime import Linker
from wasmtime import Module
from wasmtime import Store
from wasmtime import WasiConfig

app = Flask(__name__)

storage_client = StorageClient()
bucket = storage_client.bucket(os.environ["BUCKET"])

requests = Session()


def unenvelop():
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            envelope = request.get_json()

            if not envelope:
                message = "no Pub/Sub message received"
                print(message)
                abort(HTTPStatus.BAD_REQUEST, description=f"Bad Request: {message}")

            if not isinstance(envelope, dict) or "message" not in envelope:
                message = "invalid Pub/Sub message format"
                print(message)
                abort(HTTPStatus.BAD_REQUEST, description=f"Bad Request: {message}")

            message = envelope["message"]

            data = None

            if isinstance(message, dict) and "data" in message:
                data = base64.b64decode(message["data"]).decode("utf-8").strip()

            if not data:
                message = "received an empty message from Pub/Sub"
                print(message)
                abort(HTTPStatus.BAD_REQUEST, description=f"Bad Request: {message}")

            return func(json.loads(data))

        return wrapper

    return decorator


def run(source: str) -> str:
    with TemporaryDirectory() as path:
        os.chdir(path)

        with open("main.cpp", "w+t") as main:
            main.write(source)
            main.flush()

            try:
                result = subprocess.run(
                    [
                        "emcc",
                        "-O3",
                        "-flto",
                        "-s",
                        "ENVIRONMENT=node",
                        "-s",
                        "WASM=1",
                        "-s",
                        "PURE_WASI=1",
                        "main.cpp",
                    ],
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=300,
                )

                if result.returncode != 0:
                    return result.stderr

                with open("a.out.wasm", "rb") as binary:
                    wasi = WasiConfig()
                    wasi.stdout_file = "a.out.stdout"
                    wasi.stderr_file = "a.out.stderr"

                    config = Config()
                    config.consume_fuel = True
                    engine = Engine(config)
                    store = Store(engine)
                    store.set_wasi(wasi)
                    store.set_limits(16 * 1024 * 1024)
                    store.set_fuel(10_000_000_000)

                    linker = Linker(engine)
                    linker.define_wasi()
                    module = Module(store.engine, binary.read())
                    instance = linker.instantiate(store, module)
                    start = instance.exports(store)["_start"]
                    assert isinstance(start, Func)

                    try:
                        start(store)
                    except ExitTrap as e:
                        if e.code != 0:
                            with open("a.out.stderr", "rt") as stderr:
                                return stderr.read()

                    with open("a.out.stdout", "rt") as stdout:
                        return stdout.read()
            except subprocess.TimeoutExpired:
                return "⏰😮‍💨"
            except subprocess.CalledProcessError as e:
                return e.stderr
            except Exception as e:  # noqa
                return str(e)


@app.post("/")
@unenvelop()
def index(data):
    try:
        result = run(data["source"])

        if len(result) > 128:
            blob = bucket.blob(hashlib.sha256(str(data).encode()).hexdigest())
            blob.upload_from_string(result)
            blob.make_public()
            result = blob.public_url

        url = f"https://api.telegram.org/bot{os.environ['TELEGRAM_TOKEN']}/sendMessage"

        requests.post(
            url,
            json={
                "chat_id": data["chat_id"],
                "reply_to_message_id": data["message_id"],
                "allow_sending_without_reply": True,
                "text": result,
            },
        )

    except Exception as e:  # noqa
        print(str(e))

    return Response(status=200)
