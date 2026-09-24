from psycopg2 import sql

from odoo import api, fields, models


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
    recruitment_ai_agent_id = fields.Many2one(
        comodel_name="muk_ai.agent",
        string="MuK AI Agent",
        config_parameter="recruitment.muk_ai_agent_id",
        domain="[('active', '=', True)]",
        default=lambda self: self.env.ref("muk_ai.agent_general").id,
        help=(
            "Agent cung cấp provider và model cho quét CV và sinh mô tả công việc. "
            "Thay model trên Agent trong MuK AI sẽ được Recruitment sử dụng ngay."
        ),
    )

    @api.model
    def _migrate_recruitment_ai_to_muk(self):
        """Select the default MuK agent and remove obsolete duplicate secrets."""
        params = self.env["ir.config_parameter"].sudo()
        if not params.get_param("recruitment.muk_ai_agent_id"):
            agent = self.env.ref("muk_ai.agent_general", raise_if_not_found=False)
            if agent:
                params.set_param("recruitment.muk_ai_agent_id", agent.id)

        legacy_keys = [
            "recruitment.ai_cv_api_key",
            "recruitment.ai_cv_model",
            "recruitment.ai_cv_provider",
            "recruitment.gemini_fallback_enabled",
            "recruitment.gemini_model",
            "recruitment.ollama_chat_model",
            "recruitment.ollama_endpoint",
            "recruitment.ai_embedding_model",
            "recruitment.ai_embedding_dimensions",
        ]
        params.search([("key", "in", legacy_keys)]).unlink()

        legacy_columns = [
            "ollama_chat_model",
            "ollama_endpoint",
            "gemini_fallback_enabled",
            "gemini_api_key",
            "gemini_model",
            "ai_embedding_model",
            "ai_embedding_dimensions",
        ]
        for column_name in legacy_columns:
            self.env.cr.execute(
                sql.SQL("ALTER TABLE res_config_settings DROP COLUMN IF EXISTS {}")
                .format(sql.Identifier(column_name))
            )

        embedding_message = (
            "MuK AI chưa cung cấp API embedding dùng chung; RAG vector tạm ngưng."
        )
        self.env["recruitment.application"].sudo().with_context(
            skip_ai_reindex=True
        ).search([("cv_file", "!=", False)]).write({
            "rag_index_status": "failed",
            "rag_index_error": embedding_message,
        })
        self.env["recruitment.job.position"].sudo().with_context(
            skip_ai_reindex=True
        ).search([]).write({
            "rag_index_status": "failed",
            "rag_index_error": embedding_message,
        })
        cron = self.env.ref(
            "recruitment.ir_cron_recruitment_ai_reindex",
            raise_if_not_found=False,
        )
        if cron:
            cron.sudo().active = False
        return True
