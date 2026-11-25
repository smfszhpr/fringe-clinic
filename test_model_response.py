import unittest
from unittest.mock import patch
from server import model, parse_model_response,ModelOutputError

class Responses(unittest.TestCase):
    def test_truncated_utf8_retries(self):
        truncated = UnicodeDecodeError('utf-8', b'\xe4\xb8', 0, 2, 'unexpected end of data')
        with patch('server._model_once', side_effect=[truncated, ({'minute': 105}, {})]) as request:
            self.assertEqual(model('test', {}), ({'minute': 105}, {}))
            self.assertEqual(request.call_count, 2)

    def test_text_after_thinking(self):
        self.assertEqual(parse_model_response({'content':[{'type':'thinking','thinking':'private'},{'type':'text','text':'```json\n{"minute":105}\n```'}]}),{'minute':105})
    def test_thinking_only_retries(self):
        with self.assertRaises(ModelOutputError): parse_model_response({'content':[{'type':'thinking','thinking':'...'}]})
    def test_truncation_retries(self):
        with self.assertRaises(ModelOutputError): parse_model_response({'stop_reason':'max_tokens','content':[{'type':'text','text':'{"body":{}}'}]})
    def test_nested_fragment_not_accepted(self):
        with self.assertRaises(ModelOutputError): parse_model_response({'content':[{'type':'text','text':'{"body":{"x":1}'}]})
    def test_refusal_not_fabricated(self):
        with self.assertRaises(RuntimeError): parse_model_response({'stop_reason':'refusal','content':[]})

if __name__=='__main__': unittest.main()
