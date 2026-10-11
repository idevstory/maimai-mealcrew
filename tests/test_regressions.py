"""Regression tests for quotes, AA subsidies and revision failure boundaries."""
import contextlib
import copy
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skill/maimai-mealcrew/scripts'))
import mealcrew as M
import settlement as S
import revisions as R


def session():
    return {'session_id': 'test', 'store': 's1', 'channel': 'delivery',
            'members': [{'id': 'a'}, {'id': 'b'}],
            'items': [{'item_id': 'i1', 'product_code': 'p1', 'qty': 1,
                       'unit_price_cents': 1000, 'bearer': 'a'}],
            'public_fees': {'delivery_cents': 300}, 'subsidy': {'type': 'none'}}


def quote(s):
    q = S.build_demo_quote(s)
    q.update(source='mcp', quote_id='official-q', quote_time='2026-10-11T15:00:00+08:00')
    return q


def cli(args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = M.main(args)
    return rc, out.getvalue(), err.getvalue()


class SubsidyTests(unittest.TestCase):
    def test_aa_subsidies_cover_or_exclude_fees(self):
        for kind in ('per_person', 'whole_order'):
            for covers, expected in ((True, 1300), (False, 1000)):
                with self.subTest(kind=kind, covers=covers):
                    s = session()
                    s['settlement_mode'] = 'equal_split'
                    s['subsidy'] = {'type': kind, 'per_person_cents': 2000,
                                    'whole_order_cents': 4000, 'covers_public_fees': covers}
                    r = S.compute_settlement(s, quote(s))
                    S.require_valid_settlement(r)
                    self.assertEqual(r['totals']['subsidy_total_cents'], expected)
                    self.assertEqual(sum(m['fee_share_cents'] for m in r['members']), 300)
                    self.assertEqual(sum(m['meal_cost_cents'] for m in r['members']), 1000)

    def test_aa_odd_cents_preserve_equal_totals(self):
        s = session()
        s['settlement_mode'] = 'equal_split'
        s['items'][0]['unit_price_cents'] = 1001
        s['public_fees']['delivery_cents'] = 301
        s['subsidy'] = {'type': 'per_person', 'per_person_cents': 2000, 'covers_public_fees': False}
        r = S.compute_settlement(s, quote(s))
        S.require_valid_settlement(r)
        self.assertEqual([m['pre_subsidy_cents'] for m in r['members']], [651, 651])
        self.assertEqual(sum(m['company_bears_cents'] for m in r['members']), 1001)
        self.assertEqual(sum(m['personal_pays_cents'] for m in r['members']), 301)

    def test_aa_ineligible_member(self):
        s = session()
        s['settlement_mode'] = 'equal_split'
        s['subsidy'] = {'type': 'whole_order', 'whole_order_cents': 4000, 'eligible': ['a']}
        r = S.compute_settlement(s, quote(s))
        self.assertEqual([m['company_bears_cents'] for m in r['members']], [650, 0])


class QuoteTests(unittest.TestCase):
    def test_real_rejects_demo_and_unknown(self):
        for source in ('demo', 'unknown', None):
            s = session(); q = quote(s); q['source'] = source
            rc, out, _ = cli(['settle', '--session', json.dumps(s), '--mode', 'real', '--quote', json.dumps(q)])
            self.assertNotEqual(rc, 0); self.assertEqual(out, '')

    def test_bindings_reject_same_price_other_input(self):
        for patch in ({'store': 's2'}, {'channel': 'takeaway'}, {'session_id': 'other'}):
            s = session(); q = quote(s); s.update(patch)
            with self.assertRaises(ValueError): S.validate_quote(s, q, real=True)
        s = session(); q = quote(s); s['items'][0]['product_code'] = 'p2'
        with self.assertRaises(ValueError): S.validate_quote(s, q, real=True)

    def test_rule_only_change_can_reuse_quote(self):
        s = session(); q = quote(s)
        s['subsidy'] = {'type': 'per_person', 'per_person_cents': 100}
        s['items'][0]['bearer'] = 'b'
        S.validate_quote(s, q, real=True)

    def test_missing_and_invalid_metadata(self):
        for key in ('quote_id', 'quote_time', 'session_id', 'input_hash', 'official_payable_cents'):
            s = session(); q = quote(s); del q[key]
            with self.assertRaises(ValueError): S.validate_quote(s, q, real=True)
        s = session(); q = quote(s); q['quote_time'] = '2026-10-11T15:00:00'
        with self.assertRaises(ValueError): S.validate_quote(s, q, real=True)

    def test_official_breakdown_mismatch(self):
        s = session(); q = quote(s); q['public_fees_cents'] += 100
        with self.assertRaises(ValueError): S.validate_quote(s, q, real=True)

    def test_candidates_do_not_relabel_demo(self):
        s = session()
        rc, out, _ = cli(['candidates', '--session', json.dumps(s), '--mode', 'real', '--quote', json.dumps(S.build_demo_quote(s))])
        self.assertNotEqual(rc, 0); self.assertEqual(out, '')

    def test_valid_real_settlement(self):
        s = session()
        rc, out, _ = cli(['settle', '--session', json.dumps(s), '--mode', 'real', '--quote', json.dumps(quote(s))])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)['official_payable_cents'], 1300)


