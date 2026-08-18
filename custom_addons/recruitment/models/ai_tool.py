import logging
from odoo import models, fields, api
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class RecruitmentAITool(models.Model):
    _name = "recruitment.ai.tool"
    _description = "Công cụ AI Sinh Tự Động (AI Generated Tool)"

    name = fields.Char(
        string="Tên Công cụ",
        required=True,
    )

    prompt = fields.Text(
        string="Prompt Yêu cầu Ban đầu",
        help="Nội dung Prompt người dùng gõ để AI sinh ra Tool này",
    )

    python_code = fields.Text(
        string="Mã nguồn Python (Đã duyệt AST)",
        required=True,
    )

    is_valid = fields.Boolean(
        string="Đã qua kiểm định AST",
        default=True,
    )

    active = fields.Boolean(
        string="Hoạt động",
        default=True,
    )

    execution_count = fields.Integer(
        string="Số lần đã thực thi",
        default=0,
    )

    last_execution_date = fields.Datetime(
        string="Lần thực thi cuối",
    )

    def action_execute_tool(self):
        """
        Executes the sandboxed Python code of this AI Tool cleanly in Odoo.
        """
        self.ensure_one()
        if not self.python_code or not self.is_valid:
            raise ValidationError("Công cụ này không chứa mã nguồn Python hợp lệ hoặc chưa qua kiểm duyệt bảo mật AST!")

        ai_service = self.env["recruitment.ai.service"]
        # Double check AST security
        is_safe, err_msg = ai_service.validate_python_ast(self.python_code)
        if not is_safe:
            self.write({"is_valid": False})
            raise ValidationError(f"Lỗi Bảo Mật AST: {err_msg}")

        # Execution context with safe env
        safe_globals = {
            "env": self.env,
            "datetime": fields.Datetime,
            "date": fields.Date,
            "_logger": _logger,
        }
        safe_locals = {}

        try:
            exec(self.python_code, safe_globals, safe_locals)
            self.write({
                "execution_count": self.execution_count + 1,
                "last_execution_date": fields.Datetime.now(),
            })

            # Check if code defined a result string or action
            res_msg = safe_locals.get("result_message") or "Đã thực thi Công cụ AI thành công!"
            action_res = safe_locals.get("action_result")

            if action_res and isinstance(action_res, dict):
                return action_res

            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": f"Thành công: {self.name} 🎉",
                    "message": res_msg,
                    "type": "success",
                    "sticky": False,
                },
            }
        except Exception as e:
            _logger.exception("Lỗi khi thực thi AI Tool '%s'", self.name)
            raise ValidationError(f"Lỗi khi thực thi Công cụ '{self.name}': {str(e)}")
