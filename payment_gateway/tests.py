import json
import threading
from decimal import Decimal
from unittest.mock import Mock, patch

from django.apps import apps
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase

from payment_gateway import provider_client
from .models import Operation, OperationEvent, SubmitIntent
from .provider_client import send_payment


def post_json(client, path, payload):
    return client.post(path, data=json.dumps(payload), content_type='application/json')


class HealthTests(TestCase):
    def test_health(self):
        resp = self.client.get('/health')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {'status': 'ok'})


class CreateOperationTests(TestCase):
    def setUp(self):
        self.payload = {
            'operationId': 'op-1',
            'amount': '1000.00',
            'currency': 'RUB',
            'description': 'Оплата заказа',
        }

    def test_creates_operation_with_contract_fields(self):
        resp = post_json(self.client, '/operations', self.payload)
        self.assertEqual(resp.status_code, 201)
        body = resp.json()
        self.assertEqual(body['operationId'], 'op-1')
        self.assertEqual(body['amount'], '1000.00')
        self.assertEqual(body['currency'], 'RUB')
        self.assertEqual(body['status'], 'CREATED')
        self.assertIsNone(body['providerPaymentId'])

    def test_duplicate_operation_returns_409(self):
        post_json(self.client, '/operations', self.payload)
        resp = post_json(self.client, '/operations', self.payload)
        self.assertEqual(resp.status_code, 409)

    def test_creates_initial_created_event(self):
        post_json(self.client, '/operations', self.payload)
        operation = Operation.objects.get(operation_id='op-1')
        events = OperationEvent.objects.filter(operation=operation).order_by('event_id')
        self.assertEqual(events.count(), 1)
        event = events.first()
        self.assertEqual(event.event_id, 1)
        self.assertEqual(event.event_type, 'CREATED')
        self.assertIsNone(event.from_status)
        self.assertEqual(event.to_status, 'CREATED')

    def test_missing_operation_id_returns_400(self):
        payload = dict(self.payload)
        del payload['operationId']
        resp = post_json(self.client, '/operations', payload)
        self.assertEqual(resp.status_code, 400)

    def test_invalid_amount_returns_400(self):
        for amount in ('abc', '0.00', '-5.00', '1.999'):
            payload = dict(self.payload, amount=amount)
            resp = post_json(self.client, '/operations', payload)
            self.assertEqual(resp.status_code, 400, msg=f'amount={amount}')

    def test_unsupported_currency_returns_400(self):
        payload = dict(self.payload, currency='USD')
        resp = post_json(self.client, '/operations', payload)
        self.assertEqual(resp.status_code, 400)

    def test_invalid_json_returns_400(self):
        resp = self.client.post('/operations', data='not-json', content_type='application/json')
        self.assertEqual(resp.status_code, 400)

    def test_get_operation_not_found_yet(self):
        resp = self.client.get('/operations/op-1')
        self.assertEqual(resp.status_code, 404)


class SubmitTests(TransactionTestCase):
    def setUp(self):
        self.send_payment_patch = patch('payment_gateway.provider_client.send_payment', return_value=True)
        self.mock_send_payment = self.send_payment_patch.start()
        self.addCleanup(self.send_payment_patch.stop)
        post_json(self.client, '/operations', {
            'operationId': 'sub-1',
            'amount': '100.00',
            'currency': 'RUB',
        })

    def test_first_submit_returns_202_and_creates_intent(self):
        resp = self.client.post('/operations/sub-1/submit')
        self.assertEqual(resp.status_code, 202)
        operation = Operation.objects.get(operation_id='sub-1')
        self.assertEqual(operation.status, Operation.PROCESSING)
        self.assertTrue(SubmitIntent.objects.filter(operation=operation).exists())

    def test_send_payment_called_after_commit(self):
        self.client.post('/operations/sub-1/submit')
        self.mock_send_payment.assert_called_once()

    def test_repeated_submit_returns_200_no_new_intent(self):
        first = self.client.post('/operations/sub-1/submit')
        second = self.client.post('/operations/sub-1/submit')
        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 200)
        operation = Operation.objects.get(operation_id='sub-1')
        self.assertEqual(SubmitIntent.objects.filter(operation=operation).count(), 1)

    def test_submit_unknown_operation_returns_404(self):
        resp = self.client.post('/operations/nope/submit')
        self.assertEqual(resp.status_code, 404)

    def test_submit_creates_processing_event(self):
        self.client.post('/operations/sub-1/submit')
        operation = Operation.objects.get(operation_id='sub-1')
        events = OperationEvent.objects.filter(operation=operation).order_by('event_id')
        self.assertEqual(events.count(), 2)
        self.assertEqual(events[1].event_id, 2)
        self.assertEqual(events[1].from_status, 'CREATED')
        self.assertEqual(events[1].to_status, 'PROCESSING')


