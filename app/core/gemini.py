"""Google's native REST adapter. Only explicitly confirmed unbilled projects are supported."""

import json
import re
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from app.core.cloud import CloudClient, CloudError, ModelConfig, RequestCancelled, SegmentResponse


GEMINI_BASE = 'https://generativelanguage.googleapis.com/v1beta'
GEMINI_MODEL = 'gemini-3.5-flash-lite'


class GeminiClient(CloudClient):
    def __init__(self, config: ModelConfig, transport=None, *, confirmed_free_project=False):
        if not confirmed_free_project:
            raise ValueError('Confirm that this Gemini key belongs to a project without billing enabled. The app cannot verify its billing tier.')
        if config.base_url.rstrip('/') != GEMINI_BASE or config.paid:
            raise ValueError('Direct Gemini requires the fixed Google HTTPS endpoint and an unbilled project.')
        if not re.fullmatch(r'gemini-[a-zA-Z0-9_.-]+', config.model):
            raise ValueError('Choose a fixed Gemini model ID.')
        super().__init__(config, transport)

    def prepare(self):
        self.context_length = 1_048_576
        self.reasoning_options = {'mandatory': True}

    def quota_day(self, now):
        return datetime.fromtimestamp(now, ZoneInfo('America/Los_Angeles')).date().isoformat()

    def next_quota_retry(self, now):
        local = datetime.fromtimestamp(now, ZoneInfo('America/Los_Angeles'))
        return (local.replace(hour=0, minute=5, second=0, microsecond=0) + timedelta(days=1)).timestamp()

    def check_allowance(self, cancel_callback=None):
        # Google does not expose an authenticated remaining-free-requests snapshot here.
        while self._last_dispatched and time.monotonic() - self._last_dispatched < 3.2:
            if cancel_callback and cancel_callback():
                raise RequestCancelled('Cancelled while waiting for the request interval')
            time.sleep(.1)

    def _http(self):
        return httpx.Client(timeout=httpx.Timeout(120, connect=10), transport=self.transport, follow_redirects=False)

    def _send(self, payload, numbers, cancel_callback):
        parts = []
        for part in payload['messages'][1]['content']:
            if part['type'] == 'text':
                parts.append({'text': part['text']})
            else:
                parts.append({'inlineData': {'mimeType': 'image/png',
                              'data': part['image_url']['url'].split(',', 1)[1]}})
        request = {'systemInstruction': {'parts': [{'text': payload['messages'][0]['content']}]},
                   'contents': [{'role': 'user', 'parts': parts}],
                   'generationConfig': {'temperature': .1, 'maxOutputTokens': payload['max_tokens'],
                                        'responseMimeType': 'application/json',
                                        'responseJsonSchema': payload['response_format']['json_schema']['schema']}}
        if cancel_callback and cancel_callback():
            raise RequestCancelled('Cancelled before sending a request')
        started = time.monotonic()
        try:
            with self._http() as client:
                self._last_dispatched = started
                with client.stream('POST', f'{GEMINI_BASE}/models/{self.model}:generateContent',
                                   headers={'x-goog-api-key': self.config.api_key, 'Content-Type': 'application/json'},
                                   json=request) as response:
                    body = b''.join(self._checked_chunks(response, started, cancel_callback))
                    if response.status_code >= 300:
                        self._google_error(response.status_code, body, dict(response.headers))
                    data = json.loads(body)
        except httpx.RequestError as exc:
            raise CloudError('Google connection failed. Check connectivity and regional availability.', 'network') from exc
        except (ValueError, KeyError, TypeError) as exc:
            raise CloudError('Google returned an invalid response.', 'service') from exc
        usage = data.get('usageMetadata') or {}
        actual_model = data.get('modelVersion') or self.model
        result = SegmentResponse({}, 'blocked', self.model, 'Google AI Studio',
                                 int(usage.get('promptTokenCount') or 0),
                                 int(usage.get('candidatesTokenCount') or 0) + int(usage.get('thoughtsTokenCount') or 0),
                                 0.0, data.get('responseId', ''))
        if actual_model != self.model and not actual_model.startswith(self.model + '-'):
            result.finish_reason = 'unexpected_model'
            return result
        candidates = data.get('candidates') or []
        if not candidates:
            return result
        candidate = candidates[0]
        result.finish_reason = {'STOP': 'stop', 'MAX_TOKENS': 'length'}.get(candidate.get('finishReason'), 'blocked')
        if result.finish_reason != 'stop':
            return result
        try:
            text = ''.join(part.get('text', '') for part in candidate.get('content', {}).get('parts', []) if not part.get('thought'))
            rows = json.loads(text)['notes']
            result.notes = {row['page']: row['markdown'] for row in rows}
            if (any(type(row['page']) is not int for row in rows) or len(rows) != len(numbers) or
                    set(result.notes) != set(numbers) or any(not isinstance(value, str) or not value.strip() for value in result.notes.values())):
                result.notes, result.finish_reason = {}, 'incomplete'
        except (ValueError, KeyError, TypeError):
            result.notes, result.finish_reason = {}, 'invalid'
        return result

    @staticmethod
    def _google_error(status, body, headers):
        # Read error metadata to classify it, but never forward provider text or request data.
        try:
            data = json.loads(body)
        except ValueError:
            data = {}
        detail = json.dumps(data).lower()
        if status == 429:
            daily = any(value in detail for value in ('perday', 'per_day', 'per day', 'daily', 'requests/day'))
            retry = 2.0
            for item in (data.get('error') or {}).get('details', []):
                try:
                    retry = max(retry, float(item.get('retryDelay', '0s').rstrip('s')))
                except (ValueError, TypeError, AttributeError):
                    pass
            raise CloudError('Google free daily quota exhausted.' if daily else 'Google rate limit reached.',
                             'daily_quota' if daily else 'rate_limit', retry)
        CloudClient._raise_error(httpx.Response(status, content=body, headers={
            key: value for key, value in headers.items() if key.lower() not in {'content-encoding', 'content-length', 'transfer-encoding'}}))

    def list_models(self, cancel_callback=None):
        if cancel_callback and cancel_callback():
            raise RequestCancelled('Model list cancelled')
        try:
            with self._http() as client:
                response = client.get(f'{GEMINI_BASE}/models', headers={'x-goog-api-key': self.config.api_key})
            if response.status_code != 200:
                self._google_error(response.status_code, response.content, dict(response.headers))
            return [{'id': row['name'].removeprefix('models/'), 'name': row.get('displayName', row['name']),
                     'free': True, 'conditional_free': True} for row in response.json().get('models', [])
                    if 'generateContent' in row.get('supportedGenerationMethods', []) and
                    re.fullmatch(r'models/gemini-[\d.]+-flash(?:-lite)?', row['name'])]
        except httpx.RequestError as exc:
            raise CloudError('Cannot reach the Google model catalog.', 'network') from exc
