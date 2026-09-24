"""Opt-in local Ollama model-selection check on an isolated database."""
import json
from unittest.mock import patch


assert env.cr.dbname.startswith('muk_ai_regression_'), 'Use an isolated regression database.'
REQUEST = 'hãy tạo ra 1 tool đếm số lượng ứng cử viên trong hệ thống'
TEST_MODEL = 'qwen2.5-coder:7b'

user = env['res.users'].browse(2)
session = env['muk_ai.session'].create({
    'name': 'Live candidate selection regression',
    'user_id': user.id,
    'agent_id': 1,
    'state': 'running',
    'turn_seq': 1,
    'view_context': {
        'kind': 'graph',
        'model': 'recruitment.application',
        'action_id': 651,
        'view_type': 'graph',
    },
    'conversation': [{
        'role': 'user',
        'content': [{'type': 'input_text', 'text': REQUEST}],
    }],
}).with_user(user)
provider = env.ref('muk_ai_ollama.provider_ollama')
provider.ollama_fallback_provider_id = False
session.agent_id.model_id.sudo().write({'technical_name': TEST_MODEL})

try:
    with patch.object(type(session), '_check_cancelled'), \
         patch.object(type(session), '_on_stream_delta'), \
         patch.object(type(session), '_flush_stream_buffer'):
        for step in range(8):
            schema = session._get_tool_schema()
            payload = session._stream_provider_round(
                provider, schema, TEST_MODEL, session.agent_id, cache={}
            )
            calls = payload.get('tool_calls') or []
            assert calls, ('No tool call', payload, session._tool_builder_next_step())
            call = calls[0]
            session.conversation = list(session.conversation) + payload['carry_inputs']
            result, ok = session._dispatch_tool_call(
                call['name'], call['arguments'], call['call_id']
            )
            result = json.loads(result) if isinstance(result, str) else result
            print(
                'CANDIDATE_SELECTION_STEP', step, call['name'],
                json.dumps(call['arguments'], ensure_ascii=False),
                json.dumps({
                    'ok': ok,
                    'status': result.get('status'),
                    'rejection_reason': result.get('rejection_reason'),
                    'best_matching_models': result.get('best_matching_models'),
                }, ensure_ascii=False),
                flush=True,
            )
            session.conversation = list(session.conversation) + [{
                'type': 'function_call_output',
                'call_id': call['call_id'],
                'output': json.dumps(result, ensure_ascii=False),
            }]
            if call['name'] == 'define_generated_tool_task' and ok:
                assert session.tool_builder_task_contract['models'][0]['model'] == (
                    'recruitment.candidate'
                ), session.tool_builder_task_contract
                print('CANDIDATE_SELECTION_OK recruitment.candidate', flush=True)
                break
        else:
            raise AssertionError('Candidate model was not bound within bounded rounds')
finally:
    env.cr.rollback()
