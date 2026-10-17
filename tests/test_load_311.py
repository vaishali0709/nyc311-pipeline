"""Offline regression tests for temporary NYC Open Data outages."""

import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from unittest.mock import Mock, call, patch

import requests

os.environ.setdefault("GCP_PROJECT", "retry-tests")
from ingest import load_311


def response(status, data=None, retry_after=None):
    result = requests.Response()
    result.status_code = status
    result.url = load_311.API
    result._content = json.dumps(data if data is not None else []).encode()
    result._content_consumed = True
    if retry_after is not None:
        result.headers["Retry-After"] = retry_after
    return result


class FetchPageTests(unittest.TestCase):
    def setUp(self):
        self.cursor = datetime(2026, 10, 14, 21, 52, 33, tzinfo=timezone.utc)
        self.batch = [{"unique_key": "1", "created_date": "2026-10-14T21:52:34"}]
        get_patch = patch.object(load_311.requests, "get")
        sleep_patch = patch.object(load_311.time, "sleep")
        self.get = get_patch.start()
        self.sleep = sleep_patch.start()
        self.addCleanup(get_patch.stop)
        self.addCleanup(sleep_patch.stop)

    def test_success_does_not_retry(self):
        self.get.return_value = response(200, self.batch)
        self.assertEqual(load_311.fetch_page(self.cursor), self.batch)
        self.get.assert_called_once()
        self.sleep.assert_not_called()

    def test_temporary_http_errors_retry_the_identical_page(self):
        for status in (408, 429, 500, 502, 503, 504):
            with self.subTest(status=status):
                self.get.reset_mock()
                self.sleep.reset_mock()
                self.get.side_effect = [response(status), response(200, self.batch)]
                self.assertEqual(load_311.fetch_page(self.cursor), self.batch)
                self.assertEqual(self.get.call_count, 2)
                self.assertEqual(self.get.call_args_list[0], self.get.call_args_list[1])
                self.assertEqual(
                    self.get.call_args.kwargs["params"]["$where"],
                    "created_date >= '2026-10-14T21:52:33'",
                )
                self.sleep.assert_called_once_with(10)

    def test_permanent_http_errors_fail_immediately(self):
        for status in (400, 401, 403, 404, 422, 501):
            with self.subTest(status=status):
                self.get.reset_mock()
                self.sleep.reset_mock()
                self.get.return_value = response(status)
                with self.assertRaises(requests.HTTPError):
                    load_311.fetch_page(self.cursor)
                self.get.assert_called_once()
                self.sleep.assert_not_called()

    def test_persistent_503_fails_after_bounded_attempts(self):
        self.get.side_effect = [response(503) for _ in range(load_311.MAX_RETRIES)]
        with self.assertRaises(requests.HTTPError) as error:
            load_311.fetch_page(self.cursor)
        self.assertEqual(error.exception.response.status_code, 503)
        self.assertEqual(self.get.call_count, load_311.MAX_RETRIES)
        self.assertEqual(self.sleep.call_args_list, [call(10), call(20), call(40), call(80)])

    def test_transport_errors_still_retry(self):
        for error in (requests.Timeout(), requests.ConnectionError(), TimeoutError()):
            with self.subTest(error=type(error).__name__):
                self.get.reset_mock()
                self.sleep.reset_mock()
                self.get.side_effect = [error, response(200, self.batch)]
                self.assertEqual(load_311.fetch_page(self.cursor), self.batch)
                self.sleep.assert_called_once_with(10)

    def test_persistent_timeout_is_not_reported_as_success(self):
        self.get.side_effect = requests.Timeout("unavailable")
        with self.assertRaises(requests.Timeout):
            load_311.fetch_page(self.cursor)
        self.assertEqual(self.get.call_count, load_311.MAX_RETRIES)
        self.assertEqual(self.sleep.call_count, load_311.MAX_RETRIES - 1)

    def test_retry_after_controls_wait(self):
        self.get.side_effect = [response(429, retry_after="45"), response(200, self.batch)]
        self.assertEqual(load_311.fetch_page(self.cursor), self.batch)
        self.sleep.assert_called_once_with(45)

    def test_second_page_503_recovers_and_continues_loading(self):
        first_page = [
            {"unique_key": "1", "created_date": "2026-10-14T21:52:34"},
            {"unique_key": "2", "created_date": "2026-10-14T21:52:35"},
        ]
        last_page = [{"unique_key": "3", "created_date": "2026-10-14T21:52:36"}]
        self.get.side_effect = [response(200, first_page), response(503), response(200, last_page)]
        client = Mock()
        with patch.object(load_311.bigquery, "Client", return_value=client), \
                patch.object(load_311, "get_watermark", return_value=self.cursor), \
                patch.object(load_311, "PAGE_SIZE", 2), \
                patch.object(load_311, "load") as load:
            self.assertEqual(load_311.main(), 0)
        self.assertEqual(load.call_count, 2)
        loaded_keys = [key for item in load.call_args_list for key in item.args[1]["unique_key"]]
        self.assertEqual(loaded_keys, ["1", "2", "3"])
        self.assertEqual(self.get.call_args_list[1], self.get.call_args_list[2])
        self.assertEqual(
            self.get.call_args.kwargs["params"]["$where"],
            "created_date >= '2026-10-14T21:52:35'",
        )


class RetryDelayTests(unittest.TestCase):
    def test_bad_or_past_retry_after_uses_backoff(self):
        for header in (None, "", "invalid", "1.5", "-1", "Thu, 01 Jan 1970 00:00:00 GMT"):
            with self.subTest(header=header):
                self.assertEqual(load_311.retry_delay(2, header), 20)

    def test_http_date_retry_after_is_supported(self):
        now = datetime(2026, 10, 17, 10, 0, tzinfo=timezone.utc)
        header = format_datetime(now + timedelta(seconds=45), usegmt=True)
        with patch.object(load_311, "datetime") as clock:
            clock.now.return_value = now
            self.assertEqual(load_311.retry_delay(1, header), 45)

    def test_wait_is_capped(self):
        self.assertEqual(load_311.retry_delay(10), load_311.MAX_RETRY_DELAY)
        self.assertEqual(load_311.retry_delay(1, "999999"), load_311.MAX_RETRY_DELAY)


if __name__ == "__main__":
    unittest.main()