class RevisionTests(unittest.TestCase):
    def test_prepare_then_finalize(self):
        s = session(); s.update(state='CONFIRMED', confirmation={'by': 'a'})
        changes = {'update_items': {'i1': {'qty': 2}}}
        rc, prepared, _ = cli(['revise', '--session', json.dumps(s), '--changes', json.dumps(changes), '--mode', 'real', '--prepare'])
        self.assertEqual(rc, 0)
        n = json.loads(prepared)['new_session']
        self.assertNotIn('confirmation', n)
        self.assertEqual(n['revision_number'], 1)
        self.assertEqual(json.loads(prepared)['quote_request']['input_hash'], S.input_hash(n))
        rc, out, _ = cli(['revise', '--session', json.dumps(s), '--changes', json.dumps(changes), '--mode', 'real',
                          '--quote-old', json.dumps(quote(s)), '--quote-new', json.dumps(quote(n))])
        self.assertEqual(rc, 0)
        r = json.loads(out)
        self.assertEqual(r['diff']['official_total_change_cents'], 1000)
        self.assertTrue(r['diff']['reconfirm_required'])
        self.assertEqual(r['old_version']['session_snapshot']['items'][0]['qty'], 1)
        self.assertEqual(r['new_version']['session_snapshot']['items'][0]['qty'], 2)

    def test_revise_blocks_stale_quote_without_output(self):
        s = session(); q = quote(s)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'bill.json'
            rc, _, _ = cli(['revise', '--session', json.dumps(s), '--changes', '{"update_items":{"i1":{"qty":2}}}', '--mode', 'real',
                             '--quote-old', json.dumps(q), '--quote-new', json.dumps(q), '--output', str(path)])
            self.assertNotEqual(rc, 0); self.assertFalse(path.exists())

    def test_revise_blocks_reconciliation_even_with_correct_hash(self):
        s = session(); q = quote(s); q['official_payable_cents'] = 900
        rc, out, _ = cli(['revise', '--session', json.dumps(s), '--changes', '{}', '--mode', 'real',
                          '--quote-old', json.dumps(q), '--quote-new', json.dumps(q)])
        self.assertNotEqual(rc, 0); self.assertEqual(out, '')

    def test_discount_scope_change_requires_confirmation(self):
        s = session(); s['items'].append({'item_id': 'i2', 'unit_price_cents': 1000, 'qty': 1, 'bearer': 'b'})
        s.update(order_discount_cents=500, order_discount_scope=['i1'])
        n = copy.deepcopy(s); n['order_discount_scope'] = ['i2']
        o = R.make_version('old', s, quote(s), S.compute_settlement(s, quote(s)))
        v = R.make_version('new', n, quote(n), S.compute_settlement(n, quote(n)))
        d = R.diff_versions(o, v)
        self.assertEqual(d['official_total_change_cents'], 0)
        self.assertTrue(d['reconfirm_required']); self.assertEqual(len(d['per_member_changes']), 2)

    def test_version_snapshot_is_independent(self):
        s = session(); q = quote(s); r = S.compute_settlement(s, q); v = R.make_version('old', s, q, r)
        s['items'][0]['qty'] = 4; q['source'] = 'demo'; r['members'][0]['personal_pays_cents'] = 0
        self.assertEqual(v['session_snapshot']['items'][0]['qty'], 1)
        self.assertEqual(v['quote']['source'], 'mcp')
        self.assertNotEqual(v['settlement']['members'][0]['personal_pays_cents'], 0)


