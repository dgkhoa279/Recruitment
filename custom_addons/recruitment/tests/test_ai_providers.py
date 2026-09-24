import json
from unittest.mock import patch

from odoo.tests.common import TransactionCase


class TestRecruitmentMuKAI(TransactionCase):

    def setUp(self):
        super().setUp()
        self.ai_service = self.env["recruitment.ai.service"]
        self.agent = self.env.ref("muk_ai.agent_general")
        self.model = self.env.ref("muk_ai.model_gemini_3_5_flash")
        self.agent.model_id = self.model
        self.env["ir.config_parameter"].sudo().set_param(
            "recruitment.muk_ai_agent_id", self.agent.id
        )

    def test_cv_analysis_uses_selected_muk_agent_model(self):
        response = {
            "candidate_name": "Nguyen Van A",
            "phone": "0900000000",
            "email": "a@example.com",
            "matching_score": 75,
            "matched_skills": ["Python", "SQL"],
            "missing_skills": ["Java"],
            "ai_summary": "Phù hợp phần lớn yêu cầu.",
        }
        captured = {}

        def fake_request(_provider, *args, **kwargs):
            captured.update(kwargs)
            return {"text": json.dumps(response)}

        with patch.object(
            type(self.model.provider_id),
            "_request_responses",
            autospec=True,
            side_effect=fake_request,
        ):
            result = self.ai_service.analyze_cv(
                b"Nguyen Van A knows Python and SQL",
                job_title="Backend Developer",
                req_skills="Python, SQL, Java",
            )

        self.assertEqual(captured["model"], self.model.technical_name)
        self.assertEqual(
            captured["text_schema"]["name"], "recruitment_cv_analysis"
        )
        self.assertEqual(result["matched_skills"], "Python, SQL")
        self.assertEqual(result["missing_skills"], "Java")
        self.assertEqual(result["_muk_ai_agent"], self.agent.display_name)
        self.assertEqual(result["_muk_ai_model"], self.model.technical_name)

    def test_job_description_uses_muk_ai(self):
        captured = {}

        def fake_request(_provider, *args, **kwargs):
            captured.update(kwargs)
            return {"text": "Mô tả công việc từ MuK AI"}

        with patch.object(
            type(self.model.provider_id),
            "_request_responses",
            autospec=True,
            side_effect=fake_request,
        ):
            result = self.ai_service.generate_job_description(
                "Backend Developer", "Python, SQL", "Intern"
            )

        self.assertEqual(result, "Mô tả công việc từ MuK AI")
        self.assertEqual(captured["model"], self.model.technical_name)
        self.assertIsNone(captured["text_schema"])

    def test_migration_removes_legacy_recruitment_ai_parameters(self):
        params = self.env["ir.config_parameter"].sudo()
        params.set_param("recruitment.ai_cv_api_key", "obsolete-secret")
        params.set_param("recruitment.gemini_model", "obsolete-model")

        self.env["res.config.settings"]._migrate_recruitment_ai_to_muk()

        self.assertFalse(params.get_param("recruitment.ai_cv_api_key"))
        self.assertFalse(params.get_param("recruitment.gemini_model"))
        self.assertEqual(
            params.get_param("recruitment.muk_ai_agent_id"), str(self.agent.id)
        )