class ConcurrentSubmitTests(TransactionTestCase):
    def setUp(self):
        self.send_payment_patch = patch('payment_gateway.provider_client.send_payment', return_value=True)
        self.mock_send_payment = self.send_payment_patch.start()
        self.addCleanup(self.send_payment_patch.stop)

    def test_concurrent_submits_create_single_intent_and_payment(self):
        post_json(self.client, '/operations', {
            'operationId': 'race-1',
            'amount': '42.00',
            'currency': 'RUB',
        })

        results = []
        barrier = threading.Barrier(5)

        def submit():
            barrier.wait()
            try:
                resp = self.client.post('/operations/race-1/submit')
                results.append(resp.status_code)
            finally:
                close_old_connections()

        threads = [threading.Thread(target=submit) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(sorted(results), [200, 200, 200, 200, 202])
        operation = Operation.objects.get(operation_id='race-1')
        self.assertEqual(operation.status, Operation.PROCESSING)
        self.assertEqual(SubmitIntent.objects.filter(operation=operation).count(), 1)
        self.assertEqual(self.mock_send_payment.call_count, 1)


class GetOperationTests(TestCase):
    def setUp(self):
        post_json(self.client, '/operations', {
            'operationId': 'get-1',
            'amount': '5.00',
            'currency': 'RUB',
        })

    def test_get_operation_returns_state(self):
        resp = self.client.get('/operations/get-1')
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body['operationId'], 'get-1')
        self.assertEqual(body['status'], 'CREATED')
        self.assertEqual(body['amount'], '5.00')

    def test_get_events_format(self):
        resp = self.client.get('/operations/get-1/events')
        self.assertEqual(resp.status_code, 200)
        events = resp.json()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['eventId'], 1)
        self.assertEqual(events[0]['type'], 'CREATED')
        self.assertIsNone(events[0]['fromStatus'])
        self.assertEqual(events[0]['toStatus'], 'CREATED')
        self.assertTrue(events[0]['occurredAt'].endswith('Z'))

    def test_get_missing_operation_returns_404(self):
        self.assertEqual(self.client.get('/operations/nope').status_code, 404)
        self.assertEqual(self.client.get('/operations/nope/events').status_code, 404)


