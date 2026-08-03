from __future__ import annotations

import unittest
from unittest.mock import patch

import requests

from deepseek_balance_monitor.api import ApiClient
from deepseek_balance_monitor.models import ProbeStatus


class FakeResponse:
    def __init__(self, status_code: int, payload=None, json_error: Exception | None = None):
        self.status_code = status_code
        self._payload = payload
        self._json_error = json_error

    def json(self):
        if self._json_error is not None:
            raise self._json_error
        return self._payload


class FakeSession:
    def __init__(self, response: FakeResponse):
        self.response = response
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


class FailingSession:
    def get(self, url, **kwargs):
        raise RuntimeError("secret-key must not be exposed")


class PreparingSession(requests.Session):
    def __init__(self):
        super().__init__()
        self.prepared = []

    def send(self, request, **kwargs):
        self.prepared.append((request, kwargs))
        return FakeResponse(401)


VALID_PAYLOAD = {
    "is_available": True,
    "balance_infos": [
        {
            "currency": "CNY",
            "total_balance": "12.30",
            "granted_balance": "2.30",
            "topped_up_balance": "10.00",
        }
    ],
}


class ApiClientTests(unittest.TestCase):
    def client_for(self, response: FakeResponse) -> tuple[ApiClient, FakeSession]:
        session = FakeSession(response)
        return ApiClient(session=session), session

    def test_probe_200_parses_validated_schema(self):
        client, _ = self.client_for(FakeResponse(200, VALID_PAYLOAD))

        result = client.probe_key("secret-key")

        self.assertEqual(ProbeStatus.VALID, result.status)
        self.assertEqual("12.30", str(result.snapshot.balance_infos[0].total_balance))

    def test_probe_200_with_missing_fields_is_indeterminate(self):
        client, _ = self.client_for(FakeResponse(200, {"is_available": True}))

        result = client.probe_key("secret-key")

        self.assertEqual(ProbeStatus.INDETERMINATE, result.status)
        self.assertIsNone(result.snapshot)

    def test_probe_200_with_non_finite_balance_is_indeterminate(self):
        payload = {
            **VALID_PAYLOAD,
            "balance_infos": [{**VALID_PAYLOAD["balance_infos"][0], "total_balance": "NaN"}],
        }
        client, _ = self.client_for(FakeResponse(200, payload))

        result = client.probe_key("secret-key")

        self.assertEqual(ProbeStatus.INDETERMINATE, result.status)

    def test_amount_fields_require_clean_finite_json_strings(self):
        invalid_values = (
            12,
            12.3,
            True,
            None,
            "",
            " ",
            " 12.30",
            "12.30 ",
            "Infinity",
            "١٢.٣٠",
            "1_2.30",
        )
        amount_fields = ("total_balance", "granted_balance", "topped_up_balance")
        for field in amount_fields:
            for invalid in invalid_values:
                with self.subTest(field=field, invalid=repr(invalid)):
                    raw_info = {**VALID_PAYLOAD["balance_infos"][0], field: invalid}
                    payload = {**VALID_PAYLOAD, "balance_infos": [raw_info]}
                    client, _ = self.client_for(FakeResponse(200, payload))

                    result = client.probe_key("secret-key")

                    self.assertEqual(ProbeStatus.INDETERMINATE, result.status)
                    self.assertEqual("Balance response has an invalid schema", result.error)

    def test_clean_currency_is_validated_and_normalized(self):
        raw_info = {**VALID_PAYLOAD["balance_infos"][0], "currency": "cNy9"}
        payload = {**VALID_PAYLOAD, "balance_infos": [raw_info]}
        client, _ = self.client_for(FakeResponse(200, payload))

        result = client.probe_key("secret-key")

        self.assertEqual(ProbeStatus.VALID, result.status)
        self.assertEqual("CNY9", result.snapshot.balance_infos[0].currency)

    def test_currency_rejects_invalid_codes_with_generic_error(self):
        for invalid in (
            "",
            " ",
            " CNY",
            "CNY ",
            "\tCNY\n",
            "US-D",
            "ß",
            "人民币",
            "ABCDEFGHIJKLM",
            None,
        ):
            with self.subTest(invalid=repr(invalid)):
                raw_info = {**VALID_PAYLOAD["balance_infos"][0], "currency": invalid}
                payload = {**VALID_PAYLOAD, "balance_infos": [raw_info]}
                client, _ = self.client_for(FakeResponse(200, payload))

                result = client.probe_key("secret-key")

                self.assertEqual(ProbeStatus.INDETERMINATE, result.status)
                self.assertEqual("Balance response has an invalid schema", result.error)

    def test_normalized_duplicate_currency_rejects_entire_snapshot(self):
        duplicate = {**VALID_PAYLOAD["balance_infos"][0], "currency": "cny"}
        payload = {
            **VALID_PAYLOAD,
            "balance_infos": [VALID_PAYLOAD["balance_infos"][0], duplicate],
        }
        client, _ = self.client_for(FakeResponse(200, payload))

        result = client.probe_key("secret-key")

        self.assertEqual(ProbeStatus.INDETERMINATE, result.status)
        self.assertIsNone(result.snapshot)
        self.assertEqual("Balance response has an invalid schema", result.error)

    def test_unavailable_flag_does_not_invalidate_well_formed_snapshot(self):
        client, _ = self.client_for(FakeResponse(200, {**VALID_PAYLOAD, "is_available": False}))

        result = client.probe_key("secret-key")

        self.assertEqual(ProbeStatus.VALID, result.status)
        self.assertFalse(result.snapshot.is_available)

    def test_probe_401_is_invalid(self):
        client, _ = self.client_for(FakeResponse(401))

        self.assertEqual(ProbeStatus.INVALID, client.probe_key("secret-key").status)

    def test_probe_402_is_authenticated_without_balance(self):
        client, _ = self.client_for(FakeResponse(402))

        self.assertEqual(
            ProbeStatus.AUTHENTICATED_NO_BALANCE,
            client.probe_key("secret-key").status,
        )

    def test_probe_429_is_indeterminate(self):
        client, _ = self.client_for(FakeResponse(429))

        self.assertEqual(ProbeStatus.INDETERMINATE, client.probe_key("secret-key").status)

    def test_probe_5xx_is_indeterminate(self):
        client, _ = self.client_for(FakeResponse(503))

        self.assertEqual(ProbeStatus.INDETERMINATE, client.probe_key("secret-key").status)

    def test_probe_invalid_json_is_indeterminate(self):
        client, _ = self.client_for(FakeResponse(200, json_error=ValueError("bad json")))

        self.assertEqual(ProbeStatus.INDETERMINATE, client.probe_key("secret-key").status)

    def test_fetch_missing_fields_returns_safe_error(self):
        client, _ = self.client_for(FakeResponse(200, {"balance_infos": []}))

        result = client.fetch_balance("secret-key")

        self.assertIsNone(result.snapshot)
        self.assertIsNotNone(result.error)

    def test_request_disables_redirects_and_uses_contract_timeout(self):
        client, session = self.client_for(FakeResponse(401))

        client.probe_key("secret-key")

        url, options = session.calls[0]
        self.assertEqual("https://api.deepseek.com/user/balance", url)
        self.assertFalse(options["allow_redirects"])
        self.assertEqual((3.05, 6.0), options["timeout"])
        self.assertTrue(options["verify"])
        prepared = requests.Request("GET", url).prepare()
        options["auth"](prepared)
        self.assertEqual("Bearer secret-key", prepared.headers["Authorization"])

    def test_explicit_bearer_auth_cannot_be_replaced_by_netrc(self):
        session = PreparingSession()
        with patch(
            "requests.sessions.get_netrc_auth",
            return_value=("netrc-user", "netrc-secret"),
        ):
            result = ApiClient(session=session).probe_key("secret-key")

        self.assertEqual(ProbeStatus.INVALID, result.status)
        self.assertTrue(session.trust_env)
        request, _ = session.prepared[0]
        self.assertEqual("Bearer secret-key", request.headers["Authorization"])

    def test_bearer_auth_representation_does_not_expose_key(self):
        client, session = self.client_for(FakeResponse(401))

        client.probe_key("secret-key")

        auth = session.calls[0][1]["auth"]
        self.assertNotIn("secret-key", repr(auth))
        self.assertNotIn("secret-key", str(auth))

    def test_error_text_never_contains_key(self):
        client, _ = self.client_for(FakeResponse(503))

        result = client.fetch_balance("secret-key")

        self.assertNotIn("secret-key", result.error)

    def test_transport_error_never_contains_key(self):
        result = ApiClient(session=FailingSession()).fetch_balance("secret-key")

        self.assertNotIn("secret-key", result.error)


if __name__ == "__main__":
    unittest.main()
