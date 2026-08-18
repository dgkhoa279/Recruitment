import base64
import json
import logging
import math
import requests
from odoo import models, fields, api
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class RecruitmentAIService(models.AbstractModel):
    _name = "recruitment.ai.service"
    _description = "Service Layer cho các tính năng AI & Vector Search"

    def _get_ai_config(self):
        config_env = self.env["ir.config_parameter"].sudo()
        provider = config_env.get_param("recruitment.ai_cv_provider", default="gemini")
        api_key = (config_env.get_param("recruitment.ai_cv_api_key") or "").strip()
        model_name = (config_env.get_param("recruitment.ai_cv_model") or "gemini-1.5-flash").strip()
        return provider, api_key, model_name

    def scan_cv_vision(self, cv_bytes, job_title="Vị trí Tuyển dụng", req_skills="Chưa chỉ định", job_desc="Không có mô tả công việc"):
        """
        Sends raw PDF/Image bytes to AI Multimodal Vision API and returns parsed JSON dictionary.
        """
        provider, api_key, model_name = self._get_ai_config()
        if not api_key:
            return None

        prompt = f"""Bạn là Chuyên gia Tuyển dụng HR AI cao cấp (Engine 2 Multimodal Vision).
Hãy PHÂN TÍCH TRỰC TIẾP FILE CV (PDF/Ảnh) đính kèm để tuyển dụng cho Vị trí: '{job_title}'.
Kỹ năng yêu cầu: {req_skills}
Mô tả công việc: {job_desc}

YÊU CẦU XỬ LÝ QUAN TRỌNG:
1. Đọc trực tiếp nội dung bố cục trang CV đính kèm, tự động sửa lỗi chính tả, lỗi font chữ/OCR.
2. Phân tích độ tương đồng ngữ nghĩa kỹ năng (Semantic Skill Evaluation), ví dụ: nếu CV có Java/Microservices thì tự suy luận mức độ gần với Java Spring Boot.
3. Trả về đúng định dạng JSON duy nhất (KHÔNG bọc markdown ```json ... ```):
{{
  "candidate_name": "Tên ứng viên trích xuất được hoặc rỗng",
  "phone": "Số điện thoại hoặc rỗng",
  "email": "Email hoặc rỗng",
  "matching_score": 85,
  "matched_skills": "Chuỗi các kỹ năng đáp ứng",
  "missing_skills": "Chuỗi các kỹ năng còn thiếu",
  "corrected_text": "Văn bản CV bóc tách được đã được sửa lỗi chính tả và chuẩn hóa",
  "ai_summary": "Đoạn tóm tắt nhận xét 3-4 dòng đánh giá ưu/nhược điểm và độ phù hợp của ứng viên"
}}
"""

        try:
            if provider == "openai":
                url = "https://api.openai.com/v1/chat/completions"
                headers = {
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                }
                payload = {
                    "model": model_name or "gpt-4o-mini",
                    "messages": [{"role": "user", "content": prompt}],
                    "response_format": {"type": "json_object"},
                    "temperature": 0.2,
                }
                res = requests.post(url, json=payload, headers=headers, timeout=25)
                if res.status_code == 200:
                    text_res = res.json()["choices"][0]["message"]["content"]
                    return json.loads(text_res)
            else:
                # Default: Gemini Vision API (Inline Base64 Binary)
                base64_cv = base64.b64encode(cv_bytes).decode("utf-8")
                mime_type = "application/pdf" if cv_bytes.startswith(b"%PDF") else "image/jpeg"

                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
                payload = {
                    "contents": [{
                        "parts": [
                            {"text": prompt},
                            {
                                "inlineData": {
                                    "mimeType": mime_type,
                                    "data": base64_cv,
                                }
                            },
                        ]
                    }],
                    "generationConfig": {
                        "temperature": 0.2,
                        "responseMimeType": "application/json",
                    },
                }
                res = requests.post(url, json=payload, timeout=25)
                if res.status_code == 200:
                    text_res = res.json()["candidates"][0]["content"]["parts"][0]["text"]
                    clean_json = text_res.replace("```json", "").replace("```", "").strip()
                    return json.loads(clean_json)
                else:
                    _logger.warning("Gemini Vision HTTP %s: %s", res.status_code, res.text)
        except Exception as e:
            _logger.warning("Lỗi gọi AI Engine 2 Vision Service: %s", str(e))
        return None

    def generate_vector_embedding(self, text):
        """Generates a 768-dimensional embedding vector from text using Gemini Embedding API or Local Hash Fallback."""
        _, api_key, _ = self._get_ai_config()

        if api_key:
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/text-embedding-004:embedContent?key={api_key}"
                payload = {
                    "model": "models/text-embedding-004",
                    "content": {"parts": [{"text": text[:2000]}]},
                }
                res = requests.post(url, json=payload, timeout=10)
                if res.status_code == 200:
                    values = res.json().get("embedding", {}).get("values", [])
                    if values:
                        return values
            except Exception as e:
                _logger.warning("Lỗi Gemini Embedding Service: %s", str(e))

        # Fallback Local Deterministic Vector Generator (768-dimensional)
        vec = [0.0] * 768
        words = text.lower().split()
        for idx, word in enumerate(words):
            val = sum(ord(c) for c in word)
            slot = val % 768
            vec[slot] += (idx + 1) * 0.1
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [round(x / norm, 5) for x in vec]

    def compute_cosine_similarity(self, vec_a, vec_b):
        """Computes Cosine Similarity between two float vectors."""
        if not vec_a or not vec_b:
            return 0.0
        dot_product = sum(a * b for a, b in zip(vec_a, vec_b))
        norm_a = math.sqrt(sum(a * a for a in vec_a)) or 1.0
        norm_b = math.sqrt(sum(b * b for b in vec_b)) or 1.0
        sim = dot_product / (norm_a * norm_b)
        return round(max(0.0, min(100.0, sim * 100.0)), 1)
