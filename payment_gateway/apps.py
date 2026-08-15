import logging
import os
import threading
import time

from django.apps import AppConfig
from django.db import connection

import payment_gateway

logger = logging.getLogger(__name__)


class PaymentGatewayConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'payment_gateway'

    def ready(self):
        if not os.environ.get("RUN_MAIN"):
            return
        threading.Thread(target=self._resume_pending, daemon=True).start()

    def _resume_pending(self):
        from .models import Operation

        for _ in range(50):
            try:
                connection.ensure_connection()
                break
            except Exception:
                time.sleep(0.5)
        else:
            return

        pending = []
        for _ in range(50):
            try:
                pending = list(Operation.objects.filter(status=Operation.PROCESSING))
                break
            except Exception:
                pending = None
                time.sleep(0.5)
        if not pending:
            return

        for operation in pending:
            logger.info("resuming payment for %s", operation.operation_id)
            try:
                self._send(operation)
            except Exception:
                logger.exception("resume failed for %s", operation.operation_id)

    def _send(self, operation):
        from .provider_client import send_payment
        send_payment(operation)
