"""Local protocol regressions, not an end-to-end legal acceptance score."""
import copy
import unittest
from fractions import Fraction

from app.answer_pipeline import DRAFT_SYSTEM, apply_review, calculation_error_code, compute, source_numbers


class CalculationDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.packet = dict(
            question='我跟公司签了两年的劳动合同，约定六个月试用期，已经干满六个月。转正后的月工资是8000元。',
            sources=[
                dict(id='E1', title='测试片段：期限', source_rank=1,
                     text='劳动合同期限一年以上不满三年的，试用期不得超过二个月。'),
                dict(id='E2', title='测试片段：计算', source_rank=2,
                     text='违法约定的试用期已经履行的，以试用期满月工资为标准，按已经履行的超过法定试用期的期间向劳动者支付赔偿金。'),
            ])
        self.plan = dict(inputs=[
            dict(name='actual_months', label='已履行期间', value='6', unit='月', source='Q', quote='已经干满六个月'),
            dict(name='lawful_months', label='法定最长期限', value='2', unit='月', source='E1', quote='试用期不得超过二个月'),
            dict(name='monthly_salary', label='期满月工资', value='8000', unit='元/月', source='Q', quote='月工资是8000元'),
        ], steps=[
            dict(name='excess_months', label='超过法定期限', expression='max(actual_months-lawful_months,0)', unit='月', citations=['E1', 'E2']),
            dict(name='compensation', label='赔偿金额', expression='excess_months*monthly_salary', unit='元', citations=['E2']),
        ], outputs=['compensation'])
        self.draft = dict(answers=[], gaps=[], calculations=self.plan)
        self.review = dict(claims=[], calculation=dict(supported=True, reason='测试中假定独立支持检查通过'), missing=[])

    def test_chinese_source_quantities_compute(self):
        result = compute(self.plan, self.packet)
        self.assertEqual(result['outputs'][0]['value'], '32000.00')
        self.assertEqual(result['outputs'][0]['exact_value'], '32000')
        # Each retained value is still bound to its original source, not a guessed rule.
        self.assertEqual(result['inputs'][1]['quote'], self.packet['sources'][0]['text'])

    def test_changed_fact_uses_new_source(self):
        self.packet['question'] = self.packet['question'].replace('六个月', '五个月')
        self.plan['inputs'][0].update(value='5', quote='已经干满五个月')
        self.assertEqual(compute(self.plan, self.packet)['outputs'][0]['value'], '24000.00')
        self.plan['inputs'][0]['value'] = '6'
        with self.assertRaisesRegex(ValueError, 'numeric source mismatch'):
            compute(self.plan, self.packet)

    def test_rule_absence_still_rejected(self):
        self.packet['sources'][0]['text'] = '期限按有关规定确定。'
        with self.assertRaisesRegex(ValueError, 'numeric source mismatch'):
            compute(self.plan, self.packet)

    def test_model_rejection_still_hides_arithmetic(self):
        self.review['calculation'].update(supported=False, reason='不适用此条规则')
        result = apply_review(self.draft, self.review, self.packet)
        self.assertIsNone(result['calculations'])
        self.assertEqual(result['status'], 'insufficient')

    def test_chinese_quantities_are_exact(self):
        values = source_numbers('两年，六个月，三十日，一百二十三元，一万二千三百四十五元，百分之二十，三分之二。')
        self.assertTrue({Fraction(2), Fraction(6), Fraction(30), Fraction(123), Fraction(12345), Fraction(1,5), Fraction(2,3)} <= values)

    def test_ordinary_words_are_not_numeric_quantities(self):
        self.assertFalse(source_numbers('一般情况，一旦发生，千方百计，万一如此。'))

    def test_ambiguous_shorthand_not_silently_changed(self):
        for text in ('一百二元', '一万二元', '两三个月', '二三个月', '十二三天'):
            self.assertFalse(source_numbers(text), text)
        self.assertIn(Fraction(2023), source_numbers('二〇二三年'))
        self.assertIn(Fraction(102), source_numbers('一百零二元'))
        self.assertIn(Fraction(10002), source_numbers('一万零二元'))
        self.assertIn(Fraction(1,5), source_numbers('二十%'))

    def test_safe_diagnostic_reports_actual_failure_class(self):
        for mutation,code in (
            (lambda plan:plan['inputs'][0].update(value='987654'), 'source_numeric_mismatch'),
            (lambda plan:plan['inputs'][0].update(source='E99'), 'source_id_mismatch'),
            (lambda plan:plan['steps'][0].update(expression='absent+1'), 'undefined_variable'),
            (lambda plan:plan['steps'][0].update(expression='actual_months/0'), 'division_by_zero'),
            (lambda plan:plan['steps'][0].update(expression='actual_months+'), 'expression_syntax'),
            (lambda plan:plan['steps'][0].update(citations=['fake-secret-payload']), 'citation_id_mismatch'),
        ):
            draft=copy.deepcopy(self.draft);mutation(draft['calculations'])
            result=apply_review(draft,self.review,self.packet,coverage_only_gaps=True)
            self.assertEqual(result['support_check']['calculation_validation'],dict(status='failed',code=code))
            self.assertIsNone(result['calculations'])
            self.assertTrue(result['support_check']['calculation_supported'])
        self.assertEqual(calculation_error_code(ValueError('arbitrary secret string')),'invalid_plan')

    def test_diagnostics_distinguish_arithmetic_from_review(self):
        result=apply_review(self.draft,self.review,self.packet)
        self.assertEqual(result['support_check']['calculation_validation'],dict(status='passed',code=None))
        self.review['calculation']['supported']=False
        result=apply_review(self.draft,self.review,self.packet)
        self.assertEqual(result['support_check']['calculation_validation'],dict(status='not_run',code='semantic_review_rejected'))
        self.draft['calculations']=None
        result=apply_review(self.draft,self.review,self.packet)
        self.assertEqual(result['support_check']['calculation_validation'],dict(status='not_requested',code=None))

    def test_prompt_example_uses_defined_inputs(self):
        # The old example was a+b with only a defined; catch that contract defect.
        self.assertNotIn('"expression":"a+b"',DRAFT_SYSTEM)
        self.assertIn('citations不能写步骤name',DRAFT_SYSTEM)


if __name__ == '__main__':
    unittest.main()
