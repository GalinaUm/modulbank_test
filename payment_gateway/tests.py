import json
import threading
from unittest.mock import patch

from django.test import TestCase, TransactionTestCase

from .models import Operation, OperationEvent, SubmitIntent


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
            resp = self.client.post('/operations/race-1/submit')
            results.append(resp.status_code)

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