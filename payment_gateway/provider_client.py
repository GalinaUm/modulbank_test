import  logging
import os
import time

import requests

from .services import save_provider_payment_id

logger = logging.getLogger(__name__)

PROVIDER_URL = os.environ.get('PROVIDER_URL', 'http://localhost:8081')
MAX_RETRIES = int(os.environ.get('MAX_RETRIES', 5))
TIMEOUT = int(os.environ.get('TIMEOUT', 10))


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
                logger.info("payment accepted for %s", operation.operation_id)
                return True

            if resp.status_code == 503:
                logger.warning("provider busy for %s, retry %s", operation.operation_id, attempt)
                time.sleep(delay)
                continue

            logger.warning("unexpected status %s for %s", resp.status_code, operation.operation_id)
            return False

        except requests.RequestException:
            logger.warning("network error for %s, retry %s", operation.operation_id, attempt)
            time.sleep(delay)
            continue

    return False