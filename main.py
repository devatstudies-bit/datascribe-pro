"""
DataScribe Pro — Entry Point
Run: python main.py   (or: uvicorn main:app --reload)

Ensure .env is configured before starting.
See .env.example for all options.
"""
import uvicorn
from src.config.settings import get_settings
from src.observability.logger import setup_logging
from src.observability.tracing import setup_tracing

setup_logging()   # structlog JSON pipeline must be ready before any imports log
setup_tracing()

from src.consumer.api import app  # noqa: E402


if __name__ == "__main__":
    s = get_settings()
    print(f"""
╔══════════════════════════════════════════════╗
║        DataScribe Pro  v1.0                  ║
║  AI-Powered Read-Only Database Explorer      ║
╠══════════════════════════════════════════════╣
║  Provider : {s.llm_provider:<32}║
║  Database : {s.database_url:<32}║
║  URL      : http://{s.app_host}:{s.app_port:<22}║
╚══════════════════════════════════════════════╝
""")
    uvicorn.run(
        "main:app",
        host=s.app_host,
        port=s.app_port,
        reload=False,
        log_level="warning",   # uvicorn access logs — our handlers cover the rest
    )
