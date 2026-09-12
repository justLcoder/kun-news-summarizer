import io
import json
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import Mock, patch

from news_backend.jobs import refresh
from news_backend.services.ingestion import IngestionFailure, IngestionResult
from news_backend.services.summarization import (
    SummarizationAlreadyRunning,
    SummarizationFailure,
    SummarizationResult,
)


def ingestion(*, stored=1, failures=()):
    return IngestionResult(
        discovered=3,
        skipped=3 - stored - len(failures),
        stored=stored,
        failures=failures,
    )


def summarization(*, failures=()):
    return SummarizationResult(selected=2, stored=2 - len(failures), skipped=0, failures=failures)


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.factory = Mock(name="session_factory")
        self.generate = Mock(name="generate")

    def run_job(self, *, ingestion_result=None, summary_result=None, **kwargs):
        with (
            patch.object(refresh, "ingest_kun_uz", return_value=ingestion_result or ingestion()) as ingest,
            patch.object(refresh, "summarize_articles", return_value=summary_result or summarization()) as summarize,
        ):
            result = refresh.run_refresh(
                self.factory, generate=self.generate, **kwargs
            )
        return result, ingest, summarize

    def test_ingestion_precedes_summarization_and_limit_is_forwarded(self):
        order = []
        with (
            patch.object(refresh, "ingest_kun_uz", side_effect=lambda factory: order.append("ingest") or ingestion()),
            patch.object(refresh, "summarize_articles", side_effect=lambda *args, **kwargs: order.append("summarize") or summarization()) as summarize,
        ):
            result = refresh.run_refresh(self.factory, generate=self.generate, summary_limit=27)
        self.assertEqual(order, ["ingest", "summarize"])
        summarize.assert_called_once_with(self.factory, generate=self.generate, limit=27)
        self.assertEqual(result.summarization_status, "completed")
        self.assertIsInstance(result.summarization, SummarizationResult)

    def test_partial_ingestion_failures_still_summarize(self):
        partial = ingestion(failures=(IngestionFailure("private-url", "invalid_article"),))
        result, _, summarize = self.run_job(ingestion_result=partial)
        summarize.assert_called_once()
        self.assertIs(result.ingestion, partial)

    def test_zero_stored_articles_still_summarize(self):
        result, _, summarize = self.run_job(ingestion_result=ingestion(stored=0))
        summarize.assert_called_once()
        self.assertEqual(result.ingestion.stored, 0)

    def test_fatal_ingestion_prevents_summarization(self):
        with (
            patch.object(refresh, "ingest_kun_uz", side_effect=RuntimeError("rss failed")),
            patch.object(refresh, "summarize_articles") as summarize,
        ):
            with self.assertRaisesRegex(RuntimeError, "rss failed"):
                refresh.run_refresh(self.factory, generate=self.generate)
        summarize.assert_not_called()

    def test_article_level_summary_failure_is_completed(self):
        failed = summarization(failures=(SummarizationFailure(5, "models_unavailable"),))
        result, _, _ = self.run_job(summary_result=failed)
        self.assertEqual(result.summarization_status, "completed")
        self.assertIs(result.summarization, failed)

    def test_fatal_summarization_failure_propagates(self):
        with (
            patch.object(refresh, "ingest_kun_uz", return_value=ingestion()),
            patch.object(refresh, "summarize_articles", side_effect=RuntimeError("provider fatal")),
        ):
            with self.assertRaisesRegex(RuntimeError, "provider fatal"):
                refresh.run_refresh(self.factory, generate=self.generate)

    def test_already_running_is_explicit_success(self):
        with (
            patch.object(refresh, "ingest_kun_uz", return_value=ingestion()) as ingest,
            patch.object(refresh, "summarize_articles", side_effect=SummarizationAlreadyRunning()),
        ):
            result = refresh.run_refresh(self.factory, generate=self.generate)
        ingest.assert_called_once()
        self.assertEqual(result.summarization_status, "already_running")
        self.assertIsNone(result.summarization)

    def test_limit_validation_precedes_services(self):
        for value in (0, -1, 101, 1.5, True, "10"):
            with self.subTest(value=value), patch.object(refresh, "ingest_kun_uz") as ingest:
                with self.assertRaises(ValueError):
                    refresh.run_refresh(self.factory, generate=self.generate, summary_limit=value)
                ingest.assert_not_called()

    def test_result_invariant(self):
        for status, summary in (("completed", None), ("already_running", summarization()), ("other", None)):
            with self.subTest(status=status), self.assertRaises(ValueError):
                refresh.RefreshResult(ingestion(), status, summary)


