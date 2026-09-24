from odoo import models, fields


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    facebook_page_id = fields.Char(
        string="Facebook Page ID",
        config_parameter="recruitment.facebook_page_id",
        help="ID của Facebook Fanpage công ty (ví dụ: 102938475610293)",
    )

    facebook_page_access_token = fields.Char(
        string="Facebook Page Access Token",
        config_parameter="recruitment.facebook_page_access_token",
        help="Long-lived Page Access Token được cấp từ Facebook Developer App",
    )
    # Ollama is the primary runtime; Gemini is an explicit cloud fallback.
    ollama_chat_model = fields.Char(
        string="Ollama Generation Model",
        default="qwen3:1.7b",
        config_parameter="recruitment.ollama_chat_model",
        help="Model local dùng để phân tích CV và sinh nội dung nghiệp vụ.",
    )
    ollama_endpoint = fields.Char(
        string="Ollama Endpoint",
        default="http://localhost:11434",
        config_parameter="recruitment.ollama_endpoint",
        help="Địa chỉ cổng Ollama API Server (Mặc định: http://localhost:11434)",
    )

    gemini_fallback_enabled = fields.Boolean(
        string="Cho phép Gemini fallback",
        default=True,
        config_parameter="recruitment.gemini_fallback_enabled",
        help="Chỉ gửi yêu cầu lên Gemini khi Ollama gặp lỗi kết nối hoặc phản hồi.",
    )

    gemini_api_key = fields.Char(
        string="Gemini API Key",
        config_parameter="recruitment.ai_cv_api_key",
        help="Chỉ được sử dụng khi Gemini fallback đang bật và Ollama thất bại.",
    )

    gemini_model = fields.Char(
        string="Gemini Fallback Model",
        default="gemini-1.5-flash",
        config_parameter="recruitment.gemini_model",
    )

    ai_embedding_model = fields.Char(
        string="Ollama Embedding Model",
        default="embeddinggemma",
        config_parameter="recruitment.ai_embedding_model",
        help="Embedding luôn chạy local bằng Ollama để giữ một không gian vector nhất quán.",
    )

    ai_embedding_dimensions = fields.Integer(
        string="Embedding Dimensions",
        default=768,
        config_parameter="recruitment.ai_embedding_dimensions",
    )

    def set_values(self):
        params = self.env["ir.config_parameter"].sudo()
        old_identity = (
            params.get_param("recruitment.ollama_endpoint") or "http://localhost:11434",
            params.get_param("recruitment.ai_embedding_model") or "embeddinggemma",
            params.get_param("recruitment.ai_embedding_dimensions") or "768",
        )
        result = super().set_values()
        new_identity = (
            self.ollama_endpoint or "http://localhost:11434",
            self.ai_embedding_model or "embeddinggemma",
            str(self.ai_embedding_dimensions or 768),
        )
        if old_identity != new_identity:
            self.env["recruitment.application"].sudo().with_context(
                skip_ai_reindex=True
            ).search([("cv_file", "!=", False)]).write({
                "rag_index_status": "pending",
                "rag_index_error": False,
            })
            self.env["recruitment.job.position"].sudo().with_context(
                skip_ai_reindex=True
            ).search([]).write({
                "rag_index_status": "pending",
                "rag_index_error": False,
            })
        return result
