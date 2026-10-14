"""Provider-free incoming decoder tests; no I/O, native or delivery evidence."""
import array
import gc
import hashlib
import json
import tracemalloc
import unittest

from trial_sdk_incoming import (BodyDecoder, DecodedLine, HTTP_STREAM_BYTES, IncomingError,
                                INTEGER_BITS, LineAssembler, MAX_CHUNK_BYTES, MAX_DEPTH,
                                MAX_DOCUMENT_BYTES, MAX_NODES, NATIVE_STREAM_BYTES, OwnedJSON, canonical_chunks,
                                canonical_size_sha256, parse_document, validate_line)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                      allow_nan=False).encode('utf-8')


def sha(content):
    return hashlib.sha256(content).hexdigest()


def callback_wire(size, tail=b'}}}}'):
    """A valid PostToolUse-shaped object whose large value sits in tool_response."""
    head = (b'{"type":"control_request","request_id":"call-1","request":{"subtype":"hook_callback",'
            b'"callback_id":"cb","input":{"hook_event_name":"PostToolUse","session_id":"s",'
            b'"tool_name":"Read","tool_use_id":"toolu_1","tool_response":{"content":"')
    unit = 'é\U0001F600\\n\\"\\\\x'.encode('utf-8')
    body = unit * ((size - len(head) - len(tail) - 1) // len(unit))
    body += b'x' * (size - len(head) - len(tail) - 1 - len(body))
    return head + body + b'"' + tail


def chunks(raw, size):
    return [raw[index:index + size] for index in range(0, len(raw), size)]


def lines(assembler, pieces, clock=0):
    out = []
    for index, piece in enumerate(pieces):
        out.extend(assembler.feed(piece, clock + index))
    return out


class LimitTest(unittest.TestCase):
    def test_original_and_selected_limits_are_distinct_and_unchanged(self):
        self.assertEqual(MAX_DOCUMENT_BYTES, 131072)
        self.assertEqual(NATIVE_STREAM_BYTES, 3 * 1024 * 1024)
        self.assertEqual(HTTP_STREAM_BYTES, 768 * 1024)
        self.assertEqual((MAX_CHUNK_BYTES, MAX_DEPTH, MAX_NODES, INTEGER_BITS),
                         (8192, 32, 131072, 1024))
        for cls in (BodyDecoder, LineAssembler):
            self.assertEqual(cls(NATIVE_STREAM_BYTES).max_bytes, NATIVE_STREAM_BYTES)
            self.assertEqual(cls(HTTP_STREAM_BYTES).max_bytes, HTTP_STREAM_BYTES)
            for bad in (0, -1, NATIVE_STREAM_BYTES + 1, True, 1.0, '8'):
                with self.assertRaises(IncomingError) as caught:
                    cls(bad)
                self.assertEqual(caught.exception.code, 'incoming_limit')
        with self.assertRaises(IncomingError):
            parse_document('{}', 2, NATIVE_STREAM_BYTES + 1)

    def test_decoder_can_consume_beyond_selected_document_but_validation_refuses(self):
        raw = b'{"k":"' + b'x' * (MAX_DOCUMENT_BYTES - 7) + b'"}'
        self.assertEqual(len(raw), MAX_DOCUMENT_BYTES + 1)
        decoder = BodyDecoder(NATIVE_STREAM_BYTES)
        for piece in chunks(raw, MAX_CHUNK_BYTES):
            decoder.feed(piece)
        text = decoder.finish()
        self.assertEqual(decoder.raw_sha256, sha(raw))
        with self.assertRaises(IncomingError) as caught:
            parse_document(text, decoder.raw_bytes)
        self.assertEqual(caught.exception.code, 'incoming_document_bytes')
        self.assertEqual(parse_document(text, decoder.raw_bytes, NATIVE_STREAM_BYTES)['k'][:1], 'x')
        exact = b'{"k":"' + b'x' * (MAX_DOCUMENT_BYTES - 8) + b'"}'
        self.assertEqual(len(parse_document(exact.decode(), len(exact))['k']), MAX_DOCUMENT_BYTES - 8)


class BodyDecoderTest(unittest.TestCase):
    def test_70kib_every_fragment_size_matches_whole(self):
        raw = callback_wire(70 * 1024)
        self.assertEqual(len(raw), 71680)
        for size in (1, 2, 3, 5, 7, 64, 1000, 4097, MAX_CHUNK_BYTES):
            decoder = BodyDecoder(MAX_DOCUMENT_BYTES)
            for piece in chunks(raw, size):
                self.assertIsNone(decoder.feed(memoryview(piece) if size % 2 else piece))
                self.assertLessEqual(decoder.carry_bytes, 3)
            self.assertIsNone(decoder.raw_sha256)
            text = decoder.finish()
            self.assertEqual(text, raw.decode('utf-8'))
            self.assertEqual((decoder.raw_bytes, decoder.raw_sha256), (len(raw), sha(raw)))

    def test_every_split_inside_multibyte_characters(self):
        raw = '{"a":"é€\U0001F600\U0010FFFF"}'.encode('utf-8')
        for cut in range(1, len(raw)):
            decoder = BodyDecoder(64)
            decoder.feed(raw[:cut])
            self.assertLessEqual(decoder.carry_bytes, 3)
            decoder.feed(raw[cut:])
            self.assertEqual(decoder.finish(), raw.decode())
        decoder = BodyDecoder(64)
        decoder.feed(b'\xf0\x9f\x98')
        self.assertEqual(decoder.carry_bytes, 3)

    def test_invalid_utf8_refuses_at_feed_with_truthful_prefix(self):
        for bad in (b'\xff', b'\xc0\x80', b'\xe0\x80\x80', b'\xed\xa0\x80', b'\xf4\x90\x80\x80',
                    b'\xe2\x28\xa1'):
            decoder = BodyDecoder(64)
            decoder.feed(b'{"a":"')
            with self.assertRaises(IncomingError) as caught:
                decoder.feed(bad)
            self.assertEqual(caught.exception.code, 'incoming_utf8')
            refusal = decoder.refusal
            self.assertEqual((refusal['consumed_prefix_bytes'], refusal['consumed_prefix_sha256'],
                              refusal['hash_complete']), (6, sha(b'{"a":"'), False))
            self.assertIsNone(decoder.raw_sha256)
            json.dumps(refusal)
            with self.assertRaises(IncomingError) as caught:
                decoder.feed(b'x')
            self.assertEqual(caught.exception.code, 'incoming_unusable')

    def test_truncated_utf8_at_finish_has_complete_hash_but_refuses(self):
        decoder = BodyDecoder(64)
        decoder.feed(b'{"a":"\xe2\x82')
        with self.assertRaises(IncomingError) as caught:
            decoder.finish()
        self.assertEqual(caught.exception.code, 'incoming_utf8')
        self.assertEqual(decoder.refusal['consumed_prefix_sha256'], sha(b'{"a":"\xe2\x82'))
        self.assertTrue(decoder.refusal['hash_complete'])
        self.assertIsNone(decoder.raw_sha256)

    def test_limit_refuses_before_hash_or_decode(self):
        decoder = BodyDecoder(10)
        decoder.feed(b'0123456789')
        with self.assertRaises(IncomingError) as caught:
            decoder.feed(b'a')
        self.assertEqual(caught.exception.code, 'incoming_bytes_exceeded')
        self.assertEqual(decoder.raw_bytes, 10)
        self.assertEqual(decoder.refusal['consumed_prefix_sha256'], sha(b'0123456789'))

    def test_chunk_shape_refuses_and_poisons(self):
        bad_chunks = (b'', b'x' * (MAX_CHUNK_BYTES + 1), bytearray(b'x'), 'x',
                      memoryview(array.array('I', [1])), memoryview(b'abcd')[::2])
        for bad in bad_chunks:
            decoder = BodyDecoder(MAX_CHUNK_BYTES * 2)
            with self.assertRaises(IncomingError) as caught:
                decoder.feed(bad)
            self.assertEqual(caught.exception.code, 'incoming_chunk')
            with self.assertRaises(IncomingError):
                decoder.feed(b'x')
        decoder = BodyDecoder(MAX_CHUNK_BYTES)
        decoder.feed(b'x' * MAX_CHUNK_BYTES)
        self.assertEqual(decoder.finish(), 'x' * MAX_CHUNK_BYTES)

    def test_properties_stable_after_finish(self):
        decoder = BodyDecoder(16)
        decoder.feed(b'{}')
        self.assertEqual(decoder.finish(), '{}')
        facts = (decoder.raw_bytes, decoder.raw_sha256, decoder.finished)
        for call in (lambda: decoder.finish(), lambda: decoder.feed(b'x')):
            with self.assertRaises(IncomingError) as caught:
                call()
            self.assertEqual(caught.exception.code, 'incoming_unusable')
        self.assertEqual((decoder.raw_bytes, decoder.raw_sha256, decoder.finished), facts)
        self.assertEqual(facts, (2, sha(b'{}'), True))
        self.assertIsNone(decoder.refusal)


class LineAssemblerTest(unittest.TestCase):
    def test_70kib_fragmented_and_coalesced(self):
        first, second = callback_wire(70 * 1024), b'{"type":"system","subtype":"init"}'
        stream = first + b'\n' + second + b'\n'
        for size in (1, 3, 7, 1024, MAX_CHUNK_BYTES):
            assembler = LineAssembler(MAX_DOCUMENT_BYTES)
            got = lines(assembler, chunks(stream, size), clock=100)
            self.assertEqual([line.raw_sha256 for line in got], [sha(first), sha(second)])
            self.assertEqual([line.raw_bytes for line in got], [len(first), len(second)])
            self.assertEqual(got[0].text, first.decode())
            self.assertEqual(got[0].first_byte_ns, 100)
            # The second line starts in the chunk that holds its first byte.
            self.assertEqual(got[1].first_byte_ns, 100 + (len(first) + 1) // size)
            self.assertEqual(validate_line(got[0]).take()['request']['input']['tool_response']
                             ['content'], json.loads(first)['request']['input']['tool_response']['content'])
            assembler.finish()
            self.assertTrue(assembler.closed)

    def test_coalesced_frames_use_chunk_time_and_partial_keeps_earliest(self):
        assembler = LineAssembler(64)
        self.assertEqual(list(assembler.feed(b'{"a":', 5)), [])
        got = list(assembler.feed(b'1}\n{"b":2}\n{"c":', 9))
        self.assertEqual([(line.text, line.first_byte_ns) for line in got],
                         [('{"a":1}', 5), ('{"b":2}', 9)])
        self.assertEqual(assembler.pending_bytes, 5)
        got = list(assembler.feed(b'3}\n\n', 12))
        self.assertEqual([(line.text, line.first_byte_ns) for line in got], [('{"c":3}', 9), ('', 12)])
        self.assertEqual(got[1].raw_sha256, sha(b''))
        assembler.finish()

    def test_every_split_position_around_utf8_escapes_keys_and_lf(self):
        doc = '{"kéy":"\\u00e9\\"\\\\\U0001F600","n":[1,-2.5e3,true,null]}'.encode('utf-8')
        stream = doc + b'\r\n' + doc + b'\n'
        for cut in range(1, len(stream)):
            assembler = LineAssembler(256)
            got = lines(assembler, [stream[:cut], stream[cut:]])
            self.assertEqual([line.raw_sha256 for line in got], [sha(doc + b'\r'), sha(doc)])
            self.assertEqual(validate_line(got[0]).take(), json.loads(doc))
            self.assertEqual(got[1].first_byte_ns, 0 if cut > len(doc) + 2 else 1)
            assembler.finish()

    def test_line_limit_boundary_refuses_before_allocation(self):
        assembler = LineAssembler(8)
        self.assertEqual([line.text for line in assembler.feed(b'{"a":12}\n', 0)], ['{"a":12}'])
        with self.assertRaises(IncomingError) as caught:
            list(assembler.feed(b'{"a":123}', 1))
        self.assertEqual(caught.exception.code, 'incoming_bytes_exceeded')
        self.assertEqual(assembler.refusal['consumed_prefix_bytes'], 0)
        self.assertFalse(assembler.refusal['hash_complete'])
        with self.assertRaises(IncomingError) as caught:
            list(assembler.feed(b'\n', 2))
        self.assertEqual(caught.exception.code, 'incoming_unusable')

    def test_prefix_refusal_hash_is_labelled_prefix(self):
        assembler = LineAssembler(MAX_DOCUMENT_BYTES)
        raw = callback_wire(70 * 1024)
        pieces = chunks(raw, MAX_CHUNK_BYTES)
        lines(assembler, pieces[:3])
        with self.assertRaises(IncomingError):
            list(assembler.feed(b'\xff', 4))
        refusal = assembler.refusal
        self.assertEqual(refusal['consumed_prefix_bytes'], 3 * MAX_CHUNK_BYTES)
        self.assertEqual(refusal['consumed_prefix_sha256'], sha(raw[:3 * MAX_CHUNK_BYTES]))
        self.assertFalse(refusal['hash_complete'])
        self.assertFalse(refusal['origin_attested'])

    def test_eof_partial_refuses_and_closes(self):
        assembler = LineAssembler(64)
        list(assembler.feed(b'{"a":1}\n{"b"', 0))
        with self.assertRaises(IncomingError) as caught:
            assembler.finish()
        self.assertEqual(caught.exception.code, 'incoming_partial_line')
        self.assertEqual(assembler.refusal['consumed_prefix_sha256'], sha(b'{"b"'))
        self.assertTrue(assembler.closed)
        with self.assertRaises(IncomingError):
            assembler.feed(b'}\n', 1)
        utf8 = LineAssembler(64)
        list(utf8.feed(b'{"a":"\xe2\x82', 0))
        with self.assertRaises(IncomingError) as caught:
            utf8.finish()
        self.assertEqual(caught.exception.code, 'incoming_partial_line')
        clean = LineAssembler(64)
        list(clean.feed(b'{}\n', 0))
        clean.finish()
        with self.assertRaises(IncomingError) as caught:
            clean.finish()
        self.assertEqual(caught.exception.code, 'incoming_unusable')

    def test_truncated_utf8_before_lf_refuses(self):
        assembler = LineAssembler(64)
        with self.assertRaises(IncomingError) as caught:
            list(assembler.feed(b'{"a":"\xe2\x82\n', 0))
        self.assertEqual(caught.exception.code, 'incoming_utf8')
        self.assertTrue(assembler.refusal['hash_complete'])

    def test_clock_and_abandoned_generator_poison(self):
        assembler = LineAssembler(64)
        list(assembler.feed(b'{', 10))
        with self.assertRaises(IncomingError) as caught:
            assembler.feed(b'}', 9)
        self.assertEqual(caught.exception.code, 'incoming_clock')
        with self.assertRaises(IncomingError):
            assembler.feed(b'}', 11)
        for abandon in ('close', 'unstarted'):
            assembler = LineAssembler(64)
            pending = assembler.feed(b'{}\n{}\n', 0)
            if abandon == 'close':
                next(pending)
                pending.close()
            with self.assertRaises(IncomingError) as caught:
                assembler.feed(b'{}\n', 1)
            self.assertEqual(caught.exception.code, 'incoming_unusable' if abandon == 'close'
                             else 'incoming_feed_abandoned')
            self.assertEqual(assembler.refusal['code'], 'incoming_feed_abandoned')

    def test_no_retained_raw_and_bounded_transient_raw(self):
        raw = b'x' * (70 * 1024)
        pieces = chunks(b'{"k":"' + raw + b'"}', MAX_CHUNK_BYTES)
        assembler = LineAssembler(MAX_DOCUMENT_BYTES)
        tracemalloc.start()
        try:
            for index, piece in enumerate(pieces):
                before = tracemalloc.get_traced_memory()[0]
                tracemalloc.reset_peak()
                self.assertEqual(list(assembler.feed(piece, index)), [])
                current, peak = tracemalloc.get_traced_memory()
                # Transient work in one feed stays near one decoded block, never a line.
                self.assertLess(peak - before, 3 * MAX_CHUNK_BYTES)
                self.assertLess(current - before, 2 * MAX_CHUNK_BYTES)
        finally:
            tracemalloc.stop()
        retained, seen, pending = [], set(), [assembler]
        while pending:
            item = pending.pop()
            if id(item) in seen or isinstance(item, type):
                continue
            seen.add(id(item))
            if isinstance(item, (bytes, bytearray, memoryview)):
                retained.append(len(item))
            elif not isinstance(item, str):
                pending.extend(gc.get_referents(item))
        self.assertLessEqual(max(retained, default=0), 3)
        self.assertEqual(assembler.pending_bytes, sum(map(len, pieces)))


class ParseDocumentTest(unittest.TestCase):
    def parse(self, raw, max_bytes=MAX_DOCUMENT_BYTES):
        return parse_document(raw.decode('utf-8'), len(raw), max_bytes)

    def refuses(self, raw, code):
        with self.assertRaises(IncomingError) as caught:
            self.parse(raw)
        self.assertEqual(caught.exception.code, code, raw[:80])

    def test_valid_strict_scalars(self):
        value = self.parse(b' {"a":[18446744073709551615,-18446744073709551615,-0,1.5e300,'
                           b'"\\ud83d\\ude00","\xf0\x9f\x98\x80"],"b":{}}\r')
        self.assertEqual(value['a'][0], 2 ** 64 - 1)
        self.assertEqual(value['a'][4], value['a'][5])

    def test_integer_magnitude_and_no_coercion(self):
        for number in (2 ** 64, 2 ** 1023, 2 ** 1024 - 1, -(2 ** 1024 - 1)):
            raw = b'{"n":%d}' % number
            self.assertEqual(self.parse(raw)['n'], number)
            self.assertIs(type(self.parse(raw)['n']), int)
        for number in (2 ** 1024, -(2 ** 1024)):
            self.refuses(b'{"n":%d}' % number, 'incoming_integer_bounds')
        value = self.parse(b'{"i":1,"f":1.0,"e":1e300,"t":true,"z":0}')
        self.assertEqual([type(value[key]) for key in 'ifetz'], [int, float, float, bool, int])

    def test_node_count_cap_includes_keys(self):
        allowed = canonical({'k': [0] * (MAX_NODES - 3)})
        self.assertEqual(len(parse_document(allowed.decode(), len(allowed), NATIVE_STREAM_BYTES)['k']),
                         MAX_NODES - 3)
        over = canonical({'k': [0] * (MAX_NODES - 2)})
        with self.assertRaises(IncomingError) as caught:
            parse_document(over.decode(), len(over), NATIVE_STREAM_BYTES)
        self.assertEqual(caught.exception.code, 'incoming_graph_bounds')
        self.refuses(over, 'incoming_document_bytes')

    def test_large_prefix_then_malformed_or_duplicate_tail(self):
        prefix = callback_wire(70 * 1024, tail=b'')
        cases = {
            prefix + b',"x":{"d":1,"d":2}}}}}': 'incoming_duplicate_key',
            prefix + b'}}}}}': 'incoming_json',
            prefix + b'}}}': 'incoming_json',
            prefix + b'}}}} x': 'incoming_json',
            prefix + b'}}}}{}': 'incoming_json',
            prefix + b',"n":NaN}}}}': 'incoming_nonfinite',
            prefix + b',"n":Infinity}}}}': 'incoming_nonfinite',
            prefix + b',"n":-Infinity}}}}': 'incoming_nonfinite',
            prefix + b',"n":1e309}}}}': 'incoming_nonfinite',
            prefix + b',"n":-1e309}}}}': 'incoming_nonfinite',
            prefix + b',"n":%d}}}}' % 2 ** 1024: 'incoming_integer_bounds',
            prefix + b',"n":-%d}}}}' % 2 ** 1024: 'incoming_integer_bounds',
            prefix + b',"n":' + b'9' * 5000 + b'}}}}': 'incoming_integer_bounds',
            prefix + b',"s":"\\ud800"}}}}': 'incoming_surrogate',
            prefix + b',"\\udc00":1}}}}': 'incoming_surrogate',
            prefix + b',"s":"\\ude00\\ud83d"}}}}': 'incoming_surrogate',
            prefix + b',"c":"\x01"}}}}': 'incoming_json',
        }
        for raw, code in cases.items():
            self.refuses(raw, code)
        self.assertEqual(len(self.parse(prefix + b'}}}}')['request']['input']), 5)

    def test_depth_boundary(self):
        def nested(depth):
            return b'{"a":' * depth + b'0' + b'}' * depth
        self.assertIsNotNone(self.parse(nested(MAX_DEPTH)))
        self.refuses(nested(MAX_DEPTH + 1), 'incoming_depth')
        self.refuses(b'{"a":' + b'[' * 50000 + b']' * 50000 + b'}', 'incoming_depth')

    def test_graph_shapes_beyond_old_schema_guesses(self):
        value = {'list': list(range(5000)), 'keys': {'k%04d' % n: n for n in range(1000)},
                 'nodes': [{'a': [None]} for _ in range(3000)]}
        raw = canonical(value)
        self.assertGreater(len(value['list']), 256)
        self.assertGreater(len(value['keys']), 64)
        self.assertEqual(self.parse(raw), value)

    def test_document_shape_and_byte_consistency(self):
        self.refuses(b'[]', 'incoming_not_object')
        self.refuses(b'"x"', 'incoming_not_object')
        self.refuses(b'', 'incoming_json')
        self.refuses(b'\xef\xbb\xbf{}', 'incoming_json')
        for text, raw_bytes in (('{}', 1), ('{}', 3), ('{"a":"é"}', 9), (b'{}', 2), ('{}', True)):
            with self.assertRaises(IncomingError) as caught:
                parse_document(text, raw_bytes)
            self.assertEqual(caught.exception.code, 'incoming_document_bytes')
        with self.assertRaises(IncomingError) as caught:
            parse_document('{"a":"\ud800"}', 12)
        self.assertEqual(caught.exception.code, 'incoming_surrogate')
        self.assertEqual(parse_document('{"a":"é"}', 10), {'a': 'é'})


class OwnedJSONTest(unittest.TestCase):
    def owned(self, raw=b'{"type":"control_request","request":{"x":[1]}}'):
        assembler = LineAssembler(256)
        (line,) = assembler.feed(raw + b'\n', 42)
        return validate_line(line), line

    def test_take_exactly_once_and_readonly(self):
        token, line = self.owned()
        self.assertEqual((token.kind, token.raw_bytes, token.raw_sha256, token.first_byte_ns),
                         ('control_request', line.raw_bytes, line.raw_sha256, 42))
        self.assertFalse(token.metadata()['origin_attested'])
        graph = token.take()
        self.assertEqual(graph, {'type': 'control_request', 'request': {'x': [1]}})
        self.assertTrue(token.taken)
        self.assertNotIn(graph, gc.get_referents(token))
        with self.assertRaises(IncomingError) as caught:
            token.take()
        self.assertEqual(caught.exception.code, 'incoming_owned_consumed')
        for name in ('kind', '_graph', 'raw_bytes', 'extra'):
            with self.assertRaises(AttributeError):
                setattr(token, name, 1)
        with self.assertRaises(AttributeError):
            del token._kind
        self.assertNotIn('control_request\', \'request', repr(token))

    def test_parse_once_gives_fresh_graph_per_token(self):
        first, line = self.owned()
        second = validate_line(line)
        one, two = first.take(), second.take()
        self.assertEqual(one, two)
        self.assertIsNot(one, two)
        self.assertIsNot(one['request'], two['request'])

    def test_kind_and_construction(self):
        self.assertIsNone(self.owned(b'{"kind":"backend_ready"}')[0].kind)
        self.assertIsNone(self.owned(b'{"type":7}')[0].kind)
        with self.assertRaises(IncomingError) as caught:
            OwnedJSON(object(), {}, 2, sha(b'{}'), 0)
        self.assertEqual(caught.exception.code, 'incoming_owned_construction')
        with self.assertRaises(IncomingError) as caught:
            validate_line(dict(text='{}', raw_bytes=2, raw_sha256=sha(b'{}'), first_byte_ns=0))
        self.assertEqual(caught.exception.code, 'incoming_line_type')
        with self.assertRaises(IncomingError) as caught:
            validate_line(DecodedLine('{"a":1,"a":2}', 13, 'x', 0))
        self.assertEqual(caught.exception.code, 'incoming_duplicate_key')


class CanonicalTest(unittest.TestCase):
    values = (
        {}, [], {'b': 1, 'a': [True, False, None]},
        {'é': 1, 'e': 2, '\U0001F600': [{}], 'E': {'z': -0.0, 'y': 1e16, 'x': 5e-324}},
        {'s': 'quote" back\\ nl\n tab\t ctl\x00\x1f del\x7f ls  astral\U0001F600'},
        {'n': [2 ** 64 - 1, -(2 ** 64 - 1), 2 ** 64, 2 ** 1023, -(2 ** 1024 - 1), 0, 1.5, 0.1,
               1e308, True, False]},
        {'k' * 9000: 'v' * 9000},
    )

    def test_exact_json_dumps_equality(self):
        for value in self.values:
            pieces = list(canonical_chunks(value))
            self.assertEqual(b''.join(pieces), canonical(value))
            self.assertTrue(all(0 < len(piece) <= MAX_CHUNK_BYTES for piece in pieces))
            self.assertEqual(canonical_size_sha256(value),
                             (len(canonical(value)), sha(canonical(value))))
        size, digest = canonical_size_sha256({})
        self.assertEqual((type(size), digest), (int, digest.lower()))

    def test_large_tokens_are_subdivided_before_encoding(self):
        for text in ('x' * 71680, '\x01' * 71680, '\U0001F600' * 30000, 'é"' * 40000):
            value = {'tool_response': {'content': text}}
            pieces = list(canonical_chunks(value))
            self.assertGreater(len(pieces), 8)
            self.assertLessEqual(max(map(len, pieces)), MAX_CHUNK_BYTES)
            self.assertEqual(b''.join(pieces), canonical(value))

    def test_70kib_callback_round_trip_digest(self):
        raw = callback_wire(70 * 1024)
        assembler = LineAssembler(MAX_DOCUMENT_BYTES)
        (line,) = lines(assembler, chunks(raw + b'\n', 4096))
        graph = validate_line(line).take()
        self.assertEqual(line.raw_sha256, sha(raw))
        self.assertEqual(canonical_size_sha256(graph['request']['input']),
                         (len(canonical(graph['request']['input'])),
                          sha(canonical(graph['request']['input']))))

    def test_chunk_boundary_exact(self):
        value = 'x' * (MAX_CHUNK_BYTES - 2)
        self.assertEqual([len(piece) for piece in canonical_chunks(value)], [MAX_CHUNK_BYTES])

    def test_refusals(self):
        cyclic = []
        cyclic.append(cyclic)

        class Mapping(dict):
            pass

        def nested(depth):
            value = 0
            for _ in range(depth):
                value = [value]
            return value
        cases = ((float('nan'), 'canonical_nonfinite'), ({'a': float('inf')}, 'canonical_nonfinite'),
                 ([2 ** 1024], 'canonical_integer'), ([-(2 ** 1024)], 'canonical_integer'),
                 ([0] * MAX_NODES, 'canonical_graph_bounds'),
                 ({'k%d' % n: None for n in range(MAX_NODES // 2)}, 'canonical_graph_bounds'), ((1,), 'canonical_shape'), ({1}, 'canonical_shape'),
                 ({1: 'a'}, 'canonical_shape'), ({True: 'a'}, 'canonical_shape'),
                 (Mapping(), 'canonical_shape'), (b'x', 'canonical_shape'),
                 ({'a': '\ud800'}, 'canonical_surrogate'), ({'\udc00': 1}, 'canonical_surrogate'),
                 (cyclic, 'canonical_depth'), (nested(MAX_DEPTH + 1), 'canonical_depth'))
        for value, code in cases:
            with self.assertRaises(IncomingError) as caught:
                canonical_size_sha256(value)
            self.assertEqual(caught.exception.code, code)
        self.assertEqual(canonical_size_sha256(nested(MAX_DEPTH))[0], len(canonical(nested(MAX_DEPTH))))
        widest = [0] * (MAX_NODES - 1)
        self.assertEqual(canonical_size_sha256(widest), (len(canonical(widest)), sha(canonical(widest))))


if __name__ == '__main__':
    unittest.main()