class ReceiptTests(TransactionTestCase):
    def setUp(self):
        self.send_payment_patch = patch('payment_gateway.provider_client.send_payment', return_value=True)
        self.mock_send_payment = self.send_payment_patch.start()
        self.addCleanup(self.send_payment_patch.stop)
        post_json(self.client, '/operations', {
            'operationId': 'rec-1',
            'amount': '77.00',
            'currency': 'RUB',
        })
        self.client.post('/operations/rec-1/submit')

    def receipt(self, provider_payment_id='pid-1', result='COMPLETED', message='done'):
        return post_json(self.client, '/receipts', {
            'operationId': 'rec-1',
            'providerPaymentId': provider_payment_id,
            'result': result,
            'message': message,
            'occurredAt': '2026-08-15T12:00:00Z',
        })

    def test_receipt_transitions_to_final_status(self):
        resp = self.receipt()
        self.assertEqual(resp.status_code, 204)
        operation = Operation.objects.get(operation_id='rec-1')
        self.assertEqual(operation.status, Operation.COMPLETED)
        self.assertEqual(operation.provider_payment_id, 'pid-1')

    def test_receipt_before_provider_response_sets_provider_payment_id(self):
        self.receipt(provider_payment_id='early-pid')
        operation = Operation.objects.get(operation_id='rec-1')
        self.assertEqual(operation.status, Operation.COMPLETED)
        self.assertEqual(operation.provider_payment_id, 'early-pid')

    def test_duplicate_receipt_does_not_create_new_event(self):
        self.receipt()
        before = OperationEvent.objects.filter(operation__operation_id='rec-1').count()
        resp = self.receipt()
        after = OperationEvent.objects.filter(operation__operation_id='rec-1').count()
        self.assertEqual(resp.status_code, 204)
        self.assertEqual(after, before)

    def test_late_conflicting_receipt_is_ignored(self):
        self.receipt(result='COMPLETED')
        resp = self.receipt(result='REJECTED')
        self.assertEqual(resp.status_code, 204)
        operation = Operation.objects.get(operation_id='rec-1')
        self.assertEqual(operation.status, Operation.COMPLETED)
        self.assertEqual(
            OperationEvent.objects.filter(
                operation__operation_id='rec-1', event_type='IGNORED').count(),
            1,
        )

    def test_mismatched_provider_payment_id_returns_409(self):
        self.receipt()
        resp = self.receipt(provider_payment_id='other-pid')
        self.assertEqual(resp.status_code, 409)

    def test_invalid_result_returns_400(self):
        resp = self.receipt(result='NEVER')
        self.assertEqual(resp.status_code, 400)

    def test_unknown_operation_returns_404(self):
        resp = post_json(self.client, '/receipts', {
            'operationId': 'nope',
            'providerPaymentId': 'pid-1',
            'result': 'COMPLETED',
        })
        self.assertEqual(resp.status_code, 404)

    def test_missing_fields_returns_400(self):
        resp = post_json(self.client, '/receipts', {
            'operationId': 'rec-1',
            'providerPaymentId': 'pid-1',
        })
        self.assertEqual(resp.status_code, 400)

    def test_completed_and_rejected_pass(self):
        self.receipt(result='REJECTED')
        operation = Operation.objects.get(operation_id='rec-1')
        self.assertEqual(operation.status, Operation.REJECTED)


class RecoveryTests(TransactionTestCase):
    def setUp(self):
        self.send_payment_patch = patch('payment_gateway.provider_client.send_payment', return_value=True)
        self.mock_send_payment = self.send_payment_patch.start()
        self.addCleanup(self.send_payment_patch.stop)
        self.config = apps.get_app_config('payment_gateway')

    def test_resumes_processing_operations(self):
        post_json(self.client, '/operations', {
            'operationId': 'recover-1',
            'amount': '50.00',
            'currency': 'RUB',
        })
        Operation.objects.create(
            operation_id='recover-2',
            amount=Decimal('33.00'),
            currency='RUB',
            status=Operation.PROCESSING,
        )
        op1 = Operation.objects.get(operation_id='recover-1')
        op1.status = Operation.PROCESSING
        op1.save(update_fields=['status'])
        SubmitIntent.objects.create(operation=op1)

        self.config._resume_pending()

        self.assertEqual(self.mock_send_payment.call_count, 2)
        for call in self.mock_send_payment.call_args_list:
            self.assertIn(call.args[0].operation_id, ('recover-1', 'recover-2'))

    def test_resume_skips_when_nothing_pending(self):
        self.config._resume_pending()
        self.mock_send_payment.assert_not_called()


