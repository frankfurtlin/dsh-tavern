from alembic import context
from app.db import engine, Base
from app import models
from app.foundation import models as product_models
from app.experience import models as experience_models

with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()