class ValidationTests(unittest.TestCase):
    def test_discount_overflow_rejected(self):
        s = session(); s['items'][0]['item_discount_cents'] = 1001
        with self.assertRaises(ValueError): S.compute_settlement(s, {'official_payable_cents': 300})
        s = session(); s.update(order_discount_cents=1500, order_discount_scope=['i1'])
        with self.assertRaises(ValueError): S.compute_settlement(s, {'official_payable_cents': 300})

    def test_noninteger_and_boolean_values_rejected(self):
        for key, value in (('qty', 1.9), ('qty', True), ('unit_price_cents', '1000'), ('unit_price_cents', -1)):
            s = session(); s['items'][0][key] = value
            with self.assertRaises(ValueError): S.validate_session(s)

    def test_shared_duplicates_rejected(self):
        s = session(); s['items'][0].update(bearer_type='shared', shared_members=['a', 'a', 'b'])
        with self.assertRaises(ValueError): S.validate_session(s)

    def test_proportional_float_weights(self):
        self.assertEqual(S.allocate_proportional(10, [1.0, 2.0]), [3, 7])
        with self.assertRaises(ValueError): S.allocate_proportional(10, [-1, 2])

    def test_equal_split_cannot_hide_mismatch(self):
        s = session(); s['settlement_mode'] = 'equal_split'
        r = S.compute_settlement(s, {'official_payable_cents': 900})
        with self.assertRaises(ValueError): S.require_valid_settlement(r)


class CompareTests(unittest.TestCase):
    def plans(self):
        a = session(); b = session(); b['items'][0]['unit_price_cents'] = 900
        return [{'label': 'base', 'session': a, 'quote': quote(a), 'equivalent_confirmed': True},
                {'label': 'coupon', 'session': b, 'quote': quote(b), 'equivalent_confirmed': True}]

    def test_compare_real_quotes(self):
        rc, out, _ = cli(['compare', '--plans', json.dumps(self.plans())])
        self.assertEqual(rc, 0)
        r = json.loads(out)
        self.assertEqual(r['lowest_in_compared_plans'], ['coupon'])
        self.assertEqual(r['ranking'][0]['payable_cents'], 1200)

    def test_unconfirmed_or_stale_plan_cannot_win(self):
        for change in ('unconfirmed', 'stale'):
            plans = self.plans()
            if change == 'unconfirmed': plans[1]['equivalent_confirmed'] = False
            else: plans[1]['session']['items'][0]['qty'] = 2
            rc, out, _ = cli(['compare', '--plans', json.dumps(plans)])
            self.assertNotEqual(rc, 0); self.assertEqual(out, '')

    def test_partial_failures_are_reported(self):
        plans = self.plans(); bad = copy.deepcopy(plans[0]); bad.update(label='failed', equivalent_confirmed=False)
        plans.append(bad)
        rc, out, _ = cli(['compare', '--plans', json.dumps(plans)])
        self.assertEqual(rc, 0); self.assertEqual(json.loads(out)['failed'][0]['label'], 'failed')


class OutputTests(unittest.TestCase):
    def test_json_output_flag_saves_bill(self):
        s = session()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'bill.json'
            rc, out, _ = cli(['settle', '--session', json.dumps(s), '--mode', 'real', '--quote', json.dumps(quote(s)), '--output', str(path)])
            self.assertEqual(rc, 0); self.assertEqual(out, '')
            self.assertEqual(json.loads(path.read_text())['official_payable_cents'], 1300)

    def test_render_blocks_bad_bill_and_preserves_existing_file(self):
        s = session(); r = S.compute_settlement(s, {'official_payable_cents': 900})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'bill.html'; path.write_text('existing bill')
            rc, out, _ = cli(['render', '--result', json.dumps(r), '--format', 'html', '--output', str(path)])
            self.assertNotEqual(rc, 0); self.assertEqual(path.read_text(), 'existing bill')

    def test_zero_payable_and_zero_subsidy(self):
        s = session(); s['items'][0]['item_discount_cents'] = 1000; s['public_fees'] = {}
        r = S.compute_settlement(s, quote(s)); S.require_valid_settlement(r)
        self.assertEqual(r['official_payable_cents'], 0)
        self.assertEqual(r['totals']['subsidy_total_cents'], 0)
