"""Opt-in: run through odoo-bin shell on an isolated test database only."""
import json
from unittest.mock import patch

assert env.cr.dbname.startswith('muk_ai_regression_'), 'Use the isolated regression database.'
REQUEST = 'Tôi cần bạn tạo một tool viết mô tả cho job position có description trống, văn phong phải như các nhà tuyển dụng hay viết.'
TEST_MODEL = 'qwen2.5-coder:7b'

user = env['res.users'].browse(2)
session = env['muk_ai.session'].create({
    'name': 'Live local regression', 'user_id': user.id, 'agent_id': 1,
    'state': 'running', 'turn_seq': 1,
    'view_context': {'kind': 'list', 'model': 'recruitment.job.position',
                     'action_id': 648, 'view_type': 'list'},
    'conversation': [{'role': 'user', 'content': [{'type': 'input_text', 'text': REQUEST}]}],
})
session = session.with_user(user)
provider = env.ref('muk_ai_ollama.provider_ollama')
# Explicitly local: tests never fall back to an external paid provider.
provider.ollama_fallback_provider_id = False
# The running endpoint currently has this local model. Change only the cloned
# transaction so generation and Preview use the same actually available model.
session.agent_id.model_id.sudo().write({'technical_name': TEST_MODEL})
request_responses = type(provider)._request_responses
raw_responses = []

def capture_response(record, *args, **kwargs):
    response = request_responses(record, *args, **kwargs)
    raw_responses.append(response)
    return response

try:
    with patch.object(type(session), '_check_cancelled'), \
         patch.object(type(session), '_on_stream_delta'), \
         patch.object(type(session), '_flush_stream_buffer'), \
         patch.object(type(provider), '_request_responses', capture_response):
        for step in range(12):
            schema = session._get_tool_schema()
            print('LIVE_SCHEMA', step, [item['name'] for item in schema],
                  session._tool_builder_next_step(), flush=True)
            payload = session._stream_provider_round(
                provider, schema, TEST_MODEL, session.agent_id, cache={}
            )
            calls = payload.get('tool_calls') or []
            if not calls:
                print('LIVE_RAW_RESPONSE', json.dumps(raw_responses[-1], ensure_ascii=False), flush=True)
                raise AssertionError(('No tool call', payload, session._tool_builder_next_step(), session._latest_counted_user_entry().get('_tool_builder_last_error')))
            call = calls[0]
            session.conversation = list(session.conversation) + payload['carry_inputs']
            result, ok = session._dispatch_tool_call(call['name'], call['arguments'], call['call_id'])
            if isinstance(result, str):
                result = json.loads(result)
            print('LIVE_STEP', step, call['name'], json.dumps(call['arguments'], ensure_ascii=False),
                  json.dumps(result if not ok else {'status': result.get('status')}, ensure_ascii=False), flush=True)
            session.conversation = list(session.conversation) + [{
                'type': 'function_call_output', 'call_id': call['call_id'],
                'output': json.dumps(result, ensure_ascii=False),
            }]
            if call['name'] == 'ask_user':
                raise AssertionError('Unexpected clarification for complete request')
            if session._tool_builder_next_step() == 'summarize_result':
                tool = env['muk_ai.generated.tool'].browse(result['generated_tool_id'])
                print('LIVE_SOURCE', tool.source_code, flush=True)
                assert tool.capability_manifest['uses_ai']
                assert {m['model'] for m in tool.capability_manifest['models']} == {'recruitment.job.position'}
                assert set(tool.capability_manifest['models'][0]['operations']) == {'read', 'write'}
                print('LIVE_CREATED', tool.id, flush=True)
                wizard = env['muk_ai.generated.tool.execution'].with_user(user)._create_for_tool(tool)
                wizard = wizard.with_user(user)
                wizard.action_run_direct()
                print('LIVE_RUN', wizard.state,
                      json.dumps(wizard.result_text or {}, ensure_ascii=False),
                      flush=True)
                assert wizard.state == 'done', wizard.result_text
                assert len(wizard.preview_data.get('effects') or []) <= tool.max_records
                for effect in wizard.preview_data.get('effects') or []:
                    assert effect['method'] == 'write'
                    assert all(not item.get('description') for item in effect['before'])
                    records = env[effect['model']].with_user(user).browse(effect['ids'])
                    assert all(not record.description for record in records)
                break
        else:
            raise AssertionError('No completed tool within bounded rounds')
finally:
    env.cr.rollback()
