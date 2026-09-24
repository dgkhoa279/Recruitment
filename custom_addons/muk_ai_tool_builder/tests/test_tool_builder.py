import json
from unittest.mock import MagicMock, patch

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests.common import TransactionCase, tagged
from odoo.tools import html2plaintext

from ..models.authoring_prompt import build_authoring_inputs
from ..models.generated_tool import _GeneratedScriptContext
from ..models.human_gate import AI_DISPATCH
from ..models.intent_routing import ROUTING_SCHEMA_NAME, decode_routing


@tagged('post_install', '-at_install', 'muk_ai_tool_builder')
class TestAIToolBuilder(TransactionCase):
    """Current authoring workflow, permissions and real isolated worker."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = cls.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Builder regression interviewer', 'login': 'builder.regression.interviewer',
            'groups_id': [(6, 0, [cls.env.ref('base.group_user').id,
                                 cls.env.ref('recruitment.group_recruitment_interviewer').id])],
        })
        cls.admin = cls.env.ref('base.user_admin')
        cls.skill = cls.env.ref('muk_ai_tool_builder.skill_recruitment_interview_tool_builder')
        cls.agent = cls.env['muk_ai.agent'].create({'name': 'Builder regression'})
        cls.tools = cls.env['muk_ai.generated.tool']

    def _session(self, user=None, model='recruitment.job.position', request='Tạo công cụ cho vị trí tuyển dụng'):
        return self.env['muk_ai.session'].create({
            'name': 'Regression', 'user_id': (user or self.admin).id, 'agent_id': self.agent.id,
            'turn_seq': 1, 'state': 'running',
            'view_context': {'model': model, 'kind': 'list', 'view_type': 'list'},
            'conversation': [{'role': 'user', 'content': [{'type': 'input_text', 'text': request}]}],
        }).with_user(user or self.admin)

    def _call(self, session, name, **arguments):
        result, ok = session._dispatch_tool_call(name, arguments, 'regression_call')
        return json.loads(result) if isinstance(result, str) else result, ok

    def _prepare(self, session, operations=None, read_fields=None, write_fields=None, uses_ai=False):
        result, ok = self._call(session, 'route_tool_request', intent='create', summary='Tạo công cụ')
        self.assertTrue(ok, result)
        result, ok = self._call(
            session, 'inspect_authorized_model',
            model=session.view_context['model'], required_access=['read'],
            selection_reason='The original request explicitly targets this business entity.',
            entity_label=session.view_context['model'],
        )
        self.assertTrue(ok, result)
        result, ok = self._call(session, 'define_generated_tool_task', contract={
            'summary': 'Công cụ kiểm thử', 'target': 'Đối tượng trên màn hình',
            'expected_result': 'Kết quả kiểm thử', 'uses_ai': uses_ai,
            'output_language': 'Vietnamese',
            'content_requirements': ['Dùng văn phong phù hợp với yêu cầu.'] if uses_ai else [],
            'models': [{'operations': operations or ['read'],
                        'read_fields': read_fields or [], 'write_fields': write_fields or []}],
        })
        self.assertTrue(ok, result)
        return session._tool_intent()['tool_name']

    def _propose(self, session, source, **extra):
        result, ok = self._call(session, 'propose_generated_tool',
                                description='Công cụ kiểm thử', source_code=source, **extra)
        self.assertTrue(ok, result)
        self.assertEqual(result['status'], 'active', result)
        return self.tools.browse(result['generated_tool_id'])

    def _preview(self, tool, user=None, **kwargs):
        wizard = self.env['muk_ai.generated.tool.execution'].with_user(user or self.admin)._create_for_tool(tool)
        wizard = wizard.with_user(user or self.admin)
        wizard.write({'task_reviewed': True, **kwargs})
        wizard.action_preview()
        return wizard

    def test_create_count_and_run_real_worker(self):
        session = self._session(self.user, 'recruitment.interview')
        name = self._prepare(session)
        self.assertTrue(name.startswith('generated_tool_'))
        tool = self._propose(session, 'result = {"total": ctx.search_count("recruitment.interview", [])}')
        self.assertEqual(tool.name, name)
        self.assertEqual(len(tool.revision_ids), 1)
        wizard = self._preview(tool, self.user)
        self.assertEqual(wizard.state, 'done', wizard.result_text)
        self.assertEqual(wizard.preview_data['preview_result']['total'],
                         self.env['recruitment.interview'].with_user(self.user).search_count([]))
        self.assertFalse(wizard.preview_data['effects'])

    def test_task_reuses_model_and_rechecks_write(self):
        session = self._session()
        self._prepare(session, ['read', 'write'], ['name'], ['description'], True)
        self.assertEqual(session.tool_builder_task_contract['models'][0]['model'], 'recruitment.job.position')
        self.assertEqual(session._tool_builder_next_step(), 'propose_generated_tool')
        schema = next(item for item in session._get_tool_schema() if item['name'] == 'propose_generated_tool')
        self.assertNotIn('name', schema['parameters']['properties'])
        self.assertNotIn('skill_name', schema['parameters']['properties'])

    def test_clear_scoped_request_does_not_offer_premature_ask_user(self):
        session = self._session(request=(
            'Tạo tool viết mô tả cho job position có description trống.'
        ))
        result, ok = self._call(session, 'route_tool_request', intent='create',
                                summary='Viết mô tả cho vị trí thiếu mô tả')
        self.assertTrue(ok, result)
        self.assertEqual(session._tool_builder_next_step(), 'inspect_authorized_model')
        self.assertEqual(
            [item['name'] for item in session._get_tool_schema()],
            ['inspect_authorized_model'],
        )

    def test_requested_entity_ranks_before_current_view_model(self):
        session = self._session(
            model='recruitment.application',
            request='Create a reusable tool that counts recruitment candidates.',
        )
        result, ok = self._call(
            session, 'route_tool_request', intent='create',
            summary='Create a tool that counts candidates',
        )
        self.assertTrue(ok, result)
        candidates = result['context_scope']['skills'][0]['models']
        self.assertEqual(candidates[0]['model'], 'recruitment.candidate')
        self.assertTrue(candidates[0]['semantic_signals'])
        self.assertFalse(candidates[0]['is_current_view_model'])
        schema = next(
            item for item in session._get_tool_schema()
            if item['name'] == 'inspect_authorized_model'
        )
        self.assertEqual(
            schema['parameters']['properties']['model']['enum'][0],
            'recruitment.candidate',
        )
        self.assertIn('selection_reason', schema['parameters']['required'])
        self.assertIn('entity_label', schema['parameters']['required'])

    def test_semantic_rank_keeps_vietnamese_accents(self):
        ranked = self.env['muk_mcp.mixin']._rank_scoped_models([
            {
                'model': 'recruitment.application', 'label': 'Đơn ứng tuyển',
                'label_en': 'Recruitment Application', 'navigation': [],
                'is_current_view_model': True,
            },
            {
                'model': 'recruitment.candidate', 'label': 'Ứng cử viên',
                'label_en': 'Recruitment Candidate', 'navigation': [],
                'is_current_view_model': False,
            },
        ], 'Tạo tool đếm số lượng ứng cử viên')
        self.assertEqual(ranked[0]['model'], 'recruitment.candidate')
        self.assertGreater(ranked[0]['semantic_score'], ranked[1]['semantic_score'])

    def test_current_view_is_not_a_valid_selection_reason(self):
        session = self._session(
            model='recruitment.application',
            request='Create a reusable tool that counts candidates.',
        )
        result, ok = self._call(
            session, 'route_tool_request', intent='create',
            summary='Create a tool that counts candidates',
        )
        self.assertTrue(ok, result)
        result, ok = self._call(
            session, 'inspect_authorized_model',
            model='recruitment.application', required_access=['read'],
            selection_reason='It is the currently open screen.',
            entity_label='Candidate',
        )
        self.assertFalse(ok)
        self.assertEqual(result['required_next_tool'], 'inspect_authorized_model')
        self.assertEqual(result['rejection_reason'], 'translated_entity_mismatch')
        self.assertEqual(result['best_matching_models'], ['recruitment.candidate'])
        self.assertEqual(
            result['selection_policy'],
            'translated_user_entity_before_current_view',
        )

    def test_candidate_request_from_application_view_binds_candidate_model(self):
        session = self._session(
            model='recruitment.application',
            request='Hãy tạo tool đếm số lượng ứng cử viên trong hệ thống.',
        )
        result, ok = self._call(
            session, 'route_tool_request', intent='create',
            summary='Tạo tool đếm số lượng ứng cử viên',
        )
        self.assertTrue(ok, result)
        result, ok = self._call(
            session, 'inspect_authorized_model',
            model='recruitment.candidate', required_access=['read'],
            entity_label='Candidate',
            selection_reason=(
                'Ứng cử viên means Candidate, matching Recruitment Candidate; '
                'Application is only the current screen.'
            ),
        )
        self.assertTrue(ok, result)
        result, ok = self._call(
            session, 'define_generated_tool_task', contract={
                'summary': 'Đếm ứng cử viên', 'target': 'Ứng cử viên',
                'expected_result': 'Tổng số ứng cử viên', 'uses_ai': False,
                'output_language': 'Vietnamese', 'content_requirements': [],
                'models': [{
                    'operations': ['read'], 'read_fields': [], 'write_fields': [],
                }],
            },
        )
        self.assertTrue(ok, result)
        self.assertEqual(
            session.tool_builder_task_contract['models'][0]['model'],
            'recruitment.candidate',
        )

    def test_completed_tool_uses_deterministic_summary_without_question(self):
        session = self._session(
            request='Hãy tạo tool đếm số lượng vị trí tuyển dụng.',
        )
        self._prepare(session)
        tool = self._propose(
            session,
            'result = {"total": ctx.search_count("recruitment.job.position", [])}',
        )
        self.assertEqual(session._tool_builder_next_step(), 'summarize_result')
        provider = MagicMock()
        provider.name = 'ollama'
        payload = session._stream_provider_round(
            provider, [], 'qwen2.5-coder:7b', session.agent_id, cache={}
        )
        provider._request_responses.assert_not_called()
        self.assertIn(tool.name, payload['text'])
        self.assertIn('recruitment.job.position', payload['text'])
        self.assertNotIn('?', payload['text'])
        self.assertFalse(payload['tool_calls'])

    def test_routing_defaults_omitted_empty_fields(self):
        name, arguments = decode_routing(json.dumps({
            'intent': 'create',
            'summary': 'Tạo công cụ thêm 10 ứng cử viên mẫu',
        }))
        self.assertEqual(name, 'route_tool_request')
        self.assertEqual(arguments, {
            'intent': 'create',
            'summary': 'Tạo công cụ thêm 10 ứng cử viên mẫu',
        })

    def test_routing_still_requires_question_for_clarify(self):
        with self.assertRaisesRegex(ValueError, 'clarification question'):
            decode_routing(json.dumps({
                'intent': 'clarify',
                'summary': 'Yêu cầu chưa rõ',
            }))

    def test_qwen_routing_uses_constrained_json_and_normalizes_empty_fields(self):
        session = self._session(request='Hãy tạo công cụ thêm 10 ứng cử viên mẫu')
        provider = MagicMock()
        provider.name = 'ollama'
        provider._resolve_model_name.return_value = 'qwen2.5:14b'
        provider._request_responses.return_value = {
            'text': json.dumps({
                'intent': 'create',
                'summary': 'Tạo công cụ thêm 10 ứng cử viên mẫu',
            }),
            'usage': {'output_tokens': 20},
        }
        with patch.object(type(session), '_check_cancelled'), \
             patch.object(type(session), '_on_stream_delta'), \
             patch.object(type(session), '_flush_stream_buffer'):
            payload = session._stream_provider_round(
                provider, [], 'qwen2.5:14b', session.agent_id, cache={},
            )
        request = provider._request_responses.call_args.kwargs
        self.assertEqual(request['tools_schema'], [])
        self.assertEqual(request['text_schema']['name'], ROUTING_SCHEMA_NAME)
        self.assertEqual(payload['tool_calls'][0]['name'], 'route_tool_request')
        self.assertEqual(payload['tool_calls'][0]['arguments']['intent'], 'create')
        self.assertEqual(
            session._latest_counted_user_entry()['_tool_builder_routing_failures'],
            0,
        )

    def test_settings_scope_is_unavailable_and_answer_does_not_replace_request(self):
        request = 'Tôi cần bạn tạo một tool viết mô tả cho job position có description trống.'
        session = self._session(model='res.config.settings', request=request)
        result, ok = self._call(session, 'route_tool_request', intent='create', summary=request)
        self.assertTrue(ok, result)
        self.assertEqual(result['context_scope']['status'], 'unavailable')
        self.assertEqual(session._tool_builder_next_step(), 'ask_user')
        self.assertEqual([item['name'] for item in session._get_tool_schema()], ['ask_user'])
        continuation = [{'type': 'function_call_output', 'call_id': 'scope_question',
                         'output': {'status': 'answered', 'question': 'Phạm vi?',
                                    'answer': 'Tất cả vị trí công việc'}}]
        with patch.object(type(session), '_trigger_worker'), patch.object(type(session), '_publish_event'):
            session._resume_turn(continuation, event={
                'kind': 'answer', 'question': 'Phạm vi?', 'answer': 'Tất cả vị trí công việc'
            })
        self.assertEqual(session._user_entry_text(session._latest_counted_user_entry()), request)
        self.assertEqual(session._tool_intent()['intent'], 'create')
        self.assertIn('Tất cả vị trí công việc',
                      session._latest_counted_user_entry()['_tool_builder_clarifications'])
        self.assertEqual(session._tool_builder_next_step(), 'stop_and_report')
        self.assertFalse(session._get_tool_schema())

    def test_malformed_structured_output_retries_once_then_stops(self):
        session = self._session()
        self._prepare(session)
        provider = MagicMock()
        provider.name = 'ollama'
        provider._request_responses.return_value = {
            'text': '{"tool":"propose_generated_tool","arguments":',
            'usage': {'output_tokens': 2048},
        }
        with patch.object(type(session), '_check_cancelled'), \
             patch.object(type(session), '_on_stream_delta'), \
             patch.object(type(session), '_flush_stream_buffer'):
            first = session._stream_tool_builder_step(
                provider, session._get_tool_schema(), 'qwen3:1.7b', {}, None
            )
            self.assertEqual(first['tool_calls'][0]['name'], 'propose_generated_tool')
            self.assertIn('structured_output_invalid', first['tool_calls'][0]['_parse_error'])
            second = session._stream_tool_builder_step(
                provider, session._get_tool_schema(), 'qwen3:1.7b', {}, None
            )
        self.assertTrue(second['tool_calls'][0]['_parse_error'])
        self.assertEqual(session._tool_builder_next_step(), 'stop_and_report')
        self.assertFalse(session._get_tool_schema())

    def test_gemini_structured_source_accepts_literal_newline(self):
        session = self._session()
        self._prepare(session)
        provider = MagicMock()
        provider.name = 'google'
        provider._request_responses.return_value = {
            'text': (
                '{"tool":"propose_generated_tool","arguments":'
                '{"description":"Count records","source_code":'
                '"result = {\n\\"total\\": 1}"}}'
            ),
            'usage': {'output_tokens': 40},
        }
        with patch.object(type(session), '_check_cancelled'), \
             patch.object(type(session), '_on_stream_delta'), \
             patch.object(type(session), '_flush_stream_buffer'):
            payload = session._stream_provider_round(
                provider, session._get_tool_schema(), 'gemini-test',
                session.agent_id, cache={},
            )
        self.assertEqual(
            payload['tool_calls'][0]['name'],
            'propose_generated_tool',
        )
        self.assertEqual(
            payload['tool_calls'][0]['arguments']['source_code'],
            'result = {\n"total": 1}',
        )
        self.assertFalse(payload['tool_calls'][0]['_parse_error'])

    def test_count_cannot_replace_description_task(self):
        session = self._session()
        self._prepare(session, ['read', 'write'], ['name'], ['description'], True)
        result, ok = self._call(session, 'propose_generated_tool', description='Wrong implementation',
                                source_code='result = ctx.search_count("recruitment.job.position", [])')
        self.assertFalse(ok)
        self.assertEqual(result['error_kind'], 'validation')
        self.assertFalse(self.tools.search([('source_session_id', '=', session.id)]))

    def test_wrong_model_is_not_silently_substituted(self):
        session = self._session()
        self._prepare(session)
        result, ok = self._call(session, 'propose_generated_tool', description='Wrong model',
                                source_code='result = ctx.search_count("hr.job", [])')
        self.assertFalse(ok)
        self.assertEqual(result['error_kind'], 'validation')

    def test_interviewer_cannot_read_offer(self):
        manifest = {'models': [{'model': 'recruitment.offer', 'operations': ['read'],
                                'read_fields': [], 'write_fields': []}], 'uses_ai': False}
        with self.assertRaises(AccessError):
            self.tools._check_script_access(self.user, self.skill, manifest)

    def test_run_rechecks_revoked_access(self):
        session = self._session(self.user, 'recruitment.interview')
        self._prepare(session)
        tool = self._propose(session, 'result = ctx.search_count("recruitment.interview", [])')
        self.user.write({'groups_id': [(3, self.env.ref('recruitment.group_recruitment_interviewer').id)]})
        with self.assertRaises(AccessError):
            tool._authorize(self.user)

    def test_record_rules_are_applied_by_worker(self):
        session = self._session(self.user, 'recruitment.interview')
        self._prepare(session)
        tool = self._propose(session, 'result = ctx.search_count("recruitment.interview", [])')
        self.env['ir.rule'].create({'name': 'Regression no interview rows',
            'model_id': self.env['ir.model']._get_id('recruitment.interview'), 'domain_force': "[('id', '=', 0)]"})
        self.assertEqual(tool._execute_script(self.user), 0)

    def test_repeated_invalid_source_stops_dispatch(self):
        session = self._session()
        self._prepare(session)
        for attempt in range(2):
            result, ok = self._call(session, 'propose_generated_tool', description='Invalid', source_code='import os')
            self.assertFalse(ok)
        self.assertEqual(session._tool_builder_next_step(), 'stop_and_report')
        self.assertFalse(session._get_tool_schema())
        result, ok = self._call(session, 'propose_generated_tool', description='Late',
                                source_code='result = ctx.search_count("recruitment.job.position", [])')
        self.assertFalse(ok)
        self.assertFalse(self.tools.search([('source_session_id', '=', session.id)]))

    def test_duplicate_name_is_terminal(self):
        session = self._session()
        name = self._prepare(session)
        tool = self._propose(session, 'result = ctx.search_count("recruitment.job.position", [])')
        second = self._session(request='Tạo tool tên ' + name)
        result, ok = self._call(second, 'route_tool_request', intent='create', summary='Tạo tool', tool_name=name)
        self.assertFalse(ok, result)
        self.assertFalse(second._get_tool_schema())
        result, ok = self._call(second, 'route_tool_request', intent='chat', summary='Bypass terminal')
        self.assertFalse(ok, result)
        self.assertEqual(len(tool.revision_ids), 1)

    def test_ast_rejects_unsafe_code(self):
        for source in ('import os', 'result = env["res.users"].sudo().search([])',
                       'result = ctx.__class__', 'while True:\n    pass',
                       'def function():\n    return 1'):
            with self.subTest(source=source), self.assertRaises(Exception):
                self.tools._analyze_source_code(source)

    def test_unlink_does_not_require_values(self):
        _, manifest = self.tools._analyze_source_code('ctx.unlink("res.partner", [1])\nresult = True')
        self.assertEqual(manifest['models'][0]['operations'], ['unlink'])
        self.assertEqual(manifest['models'][0]['write_fields'], [])

    def test_invalid_ctx_signature_is_rejected_before_storage(self):
        with self.assertRaises(ValidationError):
            self.tools._analyze_source_code(
                'result = ctx.search_count("recruitment.job.position", [], ["name"])'
            )

    def _description_tool(self, names):
        request = 'Tạo tool viết mô tả tiếng Việt chuyên nghiệp cho vị trí có mô tả trống.'
        session = self._session(request=request)
        self._prepare(session, ['read', 'write'], ['description', 'name'], ['description'], True)
        source = (
            'rows = ctx.search("recruitment.job.position", '
            '[["name", "in", %s], ["description", "=", False]], '
            '["name", "description"], 10)\n'
            'updates = []\n'
            'for row in rows:\n'
            '    text = ctx.generate_text(%s, data=row, system=%s)\n'
            '    ctx.write("recruitment.job.position", [row["id"]], {"description": text})\n'
            '    updates.append({"id": row["id"], "description": text})\n'
            'result = {"selected": len(rows), "processed": len(updates), "updates": updates}'
        ) % (repr(names), repr(request), repr(
            'Vietnamese | Dùng văn phong phù hợp với yêu cầu. | '
            'Use only facts present in the supplied Odoo record; omit missing information '
            'and never invent salary, benefits, company policy, qualifications or requirements.'
        ))
        return self._propose(session, source)

    def test_preview_with_no_empty_descriptions_has_no_effects(self):
        job = self.env['recruitment.job.position'].create({
            'name': 'Regression filled description only', 'description': 'Đã có mô tả'
        })
        tool = self._description_tool([job.name])
        with patch.object(_GeneratedScriptContext, 'generate_text', return_value='Không được gọi') as generate:
            wizard = self._preview(tool, allow_ai=True)
        self.assertEqual(wizard.state, 'done', wizard.result_text)
        result_data = json.loads(wizard.result_text)
        self.assertEqual(result_data['processed'], 0)
        generate.assert_not_called()

    def test_preview_and_confirm_only_empty_description(self):
        self.assertEqual(
            self.env['recruitment.job.position']._fields['description'].type,
            'text',
        )
        empty = self.env['recruitment.job.position'].create({'name': 'Regression empty description'})
        filled = self.env['recruitment.job.position'].create({
            'name': 'Regression existing description', 'description': 'Giữ nguyên'
        })
        tool = self._description_tool([empty.name, filled.name])
        with patch.object(_GeneratedScriptContext, 'generate_text',
                          return_value='Mô tả tuyển dụng đã duyệt') as generate:
            wizard = self._preview(tool, allow_ai=True)
        self.assertEqual(wizard.state, 'preview', wizard.result_text)
        self.assertFalse(empty.description)
        wizard.write({'changes_reviewed': True})
        wizard.action_confirm()
        self.assertEqual(empty.description, 'Mô tả tuyển dụng đã duyệt')
        self.assertEqual(filled.description, 'Giữ nguyên')
        generate.assert_called_once()

    def _write_preview(self):
        partner = self.env['res.partner'].create({'name': 'Regression preview target'})
        session = self._session(model='res.partner')
        self._prepare(session, ['read', 'write'], ['name'], ['comment'])
        tool = self._propose(session, 'rows = ctx.read("res.partner", [%s], ["name"])\n'
                             'ctx.write("res.partner", [%s], {"comment": "Reviewed draft"})\n'
                             'result = {"total": len(rows)}' % (partner.id, partner.id))
        wizard = self._preview(tool)
        self.assertEqual(wizard.state, 'preview', wizard.result_text)
        self.assertFalse(partner.comment)
        return partner, wizard

    def test_preview_then_confirm_applies_exact_plan(self):
        partner, wizard = self._write_preview()
        wizard.write({'changes_reviewed': True})
        wizard.action_confirm()
        self.assertEqual(wizard.state, 'done', wizard.result_text)
        self.assertEqual(html2plaintext(partner.comment).strip(), 'Reviewed draft')

    def test_compact_prompt_preserves_request(self):
        request = 'Viết mô tả tiếng Việt, không thay đổi những vị trí đã có mô tả.'
        messages = build_authoring_inputs({'original_request': request, 'next_step': 'propose_generated_tool'}, {})
        self.assertEqual([m['role'] for m in messages], ['system', 'user'])
        self.assertIn(request, messages[1]['content'][0]['text'])
        self.assertIn('ctx.generate_text', messages[0]['content'][0]['text'])

    def test_scoped_context_bypasses_search_loop(self):
        session = self._session(self.user, model='recruitment.job.position')
        result, ok = self._call(session, 'route_tool_request', intent='create', summary='Tạo công cụ')
        self.assertTrue(ok, result)
        self.assertEqual(result.get('context_scope', {}).get('status'), 'scoped')
        self.assertNotEqual(session._tool_builder_next_step(), 'search_authorized_models')
        self.assertIn(session._tool_builder_next_step(), ('inspect_authorized_model', 'define_generated_tool_task'))
