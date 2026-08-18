from odoo.tests.common import TransactionCase
from odoo.exceptions import ValidationError


class TestAIToolAST(TransactionCase):

    def setUp(self):
        super(TestAIToolAST, self).setUp()
        self.ai_service = self.env['recruitment.ai.service']

    def test_safe_python_ast(self):
        safe_code = """
apps = env['recruitment.application'].search([('matching_score', '>=', 80)])
for app in apps:
    app.write({'ai_summary': 'Ứng viên đạt yêu cầu'})
result_message = f"Đã cập nhật {len(apps)} ứng viên!"
"""
        is_safe, err_msg = self.ai_service.validate_python_ast(safe_code)
        self.assertTrue(is_safe)
        self.assertIsNone(err_msg)

    def test_forbidden_import_ast(self):
        dangerous_code = "import os; os.system('ls')"
        is_safe, err_msg = self.ai_service.validate_python_ast(dangerous_code)
        self.assertFalse(is_safe)
        self.assertIn("import", err_msg.lower())

    def test_forbidden_sudo_ast(self):
        dangerous_code = "env['recruitment.application'].sudo().search([])"
        is_safe, err_msg = self.ai_service.validate_python_ast(dangerous_code)
        self.assertFalse(is_safe)
        self.assertIn("sudo", err_msg.lower())
