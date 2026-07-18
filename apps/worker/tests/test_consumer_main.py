from enterprise_doc_worker.config import WorkerSettings
from enterprise_doc_worker.consumer_main import consumer_worker_argv
from enterprise_doc_worker.queue import JOB_QUEUE_NAME


def test_consumer_entrypoint_starts_the_registered_job_queue() -> None:
    settings = WorkerSettings(_env_file=None)

    argv = consumer_worker_argv(settings)

    assert argv[:2] == ["worker", "--loglevel"]
    assert argv[argv.index("--pool") + 1] == "solo"
    assert argv[argv.index("--concurrency") + 1] == "1"
    assert argv[argv.index("--queues") + 1] == JOB_QUEUE_NAME
    assert argv[argv.index("--hostname") + 1] == f"{settings.worker.worker_id}@%h"
