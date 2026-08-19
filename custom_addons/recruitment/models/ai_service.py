import ast
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

QUY TẮC PHÂN ĐỊNH KỸ NĂNG NGHIÊM NGẠT:
1. 'matched_skills' CHỈ ĐƯỢC CHỨA các kỹ năng THỰC TẾ XUẤT HIỆN trong CV hoặc có bằng chứng cụ thể. KHÔNG ĐƯỢC TỰ SUY ĐOÁN BỊA THÊM kỹ năng nếu CV không hề nhắc tới.
2. 'missing_skills' là các kỹ năng có trong Yêu cầu tuyển dụng nhưng KHÔNG NẰM TRONG CV của ứng viên.
3. QUY TẮC RỜI RẠC BẮT BUỘC: 'matched_skills' và 'missing_skills' KHÔNG ĐƯỢC PHÉP TRÙNG BẤT KỲ TỪ NÀO! Nếu 1 kỹ năng đã thuộc 'missing_skills' thì TUYỆT ĐỐI KHÔNG ĐƯỢC ĐƯA VÀO 'matched_skills'!

Trả về đúng định dạng JSON duy nhất (KHÔNG bọc markdown ```json ... ```):
{{
  "candidate_name": "Tên ứng viên trích xuất được hoặc rỗng",
  "phone": "Số điện thoại hoặc rỗng",
  "email": "Email hoặc rỗng",
  "matching_score": 85,
  "matched_skills": "Chuỗi các kỹ năng thực tế ứng viên có (phân cách bằng dấu phẩy)",
  "missing_skills": "Chuỗi các kỹ năng ứng viên còn thiếu (phân cách bằng dấu phẩy)",
  "corrected_text": "Văn bản CV bóc tách được đã được sửa lỗi chính tả và chuẩn hóa",
  "ai_summary": "Đoạn tóm tắt nhận xét 3-4 dòng đánh giá ưu/nhược điểm và độ phù hợp của ứng viên"
}}
"""

        try:
            result = None
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
                    result = json.loads(text_res)
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
                    result = json.loads(clean_json)
                else:
                    _logger.warning("Gemini Vision HTTP %s: %s", res.status_code, res.text)

            # Post-Processing: Đảm bảo 2 tập hợp matched_skills và missing_skills hoàn toàn rời rạc
            if result and isinstance(result, dict):
                matched_raw = result.get("matched_skills", "")
                missing_raw = result.get("missing_skills", "")

                matched_list = [s.strip() for s in matched_raw.split(",") if s.strip()]
                missing_list = [s.strip() for s in missing_raw.split(",") if s.strip()]

                missing_set_lower = {s.lower() for s in missing_list}
                clean_matched = [s for s in matched_list if s.lower() not in missing_set_lower]

                result["matched_skills"] = ", ".join(clean_matched) if clean_matched else "Không có"
                result["missing_skills"] = ", ".join(missing_list) if missing_list else "Không có"

            return result
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

    def generate_job_description(self, job_name, required_skills="", job_level=""):
        """Generates a professional Job Description (JD) text using Gemini / OpenAI API."""
        provider, api_key, model_name = self._get_ai_config()
        if not api_key:
            return f"Mô tả vị trí {job_name}: Yêu cầu kỹ năng {required_skills or 'chuyên môn tốt'}. Cần chủ động và có tinh thần trách nhiệm cao."

        prompt = f"""Bạn là Trợ lý HR Tuyển dụng Chuyên nghiệp.
Hãy viết Mô tả Công việc (Job Description - JD) thật đầy đủ, thu hút và chi tiết cho vị trí tuyển dụng sau:
- Tên vị trí: {job_name}
- Cấp bậc: {job_level or 'Nhân viên'}
- Kỹ năng yêu cầu: {required_skills or 'Tương ứng vị trí'}

Vui lòng cấu trúc nội dung JD bao gồm 4 phần rõ ràng:
1. 📌 Mô tả Công việc
2. 🎯 Yêu cầu ứng viên
3. 🎁 Quyền lợi được hưởng
4. 🚀 Môi trường làm việc

