"""Initialize only the disposable, loopback CI SQL instance."""

import os
import time

import pyodbc

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
    db.execute("IF DB_ID('gametheory_test') IS NULL CREATE DATABASE gametheory_test")
finally:
    db.close()
