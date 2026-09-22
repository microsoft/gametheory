"""Initialize only the disposable, loopback CI SQL instance."""

import argparse
import os
import re
import time

import pyodbc

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--database-name", default="gametheory_test")
args = parser.parse_args()
if not re.fullmatch(r"(?:gametheory_test|flood_lab_test)(?:_[A-Za-z0-9]+)*", args.database_name):
    parser.error("Only explicitly named Game Theory or flood lab test databases are allowed")

password = os.environ["TEST_SQL_PASSWORD"]
connection = (
    "DRIVER={ODBC Driver 18 for SQL Server};SERVER=127.0.0.1,1433;"
    f"UID=sa;PWD={{{password.replace('}', '}}')}}};"
    "DATABASE=master;Encrypt=yes;TrustServerCertificate=yes"
)
deadline = time.monotonic() + 90
while True:
    try:
        db = pyodbc.connect(connection, autocommit=True, timeout=2)
        break
    except pyodbc.Error:
        if time.monotonic() >= deadline:
            raise
        time.sleep(2)
try:
    db.execute(f"IF DB_ID('{args.database_name}') IS NULL CREATE DATABASE [{args.database_name}]")
finally:
    db.close()