Trả về nội dung văn bản thuần đẹp mắt, trình bày rõ ràng."""

        try:
            if provider == "openai":
                url = "https://api.openai.com/v1/chat/completions"
                headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
                payload = {
                    "model": model_name or "gpt-4o-mini",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.3,
                }
                res = requests.post(url, json=payload, headers=headers, timeout=20)
                if res.status_code == 200:
                    return res.json()["choices"][0]["message"]["content"]
            else:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
                payload = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": 0.3},
                }
                res = requests.post(url, json=payload, timeout=20)
                if res.status_code == 200:
                    return res.json()["candidates"][0]["content"]["parts"][0]["text"]
        except Exception as e:
            _logger.warning("Lỗi generate_job_description: %s", str(e))

        return f"Mô tả vị trí {job_name}: Xây dựng giải pháp, thiết kế hệ thống và phát triển phần mềm theo định hướng công ty."

    def validate_python_ast(self, python_code):
        """Validates Python code against security guardrails using AST parsing."""
        if not python_code:
            return False, "Mã nguồn rỗng!"
        try:
            tree = ast.parse(python_code)
            SecurityASTValidator().visit(tree)
            return True, None
        except Exception as e:
            return False, str(e)

    def _get_dynamic_recruitment_schema(self):
        """
        Dynamic Odoo ORM Schema Introspection:
        Automatically scans all models starting with 'recruitment.' registered in Odoo env at runtime.
        Extracts model technical names, descriptions, field names, field types, and relational comodels dynamically.
        Zero hardcoding required!
        """
        schema_lines = []
        recruitment_models = [m for m in self.env if m.startswith("recruitment.")]

        for model_name in sorted(recruitment_models):
            try:
                model_obj = self.env[model_name]
                desc = model_obj._description or model_name
                schema_lines.append(f"📌 MODEL `env['{model_name}']` ({desc}):")

                field_items = []
                for f_name, f_obj in model_obj._fields.items():
                    if f_name in ('create_date', 'create_uid', 'write_date', 'write_uid', '__last_update'):
                        continue
                    f_type = f_obj.type
                    rel = f" -> {f_obj.comodel_name}" if hasattr(f_obj, 'comodel_name') and f_obj.comodel_name else ""
                    field_items.append(f"   - `{f_name}` ({f_type}{rel}): {f_obj.string}")

                schema_lines.append("\n".join(field_items))
                schema_lines.append("")
            except Exception as e:
                continue

        return "\n".join(schema_lines)

    def process_chat_message(self, user_message, history=None):
        """
        AI Copilot Router with Multi-turn Conversation Memory & Dynamic Schema Auto-Discovery:
        Classifies user intent (CHAT vs GENERATE_TOOL) while maintaining full context history & live database schema!
        """
        provider, api_key, model_name = self._get_ai_config()

        dynamic_schema = self._get_dynamic_recruitment_schema()

        system_prompt = f"""Bạn là Trợ lý Tuyển dụng AI Copilot chuyên nghiệp và chính xác 100%.
Nhiệm vụ của bạn là tiếp nhận tin nhắn chat từ nhân sự HR và phân loại mục đích (Intent):

THƯỜNG GỢI Ý 2 TRƯỜNG HỢP:
1. Nếu người dùng hỏi đáp/tư vấn HR bình thường: Trả về JSON với "intent": "CHAT" và "ai_reply": "câu trả lời...".
2. Nếu người dùng YÊU CẦU TẠO/SỬA TOOL TỰ ĐỘNG HÓA NGHIỆP VỤ trong Odoo:
   Trả về JSON với:
   - "intent": "GENERATE_TOOL"
   - "tool_name": "Tên công cụ ngắn gọn"
   - "python_code": "Mã Python chuẩn Odoo ORM"
   - "ai_reply": "Lời giải thích ngắn 1 dòng"

⚠️ SƠ ĐỒ DATABASE CHÍNH XÁC HIỆN TẠI (ĐƯỢC QUÉT ĐỘNG TRỰC TIẾP TỪ ODOO ORM AT RUNTIME):
🚫 CẤM TUYỆT ĐỐI SỬ DỤNG CÁC MODEL CHUẨN MẶC ĐỊNH CỦA ODOO (Như `hr.applicant`, `hr.job`, `hr.department`, `hr.employee`...)!
✅ BẮT BUỘC CHỈ SỬ DỤNG CÁC MODEL TÙY CHỈNH DƯỚI ĐÂY:

{dynamic_schema}

QUY TẮC VIẾT PYTHON CODE TRONG TOOL:
- Để đếm số lượng bản ghi, PHẢI DÙNG `search_count(domain)` thay vì `len(search(domain))` để tối ưu RAM.
- Nếu người dùng yêu cầu viết/sinh Mô tả công việc (JD), gọi hàm AI: `env['recruitment.ai.service'].generate_job_description(job.name, job.required_skills, job.job_level)`.
- Luôn gán `result_message = 'thông báo kết quả...'` ở dòng cuối cùng của python_code.
- CẤM dùng `import`, `sudo()`, `unlink()`, `exec`, `eval`.

