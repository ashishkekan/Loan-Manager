from datetime import date
from decimal import Decimal
from unittest.mock import patch
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from loans.accounting import schedule, refresh_balance
from loans.models import Loan, LoanDisbursement
from loans.forms import LoanForm
from payments.services import process_emi_payment, process_prepayment


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class DailyScenarioTests(TestCase):
    def setUp(self):
        self.clock = patch('django.utils.timezone.localdate', return_value=date(2026, 10, 10))
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.user = User.objects.create_user('july-test', password='test-pass')
        self.staff = User.objects.create_superuser('admin-test', password='test-pass')
        self.loan = Loan.objects.create(user=self.user, loan_name='July Home Loan',
            amount=2000000, interest_rate=Decimal('7.30'), tenure_years=20,
            emi=Decimal('15869'), interest_basis='actual_365',
            start_date=date(2026, 7, 18), first_emi_date=date(2026, 9, 10))
        for when, amount, purpose in [(date(2026,7,30),29000,'insurance'),
                                      (date(2026,7,30),135742,'builder'),
                                      (date(2026,8,31),359842,'builder')]:
            LoanDisbursement.objects.create(loan=self.loan, disbursement_date=when,
                                           amount=amount, purpose=purpose)
        refresh_balance(self.loan)

    def pay(self, number=1, when=date(2026,9,10)):
        return process_emi_payment(self.loan, payment_date=when, request_key=f'july-{number}',
                                   expected_period=number, expected_amount='15869')

    def test_exact_dates_interest_and_ledger(self):
        rows = schedule(self.loan)
        # Independent expected amount: 29,000*42 + 135,742*42 + 359,842*10 principal-days.
        self.assertEqual(rows[0]['interest'], Decimal('2104.00'))
        self.assertEqual(rows[0]['principal'], Decimal('13765.00'))
        self.assertEqual(rows[0]['balance'], Decimal('510819.00'))
        self.assertEqual(rows[1]['due_date'], date(2026,10,10))
        self.assertEqual(rows[1]['interest'], Decimal('3065.00'))
        self.assertEqual(rows[1]['principal'], Decimal('12804.00'))
        self.assertEqual(rows[1]['balance'], Decimal('498015.00'))
        payment = self.pay()
        self.assertEqual(self.pay().pk, payment.pk)
        self.loan.refresh_from_db()
        self.assertEqual(self.loan.remaining_balance, Decimal('510819.00'))
        self.assertEqual(self.loan.total_interest_paid, Decimal('2104.00'))
        self.assertEqual(self.loan.remaining_sanction_amount, Decimal('1475416'))
        after = schedule(self.loan)
        self.assertEqual(after[1]['interest'], rows[1]['interest'])
        self.pay(2, date(2026,10,10))
        self.loan.refresh_from_db()
        self.assertEqual(self.loan.current_balance, Decimal('498015.00'))
        self.assertEqual(sum(p.principal_component for p in self.loan.payments.all()) + self.loan.current_balance, Decimal('524584'))

    def test_late_principal_reduces_on_actual_payment_day(self):
        self.pay(when=date(2026,9,15))
        # 5 days at 524584, then 25 days at 510819.00.
        self.assertEqual(schedule(self.loan)[1]['interest'], Decimal('3079.00'))

    def test_partial_prepayment_splits_daily_interval(self):
        self.pay()
        process_prepayment(self.loan, Decimal('10000'), date(2026,9,20), request_key='extra')
        self.assertEqual(schedule(self.loan)[1]['interest'], Decimal('3025.00'))

    def test_release_on_due_date_has_zero_interest_that_day(self):
        LoanDisbursement.objects.create(loan=self.loan, amount=100000,
                                       disbursement_date=date(2026,9,10))
        self.assertEqual(schedule(self.loan)[0]['interest'], Decimal('2104.00'))
        self.assertEqual(schedule(self.loan)[0]['balance'], Decimal('610819.00'))

    def test_full_prepayment_cannot_discard_unbilled_daily_interest(self):
        self.pay()
        with self.assertRaisesMessage(ValueError, 'settlement quote'):
            process_prepayment(self.loan, Decimal('510819.00'), date(2026,9,20), request_key='full')
        self.assertFalse(self.loan.prepayments.exists())
        process_prepayment(self.loan, Decimal('510819.00'), date(2026,9,10), request_key='same-day')
        self.loan.refresh_from_db()
        self.assertEqual(self.loan.status, 'closed')
        self.assertEqual(len(schedule(self.loan)), 1)

    def test_future_and_pending_releases_do_not_charge_earlier(self):
        LoanDisbursement.objects.create(loan=self.loan, amount=100000, disbursement_date=date(2026,10,30))
        LoanDisbursement.objects.create(loan=self.loan, amount=100000, disbursement_date=date(2026,8,1), status='pending')
        self.assertEqual(schedule(self.loan)[0]['interest'], Decimal('2104.00'))
        self.assertEqual(schedule(self.loan)[1]['interest'], Decimal('3065.00'))

    def test_bank_emi_form_and_protected_basis(self):
        data = dict(loan_name='July', loan_type='home', amount='2000000', interest_rate='7.30',
            interest_basis='actual_365', tenure_years='20', emi='15869', start_date='2026-07-18',
            first_emi_date='2026-09-10', emi_frequency='monthly')
        self.client.force_login(self.user)
        response = self.client.post(reverse('create_loan'), data)
        self.assertEqual(response.status_code, 302)
        created = Loan.objects.get(loan_name='July')
        self.assertEqual(created.emi, Decimal('15869'))
        self.pay()
        data['interest_basis'] = 'periodic'
        form = LoanForm(data, instance=self.loan, user=self.user)
        self.assertFalse(form.is_valid())
        self.assertIn('interest_basis', form.errors)

    def test_populated_routes_exports_and_access(self):
        self.pay()
        routes = ['dashboard', 'loan_list', 'create_loan', 'payment_dashboard',
            'prepayment_dashboard', 'documents_dashboard', 'notifications_dashboard',
            'support_dashboard', 'create_support_ticket', 'settings_dashboard',
            'marketplace', 'setup_profile', 'loan_compare', 'export_payment_excel',
            'download_statement', 'export_prepayment_excel', 'export_prepayment_pdf']
        for user in [self.user, self.staff]:
            self.client.force_login(user)
            for name in routes:
                with self.subTest(user=user.username, route=name):
                    self.assertEqual(self.client.get(reverse(name)).status_code, 200)
            for name in ['loan_detail', 'edit_loan', 'loan_disbursement_list',
                         'create_disbursement', 'emi_schedule', 'transaction_ledger', 'export_excel']:
                with self.subTest(user=user.username, route=name):
                    self.assertEqual(self.client.get(reverse(name, args=[self.loan.pk])).status_code, 200)
        for name in ['admin_users', 'admin_banks', 'activity_logs_dashboard', 'admin_reports']:
            self.assertEqual(self.client.get(reverse(name)).status_code, 200)
        for kind in ['loan_portfolio', 'payment_collection', 'overdue', 'user_summary', 'performance']:
            for fmt in ['csv', 'excel', 'pdf']:
                self.assertEqual(self.client.get(reverse('export_admin_report', args=[kind,fmt])).status_code, 200)
        other = User.objects.create_user('unrelated')
        self.client.force_login(other)
        self.assertEqual(self.client.get(reverse('loan_detail', args=[self.loan.pk])).status_code, 404)
