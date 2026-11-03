import logging
import os
from logging.handlers import TimedRotatingFileHandler
from .config import settings


def setup_logging():
    folder=settings.storage_path/"technical-logs"
    folder.mkdir(parents=True,exist_ok=True,mode=0o700)
    # Independent API/executor processes must not rotate a file another process
    # holds open on Windows. Keep the same structured, private technical log.
    pending=[logging.getLogger(name) for name in ('reader','worker') if not logging.getLogger(name).handlers]
    if not pending:return
    name='runtime-'+str(os.getpid())+'.log' if os.name=='nt' else 'runtime.log'
    handler=TimedRotatingFileHandler(folder/name,when="midnight",backupCount=14,utc=True,encoding="utf-8")
    formatter=logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    handler.setFormatter(formatter)
    for logger in pending:
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