class ProviderClientTests(TestCase):
    def setUp(self):
        self.operation = Operation.objects.create(
            operation_id='pc-1',
            amount=Decimal('10.00'),
            currency='RUB',
        )
        self.sleep_patch = patch('payment_gateway.provider_client.time.sleep')
        self.sleep_patch.start()
        self.addCleanup(self.sleep_patch.stop)
        self.max_retries_patch = patch.object(provider_client, 'MAX_RETRIES', 3)
        self.max_retries_patch.start()
        self.addCleanup(self.max_retries_patch.stop)
        self.post_patch = patch('payment_gateway.provider_client.requests.post')
        self.mock_post = self.post_patch.start()
        self.addCleanup(self.post_patch.stop)

    def ok_response(self, provider_payment_id='pid-1'):
        response = Mock(status_code=202)
        response.json.return_value = {'providerPaymentId': provider_payment_id, 'status': 'ACCEPTED'}
        return response

    def test_sends_expected_headers_and_body(self):
        self.mock_post.return_value = self.ok_response()
        self.assertTrue(send_payment(self.operation))
        _, kwargs = self.mock_post.call_args
        self.assertEqual(kwargs['headers']['Idempotency-Key'], 'pc-1')
        self.assertEqual(kwargs['headers']['X-Correlation-ID'], 'pc-1')
        self.assertEqual(kwargs['json']['operationId'], 'pc-1')
        self.assertEqual(kwargs['json']['amount'], '10.00')
        self.assertEqual(kwargs['json']['currency'], 'RUB')
        self.assertEqual(kwargs['timeout'], 10)
        self.operation.refresh_from_db()
        self.assertEqual(self.operation.provider_payment_id, 'pid-1')

    def test_retries_on_503_with_same_idempotency_key(self):
        self.mock_post.side_effect = [
            Mock(status_code=503),
            Mock(status_code=503),
            self.ok_response('pid-2'),
        ]
        self.assertTrue(send_payment(self.operation))
        self.assertEqual(self.mock_post.call_count, 3)
        for call in self.mock_post.call_args_list:
            self.assertEqual(call.kwargs['headers']['Idempotency-Key'], 'pc-1')

    def test_retries_on_network_error(self):
        self.mock_post.side_effect = [
            provider_client.requests.ConnectionError('boom'),
            self.ok_response('pid-3'),
        ]
        self.assertTrue(send_payment(self.operation))
        self.assertEqual(self.mock_post.call_count, 2)
        self.operation.refresh_from_db()
        self.assertEqual(self.operation.provider_payment_id, 'pid-3')

    def test_exhausted_retries_keep_status_unchanged(self):
        self.mock_post.side_effect = provider_client.requests.ConnectionError('boom')
        self.assertFalse(send_payment(self.operation))
        self.assertEqual(self.mock_post.call_count, 3)
        self.operation.refresh_from_db()
        self.assertIsNone(self.operation.provider_payment_id)
        self.assertEqual(self.operation.status, Operation.CREATED)


class MetricsTests(TestCase):
    def test_metrics_returns_shape_and_database_counts(self):
        Operation.objects.create(
            operation_id='m-1', amount=Decimal('1.00'), currency='RUB', status=Operation.PROCESSING
        )
        Operation.objects.create(
            operation_id='m-2', amount=Decimal('2.00'), currency='RUB',
            status=Operation.COMPLETED, provider_payment_id='pid-m',
        )
        Operation.objects.create(
            operation_id='m-3', amount=Decimal('3.00'), currency='RUB', status=Operation.REJECTED
        )

        response = self.client.get('/metrics')
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        for key in ('operationsProcessing', 'operationsCompleted', 'operationsRejected',
                    'retries', 'paymentsAccepted'):
            self.assertIn(key, payload)
        self.assertEqual(payload['operationsProcessing'], 1)
        self.assertEqual(payload['operationsCompleted'], 1)
        self.assertEqual(payload['operationsRejected'], 1)