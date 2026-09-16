"""Scripted provider steps; the installed harness executes all fixture commands."""
import json
import shlex


def inbox_tool_step(request, context, expected_source, tool):
    results = {m['tool_call_id']: m.get('content') for m in request['messages'] if m['role'] == 'tool'}
    assert tool in [t['function']['name'] for t in request['tools']]
    if 'source' not in results:
        command = 'printf %s ' + shlex.quote(json.dumps(context['read_input'])) + ' | ' + shlex.join(context['read'])
        ident = 'source'
    elif 'completion' not in results:
        assert expected_source in json.dumps(results['source'])
        command = "printf %s 'Native inbox selected result' | " + shlex.join(context['completion'])
        ident = 'completion'
    else:
        assert 'handled' in json.dumps(results['completion'])
        return dict(role='assistant', content='Native inbox handled.'), 'stop'
    arguments = dict(command=command)
    if tool == 'bash':
        arguments['description'] = 'Execute selected native inbox fixture step'
    return dict(role='assistant', tool_calls=[dict(index=0, id=ident, type='function',
        function=dict(name=tool, arguments=json.dumps(arguments)))]), 'tool_calls'


def chat_stream(delta, finish):
    chunks = [dict(id='probe', object='chat.completion.chunk', created=0, model='probe',
        choices=[dict(index=0, delta=delta, finish_reason=None)]),
        dict(id='probe', object='chat.completion.chunk', created=0, model='probe',
        choices=[dict(index=0, delta={}, finish_reason=finish)])]
    return (''.join('data: ' + json.dumps(c) + '\n\n' for c in chunks) + 'data: [DONE]\n\n').encode()
