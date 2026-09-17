from __future__ import annotations

import threading
import time
import ssl
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests

from .models import RestorationError


class ArchiveClient:
    """Only explicit archive URLs; every redirect remains inside Wayback."""

    def __init__(self, cancel: threading.Event | None = None, timeout: int = 35, on_event=None, on_activity=None):
        self.session = requests.Session()
        self.session.headers['User-Agent'] = 'DropRestorer/0.1 (user-selected Wayback snapshots)'
        self.cancel = cancel or threading.Event()
        self.timeout = timeout
        self.on_event = on_event or (lambda message: None)
        self.on_activity = on_activity or (lambda event: None)
        self.last_response_url = None

    @staticmethod
    def safe_url(url: str) -> str:
        parsed = urlsplit(url)
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, '', '')) + ('?[параметры скрыты]' if parsed.query else '')

    @staticmethod
    def tls_reason(error):
        """Classify wrapped urllib3/OpenSSL errors without logging their raw URLs."""
        pending, seen, values = [error], set(), []
        while pending:
            current = pending.pop()
            if id(current) in seen:
                continue
            seen.add(id(current))
            values.append(current)
            pending.extend(value for value in (
                getattr(current, 'reason', None), getattr(current, '__cause__', None),
                getattr(current, '__context__', None), *getattr(current, 'args', ()))
                if isinstance(value, BaseException))
        message = ' '.join(str(value) for value in values).upper()
        # Certificate failures must never be reclassified as transient EOFs.
        if any(isinstance(value, ssl.SSLCertVerificationError) for value in values) or any(
                token in message for token in ('CERTIFICATE_VERIFY_FAILED', 'HOSTNAME MISMATCH', 'CERTIFICATE HAS EXPIRED')):
            return 'certificate'
        if any(isinstance(value, ssl.SSLEOFError) for value in values) or any(
                token in message for token in ('UNEXPECTED_EOF_WHILE_READING', 'EOF OCCURRED IN VIOLATION OF PROTOCOL',
                                                'TLSV1_ALERT_INTERNAL_ERROR', 'SSLV3_ALERT_INTERNAL_ERROR')):
            return 'interrupted'
        return 'other'

    def request_error(self, error, url):
        code = getattr(error.response, 'status_code', None)
        if code is not None:
            detail = f'HTTP {code}'
        elif isinstance(error, requests.exceptions.SSLError):
            reason = self.tls_reason(error)
            detail = {'interrupted': 'защищённое соединение TLS оборвалось (EOF/internal alert)',
                      'certificate': 'не удалось проверить сертификат TLS',
                      'other': 'ошибка защищённого соединения TLS'}[reason]
        elif isinstance(error, requests.Timeout):
            detail = 'время ожидания ответа истекло'
        elif isinstance(error, requests.ConnectionError):
            detail = 'соединение прервано или не установлено'
        elif isinstance(error, requests.exceptions.ChunkedEncodingError):
            detail = 'загрузка ответа прервалась'
        else:
            detail = 'ошибка сетевого запроса'
        return f'Не удалось скачать ресурс из Wayback: {detail} ({type(error).__name__}). Адрес: {self.safe_url(url)}'

    def check_cancel(self):
        if self.cancel.is_set():
            raise RestorationError('Восстановление отменено.')

    @staticmethod
    def retry_delay(response, attempt):
        fallback = 2 ** (attempt + 1)
        value = response.headers.get('Retry-After', '').strip()
        try:
            delay = float(value) if value.isdigit() else (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
            return max(fallback, min(300, delay))
        except (TypeError, ValueError, OverflowError):
            return fallback

    def get(self, url: str, limit: int = 30_000_000) -> tuple[bytes, str]:
        for redirect in range(8):
            self.check_cancel()
            parsed = urlsplit(url)
            if parsed.hostname != 'web.archive.org' or parsed.scheme not in ('http', 'https') or parsed.username or parsed.port:
                raise RestorationError('Wayback перенаправил ресурс за пределы архива; загрузка остановлена.')
            redirected = False
            for attempt in range(3):
                response = None
                try:
                    self.check_cancel()
                    safe_url = self.safe_url(url)
                    started = time.monotonic()
                    self.on_activity({'status': 'requesting', 'url': safe_url, 'attempt': attempt + 1,
                                      'request_bytes': 0})
                    response = self.session.get(url, timeout=(12, self.timeout), allow_redirects=False, stream=True)
                    if response.status_code in (429, 500, 502, 503, 504, 520, 521, 522, 523, 524) and attempt < 2:
                        delay = self.retry_delay(response, attempt)
                        self.on_activity({'status': 'retrying', 'url': safe_url, 'attempt': attempt + 2,
                                          'request_bytes': 0, 'http_status': response.status_code, 'retry_delay': delay})
                        self.on_event(f'Wayback HTTP {response.status_code}; повтор {attempt + 2}/3 через {delay:g} с. Адрес: {self.safe_url(url)}')
                        response.close()
                        if self.cancel.wait(delay):
                            self.check_cancel()
                        continue
                    if response.is_redirect:
                        url = urljoin(url, response.headers.get('Location', ''))
                        redirected = True
                        break
                    response.raise_for_status()
                    chunks, size = [], 0
                    last_activity = time.monotonic()
                    for chunk in response.iter_content(65536):
                        self.check_cancel()
                        size += len(chunk)
                        if size > limit:
                            raise RestorationError(f'Ресурс превышает допустимый размер загрузки. Адрес: {self.safe_url(url)}')
                        chunks.append(chunk)
                        now = time.monotonic()
                        if now - last_activity >= .35:
                            self.on_activity({'status': 'receiving', 'url': safe_url, 'attempt': attempt + 1,
                                              'request_bytes': size,
                                              'request_seconds': round(now - started, 2)})
                            last_activity = now
                    self.last_response_url = url
                    self.on_activity({'status': 'received', 'url': safe_url, 'attempt': attempt + 1,
                                      'request_bytes': size, 'request_seconds': round(time.monotonic() - started, 2)})
                    return b''.join(chunks), response.headers.get('Content-Type', '')
                except requests.RequestException as error:
                    tls = isinstance(error, requests.exceptions.SSLError)
                    retryable = (self.tls_reason(error) == 'interrupted' if tls else
                                 isinstance(error, (requests.Timeout, requests.ConnectionError, requests.exceptions.ChunkedEncodingError)))
                    if retryable and attempt < 2:
                        self.on_activity({'status': 'retrying', 'url': self.safe_url(url), 'attempt': attempt + 2,
                                          'request_bytes': 0, 'detail': self.request_error(error, url)})
                        self.on_event(f'{self.request_error(error, url)} Повтор {attempt + 2}/3.')
                        if response is not None:
                            response.close()
                        if tls:
                            # Clear interrupted pooled connections, retaining CA/proxy/header settings.
                            self.session.close()
                        if self.cancel.wait(2 ** (attempt + 1)):
                            self.check_cancel()
                        continue
                    detail = self.request_error(error, url)
                    if retryable:
                        detail += f' Попыток: {attempt + 1}/3. Повторите загрузку, когда соединение восстановится.'
                    self.on_activity({'status': 'failed', 'url': self.safe_url(url), 'attempt': attempt + 1,
                                      'request_bytes': 0, 'detail': detail})
                    raise RestorationError(detail) from error
                finally:
                    if response is not None:
                        response.close()
            if not redirected:
                break
        raise RestorationError('Слишком много перенаправлений Wayback.')

    def close(self):
        self.session.close()
