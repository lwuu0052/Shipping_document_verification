"""Offline pipeline regression tests; no credentials or API calls required."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from main import process_email
from comparator import compare_documents
from extract.llm_client import parse_llm_response
from extract.parsers import ParseError, parse_to_text


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.email = json.loads((Path(__file__).resolve().parent.parent /
            'sdoc-hackathon-bundle/inbox/email_001.json').read_text())
        self.fields = dict(shipper='ABC', consignee='XYZ', notify_party='XYZ',
            port_of_loading='KLANG', port_of_discharge='SINGAPORE',
            container_count=3, gross_weight_kg=22000)

    def test_pipeline_with_stubbed_services(self):
        with patch('main.classify_email', return_value={
            'category': 'BL_COMPARISON', 'should_process': True
        }), patch('extract.extractor.extract_fields', return_value=self.fields):
            result = process_email(self.email)
        self.assertEqual(result['status'], 'OK')

    def test_missing_attachment_has_review_reason(self):
        with patch('main.classify_email', return_value={
            'category': 'BL_COMPARISON', 'should_process': True
        }):
            result = process_email({**self.email, 'attachments': []})
        self.assertEqual(result['review_reason'], 'missing_attachment')

    def test_missing_value_context_reaches_review(self):
        with patch('main.classify_email', return_value={
            'category': 'BL_COMPARISON', 'should_process': True
        }), patch('extract.extractor.extract_fields', return_value={
            **self.fields, 'gross_weight_kg': None
        }):
            result = process_email(self.email)
        self.assertEqual(result['review_reason'], 'missing_value')
        self.assertIn('gross_weight_kg', result['missing_fields'])

    def test_invalid_numbers_require_review(self):
        for value in (float('nan'), float('inf'), 'Infinity', True):
            with self.subTest(value=value):
                result = compare_documents(self.fields, {**self.fields, 'gross_weight_kg': value})
                self.assertEqual(result['status'], 'NEEDS_REVIEW')

    def test_prefixed_json_fence(self):
        result = parse_llm_response('Here is the JSON:\n```json\n' + json.dumps(self.fields) + '\n```')
        self.assertEqual(result, self.fields)

    def test_unreadable_and_blank_text(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder) / 'directory.txt'
            directory.mkdir()
            blank = Path(folder) / 'blank.txt'
            blank.write_text('   \n')
            for path in (directory, blank):
                with self.subTest(path=path), self.assertRaises(ParseError):
                    parse_to_text(path)


if __name__ == '__main__':
    unittest.main()