class ConfiguredLifecycleTests(unittest.TestCase):
    def configured(self, *, run_side_effect=None, router_side_effect=None):
        engine = Mock(name="engine")
        factory = Mock(name="factory")
        client = Mock(name="client")
        router = Mock(name="router")
        expected = refresh.RefreshResult(ingestion(), "completed", summarization())
        with (
            patch.object(refresh, "make_engine", return_value=engine) as make_engine,
            patch.object(refresh, "make_session_factory", return_value=factory) as make_factory,
            patch.object(refresh, "make_client", return_value=client) as make_client,
            patch.object(refresh, "make_router", return_value=router, side_effect=router_side_effect) as make_router,
            patch.object(refresh, "run_refresh", return_value=expected, side_effect=run_side_effect) as run,
        ):
            if run_side_effect or router_side_effect:
                with self.assertRaises(RuntimeError):
                    refresh.run_configured_refresh(summary_limit=12)
                result = None
            else:
                result = refresh.run_configured_refresh(summary_limit=12)
        return result, engine, factory, client, router, make_engine, make_factory, make_client, make_router, run

    def test_success_uses_each_resource_once_and_cleans_up(self):
        result, engine, factory, client, router, make_engine, make_factory, make_client, make_router, run = self.configured()
        self.assertIsInstance(result, refresh.RefreshResult)
        make_engine.assert_called_once_with()
        make_factory.assert_called_once_with(engine)
        make_client.assert_called_once_with()
        make_router.assert_called_once_with(client=client)
        run.assert_called_once_with(factory, generate=router.generate_summary, summary_limit=12)
        client.close.assert_called_once_with()
        engine.dispose.assert_called_once_with()
        client.models.generate_content.assert_not_called()

    def test_cleanup_after_ingestion_or_summarization_failure(self):
        for message in ("ingestion failed", "summarization failed"):
            with self.subTest(message=message):
                values = self.configured(run_side_effect=RuntimeError(message))
                values[1].dispose.assert_called_once_with()
                values[3].close.assert_called_once_with()

    def test_router_composition_failure_cleans_constructed_resources(self):
        values = self.configured(router_side_effect=RuntimeError("invalid references"))
        values[1].dispose.assert_called_once_with()
        values[3].close.assert_called_once_with()
        values[-1].assert_not_called()

    def test_client_construction_failure_disposes_engine(self):
        engine = Mock()
        with (
            patch.object(refresh, "make_engine", return_value=engine),
            patch.object(refresh, "make_session_factory"),
            patch.object(refresh, "make_client", side_effect=RuntimeError("bad key")),
            patch.object(refresh, "make_router") as make_router,
        ):
            with self.assertRaisesRegex(RuntimeError, "bad key"):
                refresh.run_configured_refresh()
        engine.dispose.assert_called_once_with()
        make_router.assert_not_called()

    def test_import_creates_no_resources(self):
        code = """from unittest.mock import patch
with patch('news_backend.db.session.make_engine') as engine, \\
     patch('news_backend.integrations.gemini.config.make_client') as client, \\
     patch('news_backend.integrations.gemini.config.make_router') as router:
    import news_backend.jobs.refresh
    engine.assert_not_called()
    client.assert_not_called()
    router.assert_not_called()
"""
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


class CliTests(unittest.TestCase):
    def completed(self):
        return refresh.RefreshResult(ingestion(), "completed", summarization())

    def test_default_and_explicit_limit_and_compact_json(self):
        for argv, limit in (([], 10), (["--summary-limit", "20"], 20)):
            with (
                self.subTest(argv=argv),
                patch.object(refresh, "run_configured_refresh", return_value=self.completed()) as run,
                redirect_stdout(io.StringIO()) as stdout,
            ):
                self.assertEqual(refresh.main(argv), 0)
            run.assert_called_once_with(summary_limit=limit)
            output = stdout.getvalue()
            self.assertEqual(output.count("\n"), 1)
            self.assertEqual(output.strip(), json.dumps({
                "status": "completed",
                "ingestion": {"discovered": 3, "stored": 1, "skipped": 2, "failed": 0},
                "summarization": {"status": "completed", "selected": 2, "stored": 2, "skipped": 0, "failed": 0},
            }, separators=(",", ":")))

    def test_already_running_json(self):
        result = refresh.RefreshResult(ingestion(), "already_running", None)
        with patch.object(refresh, "run_configured_refresh", return_value=result), redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(refresh.main([]), 0)
        self.assertEqual(json.loads(stdout.getvalue())["summarization"], {"status": "already_running"})

    def test_invalid_limits_exit_two_without_running(self):
        for value in ("0", "-1", "101", "not-an-integer"):
            with (
                self.subTest(value=value),
                patch.object(refresh, "run_configured_refresh") as run,
                redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit) as raised,
            ):
                refresh.main(["--summary-limit", value])
            self.assertEqual(raised.exception.code, 2)
            run.assert_not_called()

    def test_fatal_exception_is_exit_one_and_prints_no_json(self):
        with (
            patch.object(refresh, "run_configured_refresh", side_effect=RuntimeError("fatal")),
            self.assertLogs(refresh.logger, level="ERROR") as logs,
            redirect_stdout(io.StringIO()) as stdout,
        ):
            self.assertEqual(refresh.main([]), 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("RuntimeError: fatal", "\n".join(logs.output))

    def test_keyboard_interrupt_is_not_converted(self):
        with patch.object(refresh, "run_configured_refresh", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                refresh.main([])
