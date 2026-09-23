"""Loopback-only, real-SQL integration fixture. Never imported by the lab runtime."""

import argparse
import json
import os
import socket
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address
from pathlib import Path
from uuid import UUID, uuid4

import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from sqlalchemy import event, text
from sqlalchemy.engine import make_url

from flood_lab.api import create_app
from flood_lab.auth import Actor
from flood_lab.config import Settings
from flood_lab.database import make_engine, sessions
from flood_lab.operator import Operator, migrate
from flood_lab.service import LabService


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    url = os.environ["FLOOD_LAB_TEST_DATABASE_URL"]
    name = os.environ["FLOOD_LAB_TEST_DATABASE_NAME"]
    if (
        os.environ.get("FLOOD_LAB_TEST_ALLOW_RESET") != "yes"
        or not name.startswith("flood_lab_test_")
        or make_url(url).database != name
        or make_url(url).host not in {"127.0.0.1", "localhost"}
    ):
        raise SystemExit(
            "This fixture requires an explicitly allowed loopback disposable lab database."
        )
    engine = make_engine(url, name)
    migrate(engine, name)
    operator = Operator(sessions(engine), name)
    manifest = operator.seed("executor-fixture-" + uuid4().hex, apply=True)["manifest"]
    run_id, tenant = UUID(manifest["run_id"]), uuid4()
    api_actor, participant = Actor(tenant, uuid4(), "service"), Actor(tenant, uuid4(), "user")
    expires = datetime.now(UTC) + timedelta(hours=2)
    for actor, role in ((api_actor, "api"), (participant, "participant")):
        operator.grant(
            run_id, actor.tenant_id, actor.object_id, actor.kind, role, expires, apply=True
        )
    injector, api_user = "injector_" + uuid4().hex, "api_" + uuid4().hex
    with engine.begin() as connection:
        for username, role in ((injector, "flood_injector"), (api_user, "flood_api")):
            connection.execute(text(f"CREATE USER [{username}] WITHOUT LOGIN"))
            connection.execute(text(f"ALTER ROLE [{role}] ADD MEMBER [{username}]"))
    operator.sql_grant(run_id, injector, "inject", expires, apply=True)
    api_engine = make_engine(url, name)

    @event.listens_for(api_engine, "connect")
    def api_principal(connection, _record):
        cursor = connection.cursor()
        cursor.execute(f"EXECUTE AS USER = '{api_user}'")
        cursor.close()

    app = create_app(
        Settings(
            database_url=url,
            database_name=name,
            entra_tenant_id=str(tenant),
            entra_audience="fixture-only-api",
        )
    )
    app.state.service = LabService(sessions(api_engine), name)

    class FixtureVerifier:
        def verify(self, token):
            actors = {"fixture-service": api_actor, "fixture-participant": participant}
            if token not in actors:
                raise ValueError("Unknown test-only token")
            return actors[token]

    app.state.verifier = FixtureVerifier()
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Loopback execution test only")])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(minutes=1))
        .not_valid_after(datetime.now(UTC) + timedelta(hours=2))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ip_address("127.0.0.1"))]), critical=False
        )
        .sign(key, hashes.SHA256())
    )
    certificate_path, key_path = args.output.with_suffix(".crt"), args.output.with_suffix(".key")
    certificate_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    key_path.chmod(0o600)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    args.output.write_text(
        json.dumps(
            {
                "manifest": manifest,
                "sql_principal": injector,
                "api_origin": f"https://127.0.0.1:{listener.getsockname()[1]}",
                "certificate": str(certificate_path),
            }
        )
    )
    try:
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                ssl_keyfile=str(key_path),
                ssl_certfile=str(certificate_path),
                access_log=False,
                log_level="warning",
            )
        )
        server.run(sockets=[listener])
    finally:
        listener.close()
        api_engine.dispose()
        engine.dispose()
        key_path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