TRẢ VỀ ĐÚNG ĐỊNH DẠNG JSON DUY NHẤT (KHÔNG markdown ```json ... ```):
"""

        try:
            res_dict = None
            if api_key:
                contents = [{"parts": [{"text": system_prompt}]}]

                # Append conversation history for multi-turn context awareness
                if history and isinstance(history, list):
                    for item in history:
                        role = "model" if item.get("role") == "model" else "user"
                        msg_text = item.get("content") or ""
                        if msg_text:
                            contents.append({
                                "role": role,
                                "parts": [{"text": msg_text}]
                            })

                # Append current user prompt
                contents.append({
                    "role": "user",
                    "parts": [{"text": f"Tin nhắn mới của HR: {user_message}"}]
                })

                if provider == "openai":
                    openai_messages = [{"role": "system", "content": system_prompt}]
                    if history and isinstance(history, list):
                        for item in history:
                            r = "assistant" if item.get("role") == "model" else "user"
                            if item.get("content"):
                                openai_messages.append({"role": r, "content": item.get("content")})
                    openai_messages.append({"role": "user", "content": user_message})

                    url = "https://api.openai.com/v1/chat/completions"
                    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
                    payload = {
                        "model": model_name or "gpt-4o-mini",
                        "messages": openai_messages,
                        "response_format": {"type": "json_object"},
                        "temperature": 0.2,
                    }
                    res = requests.post(url, json=payload, headers=headers, timeout=25)
                    if res.status_code == 200:
                        text_res = res.json()["choices"][0]["message"]["content"]
                        res_dict = json.loads(text_res)
                else:
                    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
                    payload = {
                        "contents": contents,
                        "generationConfig": {
                            "temperature": 0.2,
                            "responseMimeType": "application/json",
                        },
                    }
                    res = requests.post(url, json=payload, timeout=25)
                    if res.status_code == 200:
                        text_res = res.json()["candidates"][0]["content"]["parts"][0]["text"]
                        clean_json = text_res.replace("```json", "").replace("```", "").strip()
                        res_dict = json.loads(clean_json)

                    if res_dict.get("intent") == "GENERATE_TOOL":
                        code = res_dict.get("python_code", "")
                        tool_name = res_dict.get("tool_name", "Công cụ AI Tự Động")
                        is_safe, err_msg = self.validate_python_ast(code)

                        if is_safe:
                            is_update_request = any(kw in user_message.lower() for kw in ["sửa", "update", "fix", "chỉnh sửa", "thay đổi", "viết lại", "cập nhật", "lại"])

                            existing_tool = None
                            if is_update_request or tool_name:
                                existing_tool = self.env["recruitment.ai.tool"].search([
                                    "|",
                                    ("name", "ilike", tool_name),
                                    ("name", "=", tool_name)
                                ], order="id desc", limit=1)

                                if not existing_tool and is_update_request:
                                    existing_tool = self.env["recruitment.ai.tool"].search([], order="id desc", limit=1)

                            if existing_tool and (is_update_request or existing_tool.name.strip().lower() == tool_name.strip().lower()):
                                existing_tool.write({
                                    "name": tool_name,
                                    "prompt": user_message,
                                    "python_code": code,
                                    "is_valid": True,
                                })
                                tool_rec = existing_tool
                                action_label = "🔄 Đã cập nhật lại mã nguồn công cụ"
                            else:
                                tool_rec = self.env["recruitment.ai.tool"].create({
                                    "name": tool_name,
                                    "prompt": user_message,
                                    "python_code": code,
                                    "is_valid": True,
                                })
                                action_label = "✨ Đã khởi tạo công cụ mới"

                            return {
                                "intent": "GENERATE_TOOL",
                                "ai_reply": f"{action_label}: **{tool_rec.name}**!\n\n🛠️ **Mã Tool:** #{tool_rec.id}\n🛡️ **Bảo mật AST:** Hợp lệ ✅",
                                "tool_id": tool_rec.id,
                                "tool_name": tool_rec.name,
                            }
                        else:
                            return {
                                "intent": "CHAT",
                                "ai_reply": f"⚠️ Tôi đã sinh code cho công cụ '{tool_name}' nhưng không vượt qua được Bộ duyệt Bảo mật AST:\n`{err_msg}`\nVui lòng thử lại với yêu cầu khác!",
                            }
                    else:
                        return {
                            "intent": "CHAT",
                            "ai_reply": res_dict.get("ai_reply", "Tôi có thể giúp gì thêm cho bạn?"),
                        }
        except Exception as e:
            _logger.warning("Lỗi process_chat_message: %s", str(e))

        # Fallback offline reply
        return {
            "intent": "CHAT",
            "ai_reply": f"🤖 Xin chào! Tôi là Trợ lý AI Tuyển dụng. Bạn có thể yêu cầu tôi tư vấn hoặc tạo Công cụ tự động hóa Odoo! (Tin nhắn: '{user_message}')",
        }


class SecurityASTValidator(ast.NodeVisitor):
    ALLOWED_MODELS = {
        "recruitment.application",
        "recruitment.job.position",
        "recruitment.candidate",
        "recruitment.stage",
        "recruitment.interview",
        "recruitment.offer",
        "recruitment.refuse.reason",
    }
    FORBIDDEN_ATTRS = {"sudo", "unlink", "_cr", "_sql", "__subclasses__", "eval", "exec", "open", "system", "popen"}

    def visit_Import(self, node):
        raise ValueError("Lỗi Bảo Mật: Không được dùng lệnh 'import' trong mã nguồn AI Tool!")

    def visit_ImportFrom(self, node):
        raise ValueError("Lỗi Bảo Mật: Không được dùng lệnh 'from ... import' trong mã nguồn AI Tool!")

    def visit_Attribute(self, node):
        if node.attr in self.FORBIDDEN_ATTRS:
            raise ValueError(f"Lỗi Bảo Mật: Thao tác nguy hiểm '.{node.attr}' bị cấm!")
        self.generic_visit(node)

