from __future__ import annotations

import json
import subprocess
import sys
from textwrap import dedent


def test_celery_startup_preserves_structured_redacted_application_errors() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            dedent(
                """
                import logging

                from enterprise_doc_core.config import AppEnvironment
                from enterprise_doc_core.logging import configure_logging
                from enterprise_doc_worker.config import WorkerSettings
                from enterprise_doc_worker.queue import create_celery_app

                configure_logging(service="worker-consumer", environment="local", level="INFO")
                app = create_celery_app(
                    WorkerSettings(_env_file=None, app_env=AppEnvironment.LOCAL)
                )
                app.log.setup(loglevel="INFO", redirect_stdouts=True)
                logger = logging.getLogger("enterprise_doc_worker.agent_handler")
                try:
                    raise RuntimeError("synthetic-private-exception-body")
                except RuntimeError:
                    logger.exception(
                        "agent_execution_handler_failed",
                        extra={
                            "event_data": {
                                "execution_kind": "initial",
                                "diagnostic_code": "agent.unexpected.runtime_error",
                                "api_key": "synthetic-private-api-key",
                            }
                        },
                    )
                """
            ),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )

    assert "synthetic-private" not in result.stdout + result.stderr
    assert "Traceback" not in result.stdout + result.stderr
    records = [json.loads(line) for line in result.stderr.splitlines()]
    assert len(records) == 1
    record = records[0]
    assert record["event"] == "agent_execution_handler_failed"
    assert record["error_type"] == "RuntimeError"
    assert record["execution_kind"] == "initial"
    assert record["diagnostic_code"] == "agent.unexpected.runtime_error"
    assert record["api_key"] == "**********"
    assert record["service"] == "worker-consumer"
