from alembic import context
from sqlalchemy import create_engine

from gametheory.config import get_settings
from gametheory.persistence import Base

url = get_settings().sql_url
if not url.startswith("mssql+pyodbc://"):
    raise ValueError("Set GT_SQL_URL to the dedicated application SQL database")

if context.is_offline_mode():
    context.configure(url=url, target_metadata=Base.metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    with create_engine(url).connect() as connection:
        context.configure(connection=connection, target_metadata=Base.metadata)
        with context.begin_transaction():
            context.run_migrations()
