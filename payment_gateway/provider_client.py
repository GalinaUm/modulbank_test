import logging
import os
import time
from threading import Lock

import requests

from .services import save_provider_payment_id

logger = logging.getLogger(__name__)

PROVIDER_URL = os.environ.get('PROVIDER_URL', 'http://localhost:8081')
MAX_RETRIES = int(os.environ.get('MAX_RETRIES', 5))
TIMEOUT = int(os.environ.get('TIMEOUT', 10))

_retry_count = 0
_accept_count = 0
_counters_lock = Lock()


def get_provider_counters():
    with _counters_lock:
        return {'retries': _retry_count, 'accepted': _accept_count}


def _record_retry():
    global _retry_count
    with _counters_lock:
        _retry_count += 1


def _record_accept():
    global _accept_count
    with _counters_lock:
        _accept_count += 1


def send_payment(operation):
    headers = {
        'Idempotency-Key': operation.operation_id,
        'X-Correlation-ID': operation.operation_id,
        'Content-Type': 'application/json',
    }
    body = {
        'operationId': operation.operation_id,
        'amount': str(operation.amount),
        'currency': operation.currency,
    }

    for attempt in range(1, MAX_RETRIES + 1):
        delay = min(2 ** (attempt - 1), 30)
        try:
            resp = requests.post(
                f"{PROVIDER_URL}/payments", json=body, headers=headers, timeout=TIMEOUT
            )
            if resp.status_code == 202:
                data = resp.json()
                save_provider_payment_id(operation, data['providerPaymentId'])
                _record_accept()
                logger.info("payment accepted for %s", operation.operation_id)
                return True

            if resp.status_code == 503:
                _record_retry()
                logger.warning("provider busy for %s, retry %s", operation.operation_id, attempt)
                time.sleep(delay)
                continue

            logger.warning("unexpected status %s for %s", resp.status_code, operation.operation_id)
            return False

        except requests.RequestException:
            _record_retry()
            logger.warning("network error for %s, retry %s", operation.operation_id, attempt)
            time.sleep(delay)
            continue

    return False